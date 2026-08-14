import { describe, expect, it } from 'vitest';
import { screen } from '@testing-library/react';
import { renderApp, loginAs } from './test-utils';
import { useAuthStore } from '@/store/authStore';

function logout() {
  useAuthStore.setState({ token: null, refreshToken: null, user: null, role: null });
}

describe('路由可达与 RequireAuth 守卫', () => {
  it('未登录访问 /admin/knowledge → 重定向 /login', () => {
    logout();
    renderApp('/admin/knowledge');
    expect(screen.getByRole('heading', { name: '登录' })).toBeInTheDocument();
  });

  it('未登录访问 /chat → 重定向 /login', () => {
    logout();
    renderApp('/chat');
    expect(screen.getByRole('heading', { name: '登录' })).toBeInTheDocument();
  });

  it('匿名可访问 /widget（渲染挂件页）', () => {
    logout();
    renderApp('/widget');
    expect(screen.getByRole('heading', { name: '对话挂件' })).toBeInTheDocument();
  });

  it('普通用户访问 /admin/knowledge → 重定向 /login', () => {
    loginAs('user');
    renderApp('/admin/knowledge');
    expect(screen.getByRole('heading', { name: '登录' })).toBeInTheDocument();
  });

  it('admin 访问 /admin/knowledge → 渲染知识库管理页', () => {
    loginAs('admin');
    renderApp('/admin/knowledge');
    expect(screen.getByRole('heading', { name: '知识库管理' })).toBeInTheDocument();
  });

  it('admin 访问 /admin/users 与 /admin/stats → 渲染对应空壳', () => {
    loginAs('admin');
    renderApp('/admin/users');
    expect(screen.getByRole('heading', { name: '用户管理' })).toBeInTheDocument();
    renderApp('/admin/stats');
    expect(screen.getByRole('heading', { name: '运营统计' })).toBeInTheDocument();
  });

  it('已登录访问 /chat → 渲染对话页', () => {
    loginAs('user');
    renderApp('/chat');
    expect(screen.getByRole('heading', { name: '对话' })).toBeInTheDocument();
  });
});
