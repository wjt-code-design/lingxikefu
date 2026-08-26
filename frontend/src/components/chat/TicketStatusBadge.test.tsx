import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import { TicketStatusBadge } from './TicketStatusBadge';
import { getTicket } from '@/api/tickets';
import type { TicketItem } from '@/contracts/api';

/** 轮询纪律回归：页面不可见不发请求；到达终态停止轮询；非终态持续轮询。 */

vi.mock('@/api/tickets', () => ({ getTicket: vi.fn() }));

const mocked = vi.mocked(getTicket);

/** jsdom 恒为 visible：用 getter 覆盖 document.hidden（组件轮询前同步读取） */
function setHidden(hidden: boolean) {
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden });
}

function ticket(status: TicketItem['status']): TicketItem {
  return {
    ticket_id: 't-1', session_id: 's-1', message_id: null, status,
    source: 'manual', assignee_id: null,
    created_at: '2026-08-22T00:00:00Z', updated_at: '2026-08-22T00:00:00Z', version: 0,
  };
}

describe('TicketStatusBadge 轮询纪律', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mocked.mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
    setHidden(false);
  });

  it('页面不可见时不发请求', async () => {
    setHidden(true);
    mocked.mockResolvedValue(ticket('processing'));
    render(<TicketStatusBadge ticketId="t-1" />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000);
    });
    expect(mocked).not.toHaveBeenCalled();
  });

  it('到达终态（closed）后停止轮询', async () => {
    mocked.mockResolvedValue(ticket('closed'));
    render(<TicketStatusBadge ticketId="t-1" />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(mocked).toHaveBeenCalledTimes(1);
    expect(screen.getByText('已关闭')).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000); // 推进 3 个轮询周期
    });
    expect(mocked).toHaveBeenCalledTimes(1);
  });

  it('非终态按周期持续轮询', async () => {
    mocked.mockResolvedValue(ticket('processing'));
    render(<TicketStatusBadge ticketId="t-1" />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(11_000);
    });
    // 初始 1 次 + 5s/10s 各 1 次 = 3 次（宽口径 ≥3 防计时抖动误报）
    expect(mocked.mock.calls.length).toBeGreaterThanOrEqual(3);
    expect(screen.getByText('处理中')).toBeInTheDocument();
  });

  it('同一 ticketId 多角标共享一个轮询（不重复发请求）', async () => {
    mocked.mockResolvedValue(ticket('processing'));
    render(
      <>
        <TicketStatusBadge ticketId="t-1" />
        <TicketStatusBadge ticketId="t-1" />
      </>,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(11_000);
    });
    // P4：共享轮询 → 两角标只产生 1 条轮询链（初始 1 次 + 5s/10s 各 1 次 = 3 次），非各自 6 次
    expect(mocked.mock.calls.length).toBe(3);
    expect(screen.getAllByText('处理中')).toHaveLength(2);
  });

  it('不同 ticketId 各自独立轮询', async () => {
    mocked.mockResolvedValue(ticket('processing'));
    render(
      <>
        <TicketStatusBadge ticketId="t-1" />
        <TicketStatusBadge ticketId="t-2" />
      </>,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(11_000);
    });
    // 两条独立轮询链：每条 3 次，共 6 次
    expect(mocked.mock.calls.length).toBe(6);
  });
});
