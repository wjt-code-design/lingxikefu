/**
 * A11y 对比度核算（WCAG 2.1 AA）——校验 tokens.css 关键色对。
 * 用法：node scripts/check-a11y.mjs（package.json: check:a11y）
 * 标准：正文/辅助文字 ≥ 4.5:1；大字号/图形 ≥ 3:1
 */
import { readFileSync, readdirSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');

function lum(hex) {
  const h = hex.replace('#', '');
  const rgb = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
  const lin = rgb.map((c) => (c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)));
  return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2];
}

function contrast(a, b) {
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** 前景按 p 比例染到背景上（CSS color-mix(in srgb, Fg p%, transparent) 的等效算法）。
 *  仓库里 `.faq-item__meta` 这类 chip 的底就是 `color-mix(… var(--text-3) 10% …)`，
 *  即"字色自己染色当底"——只按纯色面算会漏掉这种派生底（历史上就是这么漏的）。 */
function tint(fg, bg, p) {
  const f = fg.replace('#', '').match(/../g).map((x) => parseInt(x, 16));
  const b = bg.replace('#', '').match(/../g).map((x) => parseInt(x, 16));
  return '#' + f.map((v, i) => Math.round(p * v + (1 - p) * b[i]).toString(16).padStart(2, '0')).join('');
}

// 仅浅色：深色/跟随系统主题已于 a7825b3 移除（index.html 恒 data-theme=light），
// 不再校验 dark 块——旧脚本对已删除的 dark 块强依赖导致直接抛错崩溃（预存腐烂）。
function extractVars() {
  const css = readFileSync(resolve(root, 'src/styles/tokens.css'), 'utf-8');
  const m = css.match(/:root\s*\{([^}]*)\}/);
  if (!m) throw new Error('tokens.css 找不到 :root 块');
  const vars = {};
  for (const line of m[1].split('\n')) {
    const kv = line.match(/--([\w-]+):\s*(#[0-9a-fA-F]{6})/);
    if (kv) vars[kv[1]] = kv[2];
  }
  return vars;
}

/** 读出 --atmo-gradient 里的 rgba 停止点（.page-atmo::before 的唯一来源）。
 *  不写死数值：渐变改深/改色时这把尺子自动跟着重算，避免"尺子与样式各说各话"。 */
function extractAtmoStops() {
  const css = readFileSync(resolve(root, 'src/styles/tokens.css'), 'utf-8');
  const grad = css.match(/--atmo-gradient:\s*([^;]+);/);
  if (!grad) throw new Error('tokens.css 找不到 --atmo-gradient');
  const stops = [...grad[1].matchAll(/rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.]+)\s*\)/g)];
  if (!stops.length) throw new Error('--atmo-gradient 里没有 rgba() 停止点，读不到合成底');
  return stops.map((m) => ({
    hex: '#' + [1, 2, 3].map((i) => (+m[i]).toString(16).padStart(2, '0')).join(''),
    alpha: +m[4],
  }));
}

/** 氛围渐变压在最深色停那一档 = 挂 .page-atmo 的页面上半部**实际**底。
 *  axe 在真 Chromium 里解析不了伪元素底 → color-contrast 判 incomplete → 被
 *  `resultTypes: ['violations']` 丢弃 ⇒ 14 个 .page-atmo 页面（覆盖 19 次路由审计中的 13 次）
 *  的对比度回归只有这把静态尺兜得住。 */
function atmoSurface(base, stops) {
  const worst = stops.reduce((a, b) => (b.alpha > a.alpha ? b : a));
  return tint(worst.hex, base, worst.alpha);
}

// 前景 × 它真实会落的底色。旧版四项全拿 color-surface（纯白卡）算，于是
// "辅助文字 5.03 ✓"恒成立；而同一支 text-3 落在 --bg-page-deep 上只有 4.39、
// 落在自己染色出的 chip 底上只有 4.44 —— 2026-09-26 登录态 axe 复扫就是这么抓到漏网的。
const MIN_BODY = 4.5; // 正文/辅助文字（WCAG AA 普通文本）
const MIN_LARGE = 3.0; // 大文本/图形件

