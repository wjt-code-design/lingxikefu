import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Alert, Button, Checkbox, Form, Input, Typography, message } from 'antd';
import type { ApiError } from '@/contracts/api';
import { me, register } from '@/api/auth';
import { useAuthStore } from '@/store/authStore';

/**
 * 注册页（FE-02）：邮箱 + 密码（手机号可选）→ register() → 自动登录 → 跳 /chat。
 * Phase 2 task 12：品牌化布局改造（左品牌区/右表单区，样式在 AuthLayout.css）；
 * 新增「已阅读并同意《服务条款》《隐私政策》」勾选框，勾选后才允许提交
 * （条款/政策链接为 # 占位，无实际文档页）。
 * UI 审查 2026-08-31 高1：失败反馈改为「持久内联 Alert + toast」双通道——toast 3s
 * 自动消失，自动化/慢速浏览场景下用户对结果完全不可见；Alert 持久展示直到下次提交。
 */
export function RegisterPage() {
  const navigate = useNavigate();
  const [form] = Form.useForm<{ email: string; phone?: string; password: string }>();
  const [loading, setLoading] = useState(false);
  const [agreed, setAgreed] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onFinish = async (values: { email: string; phone?: string; password: string }) => {
    setLoading(true);
    setError(null);
    try {
      // 后端 register 直接返回 AuthResp（含 token），无需二次登录请求
      const resp = await register({ email: values.email, phone: values.phone, password: values.password });
      useAuthStore.setState({
        token: resp.access_token,
        refreshToken: resp.refresh_token,
      });
      const meResp = await me();
      useAuthStore.getState().setUser(meResp);
      message.success('注册成功，已自动登录');
      navigate('/chat');
    } catch (e) {
      const msg = (e as ApiError).message || '注册失败，请稍后重试';
      setError(msg);
      message.error(msg);
    } finally {
      setLoading(false);
    }
  };

  // 占位提示：条款/政策文档页尚未上线，不实现实际跳转
  // UI 审查低15：href="#" 死链语义 → Button type="link"（真按钮语义，可聚焦可键盘操作）
  const openTerms = () => {
    message.info('《服务条款》即将上线，敬请期待');
  };
  const openPrivacy = () => {
    message.info('《隐私政策》即将上线，敬请期待');
  };

  return (
    <div className="auth-card">
      {/* a11y：level={1} 渲染 h1（页面主标题语义），视觉字号由 .auth-card__title 控制 */}
      <Typography.Title level={1} className="auth-card__title">
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
          {/* UI 审查低14：type="email" —— 移动端弹邮箱键盘 + 原生表单语义 */}
          <Input size="large" type="email" placeholder="you@example.com" autoComplete="email" />
        </Form.Item>
        <Form.Item name="phone" label="手机号（可选）">
          <Input size="large" placeholder="手机号" autoComplete="tel" />
        </Form.Item>
        <Form.Item
          name="password"
          label="密码"
          rules={[
            { required: true, message: '请输入密码' },
            { min: 8, message: '密码至少 8 位' },
            {
              pattern: /^(?=.*[A-Za-z])(?=.*\d).+$/,
              message: '密码需同时包含字母和数字',
            },
          ]}
        >
          <Input.Password size="large" placeholder="至少 8 位，含字母和数字" autoComplete="new-password" />
        </Form.Item>
        <Form.Item style={{ marginBottom: 8 }}>
          <Checkbox
            checked={agreed}
            onChange={(e) => setAgreed(e.target.checked)}
            className="auth-card__terms"
          >
            我已阅读并同意
            <Button type="link" onClick={openTerms} className="auth-card__link">
              《服务条款》
            </Button>
            和
            <Button type="link" onClick={openPrivacy} className="auth-card__link">
              《隐私政策》
            </Button>
          </Checkbox>
        </Form.Item>
        {error && (
          <Form.Item style={{ marginBottom: 8 }}>
            <Alert type="error" showIcon message={error} role="alert" />
          </Form.Item>
        )}
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
