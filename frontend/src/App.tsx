import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import AppRoutes from '@/router';
import { ErrorBoundary } from '@/components/common/ErrorBoundary';
import { lightTokens } from '@/theme';

// 全局 QueryClient：REST 服务端状态缓存（FE-02+ 复用）
const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: false },
  },
});

export default function App() {
  // 仅浅色（2026-08-20 取消深色/跟随系统）：AntD 层固定 defaultAlgorithm + lightTokens。
  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: lightTokens,
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
