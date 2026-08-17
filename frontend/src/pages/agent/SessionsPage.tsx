import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Button, Popconfirm, Space, Typography, message } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { AppTable } from '@/components/common/AppTable';
import { useNavigate } from 'react-router-dom';
import { deleteSession, listSessions } from '@/api/sessions';
import type { ApiError, Session } from '@/contracts/api';
import { EmptyState } from '@/components/common/EmptyState';

/**
 * Agent 工作台 · 会话列表（真实查询 GET /sessions + T8 React Query）。
 * 点击行进入对话页（/chat）；操作列支持删除（T4：含未关闭工单 409 时提示）。
 */
export function SessionsPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: ['sessions'],
    queryFn: () => listSessions({ page: 1, size: 50 }),
  });
  const items: Session[] = data?.items ?? [];

  const onDelete = (record: Session) => {
    deleteSession(record.id)
      .then(() => {
        message.success('已删除会话');
        queryClient.invalidateQueries({ queryKey: ['sessions'] });
      })
      .catch((e: unknown) => {
        const err = e as ApiError;
        message.error(err?.message || '删除失败');
      });
  };

  if (!isLoading && items.length === 0) {
    return <EmptyState title="暂无会话" description="用户发起对话后会出现在这里" />;
  }

  const columns: ColumnsType<Session> = [
    {
      title: '标题',
      dataIndex: 'title',
      render: (v: string | undefined) => v || '（未命名会话）',
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      width: 200,
      render: (v: string) => new Date(v).toLocaleString('zh-CN'),
    },
    {
      title: '操作',
      key: 'action',
      width: 180,
      render: (_, record) => (
        <Space onClick={(e) => e.stopPropagation()}>
          <Button size="small" onClick={() => navigate(`/chat?session=${record.id}`)}>
            查看对话
          </Button>
          <Popconfirm
            title="删除该会话？"
            description="删除后对话记录不可恢复"
            okText="删除"
            okButtonProps={{ danger: true }}
            onConfirm={() => onDelete(record)}
          >
            <Button size="small" danger>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div className="agent-sessions">
      <Typography.Title level={4}>会话列表</Typography.Title>
      <AppTable<Session>
        rowKey="id"
        loading={isLoading}
        columns={columns}
        dataSource={items}
        pagination={{ pageSize: 20, showSizeChanger: false }}
        onRow={(record) => ({
          onClick: () => navigate(`/chat?session=${record.id}`),
          style: { cursor: 'pointer' },
        })}
      />
    </div>
  );
}

export default SessionsPage;
