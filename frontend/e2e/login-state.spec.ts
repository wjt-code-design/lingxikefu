import { test, expect, type Page } from '@playwright/test';
import { ROUTES, signIn, auditRoute, formatViolations, type E2ERole } from './fixtures';

/**
 * 登录态全路由 axe 扫描（D9 的固化：2026-09-26 那轮手工取证 19 条路由 → 从此进 CI 变成会红的断言）。
 *
 * 为什么值得单独一条 e2e 而不是全靠 vitest：
 * `src/tests/a11y-axe.test.tsx` 跑在 jsdom，布局依赖规则一律判不了——`color-contrast` 必须显式关掉，
 * `target-size`（2.5.8）连几何都没有。真 Chromium 有布局，这两条才量得出，覆盖面也从
 * 「匿名两页 + 一个气泡」扩到三角色全部工作台/管理端。
 *
 * 断言是「零违规」而不是「不超过基线」：本轮 D9/D11/D12 已把对比度修到 0，
 * `target-size` 是这次新打开的规则，若它量出新账，宁可红着暴露也不写进基线。
 *
 * 只跑桌面 project：管理端在 375px 下是抽屉导航，交互路径不同（不是同一条页的另一状态），
 * 移动端由 `login.spec.ts` 的横向滚动断言 + `touch-targets.spec.ts` 的实测尺寸负责。
 */

for (const [role, routes] of Object.entries(ROUTES) as [E2ERole, string[]][]) {
  test.describe(`登录态 axe：${role}`, () => {
    // 跳过必须写在 describe 内部：写在文件顶层时 describe 里的用例不受影响
    // （首版就踩了——mobile project 照跑，三角色 × 两 project = 6 次登录，
    //   第 6 次撞 `auth.py:32` 的 5 次/分钟 IP 限流，卡在 /login 报"登录失败"）。
    test.skip(({ isMobile }) => isMobile, '登录态整页只在桌面视口审；移动视口见 touch-targets.spec.ts');
    test.describe.configure({ mode: 'serial' });

    let page: Page;

    test.beforeAll(async ({ browser }, testInfo) => {
      // describe 顶部那条条件 skip 只作用于用例，**不阻止 beforeAll 执行**（Playwright 的
      // 运行时求值顺序：worker 钩子先于 skip 判定）。所以登录这一步自己也要挡一层，
      // 否则移动 project 会白跑一次真登录——既慢，又占掉 `auth.py:32` 的 5 次/分钟额度。
      if (testInfo.project.use.isMobile) return;
      // 一个 context 登录一次：access token 只在内存（BUG-15），跨路由靠 401 拦截器自续期，
      // 与真人点侧栏导航的路径一致；每路由新开 context 反而会各触发一次 bootstrap 轮换令牌。
      page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
      await signIn(page, role);
    });

    test.afterAll(async () => {
      await page?.close();
    });

    for (const route of routes) {
      test(`${route} 零违规`, async () => {
        const violations = await auditRoute(page, route);
        expect(violations, `${route} 检出 axe 违规：\n${formatViolations(violations)}`).toEqual([]);
      });
    }
  });
}
