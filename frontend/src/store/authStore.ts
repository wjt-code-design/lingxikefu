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
 * zustand persist 在 localStorage 的键（单一真源）。
 * `api/client.ts` 的多标签页兜底要直接读盘（内存里可能是已轮换掉的旧票），
 * 测试也要按同一形状构造/校验样本——三处各写一遍字面量迟早漂一个。
 */
export const AUTH_STORAGE_KEY = 'lingxi-auth';

/**
 * 认证状态，持久化到 localStorage（key: AUTH_STORAGE_KEY）。
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
      name: AUTH_STORAGE_KEY,
      // BUG-15：token（access token）不落 localStorage；refreshToken/user/role 持久化以支持静默续期
      partialize: (s) => ({
        refreshToken: s.refreshToken,
        user: s.user,
        role: s.role,
      }),
    }
  )
);

/**
 * 跨标签页同步 refreshToken（D16）。
 *
 * 为什么需要：`/auth/refresh` 是 R-4 轮换制（旧 jti 立即进 Redis 吊销表），而 access token 按 BUG-15
 * 只存内存。同浏览器开两个标签页时，后启动那个 bootstrapAuth 会轮换出新票写进**共享的** localStorage，
 * 先启动那个内存里仍是已吊销的旧票。`client.ts` 的 401 兜底（D15）已经保证它不会被踢下线，
 * 但那条路径要先吃一个 401 才自愈——本函数把它变成"另一页写盘时立刻采纳"，用户不再经过失败请求。
 *
 * 三条边界：
 * - `storage` 事件**只在其他标签页写入时触发**，写入方自己收不到 ⇒ 不存在自我覆盖；
 *   采纳后本标签页 persist 会再写一次同值，其他标签页因下面的相等判断不再回写（无乒乓）。
 * - 只同步 refreshToken，**绝不同步 token**：access 不落盘是 BUG-15 的安全取舍，同步它等于把它写进 localStorage。
 * - 盘上 refreshToken 变空 = 另一页登出 ⇒ 本页一并清态（单会话语义）。
 *
 * @returns 卸载监听（测试/热更新用）
 */
export function startCrossTabAuthSync(): () => void {
  if (typeof window === 'undefined') return () => {};
  const onStorage = (e: StorageEvent) => {
    if (e.key !== AUTH_STORAGE_KEY) return;
    if (e.newValue === null) {
      useAuthStore.getState().clear(); // removeItem（登出/清理）
      return;
    }
    let stored: { state?: { refreshToken?: string | null } } | null = null;
    try {
      stored = JSON.parse(e.newValue);
    } catch {
      return; // 坏 JSON：不动内存，留给 401 兜底路径处理
    }
    const rt = stored?.state?.refreshToken;
    if (rt === undefined) return;
    if (!rt) {
      useAuthStore.getState().clear();
      return;
    }
    const cur = useAuthStore.getState();
    if (cur.refreshToken !== rt) useAuthStore.setState({ refreshToken: rt });
  };
  window.addEventListener('storage', onStorage);
  return () => window.removeEventListener('storage', onStorage);
}
