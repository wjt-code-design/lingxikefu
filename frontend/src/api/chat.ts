import { http } from '@/api/client';
import type { FeedbackReq } from '@/contracts/api';

/**
 * 对话接口。
 * 流式问答走 hooks/useChatStream.ts（fetch + ReadableStream 消费 SSE）；
 * 本文件承载非流式/反馈类接口。
 */

/** 对 assistant 消息提交 up/down 反馈（幂等，重复提交更新）。 */
export async function sendFeedback(messageId: string, req: FeedbackReq): Promise<void> {
  await http.post(`/messages/${messageId}/feedback`, req);
}
