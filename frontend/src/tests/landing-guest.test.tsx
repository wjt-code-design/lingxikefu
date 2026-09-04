/**
 * 批次B（2026-09-04 匿名会话）：落地页免登录体验 + 游客菜单标识。
 * - LandingPage「免登录体验」→ guestLogin()+me() → token 入 store → 跳 /chat；
 * - 签发失败（如 IP 超限 429）→ 内联/消息提示，不写 token、不跳转；
 * - UserMenu：guest 用户显示「游客」标签 + 「注册转正」入口。
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { LandingPage } from '@/pages/LandingPage';
import { UserMenu } from '@/components/common/UserMenu';
import { useAuthStore } from '@/store/authStore';
import type { AuthResp, MeResp } from '@/contracts/api';

vi.mock('@/api/auth', () => ({
  guestLogin: vi.fn(async (): Promise<AuthResp> => ({
    user_id: 'g1',
    access_token: 'ga',
    refresh_token: 'gr',
  })),
  me: vi.fn(async (): Promise<MeResp> => ({
    user_id: 'g1',
    role: 'user',
    quota_left: 10,
    quota_total: 10,
    guest: true,
  })),
  logout: vi.fn(),
}));

import { guestLogin } from '@/api/auth';

function renderLanding() {
  return render(
    <ConfigProvider>
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route path="/" element={<LandingPage />} />
          <Route path="/chat" element={<h1>对话</h1>} />
        </Routes>
      </MemoryRouter>
    </ConfigProvider>
  );
}

describe('LandingPage 免登录体验（批次B）', () => {
  beforeEach(() => {
    useAuthStore.setState({ token: null, refreshToken: null, user: null, role: null });
    vi.clearAllMocks();
  });

  it('点击「免登录体验」→ guestLogin() → token 入 store → 跳 /chat', async () => {
    renderLanding();
    fireEvent.click(screen.getByRole('button', { name: /免登录体验/ }));
    await waitFor(() => expect(guestLogin).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: '对话' })).toBeInTheDocument()
    );
    const s = useAuthStore.getState();
    expect(s.token).toBe('ga');
    expect(s.refreshToken).toBe('gr');
    expect(s.user?.guest).toBe(true);
  });

  it('签发失败（429 IP 超限）→ 不写 token、不跳转，给出提示', async () => {
    vi.mocked(guestLogin).mockRejectedValueOnce({
      code: '429',
      message: '今日匿名体验次数已用完，请注册后继续',
    });
    renderLanding();
    fireEvent.click(screen.getByRole('button', { name: /免登录体验/ }));
    await waitFor(() => expect(guestLogin).toHaveBeenCalledTimes(1));
    expect(useAuthStore.getState().token).toBeNull();
    expect(screen.queryByRole('heading', { name: '对话' })).not.toBeInTheDocument();
    // 提示文案含「注册」引导（message.error 渲染到 body）
    expect(await screen.findByText(/今日匿名体验次数已用完/)).toBeInTheDocument();
  });
});

describe('UserMenu 游客标识（批次B）', () => {
  beforeEach(() => {
    useAuthStore.setState({ token: null, refreshToken: null, user: null, role: null });
    vi.clearAllMocks();
  });

  function renderMenu() {
    return render(
      <ConfigProvider>
        <MemoryRouter>
          <UserMenu />
        </MemoryRouter>
      </ConfigProvider>
    );
  }

  it('guest 用户：显示「游客」标签，菜单含「注册转正」入口', async () => {
    useAuthStore.setState({
      token: 'ga',
      refreshToken: 'gr',
      user: { user_id: 'g1', role: 'user', quota_left: 9, quota_total: 10, guest: true },
      role: 'user',
    });
    renderMenu();
    expect(screen.getByText('游客')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '用户菜单' }));
    expect(await screen.findByText('注册转正')).toBeInTheDocument();
  });

  it('注册用户：仍显示「用户」标签，无「注册转正」入口', async () => {
    useAuthStore.setState({
      token: 'a',
      refreshToken: 'r',
      user: { user_id: 'u1', email: 'a@b.com', role: 'user', quota_left: 5, quota_total: 200 },
      role: 'user',
    });
    renderMenu();
    expect(screen.getByText('用户')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '用户菜单' }));
    await screen.findByText('退出登录');
    expect(screen.queryByText('注册转正')).not.toBeInTheDocument();
  });
});
