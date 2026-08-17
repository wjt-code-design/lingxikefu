import { Layout, Segmented, Space, Typography } from 'antd';
import { DesktopOutlined, MoonOutlined, SunOutlined } from '@ant-design/icons';
import { useTheme, type ThemeMode } from '@/hooks/useTheme';
import { NotificationBell } from './NotificationBell';
import { UserMenu } from './UserMenu';

const themeOptions = [
  { label: '浅色', value: 'light', icon: <SunOutlined /> },
  { label: '柔和', value: 'dark', icon: <MoonOutlined /> },
  { label: '跟随系统', value: 'system', icon: <DesktopOutlined /> },
];

/**
 * 内部台顶栏：品牌 + 主题切换（浅色/柔和/跟随系统）+ 通知铃铛 + UserMenu。
 * 柔和档仅切换浅色海盐变体（theme.ts softTokens + CSS 变量），永不出现深色界面。
 */
export function AppHeader() {
  const { theme, setTheme } = useTheme();

  return (
    <Layout.Header className="app-header">
      <Typography.Text strong className="app-header__title">
        灵犀 · 星河智家 智能客服
      </Typography.Text>
      <Space className="app-header__actions">
        <Segmented
          options={themeOptions}
          value={theme}
          onChange={(v) => setTheme(v as ThemeMode)}
          aria-label="主题切换"
        />
        <NotificationBell />
        <UserMenu />
      </Space>
    </Layout.Header>
  );
}

export default AppHeader;
