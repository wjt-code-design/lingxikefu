import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Button, Card, Col, Empty, List, Row, Spin, Statistic, Typography, message } from 'antd';
import { CopyOutlined, EditOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { getAdminStats } from '@/api/admin';

function StatCard({ title, value }: { title: string; value: number | string }) {
  return (
    <Col xs={12} sm={8} md={6}>
      <Card>
        <Statistic title={title} value={value} />
      </Card>
    </Col>
  );
}

/**
 * 运营统计页（FE-04 + T8 + F1 落地）：React Query 统一服务端状态。
 * F1：待补录问题 Top10（handoff/refuse 高频问句）→ 运营补 KB 信号，点击复制问句。
 */
export function StatsPage() {
  const navigate = useNavigate();
  const { data: stats, isLoading } = useQuery({
    queryKey: ['admin-stats'],
    queryFn: getAdminStats,
  });
  const [copiedQ, setCopiedQ] = useState<string | null>(null);
  const copyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (copyTimerRef.current) clearTimeout(copyTimerRef.current); }, []);

  const copyQuestion = async (q: string) => {
    try {
      await navigator.clipboard?.writeText(q);
      setCopiedQ(q);
      message.success('已复制问题，可去知识库补录');
      if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
      copyTimerRef.current = setTimeout(() => setCopiedQ(null), 1500);
    } catch {
      /* 剪贴板不可用 → 静默 */
    }
  };

  const gaps = stats?.hot_gaps ?? [];

  return (
    <div className="page">
      <Typography.Title level={3}>运营统计</Typography.Title>
      {isLoading && !stats ? (
        <Spin />
      ) : (
        <>
          <Row gutter={[16, 16]}>
            <StatCard title="总会话数" value={stats?.sessions ?? 0} />
            <StatCard title="总消息数" value={stats?.messages ?? 0} />
            <StatCard title="文档数" value={stats?.documents ?? 0} />
            <StatCard title="赞" value={stats?.feedback_up ?? 0} />
            <StatCard title="踩" value={stats?.feedback_down ?? 0} />
            <StatCard title="平均首字时延(ms)" value={stats?.avg_first_token_ms ?? 0} />
          </Row>
          <Card title="待补录问题 Top10（转人工/拒答高频）" style={{ marginTop: 16 }}>
            {gaps.length === 0 ? (
              <Empty description="暂无待补录问题" />
            ) : (
              <List
                size="small"
                dataSource={gaps}
                renderItem={(g, i) => (
                  <List.Item
                    actions={[
                      <Button
                        key="copy"
                        type="link"
                        size="small"
                        icon={<CopyOutlined />}
                        onClick={() => copyQuestion(g.question)}
                      >
                        {copiedQ === g.question ? '已复制' : '复制'}
                      </Button>,
                      <Button
                        key="supply"
                        type="link"
                        size="small"
                        icon={<EditOutlined />}
                        onClick={() => navigate('/admin/knowledge')}
                      >
                        去补录
                      </Button>,
                    ]}
                  >
                    <Typography.Text>
                      <b>{i + 1}.</b> {g.question}
                    </Typography.Text>
                    <Typography.Text type="secondary">（{g.count} 次）</Typography.Text>
                  </List.Item>
                )}
              />
            )}
          </Card>
        </>
      )}
    </div>
  );
}

export default StatsPage;
