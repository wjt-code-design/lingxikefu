import { useState } from 'react';
import { Button, Card, Form, Input, Radio, Typography, message } from 'antd';
import { ArrowLeftOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import type { ApiError } from '@/contracts/api';
import { submitSuggestion } from '@/api/suggestions';
import { useAuthStore } from '@/store/authStore';

const { Title, Text } = Typography;
const { TextArea } = Input;

export default function FeedbackPage() {
  const navigate = useNavigate();
  const role = useAuthStore((s) => s.role);
  const [form] = Form.useForm();
  const [submitting, setSubmitting] = useState(false);

  const home = role === 'admin' ? '/admin/dashboard' : role === 'agent' ? '/agent/dashboard' : '/chat';

  const onFinish = async (values: { type: 'bug' | 'suggestion' | 'other'; content: string; contact?: string }) => {
    setSubmitting(true);
    try {
      // P2-修复#2：真实落库（此前假提交，用户反馈全部丢弃）
      await submitSuggestion({ type: values.type, content: values.content, contact: values.contact });
      message.success('感谢反馈！我们会认真查看每一条建议');
      form.resetFields();
    } catch (e) {
      // 失败保留表单内容供用户重试（不清空）
      message.error((e as ApiError).message || '提交失败，请稍后重试');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="page" style={{ maxWidth: 720, margin: '0 auto', padding: '24px 16px' }}>
      <Button
        type="text"
        icon={<ArrowLeftOutlined />}
        onClick={() => navigate(home)}
        style={{ marginBottom: 16 }}
      >
        返回
      </Button>

      {/* a11y：页面主标题语义 h1（axe page-has-heading-one），视觉保持 24px */}
      <Title level={1} style={{ marginBottom: 8, fontSize: 24 }}>意见反馈</Title>
      <Text type="secondary" style={{ display: 'block', marginBottom: 24 }}>
        您的建议能帮助我们改进产品，所有反馈都会被认真对待
      </Text>

      <Card>
        <Form form={form} layout="vertical" onFinish={onFinish}>
          {/* role=radiogroup：antd 仅在该 div 注入 aria-required，补 role 后属性才合法（axe aria-allowed-attr）。
              antd RadioGroupProps 类型未声明 role，走 spread 透传（运行时落在根 div 上） */}
          <Form.Item name="type" label="反馈类型" initialValue="suggestion" rules={[{ required: true }]}>
            <Radio.Group {...({ role: 'radiogroup' } as const)}>
              <Radio.Button value="bug">问题反馈</Radio.Button>
              <Radio.Button value="suggestion">功能建议</Radio.Button>
              <Radio.Button value="other">其他</Radio.Button>
            </Radio.Group>
          </Form.Item>

          <Form.Item
            name="content"
            label="详细描述"
            rules={[{ required: true, message: '请输入反馈内容' }]}
          >
            <TextArea rows={6} placeholder="请描述您遇到的问题或建议..." maxLength={1000} showCount />
          </Form.Item>

          <Form.Item name="contact" label="联系方式（选填）">
            <Input placeholder="邮箱或手机号，方便我们联系您" />
          </Form.Item>

          <Form.Item>
            <Button type="primary" htmlType="submit" loading={submitting} block size="large">
              提交反馈
            </Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  );
}
