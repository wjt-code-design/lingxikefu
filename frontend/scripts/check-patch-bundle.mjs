#!/usr/bin/env node
/**
 * 构建产物守卫：断 React 19 补丁的**副作用真的落进了生产入口 chunk**。
 *
 * 与另一把尺子的分工（2026-09-27 门禁有效性审计 · 靶 2）：
 *   - src/tests/entry-react19-patch.test.ts（进 npm run test，毫秒级）
 *     防"删掉那行 / 顺序放错"，判据是入口**源文本**。
 *   - 本脚本（npm run check:bundle，约 12s，只在 build 语境跑，不进 npm run test）
 *     防"源码写了但产物里没有"：补丁模块被 tree-shake 摇掉、被挪进某个懒加载 chunk、
 *     或入口换成了另一个文件——这三种只有看**产物**才看得见。
 *   审计实测的坏输入：删掉 src/main.tsx:1 的补丁 import 后 tsc 绿 / vite build 绿 / vitest 143 绿，
 *   而 dist 的 index chunk 从 469.73kB 掉到 469.53kB（补丁模块被整体摇掉）。本脚本就是为了让那种
 *   "能构建出运行期坏的包而照绿"变成红。
 *
 * 判据为什么取得出来：补丁的副作用体是
 *     unstableSetRender((node, container) => { container._reactRoot ||= createRoot(container); ... })
 *   `unstableSetRender` 会被压缩器改名（产物里 grep 它为 0 命中），但 **属性名 `_reactRoot` 不会被改**
 *   （esbuild/terser 默认不 mangle properties），于是 `_reactRoot||(` 成为这段副作用在生产 chunk 里的
 *   稳定指纹。实测对照：正常构建的 index chunk 命中 1 次，删掉 import 后同一 chunk 命中 0 次
 *   （react-dom 自带的那个是 `_reactRootContainer`，形状不同，不会造成假绿）。
 *
 * 用法：
 *   node scripts/check-patch-bundle.mjs                 # 自己在内存里 build 一份（不碰 dist/，
 *                                                       # 因此不受本机 dist/ 是否过期影响）
 *   node scripts/check-patch-bundle.mjs --dist <dir>    # 改读已构建好的产物（如 CI 的 dist/）
 *     注：--dist 模式拿不到 rollup 的模块图，只做文本指纹断言；默认模式两条都断。
 */
