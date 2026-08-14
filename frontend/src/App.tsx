import { ConfigProvider, theme as antdTheme } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import AppRoutes from '@/router';
import { ErrorBoundary } from '@/components/common/ErrorBoundary';
import { useTheme } from '@/hooks/useTheme';

// 全局 QueryClient：REST 服务端状态缓存（FE-02+ 复用）
const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: false },
  },
});

export default function App() {
  const { resolved } = useTheme();

  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        // 主题算法随 themeStore 切换：dark <-> default，与 <html data-theme> 同步
        algorithm: resolved === 'dark' ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
        token: {
          colorPrimary: '#2F6BFF',
          borderRadius: 8,
        },
      }}
    >
      <QueryClientProvider client={queryClient}>
        <ErrorBoundary>
          <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
            <AppRoutes />
          </BrowserRouter>
        </ErrorBoundary>
      </QueryClientProvider>
    </ConfigProvider>
  );
}
