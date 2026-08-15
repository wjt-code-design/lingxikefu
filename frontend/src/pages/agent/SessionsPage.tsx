import { useEffect, useState } from 'react';
import { Table, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useNavigate } from 'react-router-dom';
import { listSessions } from '@/api/sessions';
import type { Session } from '@/contracts/api';
import { EmptyState } from '@/components/common/EmptyState';

/**
 * Agent 工作台 · 会话列表（真实查询 GET /sessions）。
 * 点击行进入该会话的对话页（/chat）。
 */
export function SessionsPage() {
  const [items, setItems] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();

  useEffect(() => {
    let alive = true;
    listSessions({ page: 1, size: 50 })
      .then((resp) => {
        if (alive) setItems(resp.items);
      })
      .catch(() => {
        /* 加载失败保留空态，不打断页面 */
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  if (!loading && items.length === 0) {
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
  ];

  return (
    <div className="agent-sessions">
      <Typography.Title level={4}>会话列表</Typography.Title>
      <Table<Session>
        rowKey="id"
        loading={loading}
        columns={columns}
        dataSource={items}
        pagination={{ pageSize: 20, showSizeChanger: false }}
        onRow={() => ({ onClick: () => navigate('/chat'), style: { cursor: 'pointer' } })}
      />
    </div>
  );
}

export default SessionsPage;
