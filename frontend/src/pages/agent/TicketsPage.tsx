import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Select, Space, Typography, message } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useNavigate } from 'react-router-dom';
import { AppTable, StatusTag } from '@/components/common/AppTable';
import { listTickets, updateTicket } from '@/api/tickets';
import type { ApiError, TicketItem, TicketStatus } from '@/contracts/api';

const STATUS_OPTIONS = [
  { label: '全部', value: '' },
  { label: '待处理', value: 'open' as TicketStatus },
  { label: '处理中', value: 'processing' as TicketStatus },
  { label: '已解决', value: 'resolved' as TicketStatus },
  { label: '已关闭', value: 'closed' as TicketStatus },
];

const STATUS_TEXT: Record<TicketStatus, string> = {
  open: '待处理',
  processing: '处理中',
  resolved: '已解决',
  closed: '已关闭',
};

/** 合法迁移（与后端 tickets.py _ALLOWED_TRANSITIONS 对齐） */
const NEXT_STATUS: Record<TicketStatus, TicketStatus[]> = {
  open: ['processing', 'closed'],
  processing: ['resolved', 'closed'],
  resolved: ['closed'],
  closed: [],
};

/**
 * 工单列表（T1 + T8 落地）：React Query 统一服务端状态。
 * - status/page 进 queryKey（筛选/翻页驱动 refetch，keepPreviousData 防闪烁）
 * - 流转后 invalidate 刷新（缓存一致）
 */
export function TicketsPage() {
  const navigate = useNavigate();
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState<TicketStatus | undefined>(undefined);
  const queryClient = useQueryClient();

  const { data, isFetching } = useQuery({
    queryKey: ['tickets', status, page],
    queryFn: () => listTickets(status, page, 20),
    placeholderData: (prev) => prev,
  });

  const changeStatus = (t: TicketItem, next: TicketStatus) => {
    // S2 乐观锁：回传当前 version，冲突 409 时提示并刷新最新状态
    updateTicket(t.ticket_id, { status: next, version: t.version })
      .then((r) => {
        message.success(`已更新为「${STATUS_TEXT[r.status]}」`);
        queryClient.invalidateQueries({ queryKey: ['tickets'] });
      })
      .catch((e: unknown) => {
        const err = e as ApiError;
        if (err.code === '409') {
          message.error('工单已被其他客服更新，已刷新最新状态，请重试');
          queryClient.invalidateQueries({ queryKey: ['tickets'] });
        } else {
          message.error(err.message || '更新失败');
        }
      });
  };

  const columns: ColumnsType<TicketItem> = [
    {
      title: '工单号',
      dataIndex: 'ticket_id',
      width: 120,
      render: (v: string) => <Typography.Text code>{v.slice(0, 8)}</Typography.Text>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
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
      width: 190,
      render: (v: string) => new Date(v).toLocaleString('zh-CN'),
    },
    {
      title: '操作',
      key: 'action',
      width: 140,
      render: (_, t) =>
        NEXT_STATUS[t.status].length ? (
          <Select<TicketStatus>
            style={{ width: 110 }}
            placeholder="流转"
            value={undefined}
            onChange={(n) => changeStatus(t, n)}
            options={NEXT_STATUS[t.status].map((s) => ({ label: `→ ${STATUS_TEXT[s]}`, value: s }))}
          />
        ) : (
          <Typography.Text type="secondary">已关闭</Typography.Text>
        ),
    },
  ];

  return (
    <div className="agent-tickets">
      <Space style={{ marginBottom: 16, justifyContent: 'space-between', width: '100%' }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          工单列表
        </Typography.Title>
        <Select<TicketStatus | undefined>
          style={{ width: 140 }}
          value={status}
          onChange={(v) => {
            setStatus(v);
            setPage(1);
          }}
          options={STATUS_OPTIONS}
        />
      </Space>
      <AppTable<TicketItem>
        rowKey="ticket_id"
        loading={isFetching}
        columns={columns}
        dataSource={data?.items ?? []}
        pagination={{
          current: page,
          total: data?.total ?? 0,
          pageSize: 20,
          showSizeChanger: false,
          onChange: (p) => setPage(p),
        }}
      />
    </div>
  );
}

export default TicketsPage;
