import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
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
  it('渲染品牌标题与主题切换器（浅色/跟随系统，无深色）', () => {
    renderHeader();
    expect(screen.getByText('灵犀 · 星河智家 智能客服')).toBeInTheDocument();
    expect(screen.getByText('浅色')).toBeInTheDocument();
    expect(screen.getByText('跟随系统')).toBeInTheDocument();
    // 拒绝深色：不应出现「柔和/深色」选项
    expect(screen.queryByText('柔和')).not.toBeInTheDocument();
  });

  it('切换到浅色后 <html data-theme> 同步为 light', () => {
    renderHeader();
    fireEvent.click(screen.getByText('浅色'));
    expect(document.documentElement.getAttribute('data-theme')).toBe('light');
  });

  it('跟随系统解析为 light（拒绝深色，无深色模式）', () => {
    renderHeader();
    fireEvent.click(screen.getByText('跟随系统'));
    expect(document.documentElement.getAttribute('data-theme')).toBe('light');
  });
});
