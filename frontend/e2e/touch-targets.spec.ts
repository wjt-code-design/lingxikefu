import { expect, test } from '@playwright/test';

/**
 * 触摸目标尺寸实测（WCAG 2.5.8）。
 *
 * 与 `login-state.spec.ts` 里 axe 的 `target-size` 规则不重复：axe 那条按 2.5.8 的
 * 「间距例外」放行（24px 不足但四周留白够宽也算过），所以它管"整页有没有不合规的控件"；
 * 这里钉的是**本轮真的手工抬过的那两个元素**的尺寸——把它们改回 22/23px 时 axe 未必红，
 * 这条一定会红。桌面 + 移动两个 project 都跑，小屏才是触摸目标真正被用的地方。
 *
 * 数值来自 2026-09-26 实测（`339b504`：`auth-card__link` 80×23→24、`widget-shell__brand` 28×22→24）。
 */

const MIN_PX = 24;

const PINS = [
  { route: '/login', selector: '.auth-card__link', label: '忘记密码链接' },
  { route: '/faq', selector: '.widget-shell__brand', label: '挂件壳品牌区' },
];

for (const { route, selector, label } of PINS) {
  test(`${route} ${label} 触摸目标 ≥ ${MIN_PX}px`, async ({ page }) => {
    await page.goto(route, { waitUntil: 'load' });
    const box = await page.locator(selector).first().boundingBox({ timeout: 15_000 });
    expect(box, `${selector} 未渲染`).not.toBeNull();
    const { width, height } = box!;
    expect(
      height,
      `${selector} 高度 ${Math.round(height)}px < ${MIN_PX}px`
    ).toBeGreaterThanOrEqual(MIN_PX);
    expect(width, `${selector} 宽度 ${Math.round(width)}px < ${MIN_PX}px`).toBeGreaterThanOrEqual(
      MIN_PX
    );
  });
}
