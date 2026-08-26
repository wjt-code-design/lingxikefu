import { useEffect, useState } from 'react';
import { StatusTag, TICKET_STATUS_TEXT } from '@/components/common/AppTable';
import { getTicket } from '@/api/tickets';
import type { TicketStatus } from '@/contracts/api';

/** 工单终态：到达后状态不再变化，轮询可停 */
const TICKET_TERMINAL_STATUS: readonly TicketStatus[] = ['resolved', 'closed'];
const POLL_MS = 5_000;

/**
 * P4：同 ticketId 共享一份轮询。聊天气泡 chip 与聊天头角标可能同屏渲染同一工单，
 * 若各自开 setInterval 会对同一接口重复发请求（两倍空转）。这里用模块级注册表：
 * 首个订阅者启动轮询，后续订阅者只收推送；最后一个订阅者卸载后停止。
 */
type StatusListener = (status: TicketStatus) => void;
const listeners = new Map<string, Set<StatusListener>>();
const timers = new Map<string, ReturnType<typeof setInterval>>();

function stopPoll(ticketId: string) {
  const timer = timers.get(ticketId);
  if (timer) {
    clearInterval(timer);
    timers.delete(ticketId);
  }
}

function ensurePolling(ticketId: string) {
  if (timers.has(ticketId)) return;
  const load = async () => {
    // 后台标签页静默：不发请求（与旧版逐组件行为一致）
    if (document.hidden) return;
    try {
      const t = await getTicket(ticketId);
      const subs = listeners.get(ticketId);
      if (!subs || subs.size === 0) return; // 查询期间订阅者已全部离开
      for (const fn of subs) fn(t.status);
      if (TICKET_TERMINAL_STATUS.includes(t.status)) {
        stopPoll(ticketId); // 终态后状态不再变化，无需继续轮询
      }
    } catch {
      /* 轮询失败静默：网络抖动/工单暂不可见时不报错，下一轮重试 */
    }
  };
  void load();
  timers.set(ticketId, setInterval(load, POLL_MS));
}

/**
 * 工单状态角标（聊天页通用：工单气泡 / 消息内工单 chip 复用）。
 * 轮询单工单状态实时展示处理进度（顾客侧感知自动流转，无需手动刷新）；
 * 到达终态（resolved/closed）或页面不可见（后台标签页）时不再发请求。
 * staff/observe 视角同样生效（后端接口对 agent/admin 放行）。
 */
export function TicketStatusBadge({ ticketId }: { ticketId: string }) {
  const [status, setStatus] = useState<TicketStatus | null>(null);
  useEffect(() => {
    const onChange = (s: TicketStatus) => setStatus(s);
    const subs = listeners.get(ticketId) ?? new Set<StatusListener>();
    subs.add(onChange);
    listeners.set(ticketId, subs);
    ensurePolling(ticketId);
    return () => {
      subs.delete(onChange);
      if (subs.size === 0) listeners.delete(ticketId);
      if (!listeners.has(ticketId)) stopPoll(ticketId);
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