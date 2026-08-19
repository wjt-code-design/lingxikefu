import { Layout } from 'antd';
import { Outlet } from 'react-router-dom';
import AppHeader from '@/components/common/AppHeader';
import SideNav from '@/components/common/SideNav';
import { RouteChrome } from '@/components/common/RouteChrome';

/**
 * 内部工作台外壳（semantic-workbench）：#2E5D85 深侧栏白字菜单（唯一中深色）+ 发丝线 + 浅冷灰内容区。
 * 供 /admin/* 与 /agent/* 使用（仅菜单权限不同），路由由 RequireAuth 守卫。
 * .app-workbench 触发 tokens.css 的 workbench 语义覆盖（User 端不受影响）。
 */
export function AdminLayout() {
  return (
    <Layout className="admin-layout app-workbench" hasSider>
      <Layout.Sider collapsible width={220} className="admin-layout__sider">
        <div className="admin-layout__logo">灵犀</div>
        <SideNav />
      </Layout.Sider>
      <Layout className="admin-layout__main">
        <AppHeader />
        <Layout.Content className="admin-layout__content">
          <RouteChrome />
          <Outlet />
        </Layout.Content>
      </Layout>
    </Layout>
  );
}

export default AdminLayout;
