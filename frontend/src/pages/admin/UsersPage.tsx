import { Typography } from 'antd';
import { EmptyState } from '@/components/common/EmptyState';

/**
 * 用户管理页（FE-04 实现列表 + 角色变更）。
 */
export function UsersPage() {
  return (
    <div className="page">
      <Typography.Title level={3}>用户管理</Typography.Title>
      <EmptyState title="用户列表占位" description="FE-04 在此渲染用户列表与角色更新" />
    </div>
  );
}

export default UsersPage;
