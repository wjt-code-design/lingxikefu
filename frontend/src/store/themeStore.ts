import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export type ThemeMode = 'light' | 'dark' | 'system';
export type ResolvedTheme = 'light' | 'dark';

interface ThemeState {
  theme: ThemeMode;
  setTheme: (theme: ThemeMode) => void;
}

/** 解析 system 档：跟随 OS 深浅偏好（matchMedia；SSR/无 matchMedia 环境回退浅色）。 */
export function resolveSystem(): ResolvedTheme {
  if (typeof window === 'undefined' || !window.matchMedia) return 'light';
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

/**
 * 主题状态（light / dark / system 三态，2026-08-20 恢复真深色支持）。
 * 历史：曾因「假深色档」（点 dark 几乎不变色）删除该档；本轮补齐
 * tokens.css [data-theme='dark'] + AntD darkAlgorithm 后恢复。
 * 持久化 key `lingxi-theme`；实际 DOM 应用（<html data-theme>）由 hooks/useTheme.ts 负责，
 * 首帧由 index.html 内联脚本兜底（防 FOUC）。
 */
export const useThemeStore = create<ThemeState>()(
  persist(
    (set) => ({
      theme: 'light',
      setTheme: (theme) => set({ theme }),
    }),
    {
      name: 'lingxi-theme',
      partialize: (s) => ({ theme: s.theme }),
    }
  )
);
