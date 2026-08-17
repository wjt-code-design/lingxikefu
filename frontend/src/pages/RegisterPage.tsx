import { useState } from 'react';
import type { MouseEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Button, Checkbox, Form, Input, Typography, message } from 'antd';
import type { ApiError } from '@/contracts/api';
import { login, me, register } from '@/api/auth';
import { useAuthStore } from '@/store/authStore';

/**
 * 注册页（FE-02）：邮箱 + 密码（手机号可选）→ register() → 自动登录 → 跳 /chat。
 * Phase 2 task 12：品牌化布局改造（左品牌区/右表单区，样式在 AuthLayout.css）；
 * 新增「已阅读并同意《服务条款》《隐私政策》」勾选框，勾选后才允许提交
 * （条款/政策链接为 # 占位，无实际文档页）。
 * 表单提交/校验/路由跳转逻辑保持完全不变。
 */
export function RegisterPage() {
  const navigate = useNavigate();
  const [form] = Form.useForm<{ email: string; phone?: string; password: string }>();
  const [loading, setLoading] = useState(false);
  const [agreed, setAgreed] = useState(false);

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

  // 占位提示：条款/政策文档页尚未上线，不实现实际跳转
  const openTerms = (e: MouseEvent<HTMLAnchorElement>) => {
    e.preventDefault();
    message.info('《服务条款》即将上线，敬请期待');
  };
  const openPrivacy = (e: MouseEvent<HTMLAnchorElement>) => {
    e.preventDefault();
    message.info('《隐私政策》即将上线，敬请期待');
  };

  return (
    <div className="auth-card">
      <Typography.Title level={3} className="auth-card__title" style={{ marginBottom: 8 }}>
        注册灵犀账号
      </Typography.Title>
      <Typography.Paragraph className="auth-card__sub">
        1 分钟创建账号，开启 7×24 智能客服体验
      </Typography.Paragraph>
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
          <Checkbox
            checked={agreed}
            onChange={(e) => setAgreed(e.target.checked)}
            className="auth-card__terms"
          >
            我已阅读并同意
            <a href="#" onClick={openTerms}>
              《服务条款》
            </a>
            和
            <a href="#" onClick={openPrivacy}>
              《隐私政策》
            </a>
          </Checkbox>
        </Form.Item>
        <Form.Item style={{ marginBottom: 8 }}>
          <Button
            type="primary"
            htmlType="submit"
            size="large"
            block
            loading={loading}
            disabled={!agreed}
          >
            注册并登录
          </Button>
        </Form.Item>
      </Form>
      <Typography.Paragraph type="secondary" className="auth-card__switch" style={{ margin: 0 }}>
        已有账号？<Link to="/login">去登录</Link>
      </Typography.Paragraph>
    </div>
  );
}

export default RegisterPage;
