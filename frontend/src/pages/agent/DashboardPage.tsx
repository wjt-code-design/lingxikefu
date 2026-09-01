import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Button, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useNavigate } from 'react-router-dom';
import { AppTable, StatusTag } from '@/components/common/AppTable';
import { BrandEmpty } from '@/components/common/BrandEmpty';
import { KpiCard } from '@/components/common/KpiCard';
import { KpiCardSkeleton, TableSkeleton } from '@/components/common/Skeleton';
import { listTickets } from '@/api/tickets';
import { listSessions } from '@/api/sessions';
import type { Session, TicketItem, TicketStatus } from '@/contracts/api';
import './DashboardPage.css';

const STATUS_TEXT: Record<TicketStatus, string> = {
  open: '待处理',
  processing: '处理中',
  resolved: '已解决',
  closed: '已关闭',
};

/**
 * 客服工作台首页（Phase3）：今日待办工单 + 实时会话 + 个人接待统计。
 * - 统计：listTickets 全量拉一次分状态计数（open/processing/resolved）
 * - 待办工单：open/processing 优先展示，工单号前 8 位 + StatusTag + 关联会话跳转
 * - 最近会话：listSessions，点击进入 /chat?session=id
 * 数据统一 @tanstack/react-query，空态用 BrandEmpty。
 */
