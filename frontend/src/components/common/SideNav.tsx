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
  UserOutlined,
} from '@ant-design/icons';
import { useLocation, useNavigate } from 'react-router-dom';
import { useAuthStore } from '@/store/authStore';

/**
 * 通用侧栏导航：按角色渲染菜单项。
 * - user：我的对话 / 我的工单 / 个人中心 / 意见反馈
 * - agent：客服工作台 / 会话列表 / 工单处理 / 客户管理 / 知识快搜（+ 我的对话 / 个人中心）
 * - admin：运营总览 / 知识库 / 用户管理 / 运营统计 / 踩反馈 / 会话审计 / 系统设置 / 审计日志（+ 我的对话 / 个人中心）
 */
export function SideNav() {
  const role = useAuthStore((s) => s.role);
  const navigate = useNavigate();
  const location = useLocation();

  // 所有已登录用户都有的"个人"菜单
  const personalMenu = {
    type: 'group' as const,
    label: '我的',
    children: [
      { key: '/chat', icon: <MessageOutlined />, label: '我的对话' },
      { key: '/tickets', icon: <FileTextOutlined />, label: '我的工单' },
      { key: '/profile', icon: <UserOutlined />, label: '个人中心' },
      { key: '/feedback', icon: <DislikeOutlined />, label: '意见反馈' },
    ],
  };

  const items = [
    // 个人菜单（所有角色可见）
    personalMenu,
    // 客服工作台（agent 或 admin 可见）
    ...(role === 'admin' || role === 'agent'
      ? [
          {
            type: 'group' as const,
            label: '客服工作台',
            children: [
              { key: '/agent/dashboard', icon: <CustomerServiceOutlined />, label: '工作台首页' },
              { key: '/agent/sessions', icon: <MessageOutlined />, label: '会话列表' },
              { key: '/agent/tickets', icon: <FileTextOutlined />, label: '工单处理' },
              { key: '/agent/customers', icon: <TeamOutlined />, label: '客户管理' },
              { key: '/agent/kb-search', icon: <FileSearchOutlined />, label: '知识快搜' },
            ],
          },
        ]
      : []),
    // 运营后台（仅 admin 可见）
    ...(role === 'admin'
      ? [
          {
            type: 'group' as const,
            label: '运营后台',
            children: [
              { key: '/admin/dashboard', icon: <BarChartOutlined />, label: '运营总览' },
              { key: '/admin/knowledge', icon: <BookOutlined />, label: '知识库' },
              { key: '/admin/users', icon: <TeamOutlined />, label: '用户管理' },
              { key: '/admin/roles', icon: <SafetyOutlined />, label: '角色权限' },
              { key: '/admin/stats', icon: <BarChartOutlined />, label: '数据统计' },
              { key: '/admin/feedback', icon: <DislikeOutlined />, label: '评价反馈' },
              { key: '/admin/sessions', icon: <MessageOutlined />, label: '会话审计' },
              { key: '/admin/settings', icon: <SettingOutlined />, label: '系统设置' },
              { key: '/admin/logs', icon: <FileTextOutlined />, label: '操作日志' },
            ],
          },
        ]
      : []),
  ];

  // 计算默认展开的分组：当前路由所在分组
  const getDefaultOpenKeys = () => {
    const path = location.pathname;
    if (path.startsWith('/admin')) return ['admin-group'];
    if (path.startsWith('/agent')) return ['agent-group'];
    return ['personal-group'];
  };

  return (
    <Menu
      className="side-nav"
      mode="inline"
      selectedKeys={[location.pathname]}
      defaultOpenKeys={getDefaultOpenKeys()}
      items={items}
      onClick={({ key }) => navigate(key)}
    />
  );
}

export default SideNav;
