import { Space, Typography } from 'antd';
import { Outlet, useNavigate } from 'react-router-dom';
import { NotificationBell } from '@/components/common/NotificationBell';
import { UserMenu } from '@/components/common/UserMenu';
import { useAuthStore } from '@/store/authStore';

/**
 * 对话挂件最小外壳：无侧栏，极简顶条（品牌 + 用户菜单）。
 * 恒为浅色海盐系（拒绝深色），无主题切换——仅一种颜色，切换入口无意义，已移除。
 * 供 /widget（iframe 嵌入）、/chat（站内完整页）、/faq、/help、/tickets、/profile 复用。
 *
 * 导航闭环（2026-08-18）：
 * - 品牌可点击 → 回角色首页（user→/chat，agent→/agent/dashboard，admin→/admin/dashboard，匿名→/）；
 * - agent/admin 补通知铃铛（避免从工作台进入 /chat 后收不到工单/转人工通知，成为导航孤岛）。
 */
export function WidgetShell() {
  const navigate = useNavigate();
  const role = useAuthStore((s) => s.role);

  const home = role === 'admin' ? '/admin/dashboard' : role === 'agent' ? '/agent/dashboard' : '/chat';

  return (
    <div className="widget-shell">
      <div className="widget-shell__bar">
        <button
          type="button"
          className="widget-shell__brand"
          onClick={() => navigate(role ? home : '/')}
        >
          <Typography.Text strong>灵犀 · 星河智家</Typography.Text>
        </button>
        <Space>
          <NotificationBell />
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