export function DashboardPage() {
  const navigate = useNavigate();

  const { data: ticketsData, isLoading: ticketsLoading } = useQuery({
    queryKey: ['agent-dashboard-tickets'],
    queryFn: () => listTickets(undefined, 1, 200),
  });
  const { data: sessionsData, isLoading: sessionsLoading } = useQuery({
    queryKey: ['agent-dashboard-sessions'],
    queryFn: () => listSessions({ page: 1, size: 50 }),
  });

  const tickets: TicketItem[] = ticketsData?.items ?? [];

  // 分状态统计
  const stats = useMemo(() => {
    let open = 0;
    let processing = 0;
    let resolved = 0;
    for (const t of tickets) {
      if (t.status === 'open') open += 1;
      else if (t.status === 'processing') processing += 1;
      else if (t.status === 'resolved') resolved += 1;
    }
    return { open, processing, resolved, todo: open + processing };
  }, [tickets]);

  const todoTickets = useMemo(
    () => tickets.filter((t) => t.status === 'open' || t.status === 'processing'),
    [tickets]
  );

  // 最近会话（按创建时间倒序）
  const sessions = useMemo(() => {
    const items = sessionsData?.items ?? [];
    return [...items].sort(
      (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
    );
  }, [sessionsData]);

  // 今日会话数：从最近会话列表中按 created_at 过滤当天
  const todaySessions = useMemo(() => {
    const now = new Date();
    const y = now.getFullYear();
    const m = now.getMonth();
    const d = now.getDate();
    return sessions.filter((s) => {
      const t = new Date(s.created_at);
      return t.getFullYear() === y && t.getMonth() === m && t.getDate() === d;
    }).length;
  }, [sessions]);

  const ticketColumns: ColumnsType<TicketItem> = [
    {
      title: '工单号',
      dataIndex: 'ticket_id',
      width: 120,
      render: (v: string) => <Typography.Text code>{v.slice(0, 8)}</Typography.Text>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (s: TicketStatus) => <StatusTag status={s} text={STATUS_TEXT[s]} />,
    },
    {
      title: '关联会话',
      dataIndex: 'session_id',
      render: (v: string) => (
        <Typography.Link onClick={() => navigate(`/chat?session=${v}`)}>查看对话</Typography.Link>
      ),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      width: 170,
      render: (v: string) => new Date(v).toLocaleString('zh-CN'),
    },
  ];

  const sessionColumns: ColumnsType<Session> = [
    {
      title: '标题',
      dataIndex: 'title',
      render: (v: string | undefined) => v || '（未命名会话）',
    },
    {
      title: '客户',
      dataIndex: 'user_email',
      width: 180,
      render: (v: string | undefined) => v || '—',
    },
    {
      title: '时间',
      dataIndex: 'created_at',
      width: 170,
      render: (v: string) => new Date(v).toLocaleString('zh-CN'),
    },
  ];

  const isLoading = ticketsLoading || sessionsLoading;

  return (
    <div className="dash-page">
      <div className="dash-page__header">
        <Typography.Title level={1} className="dash-page__title">
          客服工作台
        </Typography.Title>
        <Typography.Text className="dash-page__subtitle">
          今日待办、实时会话与个人接待概览
        </Typography.Text>
      </div>

      {isLoading ? (
        <>
          <div className="dash-kpis">
            {Array.from({ length: 4 }).map((_, i) => (
              <KpiCardSkeleton key={i} />
            ))}
          </div>
          <div className="dash-grid">
            <section className="dash-card">
              <header className="dash-card__head">
                {/* a11y：level 2（视觉字号由 .dash-card__title pin 14px）——h1 下 h5 跳级 axe heading-order */}
                <Typography.Title level={2} className="dash-card__title">
                  待办工单
                </Typography.Title>
                <span className="dash-card__count">—</span>
              </header>
              <div className="dash-card__body">
                <TableSkeleton rows={5} columns={3} />
              </div>
            </section>
            <section className="dash-card">
              <header className="dash-card__head">
                <Typography.Title level={2} className="dash-card__title">
                  最近会话
                </Typography.Title>
                <span className="dash-card__count">—</span>
              </header>
              <div className="dash-card__body">
                <TableSkeleton rows={5} columns={3} />
              </div>
            </section>
          </div>
        </>
      ) : (
        <>
          <div className="dash-kpis">
            {/* UI 审查中8：KPI 副文案去中英混杂、口径说明化 */}
            <KpiCard label="待办工单" value={stats.todo} caption="待处理 + 处理中" accent="warning" />
            <KpiCard label="处理中" value={stats.processing} accent="brand" />
            <KpiCard label="已解决" value={stats.resolved} accent="success" />
            <KpiCard
              label="今日会话"
              value={todaySessions}
              caption={`统计自最近 ${sessions.length} 条会话`}
              accent="brand"
            />
          </div>

          <div className="dash-grid">
            <section className="dash-card">
              <header className="dash-card__head">
                <Typography.Title level={2} className="dash-card__title">
                  待办工单
                </Typography.Title>
                <span className="dash-card__count">{todoTickets.length}</span>
              </header>
              <div className="dash-card__body">
                {todoTickets.length === 0 ? (
                  <BrandEmpty title="暂无待办工单" hint="新工单出现后会在这里提醒你" />
                ) : (
                  <AppTable<TicketItem>
                    rowKey="ticket_id"
                    columns={ticketColumns}
                    dataSource={todoTickets}
                    pagination={false}
                    scroll={{ y: 320 }}
                  />
                )}
              </div>
            </section>

            <section className="dash-card">
              <header className="dash-card__head">
                <Typography.Title level={2} className="dash-card__title">
                  最近会话
                </Typography.Title>
                <span className="dash-card__count">{sessions.length}</span>
              </header>
              <div className="dash-card__body">
                {sessions.length === 0 ? (
                  <BrandEmpty title="暂无会话" hint="用户发起对话后会出现在这里" />
                ) : (
                  <AppTable<Session>
                    rowKey="id"
                    columns={sessionColumns}
                    dataSource={sessions}
                    pagination={false}
                    scroll={{ y: 320 }}
                    onRow={(record) => ({
                      onClick: () => navigate(`/chat?session=${record.id}`),
                      style: { cursor: 'pointer' },
                    })}
                  />
                )}
              </div>
            </section>
          </div>

          <section className="dash-card dash-quick">
            <header className="dash-card__head">
              <Typography.Title level={2} className="dash-card__title">
                快捷操作
              </Typography.Title>
            </header>
            <div className="dash-quick__actions">
              <Button onClick={() => navigate('/agent/tickets')}>工单管理</Button>
              <Button onClick={() => navigate('/agent/sessions')}>会话记录</Button>
              <Button onClick={() => navigate('/agent/kb-search')}>知识库快搜</Button>
            </div>
          </section>
        </>
      )}
    </div>
  );
}

export default DashboardPage;
