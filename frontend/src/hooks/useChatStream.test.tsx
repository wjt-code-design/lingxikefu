import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { useChatStream } from './useChatStream';

/** H3（外部审查 2026-08-22）：用户点"停止生成"后 stage 必须落到终止态。
 *  旧实现 stop() 只 abort，AbortError 非超时分支静默 → streaming 恒真，输入区永久禁用。 */

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
