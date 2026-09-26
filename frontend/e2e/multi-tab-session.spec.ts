import { expect, test } from '@playwright/test';
import { AUTH_KEY, settle, signIn } from './fixtures';

/**
 * 真·双标签页会话守卫（D18，钉住 D16 的跨标签同步与 D15 的 401 兜底）。
 *
 * 为什么这条必须在 e2e 而不是只在单测里：`/auth/refresh` 是 R-4 轮换制，**旧 jti 的吊销发生在
 * 真 Redis 里**。jsdom 那两层单测（`auth-cross-tab.test.ts` / `client.test.ts`）用的是假 adapter，
 * 只能证明"代码按票的新旧分支走"，证不了"另一页轮换之后这页手里的票真的作废了"。
 * 这里同一个 browser context 开两个 page —— 同 context 共享 localStorage，就是浏览器里的两个标签页。
 *
 * 时序（每步都是真实请求）：
 *   A 登录 → 盘上 rt#1；
 *   B 冷启动进 /chat → 它的 bootstrapAuth 用 rt#1 换出 rt#2 并写盘（rt#1 当场作废）；
 *   A 采纳 rt#2（D16 的 storage 监听）；
 *   A **客户端**点侧栏去 /tickets（不整页重载，所以内存态保留），
 *   把这一跳的 GET /tickets/mine 伪造成 401，逼出一次真实续期。
 *
 * 两个断言分别钉两层：
 *   URL 仍在 /tickets  ⇒ D15（就算拿着废票也不会被踢下线）；
 *   A 只发 **1 次** /auth/refresh ⇒ D16（因为已同步到新票，不必先吃一次失败的续期）。
 * 关掉 D16 后该计数变 2（废票 401 → 重读盘 → 新票 200），断言会红——负向自证已在本机跑过。
 *
 * 顺带补上 §14 记的那块覆盖面：这是全仓第一条**客户端导航**路径的 e2e（其余登录态用例都是整页 goto）。
 */

test.skip(({ isMobile }) => isMobile, '双标签页时序只在桌面视口验证');

test.describe('多标签页会话', () => {
  test.describe.configure({ mode: 'serial' });

  test('另一标签页轮换令牌后，旧标签页客户端导航既不被踢、也不先吃一次失败的续期', async ({ browser }) => {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const pageA = await ctx.newPage();
    await signIn(pageA, 'user');

    const envelopeA = await pageA.evaluate((k) => localStorage.getItem(k), AUTH_KEY);
    const rtOf = (env: string | null) => {
      try {
        return (JSON.parse(String(env)) as { state?: { refreshToken?: string } }).state?.refreshToken ?? null;
      } catch {
        return null;
      }
    };
    expect(rtOf(envelopeA), '登录后盘上应有 refreshToken').toBeTruthy();

    const refreshCallsOf = () => {
      const urls: string[] = [];
      return {
        urls,
        attach: (p: typeof pageA) =>
          p.on('request', (r) => {
            if (r.method() === 'POST' && new URL(r.url()).pathname.endsWith('/auth/refresh')) {
              urls.push(r.url());
            }
          }),
      };
    };
    const aRefresh = refreshCallsOf();
    aRefresh.attach(pageA);
    const aApiCalls: string[] = [];
    pageA.on('request', (r) => {
      const p = new URL(r.url()).pathname;
      if (p.startsWith('/api/')) aApiCalls.push(`${r.method()} ${p}`);
    });
    const routeHits: string[] = [];

    // 第二个标签页：冷启动进 /chat（**故意不选 /tickets**——同一条 GET 若先被 B 拉过，
    // A 后面那一跳会被浏览器 HTTP 缓存直接吃掉，route/请求事件双双为空，401 分支根本进不去。
    // 首版就是这么"红"的：A.url 已到 /tickets、页面也渲染了，但全 context 只有 B 发过 /tickets/mine）。
    const pageB = await ctx.newPage();
    await pageB.goto('/chat', { waitUntil: 'load' });
    await expect(pageB).toHaveURL(/\/chat$/, { timeout: 30_000 });
    await settle(pageB, '/chat');
    // 等 B 轮换出的新票确实落盘（否则 A 的 storage 监听还没东西可采纳，后面那条计数断言就没有意义）
    await expect
      .poll(async () => await pageA.evaluate((k) => localStorage.getItem(k), AUTH_KEY), {
        message: 'B 的续期没写回 localStorage，后面那条计数断言就没有意义',
        timeout: 15_000,
      })
      .not.toBe(envelopeA);

    // 只伪造 A 这一跳的业务请求为 401；/auth/* 一律放行，让真后端做真轮换
    let faked = 0;
    await pageA.route('**/api/v1/**', (route) => {
      const path = new URL(route.request().url()).pathname;
      routeHits.push(path);
      if (path.startsWith('/api/v1/auth/')) return route.fallback();
      if (path.endsWith('/tickets/mine') && faked === 0) {
        faked += 1;
        return route.fulfill({
          status: 401,
          contentType: 'application/json',
          body: JSON.stringify({ code: '401', message: 'token 已过期', request_id: 'e2e-multitab' }),
        });
      }
      return route.fallback();
    });

    // 客户端导航（不整页重载 ⇒ A 的内存态保留，那枚废票才留在手里）
    await pageA.getByRole('menuitem', { name: '我的工单' }).click();
    await expect(pageA).toHaveURL(/\/tickets$/, { timeout: 30_000 });
    await expect
      .poll(() => faked, {
        message: `A 那一跳没发出 GET /tickets/mine\nA 的 API 请求：${JSON.stringify(aApiCalls)}\n路由命中：${JSON.stringify(routeHits)}`,
        timeout: 20_000,
      })
      .toBe(1);
    await settle(pageA, '/tickets');
    expect(
      aRefresh.urls,
      `A 应只用同步到的新票续期一次；实际 ${aRefresh.urls.length} 次（2 次=D16 的跨标签同步没生效）`
    ).toHaveLength(1);

    await ctx.close();
  });
});
