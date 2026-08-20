import { Button, Layout, Space, Typography } from 'antd';
import { MoonOutlined, SunOutlined, SyncOutlined } from '@ant-design/icons';
import { useTheme } from '@/hooks/useTheme';
import { NotificationBell } from './NotificationBell';
import { UserMenu } from './UserMenu';

/** 主题档位元数据（三态循环：light → dark → system）。 */
const THEME_META = {
  light: { icon: <SunOutlined />, label: '浅色', next: 'dark' as const },
  dark: { icon: <MoonOutlined />, label: '深色', next: 'system' as const },
  system: { icon: <SyncOutlined />, label: '跟随系统', next: 'light' as const },
};

/**
 * 内部台顶栏：品牌 + 主题切换 + 通知铃铛 + UserMenu。
 * 主题三态循环（light/dark/system），CSS 层走 tokens.css [data-theme]，AntD 层走 App.tsx algorithm。
 */
export function AppHeader() {
  const { theme, setTheme } = useTheme();
  const meta = THEME_META[theme] ?? THEME_META.light;

  return (
    <Layout.Header className="app-header">
        <Typography.Text strong className="app-header__title">
          灵犀 · 智能客服
        </Typography.Text>
      <Space className="app-header__actions">
        <Button
          type="text"
          size="small"
          icon={meta.icon}
          onClick={() => setTheme(meta.next)}
          aria-label={`主题：${meta.label}（点击切换）`}
          title={`主题：${meta.label}（点击切换）`}
          className="app-header__theme"
        />
        <NotificationBell />
        <UserMenu />
      </Space>
    </Layout.Header>
  );
}

export default AppHeader;
