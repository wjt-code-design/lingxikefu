import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Button, Card, Empty, Result, Space, Statistic, Table, Tag, Typography, message } from 'antd';
import { BarChartOutlined, PlayCircleOutlined, ReloadOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { getEvalHistory, getEvalLatest, runEval } from '@/api/eval';
import './EvalPage.css';

const { Title, Paragraph, Text } = Typography;

interface EvalMetric {
  metric: string;
  score: number;
  passed: number;
  total: number;
}

interface EvalLatestResp {
  has_history: boolean;
  latest: {
    run_id: string;
    metrics: EvalMetric[];
  } | null;
  alerts: string[];
}

const METRIC_LABELS: Record<string, string> = {
  faithfulness: '忠实度',
  recall: '召回率',
  citation: '引用合法率',
  refuse: '诚实拒答率',
  refuse_qa: '误拒答率',
  handoff: '转人工率',
  chitchat: '闲聊引导率',
};

function scoreColor(score: number): string {
  if (score >= 0.85) return 'success';
  if (score >= 0.7) return 'warning';
  return 'error';
}

export function EvalPage() {
  const qc = useQueryClient();

  const { data: history, isLoading: historyLoading } = useQuery({
    queryKey: ['eval-history'],
    queryFn: getEvalHistory,
    placeholderData: (prev) => prev,
  });

  const { data: latest } = useQuery<EvalLatestResp>({
    queryKey: ['eval-latest'],
    queryFn: getEvalLatest,
    placeholderData: (prev) => prev,
  });

  const triggerMutation = useMutation({
    mutationFn: (params: { limit?: number }) => runEval(params),
    onSuccess: () => {
      message.success('评测已启动，请稍后刷新');
      setTimeout(() => {
        qc.invalidateQueries({ queryKey: ['eval-history'] });
        qc.invalidateQueries({ queryKey: ['eval-latest'] });
      }, 5000);
    },
    onError: () => {
      message.error('启动评测失败');
    },
  });

  const columns: ColumnsType<{
    run_id: string;
    metric: string;
    score: number;
    total: number;
    passed: number;
    status: string;
    source: string;
    created_at: string;
  }> = [
    {
      title: '时间',
      dataIndex: 'created_at',
      key: 'created_at',
      render: (v: string) => new Date(v).toLocaleString('zh-CN'),
    },
    {
      title: '指标',
      dataIndex: 'metric',
      key: 'metric',
      render: (v: string) => METRIC_LABELS[v] ?? v,
    },
    {
      title: '分数',
      dataIndex: 'score',
      key: 'score',
      render: (v: number) => (
        <Tag color={scoreColor(v)}>{(v * 100).toFixed(1)}%</Tag>
      ),
    },
    {
      title: '通过/总数',
      key: 'pass_total',
      render: (_, r) => `${r.passed}/${r.total}`,
    },
    {
      title: '来源',
      dataIndex: 'source',
      key: 'source',
    },
  ];

  return (
    <div className="eval-page page-atmo">
      <Typography>
        <Title level={3}>
          <BarChartOutlined style={{ marginRight: 8 }} />
          评测中心
        </Title>
        <Paragraph type="secondary">
          RAG 管线质量监控：忠实度 / 召回率 / 引用合法率。手动触发评测，历史趋势一目了然。
        </Paragraph>
      </Typography>

      {/* 告警区 */}
      {latest?.alerts && latest.alerts.length > 0 && (
        <div className="eval-alerts">
          {latest.alerts.map((a, i) => (
            <Result
              key={i}
              status="warning"
              title="质量退化警告"
              subTitle={a}
            />
          ))}
        </div>
      )}

      {/* 最新指标卡片 */}
      <div className="eval-stats">
        {latest?.latest?.metrics.map((m) => (
          <Card key={m.metric} className="eval-stat-card">
            <Statistic
              title={METRIC_LABELS[m.metric] ?? m.metric}
              value={m.score * 100}
              precision={1}
              suffix="%"
              valueStyle={{
                color: m.score >= 0.85 ? '#3f8600' : m.score >= 0.7 ? '#faad14' : '#cf1322',
              }}
            />
            <Text type="secondary">
              {m.passed}/{m.total} 通过
            </Text>
          </Card>
        ))}
        {!latest?.has_history && (
          <Empty description="暂无评测数据" className="eval-empty" />
        )}
      </div>

      {/* 操作区 */}
      <div className="eval-actions">
        <Space>
          <Button
            type="primary"
            icon={<PlayCircleOutlined />}
            loading={triggerMutation.isPending}
            onClick={() => triggerMutation.mutate({ limit: 0 })}
          >
            运行全部评测
          </Button>
          <Button
            icon={<ReloadOutlined />}
            onClick={() => {
              qc.invalidateQueries({ queryKey: ['eval-history'] });
              qc.invalidateQueries({ queryKey: ['eval-latest'] });
            }}
          >
            刷新
          </Button>
        </Space>
      </div>

      {/* 历史表格 */}
      <Card title="历史记录" className="eval-history-card">
        <Table
          loading={historyLoading}
          dataSource={history?.items ?? []}
          columns={columns}
          rowKey={(r) => `${r.run_id}-${r.metric}`}
          pagination={{ pageSize: 20 }}
        />
      </Card>
    </div>
  );
}

export default EvalPage;
