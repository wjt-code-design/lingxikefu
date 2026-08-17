import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { ConfigProvider } from 'antd';
import { ChatContainer } from '@/components/chat/ChatContainer';
import { useAuthStore } from '@/store/authStore';
import type { ChatStage } from '@/hooks/useChatStream';

/** P0-1 回归测试：用户消息立即可见 + done 后不重复追加 assistant（防重复 finalize）。 */

let mockStage: ChatStage = 'idle';
let mockTokens = '';
let mockSources: { chunk_id: string; doc_title: string; snippet: string; score: number }[] = [];
let mockMessageId: string | null = null;
let mockError: { code: string; message: string } | null = null;
const streamFn = vi.fn();

vi.mock('@/hooks/useChatStream', () => ({
  useChatStream: () => ({
    stage: mockStage,
    tokens: mockTokens,
    sources: mockSources,
    messageId: mockMessageId,
    ticketId: null,
    error: mockError,
    reset: vi.fn(),
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
    user: { user_id: 'u', role: 'user', quota_left: 10 },
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
  mockError = null;
  streamFn.mockReset();
  streamFn.mockImplementation(() => new Promise((r) => setTimeout(r, 0)));
});

describe('P0-1 消息生命周期', () => {
  it('发送后用户消息立即可见（sending 态），不等流式完成', async () => {
    renderContainer();
    const input = screen.getByRole('textbox', { name: '问题输入' });
    await userEvent.type(input, '七天无理由退货怎么申请？');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));
    // 立即断言（不等 mock done）
    expect(screen.getByText('七天无理由退货怎么申请？')).toBeInTheDocument();
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
});
