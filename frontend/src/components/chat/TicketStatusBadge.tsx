import { useEffect, useState } from 'react';
import { StatusTag, TICKET_STATUS_TEXT } from '@/components/common/AppTable';
import { getTicket } from '@/api/tickets';
import type { TicketStatus } from '@/contracts/api';

/** 工单终态：到达后状态不再变化，轮询可停 */
const TICKET_TERMINAL_STATUS: readonly TicketStatus[] = ['resolved', 'closed'];

/**
 * 工单状态角标（聊天页通用：工单气泡 / 消息内工单 chip 复用）。
 * 轮询单工单状态实时展示处理进度（顾客侧感知自动流转，无需手动刷新）；
 * 到达终态（resolved/closed）或页面不可见（后台标签页）时不再发请求。
 * staff/observe 视角同样生效（后端接口对 agent/admin 放行）。
 */
export function TicketStatusBadge({ ticketId }: { ticketId: string }) {
  const [status, setStatus] = useState<TicketStatus | null>(null);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setInterval> | undefined;
    const load = async () => {
      if (document.hidden) return; // 后台标签页静默：不发请求
      try {
        const t = await getTicket(ticketId);
        if (!alive) return;
        setStatus(t.status);
        if (TICKET_TERMINAL_STATUS.includes(t.status) && timer) {
          clearInterval(timer); // 终态停轮询，不再空转
        }
      } catch {
        /* 轮询失败静默：网络抖动/工单暂不可见时不报错，下一轮重试 */
      }
    };
    void load();
    timer = setInterval(load, 5_000);
    return () => {
      alive = false;
      if (timer) clearInterval(timer);
    };
  }, [ticketId]);

  if (!status) return null;
  return (
    <div className="chat-msg__ticket-status">
      <StatusTag status={status} text={TICKET_STATUS_TEXT[status] ?? status} />
    </div>
  );
}

export default TicketStatusBadge;
