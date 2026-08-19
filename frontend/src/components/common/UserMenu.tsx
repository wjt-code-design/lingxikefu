import { Avatar, Dropdown, Tag, Typography } from 'antd';
import {
  FileTextOutlined,
  LogoutOutlined,
  ProfileOutlined,
  UserOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { useAuthStore } from '@/store/authStore';
import { logout as logoutApi } from '@/api/auth';
import type { Role } from '@/contracts/api';

const ROLE_TAG: Record<Role, { label: string; color: string }> = {
  user: { label: '用户', color: 'blue' },
  agent: { label: '客服', color: 'cyan' },
  admin: { label: '管理员', color: 'gold' },
};

/**
 * 用户菜单（精简版 2026-08-19）：
 * - 通用项：个人中心 / 退出登录；
 * - user：额外保留"我的工单"——侧边栏无入口，只能从下拉菜单进；
 * - agent/admin：去掉"后台管理/工单管理"——这两项在侧边栏已经存在，
 *   重复入口会让用户困惑（哪个是真正的入口？）。
 * 由 AppHeader 与 WidgetShell 接入；无登录态时渲染 null。
 */
export function UserMenu() {
  const navigate = useNavigate();
  const { refreshToken, clear, user, role } = useAuthStore();

  if (!user || !role) return null;

  const initial = (user.email || user.phone || '客').slice(0, 1).toUpperCase();
  const tag = ROLE_TAG[role];
  const isStaff = role === 'agent' || role === 'admin';

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
    <Dropdown
      trigger={['click']}
      menu={{
        items: [
          { key: 'profile', icon: <ProfileOutlined />, label: '个人中心' },
          // 仅 user 角色显示"我的工单"——侧边栏无入口，菜单是唯一通路
          ...(isStaff ? [] : [{ key: 'tickets', icon: <FileTextOutlined />, label: '我的工单' }]),
          { type: 'divider' },
          { key: 'logout', icon: <LogoutOutlined />, label: '退出登录', danger: true },
        ],
        onClick: ({ key }) => {
          if (key === 'logout') void handleLogout();
          else if (key === 'profile') navigate('/profile');
          else if (key === 'tickets') navigate('/tickets');
        },
      }}
    >
      <button type="button" className="user-menu" aria-label="用户菜单">
        <Avatar size={28} icon={<UserOutlined />} className="user-menu__avatar">
          {initial}
        </Avatar>
        <Tag className="user-menu__tag" color={tag.color}>
          {tag.label}
        </Tag>
        <Typography.Text className="user-menu__name">
          {user.email || user.phone || '用户'}
        </Typography.Text>
      </button>
    </Dropdown>
  );
}

export default UserMenu;
