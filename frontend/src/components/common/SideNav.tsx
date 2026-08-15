import { Menu } from 'antd';
import {
  BarChartOutlined,
  BookOutlined,
  FileTextOutlined,
  MessageOutlined,
  TeamOutlined,
} from '@ant-design/icons';
import { useLocation, useNavigate } from 'react-router-dom';
import { useAuthStore } from '@/store/authStore';

/**
 * 通用侧栏导航：按角色渲染菜单项。
 * - admin：知识库 / 用户管理 / 运营统计
 * - agent（Phase2 预留）：会话列表 / 工单
 */
export function SideNav() {
  const role = useAuthStore((s) => s.role);
  const navigate = useNavigate();
  const location = useLocation();

  const items = [
    ...(role === 'admin'
      ? [
          { key: '/admin/knowledge', icon: <BookOutlined />, label: '知识库' },
          { key: '/admin/users', icon: <TeamOutlined />, label: '用户管理' },
          { key: '/admin/stats', icon: <BarChartOutlined />, label: '运营统计' },
        ]
      : []),
    ...(role === 'admin' || role === 'agent'
      ? [
          { key: '/agent/sessions', icon: <MessageOutlined />, label: '会话列表' },
          { key: '/agent/customers', icon: <TeamOutlined />, label: '客户管理' },
          { key: '/agent/tickets', icon: <FileTextOutlined />, label: '工单（Phase2）' },
        ]
      : []),
  ];

  return (
    <Menu
      className="side-nav"
      mode="inline"
      selectedKeys={[location.pathname]}
      items={items}
      onClick={({ key }) => navigate(key)}
    />
  );
}

export default SideNav;
