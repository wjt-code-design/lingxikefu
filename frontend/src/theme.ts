/**
 * 灵犀设计 Token（JS 侧，AntD ConfigProvider 唯一输入）。
 * 与 styles/tokens.css 同值（海盐蓝治愈系 · 2026-08-17 定稿）。
 * 改色时两处同步；构建期一致性校验见 scripts/check-tokens。
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

export const darkTokens: ThemeConfig['token'] = {
  // 按规范「拒绝深色」：dark 同为浅色海盐系（微调对比，不出现深色界面）
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
  return resolved === 'dark' ? darkTokens : lightTokens;
}
