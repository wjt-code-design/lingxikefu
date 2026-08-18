/**
 * 灵犀设计 Token（JS 侧，AntD ConfigProvider 唯一输入）。
 * 与 styles/tokens.css 同值（海盐蓝治愈系 · 2026-08-17 重构定稿）。
 * 唯一语义层：lightTokens（拒绝深色——原 softTokens/dark 档已删除）。
 * 改色时与 tokens.css 同步。
 */
import type { ThemeConfig } from 'antd';

export const lightTokens: ThemeConfig['token'] = {
  colorPrimary: '#539FD8',
  colorInfo: '#539FD8',
  colorSuccess: '#73C9A8',
  colorWarning: '#F6C38E',
  colorError: '#E58E8E',
  colorTextBase: '#2F3E4E',
  colorBgLayout: '#F8FBFE',
  colorBorder: '#E6F0F8',
  borderRadius: 16,
};
