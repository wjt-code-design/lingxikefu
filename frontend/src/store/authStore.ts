import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { AuthResp, MeResp, Role } from '@/contracts/api';

interface AuthState {
  token: string | null;
  refreshToken: string | null;
  user: MeResp | null;
  role: Role | null;
  /** 登录成功写入 token/refreshToken/user/role（FE-02 调用） */
  setAuth: (resp: AuthResp, user: MeResp) => void;
  setUser: (user: MeResp) => void;
  clear: () => void;
}

/**
 * 认证状态，持久化到 localStorage（key: lingxi-auth）。
 * BUG-15：access token 仅存内存，不持久化（降 XSS 泄露面）；
 * 仅持久化 refreshToken/user/role，刷新页面后由 api/auth.bootstrapAuth 静默续期恢复会话。
 * 路由守卫 RequireAuth 读取 token/role 做访问控制。
 */
export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      refreshToken: null,
      user: null,
      role: null,
      setAuth: (resp, user) =>
        set({
          token: resp.access_token,
          refreshToken: resp.refresh_token,
          user,
          role: user.role,
        }),
      setUser: (user) => set({ user, role: user.role }),
      clear: () => set({ token: null, refreshToken: null, user: null, role: null }),
    }),
    {
      name: 'lingxi-auth',
      // BUG-15：token（access token）不落 localStorage；refreshToken/user/role 持久化以支持静默续期
      partialize: (s) => ({
        refreshToken: s.refreshToken,
        user: s.user,
        role: s.role,
      }),
    }
  )
);
