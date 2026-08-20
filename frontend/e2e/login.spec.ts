import { test, expect } from '@playwright/test';

// 最小 smoke：npm run dev 起服后访问 /login 并截图（作为 FE-01 验收证据）
// 桌面 + 移动（chromium-mobile project）双跑：截图以 project 名区分，避免互相覆盖
test('登录页 smoke 可达并可截图', async ({ page }, testInfo) => {
  // 不依赖 networkidle/screenshot：vite dev 的 HMR 长连接会让 Playwright 的"页稳定/screenshot"永不空闲而超时。
  // 标题可见性用它自己的 auto-wait（15s）即可。
  await page.goto('/login', { waitUntil: 'load' });
  await expect(page).toHaveURL(/\/login/);
  await expect(page.getByRole('heading', { name: '登录灵犀客服' })).toBeVisible({ timeout: 15000 });

  // 移动端：页面不得横向滚动（响应式验证，重点覆盖近期大批量 CSS 改动）
  if (testInfo.project.name.includes('mobile')) {
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth
    );
    expect(overflow, '移动视口下不应出现横向滚动').toBe(false);
  }
});