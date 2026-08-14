import { test, expect } from '@playwright/test';

// 最小 smoke：npm run dev 起服后访问 /login 并截图（作为 FE-01 验收证据）
test('登录页 smoke 可达并可截图', async ({ page }) => {
  await page.goto('/login');
  await expect(page).toHaveURL(/\/login/);
  // 登录空壳页应有标题
  await expect(page.getByRole('heading', { name: '登录' })).toBeVisible();
  await page.screenshot({ path: 'e2e/screenshots/login.png', fullPage: true });
});
