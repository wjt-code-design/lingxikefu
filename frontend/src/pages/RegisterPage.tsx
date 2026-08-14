import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Button, Form, Input, Typography, message } from 'antd';
import type { ApiError } from '@/contracts/api';
import { login, me, register } from '@/api/auth';
import { useAuthStore } from '@/store/authStore';

/**
 * 注册页（FE-02）：邮箱 + 密码（手机号可选）→ register() → 自动登录 → 跳 /chat。
 */
export function RegisterPage() {
  const navigate = useNavigate();
  const [form] = Form.useForm<{ email: string; phone?: string; password: string }>();
  const [loading, setLoading] = useState(false);

  const onFinish = async (values: { email: string; phone?: string; password: string }) => {
    setLoading(true);
    try {
      await register({ email: values.email, phone: values.phone, password: values.password });
      // 注册成功自动登录（email 即 account）
      const resp = await login({ account: values.email, password: values.password });
      useAuthStore.setState({
        token: resp.access_token,
        refreshToken: resp.refresh_token,
      });
      const meResp = await me();
      useAuthStore.getState().setUser(meResp);
      message.success('注册成功，已自动登录');
      navigate('/chat');
    } catch (e) {
      message.error((e as ApiError).message || '注册失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="auth-card">
      <Typography.Title level={3} style={{ textAlign: 'center', marginBottom: 24 }}>
        注册灵犀账号
      </Typography.Title>
      <Form form={form} layout="vertical" onFinish={onFinish} requiredMark={false}>
        <Form.Item
          name="email"
          label="邮箱"
          rules={[
            { required: true, message: '请输入邮箱' },
            { type: 'email', message: '邮箱格式不正确' },
          ]}
        >
          <Input size="large" placeholder="you@example.com" autoComplete="email" />
        </Form.Item>
        <Form.Item name="phone" label="手机号（可选）">
          <Input size="large" placeholder="手机号" autoComplete="tel" />
        </Form.Item>
        <Form.Item
          name="password"
          label="密码"
          rules={[
            { required: true, message: '请输入密码' },
            { min: 6, message: '密码至少 6 位' },
          ]}
        >
          <Input.Password size="large" placeholder="至少 6 位" autoComplete="new-password" />
        </Form.Item>
        <Form.Item style={{ marginBottom: 8 }}>
          <Button type="primary" htmlType="submit" size="large" block loading={loading}>
            注册并登录
          </Button>
        </Form.Item>
      </Form>
      <Typography.Paragraph type="secondary" style={{ textAlign: 'center', margin: 0 }}>
        已有账号？<Link to="/login">去登录</Link>
      </Typography.Paragraph>
    </div>
  );
}

export default RegisterPage;
