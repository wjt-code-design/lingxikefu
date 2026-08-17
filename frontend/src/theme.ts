/**
 * 灵犀设计 Token（JS 侧，AntD ConfigProvider 唯一输入）。
 * 与 styles/tokens.css 同值（海盐蓝治愈系 · 2026-08-17 重构定稿）。
 * 两层语义：lightTokens = semantic-user；softTokens = 柔和变体（原 dark 语义废除，
 * 无深色模式——darkAlgorithm 恒不启用，见 App.tsx）。改色时两处同步。
 */
import type { ThemeConfig } from 'antd';

export const lightTokens: ThemeConfig['token'] = {
  colorPrimary: '#96C8E8',
  colorInfo: '#96C8E8',
  colorSuccess: '#73C9A8',
  colorWarning: '#F6C38E',
  colorError: '#E58E8E',
  colorTextBase: '#2F3E4E',
  colorBgLayout: '#F8FBFE',
  colorBorder: '#E6F0F8',
  borderRadius: 16,
};

export const softTokens: ThemeConfig['token'] = {
  // 柔和变体（「柔和」档）：同为浅色海盐系，微调对比，不出现深色界面
  colorPrimary: '#96C8E8',
  colorInfo: '#96C8E8',
  colorSuccess: '#73C9A8',
  colorWarning: '#F6C38E',
  colorError: '#E58E8E',
  colorTextBase: '#2F3E4E',
  colorBgLayout: '#F0F7FC',
  colorBorder: '#DCEBF6',
  borderRadius: 16,
};

/** 按 resolved 主题取 token（light/dark，与 useTheme 的 resolved 对齐） */
export function themeTokens(resolved: 'light' | 'dark'): ThemeConfig['token'] {
  return resolved === 'dark' ? softTokens : lightTokens;
}
