import { Suspense, lazy, type ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { Skeleton } from 'antd';
import { useAuthStore } from '@/store/authStore';
import type { Role } from '@/contracts/api';

const ErrorPage = lazy(() => import('@/pages/ErrorPage'));

function ErrorPageFallback() {
  return (
    <div style={{ padding: 24 }}>
      <Skeleton active paragraph={{ rows: 4 }} />
    </div>
  );
}

interface RequireAuthProps {
  /** 允许访问的角色集合；缺省表示任意已登录用户 */
  roles?: Role[];
  children: ReactNode;
}

/**
 * 路由守卫：
 * - 未登录访问受保护路由 → 重定向 /login（记录来源 from）
 * - 已登录但角色不在 roles 内（如普通用户访问 /admin/*）→ 渲染 403 无权限页（不踢回登录）
 */
export function RequireAuth({ roles, children }: RequireAuthProps) {
  const token = useAuthStore((s) => s.token);
  const role = useAuthStore((s) => s.role);
  const location = useLocation();

  if (!token) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  if (roles && (!role || !roles.includes(role))) {
    // 已登录但角色不足 → 展示 403 页，而非踢回登录页
    return (
      <Suspense fallback={<ErrorPageFallback />}>
        <ErrorPage type="403" />
      </Suspense>
    );
  }
  return <>{children}</>;
}

export default RequireAuth;
