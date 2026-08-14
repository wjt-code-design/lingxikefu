import '@testing-library/jest-dom/vitest';
import '@ant-design/v5-patch-for-react-19';
import { afterEach, beforeEach, vi } from 'vitest';
import { cleanup } from '@testing-library/react';
import { useAuthStore } from '@/store/authStore';
import { useThemeStore } from '@/store/themeStore';

// jsdom 未实现 matchMedia：补 mock（默认 matches=false → light）
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

beforeEach(() => {
  localStorage.clear();
  // 重置持久化 store 的内存态，避免跨用例耦合
  useThemeStore.setState({ theme: 'system' });
  useAuthStore.setState({ token: null, refreshToken: null, user: null, role: null });
});

afterEach(() => {
  cleanup();
  localStorage.clear();
  document.documentElement.removeAttribute('data-theme');
});
