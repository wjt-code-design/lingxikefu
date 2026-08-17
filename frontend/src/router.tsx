import { lazy, Suspense } from 'react';
import { Skeleton } from 'antd';
import { Navigate, Route, Routes } from 'react-router-dom';
import AuthLayout from '@/layouts/AuthLayout';
import AdminLayout from '@/layouts/AdminLayout';
import WidgetShell from '@/layouts/WidgetShell';
import RequireAuth from '@/components/common/RequireAuth';
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
const AgentTicketsPage = lazy(() => import('@/pages/agent/TicketsPage')); // T1：工单流转
const KnowledgePage = lazy(() => import('@/pages/admin/KnowledgePage'));
const NotFoundPage = lazy(() => import('@/pages/NotFoundPage'));
const MyTicketsPage = lazy(() => import('@/pages/MyTicketsPage'));
const UsersPage = lazy(() => import('@/pages/admin/UsersPage'));
const StatsPage = lazy(() => import('@/pages/admin/StatsPage'));
const FeedbackPage = lazy(() => import('@/pages/admin/FeedbackPage'));
// T4'：新路由（403/个人中心/服务首页）
const ForbiddenPage = lazy(() => import('@/pages/ForbiddenPage'));
const ProfilePage = lazy(() => import('@/pages/ProfilePage'));
const LandingPage = lazy(() => import('@/pages/LandingPage'));

function RouteFallback() {
  // U1：路由懒加载 fallback 用骨架屏（感知性能，替代"加载中…"文字）
  return (
    <div className="route-fallback" style={{ padding: 24 }}>
      <Skeleton active paragraph={{ rows: 8 }} />
    </div>
  );
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
  const role = useAuthStore((s) => s.role);

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

        {/* P2-1：用户「我的工单」（只读，user 可调） */}
        <Route
          path="/tickets"
          element={
            <RequireAuth>
              <WidgetShell />
            </RequireAuth>
          }
        >
          <Route index element={<MyTicketsPage />} />
        </Route>

        {/* T4'：个人中心（user/agent/admin 均可；/auth/me 已有） */}
        <Route
          path="/profile"
          element={
            <RequireAuth>
              <WidgetShell />
            </RequireAuth>
          }
        >
          <Route index element={<ProfilePage />} />
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
          <Route path="tickets" element={<AgentTicketsPage />} />
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
          <Route path="feedback" element={<FeedbackPage />} />
          {/* T4'：dashboard 入口一行重定向 → stats（不建独立占位页） */}
          <Route path="dashboard" element={<Navigate to="/admin/stats" replace />} />
        </Route>

        {/* T4'：/403 无权限 */}
        <Route path="/403" element={<ForbiddenPage />} />

        {/* T4'：/ 服务首页——已登录按角色分流，未登录展示品牌落地页 */}
        <Route path="/" element={role ? <Navigate to={home} replace /> : <LandingPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </Suspense>
  );
}

export default AppRoutes;
