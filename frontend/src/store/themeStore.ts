import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export type ThemeMode = 'light' | 'system';
export type ResolvedTheme = 'light' | 'dark';

interface ThemeState {
  theme: ThemeMode;
  setTheme: (theme: ThemeMode) => void;
}

/** 拒绝深色：无论系统明暗，恒解析为浅色（无深色模式）。 */
function resolveSystem(): ResolvedTheme {
  return 'light';
}

/**
 * 主题状态（light/system）。原「dark/柔和」档已删除（假深色 bug：点它几乎不变色）。
 * 持久化 key `lingxi-theme`；历史持久化的 'dark' 值由 useTheme 归一为 light。
 * 实际 DOM 应用（<html data-theme>）由 hooks/useTheme.ts 负责。
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

export { resolveSystem };
