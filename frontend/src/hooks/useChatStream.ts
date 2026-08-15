import { useCallback, useRef, useState } from 'react';
import { API_PREFIX, type ChatStreamReq, type MessageSource, type SSEEvent } from '@/contracts/api';
import { useAuthStore } from '@/store/authStore';

/** 流式对话的本地状态（与契约 SSEStage 对齐 + idle 初始态） */
export type ChatStage = 'idle' | 'retrieving' | 'generating' | 'done' | 'error';

export interface ChatStreamState {
  stage: ChatStage;
  tokens: string;
  sources: MessageSource[];
  messageId?: string;
  error?: { code: string; message: string };
}

const INITIAL: ChatStreamState = { stage: 'idle', tokens: '', sources: [] };

/** 后端 SSE 事件分派（stage/token/sources/done/error，见 contracts/api.ts SSEEvent） */
function applyEvent(state: ChatStreamState, ev: SSEEvent): ChatStreamState {
  switch (ev.event) {
    case 'stage':
      return { ...state, stage: ev.data.stage, error: undefined };
    case 'token':
      return { ...state, tokens: state.tokens + ev.data.delta };
    case 'sources':
      return { ...state, sources: ev.data.sources };
    case 'done':
      return { ...state, stage: 'done', messageId: ev.data.message_id };
    case 'error':
      return { ...state, stage: 'error', error: ev.data };
  }
}

/**
 * SSE 流式对话 hook（FE-03 实现）。
 * 原生 EventSource 不支持 POST + body → fetch + ReadableStream：
 * 逐块读取，按空行切分 SSE 帧，解析 `data: {json}` 行后按 event 分派。
 */
export function useChatStream() {
  const [state, setState] = useState<ChatStreamState>(INITIAL);
  const abortRef = useRef<AbortController | null>(null);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    setState(INITIAL);
  }, []);

  /** 发起流式请求并逐事件更新本地状态。可被 AbortController 中断（新请求/卸载）。 */
  const stream = useCallback(async (req: Omit<ChatStreamReq, 'stream'>): Promise<void> => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const token = useAuthStore.getState().token;

    const base = import.meta.env.VITE_API_BASE || API_PREFIX;
    setState({ stage: 'retrieving', tokens: '', sources: [] });
    try {
      const resp = await fetch(`${base}/chat/stream`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ ...req, stream: true }),
        signal: controller.signal,
      });
      if (!resp.ok || !resp.body) {
        setState((s) => ({
          ...s,
          stage: 'error',
          error: { code: 'HTTP', message: `请求失败（HTTP ${resp.status}），请稍后重试` },
        }));
        return;
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let buf = '';
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        // 按空行切分 SSE 帧（后端 _sse 输出 `data: {...}\n\n`）
        let idx: number;
        while ((idx = buf.indexOf('\n\n')) >= 0) {
          const frame = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          const line = frame.split('\n').find((l) => l.startsWith('data: '));
          if (!line) continue;
          let ev: SSEEvent;
          try {
            ev = JSON.parse(line.slice(6)) as SSEEvent;
          } catch {
            continue; // 非 JSON 帧（心跳/空行）忽略
          }
          setState((s) => applyEvent(s, ev));
        }
      }
    } catch (e) {
      if ((e as Error).name !== 'AbortError') {
        setState((s) => ({
          ...s,
          stage: 'error',
          error: { code: 'NET', message: '网络连接失败，请检查网络后重试' },
        }));
      }
    }
  }, []);

  return { ...state, reset, stream };
}
