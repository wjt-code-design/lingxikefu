import { Layout, Space, Typography } from 'antd';
import { NotificationBell } from './NotificationBell';
import { UserMenu } from './UserMenu';

/**
 * 内部台顶栏：品牌 + 通知铃铛 + UserMenu。
 * 恒为浅色海盐系（拒绝深色），无主题切换——仅一种颜色，切换入口无意义，已移除。
 */
export function AppHeader() {
  return (
    <Layout.Header className="app-header">
        <Typography.Text strong className="app-header__title">
          灵犀 · 智能客服
        </Typography.Text>
      <Space className="app-header__actions">
        <NotificationBell />
        <UserMenu />
      </Space>
    </Layout.Header>
  );
}

export default AppHeader;
