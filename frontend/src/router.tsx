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
import KnowledgePage from '@/pages/admin/KnowledgePage';
import UsersPage from '@/pages/admin/UsersPage';
import StatsPage from '@/pages/admin/StatsPage';

/**
 * 路由表（规划 §1.1）：
 * - /login /register：匿名
 * - /widget：匿名/普通用户（嵌入挂件）
 * - /chat：已登录（站内完整对话）
 * - /admin/*：仅 admin
 * - /agent/*：agent/admin（Phase2 预留）
 */
export function AppRoutes() {
  return (
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

      {/* Phase2 预留：/agent/sessions、/agent/tickets */}
      <Route
        path="/agent"
        element={
          <RequireAuth roles={['admin', 'agent']}>
            <AdminLayout />
          </RequireAuth>
        }
      >
        <Route index element={<Navigate to="/agent/sessions" replace />} />
        <Route
          path="sessions"
          element={<EmptyState title="会话列表" description="Phase2：客服实时接手（WebSocket）" />}
        />
        <Route
          path="tickets"
          element={<EmptyState title="工单列表" description="Phase2：工单流转视图" />}
        />
      </Route>

      <Route path="/" element={<Navigate to="/chat" replace />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default AppRoutes;
