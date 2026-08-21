import '@testing-library/jest-dom/vitest';
import '@ant-design/v5-patch-for-react-19';
import { afterEach, beforeEach, vi } from 'vitest';
import { cleanup } from '@testing-library/react';
import { useAuthStore } from '@/store/authStore';

// jsdom 未实现 matchMedia：补 mock（antd responsiveObserver 依赖；默认 matches=false → 浅色/非深色）
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
});

// 固定浅色主题（2026-08-20 取消深色/跟随系统）
document.documentElement.setAttribute('data-theme', 'light');

beforeEach(() => {
  localStorage.clear();
  useAuthStore.setState({ token: null, refreshToken: null, user: null, role: null });
});

afterEach(() => {
  cleanup();
  localStorage.clear();
});
