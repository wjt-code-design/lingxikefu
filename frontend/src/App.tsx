import { ConfigProvider, theme as antdTheme } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import AppRoutes from '@/router';
import { ErrorBoundary } from '@/components/common/ErrorBoundary';
import { useTheme } from '@/hooks/useTheme';
import { darkTokens, lightTokens } from '@/theme';

// 全局 QueryClient：REST 服务端状态缓存（FE-02+ 复用）
const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: false },
  },
});

export default function App() {
  // 三态主题（light/dark/system）：useTheme 解析 resolved 并同步 <html data-theme>，
  // AntD 层按 resolved 切 algorithm + token（2026-08-20 恢复真深色支持）。
  const { resolved } = useTheme();
  const isDark = resolved === 'dark';

  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        algorithm: isDark ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
        token: isDark ? darkTokens : lightTokens,
      }}
    >
      <QueryClientProvider client={queryClient}>
        <ErrorBoundary>
          <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
            {/* Skip to content — 键盘无障碍 */}
            <a href="#main-content" className="skip-link">
              跳转到主要内容
            </a>
            <AppRoutes />
          </BrowserRouter>
        </ErrorBoundary>
      </QueryClientProvider>
    </ConfigProvider>
  );
}
