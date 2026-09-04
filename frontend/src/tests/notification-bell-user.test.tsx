import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { NotificationBell } from '@/components/common/NotificationBell';
import { useAuthStore } from '@/store/authStore';

/**
 * D4 铃铛立项（2026-09-04）：通知按人投递后 user 角色开放通知中心
 * （仅可见定向本人的通知，后端 _visibility_cond 强过滤）。
 * 旧行为：user 角色直接 return null（无通知中心）。
 */

vi.mock('@/api/notifications', () => ({
  getNotifications: vi.fn(() => Promise.resolve({ items: [], total: 0 })),
  getUnreadCount: vi.fn(() => Promise.resolve({ count: 0 })),
  markRead: vi.fn(() => Promise.resolve({ ok: true })),
  markAllRead: vi.fn(() => Promise.resolve({ ok: true })),
  subscribeNotifications: vi.fn(() => () => {}),
}));

function renderBell() {
  return render(
    <ConfigProvider>
      <NotificationBell />
    </ConfigProvider>
  );
}

describe('D4：user 角色通知铃铛', () => {
  beforeEach(() => {
    useAuthStore.setState({
      token: 't',
      refreshToken: 'r',
      user: { user_id: 'u1', role: 'user', quota_left: 10, quota_total: 200 },
      role: 'user',
    });
  });

  it('user 登录态渲染铃铛（旧行为 return null）', () => {
    renderBell();
    expect(screen.getByRole('button', { name: '通知中心' })).toBeInTheDocument();
  });

  it('未登录（role=null）仍不渲染', () => {
    useAuthStore.setState({ token: null, refreshToken: null, user: null, role: null });
    renderBell();
    expect(screen.queryByRole('button', { name: '通知中心' })).not.toBeInTheDocument();
  });

  it('agent 角色保持渲染（回归保护）', () => {
    useAuthStore.setState({
      user: { user_id: 'a1', role: 'agent', quota_left: 10, quota_total: 200 },
      role: 'agent',
    });
    renderBell();
    expect(screen.getByRole('button', { name: '通知中心' })).toBeInTheDocument();
  });
});
