import { expect, type Page } from '@playwright/test';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

/**
 * e2e 登录态与 axe 取证公共件（2026-09-26 那轮一次性探针脚本的固化版）。
 *
 * 与原探针的差别（为什么现在能进 CI）：
 * - 原脚本靠 `scripts/lingxi_mint_tokens.py` 手工签 refreshToken 再 `addInitScript` 注入，
 *   且**每条路由都要一枚未用过的令牌**——`/auth/refresh` 是轮换制（旧 jti 立即进 Redis 吊销表），
 *   同令牌复用第二个页面必然 401 被踢回 /login，axe 于是审的是登录页（首轮就踩，19/19 假绿）。
 * - 现在改走 UI 真登录：`backend/scripts/seed_e2e_accounts.py` 灌三个角色号，
 *   一个 context 登录一次、后续路由靠内存 access token + 拦截器自续期，与真实用户路径一致。
 *
 * 口令与种子步骤必须同值：CI 的 `E2E_PASSWORD` 就取这里的常量（一次性库里的号，出库即无意义；
 * 写成常量而不是环境变量，是为了让 `npx playwright test` 本地零配置可跑）。
 */

export type E2ERole = 'user' | 'agent' | 'admin';

export const E2E_PASSWORD = 'e2e-not-a-secret-2026';

export const ACCOUNTS: Record<E2ERole, string> = {
  user: 'e2e-user@lingxi.test',
  agent: 'e2e-agent@lingxi.test',
  admin: 'e2e-admin@lingxi.test',
};

/** 登录后落地页，与 `router.tsx` 的 `useHome()` 分流一致。 */
export const HOME: Record<E2ERole, string> = {
  user: '/chat',
  agent: '/agent/dashboard',
  admin: '/admin/dashboard',
};

/**
 * 三个角色的登录态路由全集（19 条 = user 4 + agent 6 + admin 9）。
 * `/admin/eval` 有意不在列：那一页是评测结果看板，空库下长期停在加载态，属 D9 之外的另一件事。
 */
export const ROUTES: Record<E2ERole, string[]> = {
  user: ['/chat', '/tickets', '/feedback', '/faq'],
  agent: [
    '/agent/dashboard',
    '/agent/sessions',
    '/agent/customers',
    '/agent/tickets',
    '/agent/kb-search',
    '/chat',
  ],
  admin: [
    '/admin/dashboard',
    '/admin/knowledge',
    '/admin/users',
    '/admin/roles',
    '/admin/stats',
    '/admin/feedback',
    '/admin/sessions',
    '/admin/settings',
    '/admin/logs',
  ],
};

/** UI 真登录：填账号密码 → 提交 → 落到该角色的首页。 */
export async function signIn(page: Page, role: E2ERole): Promise<void> {
  await page.goto('/login', { waitUntil: 'load' });
  await page.getByPlaceholder('邮箱或手机号').fill(ACCOUNTS[role]);
  await page.getByPlaceholder('密码').fill(E2E_PASSWORD);
  // antd Button 对「两个汉字」的文案自动插空格（实际 accessible name 是「登 录」），故用 \s* 匹配
  await page.getByRole('button', { name: /登\s*录/ }).click();
  // 停在 /login 的两种常见原因：种子号没灌（`backend/scripts/seed_e2e_accounts.py`），
  // 或撞了登录限流（`auth.py:32` 每 IP 每分钟 5 次 → 429，页面弹"登录过于频繁"）。
  await expect(
    page,
    `登录没到 ${HOME[role]}：查 e2e 种子账号是否灌入、或 5 次/分钟限流是否被触发`
  ).toHaveURL(new RegExp(`${HOME[role]}$`), { timeout: 30_000 });
}

export type Violation = {
  id: string;
  impact: string;
  nodes: number;
  at: string;
  why: string;
};

// axe 包自带 min.js，用 addScriptTag({path}) 直接注入，省掉 @axe-core/playwright 这层依赖
const AXE_SRC = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  '..',
  'node_modules',
  'axe-core',
  'axe.min.js'
);

declare global {
  interface Window {
    axe: {
      run: (
        ctx: Document | Element,
        opts: Record<string, unknown>
      ) => Promise<{
        violations: {
          id: string;
          impact?: string;
          nodes: { target?: string[]; message?: string }[];
        }[];
      }>;
    };
  }
}

