/**
 * 校验 tokens.css（CSS 变量真源）与 theme.ts（AntD JS token）关键值一致（v2.1 修订 A）。
 * 用法：node scripts/check-tokens.mjs（package.json: check:tokens）
 * 映射：tokens.css 变量 → theme.ts 字段
 */
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');

function extractVars(file, block) {
  const css = readFileSync(resolve(root, file), 'utf-8');
  const re =
    block === 'light'
      ? /:root\s*\{([^}]*)\}/
      : /\[data-theme='dark'\]\s*\{([^}]*)\}/;
  const m = css.match(re);
  if (!m) throw new Error(`tokens.css 找不到 ${block} 块`);
  const vars = {};
  for (const line of m[1].split('\n')) {
    const kv = line.match(/--([\w-]+):\s*([^;]+);/);
    if (kv) vars[kv[1]] = kv[2].trim();
  }
  return vars;
}

function extractTsTokens() {
  const ts = readFileSync(resolve(root, 'src/theme.ts'), 'utf-8');
  const out = {};
  for (const [name, re] of [
    ['light', /lightTokens[\s\S]*?\{([\s\S]*?)\};/],
    ['dark', /darkTokens[\s\S]*?\{([\s\S]*?)\};/],
  ]) {
    const m = ts.match(re);
    if (!m) throw new Error(`theme.ts 找不到 ${name}Tokens`);
    const vals = {};
    for (const line of m[1].split('\n')) {
      const kv = line.match(/([\w]+):\s*'([^']+)'/);
      if (kv) vals[kv[1]] = kv[2].trim();
    }
    out[name] = vals;
  }
  return out;
}

// tokens.css 变量名 → theme.ts 字段名（语义映射）
// 注意：colorBgLayout=页面底 ↔ --bg-page；--color-surface 是卡片底，不映射 AntD layout
const MAP = [
  ['color-brand', 'colorPrimary'],
  ['color-accent', 'colorSuccess'],
  ['color-amber', 'colorWarning'],
  ['color-danger', 'colorError'],
  ['bg-page', 'colorBgLayout'],
];

const ts = extractTsTokens();
let fails = 0;
for (const mode of ['light', 'dark']) {
  const vars = extractVars('src/styles/tokens.css', mode);
  for (const [cssVar, tsField] of MAP) {
    const cssVal = vars[cssVar];
    const tsVal = ts[mode][tsField];
    if (!cssVal || !tsVal || cssVal.toLowerCase() !== tsVal.toLowerCase()) {
      console.error(`✗ ${mode}: --${cssVar}(${cssVal}) ≠ ${tsField}(${tsVal})`);
      fails++;
    }
  }
}

if (fails > 0) {
  console.error(`\ncheck-tokens: ${fails} 处不一致，请同步 tokens.css 与 theme.ts`);
  process.exit(1);
}
console.log('check-tokens: OK（tokens.css 与 theme.ts 关键值一致）');
