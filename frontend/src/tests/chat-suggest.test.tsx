import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Link, MemoryRouter } from 'react-router-dom';
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
  // 按入参回显会话 id：同一渲染树内 session 参数变化（sess-1 → sess-2）时，
  // 详情能返回新会话 id，避免 sessionId 永远停在旧值导致切换用例失真。
  getSessionDetail: vi.fn((sid: string) =>
    Promise.resolve({
      id: sid,
      messages: [
        { id: `m-${sid}`, role: 'user', content: '退款多久到账？', created_at: '2026-08-24T10:00:00Z' },
      ],
    }),
  ),
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
    expect(suggestMock).toHaveBeenCalledWith('sess-1', undefined, false);
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

  it('切换会话（→另一会话 / →无参）→ AI 建议卡片重置，旧会话建议不残留', async () => {
    useAuthStore.setState({
      token: 't', refreshToken: 't', role: 'agent',
      user: { user_id: 'u', role: 'agent', quota_left: 10, quota_total: 200 },
    });
    // 渲染树内挂两个站内导航链接：点击即改 searchParams，模拟客服在会话间切换
    // （rerender 换 initialEntries 不会生效——MemoryRouter 位置仅在首挂载初始化）
    render(
      <ConfigProvider>
        <MemoryRouter initialEntries={['/chat?session=sess-1']}>
          <ChatContainer />
          <Link to="/chat?session=sess-2">切换到会话2</Link>
          <Link to="/chat">切换到无参</Link>
        </MemoryRouter>
      </ConfigProvider>,
    );

    // sess-1 就绪 + 点 AI 推荐 → 卡片出现
    await waitFor(() => expect(screen.getByRole('button', { name: /转人工/ })).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /AI 推荐/ }));
    await waitFor(() =>
      expect(screen.getByRole('region', { name: 'AI 建议回复' })).toBeInTheDocument(),
    );

    // 切到另一会话（sess-1 → sess-2）：新会话详情加载后，旧建议卡片必须消失
    await userEvent.click(screen.getByRole('link', { name: '切换到会话2' }));
    await waitFor(() => expect(screen.getByText('会话 sess-2')).toBeInTheDocument());
    expect(screen.queryByRole('region', { name: 'AI 建议回复' })).not.toBeInTheDocument();

    // sess-2 再次点 AI 推荐 → 卡片出现，且请求发往新会话
    await userEvent.click(screen.getByRole('button', { name: /AI 推荐/ }));
    await waitFor(() =>
      expect(screen.getByRole('region', { name: 'AI 建议回复' })).toBeInTheDocument(),
    );
    expect(suggestMock).toHaveBeenLastCalledWith('sess-2', undefined, false);

    // 有参切到无参（sess-2 → /chat）：卡片同样重置
    await userEvent.click(screen.getByRole('link', { name: '切换到无参' }));
    await waitFor(() =>
      expect(screen.queryByRole('region', { name: 'AI 建议回复' })).not.toBeInTheDocument(),
    );
  });

  it('在途请求竞态：点推荐后立刻切会话，旧请求返回不落入新会话（卡片不出现）', async () => {
    useAuthStore.setState({
      token: 't', refreshToken: 't', role: 'agent',
      user: { user_id: 'u', role: 'agent', quota_left: 10, quota_total: 200 },
    });
    // 受控 promise：点「AI 推荐」时挂起，等切换到新会话后才放行，模拟在途请求晚归
    let resolveSuggest!: (v: { text: string; sources: unknown[] }) => void;
    suggestMock.mockReturnValue(
      new Promise((res) => {
        resolveSuggest = res;
      }),
    );
    render(
      <ConfigProvider>
        <MemoryRouter initialEntries={['/chat?session=sess-1']}>
          <ChatContainer />
          <Link to="/chat?session=sess-2">切换到会话2</Link>
        </MemoryRouter>
      </ConfigProvider>,
    );

    // sess-1 就绪 + 点 AI 推荐（请求在途）
    await waitFor(() => expect(screen.getByRole('button', { name: /转人工/ })).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /AI 推荐/ }));
    expect(suggestMock).toHaveBeenCalledWith('sess-1', undefined, false);

    // 在途时切到 sess-2，等新会话就绪（头部标题出现 = sessionId 已切到 sess-2）
    await userEvent.click(screen.getByRole('link', { name: '切换到会话2' }));
    await waitFor(() => expect(screen.getByText('会话 sess-2')).toBeInTheDocument());

    // 旧请求此刻才返回 → 建议卡片不得出现在新会话
    resolveSuggest({ text: '旧会话滞留的建议', sources: [] });
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByRole('region', { name: 'AI 建议回复' })).not.toBeInTheDocument();
    expect(screen.queryByText(/旧会话滞留的建议/)).not.toBeInTheDocument();
  });

  it('「重新生成」绕过结果缓存：请求携带 refresh=true；「AI 推荐」不携带（大扫查修复）', async () => {
    useAuthStore.setState({
      token: 't', refreshToken: 't', role: 'agent',
      user: { user_id: 'u', role: 'agent', quota_left: 10, quota_total: 200 },
    });
    render(
      <ConfigProvider>
        <MemoryRouter initialEntries={['/chat?session=sess-1']}>
          <ChatContainer />
        </MemoryRouter>
      </ConfigProvider>,
    );
    await waitFor(() => expect(screen.getByRole('button', { name: /转人工/ })).toBeInTheDocument());

    // 首次 AI 推荐：不携带 refresh（可吃缓存）
    await userEvent.click(screen.getByRole('button', { name: /AI 推荐/ }));
    await waitFor(() =>
      expect(screen.getByRole('region', { name: 'AI 建议回复' })).toBeInTheDocument(),
    );
    expect(suggestMock).toHaveBeenLastCalledWith('sess-1', undefined, false);

    // 重新生成：必须绕缓存强制重算（60s TTL 内否则返回同一文本，按钮语义失效）
    await userEvent.click(screen.getByRole('button', { name: '重新生成' }));
    await waitFor(() => expect(suggestMock).toHaveBeenLastCalledWith('sess-1', undefined, true));
  });
});
