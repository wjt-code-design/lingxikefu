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
    expect(screen.getByText('灵犀 · 星河智家 智能客服')).toBeInTheDocument();
  });

  it('不再渲染主题切换器（项目恒浅色）', () => {
    renderHeader();
    expect(screen.queryByText('浅色')).not.toBeInTheDocument();
    expect(screen.queryByText('跟随系统')).not.toBeInTheDocument();
    expect(screen.queryByText('深色')).not.toBeInTheDocument();
  });
});
