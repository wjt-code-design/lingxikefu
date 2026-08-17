import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Button, Card, Col, Empty, List, Row, Spin, Typography, message } from 'antd';
import { CopyOutlined, EditOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { getAdminStats, getStatsTrend } from '@/api/admin';
import { TrendChart } from '@/components/common/TrendChart';
import './StatsPage.css';

/** 迷你趋势线（近 14 天 sparkline）：KPI 卡装饰 + 数据语义，纯 SVG 自绘 */
function Sparkline({ data }: { data: number[] }) {
  if (data.length < 2) return null;
  const max = Math.max(...data, 1);
  const min = Math.min(...data);
  const range = max - min || 1;
  const W = 96;
  const H = 26;
  const pts = data
    .map((v, i) => {
      const px = (i * W) / (data.length - 1);
      const py = H - 3 - ((v - min) / range) * (H - 6);
      return `${px.toFixed(1)},${py.toFixed(1)}`;
    })
    .join(' ');
  return (
    <svg
      className="sparkline"
      viewBox={`0 0 ${W} ${H}`}
      width={W}
      height={H}
      aria-hidden="true"
      role="img"
    >
      <polyline
        points={pts}
        fill="none"
        stroke="var(--color-brand)"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** KPI 卡（自绘，白卡 + 发丝线 + tabular-nums 数字） */
function KpiCard({
  label,
  value,
  suffix,
  foot,
}: {
  label: string;
  value: number | string;
  suffix?: string;
  foot?: ReactNode;
}) {
  return (
    <Col xs={12} lg={6}>
      <div className="kpi-card">
        <div className="kpi-card__label">{label}</div>
        <div className="kpi-card__value">
          {value}
          {suffix ? <span className="kpi-card__suffix">{suffix}</span> : null}
        </div>
        {foot ? <div className="kpi-card__foot">{foot}</div> : null}
      </div>
    </Col>
  );
}

/**
 * 运营统计仪表盘（FE-04 + T8 + F1 落地）：React Query 统一服务端状态。
 * 布局：KPI 卡片行 → 左侧趋势图 + 右侧待补录 Top → 底部工单量分布。
 * F1：待补录问题 Top（handoff/refuse 高频问句）→ 运营补 KB 信号，点击复制问句。
 */
export function StatsPage() {
  const navigate = useNavigate();
  const { data: stats, isLoading } = useQuery({
    queryKey: ['admin-stats'],
    queryFn: getAdminStats,
  });
  // P1：近 14 天运营趋势（会话/消息/工单按日）
  const { data: trend } = useQuery({
    queryKey: ['admin-stats-trend'],
    queryFn: () => getStatsTrend(14),
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

  // ---- 从现有接口字段派生（不臆造新接口） ----
  const days = trend?.days ?? [];
  const sessions = stats?.sessions ?? 0;
  const messages = stats?.messages ?? 0;
  const documents = stats?.documents ?? 0;
  const up = stats?.feedback_up ?? 0;
  const down = stats?.feedback_down ?? 0;
  const upRatio = up + down > 0 ? (up / (up + down)) * 100 : 0;
  const avgMs = stats?.avg_first_token_ms ?? 0;
  const sumSessions14 = days.reduce((s, d) => s + d.sessions, 0);
  const sumMessages14 = days.reduce((s, d) => s + d.messages, 0);
  const sumTickets14 = days.reduce((s, d) => s + d.tickets, 0);
  const gaps = stats?.hot_gaps ?? [];

  return (
    <div className="page stats-page">
      <div className="stats-page__header">
        <div className="stats-page__head">
          <Typography.Title level={3} className="stats-page__title">运营统计</Typography.Title>
          <Typography.Text className="stats-page__subtitle">
            全站会话 / 消息 / 知识库 / 反馈概览
          </Typography.Text>
        </div>
      </div>

      {isLoading && !stats ? (
        <Spin className="stats-page__spin" />
      ) : (
        <>
          {/* ① 顶部 KPI 卡片行（4 张） */}
          <Row gutter={[16, 16]}>
            <KpiCard
              label="总会话数"
              value={sessions}
              foot={
                <>
                  <span className="kpi-card__caption">近 14 天 {sumSessions14} 会话</span>
                  <Sparkline data={days.map((d) => d.sessions)} />
                </>
              }
            />
            <KpiCard
              label="总消息数"
              value={messages}
              foot={
                <>
                  <span className="kpi-card__caption">近 14 天 {sumMessages14} 消息</span>
                  <Sparkline data={days.map((d) => d.messages)} />
                </>
              }
            />
            <KpiCard
              label="文档数"
              value={documents}
              suffix="篇"
              foot={<span className="kpi-card__caption">知识库文档总量</span>}
            />
            <KpiCard
              label="赞踩比"
              value={upRatio.toFixed(1)}
              suffix="%"
              foot={
                up + down > 0 ? (
                  <div className="kpi-card__ratio">
                    <div className="ratio-bar">
                      <span className="ratio-bar__up" style={{ width: `${upRatio}%` }} />
                      <span className="ratio-bar__down" style={{ width: `${100 - upRatio}%` }} />
                    </div>
                    <span className="kpi-card__caption">赞 {up} · 踩 {down}</span>
                  </div>
                ) : (
                  <span className="kpi-card__caption">暂无反馈数据</span>
                )
              }
            />
          </Row>

          {/* ② 中部两栏：左 2/3 消息量趋势 + 右 1/3 待补录问题 Top */}
          <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
            <Col xs={24} lg={16}>
              <Card
                className="stats-card stats-card--chart"
                title="近 14 天运营趋势"
                extra={<span className="stats-card__hint">会话 · 消息 · 工单（按日）</span>}
              >
                {days.length ? (
                  <TrendChart days={days} />
                ) : (
                  <Empty description="暂无趋势数据" image={Empty.PRESENTED_IMAGE_SIMPLE} />
                )}
              </Card>
            </Col>
            <Col xs={24} lg={8}>
              <Card
                className="stats-card stats-card--gaps"
                title="待补录问题 Top"
                extra={<span className="stats-card__hint">转人工 / 拒答高频</span>}
              >
                {gaps.length === 0 ? (
                  <Empty description="暂无待补录问题" image={Empty.PRESENTED_IMAGE_SIMPLE} />
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
                        <Typography.Text className="stats-gap">
                          <b className="stats-gap__rank">{i + 1}</b>
                          <span className="stats-gap__q">{g.question}</span>
                          <span className="stats-gap__count">（{g.count} 次）</span>
                        </Typography.Text>
                      </List.Item>
                    )}
                  />
                )}
              </Card>
            </Col>
          </Row>

          {/* ③ 底部：工单量分布（近 14 天，来自现有 trend 接口）+ 质量指标 */}
          <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
            <Col span={24}>
              <Card
                className="stats-card"
                title="工单量分布（近 14 天）"
                extra={<span className="stats-card__hint">按日工单量 · 来自现有接口</span>}
              >
                {days.length ? (
                  <div className="stats-quality">
                    <div className="ticket-bars" role="img" aria-label="近14天工单量柱状图">
                      {days.map((d) => {
                        const h = sumTickets14 > 0 ? Math.max((d.tickets / sumTickets14) * 100, 2) : 2;
                        return (
                          <div
                            key={d.date}
                            className="ticket-bars__col"
                            title={`${d.date}: ${d.tickets} 单`}
                          >
                            <div className="ticket-bars__bar" style={{ height: `${h}%` }} />
                            <span className="ticket-bars__label">{d.date.slice(5)}</span>
                          </div>
                        );
                      })}
                    </div>
                    <div className="stats-quality__meta">
                      <div className="metric">
                        <div className="metric__label">近 14 天工单</div>
                        <div className="metric__value">
                          {sumTickets14}
                          <span className="metric__unit">单</span>
                        </div>
                      </div>
                      <div className="metric">
                        <div className="metric__label">平均首字时延</div>
                        <div className="metric__value">
                          {avgMs}
                          <span className="metric__unit">ms</span>
                        </div>
                      </div>
                      <div className="metric">
                        <div className="metric__label">赞 / 踩</div>
                        <div className="metric__value">
                          {up}
                          <span className="metric__unit">赞</span>
                          {' · '}
                          {down}
                          <span className="metric__unit">踩</span>
                        </div>
                      </div>
                    </div>
                  </div>
                ) : (
                  <Empty description="暂无工单数据" image={Empty.PRESENTED_IMAGE_SIMPLE} />
                )}
              </Card>
            </Col>
          </Row>
        </>
      )}
    </div>
  );
}

export default StatsPage;
