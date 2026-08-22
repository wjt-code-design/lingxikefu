import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import { fileURLToPath, URL } from 'node:url';

// Vite 5 + Vitest 2 统一配置；test 字段由 vitest 消费
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  // 本地开发：vite 代理把前端默认相对路径 /api/v1 转发到真实后端 8000（docker compose api）
  // （dev 模式 import.meta.env.* 由 vite 托管，外部 define/插件替换不生效，故用代理最稳）
  // 前端代码保持 VITE_API_BASE 默认 /api/v1 即可；改后端端口时同步此处
  server: {
    port: 5173,
    host: true,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
    // 测试封闭性：jsdom 默认 baseURL 是 localhost:3000，会撞上本机恰好监听 3000 的
    // 任意服务（2026-08-22 实测：撞上后 404/401 经 client 拦截器触发登出，路由测试连环红）。
    // 指向不可路由端口，泄漏的 API 请求统一以网络错误快速失败，不触发任何业务分支。
    environmentOptions: {
      jsdom: {
        url: 'http://localhost:65535/',
      },
    },
    setupFiles: ['./src/tests/setup.ts'],
    css: true,
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    globals: false,
  },
  build: {
    // antd 库固有体积（gzip ~362KB）不告警；业务入口已从 1.06MB 降至 ~403KB
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        // T3 chunk 优化：把 react / antd / icons / 其余 vendor 独立分块
        // （入口 1.06MB → 分块后缓存友好；组件均按需 import 已生效，tree-shaking 正常工作）
        manualChunks: {
          'react-vendor': ['react', 'react-dom', 'react-router-dom'],
          'antd-vendor': ['antd'],
          'antd-icons': ['@ant-design/icons'],
          'vendor': ['axios'],
        },
      },
    },
  },
});
