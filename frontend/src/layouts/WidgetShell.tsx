import { Space, Typography } from 'antd';
import { Outlet } from 'react-router-dom';
import { UserMenu } from '@/components/common/UserMenu';

/**
 * 对话挂件最小外壳：无侧栏，极简顶条（品牌 + 用户菜单）。
 * 恒为浅色海盐系（拒绝深色），无主题切换——仅一种颜色，切换入口无意义，已移除。
 * 供 /widget（iframe 嵌入）、/chat（站内完整页）、/faq、/help、/tickets、/profile 复用。
 */
export function WidgetShell() {
  return (
    <div className="widget-shell">
      <div className="widget-shell__bar">
        <Typography.Text strong>灵犀 · 星河智家</Typography.Text>
        <Space>
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
