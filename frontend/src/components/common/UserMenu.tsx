import { Avatar, Dropdown, Tag, Typography } from 'antd';
import { LogoutOutlined, UserAddOutlined, UserOutlined } from '@ant-design/icons';
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
 * 用户菜单（2026-08-20 极简版）：
 * 所有导航入口（对话/工单/个人中心/反馈/工作台/管理端）均在左侧边栏，
 * 下拉菜单仅保留「退出登录」，避免重复入口。
 * 由 AppHeader 接入；无登录态时渲染 null。
 * 批次B（2026-09-04）：guest（匿名体验主体）显示「游客」标签 + 「注册转正」入口。
 */
export function UserMenu() {
  const navigate = useNavigate();
  const { refreshToken, clear, user, role } = useAuthStore();

  if (!user || !role) return null;

  const isGuest = Boolean(user.guest);
  const initial = (user.email || user.phone || '客').slice(0, 1).toUpperCase();
  const tag = isGuest ? { label: '游客', color: 'default' } : ROLE_TAG[role];

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
          // guest 无凭证可再次登录（超期即清理）→ 转正 = 注册正式账号
          ...(isGuest
            ? [{ key: 'register', icon: <UserAddOutlined />, label: '注册转正' }]
            : []),
          { key: 'logout', icon: <LogoutOutlined />, label: '退出登录', danger: true },
        ],
        onClick: ({ key }) => {
          if (key === 'logout') void handleLogout();
          if (key === 'register') navigate('/register');
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
