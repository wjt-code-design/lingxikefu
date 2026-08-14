import type { ChatStreamReq, FeedbackReq } from '@/contracts/api';

// TODO(FE-03): 流式问答经 hooks/useChatStream.ts 用 fetch + ReadableStream 消费；
// 本文件预留非流式/反馈接口签名。

export async function sendChatMessage(_req: ChatStreamReq): Promise<void> {
  throw new Error('TODO(FE-03): 问答接口待实现（流式走 useChatStream）');
}

export async function sendFeedback(_req: FeedbackReq): Promise<void> {
  throw new Error('TODO(FE-03): 反馈接口待实现');
}
