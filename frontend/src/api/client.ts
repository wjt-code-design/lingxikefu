import axios from 'axios';
import { API_PREFIX, type ApiError } from '@/contracts/api';
import { useAuthStore } from '@/store/authStore';

/**
 * 统一 axios 实例。
 * - baseURL 来自 VITE_API_BASE（默认 /api/v1，与契约 API_PREFIX 一致）
 * - 请求拦截：自动携带 Bearer token（读 authStore）
 * - 响应拦截：把后端统一错误模型 {code,message,request_id} 归一为 ApiError 后 reject
 */
export const http = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || API_PREFIX,
  timeout: 15_000,
  headers: { 'Content-Type': 'application/json' },
});

http.interceptors.request.use((config) => {
  const token = useAuthStore.getState().token;
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

http.interceptors.response.use(
  (response) => response,
  (error) => {
    const data = error.response?.data;
    const apiError: ApiError = {
      code: typeof data?.code === 'string' ? data.code : 'UNKNOWN',
      message: typeof data?.message === 'string' ? data.message : error.message || '网络错误',
      request_id: typeof data?.request_id === 'string' ? data.request_id : '',
    };
    return Promise.reject(apiError);
  }
);
