import type { MessageSource } from '@/contracts/api';

/** 会话消息（本地态；messageId 为后端落库后的消息 id，用于反馈）。 */
export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  sources?: MessageSource[];
  messageId?: string;
  feedback?: 'up' | 'down' | null;
}
