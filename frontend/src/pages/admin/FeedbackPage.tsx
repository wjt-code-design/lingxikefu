import { useQuery } from '@tanstack/react-query';
import { Card, List, Space, Tag, Typography } from 'antd';
import { listFeedback } from '@/api/admin';
import { BrandEmpty } from '@/components/common/BrandEmpty';
import './FeedbackPage.css';

/**
 * 运营 · 反馈列表（D 决策落地）：只看"踩"（down）反馈，join 消息内容。
 * 运营据此判断：答错了（补录/优化）还是回答正确但用户不满（转人工引导）。
 */
export function FeedbackPage() {
  const { data, isLoading } = useQuery({
    queryKey: ['admin-feedback'],
    queryFn: () => listFeedback(1, 50),
  });
  const items = data?.items ?? [];

  return (
    <div className="page feedback-page">
      <div className="feedback-page__header">
        <Typography.Title level={3}>踩反馈（运营排查）</Typography.Title>
        <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
          用户点"踩"的消息内容——判断是答错（需补录/优化）还是答对但用户不满（引导转人工）。
        </Typography.Paragraph>
      </div>
      {isLoading && !items.length ? (
        <Card className="feedback-card" loading />
      ) : items.length === 0 ? (
        <Card className="feedback-card">
          <BrandEmpty title="暂无踩反馈" />
        </Card>
      ) : (
        <Card
          className="feedback-card"
          title={`踩反馈列表（${items.length}）`}
        >
          <List
            size="small"
            dataSource={items}
            renderItem={(f, i) => (
              <List.Item key={i}>
                <List.Item.Meta
                  title={
                    <Space size={8}>
                      <Tag color={f.role === 'user' ? 'blue' : 'green'}>
                        {f.role === 'user' ? '用户问题' : '客服回答'}
                      </Tag>
                      <Typography.Text type="secondary">
                        {new Date(f.created_at).toLocaleString('zh-CN')}
                      </Typography.Text>
                    </Space>
                  }
                  description={
                    <>
                      <Typography.Paragraph style={{ marginBottom: 0 }} ellipsis={{ rows: 3 }}>
                        {f.message_content}
                      </Typography.Paragraph>
                      {f.comment ? (
                        <span className="feedback-comment">
                          <Typography.Text type="danger">
                            用户评语：{f.comment}
                          </Typography.Text>
                        </span>
                      ) : null}
                    </>
                  }
                />
              </List.Item>
            )}
          />
        </Card>
      )}
    </div>
  );
}

export default FeedbackPage;
