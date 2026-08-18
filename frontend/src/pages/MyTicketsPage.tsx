import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ArrowLeftOutlined } from '@ant-design/icons';
import { Button, Card, Empty, Select, Spin, Typography } from 'antd';
import { useNavigate } from 'react-router-dom';
import { AppTable, StatusTag } from '@/components/common/AppTable';
import { QueryErrorState } from '@/components/common/QueryErrorState';
import { listMyTickets } from '@/api/tickets';
import type { TicketItem, TicketStatus } from '@/contracts/api';

const STATUS_OPTIONS = [
  { label: '全部', value: '' },
  { label: '待处理', value: 'open' as TicketStatus },
  { label: '处理中', value: 'processing' as TicketStatus },
  { label: '已解决', value: 'resolved' as TicketStatus },
  { label: '已关闭', value: 'closed' as TicketStatus },
];

/**
 * 我的工单（P2-1）：用户查看自己创建的工单（只读，无流转操作）。
 * 入口：转人工/工单气泡 + 顶栏「我的工单」。
 */
export function MyTicketsPage() {
  const navigate = useNavigate();
  const [status, setStatus] = useState<TicketStatus | ''>('');
  const [page, setPage] = useState(1);

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['my-tickets', status, page],
    queryFn: () => listMyTickets(status || undefined, page, 20),
    // V-5：用户侧工单状态短轮询 —— 客服处理后 30s 内自动刷新，无需手动刷新
    refetchInterval: 30_000,
  });

  const fmtTime = (iso: string) =>
    new Date(iso).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });

  const columns = [
    {
      title: '工单号',
      dataIndex: 'ticket_id',
      width: 140,
      render: (id: string) => <Typography.Text code>#{id.slice(0, 8)}</Typography.Text>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (s: TicketStatus) => <StatusTag status={s} />,
    },
    { title: '更新时间', dataIndex: 'updated_at', width: 160, render: (v: string) => fmtTime(v) },
  ];

  return (
    <div className="page">
      <Button
        type="text"
        icon={<ArrowLeftOutlined />}
        onClick={() => navigate('/chat')}
        className="my-tickets__back"
      >
        返回对话
      </Button>
      <Card title="我的工单" style={{ borderRadius: 16 }}>
        <div style={{ marginBottom: 12 }}>
          <Select
            style={{ width: 160 }}
            value={status}
            options={STATUS_OPTIONS}
            onChange={(v) => {
              setStatus(v);
              setPage(1);
            }}
            placeholder="按状态筛选"
          />
        </div>
        {isLoading ? (
          <div className="loading-spin">
            <Spin />
          </div>
        ) : isError || !data ? (
          <QueryErrorState title="工单加载失败" onRetry={() => refetch()} />
        ) : !data.items.length ? (
          <Empty description="暂无工单" style={{ padding: '32px 0' }}>
            <Typography.Link onClick={() => navigate('/chat')}>
              需要人工服务时点击对话中的「转人工」即可创建工单
            </Typography.Link>
          </Empty>
        ) : (
          <AppTable
            rowKey="ticket_id"
            columns={columns}
            dataSource={data.items as unknown as TicketItem[]}
            pagination={{
              current: page,
              pageSize: 20,
              total: data.total,
              onChange: setPage,
            }}
            onRow={(r) => ({
              onClick: () => navigate(`/chat?session=${(r as TicketItem).session_id}`),
              style: { cursor: 'pointer' },
            })}
          />
        )}
      </Card>
    </div>
  );
}

export default MyTicketsPage;
