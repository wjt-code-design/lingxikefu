import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Button, Input, Select, Space, Typography, message } from 'antd';
import { SearchOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { useNavigate } from 'react-router-dom';
import { AppTable, StatusTag, TICKET_STATUS_TEXT as STATUS_TEXT } from '@/components/common/AppTable';
import { listTickets, updateTicket } from '@/api/tickets';
import type { ApiError, TicketItem, TicketStatus } from '@/contracts/api';
import './TicketsPage.css';

const STATUS_OPTIONS = [
  { label: '全部', value: '' },
  { label: '待处理', value: 'open' as TicketStatus },
  { label: '处理中', value: 'processing' as TicketStatus },
  { label: '已解决', value: 'resolved' as TicketStatus },
  { label: '已关闭', value: 'closed' as TicketStatus },
];

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
  // UI 审查中6：keyword 搜工单号/会话号（300ms 防抖，服务端过滤）
  const [keyword, setKeyword] = useState('');
  const [debouncedKeyword, setDebouncedKeyword] = useState('');
  const queryClient = useQueryClient();

  useEffect(() => {
    const t = setTimeout(() => {
      setDebouncedKeyword(keyword.trim());
      setPage(1);
    }, 300);
    return () => clearTimeout(t);
  }, [keyword]);

  const { data, isFetching } = useQuery({
    queryKey: ['tickets', status, page, debouncedKeyword],
    queryFn: () => listTickets(status, page, 20, debouncedKeyword || undefined),
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
      title: '关联会话',
      dataIndex: 'session_id',
      render: (v: string) => (
        <Button type="link" size="small" onClick={() => navigate(`/chat?session=${v}`)}>
          查看对话
        </Button>
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
            aria-label={`流转工单 ${t.ticket_id.slice(0, 8)}`}
          />
        ) : (
          <Typography.Text type="secondary">已关闭</Typography.Text>
        ),
    },
  ];

  return (
    <div className="agent-tickets page-atmo">
      <Space style={{ marginBottom: 16, justifyContent: 'space-between', width: '100%' }} wrap>
        <Space align="center" wrap>
          <Typography.Title level={1} style={{ margin: 0 }}>
            工单列表
          </Typography.Title>
          {/* UI 审查中6：列表按 updated_at 倒序，展示列却是创建时间——口径提示消除"排序不一致"观感 */}
          <Typography.Text type="secondary">按最近更新排序</Typography.Text>
        </Space>
        <Space wrap>
          <Input
            allowClear
            prefix={<SearchOutlined />}
            placeholder="搜工单号 / 会话号"
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            style={{ width: 200 }}
          />
          <Select<TicketStatus | ''>
            style={{ width: 130 }}
            value={status ?? ''}
            onChange={(v) => {
              setStatus(v || undefined);
              setPage(1);
            }}
            options={STATUS_OPTIONS}
            aria-label="按状态筛选工单"
          />
        </Space>
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
