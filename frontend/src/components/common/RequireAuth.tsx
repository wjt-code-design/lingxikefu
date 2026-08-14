import type { ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { useAuthStore } from '@/store/authStore';
import type { Role } from '@/contracts/api';
import ForbiddenPage from '@/pages/ForbiddenPage';

interface RequireAuthProps {
  /** 允许访问的角色集合；缺省表示任意已登录用户 */
  roles?: Role[];
  children: ReactNode;
}

/**
 * 路由守卫：
 * - 未登录访问受保护路由 → 重定向 /login（记录来源 from）
 * - 已登录但角色不在 roles 内（如普通用户访问 /admin/*）→ 重定向 /login
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
    return <ForbiddenPage />;
  }
  return <>{children}</>;
}

export default RequireAuth;
