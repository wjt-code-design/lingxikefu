import type { ReactNode } from 'react';
import { Outlet } from 'react-router-dom';
import { Card, Typography } from 'antd';

/**
 * 认证外壳：登录/注册页居中卡片布局。
 */
export function AuthLayout({ children }: { children?: ReactNode }) {
  return (
    <div className="auth-layout">
      <Typography.Title level={2} className="auth-layout__brand">
        灵犀 · Lingxi
      </Typography.Title>
      <Card className="auth-layout__card">{children ?? <Outlet />}</Card>
    </div>
  );
}

export default AuthLayout;
