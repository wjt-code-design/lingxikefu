import { expect, test } from '@playwright/test';
import { collectViolations } from './fixtures';

/**
 * axe 尺子的负向对照（证明 `login-state.spec.ts` 那 19 条"零违规"不是空跑）。
 *
 * 动机来自本轮两个真实盲区：
 * - `target-size` 在 axe 里默认 `enabled: false`，`runOnly` 按 tag 选规则时它很容易被静默排除；
 * - `color-contrast` 在 jsdom 下根本判不了（所以 vitest 版显式关掉了它），
 *   e2e 版留着它就是为了量对比度——但"留着"和"真的在跑"是两件事。
 * 所以每条被依赖的规则都要有一个"塞进坏样本、守卫必须红"的自检，否则守卫形同虚设。
 *
 * 两个坏样本都是测试自己注入的固定样式，不依赖应用 CSS，因此不会随改版漂移。
 */

test.describe('axe 尺子自检', () => {
  test.skip(({ isMobile }) => isMobile, '自检与视口无关，桌面 project 跑一次即可');

  test.beforeEach(async ({ page }) => {
    await page.goto('/login', { waitUntil: 'load' });
  });

  test('注入 12px 相邻按钮 → target-size 必须报', async ({ page }) => {
    await page.evaluate(() => {
      const host = document.createElement('div');
      host.id = 'e2e-probe-target';
      // position:fixed 是必须的：axe 的几何类规则跳过视口外节点，
      // 追加到 body 末尾的样本落在折叠线以下会被静默放过（首版就这么"过"了一次）。
      host.style.cssText = 'position:fixed;left:8px;top:8px;z-index:99999';
      host.innerHTML =
        '<button style="width:12px;height:12px;padding:0;margin:0">A</button>' +
        '<button style="width:12px;height:12px;padding:0;margin:0">B</button>';
      document.body.appendChild(host);
    });
    const ids = (await collectViolations(page)).map((v) => v.id);
    expect(
      ids,
      'axe 没执行 target-size：collectViolations 的 runOnly/rules 组合失效，2.5.8 门禁是空跑'
    ).toContain('target-size');
  });

  test('注入 1.07:1 文字 → color-contrast 必须报', async ({ page }) => {
    await page.evaluate(() => {
      const p = document.createElement('p');
      p.id = 'e2e-probe-contrast';
      // 刻意不用"白底白字"：axe 把 fg==bg 归入 incomplete（locale 里的 equalRatio 键，
      // 见 axe.js:31350 'Element has a 1:1 contrast ratio with the background'），
      // 最极端的样本反而进不了 violations。1.07:1 是 axe 真会判失败的最贴近样本，
      // 也正是本项目 check:a11y 那批 4.x:1 边缘值的同一量级。
      p.style.cssText =
        'position:fixed;left:8px;top:40px;z-index:99999;background:#ffffff;color:#f2f2f2;font-size:14px';
      p.textContent = '近白字压白底的对照样本';
      document.body.appendChild(p);
    });
    const ids = (await collectViolations(page)).map((v) => v.id);
    expect(
      ids,
      'axe 没执行 color-contrast：e2e 版对比度门禁是空跑（jsdom 版本来就判不了这条）'
    ).toContain('color-contrast');
  });
});
