import { lazy, Suspense } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import AuthLayout from '@/layouts/AuthLayout';
import AdminLayout from '@/layouts/AdminLayout';
import WidgetShell from '@/layouts/WidgetShell';
import RequireAuth from '@/components/common/RequireAuth';
import { EmptyState } from '@/components/common/EmptyState';
import LoginPage from '@/pages/LoginPage';
import RegisterPage from '@/pages/RegisterPage';
import ChatPage from '@/pages/ChatPage';
import WidgetPage from '@/pages/WidgetPage';
import { useAuthStore } from '@/store/authStore';

/**
 * 路由表（规划 §1.1 + 2026-08-16 参考 dianshangkefu 路由逻辑扩展）：
 * 角色分流 home：user→/chat，agent→/agent/sessions，admin→/admin/knowledge。
 * 懒加载：工作台/管理端按模块分割 chunk（客户侧 /chat 留在主包，首屏更快）。
 *
 * - /login /register：匿名
 * - /widget：匿名/普通用户（嵌入挂件）
 * - /chat：已登录（站内完整对话，user/agent/admin 均可）
 * - /agent/*：agent/admin（工作台：会话列表 / 工单 / 客户 / 数据）
 * - /admin/*：仅 admin
 */

// 懒加载工作台/管理端模块（参考 dianshangkefu：客户侧主 chunk，内部工具独立 chunk）
const AgentSessionsPage = lazy(() => import('@/pages/agent/SessionsPage'));
const AgentCustomersPage = lazy(() => import('@/pages/agent/CustomersPage'));
const KnowledgePage = lazy(() => import('@/pages/admin/KnowledgePage'));
const UsersPage = lazy(() => import('@/pages/admin/UsersPage'));
const StatsPage = lazy(() => import('@/pages/admin/StatsPage'));

function RouteFallback() {
  return <div className="route-fallback">加载中…</div>;
}

/** 按角色返回登录后首页（参考 dianshangkefu home 分流） */
function useHome(): string {
  const role = useAuthStore((s) => s.role);
  if (role === 'admin') return '/admin/knowledge';
  if (role === 'agent') return '/agent/sessions';
  return '/chat';
}

export function AppRoutes() {
  const home = useHome();

  return (
    <Suspense fallback={<RouteFallback />}>
      <Routes>
        <Route element={<AuthLayout />}>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/register" element={<RegisterPage />} />
        </Route>

        <Route path="/widget" element={<WidgetShell />}>
          <Route index element={<WidgetPage />} />
        </Route>

        <Route
          path="/chat"
          element={
            <RequireAuth>
              <WidgetShell />
            </RequireAuth>
          }
        >
          <Route index element={<ChatPage />} />
        </Route>

        {/* Agent 工作台（参考 dianshangkefu /workbench 模块化） */}
        <Route
          path="/agent"
          element={
            <RequireAuth roles={['admin', 'agent']}>
              <AdminLayout />
            </RequireAuth>
          }
        >
          <Route index element={<Navigate to="/agent/sessions" replace />} />
          <Route path="sessions" element={<AgentSessionsPage />} />
          <Route path="customers" element={<AgentCustomersPage />} />
          <Route
            path="tickets"
            element={<EmptyState title="工单列表" description="Phase2：工单流转视图" />}
          />
        </Route>

        <Route
          path="/admin"
          element={
            <RequireAuth roles={['admin']}>
              <AdminLayout />
            </RequireAuth>
          }
        >
          <Route index element={<Navigate to="/admin/knowledge" replace />} />
          <Route path="knowledge" element={<KnowledgePage />} />
          <Route path="users" element={<UsersPage />} />
          <Route path="stats" element={<StatsPage />} />
        </Route>

        <Route path="/" element={<Navigate to={home} replace />} />
        <Route path="*" element={<Navigate to={home} replace />} />
      </Routes>
    </Suspense>
  );
}

export default AppRoutes;
