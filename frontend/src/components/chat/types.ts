import type { MessageSource } from '@/contracts/api';

/** 会话消息（本地态；messageId 为后端落库后的消息 id，用于反馈）。 */
export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  sources?: MessageSource[];
  messageId?: string;
  ticketId?: string; // T1：handoff 建单后携带工单号（用户侧提示）
  /** P0-1：消息生命周期（sending=发送中 / done=完成 / failed=失败可重试）。历史加载默认 done。 */
  status?: 'sending' | 'done' | 'failed';
  feedback?: 'up' | 'down' | null;
  /** 消息创建时间戳（毫秒），用于时间分隔线 */
  createdAt?: number;
}
