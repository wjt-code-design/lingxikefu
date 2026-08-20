import { beforeEach, describe, expect, it } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { MemoryRouter } from 'react-router-dom';
import AppHeader from '@/components/common/AppHeader';
import { useThemeStore } from '@/store/themeStore';

beforeEach(() => {
  // 隔离主题持久化残留（zustand persist 跨测试污染 → 初始档不确定）
  localStorage.removeItem('lingxi-theme');
  useThemeStore.setState({ theme: 'light' });
});

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

  it('渲染主题切换器（三态：浅色 → 深色 → 跟随系统 循环）', () => {
    renderHeader();
    // 初始档（默认 light）
    const btn = screen.getByRole('button', { name: /主题：浅色/ });
    expect(btn).toBeInTheDocument();
    // 点击循环：light → dark → system → light
    fireEvent.click(btn);
    expect(screen.getByRole('button', { name: /主题：深色/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /主题：深色/ }));
    expect(screen.getByRole('button', { name: /主题：跟随系统/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /主题：跟随系统/ }));
    expect(screen.getByRole('button', { name: /主题：浅色/ })).toBeInTheDocument();
  });
});
