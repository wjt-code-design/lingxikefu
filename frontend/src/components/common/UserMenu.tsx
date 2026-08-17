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
 * 用户菜单（三端复用）：头像（账号首字符）+ 角色徽标 + 下拉（个人中心/我的工单/退出）。
 * 由 AppHeader 与 WidgetShell 接入；无登录态时渲染 null。
 */
export function UserMenu() {
  const navigate = useNavigate();
  const { refreshToken, clear, user, role } = useAuthStore();

  if (!user || !role) return null;

  const initial = (user.email || user.phone || '客').slice(0, 1).toUpperCase();
  const tag = ROLE_TAG[role];

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
          { key: 'tickets', icon: <FileTextOutlined />, label: '我的工单' },
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
