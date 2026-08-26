import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useNavigate } from 'react-router-dom';
import { ConfigProvider } from 'antd';
import { ChatContainer } from '@/components/chat/ChatContainer';
import { useAuthStore } from '@/store/authStore';
import type { ChatStage } from '@/hooks/useChatStream';
import { createSession as mockCreateSession, getSessionDetail as mockGetSessionDetail } from '@/api/sessions';
import type { SessionDetail } from '@/contracts/api';

/** P0-1 回归测试：用户消息立即可见 + done 后不重复追加 assistant（防重复 finalize）。 */

let mockStage: ChatStage = 'idle';
let mockTokens = '';
let mockSources: { chunk_id: string; doc_title: string; snippet: string; score: number }[] = [];
let mockMessageId: string | null = null;
let mockUserMessageId: string | null = null;
let mockError: { code: string; message: string } | null = null;
let mockTool: string | undefined = undefined;
const streamFn = vi.fn();
const resetFn = vi.fn();
const stopFn = vi.fn();

vi.mock('@/hooks/useChatStream', () => ({
  // 大扫查（2026-08-25）：mock 展开完整 ChatStreamState 形状（含 userMessageId/stop），
  // 防止「比生产少字段的假象下变绿」的 mock-fidelity 陷阱
  useChatStream: () => ({
    stage: mockStage,
    tokens: mockTokens,
    sources: mockSources,
    messageId: mockMessageId,
    userMessageId: mockUserMessageId,
    ticketId: null,
    tool: mockTool,
    error: mockError,
    reset: resetFn,
    stop: stopFn,
    stream: streamFn,
  }),
}));

vi.mock('@/api/sessions', () => ({
  createSession: vi.fn().mockResolvedValue({ id: 'sess-1', created_at: '', updated_at: '' }),
  getSessionDetail: vi.fn().mockResolvedValue({ id: 'sess-1', messages: [] }),
}));

vi.mock('@/api/chat', () => ({ sendFeedback: vi.fn().mockResolvedValue(undefined) }));
vi.mock('@/api/tickets', () => ({ escalateSession: vi.fn().mockResolvedValue({ ticket_id: 't-1' }) }));

function Wrapper({ children }: { children: React.ReactNode }) {
  useAuthStore.setState({
    token: 't', refreshToken: 't', role: 'user',
    user: { user_id: 'u', role: 'user', quota_left: 10, quota_total: 200 },
  });
  return (
    <ConfigProvider>
      <MemoryRouter>{children}</MemoryRouter>
    </ConfigProvider>
  );
}

function renderContainer() {
  return render(
    <Wrapper>
      <ChatContainer />
    </Wrapper>
  );
}

beforeEach(() => {
  mockStage = 'idle';
  mockTokens = '';
  mockSources = [];
  mockMessageId = null;
  mockUserMessageId = null;
  mockError = null;
  mockTool = undefined;
  streamFn.mockReset();
  streamFn.mockImplementation(() => new Promise((r) => setTimeout(r, 0)));
  resetFn.mockReset();
  stopFn.mockReset();
});

