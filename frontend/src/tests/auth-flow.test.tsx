import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { LoginPage } from '@/pages/LoginPage';
import { RegisterPage } from '@/pages/RegisterPage';
import { useAuthStore } from '@/store/authStore';
import type { AuthResp, MeResp } from '@/contracts/api';

vi.mock('@/api/auth', () => ({
  login: vi.fn(async (): Promise<AuthResp> => ({
    user_id: 'u1',
    access_token: 'a',
    refresh_token: 'r',
  })),
  me: vi.fn(async (): Promise<MeResp> => ({
    user_id: 'u1',
    role: 'user',
    quota_left: 5,
    quota_total: 200,
  })),
  register: vi.fn(),
  refresh: vi.fn(),
}));

import { login, me, register } from '@/api/auth';

function renderLogin() {
  return render(
    <ConfigProvider>
      <MemoryRouter initialEntries={['/login']}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/chat" element={<h1>对话</h1>} />
        </Routes>
      </MemoryRouter>
    </ConfigProvider>
  );
}

describe('LoginPage 提交闭环（FE-02）', () => {
  beforeEach(() => {
    useAuthStore.setState({ token: null, refreshToken: null, user: null, role: null });
    vi.clearAllMocks();
  });

  it('提交 → login()+me() → token 入 store → 跳转 /chat', async () => {
    renderLogin();
    fireEvent.change(screen.getByPlaceholderText('邮箱或手机号'), {
      target: { value: 'a@b.com' },
    });
    fireEvent.change(screen.getByPlaceholderText('密码'), {
      target: { value: 'secret123' },
    });
    fireEvent.click(screen.getByRole('button', { name: /登\s*录/ }));

    await waitFor(() =>
      expect(login).toHaveBeenCalledWith({ account: 'a@b.com', password: 'secret123' })
    );
    expect(me).toHaveBeenCalledTimes(1);
    expect(useAuthStore.getState().token).toBe('a');
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: '对话' })).toBeInTheDocument()
    );
  });

  it('登录失败 → 不写 token', async () => {
    vi.mocked(login).mockRejectedValueOnce({ code: 'AUTH', message: '账号或密码错误' });
    renderLogin();
    fireEvent.change(screen.getByPlaceholderText('邮箱或手机号'), {
      target: { value: 'a@b.com' },
    });
    fireEvent.change(screen.getByPlaceholderText('密码'), {
      target: { value: 'wrong' },
    });
    fireEvent.click(screen.getByRole('button', { name: /登\s*录/ }));

    await waitFor(() => expect(useAuthStore.getState().token).toBeNull());
    expect(screen.queryByRole('heading', { name: '对话' })).not.toBeInTheDocument();
  });
});

describe('RegisterPage 注册失败反馈（UI 审查 2026-08-31 高1）', () => {
  beforeEach(() => {
    useAuthStore.setState({ token: null, refreshToken: null, user: null, role: null });
    vi.clearAllMocks();
  });

  it('注册失败 → 持久内联错误提示（role=alert，不只依赖 toast）', async () => {
    vi.mocked(register).mockRejectedValueOnce({ code: '409', message: '邮箱已被注册' });
    render(
      <ConfigProvider>
        <MemoryRouter initialEntries={['/register']}>
          <Routes>
            <Route path="/register" element={<RegisterPage />} />
            <Route path="/chat" element={<h1>对话</h1>} />
          </Routes>
        </MemoryRouter>
      </ConfigProvider>
    );
    fireEvent.change(screen.getByPlaceholderText('you@example.com'), {
      target: { value: 'dup@b.com' },
    });
    fireEvent.change(screen.getByPlaceholderText('至少 8 位，含字母和数字'), {
      target: { value: 'pass1234' },
    });
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: /注册并登录/ }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('邮箱已被注册');
    expect(screen.queryByRole('heading', { name: '对话' })).not.toBeInTheDocument();
  });

  it('注册失败后重新提交 → 旧错误清除', async () => {
    vi.mocked(register).mockRejectedValueOnce({ code: '409', message: '邮箱已被注册' });
    render(
      <ConfigProvider>
        <MemoryRouter initialEntries={['/register']}>
          <Routes>
            <Route path="/register" element={<RegisterPage />} />
            <Route path="/chat" element={<h1>对话</h1>} />
          </Routes>
        </MemoryRouter>
      </ConfigProvider>
    );
    fireEvent.change(screen.getByPlaceholderText('you@example.com'), {
      target: { value: 'dup@b.com' },
    });
    fireEvent.change(screen.getByPlaceholderText('至少 8 位，含字母和数字'), {
      target: { value: 'pass1234' },
    });
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: /注册并登录/ }));
    await screen.findByRole('alert');

    vi.mocked(register).mockRejectedValueOnce({ code: '400', message: '密码过弱' });
    fireEvent.click(screen.getByRole('button', { name: /注册并登录/ }));
    // toast 与内联 Alert 会同时渲染文案 → 用 role=alert 唯一定位
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('密码过弱');
    expect(alert).not.toHaveTextContent('邮箱已被注册');
  });
});
