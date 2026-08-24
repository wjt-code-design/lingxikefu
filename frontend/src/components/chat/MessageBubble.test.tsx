import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MessageBubble } from './MessageBubble';
import { useAuthStore } from '@/store/authStore';
import type { ChatMessage } from './types';

/** T3.3：工具来源徽章——TOOL_LABEL_MAP 映射渲染，顾客可感知回答来源。 */

function renderBubble(msg: Partial<ChatMessage>) {
  useAuthStore.setState({
    token: 't', refreshToken: 't', role: 'user',
    user: { user_id: 'u', role: 'user', quota_left: 10, quota_total: 200 },
  });
  return render(
    <MessageBubble
      msg={{ id: 'a1', role: 'assistant', content: '这是回答内容', ...msg }}
      onRate={vi.fn()}
    />
  );
}

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
});
