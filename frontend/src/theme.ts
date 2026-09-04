/**
 * 灵犀设计 Token（JS 侧，AntD ConfigProvider 唯一输入）。
 * 与 styles/tokens.css 的 :root 同值（海盐蓝 · 冷灰统一 · 2026-08-19 v3）。
 * 2026-08-20：取消深色 / 跟随系统，仅保留浅色。
 */
import type { ThemeConfig } from 'antd';

export const lightTokens: ThemeConfig['token'] = {
  colorPrimary: '#539FD8',
  colorInfo: '#539FD8',
  // a11y（D 批）：colorPrimary 自动传导为 Typography.Link / type="link" 按钮文字色，
  // #539FD8 on 白底 2.87:1 不达文字 AA → 显式把「链接文字」切 #3874A6（brand-dark 4.95:1）。
  // colorPrimary 保留原值：主按钮背景/选中态填充不受影响（其上文字为反白）。
  colorLink: '#3874A6',
  colorLinkHover: '#2E5D85',
  colorSuccess: '#4DA07F',
  colorWarning: '#E0A86A',
  colorError: '#D97A7A',
  colorTextBase: '#2F3E4E',
  colorBgLayout: '#F2F5F8',
  colorBorder: '#E3E9EF',
  borderRadius: 10,
};
