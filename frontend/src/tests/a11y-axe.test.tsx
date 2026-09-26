import { describe, expect, it, vi } from 'vitest';
import { render, waitFor } from '@testing-library/react';
import axe from 'axe-core';
import { renderApp } from './test-utils';
import { MessageBubble } from '@/components/chat/MessageBubble';
import { useAuthStore } from '@/store/authStore';

/**
 * axe 结构规则守卫（把"axe 清零不回退"从人肉承诺变成会红的断言）。
 *
 * 为什么跑在 jsdom 而不是 Playwright：CI 的 frontend job 只有 `tsc + vitest`
 * （.github/workflows/ci.yml:257-276，不跑 check:a11y / check:tokens），
 * 装浏览器要给每次 push 加分钟级下载；jsdom 版零基建，且覆盖我们真正会漂移的结构类规则：
 * 角色、可访问名、合法 ARIA、**nested-interactive**。
 *
 * 边界（如实标注，别把它当完整 a11y 通过）：
 * - 只覆盖匿名可达页 + 单组件态；登录态整页（chat / 工作台 / admin）需后端。
 * - 布局依赖规则 jsdom 判不了 → 关 color-contrast；对比度由 `check:a11y` 静态算色守，
 *   触摸目标由 Playwright 探针量（本轮实测：auth-card__link 23→24px 等）。
 * - 假阳性教训（首版就踩）：落地页 axe 报 empty-heading ×1，实为
 *   `.ant-skeleton-title`——antd 骨架屏占位自身的标记，真浏览器 axe 报 0 违规。
 *   故断言前先等骨架退场，审"稳定态"而不是加载态。
 */

const JSDOM_UNEVALUABLE = { 'color-contrast': { enabled: false } as const };

async function axeIds(el: HTMLElement) {
  const { violations } = await axe.run(el, {
    resultTypes: ['violations'],
    rules: JSDOM_UNEVALUABLE,
  });
  return violations.map((v) => `${v.id} [${v.impact ?? '?'}] ×${v.nodes.length}`);
}

describe('axe 结构规则守卫（jsdom）', () => {
  it('匿名落地页 / 稳定态零违规', async () => {
    const { container } = renderApp('/');
    // 骨架屏是加载态占位（h3.ant-skeleton-title 无文本），审稳定态才等价于真浏览器
    await waitFor(() => expect(container.querySelector('.ant-skeleton')).toBeNull(), {
      timeout: 3000,
    });
    expect(await axeIds(container)).toEqual([]);
  });

  it('登录页 /login 零违规', async () => {
    const { container } = renderApp('/login');
    expect(await axeIds(container)).toEqual([]);
  });

  it('客服视角引用角标气泡：只剩已挂账的 nested-interactive，不得再多一条', async () => {
    // clickable = isAi && isStaff（MessageBubble.tsx:83）→ 根节点 role="button"；
    // 角标在 interactiveCitations 下同为 role="button" + tabIndex=0 → axe nested-interactive。
    //
    // 这是**已知挂账而非本文件新引入的问题**：历史计划（.trae/documents/
    // reply-optimization-plan.md 批 5）已写明取舍「axe 清零不回退 > sup 独立焦点」，
    // 并备好预案 B（撤 sup 的 role/tabIndex，点击经冒泡在 handleRootClick 内 closest('sup')
    // 分流）。撤掉会连带改 MessageBubble.test.tsx:89/97/108/133/141/148 六处锚定用例，
    // 属交互设计决策 → 交 Owner 定，不在此单方面翻。
    //
    // 本断言的作用是棘轮：违规集合被钉死，将来任何**新增**规则（含 aria-allowed-attr，
    // 该条已因 aria-selected→aria-pressed 修复而消失）都会把它撑红。
    useAuthStore.setState({
      token: 't',
      refreshToken: 't',
      role: 'agent',
      user: { user_id: 's', role: 'agent' },
    });
    const { container } = render(
      <MessageBubble
        msg={{
          id: 'a1',
          role: 'assistant',
          content: '维修周期 5-10 个工作日 [来源1]；同城次日达 [来源2]。',
          sources: [
            { chunk_id: 'c1', doc_title: '售后政策.md', snippet: '维修周期 5-10 个工作日', score: 0.9 },
            { chunk_id: 'c2', doc_title: '物流说明.md', snippet: '同城次日达', score: 0.8 },
          ],
        }}
        onRate={vi.fn()}
        onSelect={vi.fn()}
      />
    );
    expect(await axeIds(container)).toEqual(['nested-interactive [serious] ×1']);
  });
});