import { readFileSync, existsSync } from 'node:fs';
import { dirname, resolve, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const feRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const PATCH = '@ant-design/v5-patch-for-react-19';
const PATCH_ID_RE = /@ant-design[/\\]v5-patch-for-react-19/;
// 补丁副作用的稳定指纹（见文件头"判据为什么取得出来"）。
const SIDE_EFFECT_RE = /_reactRoot\s*\|\|/;

function fail(msg) {
  console.error(`✗ check-bundle: ${msg}`);
  process.exit(1);
}

/** 内存里跑一次 vite build，取 chunk 列表（含 rollup 的模块图信息）。 */
async function buildInMemory() {
  const { build } = await import('vite');
  const result = await build({
    configFile: join(feRoot, 'vite.config.ts'),
    root: feRoot,
    logLevel: 'warn',
    build: {
      // 只在内存里生成、不落盘：本机 dist/ 可能是上一次（甚至被改坏的）构建的残留，
      // 守卫必须与当前源码严格对应，否则又回到"dist 里躺着坏包而尺子看不见"。
      write: false,
      emptyOutDir: false,
      outDir: join(feRoot, 'node_modules/.tmp/lingxi-bundle-guard'),
      sourcemap: false,
    },
  });
  const outputs = Array.isArray(result) ? result : [result];
  const chunks = [];
  for (const o of outputs) {
    for (const item of o?.output ?? []) if (item.type === 'chunk') chunks.push(item);
  }
  return chunks;
}

/**
 * --dist 模式：入口 chunk 由 index.html 里那串 <script type="module" src=...> 决定，
 * 而不是"文件名像入口的那个"——被 hash 改名的产物靠名字猜会猜错。
 */
function readFromDist(dir) {
  const htmlFile = join(dir, 'index.html');
  if (!existsSync(htmlFile)) fail(`--dist ${dir} 下没有 index.html（产物不完整，无法确定入口 chunk）`);
  const html = readFileSync(htmlFile, 'utf-8');
  const srcs = [...html.matchAll(/<script[^>]*\bsrc=["']([^"']+)["']/g)].map((x) => x[1]);
  if (srcs.length === 0) fail(`${htmlFile} 里找不到 <script ... src=...>，取不到入口 chunk`);
  return srcs.map((s) => {
    const abs = resolve(dir, `.${s.startsWith('/') ? s : `/${s}`}`);
    if (!existsSync(abs)) fail(`index.html 引用的入口文件不存在：${s} -> ${abs}`);
    return { name: s, fileName: s, code: readFileSync(abs, 'utf-8'), isEntry: true, modules: null };
  });
}

const argv = process.argv.slice(2);
const distFlag = argv.indexOf('--dist');
let chunks;
let mode;
let haveModuleGraph;
if (distFlag >= 0) {
  const dir = argv[distFlag + 1];
  if (!dir || dir.startsWith('--')) fail('--dist 后面要跟产物目录，例如 --dist dist');
  mode = `--dist ${dir}`;
  chunks = readFromDist(resolve(feRoot, dir));
  haveModuleGraph = false;
} else {
  mode = '内存构建（build.write=false，不落盘、不动 dist/）';
  chunks = await buildInMemory();
  haveModuleGraph = true;
}

console.log(`check-bundle: 取产物方式 = ${mode}，chunk 总数 = ${chunks.length}`);

const entries = chunks.filter((c) => c.isEntry);
if (entries.length !== 1) {
  fail(`生产 HTML 应当只挂 1 个入口 module chunk，实际 ${entries.length} 个：[${entries.map((c) => c.name ?? c.fileName).join(', ')}]`);
}
const entry = entries[0];
console.log(`  入口 chunk: ${entry.name ?? entry.fileName}`);

// 断言 1（模块图级，仅默认模式可用）：入口 chunk 的 facade 模块必须是 src/main.tsx，
// 且补丁模块确实进了这个 chunk、不是"进了图但被摇成 0 字节"。
if (haveModuleGraph) {
  const ids = Object.keys(entry.modules ?? {});
  // 锚定"这个入口 chunk 的模块图里确实有 src/main.tsx"——vite 的 html 入口把 facadeModuleId
  // 记成 index.html，所以只能从模块图侧认；这条保证源码守卫与本脚本守的是同一个入口文件。
  if (!ids.some((id) => /src[/\\]main\.tsx$/.test(id))) {
    fail(
      `入口 chunk（facade=${entry.facadeModuleId}）的模块图里没有 src/main.tsx（共 ${ids.length} 个模块）。` +
        '源码守卫 entry-react19-patch.test.ts 守的是 main.tsx，入口一旦换文件它就守着死文件了——' +
        '这条断言保证两把尺子指的是同一个入口。',
    );
  }
  const patchModules = ids.filter((id) => PATCH_ID_RE.test(id));
  console.log(`  入口 chunk 模块数 = ${ids.length}`);
  if (patchModules.length === 0) {
    fail(
      `入口 chunk 的模块图里没有 ${PATCH}（共 ${ids.length} 个模块）：` +
        '源码里那行 import 要么被删了，要么被 rollup 整体摇掉了。',
    );
  }
  const rendered = patchModules.map((id) => `${id} renderedLength=${entry.modules[id].renderedLength ?? 0}`);
  const totalRendered = patchModules.reduce((a, id) => a + (entry.modules[id].renderedLength ?? 0), 0);
  console.log(`  补丁模块: ${rendered.join(' | ')}`);
  if (totalRendered === 0) {
    fail(
      `${PATCH} 在入口 chunk 里 renderedLength=0 —— 模块进了图但副作用被摇掉了。` +
        '产物里的 antd 静态方法（message/notification/Modal）会退化成运行期报错。',
    );
  }
} else {
  console.log('  （--dist 模式无模块图信息，跳过模块级断言，只做文本指纹断言）');
}

// 断言 2（产物文本级）：副作用代码真的进了入口 chunk。
const hits = entry.code.match(new RegExp(SIDE_EFFECT_RE.source, 'g')) ?? [];
console.log(`  入口 chunk 内补丁副作用指纹 ${SIDE_EFFECT_RE.source} 命中数 = ${hits.length}`);
if (hits.length === 0) {
  const elsewhere = chunks.filter((c) => c !== entry && SIDE_EFFECT_RE.test(c.code));
  fail(
    `入口 chunk 里找不到 ${PATCH} 的副作用代码（unstableSetRender 的 render 覆盖）。\n` +
      '    最可能原因：src/main.tsx 首行的 import 被删/被改，产物能构建但运行期 20+ 处 message.* 全废。\n' +
      (elsewhere.length
        ? `    副作用落到了非入口 chunk（${elsewhere.map((c) => c.name ?? c.fileName).join(', ')}）= 懒加载，` +
          '首屏渲染前补丁没跑，静态方法照样坏。'
        : '    且整个产物里都没有这段代码 = 补丁彻底没进包。'),
  );
}

console.log(`✓ check-bundle: OK（${PATCH} 的副作用确实在生产入口 chunk 里，命中 ${hits.length} 处）`);
