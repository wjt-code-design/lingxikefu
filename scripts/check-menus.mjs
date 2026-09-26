/**
 * D4：路由/菜单清单一致性比对尺（纯标准库，秒级）。
 * 用法：node scripts/check-menus.mjs
 *
 * 项目里有 4 份各自声明"哪些路径存在/谁看得见"的清单，此前没有任何脚本或测试比对过它们：
 *   ① SideNav 的 FALLBACK_MENUS   —— 按角色声明"后端没返回时侧栏该显示哪些项"
 *   ② routes.config 的 ROUTE_META —— 按路径声明 title/breadcrumb（RouteChrome 与 RolesPage 都只读它）
 *   ③ SideNav 的 ICON_MAP         —— 按路径声明图标
 *   ④ e2e fixtures 的 ROUTES      —— 按角色声明"这条门禁要审哪些路由"
 * 漂一条就是静默的幽灵菜单 / 漏审路由，所以在这里钉住。
 *
 * 口径是**包含式**而不是集合相等：一份清单比另一份宽是常态（admin 菜单含 /agent/*、
 * /chat 三角色都可见），只有"窄的那份越出宽的那份"才是漂移。ROUTE_META 的 `group` 字段
 * 写了没有消费方（SideNav 的分组由 getGroupKey() 按路径前缀算），不表达成员关系，故不参与判定。
 */
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const read = (rel) => readFileSync(resolve(root, rel), 'utf-8');

/** e2e 审计面的有意排除：这些页真实存在、也挂在菜单里，只是不进逐路由 axe 审计。
 *  /admin/eval 是评测结果看板，空库下长期停在加载态——见 frontend/e2e/fixtures.ts `ROUTES` 注释。 */
const E2E_AUDIT_EXCLUDED = new Set(['/admin/eval']);

/** 定位声明后的第一个 `open` 括号，按配平切出括号内正文。 */
function blockOf(src, declRe, where, open = '{') {
  const m = src.match(declRe);
  if (!m) throw new Error(`${where}：找不到声明 ${declRe}`);
  const close = open === '{' ? '}' : ']';
  const i = src.indexOf(open, m.index);
  if (i < 0) throw new Error(`${where}：${m[0]} 后面没有 ${open} 块`);
  let depth = 0;
  for (let j = i; j < src.length; j++) {
    if (src[j] === open) depth++;
    else if (src[j] === close) {
      depth--;
      if (depth === 0) return src.slice(i + 1, j);
    }
  }
  throw new Error(`${where}：块没闭合`);
}

/** 块内比对前先去掉注释，免得把注释里的示例路径当成在用的路径。 */
function stripComments(body) {
  return body.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
}
/** 读出块内所有 `'/…'` 路径字面量（这些清单的成员全是绝对路径，按此收口径最稳：
 *  单行数组和逐行数组都能吃到，行锚定的写法会整条漏掉 `user: ['/chat', …]`）。 */
function arrayOf(body) {
  return [...stripComments(body).matchAll(/'(\/[^']*)'/g)].map((m) => m[1]);
}

const ROLES = ['user', 'agent', 'admin'];

/* ① + ③ SideNav */
const sideNav = read('frontend/src/components/common/SideNav.tsx');
const iconMapBody = blockOf(sideNav, /const ICON_MAP:/, 'SideNav.tsx');
const ICON_MAP = [...stripComments(iconMapBody).matchAll(/'([^']+)'\s*:\s*</g)].map((m) => m[1]);
const fallbackBody = blockOf(sideNav, /const FALLBACK_MENUS:/, 'SideNav.tsx');
const FALLBACK = {};
for (const role of ROLES) {
  const b = blockOf(fallbackBody, new RegExp(`\\b${role}:\\s*\\[`), `SideNav FALLBACK_MENUS.${role}`, '[');
  FALLBACK[role] = arrayOf(b);
}

/* ② routes.config.ts ROUTE_META（只取键集＝"这条路径登记过元信息"） */
const metaSrc = read('frontend/src/routes.config.ts');
const metaBody = blockOf(metaSrc, /export const ROUTE_META:/, 'routes.config.ts');
const META_KEYS = new Set(
  [...stripComments(metaBody).matchAll(/'([^']+)'\s*:\s*\{/g)].map((m) => m[1])
);

/* ④ e2e ROUTES */
const fixSrc = read('frontend/e2e/fixtures.ts');
const routesBody = blockOf(fixSrc, /export const ROUTES:/, 'e2e/fixtures.ts');
const E2E = {};
for (const role of ROLES) {
  const b = blockOf(routesBody, new RegExp(`\\b${role}:\\s*\\[`), `fixtures ROUTES.${role}`, '[');
  E2E[role] = arrayOf(b);
}

