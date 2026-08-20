import { useEffect, useState } from 'react';
import { resolveSystem, useThemeStore, type ResolvedTheme, type ThemeMode } from '@/store/themeStore';

/**
 * 主题 hook（三态：light / dark / system）：
 * - resolved 为最终生效主题（system → matchMedia 实时解析，OS 切换深浅即时跟随）；
 * - 同步 <html data-theme>（CSS token 层）；AntD 层由 App.tsx 按 resolved 切 algorithm/token；
 * - 首帧由 index.html 内联脚本兜底（读 localStorage + matchMedia，防 FOUC）。
 */
export function useTheme() {
  const storedTheme = useThemeStore((s) => s.theme);
  const setTheme = useThemeStore((s) => s.setTheme);
  // zustand persist 恢复前（首帧）值可能是默认 'light'，与内联脚本的兜底结果一致方向，可接受
  const theme: ThemeMode = storedTheme;

  const [resolved, setResolved] = useState<ResolvedTheme>(() =>
    theme === 'system' ? resolveSystem() : (theme as ResolvedTheme)
  );

  useEffect(() => {
    const apply = () => setResolved(theme === 'system' ? resolveSystem() : (theme as ResolvedTheme));
    apply();
    // system 档：监听 OS 深浅偏好变化，实时跟随
    if (theme === 'system' && window.matchMedia) {
      const mq = window.matchMedia('(prefers-color-scheme: dark)');
      mq.addEventListener('change', apply);
      return () => mq.removeEventListener('change', apply);
    }
  }, [theme]);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', resolved);
  }, [resolved]);

  return { theme, resolved, setTheme };
}

export type { ThemeMode };
