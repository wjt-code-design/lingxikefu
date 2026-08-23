import { useEffect, useState } from 'react';
import { Menu } from 'antd';
import type { MenuProps } from 'antd';
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
import { useQuery } from '@tanstack/react-query';
import { useAuthStore } from '@/store/authStore';
import { getMyPermissions } from '@/api/admin';
import { ROUTE_META } from '@/routes.config';
import type { RoleDef } from '@/contracts/api';

type MenuItem = Required<MenuProps>['items'][number];

/**
 * 路径 → 图标映射（集中管理，避免散落在 JSX 里）
 */
const ICON_MAP: Record<string, React.ReactNode> = {
  '/chat': <MessageOutlined />,
  '/profile': <UserOutlined />,
  '/feedback': <DislikeOutlined />,
  '/tickets': <FileTextOutlined />,
  '/faq': <BookOutlined />,
  '/help': <BookOutlined />,
  '/agent/dashboard': <CustomerServiceOutlined />,
  '/agent/sessions': <MessageOutlined />,
  '/agent/tickets': <FileTextOutlined />,
  '/agent/customers': <TeamOutlined />,
  '/agent/kb-search': <FileSearchOutlined />,
  '/admin/dashboard': <BarChartOutlined />,
  '/admin/knowledge': <BookOutlined />,
  '/admin/users': <TeamOutlined />,
  '/admin/roles': <SafetyOutlined />,
  '/admin/stats': <BarChartOutlined />,
  '/admin/feedback': <DislikeOutlined />,
  '/admin/sessions': <MessageOutlined />,
  '/admin/settings': <SettingOutlined />,
  '/admin/logs': <FileTextOutlined />,
};

/**
 * 路径 → 分组映射（按路由前缀自动归类）
 */
function getGroupKey(path: string): string {
  if (path.startsWith('/admin')) return 'grp-admin';
  if (path.startsWith('/agent')) return 'grp-agent';
  return 'grp-personal';
}

const GROUP_LABELS: Record<string, string> = {
  'grp-personal': '我的',
  'grp-agent': '客服工作台',
  'grp-admin': '运营后台',
};

const GROUP_ORDER = ['grp-personal', 'grp-agent', 'grp-admin'];

/**
 * 通用侧栏导航：基于后端 /auth/me/permissions 动态渲染菜单。
 * 后端 ROLE_DEFS 定义每个角色可见的路径，前端只渲染有权限的菜单项。
 */
export function SideNav() {
  const role = useAuthStore((s) => s.role);
  const navigate = useNavigate();
  const location = useLocation();
  const [openKeys, setOpenKeys] = useState<string[]>([]);

  // 从后端拉取当前用户可见菜单
  const { data: permissions, isLoading } = useQuery({
    queryKey: ['my-permissions', role],
    queryFn: getMyPermissions,
    placeholderData: (prev) => prev,
  });

  // 提取所有可见路径
  const visiblePaths = permissions?.roles?.flatMap((r: RoleDef) => r.menus) ?? [];

  // 按分组归类
  const groupMap = new Map<string, MenuItem[]>();
  for (const path of visiblePaths) {
    const groupKey = getGroupKey(path);
    if (!groupMap.has(groupKey)) {
      groupMap.set(groupKey, []);
    }
    const meta = ROUTE_META[path];
    groupMap.get(groupKey)!.push({
      key: path,
      icon: ICON_MAP[path] ?? <MessageOutlined />,
      label: meta?.title ?? path,
    });
  }

  // 按固定顺序组装分组
  const items: MenuItem[] = GROUP_ORDER
    .filter((gk) => groupMap.has(gk))
    .map((gk) => ({
      key: gk,
      type: 'group' as const,
      label: GROUP_LABELS[gk],
      children: groupMap.get(gk)!,
    }));

  // 根据当前路径自动展开对应分组
  useEffect(() => {
    const path = location.pathname;
    setOpenKeys([getGroupKey(path)]);
  }, [location.pathname]);

  if (isLoading) {
    return <div className="side-nav-loading" style={{ padding: 24 }} />;
  }

  return (
    <Menu
      className="side-nav"
      mode="inline"
      selectedKeys={[location.pathname]}
      openKeys={openKeys}
      onOpenChange={setOpenKeys}
      items={items}
      onClick={({ key }) => navigate(key)}
      style={{ flex: 1, minHeight: 0, overflowY: 'auto' }}
    />
  );
}

export default SideNav;
