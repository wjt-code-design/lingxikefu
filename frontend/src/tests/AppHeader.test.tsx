import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { MemoryRouter } from 'react-router-dom';
import AppHeader from '@/components/common/AppHeader';

function renderHeader() {
  return render(
    <MemoryRouter>
      <ConfigProvider>
        <AppHeader />
      </ConfigProvider>
    </MemoryRouter>
  );
}

describe('AppHeader 通用组件', () => {
  it('渲染品牌标题', () => {
    renderHeader();
    expect(screen.getByText('灵犀 · 智能客服')).toBeInTheDocument();
  });

  // 2026-08-20：取消深色/跟随系统切换，主题固定浅色 → 不再渲染主题切换器
  it('不再渲染主题切换器', () => {
    renderHeader();
    expect(screen.queryByRole('button', { name: /主题/ })).not.toBeInTheDocument();
  });
});
