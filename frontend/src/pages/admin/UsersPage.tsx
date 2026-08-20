import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Select, Space, Tag, Typography, message } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { AppTable } from '@/components/common/AppTable';
import { listUsers, updateUserRole } from '@/api/admin';
import type { ApiError, Role, UserItem } from '@/contracts/api';
import './UsersPage.css';

const ROLE_META: Record<Role, { color: string; text: string }> = {
  admin: { color: 'red', text: '管理员' },
  agent: { color: 'blue', text: '客服' },
  user: { color: 'default', text: '普通用户' },
};

const ROLE_OPTIONS = [
  { label: '管理员', value: 'admin' as Role },
  { label: '客服', value: 'agent' as Role },
  { label: '普通用户', value: 'user' as Role },
];

/**
 * 用户管理页（FE-04 + T8 落地）：React Query 统一服务端状态。
 * - useQuery 接管列表（缓存 + 重试 + 翻页 keepPreviousData 防闪烁）
 * - 变更后 invalidate 刷新（缓存一致性）
 */
export function UsersPage() {
  const [page, setPage] = useState(1);

  const { data, isFetching } = useQuery({
    queryKey: ['admin-users', page],
    queryFn: () => listUsers(page, 20),
    placeholderData: (prev) => prev, // 翻页保留旧数据（骨架过渡）
  });

  const queryClient = useQueryClient();

  const changeRole = (userId: string, role: Role) => {
    updateUserRole(userId, role)
      .then(() => {
        message.success('角色已更新');
        queryClient.invalidateQueries({ queryKey: ['admin-users'] });
      })
      .catch((e: unknown) => message.error((e as ApiError).message || '更新失败'));
  };

  const columns: ColumnsType<UserItem> = [
    { title: '账号', dataIndex: 'account' },
    {
      title: '角色',
      dataIndex: 'role',
      render: (r: Role) => <Tag color={ROLE_META[r].color}>{ROLE_META[r].text}</Tag>,
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      width: 200,
      render: (v: string) => new Date(v).toLocaleString('zh-CN'),
    },
    {
      title: '操作',
      width: 180,
      render: (_, u) => (
        <Space>
          <span>设为</span>
          <Select<Role>
            style={{ width: 110 }}
            value={u.role}
            onChange={(r) => changeRole(u.user_id, r)}
            options={ROLE_OPTIONS}
          />
        </Space>
      ),
    },
  ];

  return (
    <div className="page users-page page-atmo">
      <div className="users-page__header">
        <Typography.Title level={3}>用户管理</Typography.Title>
      </div>
      <AppTable<UserItem>
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

export default UsersPage;
