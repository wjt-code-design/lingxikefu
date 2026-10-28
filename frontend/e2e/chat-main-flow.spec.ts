import { test, expect, type Page } from '@playwright/test';
import { signIn } from './fixtures';

/**
 * 聊天主链路端到端回归（2026-10-27）。
 *
 * ⚠️ **为什么「顾客消息不重复」不在这里测**：
 *   那个 bug 是**竞态**——只在「流式耗时 > 观察视角轮询周期(4s)」时显现。
 *   实测：embedding 冷启动首字 ~12s → 复现；模型预热后 ~3s → 不复现。
 *   E2E 里这个窗口不可控（试了 3 次，缺陷版仍绿）→ **不能当回归门禁**，
 *   留在 CI 里只会给人「有门禁」的错觉。确定性守卫放在
 *   `src/tests/chat-observe-duplicate.test.tsx`（vitest，mock 掉时序）。
 *
 * 本文件只守**确定性**的行为：一次点击 → 一条顾客消息 → 一条完整 AI 回答。
 */
test.describe('聊天主链路', () => {
  test.skip(({ isMobile }) => isMobile, '移动视口交互路径不同，只跑桌面');
  test.describe.configure({ mode: 'serial' });

  let page: Page;

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await signIn(page, 'admin');
  });

  test.afterAll(async () => {
    await page?.close();
  });

  test('点一次快捷问题 → 顾客消息不重复，AI 回答完整渲染', async () => {
    await page.goto('/chat', { waitUntil: 'load' });
    await expect(page.locator('button.hot-card__item').first()).toBeVisible();

    const card = page.locator('button.hot-card__item').filter({ hasText: '保修多久？' }).first();
    await expect(card, '首屏精品区应有「保修多久？」快捷卡片').toBeVisible();
    await card.click();

    // 等流式真正结束（antd「停 止」按钮消失即流结束）
    const stopBtn = page.getByRole('button', { name: /停\s*止/ });
    await expect(stopBtn, '流式应在 150s 内结束').toBeHidden({ timeout: 150_000 });
    await page.waitForTimeout(1000);

    // 顾客气泡：观察视角下 class = chat-msg--observe-customer
    const customerCount = await page.evaluate(
      () => document.querySelectorAll('.chat-msg--observe-customer').length
    );
    expect(customerCount, '一次点击应只出现一条顾客消息').toBe(1);

    // AI 回答：应完整渲染（含来源引用），而非只到首句
    const aiText = await page.evaluate(() => {
      const bubble = document.querySelector('.chat-msg--observe-ai .chat-msg__bubble');
      return (bubble?.innerText ?? '').trim();
    });
    expect(aiText.length, 'AI 回答应有实质内容（不是只渲染首句）').toBeGreaterThan(50);
    expect(aiText, '流式结束后不应残留「正在生成回答」占位').not.toContain('正在生成回答');
    // 引用在 UI 上渲染成**可点击角标**（1/2/3…）而非 `[来源N]` 原文，
    // 气泡底部另有「来源 N 条」汇总 —— 故断言后者（稳定且可读）。
    expect(aiText, 'AI 回答应带引用来源（气泡底部「来源 N 条」）').toMatch(/来源\s*\d+\s*条/);
  });
});