describe('P0-1 消息生命周期', () => {
  it('发送后用户消息立即可见（sending 态），不等流式完成', async () => {
    renderContainer();
    const input = screen.getByRole('textbox', { name: '问题输入' });
    await userEvent.type(input, '七天无理由退货怎么申请？');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));
    // 立即断言（不等 mock done）——注意：AI 首回复前快捷卡片仍在（V5 有意设计），
    // 同文本会同时出现在快捷卡片与消息气泡，故用 getAllByText 断言"消息已上屏"
    expect(screen.getAllByText('七天无理由退货怎么申请？').length).toBeGreaterThan(0);
    expect(screen.getByText('发送中…')).toBeInTheDocument();
  });

  it('done 后点赞（messages 更新）不会重复追加 assistant（防重复 finalize）', async () => {    const { rerender } = renderContainer();
    const input = screen.getByRole('textbox', { name: '问题输入' });
    await userEvent.type(input, '保修多久？');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));
    // 流式完成（mock 模块变量变化需 rerender 触发 effect 重评估）
    mockStage = 'done';
    mockTokens = '保修一年。';
    mockMessageId = 'm-1';
    rerender(
      <Wrapper>
        <ChatContainer />
      </Wrapper>
    );
    await waitFor(() => expect(screen.getByText((t) => t.includes('保修一年'))).toBeInTheDocument());
    const countAfterDone = screen.getAllByText((t) => t.includes('保修一年')).length;
    expect(countAfterDone).toBe(1);
    // 第二次同 stage 触发（模拟 messages 变化后 effect 重跑）→ 不应重复追加
    rerender(
      <Wrapper>
        <ChatContainer />
      </Wrapper>
    );
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.getAllByText((t) => t.includes('保修一年')).length).toBe(1);
  });

  it('done 带 tool → finalize 后气泡显示工具徽章（T3.2/T3.3 链路）', async () => {
    const { rerender } = renderContainer();
    const input = screen.getByRole('textbox', { name: '问题输入' });
    await userEvent.type(input, '帮我查订单 8823');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));
    // 流式完成且 done 携带工具标记
    mockStage = 'done';
    mockTokens = '订单已发货，物流单号 8823。';
    mockMessageId = 'm-2';
    mockTool = 'order_query';
    rerender(
      <Wrapper>
        <ChatContainer />
      </Wrapper>
    );
    await waitFor(() => expect(screen.getByText('订单查询')).toBeInTheDocument());
  });

  it('连续两轮：第一轮带 tool，第二轮普通 RAG → 第二轮气泡不残留徽章', async () => {
    const { rerender } = renderContainer();
    const input = screen.getByRole('textbox', { name: '问题输入' });
    // 第一轮：工具回答（带徽章）
    await userEvent.type(input, '帮我查订单 8823');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));
    mockStage = 'done';
    mockTokens = '订单已发货。';
    mockMessageId = 'm-1';
    mockTool = 'order_query';
    rerender(
      <Wrapper>
        <ChatContainer />
      </Wrapper>
    );
    await waitFor(() => expect(screen.getByText('订单查询')).toBeInTheDocument());
    // 第二轮：普通 RAG 回答（done 无 tool 字段）——最高危回归路径：
    // 若 tool 态跨轮残留，第二枚「订单查询」徽章会错挂到普通回答气泡上
    await userEvent.type(input, '退货政策是什么');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));
    mockStage = 'generating';
    mockTokens = '';
    mockMessageId = null;
    mockTool = undefined;
    rerender(
      <Wrapper>
        <ChatContainer />
      </Wrapper>
    );
    mockStage = 'done';
    mockTokens = '七天无理由退货，商品需保持完好。';
    mockMessageId = 'm-2';
    mockTool = undefined;
    rerender(
      <Wrapper>
        <ChatContainer />
      </Wrapper>
    );
    await waitFor(() =>
      expect(screen.getAllByText((t) => t.includes('七天无理由退货')).length).toBeGreaterThan(0)
    );
    // 全屏仅第一轮那一枚徽章——第二轮气泡未残留
    expect(screen.getAllByText('订单查询')).toHaveLength(1);
  });
});

/** P3-⑫ 历史加载竞态：打开 ?session=A 后立即新建 → 旧会话详情晚返回不得写入新会话。 */
function NavButton({ to, label }: { to: string; label: string }) {
  const nav = useNavigate();
  return (
    <button type="button" onClick={() => nav(to)}>
      {label}
    </button>
  );
}

describe('P3-⑫ 历史加载 stale 守卫', () => {
  it('详情在途时切到新建 → 旧会话消息不出现，后续发送创建新会话', async () => {
    let resolveDetail!: (d: SessionDetail) => void;
    vi.mocked(mockGetSessionDetail).mockImplementationOnce(
      () =>
        new Promise((res) => {
          resolveDetail = res;
        })
    );
    useAuthStore.setState({
      token: 't', refreshToken: 't', role: 'user',
      user: { user_id: 'u', role: 'user', quota_left: 10, quota_total: 200 },
    });
    render(
      <ConfigProvider>
        <MemoryRouter initialEntries={['/chat?session=old-session']}>
          <NavButton to="/chat" label="go-new" />
          <ChatContainer />
        </MemoryRouter>
      </ConfigProvider>
    );
    // 历史详情请求已发起（effect 已跑），但尚未 resolve——用户此刻点「新建」
    await waitFor(() => expect(mockGetSessionDetail).toHaveBeenCalled());
    await userEvent.click(screen.getByRole('button', { name: 'go-new' }));
    // 旧会话详情此刻才返回（携带旧消息）——stale 守卫必须丢弃，绝不写入新会话
    resolveDetail({
      id: 'old-session',
      messages: [
        { id: 'm-old', role: 'assistant', content: '旧会话A的消息', created_at: '2026-01-01T00:00:00Z' },
      ],
    } as SessionDetail);
    await waitFor(() => expect(mockGetSessionDetail).toHaveBeenCalledTimes(1));
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByText('旧会话A的消息')).not.toBeInTheDocument();
    // 后续发送 → 走新建会话（createSession），不带着旧会话 id 直发
    await userEvent.type(screen.getByRole('textbox', { name: '问题输入' }), '新会话的问题');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));
    await waitFor(() => expect(mockCreateSession).toHaveBeenCalled());
  });
});
