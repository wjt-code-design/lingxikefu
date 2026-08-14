import { Typography } from 'antd';
import { EmptyState } from '@/components/common/EmptyState';

/**
 * 登录页（FE-02 实现表单 + authStore）。
 * 本单元仅空壳：路由可达、标题占位。
 */
export function LoginPage() {
  return (
    <div className="page">
      <Typography.Title level={3}>登录</Typography.Title>
      <EmptyState title="登录表单占位" description="账号/密码表单将在 FE-02 接入 authStore 与 api/auth.ts" />
    </div>
  );
}

export default LoginPage;
