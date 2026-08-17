import axios, { type InternalAxiosRequestConfig } from 'axios';
import { API_PREFIX, type ApiError, type RefreshResp } from '@/contracts/api';
import { useAuthStore } from '@/store/authStore';

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

function redirectToLogin() {
  if (window.location.pathname !== '/login') {
    window.location.href = '/login';
  }
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

    // 仅处理 401；排除 refresh 自身请求，防止无限循环
    if (
      status === 401 &&
      original &&
      !original._retry &&
      !original.url?.includes('/auth/refresh')
    ) {
      original._retry = true;
      const { refreshToken, clear } = useAuthStore.getState();

      if (!refreshToken) {
        clear();
        redirectToLogin();
        return Promise.reject(toApiError(error));
      }

      try {
        if (!refreshing) {
          refreshing = http
            .post<RefreshResp>('/auth/refresh', { refresh_token: refreshToken })
            .then((r) => {
              // R-4：轮换后同步覆盖存储新 refresh token（旧 token 已吊销）
              useAuthStore.setState({
                token: r.data.access_token,
                ...(r.data.refresh_token ? { refreshToken: r.data.refresh_token } : {}),
              });
              return r.data.access_token;
            });
        }
        await refreshing;
        // 重试原请求：请求拦截器会自动带上更新后的 token
        return http(original);
      } catch {
        clear();
        redirectToLogin();
        return Promise.reject(toApiError(error));
      } finally {
        refreshing = null;
      }
    }

    return Promise.reject(toApiError(error));
  }
);
