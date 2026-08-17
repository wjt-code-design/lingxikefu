import { Segmented, Space, Typography } from 'antd';
import { DesktopOutlined, MoonOutlined, SunOutlined } from '@ant-design/icons';
import { Outlet } from 'react-router-dom';
import { useTheme, type ThemeMode } from '@/hooks/useTheme';

// C1 统一（V1 后无"深色"，dark = 柔和浅海盐变体）：与 AppHeader 同文案同图标
const options = [
  { label: '浅色', value: 'light', icon: <SunOutlined /> },
  { label: '柔和', value: 'dark', icon: <MoonOutlined /> },
  { label: '跟随系统', value: 'system', icon: <DesktopOutlined /> },
];

/**
 * 对话挂件最小外壳：无侧栏，极简顶条（品牌 + 主题切换），
 * 供 /widget（iframe 嵌入）与 /chat（站内完整页）复用。
 */
export function WidgetShell() {
  const { theme, setTheme } = useTheme();

  return (
    <div className="widget-shell">
      <div className="widget-shell__bar">
        <Typography.Text strong>灵犀 · 星河智家</Typography.Text>
        <Space>
          <Segmented
            size="small"
            options={options}
            value={theme}
            onChange={(v) => setTheme(v as ThemeMode)}
            aria-label="主题切换"
          />
        </Space>
      </div>
      <div className="widget-shell__body">
        <Outlet />
      </div>
    </div>
  );
}

export default WidgetShell;
