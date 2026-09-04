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
    expect(await screen.findByText('您好，我是灵犀智能客服')).toBeInTheDocument();
  });

  it('普通用户访问 /admin/knowledge → 显示 403 无权限页（不再踢登录）', async () => {
    loginAs('user');
    renderApp('/admin/knowledge');
    // 403 页随角色判断异步渲染（此前同步断言在 Skeleton 加载期失败）
    expect(await screen.findByText('403')).toBeInTheDocument();
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
    // timeout 3s：懒加载 chunk 在高负载机器（docker + dev server 并行）下会超 findBy 默认 1s
    expect(await screen.findByRole('heading', { name: '用户管理' }, { timeout: 3_000 })).toBeInTheDocument();
    renderApp('/admin/stats');
    expect(await screen.findByRole('heading', { name: '运营统计' }, { timeout: 3_000 })).toBeInTheDocument();
  });

  it('已登录访问 /chat → 渲染对话页', async () => {
    loginAs('user');
    renderApp('/chat');
    expect(await screen.findByText('您好，我是灵犀智能客服')).toBeInTheDocument();
  });

  // D2 回归守卫：/tickets 曾在 SideNav/ROUTE_META 注册但无路由 → 点菜单 404
  it('未登录访问 /tickets → 重定向 /login', () => {
    logout();
    renderApp('/tickets');
    expect(screen.getByRole('heading', { name: '登录灵犀客服' })).toBeInTheDocument();
  });

  it('已登录访问 /tickets → 渲染「我的工单」页（不再 404）', async () => {
    loginAs('user');
    renderApp('/tickets');
    // 页面标题在数据请求分支外渲染（jsdom 下请求失败走 QueryErrorState 也不影响）；
    // 404 页文案「页面不存在」不得出现。
    expect(await screen.findByRole('heading', { name: '我的工单' }, { timeout: 3_000 })).toBeInTheDocument();
    expect(screen.queryByText(/页面不存在/)).not.toBeInTheDocument();
  });
});
