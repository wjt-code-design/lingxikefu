import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { useChatStream } from './useChatStream';
import { refreshAccessToken } from '@/api/client';
import { useAuthStore } from '@/store/authStore';

/** H3（外部审查 2026-08-22）：用户点"停止生成"后 stage 必须落到终止态。
 *  旧实现 stop() 只 abort，AbortError 非超时分支静默 → streaming 恒真，输入区永久禁用。 */

/** B2：SSE fetch 401 → 走共享 refresh 续期后重试（旧实现直接报错，须整页刷新才能继续）。 */
vi.mock('@/api/client', () => ({
  refreshAccessToken: vi.fn(async () => 'new-token'),
}));

/** 可编排的 fetch 替身：先吐两个 SSE 帧（stage=generating + token），随后挂起直到 abort。 */
function installScriptedFetch() {
  const pending: string[] = [
    'data: {"event":"stage","data":{"stage":"generating"}}\n\n',
    'data: {"event":"token","data":{"delta":"部分回答"}}\n\n',
  ];
  vi.stubGlobal(
    'fetch',
    vi.fn((_url: string, init?: { signal?: AbortSignal }) =>
      Promise.resolve({
        ok: true,
        body: {
          getReader: () => ({
            read: () =>
              new Promise((resolve, reject) => {
                if (pending.length > 0) {
                  const text = pending.shift()!;
                  queueMicrotask(() =>
                    resolve({ done: false, value: new TextEncoder().encode(text) })
                  );
                } else {
                  init?.signal?.addEventListener('abort', () => {
                    const e = new Error('aborted');
                    e.name = 'AbortError';
                    reject(e);
                  });
                }
              }),
          }),
        },
      })
    )
  );
}

describe('useChatStream 停止生成（H3）', () => {
  beforeEach(() => installScriptedFetch());
  afterEach(() => vi.unstubAllGlobals());

  it('停止后 stage 落到 done、已收 tokens 保留', async () => {
    const { result } = renderHook(() => useChatStream());
    act(() => {
      void result.current.stream({ session_id: null, content: '问点啥' } as never);
    });
    await act(async () => {}); // 交付 generating + token 两帧
    expect(result.current.stage).toBe('generating');
    expect(result.current.tokens).toBe('部分回答');

    act(() => {
      result.current.stop();
    });
    // H3：旧实现此处仍是 generating（streaming 恒真，输入区永久禁用）→ 红
    expect(result.current.stage).toBe('done');
    expect(result.current.tokens).toBe('部分回答');
  });
});

describe('useChatStream SSE 401 续期重试（B2）', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
    useAuthStore.setState({ token: null, refreshToken: null, user: null, role: null });
  });

  it('401 → 共享 refresh 拿新 token → 带新 token 重试成功', async () => {
    const authHeaders: (string | undefined)[] = [];
    const frames = ['data: {"event":"done","data":{"message_id":"m1"}}\n\n'];
    vi.stubGlobal(
      'fetch',
      vi.fn((_url: string, init: { headers: Record<string, string> }) => {
        authHeaders.push(init.headers.Authorization);
        if (authHeaders.length === 1) {
          // 首次：旧 token 过期
          return Promise.resolve({ ok: false, status: 401 });
        }
        // 重试：正常 SSE 流（一帧 done 后正常关闭）
        return Promise.resolve({
          ok: true,
          status: 200,
          body: {
            getReader: () => ({
              read: () => {
                const text = frames.length > 0 ? frames.shift()! : null;
                return Promise.resolve(
                  text === null
                    ? { done: true, value: undefined }
                    : { done: false, value: new TextEncoder().encode(text) }
                );
              },
            }),
          },
        });
      })
    );
    useAuthStore.setState({ token: 'expired', refreshToken: 'rt' });

    const { result } = renderHook(() => useChatStream());
    act(() => {
      void result.current.stream({ session_id: null, content: '问点啥' } as never);
    });
    await act(async () => {});

    // B2：旧实现此处 stage=error（HTTP 401），必须整页刷新才能继续 → 红
    expect(result.current.stage).toBe('done');
    expect(authHeaders).toEqual(['Bearer expired', 'Bearer new-token']);
    expect(refreshAccessToken).toHaveBeenCalledTimes(1);
  });
});

describe('useChatStream 服务端中途断连兜底（B3）', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('流被关闭且无 done/error 事件 → 落到 error 终止态', async () => {
    const frames = [
      'data: {"event":"stage","data":{"stage":"generating"}}\n\n',
      'data: {"event":"token","data":{"delta":"部分回答"}}\n\n',
    ];
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          status: 200,
          body: {
            getReader: () => ({
              read: () => {
                const text = frames.length > 0 ? frames.shift()! : null;
                return Promise.resolve(
                  text === null
                    ? { done: true, value: undefined }
                    : { done: false, value: new TextEncoder().encode(text) }
                );
              },
            }),
          },
        })
      )
    );

    const { result } = renderHook(() => useChatStream());
    act(() => {
      void result.current.stream({ session_id: null, content: '问点啥' } as never);
    });
    await act(async () => {});

    // B3：旧实现 stage 永远停在 generating（streaming 恒真，输入区永久禁用）→ 红
    expect(result.current.stage).toBe('error');
    expect(result.current.error?.code).toBe('STREAM_ENDED');
    expect(result.current.tokens).toBe('部分回答'); // 已收内容保留
  });
});
