import { Menu } from 'antd';
import {
  BarChartOutlined,
  BookOutlined,
  CustomerServiceOutlined,
  DislikeOutlined,
  FileSearchOutlined,
  FileTextOutlined,
  MessageOutlined,
  SafetyOutlined,
  SettingOutlined,
  TeamOutlined,
} from '@ant-design/icons';
import { useLocation, useNavigate } from 'react-router-dom';
import { useAuthStore } from '@/store/authStore';

/**
 * 通用侧栏导航：按角色渲染菜单项。
 * - admin：运营总览 / 知识库 / 用户管理 / 运营统计 / 踩反馈 / 会话审计 / 系统设置 / 审计日志
 * - agent：客服工作台 / 会话列表 / 工单 / 客户管理 / 知识库快搜
 */
export function SideNav() {
  const role = useAuthStore((s) => s.role);
  const navigate = useNavigate();
  const location = useLocation();

  const items = [
    ...(role === 'admin'
      ? [
          { key: '/admin/dashboard', icon: <BarChartOutlined />, label: '运营总览' },
          { key: '/admin/knowledge', icon: <BookOutlined />, label: '知识库' },
          { key: '/admin/users', icon: <TeamOutlined />, label: '用户管理' },
          { key: '/admin/roles', icon: <SafetyOutlined />, label: '权限管理' },
          { key: '/admin/stats', icon: <BarChartOutlined />, label: '运营统计' },
          { key: '/admin/feedback', icon: <DislikeOutlined />, label: '踩反馈' },
          { key: '/admin/sessions', icon: <MessageOutlined />, label: '会话审计' },
          { key: '/admin/settings', icon: <SettingOutlined />, label: '系统设置' },
          { key: '/admin/logs', icon: <FileTextOutlined />, label: '审计日志' },
        ]
      : []),
    ...(role === 'admin' || role === 'agent'
      ? [
          { key: '/agent/dashboard', icon: <CustomerServiceOutlined />, label: '客服工作台' },
          { key: '/agent/sessions', icon: <MessageOutlined />, label: '会话列表' },
          { key: '/agent/tickets', icon: <FileTextOutlined />, label: '工单' },
          { key: '/agent/customers', icon: <TeamOutlined />, label: '客户管理' },
          { key: '/agent/kb-search', icon: <FileSearchOutlined />, label: '知识库快搜' },
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
