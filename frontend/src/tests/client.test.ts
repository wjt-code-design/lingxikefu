import { AxiosError } from 'axios';
import type { AxiosHeaderValue, AxiosResponse, InternalAxiosRequestConfig } from 'axios';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { http, refreshAccessToken } from '@/api/client';
import { AUTH_STORAGE_KEY, useAuthStore } from '@/store/authStore';

describe('client 401 自动刷新拦截器', () => {
  const originalAdapter = http.defaults.adapter;

  beforeEach(() => {
    useAuthStore.setState({ token: 'old', refreshToken: 'rt', user: null, role: 'user' });
  });

  afterEach(() => {
    http.defaults.adapter = originalAdapter;
    useAuthStore.setState({ token: null, refreshToken: null, user: null, role: null });
    localStorage.removeItem(AUTH_STORAGE_KEY);
  });

  type MockResponse = { status: number; data: unknown };
  type AdapterFn = (config: InternalAxiosRequestConfig) => Promise<AxiosResponse>;

  function toAxiosResponse(config: InternalAxiosRequestConfig, res: MockResponse): AxiosResponse {
    return {
      data: res.data,
      status: res.status,
      statusText: '',
      headers: config.headers ?? {},
      config,
    };
  }

  // handler 返回状态码；≥400 主动 reject AxiosError（带 response），模拟真实 HTTP 错误
  function mockAdapter(handler: (config: InternalAxiosRequestConfig) => MockResponse) {
    const adapter: AdapterFn = async (config) => {
      const res = handler(config);
      if (res.status >= 400) {
        throw new AxiosError(
          'mock error',
          'ERR',
          config,
          {},
          toAxiosResponse(config, res),
        );
      }
      return toAxiosResponse(config, res);
    };
    http.defaults.adapter = adapter;
  }

  it('access 过期(401) → 自动 refresh → 用新 token 重试成功', async () => {
    let secureCalls = 0;
    const seenAuth: (AxiosHeaderValue | undefined)[] = [];
    mockAdapter((config) => {
      seenAuth.push(config.headers?.Authorization);
      if (config.url === '/secure') {
        secureCalls += 1;
        if (secureCalls === 1) return { status: 401, data: {} };
        return { status: 200, data: { ok: true } };
      }
      if (config.url === '/auth/refresh') return { status: 200, data: { access_token: 'new' } };
      return { status: 404, data: {} };
    });

    const r = await http.get('/secure');
    expect(r.status).toBe(200);
    expect(r.data).toEqual({ ok: true });
    expect(seenAuth[seenAuth.length - 1]).toBe('Bearer new');
    expect(useAuthStore.getState().token).toBe('new');
  });

  it('仅排除 refresh 本身；相似路径的 401 仍应刷新', async () => {
    let protectedCalls = 0;
    mockAdapter((config) => {
      if (config.url === '/auth/refresh') {
        return { status: 200, data: { access_token: 'new' } };
      }
      if (config.url === '/auth/refresh-status' && protectedCalls++ === 0) {
        return { status: 401, data: {} };
      }
      return { status: 200, data: { ok: true } };
    });

    await expect(http.get('/auth/refresh-status')).resolves.toMatchObject({
      data: { ok: true },
    });
    expect(useAuthStore.getState().token).toBe('new');
  });

  it('无 refresh_token 时 401 → reject 且清空登录态', async () => {
    // jsdom 不实现跨页导航；本例只验证清空登录态，置于登录页避免无关噪声。
    window.history.replaceState(null, '', '/login');
    useAuthStore.setState({ token: 'old', refreshToken: null, user: null, role: 'user' });
    mockAdapter(() => ({ status: 401, data: {} }));
    let err: unknown = null;
    try {
      await http.get('/secure');
    } catch (e) {
      err = e;
    }
    expect(err).not.toBeNull();
    expect(useAuthStore.getState().token).toBeNull();
    expect(useAuthStore.getState().refreshToken).toBeNull();
  });

  it('非 401 错误直接透传，不触发刷新', async () => {
    mockAdapter(() => ({ status: 500, data: {} }));
    let err: unknown = null;
    try {
      await http.get('/secure');
    } catch (e) {
      err = e;
    }
    expect(err).not.toBeNull();
    expect((err as AxiosError).code).toBe('500'); // H1 契约：无结构化 code 时回退到 HTTP 状态码
    expect(useAuthStore.getState().token).toBe('old');
  });

  describe('多标签页 refreshToken 轮换（R-4 旧票即吊销，persist 不跨标签同步）', () => {
    /**
     * 前提守卫：下面三条用例要手工伪造 localStorage（真·多标签页在单 jsdom 里造不出来——
     * persist 每次 setState 都写盘，无法让"内存持旧票 / 盘上持新票"两边同时成立，只能绕过 store 写盘）。
     * 伪造样本一旦与 persist 真写出的形状漂移，那三条用例就会在**修复已失效**的代码上继续全绿。
     * 所以先断言：走应用自己的 persist，盘上确实有 `state.refreshToken`——
     * 谁把 `authStore.ts` 的 `partialize` 里这项删掉，这条就红。
     */
    it('persist 真写出的信封确实带 state.refreshToken（手工伪造样本的前提）', () => {
      useAuthStore.setState({ refreshToken: 'rt-from-persist' });
      const env = JSON.parse(String(localStorage.getItem(AUTH_STORAGE_KEY))) as {
        state?: { refreshToken?: string };
      };
      expect(env.state?.refreshToken).toBe('rt-from-persist');
    });

    // zustand persist 在 setState 时写盘，故覆盖 localStorage 必须放在 setState 之后
    function seedOtherTabWrote(newerRt: string) {
      localStorage.setItem(
        AUTH_STORAGE_KEY,
        JSON.stringify({ state: { refreshToken: newerRt, user: null, role: 'user' }, version: 0 })
      );
    }
    const postedRt = () => {
      const seen: string[] = [];
      mockAdapter((config) => {
        if (config.url !== '/auth/refresh') return { status: 404, data: {} };
        const sent = JSON.parse(String(config.data)) as { refresh_token: string };
        seen.push(sent.refresh_token);
        return sent.refresh_token === 'rt-new'
          ? { status: 200, data: { access_token: 'new', refresh_token: 'rt-newer' } }
          : { status: 401, data: {} };
      });
      return seen;
    };

    it('本标签页持废票、另一标签页已换新值 → 重读 localStorage 后成功，不清会话', async () => {
      const seen = postedRt();
      seedOtherTabWrote('rt-new');

      const token = await refreshAccessToken();
      expect(token).toBe('new');
      expect(seen).toEqual(['rt', 'rt-new']); // 第一次用内存旧票，第二次用盘上新票
      expect(useAuthStore.getState().refreshToken).toBe('rt-newer');
      expect(useAuthStore.getState().token).toBe('new');
    });

    it('两边同值（真·会话过期）→ 只发一次 refresh 即登出，不无限重试', async () => {
      window.history.replaceState(null, '', '/login');
      const seen = postedRt(); // 任何票都回 401
      seedOtherTabWrote('rt'); // 与内存同值

      const token = await refreshAccessToken();
      expect(token).toBeNull();
      expect(seen).toEqual(['rt']);
      expect(useAuthStore.getState().refreshToken).toBeNull();
    });

    it('localStorage 不可读（坏 JSON）→ 退回原登出路径，不抛异常', async () => {
      window.history.replaceState(null, '', '/login');
      const seen = postedRt();
      localStorage.setItem(AUTH_STORAGE_KEY, '{not json');

      await expect(refreshAccessToken()).resolves.toBeNull();
      expect(seen).toEqual(['rt']);
    });
  });
});
