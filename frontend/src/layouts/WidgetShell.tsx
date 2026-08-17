import { Segmented, Space, Typography } from 'antd';
import { DesktopOutlined, SunOutlined } from '@ant-design/icons';
import { Outlet } from 'react-router-dom';
import { useTheme, type ThemeMode } from '@/hooks/useTheme';
import { UserMenu } from '@/components/common/UserMenu';

// 拒绝深色：仅「浅色 / 跟随系统」两档（跟随系统恒解析为浅色）。原「柔和(dark)」假深色档已删除。
const options = [
  { label: '浅色', value: 'light', icon: <SunOutlined /> },
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
          <UserMenu />
        </Space>
      </div>
      <div className="widget-shell__body">
        <Outlet />
      </div>
    </div>
  );
}

export default WidgetShell;
