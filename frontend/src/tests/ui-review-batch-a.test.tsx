import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent, cleanup } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ConfigProvider } from 'antd';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { TicketsPage } from '@/pages/agent/TicketsPage';
import { UsersPage } from '@/pages/admin/UsersPage';
import { EvalPage } from '@/pages/admin/EvalPage';
import { MessageList } from '@/components/chat/MessageList';
import { listTickets as mockListTickets, updateTicket as mockUpdateTicket } from '@/api/tickets';
import { listUsers as mockListUsers, updateUserRole as mockUpdateUserRole } from '@/api/admin';
import { getEvalLatest as mockGetEvalLatest } from '@/api/eval';
import type { ChatMessage } from '@/components/chat/types';

/**
 * UI 审查中优先级批次回归测试（2026-09-01）：
 * - 中6：TicketsPage keyword 接线（300ms 防抖后透传 listTickets）
 * - 中7：UsersPage keyword 接线
 * - 中11：EvalPage 指标中文化（qa/honesty 不再裸英文）
 * - 中10：MessageList 欢迎页不自动滚底
 */

vi.mock('@/api/tickets', () => ({
  listTickets: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  updateTicket: vi.fn().mockResolvedValue({}),
}));
vi.mock('@/api/admin', () => ({
  listUsers: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  updateUserRole: vi.fn().mockResolvedValue({ ok: true }),
}));
vi.mock('@/api/eval', () => ({
  getEvalLatest: vi.fn().mockResolvedValue({ has_history: false, latest: null, alerts: [] }),
  getEvalHistory: vi.fn().mockResolvedValue({ items: [] }),
  runEval: vi.fn().mockResolvedValue({}),
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

beforeEach(() => {
  vi.mocked(mockListTickets).mockClear();
  vi.mocked(mockUpdateTicket).mockClear();
  vi.mocked(mockListUsers).mockClear();
  vi.mocked(mockUpdateUserRole).mockClear();
});

afterEach(() => {
  cleanup();
});

describe('中6：工单列表 keyword 搜索', () => {
  it('输入关键词（防抖后）→ listTickets 收到 keyword', async () => {
    withProviders(<TicketsPage />);
    const box = await screen.findByPlaceholderText('搜工单号 / 会话号');
    fireEvent.change(box, { target: { value: '3f2a1b4c' } });
    await waitFor(
      () => {
        expect(mockListTickets).toHaveBeenCalledWith(undefined, 1, 20, '3f2a1b4c');
      },
      { timeout: 1500 }
    );
  });

  it('状态筛选默认展示「全部」（不再空占位）', async () => {
    withProviders(<TicketsPage />);
    expect(await screen.findByText('按最近更新排序')).toBeInTheDocument();
  });
});

describe('中7：用户管理 keyword 搜索', () => {
  it('输入关键词（防抖后）→ listUsers 收到 keyword', async () => {
    withProviders(<UsersPage />);
    const box = await screen.findByPlaceholderText('搜邮箱 / 手机号');
    fireEvent.change(box, { target: { value: 'alice' } });
    await waitFor(
      () => {
        expect(mockListUsers).toHaveBeenCalledWith(1, 20, 'alice');
      },
      { timeout: 1500 }
    );
  });
});

describe('中11：评测中心指标中文化', () => {
  it('qa / honesty 指标渲染为中文名（不再裸英文）', async () => {
    vi.mocked(mockGetEvalLatest).mockResolvedValue({
      has_history: true,
      latest: {
        run_id: 'r1',
        metrics: [
          { metric: 'qa', score: 0.9, passed: 9, total: 10 },
          { metric: 'honesty', score: 1.0, passed: 8, total: 8 },
        ],
      },
      alerts: [],
    });
    withProviders(<EvalPage />);
    expect(await screen.findByText('回答正确率')).toBeInTheDocument();
    expect(await screen.findByText('诚实题召回率')).toBeInTheDocument();
  });
});

describe('中10：欢迎页不自动滚底', () => {
  const scrollIntoView = vi.fn();

  beforeEach(() => {
    scrollIntoView.mockClear();
    // jsdom 无 Element.scrollIntoView，统一注入 spy
    Object.defineProperty(Element.prototype, 'scrollIntoView', {
      configurable: true,
      value: scrollIntoView,
    });
  });

  afterEach(() => {
    delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView;
  });

  const msg = (id: string): ChatMessage => ({
    id,
    role: 'user',
    content: 'hello',
    status: 'done',
    createdAt: 1,
  });

  it('messages 为空（欢迎页）→ 不调用 scrollIntoView', async () => {
    withProviders(<MessageList messages={[]} onRate={() => {}} />);
    await new Promise((r) => setTimeout(r, 80));
    expect(scrollIntoView).not.toHaveBeenCalled();
  });

  it('有消息 → 自动滚底保持既有行为', async () => {
    withProviders(<MessageList messages={[msg('m1')]} onRate={() => {}} />);
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalled(), { timeout: 1500 });
  });
});
