import { Segmented, Space, Typography } from 'antd';
import { Outlet } from 'react-router-dom';
import { useTheme, type ThemeMode } from '@/hooks/useTheme';

const options = [
  { label: '浅', value: 'light' },
  { label: '深', value: 'dark' },
  { label: '系统', value: 'system' },
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
        <Typography.Text strong>灵犀 · Lingxi</Typography.Text>
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
