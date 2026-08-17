import { useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { Button, Form, Input, Typography, message } from 'antd';
import type { ApiError } from '@/contracts/api';
import { login, me } from '@/api/auth';
import { useAuthStore } from '@/store/authStore';

/**
 * 登录页（FE-02）：表单 → login() → 写入 token → me() 取档案 → 跳回来源页或 /chat。
 * 路由守卫 RequireAuth 将受保护路径存入 location.state.from（L6：登录后回到来源页）。
 */
export function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const [form] = Form.useForm<{ account: string; password: string }>();
  const [loading, setLoading] = useState(false);

  const onFinish = async (values: { account: string; password: string }) => {
    setLoading(true);
    try {
      const resp = await login(values);
      // 先写入 token，me() 才能经拦截器携带 Bearer
      useAuthStore.setState({
        token: resp.access_token,
        refreshToken: resp.refresh_token,
      });
      const meResp = await me();
      useAuthStore.getState().setUser(meResp);
      message.success('登录成功');
      const from = (location.state as { from?: string } | null)?.from;
      navigate(from && from !== '/login' ? from : '/chat');
    } catch (e) {
      message.error((e as ApiError).message || '登录失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="auth-card">
      <Typography.Title level={3} style={{ textAlign: 'center', marginBottom: 24 }}>
        登录灵犀客服
      </Typography.Title>
      <Form form={form} layout="vertical" onFinish={onFinish} requiredMark={false}>
        <Form.Item
          name="account"
          label="账号"
          rules={[{ required: true, message: '请输入邮箱或手机号' }]}
        >
          <Input size="large" placeholder="邮箱或手机号" autoComplete="username" />
        </Form.Item>
        <Form.Item
          name="password"
          label="密码"
          rules={[{ required: true, message: '请输入密码' }]}
        >
          <Input.Password size="large" placeholder="密码" autoComplete="current-password" />
        </Form.Item>
        <Form.Item style={{ marginBottom: 8 }}>
          <Button type="primary" htmlType="submit" size="large" block loading={loading}>
            登录
          </Button>
        </Form.Item>
      </Form>
      <Typography.Paragraph type="secondary" style={{ textAlign: 'center', margin: 0 }}>
        还没有账号？<Link to="/register">立即注册</Link>
      </Typography.Paragraph>
    </div>
  );
}

export default LoginPage;
