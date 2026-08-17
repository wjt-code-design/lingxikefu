import { http } from './client';
import { useAuthStore } from '@/store/authStore';
import type {
  AuthResp,
  LoginReq,
  MeResp,
  OkResp,
  RefreshReq,
  RefreshResp,
  RegisterReq,
} from '@/contracts/api';

/** POST /auth/login → AuthResp */
export async function login(req: LoginReq): Promise<AuthResp> {
  const { data } = await http.post<AuthResp>('/auth/login', req);
  return data;
}

/** POST /auth/register → AuthResp */
export async function register(req: RegisterReq): Promise<AuthResp> {
  const { data } = await http.post<AuthResp>('/auth/register', req);
  return data;
}

/** POST /auth/refresh → RefreshResp */
export async function refresh(req: RefreshReq): Promise<RefreshResp> {
  const { data } = await http.post<RefreshResp>('/auth/refresh', req);
  return data;
}

/** POST /auth/logout → OkResp（吊销 token，使旧 token 失效） */
export async function logout(refreshToken: string): Promise<OkResp> {
  const { data } = await http.post<OkResp>('/auth/logout', { refresh_token: refreshToken });
  return data;
}

/** GET /auth/me → MeResp（依赖请求拦截器携带 Bearer token） */
export async function me(): Promise<MeResp> {
  const { data } = await http.get<MeResp>('/auth/me');
  return data;
}

/**
 * BUG-15：启动时静默续期。
 * access token 仅存内存、刷新页面即丢失；若本地持久化了 refreshToken 而无 token，
 * 在应用首屏渲染前调用本函数续期恢复会话（成功则写回 token，失败则清空登录态）。
 * 由 main.tsx 在 createRoot 前 await 调用，避免路由守卫把已登录用户闪跳到 /login。
 */
export async function bootstrapAuth(): Promise<void> {
  const { token, refreshToken, setAuth } = useAuthStore.getState();
  if (token || !refreshToken) return; // 已有 token 或未登录，无需续期
  try {
    const resp = await refresh({ refresh_token: refreshToken });
    const meResp = await me();
    setAuth(
      {
        user_id: meResp.user_id,
        access_token: resp.access_token,
        refresh_token: resp.refresh_token,
      },
      meResp
    );
  } catch {
    useAuthStore.getState().clear();
  }
}
