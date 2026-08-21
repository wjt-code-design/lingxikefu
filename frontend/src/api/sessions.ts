import { http } from '@/api/client';
import type { Message, Session, SessionDetail, SessionListReq, SessionListResp } from '@/contracts/api';

/**
 * 会话接口。
 * 后端返回字段为 session_id（契约统一为 id），此处做字段映射（API 适配层）。
 */

interface BackendSession {
  session_id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
  satisfaction: string | null;
  user_email: string | null; // BUG-12：客户标识
  user_phone: string | null;
}

/** 会话详情响应：后端字段为 id（非 session_id，见 getSessionDetail 映射）。 */
interface BackendSessionDetail {
  id: string;
  title: string | null;
  messages: Message[];
  profile?: SessionDetail['profile']; // Phase D：用户画像（agent/admin 视角返回；顾客端 undefined）
}

function toSession(s: BackendSession): Session {
  return {
    id: s.session_id,
    title: s.title ?? undefined,
    created_at: s.created_at,
    updated_at: s.updated_at,
    satisfaction: s.satisfaction ?? undefined,
    user_email: s.user_email ?? undefined, // BUG-12
    user_phone: s.user_phone ?? undefined,
  };
}

/** 创建会话（挂件首次发问时调用）。 */
export async function createSession(title?: string): Promise<Session> {
  const r = await http.post<BackendSession>('/sessions', { title });
  return toSession(r.data);
}

export async function listSessions(req: SessionListReq): Promise<SessionListResp> {
  const r = await http.get<{ items: BackendSession[]; total: number }>('/sessions', { params: req });
  return { items: r.data.items.map(toSession), total: r.data.total };
}

/**
 * 会话详情（含消息历史）。
 * limit：返回最新 N 条（升序时间线），默认 200；审计 Drawer 拉全量上下文可传大值（上限 1000）。
 */
export async function getSessionDetail(id: string, limit?: number): Promise<SessionDetail> {
  const r = await http.get<BackendSessionDetail>(`/sessions/${id}`, { params: limit ? { limit } : {} });
  return {
    // BUG-FIX（Branch 3 顺带）：后端详情响应字段是 id，此前误读 session_id →
    // ?session= 打开时 sessionId 恒为 undefined，staff 转人工/建单静默失效。
    id: r.data.id,
    title: r.data.title ?? undefined,
    messages: r.data.messages.map((m) => ({
      ...m,
      session_id: r.data.id,
    })),
    profile: r.data.profile, // Phase D：透传画像（agent/admin 可见；顾客端 undefined）
  };
}

/** 删除会话（T4：owner/admin；含未关闭工单的会话后端返回 409）。 */
export async function deleteSession(id: string): Promise<void> {
  await http.delete(`/sessions/${id}`);
}

/** 会话级满意度评分（P2-2：satisfied / neutral / unsatisfied，幂等覆盖）。 */
export async function rateSatisfaction(
  id: string,
  rating: 'satisfied' | 'neutral' | 'unsatisfied'
): Promise<void> {
  await http.post(`/sessions/${id}/satisfaction`, { rating });
}

/** 人工客服代发消息（Branch 3）：仅 agent/admin；落库 role='agent'，顾客端轮询/刷新可见。 */
export async function sendAgentMessage(id: string, content: string): Promise<Message> {
  const r = await http.post<Omit<Message, 'session_id'>>(`/sessions/${id}/messages`, { content });
  return { ...r.data, session_id: id };
}
