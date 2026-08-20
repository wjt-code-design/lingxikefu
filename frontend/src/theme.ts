/**
 * 灵犀设计 Token（JS 侧，AntD ConfigProvider 唯一输入）。
 * 与 styles/tokens.css 同值（海盐蓝 · 冷灰统一 · 2026-08-19 v3）。
 * 暗色 token 与 tokens.css 的 [data-theme='dark'] 块同源，改色时两处同步。
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

/** 暗色版：深蓝灰画布（非纯黑）+ 海盐蓝品牌色不变（见 tokens.css dark 块）。 */
export const darkTokens: ThemeConfig['token'] = {
  colorPrimary: '#539FD8',
  colorInfo: '#539FD8',
  colorSuccess: '#4DA07F',
  colorWarning: '#E0A86A',
  colorError: '#D97A7A',
  colorTextBase: '#CFD9E3',
  colorBgLayout: '#141B24',
  colorBorder: '#2B3644',
  borderRadius: 10,
};
