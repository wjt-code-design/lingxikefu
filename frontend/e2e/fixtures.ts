import { expect, type Page } from '@playwright/test';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

/**
 * e2e 登录态与 axe 取证公共件（2026-09-26 那轮一次性探针脚本的固化版）。
 *
 * 与原探针的差别（为什么现在能进 CI）：
 * - 原脚本靠手工签 refreshToken 再 `addInitScript` 注入，且**每条路由要吃一枚未用过的票**——
 *   `/auth/refresh` 是 R-4 轮换制（旧 jti 立即进 Redis 吊销表），同令牌复用第二个页面必然 401
 *   被踢回 /login。首轮就踩：19 条里 16 条其实停在 /login，axe 对着登录页照报 0 违规＝假绿。
 * - 现在走 UI 真登录：`scripts/seed_e2e_accounts.py` 灌三个角色号，一个 context 只登一次。
 *
 * ⚠️ 如实标注执行路径：`auditRoute` 每条路由 `page.goto()` = **整页重载**，内存 access token
 * 随之作废（BUG-15 不落盘），所以每条路由都会重跑一次 `bootstrapAuth`（refresh + me）。
 * 这审的是"冷启动登录态"（与 D9 探针同形），**不是**真人点侧栏的客户端导航路径；
 * D15 那条修复（同页 401 后重读 localStorage）由 `src/tests/client.test.ts` 单测钉着，e2e 不覆盖。
 *
 * 口令与种子步骤必须同值：CI 的 `E2E_PASSWORD` 就取这里的常量（一次性库里的号，出库即无意义；
 * 写成常量而不是环境变量，是为了让 `npx playwright test` 本地零配置可跑）。
 */

export type E2ERole = 'user' | 'agent' | 'admin';

export const E2E_PASSWORD = 'e2e-not-a-secret-2026';

/** zustand persist 的落盘键。对岸真源是 `src/store/authStore.ts` 的 `AUTH_STORAGE_KEY`——
 *  跨 src/e2e 边界没法直接 import（playwright 的 tsconfig 面与 vite 别名不同），改一边必须改两边。 */
export const AUTH_KEY = 'lingxi-auth';

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
 * 三个角色的登录态路由全集：19 次逐路由审计（user 4 + agent 6 + admin 9），
 * 去重后是 18 条唯一路径——`/chat` 同时挂在 user 和 agent 名下，各审一次。
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
  // 停在 /login 的两种常见原因：种子号没灌（`scripts/seed_e2e_accounts.py`），
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
  /** 全部命中节点的 axe selector 路径。`at` 只有首个节点，被测页自身凑出同类违规时
   *  首节点未必是探针——自检要断"报的就是我注入的那个元素"，必须看全量。 */
  targets: string[];
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
          nodes: {
            target?: string[];
            message?: string;
            any?: { message?: string }[];
            all?: { message?: string }[];
            none?: { message?: string }[];
          }[];
        }[];
      }>;
    };
  }
}

/**
 * 正文最少字数：只为排除"壳渲染了、正文一个字都没有"的空壳态（配 `>=` 用）。
 * 别把它调高——空库下 `/agent/sessions` 这类页的真实正文就是面包屑 + 空状态
 * （实测 31 字），阈值定到 40 会把正常页判死。
 */
const MIN_CONTENT_CHARS = 8;

