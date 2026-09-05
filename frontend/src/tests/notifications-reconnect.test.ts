import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { subscribeNotifications } from '@/api/notifications';
import { useAuthStore } from '@/store/authStore';

/**
 * B1-5（2026-09-06 深度审查）：通知 SSE 断线自动重连。
 * 旧实现单次 fetch 流，服务端关闭/网络抖动后 for(;;) 退出即静默停更——
 * 角标靠 30s 轮询还准，但面板内新通知列表从此不再增量，直到手动开面板。
 */

// fetch 返回一个「立即关闭」的流（reader.read 首帧 done）→ 触发重连路径
function closingStreamFetch() {
  return vi.fn(() =>
    Promise.resolve({
      ok: true,
      body: { getReader: () => ({ read: () => Promise.resolve({ done: true, value: undefined }) }) },
    })
  );
}

describe('B1-5 通知 SSE 断线重连', () => {
  beforeEach(() => {
    useAuthStore.setState({ token: 't', refreshToken: 'r', role: 'agent' });
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('流结束后按退避重连；取消后不再重连', async () => {
    const fetchMock = closingStreamFetch();
    vi.stubGlobal('fetch', fetchMock);

    const unsub = subscribeNotifications(() => {}, { backoffMs: 100 });
    // 初次连接（同步发起）
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    // 推进超过首个退避窗口 → 应重连一次
    await vi.advanceTimersByTimeAsync(300);
    expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(2);

    // 取消订阅 → 后续不再重连
    const callsAtUnsub = fetchMock.mock.calls.length;
    unsub();
    await vi.advanceTimersByTimeAsync(5000);
    expect(fetchMock.mock.calls.length).toBe(callsAtUnsub);
  });

  it('连续网络失败时退避逐次增长（防重连风暴）', async () => {
    // 注意：连接「成功但流立即关闭」不增长退避（attempt 成功即归零——长连接断后
    // 应快速重连）。增长性只在 fetch 本身失败（网络断/后端 down）时体现。
    // Math.random=1 → delay 精确：attempt0=100ms、attempt1=200ms。
    vi.spyOn(Math, 'random').mockReturnValue(1);
    const fetchMock = vi.fn(() => Promise.reject(new Error('network down')));
    vi.stubGlobal('fetch', fetchMock);
    const unsub = subscribeNotifications(() => {}, { backoffMs: 100 });
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).toHaveBeenCalledTimes(1); // 初始连接
    await vi.advanceTimersByTimeAsync(100);
    expect(fetchMock).toHaveBeenCalledTimes(2); // 第一次失败退避 100ms 后重连
    // 第二次退避 = 200ms：再推进 150ms（t=250）不得重连
    await vi.advanceTimersByTimeAsync(150);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(50); // t=300 到点
    expect(fetchMock).toHaveBeenCalledTimes(3);
    unsub();
  });
});
