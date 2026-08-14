import { Layout, Segmented, Space, Typography } from 'antd';
import { DesktopOutlined, MoonOutlined, SunOutlined } from '@ant-design/icons';
import { useTheme, type ThemeMode } from '@/hooks/useTheme';

const themeOptions = [
  { label: '浅色', value: 'light', icon: <SunOutlined /> },
  { label: '深色', value: 'dark', icon: <MoonOutlined /> },
  { label: '跟随系统', value: 'system', icon: <DesktopOutlined /> },
];

/**
 * 通用顶栏：品牌 + 主题切换（light/dark/system）。
 * 主题切换会同步 <html data-theme> 与 AntD darkAlgorithm（见 App.tsx / useTheme）。
 */
export function AppHeader() {
  const { theme, setTheme } = useTheme();

  return (
    <Layout.Header className="app-header">
      <Typography.Text strong className="app-header__title">
        灵犀 · Lingxi 智能客服
      </Typography.Text>
      <Space className="app-header__actions">
        <Segmented
          options={themeOptions}
          value={theme}
          onChange={(v) => setTheme(v as ThemeMode)}
          aria-label="主题切换"
        />
      </Space>
    </Layout.Header>
  );
}

export default AppHeader;
