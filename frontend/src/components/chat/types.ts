import type { MessageSource } from '@/contracts/api';

/** 会话消息（本地态；messageId 为后端落库后的消息 id，用于反馈）。
 * role 扩展（2026-08-19）：增加 'agent' 支持人工客服消息——契约 P2。
 *   - user 视角下：user → self，agent / assistant → other
 *   - observe 视角下（agent/admin 打开既有会话）：user / assistant → other，agent → self */
export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'agent';
  content: string;
  sources?: MessageSource[];
  messageId?: string;
  ticketId?: string; // T1：handoff 建单后携带工单号（用户侧提示）
  /** 批次D/T3：工具回答标记（如 order_query）→ 气泡「订单查询」徽章；普通 RAG/LLM 回答无此字段 */
  tool?: string;
  /** P0-1：消息生命周期（sending=发送中 / done=完成 / failed=失败可重试）。历史加载默认 done。 */
  status?: 'sending' | 'done' | 'failed';
  feedback?: 'up' | 'down' | null;
  /** 消息创建时间戳（毫秒），用于时间分隔线 */
  createdAt?: number;
  /** 仅 agent 角色携带：客服昵称（气泡身份标签用） */
  agentName?: string;
}
