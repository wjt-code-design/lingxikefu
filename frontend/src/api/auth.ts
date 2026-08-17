import { http } from './client';
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
