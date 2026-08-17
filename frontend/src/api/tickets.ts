import { http } from './client';
import type { TicketItem, TicketListResp, TicketStatus } from '@/contracts/api';

/** 工单列表（agent/admin；status 可选过滤） */
export function listTickets(status?: TicketStatus, page = 1, size = 20): Promise<TicketListResp> {
  return http
    .get<TicketListResp>('/tickets', {
      params: { status: status ?? undefined, page, size },
    })
    .then((r) => r.data);
}

/** 状态流转 + 分配（agent/admin） */
export function updateTicket(
  ticketId: string,
  data: { status: TicketStatus; assignee_id?: string }
): Promise<TicketItem> {
  return http.patch<TicketItem>(`/tickets/${ticketId}`, data).then((r) => r.data);
}

/** 用户主动转人工（P0-4）：升级会话为工单（user 可调，幂等；越权 403） */
export function escalateSession(sessionId: string): Promise<TicketItem> {
  return http.post<TicketItem>(`/tickets/escalate/${sessionId}`).then((r) => r.data);
}

/** 我的工单（P2-1，user 可调）：只返回当前用户会话的工单 */
export function listMyTickets(status?: TicketStatus, page = 1, size = 20): Promise<TicketListResp> {
  return http
    .get<TicketListResp>('/tickets/my', {
      params: { status: status ?? undefined, page, size },
    })
    .then((r) => r.data);
}
