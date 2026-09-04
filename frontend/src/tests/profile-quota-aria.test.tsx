import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ConfigProvider } from 'antd';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ProfilePage } from '@/pages/ProfilePage';
import type { MeResp, Session } from '@/contracts/api';

/**
 * D5 回归（交接文档 §四-P2，2026-09-04）：
 * /profile 额度进度条 percent 语义是「剩余」（strokeColor 高%=绿佐证），
 * 但 aria-label 曾写「已使用 {percent}%」，与文案「已使用 total-left / total」
 * 方向相反 → 读屏用户听到的进度与视觉相反。修复=aria 改「剩余」。
 */

vi.mock('@/api/auth', () => ({
  me: vi.fn((): Promise<MeResp> =>
    Promise.resolve({ user_id: 'u1', role: 'user', quota_left: 5, quota_total: 200 })
  ),
}));
vi.mock('@/api/sessions', () => ({
  listSessions: vi.fn((): Promise<{ items: Session[]; total: number }> =>
    Promise.resolve({ items: [], total: 0 })
  ),
}));

function withProviders(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <ConfigProvider>
      <QueryClientProvider client={client}>
        <MemoryRouter>{ui}</MemoryRouter>
      </QueryClientProvider>
    </ConfigProvider>
  );
}

describe('D5：/profile 额度进度条 aria 方向', () => {
  it('进度条 percent=剩余比例（5/200≈3%），aria-label 必须是「剩余」语义而非「已使用」', async () => {
    withProviders(<ProfilePage />);
    const bar = await screen.findByRole('progressbar');
    // 文案「已使用 195 / 200」已正确；aria 若沿用「已使用 3%」则与文案方向相反
    expect(bar).toHaveAttribute('aria-label', expect.stringContaining('剩余'));
    expect(bar).toHaveAttribute('aria-label', expect.not.stringContaining('已使用'));
  });
});
