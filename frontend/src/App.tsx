import { ConfigProvider, theme as antdTheme } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import AppRoutes from '@/router';
import { ErrorBoundary } from '@/components/common/ErrorBoundary';
import { useTheme } from '@/hooks/useTheme';
import { themeTokens } from '@/theme'; // v2.1 修订 A：JS token 单一同步对象（与 tokens.css 同值）

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
        // V1 修复（假深色）：恒用 defaultAlgorithm ——「拒绝深色」规范。
        // 之前 dark 套 darkAlgorithm 会让 AntD 组件深黑底 + 深色文字不可读；
        // 柔和档仅切换浅色海盐变体（theme.ts softTokens + CSS 变量），永不出现深色界面。
        algorithm: antdTheme.defaultAlgorithm,
        token: themeTokens(resolved),
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
