import { http } from '@/api/client';
import type { Session, SessionDetail, SessionListReq, SessionListResp } from '@/contracts/api';

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

interface BackendSessionDetail extends BackendSession {
  messages: Array<{
    id: string;
    role: 'user' | 'assistant';
    content: string;
    created_at: string;
  }>;
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

export async function getSessionDetail(id: string): Promise<SessionDetail> {
  const r = await http.get<BackendSessionDetail>(`/sessions/${id}`);
  return {
    id: r.data.session_id,
    title: r.data.title ?? undefined,
    messages: r.data.messages.map((m) => ({
      ...m,
      session_id: r.data.session_id,
    })),
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
