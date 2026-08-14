import { render } from '@testing-library/react';
import type { ReactNode } from 'react';
import { ConfigProvider } from 'antd';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import AppRoutes from '@/router';
import { useAuthStore } from '@/store/authStore';
import type { MeResp, Role } from '@/contracts/api';

export function createTestQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
}

/** 用内存 router 渲染整棵 AppRoutes（覆盖路由守卫/重定向/布局切换） */
export function renderApp(route = '/', extra?: ReactNode) {
  const client = createTestQueryClient();
  return render(
    <ConfigProvider>
      <QueryClientProvider client={client}>
        <MemoryRouter
          initialEntries={[route]}
          future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
        >
          {extra ?? <AppRoutes />}
        </MemoryRouter>
      </QueryClientProvider>
    </ConfigProvider>
  );
}

/** 模拟以指定角色登录（写入 authStore） */
export function loginAs(role: Role) {
  const me: MeResp = { user_id: 'u-test', role, quota_left: 10 };
  useAuthStore.setState({
    token: 'test-access-token',
    refreshToken: 'test-refresh-token',
    user: me,
    role,
  });
}
