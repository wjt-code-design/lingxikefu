import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export type ThemeMode = 'light' | 'dark' | 'system';
export type ResolvedTheme = 'light' | 'dark';

interface ThemeState {
  theme: ThemeMode;
  setTheme: (theme: ThemeMode) => void;
}

/** 解析「跟随系统」的实际明暗：优先 matchMedia，测试/jsdom 缺省按 light */
function resolveSystem(): ResolvedTheme {
  if (typeof window !== 'undefined' && window.matchMedia) {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  return 'light';
}

/**
 * 主题状态（light/dark/system）。
 * 持久化 key `lingxi-theme` 与 index.html 防闪烁脚本保持一致。
 * 实际 DOM 应用（<html data-theme>）由 hooks/useTheme.ts 负责。
 */
export const useThemeStore = create<ThemeState>()(
  persist(
    (set) => ({
      theme: 'system',
      setTheme: (theme) => set({ theme }),
    }),
    {
      name: 'lingxi-theme',
      // 只持久化 theme，resolved 由 useTheme 动态推导
      partialize: (s) => ({ theme: s.theme }),
    }
  )
);

export { resolveSystem };
