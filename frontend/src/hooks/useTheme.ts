import { useEffect, useState } from 'react';
import { resolveSystem, useThemeStore, type ResolvedTheme, type ThemeMode } from '@/store/themeStore';

/**
 * 主题 hook（拒绝深色）：
 * - 兼容历史持久化的 'dark'（原「柔和」假深色档）→ 归一为 light；
 * - 派生 resolved：恒 light（'system' 也解析为浅色，系统深色不回退深色 UI）；
 * - 同步 <html data-theme>（恒 light，首帧由 index.html 内联脚本兜底）。
 */
export function useTheme() {
  const storedTheme = useThemeStore((s) => s.theme);
  const setTheme = useThemeStore((s) => s.setTheme);
  // 历史 localStorage 可能残留 'dark'（旧假深色档）→ 归一为 light。
  // 注意：store 的 ThemeMode 类型已不含 'dark'，但持久化的原始字符串仍可能是 'dark'，
  // 因此这里用 String() 转成原始值再比较，避免 TS2367 类型无重叠报错。
  const theme: ThemeMode = String(storedTheme) === 'dark' ? 'light' : storedTheme;

  const [resolved, setResolved] = useState<ResolvedTheme>(() =>
    theme === 'system' ? resolveSystem() : theme
  );

  useEffect(() => {
    const update = () => setResolved(theme === 'system' ? resolveSystem() : theme);
    update();
  }, [theme]);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', resolved);
  }, [resolved]);

  return { theme, resolved, setTheme };
}

export type { ThemeMode };
