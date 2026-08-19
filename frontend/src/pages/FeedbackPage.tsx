import { useState } from 'react';
import { Button, Card, Form, Input, Radio, Typography, message } from 'antd';
import { ArrowLeftOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { useAuthStore } from '@/store/authStore';

const { Title, Text } = Typography;
const { TextArea } = Input;

export default function FeedbackPage() {
  const navigate = useNavigate();
  const role = useAuthStore((s) => s.role);
  const [form] = Form.useForm();
  const [submitting, setSubmitting] = useState(false);

  const home = role === 'admin' ? '/admin/dashboard' : role === 'agent' ? '/agent/dashboard' : '/chat';

  const onFinish = async (_values: { type: string; content: string; contact?: string }) => {
    setSubmitting(true);
    // 演示阶段：仅本地提示，不真正提交
    await new Promise((r) => setTimeout(r, 800));
    message.success('感谢反馈！我们会认真查看每一条建议');
    form.resetFields();
    setSubmitting(false);
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

      <Title level={2} style={{ marginBottom: 8 }}>意见反馈</Title>
      <Text type="secondary" style={{ display: 'block', marginBottom: 24 }}>
        您的建议能帮助我们改进产品，所有反馈都会被认真对待
      </Text>

      <Card>
        <Form form={form} layout="vertical" onFinish={onFinish}>
          <Form.Item name="type" label="反馈类型" initialValue="suggestion" rules={[{ required: true }]}>
            <Radio.Group>
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