/**
 * 停进"稳定态"再取证。三段等待，顺序不能反：
 *
 * ① 先等**正文自己出来**（`#main-content` 有字）。不能只等"骨架消失"——`goto('load')`
 *    返回时 React 往往还没挂懒加载 chunk，那一刻 `.route-fallback` 根本不存在，
 *    absence 判断会在空 DOM 上秒过（首版就这么拿到过 body 只有 7 个字符的 /tickets，
 *    axe 对着空壳报 0 违规 = 纯假绿）。两种布局都有 `#main-content`（AdminLayout 的
 *    Layout.Content / WidgetShell 的 `<main class="widget-shell__body">`），故取它当公共锚。
 * ② 再等**加载态退场**（懒加载骨架 + 数据 Spin/Skeleton）。
 * ③ 最后等**入场淡入真的结束**（`page-in`/`card-in`/`order-card-in` 把 opacity 从 0 扫到 1）。
 *    这条不是洁癖而是必要：`/feedback` 的次要文案静息值只有 4.89:1，淡入中途（实测 α=0.92 时
 *    已降到 **4.15:1**）必然低于 AA 的 4.5 ⇒ axe 撞进这 280ms 就会偶发报 color-contrast
 *    （本机 6 次全量红 1 次就是这么来的）。审"用户看得见的稳定态"才是这条门禁的语义。
 *    只查 opacity——transform 位移不影响对比度；不做 `getAnimations().length===0`，
 *    因为全站有 `chat-cursor-blink`/`dot-breathe` 这类 infinite 动画，那样写会永远等不到。
 *
 * 不用 `networkidle`：vite dev 的 HMR 是长连接，networkidle 永不触发（见 `login.spec.ts` 注释）。
 * 数据加载态也不静默放过——空库下这些页本就秒出，还卡在 Skeleton/Spin 就是真问题。
 */
export async function settle(page: Page, route: string): Promise<void> {
  const content = page.locator('#main-content');
  await expect
    .poll(async () => (await content.innerText({ timeout: 2_000 }).catch(() => '')).trim().length, {
      message: `${route} 正文一直是空壳，axe 审不到东西`,
      timeout: 30_000,
      intervals: [300],
    })
    .toBeGreaterThanOrEqual(MIN_CONTENT_CHARS);

  await page.waitForFunction(
    () =>
      !document.querySelector(
        '.route-fallback, .ant-skeleton-title, .ant-skeleton-paragraph, .ant-spin-spinning'
      ),
    null,
    { timeout: 15_000 }
  );

  // 锚点用 #main-content 及其子根，而不是类名白名单——白名单已经漏过 `.my-tickets`/`.order-card`
  // 这类不挂 `.page` 的根，漏一个就是一段静默空等；空 NodeList 的 every() 恒真，故配 length>=1
  // 下界把它从"通过"变成"红"。① 已经证明 #main-content 在，这个下界不会把谁卡到超时。
  await page.waitForFunction(
    () => {
      const hosts = [
        ...document.querySelectorAll('#main-content, #main-content > *, .landing__feature, .order-card'),
      ];
      return (
        hosts.length >= 1 && hosts.every((el) => parseFloat(getComputedStyle(el).opacity) >= 0.999)
      );
    },
    null,
    { timeout: 10_000 }
  );
}

/** 当前 DOM 跑一遍 axe：只审 WCAG A/AA（含 2.5.8），显式打开默认关着的 `target-size`。
 *
 * 为什么不连 best-practice 一起跑：首轮实测 `/agent/sessions` 报 `page-has-heading-one`
 * ——空库下 `SessionsPage` 在 h1 之前就 early-return 了 `BrandEmpty`（D17，已修：h1 提到分支外）。
 * 但 `page-has-heading-one` 本身属 best-practice 而非 AA 合规项，把它算进这条门禁
 * 等于让 19 次审计为一件设计口径问题长红。
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
      targets: v.nodes.flatMap((n) => n.target ?? []),
      // 违规的具体理由在 nodes[].any/all/none 的 checks 里；nodes[].message 常常是空的
      // （首版只读 message，结果 /feedback 那次红只留下一个空字符串，等于没证据）
      why:
        v.nodes[0]?.message ||
        [...(v.nodes[0]?.any ?? []), ...(v.nodes[0]?.all ?? []), ...(v.nodes[0]?.none ?? [])]
          .map((c) => c.message)
          .filter(Boolean)
          .join(' | ') ||
        '',
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
