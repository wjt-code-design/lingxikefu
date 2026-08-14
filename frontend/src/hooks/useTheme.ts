import { useEffect, useState } from 'react';
import { resolveSystem, useThemeStore, type ResolvedTheme, type ThemeMode } from '@/store/themeStore';

/**
 * 主题 hook：
 * - 派生 resolved（system -> 实际明暗）
 * - 同步 <html data-theme>（瞬时无闪烁，首帧由 index.html 内联脚本兜底）
 * - system 模式下监听系统明暗变化
 */
export function useTheme() {
  const theme = useThemeStore((s) => s.theme);
  const setTheme = useThemeStore((s) => s.setTheme);

  const [resolved, setResolved] = useState<ResolvedTheme>(() =>
    theme === 'system' ? resolveSystem() : theme
  );

  useEffect(() => {
    const update = () => setResolved(theme === 'system' ? resolveSystem() : theme);
    update();
    if (theme === 'system' && typeof window !== 'undefined' && window.matchMedia) {
      const mq = window.matchMedia('(prefers-color-scheme: dark)');
      mq.addEventListener('change', update);
      return () => mq.removeEventListener('change', update);
    }
    return undefined;
  }, [theme]);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', resolved);
  }, [resolved]);

  return { theme, resolved, setTheme };
}

export type { ThemeMode };
