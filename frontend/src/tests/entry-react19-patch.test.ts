/**
 * 生产入口必须把 @ant-design/v5-patch-for-react-19 挂在**所有 import 之前**。
 *
 * 为什么需要这把守卫（2026-09-27 门禁有效性审计 · 靶 2）：
 *   删掉 src/main.tsx:1 那行 `import '@ant-design/v5-patch-for-react-19';` 之后实测——
 *     npx tsc --noEmit  → exit 0
 *     npm run build     → ✓ built（只有 index chunk 从 469.73kB 变 469.53kB、hash 变了）
 *     npx vitest run    → Tests 143 passed
 *   而全站 20+ 处 message.* / notification.* / Modal.* 静态方法在 React 19 下运行期全废。
 *   三道门禁没有一个能红，原因各不相同：
 *     - `import 'x'` 没有可检查的符号，tsc 天然无话可说；
 *     - vite build 只转换+打包，不执行任何一行代码，摇掉一个副作用模块不算错；
 *     - vitest 更糟：src/tests/setup.ts:2 也 import 了同一个补丁，于是**测试环境把入口的缺失
 *       掩盖掉了**——所以"在测试里断言 antd 静态方法能用"这种写法根本抓不到这个 bug。
 *   能抓到的只有两类尺子：读入口源文本（本文件），和读构建产物
 *   （frontend/scripts/check-patch-bundle.mjs，npm run check:bundle）。
 */
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const feRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const PATCH = '@ant-design/v5-patch-for-react-19';

/**
 * 按源码顺序抽出静态 import 的说明符，覆盖 `import 'x';` 与 `import { a } from 'x';` 两种写法。
 * 先剥掉注释，避免"把补丁写进注释里"冒充通过。
 */
function importSpecifiers(source: string): string[] {
  const code = source.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/^\s*\/\/.*$/gm, ' ');
  const re = /^\s*import\s+(?:[^;'"]*?\sfrom\s+)?['"]([^'"]+)['"]/gm;
  const found: string[] = [];
  let m = re.exec(code);
  while (m !== null) {
    found.push(m[1]);
    m = re.exec(code);
  }
  return found;
}

describe('生产入口的 React 19 补丁挂载', () => {
  const entrySource = readFileSync(resolve(feRoot, 'src/main.tsx'), 'utf-8');
  const specifiers = importSpecifiers(entrySource);

  it('src/main.tsx 仍然是 index.html 指向的那个入口', () => {
    // 没有这条锚，下面的断言可能守着一个已经不被任何地方加载的死文件而常绿。
    const html = readFileSync(resolve(feRoot, 'index.html'), 'utf-8');
    const srcs = [...html.matchAll(/<script[^>]*\bsrc=["']([^"']+)["']/g)].map((x) => x[1]);
    expect(srcs).toContain('/src/main.tsx');
  });

  it('入口的第一条 import 就是 React 19 补丁', () => {
    expect(specifiers.length).toBeGreaterThan(0);
    // 严格取 0 位：补丁必须在 antd 被任何模块引入之前装好 render 覆盖，
    // 而 react / react-dom 自身不需要它——所以"最先"既是最小要求也是可执行的判据。
    // 顺序错（比如有人把 polyfill 或 '@/styles/x.css' 提到前面）同样判红，改法就是把补丁行挪回首位。
    expect(specifiers[0]).toBe(PATCH);
  });

  it('入口在任何本地模块（@/ 或 ./）之前引入补丁', () => {
    const patchIdx = specifiers.indexOf(PATCH);
    expect(patchIdx).toBeGreaterThanOrEqual(0);
    const localIdx = specifiers.findIndex((s) => s.startsWith('@/') || s.startsWith('./'));
    expect(localIdx).toBeGreaterThan(patchIdx);
  });

  it('补丁是 dependencies 里的真实依赖，不是凭空写的说明符', () => {
    const pkg = JSON.parse(readFileSync(resolve(feRoot, 'package.json'), 'utf-8')) as {
      dependencies?: Record<string, string>;
    };
    expect(pkg.dependencies?.[PATCH]).toBeTruthy();
  });
});
