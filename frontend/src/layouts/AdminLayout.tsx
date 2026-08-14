import { Layout } from 'antd';
import { Outlet } from 'react-router-dom';
import AppHeader from '@/components/common/AppHeader';
import SideNav from '@/components/common/SideNav';

/**
 * 管理后台外壳：左侧可收起侧栏 + 顶栏 + 内容区。
 * 供 /admin/* 与 /agent/*（Phase2）使用，路由由 RequireAuth 守卫。
 */
export function AdminLayout() {
  return (
    <Layout className="admin-layout" hasSider>
      <Layout.Sider collapsible width={220} className="admin-layout__sider">
        <div className="admin-layout__logo">灵犀</div>
        <SideNav />
      </Layout.Sider>
      <Layout className="admin-layout__main">
        <AppHeader />
        <Layout.Content className="admin-layout__content">
          <Outlet />
        </Layout.Content>
      </Layout>
    </Layout>
  );
}

export default AdminLayout;
