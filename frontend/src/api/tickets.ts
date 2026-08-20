import { http } from './client';
import { API_PREFIX, type MyTicketSSEEvent, type StatusUpdateReq, type TicketItem, type TicketListResp, type TicketStatus } from '@/contracts/api';
import { parseSSEFrame } from '@/api/sse';
import { useAuthStore } from '@/store/authStore';

/** 工单列表（agent/admin；status 可选过滤） */
export function listTickets(status?: TicketStatus, page = 1, size = 20): Promise<TicketListResp> {
  return http
    .get<TicketListResp>('/tickets', {
      params: { status: status ?? undefined, page, size },
    })
    .then((r) => r.data);
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

/** 我的工单（P2-1，user 可调）：只返回当前用户会话的工单 */
export function listMyTickets(status?: TicketStatus, page = 1, size = 20): Promise<TicketListResp> {
  return http
    .get<TicketListResp>('/tickets/my', {
      params: { status: status ?? undefined, page, size },
    })
    .then((r) => r.data);
}

/**
 * 订阅用户侧工单状态实时推送（第6组项4）。返回取消函数（组件卸载/登出调用）。
 * 尽力而为（单 worker 进程内分发）；断线/漏事件由调用方保留的轮询兜底——推送失效不会静默丢更新。
 */
export function subscribeMyTicketStream(
  onUpdate: (e: { ticket_id: string; status: TicketStatus }) => void
): () => void {
  const controller = new AbortController();
  const token = useAuthStore.getState().token;
  const base = import.meta.env.VITE_API_BASE || API_PREFIX;

  void (async () => {
    try {
      const resp = await fetch(`${base}/tickets/my/stream`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        signal: controller.signal,
      });
      if (!resp.ok || !resp.body) return;
      const reader = resp.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let buf = '';
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let idx: number;
        while ((idx = buf.indexOf('\n\n')) >= 0) {
          const frame = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          const ev = parseSSEFrame<MyTicketSSEEvent>(frame);
          if (ev?.event === 'ticket_update') {
            onUpdate({ ticket_id: ev.data.ticket_id, status: ev.data.status });
          }
        }
      }
    } catch {
      /* AbortError（卸载）或网络异常：静默，由轮询兜底 */
    }
  })();

  return () => controller.abort();
}
