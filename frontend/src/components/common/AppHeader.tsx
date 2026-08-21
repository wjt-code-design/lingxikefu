import { Layout, Space, Typography } from 'antd';
import { NotificationBell } from './NotificationBell';
import { UserMenu } from './UserMenu';

/**
 * 内部台顶栏：品牌 + 通知铃铛 + UserMenu。
 * 2026-08-20：取消深色 / 跟随系统切换，仅保留浅色。
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
