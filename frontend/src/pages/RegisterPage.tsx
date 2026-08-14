import { Typography } from 'antd';
import { EmptyState } from '@/components/common/EmptyState';

/**
 * 注册页（FE-02 实现表单）。
 */
export function RegisterPage() {
  return (
    <div className="page">
      <Typography.Title level={3}>注册</Typography.Title>
      <EmptyState title="注册表单占位" description="注册表单将在 FE-02 接入 authStore 与 api/auth.ts" />
    </div>
  );
}

export default RegisterPage;
