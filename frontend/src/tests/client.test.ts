import { AxiosError } from 'axios';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { http } from '@/api/client';
import { useAuthStore } from '@/store/authStore';

describe('client 401 自动刷新拦截器', () => {
  const originalAdapter = http.defaults.adapter;

  beforeEach(() => {
    useAuthStore.setState({ token: 'old', refreshToken: 'rt', user: null, role: 'user' });
  });

  afterEach(() => {
    http.defaults.adapter = originalAdapter;
    useAuthStore.setState({ token: null, refreshToken: null, user: null, role: null });
  });

  // handler 返回状态码；≥400 主动 reject AxiosError（带 response），模拟真实 HTTP 错误
  function mockAdapter(handler: (config: any) => { status: number; data: unknown }) {
    http.defaults.adapter = (async (config: any) => {
      const res = handler(config);
      if (res.status >= 400) {
        throw new AxiosError('mock error', 'ERR', config, {}, {
          status: res.status,
          data: res.data,
          headers: {},
          config,
        } as any);
      }
      return { ...config, headers: config.headers || {}, statusText: '', request: {}, data: res.data, status: res.status };
    }) as any;
  }

  it('access 过期(401) → 自动 refresh → 用新 token 重试成功', async () => {
    let secureCalls = 0;
    const seenAuth: (string | undefined)[] = [];
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

  it('无 refresh_token 时 401 → reject 且清空登录态', async () => {
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
    let err: any = null;
    try {
      await http.get('/secure');
    } catch (e) {
      err = e;
    }
    expect(err).not.toBeNull();
    expect(err?.code).toBe('UNKNOWN');
    expect(useAuthStore.getState().token).toBe('old');
  });
});