/**
 * 停进"稳定态"再取证。两段等待，顺序不能反：
 *
 * ① 先等**正文自己出来**（`#main-content` 有文字）。不能只等"骨架消失"——`goto('load')`
 *    返回时 React 往往还没挂懒加载 chunk，那一刻 `.route-fallback` 根本不存在，
 *     absence 判断会在空 DOM 上秒过（首版就这么拿到过 body 只有 7 个字符的 /tickets，
 *    axe 对着空壳报 0 违规 = 纯假绿）。两种布局都有 `#main-content`（AdminLayout 的
 *    Layout.Content / WidgetShell 的 `<main class="widget-shell__body">`），故取它当公共锚。
 * ② 再等**加载态退场**（懒加载骨架 + 数据 Spin/Skeleton）。
 *
 * 不用 `networkidle`：vite dev 的 HMR 是长连接，networkidle 永不触发（见 `login.spec.ts` 注释）。
 * 数据加载态也不静默放过——空库下这些页本就秒出，还卡在 Skeleton/Spin 就是真问题。
 */
/**
 * 正文最少字数：只为排除"壳渲染了、正文一个字都没有"的空壳态。
 * 别把它调高——空库下 `/agent/sessions` 这类页的真实正文就是面包屑 + 空状态
 * （实测 31 字），阈值定到 40 会把正常页判死。
 */
const MIN_CONTENT_CHARS = 8;

export async function settle(page: Page, route: string): Promise<void> {
  const content = page.locator('#main-content');
  await expect
    .poll(async () => (await content.innerText({ timeout: 2_000 }).catch(() => '')).trim().length, {
      message: `${route} 正文一直是空壳，axe 审不到东西`,
      timeout: 30_000,
      intervals: [300],
    })
    .toBeGreaterThan(MIN_CONTENT_CHARS);

  await page.waitForFunction(
    () =>
      !document.querySelector(
        '.route-fallback, .ant-skeleton-title, .ant-skeleton-paragraph, .ant-spin-spinning'
      ),
    null,
    { timeout: 15_000 }
  );
}

/** 当前 DOM 跑一遍 axe：只审 WCAG A/AA（含 2.5.8），显式打开默认关着的 `target-size`。
 *
 * 为什么不连 best-practice 一起跑：首轮实测 `/agent/sessions` 报 `page-has-heading-one`
 * ——空库下 `SessionsPage.tsx:42` 在 h1 之前就 early-return 了 `BrandEmpty`，
 * 那是"空状态页没有页面标题"的真问题（已挂 D17），但不是 AA 合规项，
 * 把它算进这条门禁等于让 19 条路由为一件待裁的设计决策长红。
 */
export async function collectViolations(page: Page): Promise<Violation[]> {
  await page.addScriptTag({ path: AXE_SRC });
  return page.evaluate(async () => {
    const { violations } = await window.axe.run(document, {
      resultTypes: ['violations'],
      runOnly: {
        type: 'tag',
        values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'],
      },
      rules: { 'target-size': { enabled: true } },
    });
    return violations.map((v) => ({
      id: v.id,
      impact: v.impact ?? '?',
      nodes: v.nodes.length,
      // message/target 并非所有规则都填（缺模板时是 undefined），取前先兜住，
      // 否则汇总代码自己抛错，把真违规盖成一条 evaluate 失败。
      at: v.nodes[0]?.target?.join(' ') ?? '',
      why: v.nodes[0]?.message?.slice(0, 200) ?? '',
    }));
  });
}

/** 导航 → 等稳定 → axe，返回逐条摘要；被踢回 /login 视为失败（登录态没接上）。 */
export async function auditRoute(page: Page, route: string): Promise<Violation[]> {
  await page.goto(route, { waitUntil: 'load', timeout: 30_000 });
  await settle(page, route);
  expect(page.url(), `${route} 被踢回登录页，登录态未生效`).not.toMatch(/\/login$/);
  return collectViolations(page);
}

/** 失败时给人看得懂的清单，而不是 `expected [] received [object Object]`。 */
export function formatViolations(list: Violation[]): string {
  if (!list.length) return '';
  return list
    .map((v) => `  · ${v.id} [${v.impact}] ×${v.nodes} @${v.at}\n    ${v.why}`)
    .join('\n');
}
