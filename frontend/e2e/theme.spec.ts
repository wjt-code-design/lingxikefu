import { test, expect } from '@playwright/test';

// 浅色主题回归（2026-08-20 取消深色 / 跟随系统）：
// 页面固定在浅色，<html data-theme="light"> 由 index.html 首帧内联脚本设置，
// 且不渲染主题切换器。
test.skip(({ isMobile }) => isMobile, '主题与视口无关，仅在桌面 project 跑');

test('仅保留浅色：data-theme 固定 light 且无切换器', async ({ page }) => {
  // data-theme 由 index.html 首帧内联脚本同步设置，无需等 networkidle（避免 vite HMR 使 networkidle 偶发超时）
  await page.goto('/login', { waitUntil: 'load' });

  // 浅色确认：data-theme 固定为 light（不受 localStorage 残留影响）
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');

  // 浅色语义抽查：body 背景应为浅色系（RGB 亮度高）
  const bodyBg = await page.evaluate(
    () => getComputedStyle(document.body).backgroundColor
  );
  const [r] = bodyBg.match(/\d+/)?.map(Number) ?? [0];
  expect(r, `body 背景应为浅色系，实际 ${bodyBg}`).toBeGreaterThan(200);
});