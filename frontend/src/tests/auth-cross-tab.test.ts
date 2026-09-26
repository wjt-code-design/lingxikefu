import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { AUTH_STORAGE_KEY, startCrossTabAuthSync, useAuthStore } from '@/store/authStore';

/**
 * D16：跨标签页 refreshToken 同步（main.tsx 在 bootstrapAuth 之前挂 startCrossTabAuthSync）。
 *
 * 与 `client.test.ts` 那三条的分工：那三条测的是**没有**同步时的兜底（先吃 401 再重读盘自愈），
 * 这里测的是有了同步之后不必走那条路。两层都要在——兜底是安全网，同步是体验。
 *
 * jsdom 里用 `dispatchEvent(new StorageEvent(...))` 模拟"另一个标签页写盘"：
 * 真浏览器只在非写入方触发该事件，所以手工派发就是这条链唯一可观察的入口。
 */

const envelope = (refreshToken: string | null) =>
  JSON.stringify({ state: { refreshToken, user: null, role: 'user' }, version: 0 });

function fireStorage(key: string | null, newValue: string | null) {
  window.dispatchEvent(new StorageEvent('storage', { key, newValue }));
}

describe('跨标签页 refreshToken 同步', () => {
  let stop: () => void;

  beforeEach(() => {
    useAuthStore.setState({ token: 'access-a', refreshToken: 'rt-old', user: null, role: 'user' });
    stop = startCrossTabAuthSync();
  });

  afterEach(() => {
    stop();
    useAuthStore.setState({ token: null, refreshToken: null, user: null, role: null });
    localStorage.removeItem(AUTH_STORAGE_KEY);
  });

  it('另一标签页轮换出新票 → 本页内存采纳新票，且 access token 仍不落盘', () => {
    fireStorage(AUTH_STORAGE_KEY, envelope('rt-new'));
    expect(useAuthStore.getState().refreshToken).toBe('rt-new');
    // BUG-15 的取舍不能因为"同步"被绕过：同步只碰 refreshToken
    expect(localStorage.getItem(AUTH_STORAGE_KEY)).not.toContain('access-a');
    expect(useAuthStore.getState().token).toBe('access-a');
  });

  it('同值事件不再写盘（无乒乓）：只有值真的变了才 setState', () => {
    fireStorage(AUTH_STORAGE_KEY, envelope('rt-old')); // 与内存同值
    let notifications = 0;
    const un = useAuthStore.subscribe(() => {
      notifications += 1;
    });
    fireStorage(AUTH_STORAGE_KEY, envelope('rt-old'));
    fireStorage(AUTH_STORAGE_KEY, envelope('rt-old'));
    un();
    expect(notifications).toBe(0);
  });

  it('另一标签页登出（票被清空）→ 本页一并清态，保持单会话语义', () => {
    fireStorage(AUTH_STORAGE_KEY, envelope(null));
    expect(useAuthStore.getState().refreshToken).toBeNull();
    expect(useAuthStore.getState().token).toBeNull();
  });

  it('另一标签页 removeItem → 本页清态', () => {
    fireStorage(AUTH_STORAGE_KEY, null);
    expect(useAuthStore.getState().refreshToken).toBeNull();
  });

  it('坏 JSON / 别的 key → 不动内存（留给 401 兜底）', () => {
    fireStorage(AUTH_STORAGE_KEY, '{not json');
    fireStorage('some-other-app-key', envelope('rt-attacker'));
    expect(useAuthStore.getState().refreshToken).toBe('rt-old');
  });

  it('卸载后不再响应（防热更新/测试残留监听）', () => {
    stop();
    fireStorage(AUTH_STORAGE_KEY, envelope('rt-after-stop'));
    expect(useAuthStore.getState().refreshToken).toBe('rt-old');
  });
});
