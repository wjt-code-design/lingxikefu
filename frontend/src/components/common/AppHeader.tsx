import { Layout, Segmented, Space, Typography } from 'antd';
import { DesktopOutlined, SunOutlined } from '@ant-design/icons';
import { useTheme, type ThemeMode } from '@/hooks/useTheme';
import { NotificationBell } from './NotificationBell';
import { UserMenu } from './UserMenu';

// 拒绝深色：仅「浅色 / 跟随系统」两档（跟随系统恒解析为浅色）。原「柔和(dark)」假深色档已删除。
const themeOptions = [
  { label: '浅色', value: 'light', icon: <SunOutlined /> },
  { label: '跟随系统', value: 'system', icon: <DesktopOutlined /> },
];

/**
 * 内部台顶栏：品牌 + 主题切换（浅色/跟随系统）+ 通知铃铛 + UserMenu。
 * 恒为浅色海盐系，永不出现深色界面。
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
