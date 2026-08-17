import { test, expect } from '@playwright/test';

// 最小 smoke：npm run dev 起服后访问 /login 并截图（作为 FE-01 验收证据）
test('登录页 smoke 可达并可截图', async ({ page }) => {
  // 等网络空闲：5173 dev server 已运行较久（HMR 边缘状态），networkidle 避免抓到未完成的首屏
  await page.goto('/login', { waitUntil: 'networkidle' });
  await expect(page).toHaveURL(/\/login/);
  // 登录页应有标题（与 routes.test.tsx 断言一致）
  await expect(page.getByRole('heading', { name: '登录灵犀客服' })).toBeVisible({ timeout: 15000 });
  await page.screenshot({ path: 'e2e/screenshots/login.png', fullPage: true });
});
