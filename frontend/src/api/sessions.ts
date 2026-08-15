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
    updated_at: s.created_at,
  };
}

/** 创建会话（挂件首次发问时调用）。 */
export async function createSession(title?: string): Promise<Session> {
  const r = await http.post<BackendSession>('/sessions', { title });
  return toSession(r.data);
}

export async function listSessions(req: SessionListReq): Promise<SessionListResp> {
  const r = await http.get<{ items: BackendSession[] }>('/sessions', { params: req });
  return { items: r.data.items.map(toSession), total: r.data.items.length };
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
