import { Button, Layout, Segmented, Space, Typography } from 'antd';
import { DesktopOutlined, LogoutOutlined, MoonOutlined, SunOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { useAuthStore } from '@/store/authStore';
import { logout as logoutApi } from '@/api/auth';
import { useTheme, type ThemeMode } from '@/hooks/useTheme';

const themeOptions = [
  { label: '浅色', value: 'light', icon: <SunOutlined /> },
  { label: '柔和', value: 'dark', icon: <MoonOutlined /> },
  { label: '跟随系统', value: 'system', icon: <DesktopOutlined /> },
];

/**
 * 通用顶栏：品牌 + 主题切换（light/dark/system）+ 退出登录。
 * 主题切换会同步 <html data-theme> 与 AntD darkAlgorithm（见 App.tsx / useTheme）。
 */
export function AppHeader() {
  const { theme, setTheme } = useTheme();
  const navigate = useNavigate();
  const { refreshToken, clear } = useAuthStore();

  const handleLogout = async () => {
    try {
      if (refreshToken) await logoutApi(refreshToken);
    } catch {
      /* 后端吊销失败也不阻断本地登出 */
    }
    clear();
    navigate('/login');
  };

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
        <Button icon={<LogoutOutlined />} onClick={handleLogout}>
          退出登录
        </Button>
      </Space>
    </Layout.Header>
  );
}

export default AppHeader;