const FG_BODY = [
  ['text-1', '标题'],
  ['text-2', '正文'],
  ['text-3', '辅助文字'],
  // a11y D 批：项目约定「文字/链接前景一律 brand-dark，brand 仅作背景/装饰/大色块」
  // —— 旧检查拿 brand（2.87:1）当文字色校验属口径错误（rg 审计 2026-09-04）。
  ['color-brand-dark', '品牌链接/强调文字'],
  // D13：accent 本体是 colorSuccess 配对真源不能改，小字号语义文字改走深档 → 深档必须全底达标
  ['color-accent-text', '成功/在线语义小字'],
];
const SURFACES = [
  ['color-surface', '白卡'],
  ['bg-page', '冷灰画布'],
  ['bg-page-deep', '深画布'],
];
// 自染色 / 多压一层的 chip 底：先按 alpha 合成进它真实所在的卡底，再算比值。
// 只按纯色面算会漏掉这种派生底（历史上就是这么漏的），漏一档就是一个静默不达标的状态标签。
const SELF_TINT = [
  ['text-3', (s) => tint(s['text-3'], s['color-surface'], 0.1), '辅助文字 @10% 自染 chip 底'], // .faq-item__meta
  // D13 ①：SettingsPage `.settings-bool--on`（底 = .settings-card 纯白卡 + accent 14% 自染）
  [
    'color-accent-text',
    (s) => tint(s['color-accent'], s['color-surface'], 0.14),
    '成功语义小字 @14% accent 自染 chip 底',
  ],
  // D13 ②：globals.css `.wb-source__tag` —— tag 的 rgba(115,201,168,.16) 还压在
  // .wb-source 的 rgba(150,200,232,.04) 上，两层都得合成完才是真底（#73c9a8 是 globals 字面量，无 token）
  [
    'color-accent-text',
    (s) => tint('#73c9a8', tint(s['color-brand-pale'], s['color-surface'], 0.04), 0.16),
    '成功语义小字 @溯源卡双层染底',
  ],
];
// 只达大文本档的色（不得用于 ≤12px 正文）：锁 3:1 下限
const LARGE_ONLY = [
  ['text-4', (s) => tint(s['text-4'], s['color-surface'], 0.12), '禁用/占位 @12% 自染 chip 底'],
];

let fails = 0;
const v = extractVars();
const atmoStops = extractAtmoStops();
const report = (ok, label, fg, bg, ratio, min) => {
  if (!ok) fails++;
  console.log(
    `${ok ? '✓' : '✗'} ${label}: ${fg} on ${bg} = ${ratio.toFixed(2)}:1 (需 ≥${min})`
  );
};

// 白卡 / 两种冷灰画布 + 氛围渐变在两种画布上合成出的实际底（伪元素底 axe 判不出，见 atmoSurface）
const BG = SURFACES.map(([key, label]) => [label, v[key]]);
for (const [key, label] of [
  ['bg-page', '冷灰画布'],
  ['bg-page-deep', '深画布'],
]) {
  BG.push([`${label}+氛围渐变`, atmoSurface(v[key], atmoStops)]);
}

console.log('\n[light] 前景 × 实际底色矩阵');
for (const [fgVar, label] of FG_BODY) {
  for (const [bgLabel, bgHex] of BG) {
    const ratio = contrast(v[fgVar], bgHex);
    report(ratio >= MIN_BODY, `${label} @${bgLabel}`, v[fgVar], bgHex, ratio, MIN_BODY);
  }
}

for (const [fgVar, bgOf, label] of SELF_TINT) {
  const bg = bgOf(v);
  const ratio = contrast(v[fgVar], bg);
  report(ratio >= MIN_BODY, label, v[fgVar], bg, ratio, MIN_BODY);
}

for (const [fgVar, bgOf, label] of LARGE_ONLY) {
  const bg = bgOf(v);
  const ratio = contrast(v[fgVar], bg);
  report(ratio >= MIN_LARGE, label, v[fgVar], bg, ratio, MIN_LARGE);
}

