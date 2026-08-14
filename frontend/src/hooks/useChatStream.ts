import { useCallback, useState } from 'react';
import type { ChatStreamReq, MessageSource } from '@/contracts/api';

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

/**
 * SSE 流式 hook 骨架（FE-03 填充）。
 * 规划锁定方案：fetch + ReadableStream（原生 EventSource 不支持 POST + body）。
 * 契约：SSEEvent = stage | token | sources | done | error（见 contracts/api.ts）
 */
export function useChatStream() {
  const [state, setState] = useState<ChatStreamState>(INITIAL);

  const reset = useCallback(() => setState(INITIAL), []);

  /** 发起流式请求并逐事件更新本地状态（TODO(FE-03)：解析 SSE 事件流） */
  const stream = useCallback(async (_req: ChatStreamReq): Promise<void> => {
    throw new Error('TODO(FE-03): 用 fetch + ReadableStream 实现 SSE 消费');
  }, []);

  return { ...state, reset, stream };
}
