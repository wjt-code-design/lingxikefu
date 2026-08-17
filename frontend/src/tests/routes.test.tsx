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
    expect(screen.getByRole('heading', { name: '登录灵犀客服' })).toBeInTheDocument();
  });

  it('未登录访问 /chat → 重定向 /login', () => {
    logout();
    renderApp('/chat');
    expect(screen.getByRole('heading', { name: '登录灵犀客服' })).toBeInTheDocument();
  });

  it('匿名可访问 /widget（渲染挂件页）', async () => {
    logout();
    renderApp('/widget');
    // 挂件页首屏为对话欢迎语（Empty 描述），无独立 heading
    expect(await screen.findByText('您好，我是星河智家智能客服')).toBeInTheDocument();
  });

  it('普通用户访问 /admin/knowledge → 显示 403 无权限页（不再踢登录）', () => {
    loginAs('user');
    renderApp('/admin/knowledge');
    expect(screen.getByText('403')).toBeInTheDocument();
  });

  it('admin 访问 /admin/knowledge → 渲染知识库管理页', async () => {
    loginAs('admin');
    renderApp('/admin/knowledge');
    // 页面为懒加载模块，等待异步渲染完成
    expect(await screen.findByRole('heading', { name: '知识库管理' })).toBeInTheDocument();
  });

  it('admin 访问 /admin/users 与 /admin/stats → 渲染对应空壳', async () => {
    loginAs('admin');
    renderApp('/admin/users');
    expect(await screen.findByRole('heading', { name: '用户管理' })).toBeInTheDocument();
    renderApp('/admin/stats');
    expect(await screen.findByRole('heading', { name: '运营统计' })).toBeInTheDocument();
  });

  it('已登录访问 /chat → 渲染对话页', async () => {
    loginAs('user');
    renderApp('/chat');
    expect(await screen.findByText('您好，我是星河智家智能客服')).toBeInTheDocument();
  });
});
