import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { ConfigProvider } from 'antd';
import { ChatContainer } from '@/components/chat/ChatContainer';
import { useAuthStore } from '@/store/authStore';
import type { ChatStage } from '@/hooks/useChatStream';
import type { MessageSource } from '@/contracts/api';

/** 溯源「点哪条看哪条」回归（2026-08-25）：
 * 客服视角打开历史会话（?session=X）时，右栏溯源面板此前恒空——sources 只由实时流 SSE 推送，
 * 历史加载不触发流。修复：历史加载默认选中最后一条 AI 回复；点击任一 AI 回复可切换面板溯源。 */

let mockStage: ChatStage = 'idle';
let mockSources: MessageSource[] = [];
const streamFn = vi.fn();
const resetFn = vi.fn();
const stopFn = vi.fn();

vi.mock('@/hooks/useChatStream', () => ({
  useChatStream: () => ({
    stage: mockStage,
    tokens: '',
    sources: mockSources,
    messageId: null,
    userMessageId: null,
    ticketId: null,
    tool: undefined,
    error: null,
    reset: resetFn,
    stop: stopFn,
    stream: streamFn,
  }),
}));

const getSessionDetailMock = vi.fn();
vi.mock('@/api/sessions', () => ({
  createSession: vi.fn().mockResolvedValue({ id: 'sess-1', created_at: '', updated_at: '' }),
  getSessionDetail: (...args: unknown[]) => getSessionDetailMock(...args),
}));
vi.mock('@/api/chat', () => ({ sendFeedback: vi.fn().mockResolvedValue(undefined) }));
vi.mock('@/api/tickets', () => ({ escalateSession: vi.fn().mockResolvedValue({ ticket_id: 't-1' }) }));

const SRC1: MessageSource = { chunk_id: 'c1', doc_id: 'd1', doc_title: '售后政策', score: 0.9, snippet: '保修期 12 个月' };
const SRC2: MessageSource = { chunk_id: 'c2', doc_id: 'd2', doc_title: '物流说明', score: 0.85, snippet: '48 小时内发货' };

function Wrapper({ children }: { children: React.ReactNode }) {
  useAuthStore.setState({
    token: 't',
    refreshToken: 't',
    role: 'agent',
    user: { user_id: 'u', role: 'agent', quota_left: 10, quota_total: 200 },
  });
  return (
    <ConfigProvider>
      <MemoryRouter initialEntries={['/chat?session=sess-1']}>{children}</MemoryRouter>
    </ConfigProvider>
  );
}

function renderContainer(onSelectMessage = vi.fn()) {
  return render(
    <Wrapper>
      <ChatContainer onSelectMessage={onSelectMessage} />
    </Wrapper>
  );
}

/** 历史会话：两条 AI 回复（m2 带 1 条来源，m4 为最后一条带 1 条来源） */
const DETAIL = {
  id: 'sess-1',
  created_at: '2026-08-25T01:00:00Z',
  updated_at: '2026-08-25T01:00:00Z',
  messages: [
    { id: 'm1', role: 'user', content: '保修多久？', created_at: '2026-08-25T01:00:00Z' },
    { id: 'm2', role: 'assistant', content: '保修一年。', created_at: '2026-08-25T01:00:05Z', sources: [SRC1] },
    { id: 'm3', role: 'user', content: '发货多快？', created_at: '2026-08-25T01:01:00Z' },
    { id: 'm4', role: 'assistant', content: '48 小时内发货。', created_at: '2026-08-25T01:01:05Z', sources: [SRC2], answer_source: 'quick' },
  ],
};

beforeEach(() => {
  mockStage = 'idle';
  mockSources = [];
  streamFn.mockReset();
  resetFn.mockReset();
  stopFn.mockReset();
  getSessionDetailMock.mockReset();
  getSessionDetailMock.mockResolvedValue(DETAIL);
});

describe('溯源「点哪条看哪条」（客服视角历史会话）', () => {
  it('历史加载完成 → 默认选中最后一条 AI 回复（右栏面板有溯源）', async () => {
    const onSelect = vi.fn();
    renderContainer(onSelect);
    await waitFor(() => expect(onSelect).toHaveBeenCalledTimes(1));
    expect(onSelect).toHaveBeenCalledWith('m4', [SRC2], 'quick'); // 2026-08-25：默认选中带出快捷话术标记（answer_source）
  });

  it('点击其他 AI 回复 → 切换到该条溯源', async () => {
    const onSelect = vi.fn();
    renderContainer(onSelect);
    await waitFor(() => expect(onSelect).toHaveBeenCalledTimes(1));
    const clickables = screen.getAllByTitle('点击查看该回复的溯源来源');
    expect(clickables).toHaveLength(2);
    await userEvent.click(clickables[0]);
    // m2 无快捷话术标记 → 第三参 undefined（普通回答清除残留 answerSource）
    expect(onSelect).toHaveBeenLastCalledWith('m2', [SRC1], undefined);
  });

  it('点击带 quick 标记的 AI 回复 → 第三参透出 quick（code-review F10）', async () => {
    const onSelect = vi.fn();
    renderContainer(onSelect);
    await waitFor(() => expect(onSelect).toHaveBeenCalledTimes(1)); // 默认选中 m4
    const clickables = screen.getAllByTitle('点击查看该回复的溯源来源');
    await userEvent.click(clickables[1]); // m4 带 answer_source=quick
    expect(onSelect).toHaveBeenLastCalledWith('m4', [SRC2], 'quick');
  });

  it('键盘 Enter 可触发面板切换（code-review F1，WCAG 2.1.1）', async () => {
    const onSelect = vi.fn();
    renderContainer(onSelect);
    await waitFor(() => expect(onSelect).toHaveBeenCalledTimes(1));
    const clickables = screen.getAllByTitle('点击查看该回复的溯源来源');
    fireEvent.keyDown(clickables[0], { key: 'Enter' });
    expect(onSelect).toHaveBeenLastCalledWith('m2', [SRC1], undefined);
  });

  it('点击气泡内复制按钮 → 不误切溯源面板（code-review F1）', async () => {
    const onSelect = vi.fn();
    renderContainer(onSelect);
    await waitFor(() => expect(onSelect).toHaveBeenCalledTimes(1)); // 仅默认选中 m4 一次
    const copyBtns = screen.getAllByLabelText('复制回答');
    expect(copyBtns.length).toBeGreaterThan(0);
    await userEvent.click(copyBtns[0]);
    // 内部按钮被 closest 拦截 → onSelect 不再被调用（无误切）
    expect(onSelect).toHaveBeenCalledTimes(1);
  });
});
