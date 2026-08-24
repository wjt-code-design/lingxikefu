import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { ConfigProvider } from 'antd';
import { ChatContainer } from '@/components/chat/ChatContainer';
import { useAuthStore } from '@/store/authStore';

/** 批次A 坐席辅助：客服观察视角点「AI 推荐」→ 卡片展示 → 一键填入输入框。 */

const suggestMock = vi.fn();

vi.mock('@/hooks/useChatStream', () => ({
  useChatStream: () => ({
    stage: 'idle', tokens: '', sources: [], messageId: null,
    ticketId: null, error: null, reset: vi.fn(), stream: vi.fn(),
  }),
}));

vi.mock('@/api/sessions', () => ({
  createSession: vi.fn(),
  getSessionDetail: vi.fn().mockResolvedValue({
    id: 'sess-1',
    messages: [
      { id: 'm-1', role: 'user', content: '退款多久到账？', created_at: '2026-08-24T10:00:00Z' },
    ],
  }),
  sendAgentMessage: vi.fn(),
  rateSatisfaction: vi.fn(),
  suggestReply: (...args: unknown[]) => suggestMock(...args),
}));

vi.mock('@/api/chat', () => ({ sendFeedback: vi.fn() }));
vi.mock('@/api/tickets', () => ({
  escalateSession: vi.fn(),
  createTicket: vi.fn(),
}));

function Wrapper({ children }: { children: React.ReactNode }) {
  useAuthStore.setState({
    token: 't', refreshToken: 't', role: 'agent',
    user: { user_id: 'u', role: 'agent', quota_left: 10, quota_total: 200 },
  });
  return (
    <ConfigProvider>
      <MemoryRouter initialEntries={['/chat?session=sess-1']}>{children}</MemoryRouter>
    </ConfigProvider>
  );
}

beforeEach(() => {
  suggestMock.mockReset();
  suggestMock.mockResolvedValue({
    text: '您好，退款一般 1-3 个工作日原路退回 [来源1]。',
    sources: [{ chunk_id: 'c1', doc_title: '退换货政策', snippet: '退款 1-3 个工作日', score: 0.82 }],
  });
});

describe('坐席辅助 AI 推荐（批次A）', () => {
  it('客服视角点击 AI 推荐 → 展示建议卡片与来源 → 填入输入框', async () => {
    render(
      <Wrapper>
        <ChatContainer />
      </Wrapper>,
    );
    // observe 视角就绪（转人工按钮出现即代表详情已加载）
    await waitFor(() => expect(screen.getByRole('button', { name: /转人工/ })).toBeInTheDocument());

    // 点击 AI 推荐（红测关键：按钮尚不存在 → getByRole 抛错）
    await userEvent.click(screen.getByRole('button', { name: /AI 推荐/ }));
    await waitFor(() =>
      expect(screen.getByRole('region', { name: 'AI 建议回复' })).toBeInTheDocument(),
    );
    expect(screen.getByText(/退款一般 1-3 个工作日/)).toBeInTheDocument();
    expect(screen.getByText(/退换货政策/)).toBeInTheDocument();

    // 一键填入输入框
    await userEvent.click(screen.getByRole('button', { name: '填入输入框' }));
    const input = screen.getByRole('textbox', { name: '问题输入' }) as HTMLTextAreaElement;
    expect(input.value).toContain('退款一般 1-3 个工作日');
    expect(suggestMock).toHaveBeenCalledWith('sess-1');
  });

  it('建议失败（接口异常）→ 静默降级：无卡片、不打断界面', async () => {
    suggestMock.mockRejectedValue(new Error('net'));
    render(
      <Wrapper>
        <ChatContainer />
      </Wrapper>,
    );
    await waitFor(() => expect(screen.getByRole('button', { name: /转人工/ })).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /AI 推荐/ }));
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByRole('region', { name: 'AI 建议回复' })).not.toBeInTheDocument();
  });
});
