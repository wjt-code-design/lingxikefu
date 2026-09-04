import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ConfigProvider } from 'antd';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { TicketsPage } from '@/pages/agent/TicketsPage';
import { DashboardPage } from '@/pages/agent/DashboardPage';
import { MyTicketsPage } from '@/pages/MyTicketsPage';
import type { Session, TicketItem, TicketListResp } from '@/contracts/api';

/**
 * P3 回归（交接文档 §四-P3，2026-09-04）：
 * 三处「查看对话」曾用 Typography.Link（无 href 的 <a>，AX 树不暴露 role、
 * 自动化脆弱）。/agent/sessions 的真 <button> 是既定姿势 → 三页统一改 Button。
 */

const TICKET: TicketItem = {
  ticket_id: '3f2a1b4c-0000-0000-0000-000000000001',
  session_id: 'sess-1',
  status: 'open',
  created_at: '2026-09-01T00:00:00Z',
  updated_at: '2026-09-01T00:00:00Z',
  version: 1,
};

const SESSION: Session = {
  id: 'sess-1',
  title: '退款咨询',
  created_at: '2026-09-01T00:00:00Z',
  updated_at: '2026-09-01T00:00:00Z',
};

vi.mock('@/api/sessions', () => ({
  listSessions: vi.fn((): Promise<{ items: Session[]; total: number }> =>
    Promise.resolve({ items: [SESSION], total: 1 })
  ),
}));
vi.mock('@/api/tickets', () => ({
  listTickets: vi.fn((): Promise<TicketListResp> =>
    Promise.resolve({ items: [TICKET], total: 1 })
  ),
  listMyTickets: vi.fn((): Promise<TicketListResp> =>
    Promise.resolve({ items: [TICKET], total: 1 })
  ),
  updateTicket: vi.fn(),
}));

function withProviders(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <ConfigProvider>
      <QueryClientProvider client={client}>
        <MemoryRouter>{ui}</MemoryRouter>
      </QueryClientProvider>
    </ConfigProvider>
  );
}

describe('P3：「查看对话」必须是可被 AX 树暴露的 button', () => {
  it('/agent/tickets 列表行内「查看对话」渲染为 button', async () => {
    withProviders(<TicketsPage />);
    const btn = await screen.findByRole('button', { name: '查看对话' });
    expect(btn).toBeInTheDocument();
  });

  it('/agent/dashboard 待办工单「查看对话」渲染为 button', async () => {
    withProviders(<DashboardPage />);
    const btn = await screen.findByRole('button', { name: '查看对话' });
    expect(btn).toBeInTheDocument();
  });

  it('/tickets（用户侧我的工单）「查看对话」渲染为 button', async () => {
    withProviders(<MyTicketsPage />);
    const btn = await screen.findByRole('button', { name: '查看对话' });
    expect(btn).toBeInTheDocument();
  });
});
