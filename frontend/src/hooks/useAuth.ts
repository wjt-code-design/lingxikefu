import { useAuthStore } from '@/store/authStore';

/**
 * 认证态 hook（FE-02 起接入真实登录/登出）。
 * 当前为 authStore 的只读投影，登出等动作在 FE-02 实现。
 */
export function useAuth() {
  const token = useAuthStore((s) => s.token);
  const refreshToken = useAuthStore((s) => s.refreshToken);
  const user = useAuthStore((s) => s.user);
  const role = useAuthStore((s) => s.role);
  const setAuth = useAuthStore((s) => s.setAuth);
  const setUser = useAuthStore((s) => s.setUser);
  const clear = useAuthStore((s) => s.clear);

  return {
    token,
    refreshToken,
    user,
    role,
    setAuth,
    setUser,
    clear,
    isAuthenticated: Boolean(token),
  };
}