const uniq = (a) => [...new Set(a)].sort();
const allMenuPaths = uniq(ROLES.flatMap((r) => FALLBACK[r]));
const allAuditedPaths = new Set(ROLES.flatMap((r) => E2E[r]));

let fails = 0;
const fail = (msg) => {
  console.log(`✗ ${msg}`);
  fails++;
};

console.log('\n[check-menus] 各清单解析结果（去重路径数）');
for (const r of ROLES) {
  console.log(`  ${r}: FALLBACK=${uniq(FALLBACK[r]).length} e2e ROUTES=${uniq(E2E[r]).length}`);
}
console.log(
  `  ICON_MAP 键=${ICON_MAP.length}  ROUTE_META 键=${META_KEYS.size}  菜单路径并集=${allMenuPaths.length}`
);

/* 断言一：e2e 审的每一条路由，都必须能从该角色的菜单真源真的到达 */
console.log('\n[check-menus] 断言 1：逐角色 e2e ROUTES ⊆ FALLBACK_MENUS（包含式，不做集合相等）');
for (const role of ROLES) {
  const reachable = new Set(FALLBACK[role]);
  const orphan = uniq(E2E[role]).filter((p) => !reachable.has(p));
  if (orphan.length)
    fail(
      `${role}: e2e ROUTES.${role} 有 ${orphan.length} 条不在 FALLBACK_MENUS.${role} 里：${orphan.join(', ')}（菜单入口没了，或 e2e 审串了角色）`
    );
  else console.log(`  ✓ ${role}: e2e ROUTES ${uniq(E2E[role]).length} 条全部可达`);
}

/* 断言二：图标表不得有成员表之外的幽灵键（SideNav 只对可见路径查 ICON_MAP，查不到就是死数据） */
console.log('\n[check-menus] 断言 2：ICON_MAP 键 ⊆ FALLBACK_MENUS ∪ ROUTE_META ∪ e2e ROUTES');
const memberTables = new Set([...allMenuPaths, ...allAuditedPaths, ...META_KEYS]);
const ghosts = ICON_MAP.filter((p) => !memberTables.has(p));
if (ghosts.length)
  fail(`ICON_MAP 幽灵键：${ghosts.join(', ')}——不在任何成员表里，SideNav 永远查不到这个图标`);
else console.log(`  ✓ ICON_MAP ${ICON_MAP.length} 个键全部查得到`);

/* 断言三：按角色声明的菜单路径都得有元信息，否则 SideNav 直接拿路径当标签渲染 */
console.log('\n[check-menus] 断言 3：ROUTE_META 键 ⊇ 各菜单业务路径');
const noMeta = allMenuPaths.filter((p) => !META_KEYS.has(p));
if (noMeta.length) fail(`FALLBACK_MENUS 未登记 ROUTE_META 的路径：${noMeta.join(', ')}`);
else console.log('  ✓ 菜单路径全部有元信息');

/* 断言四：菜单真的能走到的路径，e2e 就得审到；审不到的必须在排除集里写明理由 */
console.log('\n[check-menus] 断言 4：菜单路径并集 ⊆ e2e 审计面 ∪ 已记录排除');
const unaudited = allMenuPaths.filter((p) => !allAuditedPaths.has(p) && !E2E_AUDIT_EXCLUDED.has(p));
if (unaudited.length)
  fail(`菜单可达但 e2e 从未审计：${unaudited.join(', ')}（新增页面要同步 frontend/e2e/fixtures.ts ROUTES）`);
else console.log(`  ✓ 菜单路径全部在审计面内（排除 ${E2E_AUDIT_EXCLUDED.size} 条）`);
// 排除集自己也钉住：列进去的路径必须确实"在菜单里且未被审"，否则这条排除已经空转
const staleExcludes = [...E2E_AUDIT_EXCLUDED].filter(
  (p) => !allMenuPaths.includes(p) || allAuditedPaths.has(p)
);
if (staleExcludes.length)
  fail(`排除集失效：${staleExcludes.join(', ')} 已不在菜单里或已被 e2e 审到，该从 E2E_AUDIT_EXCLUDED 删掉`);

if (fails > 0) {
  console.error(`\ncheck-menus: ${fails} 处清单不一致`);
  process.exit(1);
}
console.log('\ncheck-menus: OK（路由/菜单清单在包含式口径下互相一致）');
