import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { render, waitFor, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { ConfigProvider } from 'antd';
import { ChatContainer } from '@/components/chat/ChatContainer';
import { useAuthStore } from '@/store/authStore';

/**
 * 「一次点击出现两条顾客消息」回归守卫（2026-10-27）。
 *
 * 缺陷版必红的原因链（每环都已实测）：
 *   ① admin/agent 角色 → chatLayout='observe' → 观察视角轮询启动（4s 周期）
 *   ② 观察视角轮询按 **id** 去重、**不按 role 过滤**（ChatContainer.tsx）
 *   ③ 本地乐观上屏的 user 消息用**临时 id**（`u-c-<ts>-<rand>`），
 *      要等 finalize（stage 变 done）才换成后端真 id
 *   → finalize 之前那轮轮询把 DB 里同一条（真 id）当成「新消息」追加 → 显示两条
 *
 * ⚠️ 为什么用**单元测试**而不是 E2E：
 *   这是**竞态 bug**——只在「流式耗时 > 轮询周期(4s)」时显现。
 *   实测：embedding 冷启动首字 ~12s → 复现；模型预热后 ~3s → 不复现。
 *   E2E 的竞态窗口不可控（守了 3 次，缺陷版仍绿）→ 不能作回归门禁。
 *   本测试把 stage 固定在 'generating'（模拟流式中），竞态窗口**确定存在**。
 *
 * 时序（对齐真实链路）：
 *   t0 初始加载：getSessionDetail → 空（后端尚未落库该消息）
 *   t1 发送：本地乐观上屏一条 user 消息（**临时 id**）
 *   t2 流式中（stage='generating'，finalize 未发生 → 本地 id 仍是临时 id）
 *   t3 轮询（4s）：getSessionDetail → 含该 user 消息（**后端真 id**）
 *      → 缺陷版：本地无此 id → 当成新消息追加 → 两条顾客消息 ❌
 *      → 修复版：流式期间跳过轮询 → 仍是一条 ✅
 */

const getSessionDetailMock = vi.fn();
const streamFn = vi.fn();
const resetFn = vi.fn();

/** 当前 mock 的流式阶段（测试里可改，用于制造「流式中」窗口）。 */
let mockStage: 'idle' | 'retrieving' | 'generating' | 'done' | 'error' = 'generating';

vi.mock('@/hooks/useChatStream', () => ({
  useChatStream: () => ({
    stage: mockStage,
    tokens: '正在生成的内容',
    sources: [],
    messageId: 'ai-real-id',
    userMessageId: 'u-real-id',
    ticketId: null,
    tool: undefined,
    answerSource: undefined,
    error: null,
    reset: resetFn,
    stop: vi.fn(),
    stream: streamFn,
  }),
}));

vi.mock('@/api/sessions', () => ({
  createSession: vi.fn(),
  getSessionDetail: (...args: unknown[]) => getSessionDetailMock(...args),
  sendAgentMessage: vi.fn(),
  rateSatisfaction: vi.fn(),
  suggestReply: vi.fn(),
}));

vi.mock('@/api/chat', () => ({ sendFeedback: vi.fn() }));
vi.mock('@/api/tickets', () => ({
  escalateSession: vi.fn(),
  createTicket: vi.fn(),
}));

function Wrapper({ children }: { children: React.ReactNode }) {
  useAuthStore.setState({
    token: 't',
    refreshToken: 't',
    role: 'admin',
    user: { user_id: 'u', role: 'admin' },
  });
  return (
    <ConfigProvider>
      <MemoryRouter initialEntries={['/chat?session=sess-1']}>{children}</MemoryRouter>
    </ConfigProvider>
  );
}

/** 统计「顾客」气泡数量（observe 视角 class = chat-msg--observe-customer）。 */
const countCustomerBubbles = () =>
  document.querySelectorAll('.chat-msg--observe-customer').length;

describe('观察视角：流式进行中不得追加重复的顾客消息', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockStage = 'idle'; // ← 初始空闲：精品卡片的 disabled={creating || streaming} 才不拦点击
    getSessionDetailMock.mockReset();
    resetFn.mockReset();
    streamFn.mockReset();
    streamFn.mockResolvedValue(undefined);
    // t0：初始历史为空（后端此刻还没落库这条消息）
    getSessionDetailMock.mockResolvedValue({ id: 'sess-1', messages: [] });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('流式中轮询拉到 DB 里同一条 user 消息（真 id）→ 不得追加为第二条', async () => {
    render(
      <Wrapper>
        <ChatContainer />
      </Wrapper>
    );

    // 等初始历史加载完成（此时本地 0 条顾客消息）
    await waitFor(() => expect(getSessionDetailMock).toHaveBeenCalled());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });

    // t1：发送一条消息 → 本地乐观上屏（临时 id）
    //   走首屏精品快捷卡片（与用户实际操作一致；observe 视角下 Composer 是客服分支，
    //   普通「发送」按钮不渲染）。
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const cards = Array.from(document.querySelectorAll('button.hot-card__item'));
    const card = cards.find((c) => (c.textContent ?? '').includes('保修多久？'));
    if (!card) throw new Error('首屏精品卡片未找到「保修多久？」');
    await user.click(card);

    // t2：进入流式中（stage 变 generating）。
    //   此时 finalize 不触发 → 本地 user 消息仍是**临时 id**，与 DB 真 id 不匹配
    //   → 竞态窗口确定存在（缺陷版必在此处被轮询追加成两条）。
    await act(async () => {
      mockStage = 'generating';
      await vi.advanceTimersByTimeAsync(10);
    });

    await waitFor(() => {
      expect(countCustomerBubbles(), '发送后应有一条顾客消息（乐观上屏）').toBe(1);
    });

    // t3：后端已落库该消息（真 id）——模拟落库后的 DB 状态
    getSessionDetailMock.mockResolvedValue({
      id: 'sess-1',
      messages: [
        // ⚠️ 内容必须与本轮点击的问题**一致**（后端落库的就是这条）——
        //   兜底过滤按内容比对「本轮流式中那条」，内容不符就拦不住（那属于另一条消息，本就该显示）。
        { id: 'u-real-id', role: 'user', content: '保修多久？', created_at: '2026-10-27T10:00:00Z' },
      ],
    });

    // 走一个轮询周期（4s）——缺陷版在此把 DB 那条当新消息追加
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });

    expect(
      countCustomerBubbles(),
      '流式进行中，观察视角轮询不得把 DB 里的 user 消息追加为新消息（会出现两条顾客消息）'
    ).toBe(1);
  });
});
