import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { MessageBubble } from './MessageBubble';
import { useAuthStore } from '@/store/authStore';
import type { ChatMessage } from './types';

/** T3.3：工具来源徽章——TOOL_LABEL_MAP 映射渲染，顾客可感知回答来源。 */

function renderBubble(
  msg: Partial<ChatMessage>,
  layout: 'self' | 'observe' = 'self',
  role: 'user' | 'agent' | 'admin' = 'user',
) {
  useAuthStore.setState({
    token: 't', refreshToken: 't', role,
    user: { user_id: role === 'user' ? 'u' : 's', role, quota_left: 10, quota_total: 200 },
  });
  return render(
    <MessageBubble
      msg={{ id: 'a1', role: 'assistant', content: '这是回答内容', ...msg }}
      onRate={vi.fn()}
      layout={layout}
    />
  );
}

const SOURCES = [
  { chunk_id: 'c1', doc_title: '售后政策.md', snippet: '维修周期 5-10 个工作日', score: 0.9 },
  { chunk_id: 'c2', doc_title: '物流说明.md', snippet: '同城次日达', score: 0.8 },
];

const BADGE_SEL = '.chat-msg__tool-badge';

describe('MessageBubble 工具来源徽章（T3.3）', () => {
  it('tool=order_query → 显示「订单查询」徽章', () => {
    renderBubble({ tool: 'order_query' });
    // T3.3：旧实现不消费 tool 字段 → 无徽章 → 红
    expect(screen.getByText('订单查询')).toBeInTheDocument();
    expect(document.querySelector(BADGE_SEL)).not.toBeNull();
  });

  it('tool=kb_lookup → 显示「知识库」徽章', () => {
    renderBubble({ tool: 'kb_lookup' });
    expect(screen.getByText('知识库')).toBeInTheDocument();
  });

  it('tool=llm → 显示「AI 生成」徽章', () => {
    renderBubble({ tool: 'llm' });
    expect(screen.getByText('AI 生成')).toBeInTheDocument();
  });

  it('未知工具值 → 兜底显示原始 key（新增工具免改组件）', () => {
    renderBubble({ tool: 'future_tool' });
    expect(screen.getByText('future_tool')).toBeInTheDocument();
  });

  it('无 tool（普通 RAG/LLM 回答）→ 不渲染徽章', () => {
    renderBubble({});
    expect(document.querySelector(BADGE_SEL)).toBeNull();
  });

  it('user 角色消息即使误带 tool 也不渲染徽章', () => {
    useAuthStore.setState({
      token: 't', refreshToken: 't', role: 'user',
      user: { user_id: 'u', role: 'user', quota_left: 10, quota_total: 200 },
    });
    render(
      <MessageBubble
        msg={{ id: 'u1', role: 'user', content: '顾客消息', tool: 'order_query' }}
        onRate={vi.fn()}
      />
    );
    expect(document.querySelector(BADGE_SEL)).toBeNull();
  });

  it('observe 视角（客服监控）下 AI 气泡徽章照常显示（设计意图锚点）', () => {
    renderBubble({ tool: 'order_query' }, 'observe');
    expect(screen.getByText('订单查询')).toBeInTheDocument();
    expect(document.querySelector(BADGE_SEL)).not.toBeNull();
  });
});

describe('MessageBubble 引用角标点击联动（批次 1）', () => {
  const CITATION_MSG = {
    content: '维修周期 5-10 个工作日 [来源1]；同城次日达 [来源2]。',
    sources: SOURCES,
  };

  it('staff + 有 sources：sup 带 role=button 与 aria-label', () => {
    renderBubble(CITATION_MSG, 'self', 'agent');
    const sup = document.querySelector('.chat-msg__text sup');
    expect(sup).not.toBeNull();
    expect(sup!.getAttribute('role')).toBe('button');
    expect(sup!.getAttribute('aria-label')).toBe('来源 1');
  });

  it('点击 sup → 展开来源面板并高亮对应 chunk', () => {
    renderBubble(CITATION_MSG, 'self', 'agent');
    // fireEvent 直派发（userEvent 坐标点击会被 sup 上标 translateY 偏移处的相邻文本遮挡）
    fireEvent.click(screen.getByLabelText('来源 2'));
    const panel = document.querySelector('.chat-source__panel');
    expect(panel).not.toBeNull();
    const active = document.querySelector('.chat-source__chunk--active');
    expect(active).not.toBeNull();
    expect(active!.textContent).toContain('同城次日达');
  });

  it('点击 sup 不误切溯源面板（onSelect 不被调用）', () => {
    useAuthStore.setState({
      token: 't', refreshToken: 't', role: 'agent',
      user: { user_id: 's', role: 'agent', quota_left: 10, quota_total: 200 },
    });
    const onSelect = vi.fn();
    render(
      <MessageBubble
        msg={{ id: 'a1', role: 'assistant', content: CITATION_MSG.content, sources: SOURCES }}
        onRate={vi.fn()}
        onSelect={onSelect}
      />
    );
    fireEvent.click(screen.getByLabelText('来源 1'));
    expect(onSelect).not.toHaveBeenCalled();
  });

  it('N 超界（幻觉来源9但只有2条）→ 静默 no-op', () => {
    renderBubble({ content: '答案 [来源9]。', sources: SOURCES }, 'self', 'agent');
    const sup = document.querySelector('.chat-msg__text sup')!;
    fireEvent.click(sup);
    expect(document.querySelector('.chat-source__panel')).toBeNull();
    expect(document.querySelector('.chat-source__chunk--active')).toBeNull();
  });

  it('user 角色（顾客端）→ sup 纯展示无交互属性', () => {
    renderBubble(CITATION_MSG);
    const sup = document.querySelector('.chat-msg__text sup');
    expect(sup).not.toBeNull();
    expect(sup!.getAttribute('role')).toBeNull();
    expect(sup!.getAttribute('tabindex')).toBeNull();
  });

  it('staff + sources 为空 → sup 无交互属性', () => {
    renderBubble({ content: '答案 [来源1]。', sources: [] }, 'self', 'agent');
    const sup = document.querySelector('.chat-msg__text sup');
    expect(sup).not.toBeNull();
    expect(sup!.getAttribute('role')).toBeNull();
  });

  it('键盘 Enter 触发联动（WCAG 2.1.1）', () => {
    renderBubble(CITATION_MSG, 'self', 'agent');
    const sup = screen.getByLabelText('来源 1');
    sup.focus();
    fireEvent.keyDown(sup, { key: 'Enter' });
    expect(document.querySelector('.chat-source__panel')).not.toBeNull();
  });
});
