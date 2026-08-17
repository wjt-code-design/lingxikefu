import { defineConfig, devices } from '@playwright/test';

// e2e 配置：webServer 自动起 vite dev（CI 无需手动起服；本地已有 5173 则复用）
// 截图目录：test-results/ 与 e2e/screenshots/
export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  retries: 0,
  reporter: [
    ['list'],
    ['html', { outputFolder: 'playwright-report' }],
  ],
  // A2：playwright 负责拉起/等待前端 dev server（首次 optimizeDeps 较慢，60s 超时）
  webServer: {
    command: 'npm run dev',
    url: 'http://127.0.0.1:5173',
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
  use: {
    baseURL: 'http://127.0.0.1:5173',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  outputDir: 'test-results',
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
