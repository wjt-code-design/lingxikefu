import { Table, Tag } from 'antd';
import type { TableProps } from 'antd';
import type { ReactNode } from 'react';

/**
 * AppTable：工作台统一表格（设计规范 §3.4，H3 落地）。
 * - 行 hover 高亮（primary-weak 品牌化，非默认灰）
 * - 表头吸顶 + 横向滚动兜底（窄屏不破版）
 * - 中等行高（工作台密度 6-8 分）· 分页默认无 sizeChanger
 * - 其余完全透传 AntD Table props（columns/loading/dataSource 等）
 */
export function AppTable<T extends object>(props: TableProps<T>) {
  const { rowClassName, pagination, ...rest } = props;
  return (
    <Table<T>
      size="middle"
      sticky={{ offsetHeader: 0 }}
      scroll={{ x: 'max-content' }}
      rowClassName={(record, index, indent) => {
        const custom =
          typeof rowClassName === 'function' ? rowClassName(record, index, indent) : rowClassName;
        return ['app-table-row', custom].filter(Boolean).join(' ');
      }}
      pagination={{ showSizeChanger: false, ...pagination }}
      {...rest}
    />
  );
}

/** 通用状态语义色（设计规范 §3.4：状态 Tag 一色一义）。可按需扩展映射。 */
export const STATUS_COLOR: Record<string, string> = {
  // 工单状态机（T1）
  open: 'blue', // 待处理
  processing: 'gold', // 处理中
  resolved: 'green', // 已解决
  closed: 'default', // 已关闭
  // 意图
  handoff: 'orange', // 转人工（稀缺强调色）
  // 文档导入
  pending: 'gold',
  ready: 'green',
  failed: 'red',
};

/** 工单状态中文文案（单一真源：工单页 / 聊天页角标共用；未知状态回退原值）。 */
export const TICKET_STATUS_TEXT: Record<string, string> = {
  open: '待处理',
  processing: '处理中',
  resolved: '已解决',
  closed: '已关闭',
};

/** 状态标签：色随语义（STATUS_COLOR），文案默认取 status 原值。 */
export function StatusTag({ status, text }: { status: string; text?: ReactNode }) {
  return <Tag color={STATUS_COLOR[status] ?? 'default'}>{text ?? status}</Tag>;
}

export default AppTable;
