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
 * 角色分流 home：user→/chat，agent→/agent/dashboard，admin→/admin/dashboard。
 * 懒加载：工作台/管理端按模块分割 chunk（客户侧 /chat 留在主包，首屏更快）。
 *
 * - /login /register：匿名
 * - /widget：匿名/普通用户（嵌入挂件）
 * - /faq /help：匿名公开页（Phase3）
 * - /chat：已登录（站内完整对话，user/agent/admin 均可）
 * - /agent/*：agent/admin（工作台：工作台首页 / 会话列表 / 工单 / 客户 / 快搜）
 * - /admin/*：仅 admin（运营总览 / 知识库 / 用户 / 统计 / 反馈 / 会话审计 / 设置 / 日志）
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
// Phase3：路由扩展 8 项（运营总览/会话审计/客服工作台/FAQ/系统设置/审计日志/快搜/帮助中心）
const AdminDashboardPage = lazy(() => import('@/pages/admin/DashboardPage'));
const SessionsAuditPage = lazy(() => import('@/pages/admin/SessionsAuditPage'));
const SettingsPage = lazy(() => import('@/pages/admin/SettingsPage'));
const LogsPage = lazy(() => import('@/pages/admin/LogsPage'));
const RolesPage = lazy(() => import('@/pages/admin/RolesPage'));
const AgentDashboardPage = lazy(() => import('@/pages/agent/DashboardPage'));
const KbSearchPage = lazy(() => import('@/pages/agent/KbSearchPage'));
const FaqPage = lazy(() => import('@/pages/FaqPage'));
const HelpPage = lazy(() => import('@/pages/HelpPage'));
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

/** 按角色返回登录后首页（参考 dianshangkefu home 分流；Phase3：登录后直达各自工作台首页） */
function useHome(): string {
  const role = useAuthStore((s) => s.role);
  if (role === 'admin') return '/admin/dashboard';
  if (role === 'agent') return '/agent/dashboard';
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

        {/* Phase3：/faq /help 匿名公开页（无需登录，WidgetShell 外壳） */}
        <Route path="/faq" element={<WidgetShell />}>
          <Route index element={<FaqPage />} />
        </Route>
        <Route path="/help" element={<WidgetShell />}>
          <Route index element={<HelpPage />} />
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

        {/* Agent 工作台（参考 dianshangkefu /workbench 模块化；Phase3：新增 dashboard/kb-search） */}
        <Route
          path="/agent"
          element={
            <RequireAuth roles={['admin', 'agent']}>
              <AdminLayout />
            </RequireAuth>
          }
        >
          <Route index element={<Navigate to="/agent/dashboard" replace />} />
          <Route path="dashboard" element={<AgentDashboardPage />} />
          <Route path="sessions" element={<AgentSessionsPage />} />
          <Route path="customers" element={<AgentCustomersPage />} />
          <Route path="tickets" element={<AgentTicketsPage />} />
          <Route path="kb-search" element={<KbSearchPage />} />
        </Route>

        {/* Admin 后台（Phase3：dashboard 由重定向改为真实运营总览，新增 sessions/settings/logs） */}
        <Route
          path="/admin"
          element={
            <RequireAuth roles={['admin']}>
              <AdminLayout />
            </RequireAuth>
          }
        >
          <Route index element={<Navigate to="/admin/dashboard" replace />} />
          <Route path="dashboard" element={<AdminDashboardPage />} />
          <Route path="knowledge" element={<KnowledgePage />} />
          <Route path="users" element={<UsersPage />} />
          <Route path="roles" element={<RolesPage />} />
          <Route path="stats" element={<StatsPage />} />
          <Route path="feedback" element={<FeedbackPage />} />
          <Route path="sessions" element={<SessionsAuditPage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="logs" element={<LogsPage />} />
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
