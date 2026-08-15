import { EmptyState } from '@/components/common/EmptyState';

/**
 * Agent 工作台 · 客户管理。
 * 后端暂无客户画像 API（Phase2 预留），路由先行，页面为占位空态。
 */
export function CustomersPage() {
  return (
    <EmptyState
      title="客户管理"
      description="Phase2：客户画像 / 会话历史聚合视图（需后端 customers API）"
    />
  );
}

export default CustomersPage;
