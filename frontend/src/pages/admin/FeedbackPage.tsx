import { useQuery } from '@tanstack/react-query';
import { Card, List, Space, Tag, Typography } from 'antd';
import { listFeedback } from '@/api/admin';
import { listSuggestions } from '@/api/suggestions';
import { BrandEmpty } from '@/components/common/BrandEmpty';
import './FeedbackPage.css';

/** 意见反馈类型文案（SuggestionItem.type → 中文标签 + Tag 色） */
const SUGGESTION_META: Record<string, { text: string; color: string }> = {
  bug: { text: '问题反馈', color: 'red' },
  suggestion: { text: '功能建议', color: 'blue' },
  other: { text: '其他', color: 'default' },
};

/**
 * 运营 · 反馈列表（D 决策落地）：只看"踩"（down）反馈，join 消息内容。
 * 运营据此判断：答错了（补录/优化）还是回答正确但用户不满（转人工引导）。
 * P2-修复#2：追加"用户意见反馈"区块（FeedbackPage 提交，真实落库）。
 */
export function FeedbackPage() {
  const { data, isLoading } = useQuery({
    queryKey: ['admin-feedback'],
    queryFn: () => listFeedback(1, 50),
  });
  const { data: suggestionsData, isLoading: suggestionsLoading } = useQuery({
    queryKey: ['admin-suggestions'],
    queryFn: () => listSuggestions(1, 50),
  });
  const items = data?.items ?? [];
  const suggestions = suggestionsData?.items ?? [];

  return (
    <div className="page feedback-page page-atmo">
      <div className="feedback-page__header">
        <Typography.Title level={3}>反馈运营</Typography.Title>
        <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
          踩反馈定位答错还是不满；意见反馈跟进用户产品建议。
        </Typography.Paragraph>
      </div>

      {/* 意见反馈（FeedbackPage 提交，P2 起真实落库） */}
      {suggestionsLoading && !suggestions.length ? (
        <Card className="feedback-card" loading />
      ) : suggestions.length === 0 ? (
        <Card className="feedback-card">
          <BrandEmpty title="暂无意见反馈" hint="用户在「意见反馈」页提交的建议会出现在这里" />
        </Card>
      ) : (
        <Card className="feedback-card" title={`用户意见反馈（${suggestions.length}）`}>
          <List
            size="small"
            dataSource={suggestions}
            renderItem={(s) => {
              const meta = SUGGESTION_META[s.type] ?? SUGGESTION_META.other;
              return (
                <List.Item key={s.id}>
                  <List.Item.Meta
                    title={
                      <Space size={8} wrap>
                        <Tag color={meta.color}>{meta.text}</Tag>
                        <Typography.Text type="secondary">
                          {s.user_account || '未知用户'} · {new Date(s.created_at).toLocaleString('zh-CN')}
                        </Typography.Text>
                      </Space>
                    }
                    description={
                      <>
                        <Typography.Paragraph style={{ marginBottom: 0 }} ellipsis={{ rows: 3 }}>
                          {s.content}
                        </Typography.Paragraph>
                        {s.contact ? (
                          <span className="feedback-comment">
                            <Typography.Text type="secondary">联系方式：{s.contact}</Typography.Text>
                          </span>
                        ) : null}
                      </>
                    }
                  />
                </List.Item>
              );
            }}
          />
        </Card>
      )}
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
