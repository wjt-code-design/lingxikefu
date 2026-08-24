import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { SideNav } from './SideNav';
import { useAuthStore } from '@/store/authStore';
import * as adminApi from '@/api/admin';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: false },
  },
});

function renderNav(initialEntries = ['/chat']) {
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <QueryClientProvider client={queryClient}>
        <ConfigProvider>
          <SideNav />
        </ConfigProvider>
      </QueryClientProvider>
    </MemoryRouter>
  );
}

describe('SideNav 菜单渲染', () => {
  beforeEach(() => {
    queryClient.clear();
    useAuthStore.setState({ token: null, refreshToken: null, user: null, role: null });
  });

  it('admin 返回完整菜单', async () => {
    useAuthStore.setState({ role: 'admin' });
    vi.spyOn(adminApi, 'getMyPermissions').mockResolvedValue({
      roles: [
        {
          role: 'admin',
          name: '管理员',
          menus: ['/chat', '/admin/dashboard', '/admin/knowledge'],
          scope: 'all',
        },
      ],
    });

    renderNav();
    await waitFor(() => {
      expect(screen.getByText('智能对话')).toBeInTheDocument();
    });
    expect(screen.getByText('运营后台')).toBeInTheDocument();
  });

  it('permissions 接口返回空 roles 时按 role 兜底', async () => {
    useAuthStore.setState({ role: 'admin' });
    vi.spyOn(adminApi, 'getMyPermissions').mockResolvedValue({ roles: [] });

    renderNav();
    await waitFor(() => {
      expect(adminApi.getMyPermissions).toHaveBeenCalled();
    });
    // BUG-16：本地兜底菜单应仍显示
    expect(screen.getByText('智能对话')).toBeInTheDocument();
    expect(screen.getByText('运营后台')).toBeInTheDocument();
  });

  it('permissions 接口失败时按 role 兜底', async () => {
    useAuthStore.setState({ role: 'admin' });
    vi.spyOn(adminApi, 'getMyPermissions').mockRejectedValue(new Error('network'));

    renderNav();
    await waitFor(() => {
      expect(screen.getByText('智能对话')).toBeInTheDocument();
    });
  });
});
