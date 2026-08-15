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
  // 本地开发：vite 代理把前端默认相对路径 /api/v1 转发到真实后端 8003
  // （dev 模式 import.meta.env.* 由 vite 托管，外部 define/插件替换不生效，故用代理最稳）
  // 前端代码保持 VITE_API_BASE 默认 /api/v1 即可；改后端端口时同步此处
  server: {
    port: 5173,
    host: true,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://localhost:8003',
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/tests/setup.ts'],
    css: true,
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    globals: false,
  },
});
