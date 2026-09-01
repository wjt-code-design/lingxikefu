import { useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { Button, Form, Input, Typography, message } from 'antd';
import type { ApiError } from '@/contracts/api';
import { login, me } from '@/api/auth';
import { useAuthStore } from '@/store/authStore';

/**
 * 登录页（FE-02）：表单 → login() → 写入 token → me() 取档案 → 跳回来源页或 /chat。
 * 路由守卫 RequireAuth 将受保护路径存入 location.state.from（L6：登录后回到来源页）。
 * Phase 2 task 12：品牌化布局改造（左品牌区/右表单区，样式在 AuthLayout.css）；
 * 新增「忘记密码」占位链接（后端暂无密码重置流程，仅提示，不实现跳转）。
 * 表单提交/校验/路由跳转逻辑保持完全不变。
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
      // 有来源页且非登录页 → 回到来源页；否则按角色分流到对应首页
      if (from && from !== '/login') {
        navigate(from);
      } else {
        const home = meResp.role === 'admin' ? '/admin/dashboard' : meResp.role === 'agent' ? '/agent/dashboard' : '/chat';
        navigate(home);
      }
    } catch (e) {
      message.error((e as ApiError).message || '登录失败');
    } finally {
      setLoading(false);
    }
  };

  // 占位提示：后端暂无密码重置流程，不实现实际跳转
  // UI 审查低15：href="#" 死链语义 → Button type="link"（真按钮语义）
  const onForgotPassword = () => {
    message.info('密码重置功能开发中，敬请期待');
  };

  return (
    <div className="auth-card">
      {/* a11y：level={1} 渲染 h1（页面主标题语义），视觉字号由 .auth-card__title 控制 */}
      <Typography.Title level={1} className="auth-card__title">
        登录灵犀客服
      </Typography.Title>
      <Typography.Paragraph className="auth-card__sub">
        欢迎回来，7×24 随时为你解答
      </Typography.Paragraph>
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
        <div className="auth-card__extra">
          <Button type="link" onClick={onForgotPassword} className="auth-card__link">
            忘记密码？
          </Button>
        </div>
        <Form.Item style={{ marginBottom: 8 }}>
          <Button type="primary" htmlType="submit" size="large" block loading={loading}>
            登录
          </Button>
        </Form.Item>
      </Form>
      <Typography.Paragraph type="secondary" className="auth-card__switch" style={{ margin: 0 }}>
        还没有账号？<Link to="/register">立即注册</Link>
      </Typography.Paragraph>
    </div>
  );
}

export default LoginPage;
