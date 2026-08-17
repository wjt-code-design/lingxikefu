/**
 * A11y 对比度核算（WCAG 2.1 AA）——校验 tokens.css 关键色对。
 * 用法：node scripts/check-a11y.mjs（package.json: check:a11y）
 * 标准：正文/辅助文字 ≥ 4.5:1；大字号/图形 ≥ 3:1
 */
import { readFileSync } from 'node:fs';
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

function extractVars(mode) {
  const css = readFileSync(resolve(root, 'src/styles/tokens.css'), 'utf-8');
  const re =
    mode === 'light' ? /:root\s*\{([^}]*)\}/ : /\[data-theme='dark'\]\s*\{([^}]*)\}/;
  const m = css.match(re);
  if (!m) throw new Error(`tokens.css 找不到 ${mode} 块`);
  const vars = {};
  for (const line of m[1].split('\n')) {
    const kv = line.match(/--([\w-]+):\s*(#[0-9a-fA-F]{6})/);
    if (kv) vars[kv[1]] = kv[2];
  }
  return vars;
}

const CHECKS = [
  // [textVar, bgVar, label, minRatio]
  ['text-1', 'color-surface', '标题', 4.5],
  ['text-2', 'color-surface', '正文', 4.5],
  ['text-3', 'color-surface', '辅助文字', 4.5],
  ['color-brand', 'color-surface', '品牌色（图形/链接）', 3.0],
];

let fails = 0;
for (const mode of ['light', 'dark']) {
  const v = extractVars(mode);
  console.log(`\n[${mode}]`);
  for (const [tv, bg, label, min] of CHECKS) {
    const ratio = contrast(v[tv], v[bg]);
    const ok = ratio >= min;
    if (!ok) fails++;
    console.log(`${ok ? '✓' : '✗'} ${label}: ${v[tv]} on ${v[bg]} = ${ratio.toFixed(2)}:1 (需 ≥${min})`);
  }
}

if (fails > 0) {
  console.error(`\ncheck-a11y: ${fails} 处未达 AA，请调整 tokens.css 色值`);
  process.exit(1);
}
console.log('\ncheck-a11y: OK（关键色对均达 WCAG AA）');
