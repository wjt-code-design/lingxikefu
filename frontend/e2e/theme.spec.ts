import { test, expect } from '@playwright/test';

// 暗色模式回归：防"假深色"（切了 data-theme 但样式没真正变）。
// 通过 zustand persist 的 lingxi-theme 键 + 首帧 index.html 兜底脚本驱动，无需登录态。
// 主题切换与视口无关 → 仅在桌面 project 跑，避免移动端重复。
test.skip(({ isMobile }) => isMobile, '主题切换与视口无关，仅在桌面 project 跑');

const THEME_LS = (theme: string) =>
  JSON.stringify({ state: { theme }, version: 0 });

test('暗色模式真实切换背景/卡片/文字（防假深色）', async ({ page }) => {
  // data-theme 由 index.html 首帧内联脚本同步设置，无需等 networkidle（避免 vite HMR 使 networkidle 偶发超时）
  await page.goto('/login', { waitUntil: 'load' });

  // 初始浅色
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  const lightProbe = await page.evaluate(() => ({
    bodyBg: getComputedStyle(document.body).backgroundColor,
    inputBg: getComputedStyle(document.querySelector('.ant-input')!).backgroundColor,
    inputColor: getComputedStyle(document.querySelector('.ant-input')!).color,
    titleColor: getComputedStyle(document.querySelector('.auth-card__title')!).color,
  }));

  // 切深色并 reload（走 index.html 首帧兜底）
  await page.evaluate(
    (v) => localStorage.setItem('lingxi-theme', v),
    THEME_LS('dark')
  );
  await page.reload({ waitUntil: 'load' });
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');

  const darkProbe = await page.evaluate(() => ({
    bodyBg: getComputedStyle(document.body).backgroundColor,
    inputBg: getComputedStyle(document.querySelector('.ant-input')!).backgroundColor,
    inputColor: getComputedStyle(document.querySelector('.ant-input')!).color,
    titleColor: getComputedStyle(document.querySelector('.auth-card__title')!).color,
  }));

  // 客观断言：关键元素计算色必须实际变化（防假深色回归）
  for (const key of ['bodyBg', 'inputBg', 'inputColor', 'titleColor'] as const) {
    expect(
      darkProbe[key],
      `暗色下 ${key} 应与浅色不同（防假深色）`
    ).not.toBe(lightProbe[key]);
  }
  // 深色语义抽查：body 背景应是深色系（蓝色通道/亮度偏低）
  expect(darkProbe.bodyBg).not.toMatch(/^rgb\((242|255)/);

  // 切回浅色恢复
  await page.evaluate(
    (v) => localStorage.setItem('lingxi-theme', v),
    THEME_LS('light')
  );
  await page.reload({ waitUntil: 'load' });
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
});