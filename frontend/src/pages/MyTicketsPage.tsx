import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Select, Space, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useNavigate } from 'react-router-dom';
import { AppTable, StatusTag, TICKET_STATUS_TEXT as STATUS_TEXT } from '@/components/common/AppTable';
import { BrandEmpty } from '@/components/common/BrandEmpty';
import { QueryErrorState } from '@/components/common/QueryErrorState';
import { listMyTickets } from '@/api/tickets';
import type { TicketItem, TicketStatus } from '@/contracts/api';

/**
 * 用户侧「我的工单」（D2 修复）：SideNav/ROUTE_META 早已注册 /tickets，
 * 但缺路由与页面组件 → 点菜单 404。此页补全用户查看自己工单进度的入口。
 *
 * 数据源 GET /tickets/mine（后端按 Session.user_id 过滤，越权隔离已在后端保证）；
 * 只读——状态流转是客服职责（见 /agent/tickets），用户侧不提供操作。
 */
const STATUS_OPTIONS = [
  { label: '全部', value: '' },
  { label: '待处理', value: 'open' as TicketStatus },
  { label: '处理中', value: 'processing' as TicketStatus },
  { label: '已解决', value: 'resolved' as TicketStatus },
  { label: '已关闭', value: 'closed' as TicketStatus },
];

export function MyTicketsPage() {
  const navigate = useNavigate();
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState<TicketStatus | undefined>(undefined);

  const { data, isFetching, isError, refetch } = useQuery({
    queryKey: ['my-tickets', status, page],
    queryFn: () => listMyTickets(status, page, 20),
    placeholderData: (prev) => prev,
  });

  const columns: ColumnsType<TicketItem> = [
    {
      title: '工单号',
      dataIndex: 'ticket_id',
      width: 120,
      render: (v: string) => <Typography.Text code>{v.slice(0, 8)}</Typography.Text>,
    },
    {
      title: '主题',
      dataIndex: 'session_title',
      ellipsis: true,
      render: (v: string | undefined | null) =>
        v ? (
          <Typography.Text ellipsis={{ tooltip: v }}>{v}</Typography.Text>
        ) : (
          <Typography.Text type="secondary">—</Typography.Text>
        ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (s: TicketStatus) => <StatusTag status={s} text={STATUS_TEXT[s]} />,
    },
    {
      title: '最近更新',
      dataIndex: 'updated_at',
      width: 190,
      render: (v: string) => new Date(v).toLocaleString('zh-CN'),
    },
    {
      title: '关联会话',
      dataIndex: 'session_id',
      width: 120,
      render: (v: string) => (
        <Typography.Link onClick={() => navigate(`/chat?session=${v}`)}>查看对话</Typography.Link>
      ),
    },
  ];

  return (
    <div className="my-tickets page-atmo">
      <Space style={{ marginBottom: 16, justifyContent: 'space-between', width: '100%' }} wrap>
        <Space align="center" wrap>
          <Typography.Title level={1} style={{ margin: 0 }}>
            我的工单
          </Typography.Title>
          <Typography.Text type="secondary">按最近更新排序</Typography.Text>
        </Space>
        <Select<TicketStatus | ''>
          style={{ width: 130 }}
          value={status ?? ''}
          onChange={(v) => {
            setStatus(v || undefined);
            setPage(1);
          }}
          options={STATUS_OPTIONS}
          aria-label="按状态筛选我的工单"
        />
      </Space>
      {isError ? (
        <QueryErrorState onRetry={() => refetch()} />
      ) : (
        <AppTable<TicketItem>
          rowKey="ticket_id"
          loading={isFetching}
          columns={columns}
          dataSource={data?.items ?? []}
          locale={{
            emptyText: (
              <BrandEmpty
                title="暂无工单"
                hint="在对话中点击「转人工」或客服建单后，这里会显示处理进度"
              />
            ),
          }}
          pagination={{
            current: page,
            total: data?.total ?? 0,
            pageSize: 20,
            showSizeChanger: false,
            onChange: (p) => setPage(p),
          }}
        />
      )}
    </div>
  );
}

export default MyTicketsPage;
