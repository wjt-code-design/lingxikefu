import { afterEach, describe, expect, it } from 'vitest';
import { render, waitFor } from '@testing-library/react';
import { AppTable } from './AppTable';

/**
 * 锁住 AppTable 对 antd 内部类名的耦合（`.ant-table-body` / `.ant-table-cell-scrollbar`）。
 *
 * 为什么要有这个文件：这两处是键盘可达性补丁（axe scrollable-region-focusable、
 * empty-table-header），靠渲染后直接操作 antd 私有 DOM 生效——antd 升级一旦改名，
 * 补丁会静默失效，键盘用户失去滚动表格的能力，而**不会有任何测试变红**。
 * 本测试就是那声"会红"：类名不在 → 断言失败，逼着改动者同步选择器。
 *
 * 同时锁住"可滚动才补 tabindex"的判据（不可滚动时补 tabindex 是无意义的焦点噪音）。
 *
 * 用 waitFor 而非同步断言：补丁经 MutationObserver 回调补写，回调在微任务里跑；
 * 且 rc-table 的滚动占位格在测量之后才挂进 DOM。同步取属性会把"尚未写入"误判成"永不写入"。
 */

type Column = { title: string; dataIndex: string; width?: number; fixed?: 'left' };

const COLUMNS: Column[] = [
  { title: '标题', dataIndex: 'a', width: 300, fixed: 'left' },
  { title: '状态', dataIndex: 'b', width: 300 },
  { title: '时间', dataIndex: 'c', width: 300 },
];

const DATA = [{ a: '甲', b: '乙', c: '丙' }, { a: '丁', b: '戊', c: '己' }];

/** jsdom 无布局引擎：scrollWidth/clientWidth 恒为 0 → 判据永远走"不可滚动"分支。
 *  显式撑开尺寸，才能测到真实分支。 */
function forceScrollable(width: number, client: number) {
  Object.defineProperty(HTMLElement.prototype, 'scrollWidth', { configurable: true, get: () => width });
  Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, get: () => client });
}

afterEach(() => {
  for (const k of ['scrollWidth', 'clientWidth'] as const) {
    Object.defineProperty(HTMLElement.prototype, k, { configurable: true, get: () => 0 });
  }
});

describe('AppTable antd 内部 DOM 耦合（键盘可达性补丁）', () => {
  it('可滚动的滚动体补 tabindex=0', async () => {
    forceScrollable(900, 400);
    const { container } = render(
      <AppTable<Record<string, string>> columns={COLUMNS} dataSource={DATA} scroll={{ y: 120 }} />
    );

    // 类名探针：antd 改名则此条先红。
    const body = container.querySelector<HTMLElement>('.ant-table-body');
    expect(body, '.ant-table-body 不存在：antd 类名已变，请同步 AppTable 选择器').toBeTruthy();
    await waitFor(() => expect(body!.tabIndex).toBe(0));
  });

  it('表头滚动占位格降级为 presentation 并移出无障碍树', async () => {
    forceScrollable(900, 400);
    const { container } = render(
      <AppTable<Record<string, string>> columns={COLUMNS} dataSource={DATA} scroll={{ y: 120 }} />
    );

    const cell = await waitFor(() => {
      const el = container.querySelector<HTMLElement>('.ant-table-cell-scrollbar');
      expect(el, '.ant-table-cell-scrollbar 不存在：antd 类名已变，请同步 AppTable 选择器').toBeTruthy();
      return el!;
    });
    await waitFor(() => expect(cell.getAttribute('role')).toBe('presentation'));
    expect(cell.getAttribute('aria-hidden')).toBe('true');
  });

  it('不可滚动时不补 tabindex（避免无意义焦点停靠）', async () => {
    forceScrollable(400, 400); // scrollWidth === clientWidth → 不溢出
    const { container } = render(
      <AppTable<Record<string, string>> columns={COLUMNS} dataSource={DATA} scroll={{ y: 120 }} />
    );

    const body = container.querySelector<HTMLElement>('.ant-table-body');
    expect(body).toBeTruthy();
    await new Promise((r) => setTimeout(r, 0)); // 给 observer 微任务一次机会
    expect(body!.hasAttribute('tabindex')).toBe(false);
  });
});
