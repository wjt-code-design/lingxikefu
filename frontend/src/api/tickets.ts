import { http } from './client';
import { type StatusUpdateReq, type TicketItem, type TicketListResp, type TicketStatus } from '@/contracts/api';

/** 工单列表（agent/admin；status 可选过滤；keyword 搜工单号/会话号，UI 审查中6） */
export function listTickets(
  status?: TicketStatus,
  page = 1,
  size = 20,
  keyword?: string
): Promise<TicketListResp> {
  return http
    .get<TicketListResp>('/tickets', {
      params: { status: status ?? undefined, page, size, keyword: keyword || undefined },
    })
    .then((r) => r.data);
}

/** 用户侧「我的工单」列表（D2）：仅返回本人会话归属的工单；status 可选过滤 */
export function listMyTickets(
  status?: TicketStatus,
  page = 1,
  size = 20
): Promise<TicketListResp> {
  return http
    .get<TicketListResp>('/tickets/mine', {
      params: { status: status ?? undefined, page, size },
    })
    .then((r) => r.data);
}

/** 单工单查询（聊天页角标轮询用；user 仅可查自己的工单） */
export function getTicket(ticketId: string): Promise<TicketItem> {
  return http.get<TicketItem>(`/tickets/${ticketId}`).then((r) => r.data);
}

/** 状态流转 + 分配（agent/admin）；S2 乐观锁：须回传当前 version，冲突返回 409 */
export function updateTicket(ticketId: string, data: StatusUpdateReq): Promise<TicketItem> {
  return http.patch<TicketItem>(`/tickets/${ticketId}`, data).then((r) => r.data);
}

/** 用户主动转人工（P0-4）：升级会话为工单（user 可调，幂等；越权 403） */
export function escalateSession(sessionId: string): Promise<TicketItem> {
  return http.post<TicketItem>(`/tickets/escalate/${sessionId}`).then((r) => r.data);
}

/** 客服手动建单（agent/admin；POST /tickets，staff 专属，契约无独立 TS 类型故内联）。
 *  与 转人工（escalate）区分：建单仅记录工单，不切换客服介入视角。 */
export function createTicket(sessionId: string): Promise<TicketItem> {
  return http
    .post<TicketItem>('/tickets', { session_id: sessionId })
    .then((r) => r.data);
}
