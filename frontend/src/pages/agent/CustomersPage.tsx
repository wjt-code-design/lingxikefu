import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { AppTable, StatusTag } from '@/components/common/AppTable';
import { listCustomers } from '@/api/customers';
import type { CustomerItem } from '@/contracts/api';
import './CustomersPage.css';

/**
 * 客户管理（T6 + T8 落地）：React Query 统一服务端状态。
 */
export function CustomersPage() {
  const [page, setPage] = useState(1);

  const { data, isFetching } = useQuery({
    queryKey: ['customers', page],
    queryFn: () => listCustomers(page, 20),
    placeholderData: (prev) => prev,
  });

  const columns: ColumnsType<CustomerItem> = [
    { title: '账号', dataIndex: 'account' },
    { title: '会话数', dataIndex: 'session_count', width: 100 },
    {
      title: '最近活跃',
      dataIndex: 'last_active',
      width: 190,
      render: (v?: string | null) => (v ? new Date(v).toLocaleString('zh-CN') : '—'),
    },
    {
      title: '未处理工单',
      dataIndex: 'open_tickets',
      width: 120,
      render: (n: number) =>
        n > 0 ? <StatusTag status="open" text={`${n} 个待跟进`} /> : <Typography.Text type="secondary">—</Typography.Text>,
    },
  ];

  return (
    <div className="agent-customers page-atmo">
      <Typography.Title level={3} style={{ marginBottom: 16 }}>
        客户管理
      </Typography.Title>
      <AppTable<CustomerItem>
        rowKey="user_id"
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

export default CustomersPage;
