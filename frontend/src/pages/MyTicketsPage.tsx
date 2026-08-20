import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Button, Card, Select, Spin, Typography } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { AppTable, StatusTag } from '@/components/common/AppTable';
import { BrandEmpty } from '@/components/common/BrandEmpty';
import { listMyTickets, subscribeMyTicketStream } from '@/api/tickets';
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
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<TicketStatus | ''>('');
  const [page, setPage] = useState(1);

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['my-tickets', status, page],
    queryFn: () => listMyTickets(status || undefined, page, 20),
    // 实时推送为主；30s 轮询作为兜底（第6组项4：推送断线/单 worker 漏事件时保底，不静默丢更新）
    refetchInterval: 30_000,
    // 兜底：网络异常时不直接抛 QueryErrorState（对 demo 模式用户体验差），
    // 而是当作"暂无数据"展示空态，再提供重试按钮供用户在网络恢复时刷新
    retry: 0,
  });

  // 订阅工单状态实时推送 → 命中即刷新列表（推送尽力而为；卸载时 cancel 释放连接）
  useEffect(() => {
    return subscribeMyTicketStream(() => {
      queryClient.invalidateQueries({ queryKey: ['my-tickets'] });
    });
  }, [queryClient]);

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
      <Card
        title="我的工单"
        className="my-tickets__card"
      >
        <div className="my-tickets__filter">
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
        ) : !data?.items?.length ? (
          <div className="my-tickets__empty">
            <BrandEmpty
              title="暂无工单"
              hint="需要人工服务时点击对话中的「转人工」即可创建工单"
            />
            <Button
              icon={<ReloadOutlined />}
              onClick={() => refetch()}
              className="my-tickets__retry"
            >
              刷新
            </Button>
          </div>
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
