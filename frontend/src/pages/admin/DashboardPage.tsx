import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Button, Card, Col, List, Row, Spin, Typography, message } from 'antd';
import {
  CopyOutlined,
  DatabaseOutlined,
  DislikeOutlined,
  LineChartOutlined,
  TeamOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { getAdminStats, getStatsTrend } from '@/api/admin';
import { listTickets } from '@/api/tickets';
import { StatusTag } from '@/components/common/AppTable';
import { BrandEmpty } from '@/components/common/BrandEmpty';
import { KpiCard } from '@/components/common/KpiCard';
import type { TicketItem, TicketStatus } from '@/contracts/api';
import './DashboardPage.css';

const STATUS_TEXT: Record<TicketStatus, string> = {
  open: '待处理',
  processing: '处理中',
  resolved: '已解决',
  closed: '已关闭',
};

const QUICK_LINKS = [
  {
    key: 'stats',
    title: '运营统计',
    desc: '会话 / 消息 / 趋势 / 待补录',
    to: '/admin/stats',
    icon: <LineChartOutlined />,
  },
  {
    key: 'knowledge',
    title: '知识库',
    desc: '文档管理 · 补录问题',
    to: '/admin/knowledge',
    icon: <DatabaseOutlined />,
  },
  {
    key: 'users',
    title: '用户管理',
    desc: '账号与角色分配',
    to: '/admin/users',
    icon: <TeamOutlined />,
  },
  {
    key: 'feedback',
    title: '踩反馈',
    desc: '运营排查回答质量',
    to: '/admin/feedback',
    icon: <DislikeOutlined />,
  },
];

/** 迷你趋势线（近 14 天 sparkline）：KPI 卡装饰，纯 SVG 自绘。 */
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

/** KPI 卡已收敛为公共组件（components/common/KpiCard，样式在 globals.css .kpi-card）。 */

/**
 * 运营总览仪表盘（替代原 /admin/dashboard 重定向，Phase3）。
 * 布局：今日 KPI 卡行 → 左「待办工单」+ 右「告警」→ 底部「快捷入口」。
 * 数据统一 React Query；KPI/Sparkline 页面内私有实现；空态用 BrandEmpty。
 */
export function DashboardPage() {
  const navigate = useNavigate();
  const { data: stats, isLoading: statsLoading } = useQuery({
    queryKey: ['admin-dash-stats'],
    queryFn: getAdminStats,
  });
  const { data: trend } = useQuery({
    queryKey: ['admin-dash-trend'],
    queryFn: () => getStatsTrend(14),
  });
  // 待办工单：open + processing 分别拉取，前端合并去重
  const { data: openTickets, isLoading: openLoading } = useQuery({
    queryKey: ['admin-dash-tickets-open'],
    queryFn: () => listTickets('open', 1, 50),
  });
  const { data: procTickets, isLoading: procLoading } = useQuery({
    queryKey: ['admin-dash-tickets-processing'],
    queryFn: () => listTickets('processing', 1, 50),
  });

  const [copiedQ, setCopiedQ] = useState<string | null>(null);
  const copyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
    },
    []
  );

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

  const days = trend?.days ?? [];
  const sessions = stats?.sessions ?? 0;
  const messages = stats?.messages ?? 0;
  const up = stats?.feedback_up ?? 0;
  const down = stats?.feedback_down ?? 0;
  const avgMs = stats?.avg_first_token_ms ?? 0;
  const gaps = stats?.hot_gaps ?? [];

  // 待办工单：open + processing 合并去重、按创建时间倒序取前 8
  const mergedTickets = useMemo(() => {
    const seen = new Set<string>();
    const out: TicketItem[] = [];
    for (const t of [...(openTickets?.items ?? []), ...(procTickets?.items ?? [])]) {
      if (!seen.has(t.ticket_id)) {
        seen.add(t.ticket_id);
        out.push(t);
      }
    }
    return out;
  }, [openTickets, procTickets]);
  const pendingTotal = mergedTickets.length;
  const pendingList = useMemo(
    () =>
      [...mergedTickets]
        .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))
        .slice(0, 8),
    [mergedTickets]
  );

  const sumSessions14 = days.reduce((s, d) => s + d.sessions, 0);
  const sumMessages14 = days.reduce((s, d) => s + d.messages, 0);
  const sumTickets14 = days.reduce((s, d) => s + d.tickets, 0);

  // 告警：首 token 延迟偏高（>3000ms）+ 高频未命中问题前 5（点击复制）
  const alerts = useMemo(() => {
    const out: Array<{ key: string; kind: 'latency' | 'gap'; title: string; desc: string; copy?: string }> = [];
    if (avgMs > 3000) {
      out.push({
        key: 'latency',
        kind: 'latency',
        title: '首 token 延迟偏高',
        desc: `平均首字时延 ${avgMs}ms（阈值 3000ms）`,
      });
    }
    gaps.slice(0, 5).forEach((g, i) => {
      out.push({
        key: `gap-${i}`,
        kind: 'gap',
        title: '高频未命中问题，建议补录',
        desc: `「${g.question}」× ${g.count} 次`,
        copy: g.question,
      });
    });
    return out;
  }, [avgMs, gaps]);

  const ticketsLoading = openLoading || procLoading;

  return (
    <div className="page dash-page">
      <div className="dash-page__header">
        <div className="dash-page__head">
          <Typography.Title level={3} className="dash-page__title">运营总览</Typography.Title>
          <Typography.Text className="dash-page__subtitle">
            今日 KPI · 待办工单 · 告警 · 快捷入口
          </Typography.Text>
        </div>
      </div>

      {statsLoading && !stats ? (
        <Spin className="dash-page__spin" />
      ) : (
        <>
          {/* ① 今日概览 KPI 卡片行（会话 / 消息 / 待办工单 / 反馈） */}
          <Row gutter={[16, 16]}>
            <Col xs={12} lg={6}>
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
            </Col>
            <Col xs={12} lg={6}>
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
            </Col>
            <Col xs={12} lg={6}>
              <KpiCard
                label="待办工单"
                value={pendingTotal}
                suffix="单"
                foot={<span className="kpi-card__caption">近 14 天 {sumTickets14} 工单</span>}
              />
            </Col>
            <Col xs={12} lg={6}>
              <KpiCard
                label="反馈总数"
                value={up + down}
                suffix="条"
                foot={<span className="kpi-card__caption">赞 {up} · 踩 {down}</span>}
              />
            </Col>
          </Row>

          {/* ② 中部两栏：左 待办工单 + 右 告警 */}
          <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
            <Col xs={24} lg={15}>
              <Card
                className="dash-card"
                title="待办工单"
                extra={<span className="dash-card__hint">待处理 · 处理中</span>}
              >
                {ticketsLoading && !pendingList.length ? (
                  <Spin className="dash-card__spin" />
                ) : pendingList.length === 0 ? (
                  <BrandEmpty title="暂无待办工单" hint="新的转人工 / 工单会出现在这里" />
                ) : (
                  <List
                    size="small"
                    className="dash-ticket-list"
                    dataSource={pendingList}
                    renderItem={(t) => (
                      <List.Item
                        className="dash-ticket"
                        onClick={() => navigate(`/chat?session=${t.session_id}`)}
                      >
                        <div className="dash-ticket__main">
                          <span className="dash-ticket__id">{t.ticket_id.slice(0, 8)}</span>
                          <StatusTag status={t.status} text={STATUS_TEXT[t.status]} />
                        </div>
                        <span className="dash-ticket__time">
                          {new Date(t.created_at).toLocaleString('zh-CN')}
                        </span>
                      </List.Item>
                    )}
                  />
                )}
              </Card>
            </Col>
            <Col xs={24} lg={9}>
              <Card
                className="dash-card dash-card--alerts"
                title="告警"
                extra={<span className="dash-card__hint">需关注</span>}
              >
                {alerts.length === 0 ? (
                  <BrandEmpty title="暂无告警" hint="系统运行平稳" />
                ) : (
                  <List
                    size="small"
                    dataSource={alerts}
                    renderItem={(a) => (
                      <List.Item
                        actions={
                          a.copy
                            ? [
                                <Button
                                  key="copy"
                                  type="link"
                                  size="small"
                                  icon={<CopyOutlined />}
                                  onClick={() => copyQuestion(a.copy as string)}
                                >
                                  {copiedQ === a.copy ? '已复制' : '复制'}
                                </Button>,
                              ]
                            : undefined
                        }
                      >
                        <div className={`dash-alert dash-alert--${a.kind}`}>
                          <div className="dash-alert__icon" aria-hidden="true">!</div>
                          <div className="dash-alert__body">
                            <div className="dash-alert__title">{a.title}</div>
                            <div className="dash-alert__desc">{a.desc}</div>
                          </div>
                        </div>
                      </List.Item>
                    )}
                  />
                )}
              </Card>
            </Col>
          </Row>

          {/* ③ 快捷入口 */}
          <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
            {QUICK_LINKS.map((q) => (
              <Col xs={12} sm={12} lg={6} key={q.key}>
                <div
                  className="dash-quick"
                  role="link"
                  tabIndex={0}
                  onClick={() => navigate(q.to)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') navigate(q.to);
                  }}
                >
                  <div className="dash-quick__icon">{q.icon}</div>
                  <div className="dash-quick__title">{q.title}</div>
                  <div className="dash-quick__desc">{q.desc}</div>
                </div>
              </Col>
            ))}
          </Row>
        </>
      )}
    </div>
  );
}

export default DashboardPage;
