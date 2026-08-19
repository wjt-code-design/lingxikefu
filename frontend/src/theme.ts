/**
 * 灵犀设计 Token（JS 侧，AntD ConfigProvider 唯一输入）。
 * 与 styles/tokens.css 同值（海盐蓝 · 冷灰统一 · 2026-08-19 v3）。
 * 改色时与 tokens.css 同步。
 */
import type { ThemeConfig } from 'antd';

export const lightTokens: ThemeConfig['token'] = {
  colorPrimary: '#539FD8',
  colorInfo: '#539FD8',
  colorSuccess: '#4DA07F',
  colorWarning: '#E0A86A',
  colorError: '#D97A7A',
  colorTextBase: '#2F3E4E',
  colorBgLayout: '#F2F5F8',
  colorBorder: '#E3E9EF',
  borderRadius: 10,
};
