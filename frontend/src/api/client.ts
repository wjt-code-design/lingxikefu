import axios, { type InternalAxiosRequestConfig } from 'axios';
import { API_PREFIX, type ApiError, type RefreshResp } from '@/contracts/api';
import { AUTH_STORAGE_KEY, useAuthStore } from '@/store/authStore';

/**
 * 统一 axios 实例。
 * - baseURL 来自 VITE_API_BASE（默认 /api/v1，与契约 API_PREFIX 一致）
 * - 请求拦截：自动携带 Bearer token（读 authStore）
 * - 响应拦截：401 自动用 refresh_token 续期并重试原请求（并发请求共享一次刷新）
 * - 归一化后端错误模型 {code,message,request_id} → ApiError 后 reject
 *
 * 注意：此处直接 http.post('/auth/refresh')，不 import api/auth，避免与 api/auth 的循环依赖。
 */
export const http = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || API_PREFIX,
  timeout: 15_000,
  headers: { 'Content-Type': 'application/json' },
});

interface RetriableConfig extends InternalAxiosRequestConfig {
  _retry?: boolean;
}

/** 并发 401 共享的刷新 Promise，避免同时发多个 refresh 请求 */
let refreshing: Promise<string | null> | null = null;

function redirectToLogin() {
  if (window.location.pathname !== '/login') {
    window.location.href = '/login';
  }
}

/** 只读 localStorage 里的 refreshToken（另一标签页可能已把它轮换掉，本标签页内存是旧值）。
 *  只读不写：access token 按 BUG-15 仍不落盘。坏 JSON/隐私模式一律当"读不到"，走原登出路径。 */
function readStoredRefreshToken(): string | null {
  try {
    const raw = localStorage.getItem(AUTH_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { state?: { refreshToken?: string | null } };
    return parsed.state?.refreshToken ?? null;
  } catch {
    return null;
  }
}

async function postRefresh(refreshToken: string): Promise<string | null> {
  try {
    const r = await http.post<RefreshResp>('/auth/refresh', { refresh_token: refreshToken });
    // R-4：轮换后同步覆盖存储新 refresh token（旧 token 已吊销）
    useAuthStore.setState({
      token: r.data.access_token,
      ...(r.data.refresh_token ? { refreshToken: r.data.refresh_token } : {}),
    });
    return r.data.access_token;
  } catch {
    return null;
  }
}

/**
 * B2：共享刷新入口（axios 401 拦截器与 SSE fetch 共用）。
 * 并发调用复用同一次 /auth/refresh；刷新成功返回新 access token，
 * 失败时清空会话并返回 null（由调用方决定跳转登录）。
 *
 * 多标签页：R-4 是"轮换即吊销旧 jti"，而 refreshToken 存在跨标签页共享的 localStorage、
 * zustand persist 不做跨标签同步 ⇒ 后启动的标签页续期后，先启动的标签页内存里那张已成废票，
 * 它下次续期必然 401 并被 clear() 踢下线（本机两标签页脚本已复现）。故失败后**重读
 * localStorage 再试一次**；若两边同值（真·会话过期）则给并发赢家一次落盘窗口后有界重试，
 * 最多两次 POST，不做静默轮询。
 */
export function refreshAccessToken(): Promise<string | null> {
  if (!refreshing) {
    refreshing = doRefresh().finally(() => {
      refreshing = null;
    });
  }
  return refreshing;
}

async function doRefresh(): Promise<string | null> {
  const stale = useAuthStore.getState().refreshToken;
  if (!stale) return null;

  const first = await postRefresh(stale);
  if (first) return first;

  let next = readStoredRefreshToken();
  if (!next || next === stale) {
    await new Promise((r) => setTimeout(r, 300));
    next = readStoredRefreshToken();
  }
  if (!next || next === stale) {
    useAuthStore.getState().clear();
    return null;
  }

  const second = await postRefresh(next);
  if (!second) useAuthStore.getState().clear();
  return second;
}

function toApiError(error: unknown): ApiError {
  const err = error as {
    response?: { status?: number; data?: Record<string, unknown> };
    message?: string;
  };
  const data = err.response?.data;
  const status = err.response?.status;
  const message =
    (typeof data?.message === 'string' && data.message) ||
    (typeof data?.detail === 'string' && data.detail) || // H1 兜底：旧端点仍返回 {detail}
    err.message ||
    '网络错误';
  return {
    code: typeof data?.code === 'string' ? data.code : String(status ?? 'UNKNOWN'),
    message,
    request_id: typeof data?.request_id === 'string' ? data.request_id : '',
  };
}

http.interceptors.request.use((config) => {
  const token = useAuthStore.getState().token;
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

http.interceptors.response.use(
  (response) => response,
  async (error: unknown) => {
    const original = (error as { config?: RetriableConfig }).config;
    const status = (error as { response?: { status?: number } }).response?.status;

    // 仅处理 401；排除 refresh 自身请求，防止无限循环。
    // 精确匹配（外部审查 C3）：includes 会误伤未来出现的 /auth/refresh-xxx 等端点
    if (
      status === 401 &&
      original &&
      !original._retry &&
      original.url !== '/auth/refresh'
    ) {
      original._retry = true;
      const { refreshToken, clear } = useAuthStore.getState();

      if (!refreshToken) {
        clear();
        redirectToLogin();
        return Promise.reject(toApiError(error));
      }

      // B2：复用共享刷新（与 SSE fetch 同源），失败统一清会话跳登录
      const newToken = await refreshAccessToken();
      if (!newToken) {
        redirectToLogin();
        return Promise.reject(toApiError(error));
      }
      // 重试原请求：请求拦截器会自动带上更新后的 token
      return http(original);
    }

    return Promise.reject(toApiError(error));
  }
);
