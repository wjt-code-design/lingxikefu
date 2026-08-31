import { useEffect, useRef, useState } from 'react';
import { Layout } from 'antd';
import { Outlet } from 'react-router-dom';
import AppHeader from '@/components/common/AppHeader';
import SideNav from '@/components/common/SideNav';
import { RouteChrome } from '@/components/common/RouteChrome';

const SIDER_COLLAPSED_KEY = 'sider-collapsed';

/**
 * 内部工作台外壳（semantic-workbench）：#2E5D85 深侧栏白字菜单（唯一中深色）+ 发丝线 + 浅冷灰内容区。
 * 供 /admin/* 与 /agent/* 使用（仅菜单权限不同），路由由 RequireAuth 守卫。
 * .app-workbench 触发 tokens.css 的 workbench 语义覆盖（User 端不受影响）。
 *
 * UI 审查低20：折叠状态受控 + localStorage 持久化（刷新/重进保持用户上次的选择）。
 * hydrate 守卫：antd Sider mount 时必发一次 responsive onCollapse（matchMedia 初始化），
 * 不加守卫会把 localStorage 恢复的折叠态立刻覆盖回展开——用户已有显式选择时，
 * mount 时的断点初始化事件不覆盖（从未选择过则照常跟随断点自动收起）。
 */
export function AdminLayout() {
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem(SIDER_COLLAPSED_KEY) === '1'
  );
  const hydratedRef = useRef(false);
  useEffect(() => {
    const id = window.setTimeout(() => {
      hydratedRef.current = true;
    }, 0);
    return () => window.clearTimeout(id);
  }, []);
  const updateCollapsed = (v: boolean) => {
    setCollapsed(v);
    try {
      localStorage.setItem(SIDER_COLLAPSED_KEY, v ? '1' : '0');
    } catch {
      /* 隐私模式等存储不可用：仅内存态生效 */
    }
  };
  return (
    <Layout className="admin-layout app-workbench" hasSider>
      {/* breakpoint="lg"：窄屏（<992px，平板竖屏/小窗 iframe）自动收起为 64px；collapsedWidth 可手动再折叠 */}
      <Layout.Sider
        collapsible
        breakpoint="lg"
        collapsed={collapsed}
        onCollapse={(v, type) => {
          if (type === 'clickTrigger') {
            updateCollapsed(v); // 用户手动点击折叠按钮 → 持久化
            return;
          }
          // responsive：hydrate 前是 antd mount 初始化事件——已有持久化选择时不覆盖
          if (!hydratedRef.current && localStorage.getItem(SIDER_COLLAPSED_KEY) !== null) {
            return;
          }
          setCollapsed(v);
        }}
        collapsedWidth={64}
        width={220}
        className="admin-layout__sider"
      >
        <div className="admin-layout__logo">灵犀</div>
        <SideNav />
      </Layout.Sider>
      <Layout className="admin-layout__main">
        <AppHeader />
        <Layout.Content className="admin-layout__content" id="main-content">
          <RouteChrome />
          <Outlet />
        </Layout.Content>
      </Layout>
    </Layout>
  );
}

export default AdminLayout;