/* antd v5 预设 Tag：字色 = palette[7]、底色 = palette[0]（cyan/green/lime 等档已与浏览器
   getComputedStyle 逐字节核对一致）。仓库在 globals.css 用 `.ant-tag.ant-tag-X` 覆盖字色，
   这里把覆盖值读回来比对：覆盖被删/被改浅 → 回落到不达标的 palette[7] → 本项即红。
   为什么不能只靠 axe：表格单元格里的标签 axe 常判 `incomplete`（背景含伪元素判不出）→
   不计违规、静默漏（2026-09-26 实测「已解决」绿标签 3.37:1 被 axe 放过）。
   在用色名从 *.tsx 里扫出来，不写死清单——上一版手写表就漏了 lime（2.48:1）。 */
const PRESET_TAG = {
  pink: ['#fff0f6', '#c41d7f'],
  red: ['#fff1f0', '#cf1322'],
  orange: ['#fff7e6', '#d46b08'],
  yellow: ['#feffe6', '#ae8700'],
  gold: ['#fffbe6', '#d48806'],
  lime: ['#fcffe6', '#7cb305'],
  green: ['#f6ffed', '#389e0d'],
  cyan: ['#e6fffb', '#08979c'],
  blue: ['#e6f4ff', '#0958d9'],
  purple: ['#f9f0ff', '#531dab'],
  violet: ['#f9f0ff', '#722ed1'],
  geekblue: ['#f0f5ff', '#1d39c4'],
  volcano: ['#fff2e8', '#d4380d'],
  magenta: ['#fff0f6', '#c41d7f'],
};

function extractTagOverrides() {
  const css = readFileSync(resolve(root, 'src/styles/globals.css'), 'utf-8');
  const out = {};
  for (const m of css.matchAll(/\.ant-tag\.ant-tag-([a-z]+)\s*\{[^}]*?color:\s*(#[0-9a-fA-F]{6})/g)) {
    out[m[1]] = m[2];
  }
  return out;
}

/** 扫 tsx 找出「作为字符串出现的预设色名」= 可能被传给 Tag/Badge 的 color prop。
 *  宁可宽（多查几档）不可漏：漏一档就是一个静默不达标的状态标签。 */
function extractUsedPresets() {
  const names = new Set();
  const files = readdirSync(resolve(root, 'src'), { recursive: true })
    .map((f) => String(f))
    .filter((f) => /\.tsx?$/.test(f) && !f.includes('node_modules'));
  for (const rel of files) {
    const txt = readFileSync(resolve(root, 'src', rel), 'utf-8');
    for (const m of txt.matchAll(/['"]([a-z]+)['"]/g)) {
      // 必须 hasOwn 而非 `in`：`'constructor' in PRESET_TAG` 为 true，
      // 裸词扫到任何 Object.prototype 键就会凭空造一个"在用预设色"，把尺子带崩。
      if (Object.hasOwn(PRESET_TAG, m[1])) names.add(m[1]);
    }
  }
  return [...names].sort();
}

console.log('\n[light] antd 预设 Tag 字色（在用色名由 tsx 扫出，覆盖值读自 globals.css）');
const tagOv = extractTagOverrides();
const usedPresets = extractUsedPresets();
console.log(`  在用预设色名：${usedPresets.join(', ')}`);
for (const name of usedPresets) {
  const [bg, def] = PRESET_TAG[name];
  // 同样按 hasOwn 取覆盖值：tagOv 是普通对象，`tagOv['toString']` 会拿到 Function 并算出 NaN
  const ov = Object.hasOwn(tagOv, name) ? tagOv[name] : null;
  const fg = ov || def;
  const ratio = contrast(fg, bg);
  report(ratio >= MIN_BODY, `Tag ${name}（${ov ? '覆盖' : 'antd 默认'}）`, fg, bg, ratio, MIN_BODY);
}

if (fails > 0) {
  console.error(`\ncheck-a11y: ${fails} 处未达 AA，请调整 tokens.css 色值`);
  process.exit(1);
}
console.log('\ncheck-a11y: OK（前景×底色矩阵全达 WCAG AA）');
