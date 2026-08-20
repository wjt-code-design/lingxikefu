import { useEffect, useState } from 'react';
import { useQuery, keepPreviousData } from '@tanstack/react-query';
import { Button, Drawer, Input, Select, Spin, Tag, Typography } from 'antd';
import { ReloadOutlined, SearchOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { AppTable } from '@/components/common/AppTable';
import { BrandEmpty } from '@/components/common/BrandEmpty';
import { getSessionDetail, listSessions } from '@/api/sessions';
import type { Session, SessionDetail, SessionListReq } from '@/contracts/api';
import './SessionsAuditPage.css';

const SATISFACTION_META: Record<string, { color: string; text: string }> = {
  satisfied: { color: 'green', text: '满意' },
  neutral: { color: 'default', text: '一般' },
  unsatisfied: { color: 'red', text: '不满意' },
};

const SATISFACTION_OPTIONS = [
  { label: '全部满意度', value: '' },
  { label: '满意', value: 'satisfied' },
  { label: '一般', value: 'neutral' },
  { label: '不满意', value: 'unsatisfied' },
];

const PAGE_SIZE = 20;

/** 消息角色标签（P2：区分用户 / AI / 人工客服，agent 附带客服标识）。 */
const ROLE_TEXT: Record<string, string> = {
  user: '用户',
  assistant: 'AI',
  agent: '人工客服',
};

/** 会话完整对话（气泡左右区分 user / assistant / agent）。 */
function MessageThread({ messages }: { messages: SessionDetail['messages'] }) {
  return (
    <div className="audit-thread">
      {messages.map((m) => (
        <div key={m.id} className={`audit-msg audit-msg--${m.role}`}>
          <div className="audit-msg__bubble">
            <div className="audit-msg__meta">
              <span className="audit-msg__role">
                {ROLE_TEXT[m.role] ?? m.role}
                {m.role === 'agent' && m.agent_name ? ` · ${m.agent_name}` : ''}
              </span>
              <span className="audit-msg__time">
                {new Date(m.created_at).toLocaleString('zh-CN')}
              </span>
            </div>
            <div className="audit-msg__content">{m.content}</div>
          </div>
        </div>
      ))}
    </div>
  );
}

/**
 * 全租户会话审计（Phase3 + 第三批 #7 服务端化）。
 * keyword / 满意度筛选与分页全部由后端完成（此前 size=100 客户端过滤，
 * 第 101 条会话静默不可见）；排序按创建时间倒序（order=created，保持原语义）。
 * 点击行打开 Drawer 展示完整对话（getSessionDetail，limit=1000 拉全量上下文）。
 */
export function SessionsAuditPage() {
  const [keyword, setKeyword] = useState('');
  const [debouncedKeyword, setDebouncedKeyword] = useState(''); // 300ms 防抖，避免逐键请求
  const [satisfaction, setSatisfaction] = useState<SessionListReq['satisfaction']>(undefined);
  const [page, setPage] = useState(1);
  const [openId, setOpenId] = useState<string | null>(null);

  useEffect(() => {
    const t = setTimeout(() => {
      setDebouncedKeyword(keyword.trim());
      setPage(1); // 新搜索回第 1 页
    }, 300);
    return () => clearTimeout(t);
  }, [keyword]);

  const { data, isLoading, isFetching, refetch } = useQuery({
    queryKey: ['admin-sessions-audit', debouncedKeyword, satisfaction, page],
    queryFn: () =>
      listSessions({
        page,
        size: PAGE_SIZE,
        keyword: debouncedKeyword || undefined,
        satisfaction,
        order: 'created',
      }),
    placeholderData: keepPreviousData, // 翻页/筛选时保留旧数据，避免表格闪空
  });

  const items = data?.items ?? [];
  const total = data?.total ?? 0;

  const { data: detail, isLoading: detailLoading } = useQuery({
    queryKey: ['admin-session-detail', openId],
    queryFn: () => getSessionDetail(openId as string, 1000),
    enabled: !!openId,
  });

  const columns: ColumnsType<Session> = [
    {
      title: '客户标识',
      key: 'customer',
      width: 200,
      render: (_, r) => {
        const label = r.user_email || r.user_phone || '—';
        return (
          <Typography.Text className="audit-cell" ellipsis={{ tooltip: label }}>
            {label}
          </Typography.Text>
        );
      },
    },
    {
      title: '标题',
      dataIndex: 'title',
      width: 240,
      render: (v: string | undefined) => (
        <Typography.Text className="audit-cell" ellipsis={{ tooltip: v || '（未命名会话）' }}>
          {v || '（未命名会话）'}
        </Typography.Text>
      ),
    },
    {
      title: '满意度',
      dataIndex: 'satisfaction',
      width: 110,
      render: (v: string | undefined) => {
        if (!v) return <Typography.Text type="secondary">—</Typography.Text>;
        const meta = SATISFACTION_META[v];
        return meta ? (
          <Tag color={meta.color}>{meta.text}</Tag>
        ) : (
          <Typography.Text type="secondary">{v}</Typography.Text>
        );
      },
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      width: 190,
      render: (v: string) => new Date(v).toLocaleString('zh-CN'),
    },
    {
      title: '最后活跃',
      dataIndex: 'updated_at',
      width: 190,
      render: (v: string) => new Date(v).toLocaleString('zh-CN'),
    },
  ];

  return (
    <div className="page audit-page page-atmo">
      <div className="audit-page__head">
        <Typography.Title level={3} className="audit-page__title">会话审计</Typography.Title>
        <Typography.Text className="audit-page__subtitle">
          全租户会话检索 · 点击行查看完整对话
        </Typography.Text>
      </div>

      <div className="audit-filters">
        <Input
          allowClear
          prefix={<SearchOutlined />}
          placeholder="搜索客户邮箱 / 电话 / 标题关键词"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          className="audit-filters__input"
        />
        <Select
          style={{ width: 150 }}
          value={satisfaction}
          onChange={(v) => {
            setSatisfaction((v || undefined) as SessionListReq['satisfaction']);
            setPage(1);
          }}
          options={SATISFACTION_OPTIONS}
        />
        <Button icon={<ReloadOutlined />} loading={isFetching} onClick={() => refetch()}>
          刷新
        </Button>
        <span className="audit-filters__count">共 {total} 条</span>
      </div>

      {isLoading && !data ? (
        <Spin className="audit-spin" />
      ) : items.length === 0 ? (
        <BrandEmpty title="暂无匹配会话" hint="调整筛选条件或点击刷新重试" />
      ) : (
        <div className="audit-table">
          <AppTable<Session>
            rowKey="id"
            loading={isFetching}
            columns={columns}
            dataSource={items}
            pagination={{
              current: page,
              pageSize: PAGE_SIZE,
              total,
              showSizeChanger: false,
              onChange: setPage,
            }}
            onRow={(record) => ({
              onClick: () => setOpenId(record.id),
              style: { cursor: 'pointer' },
            })}
          />
        </div>
      )}

      <Drawer
        title={detail?.title || '会话详情'}
        width={560}
        open={!!openId}
        onClose={() => setOpenId(null)}
        destroyOnClose
      >
        {detailLoading ? (
          <Spin className="audit-spin" />
        ) : detail && detail.messages.length ? (
          <MessageThread messages={detail.messages} />
        ) : (
          <BrandEmpty title="暂无对话消息" hint="该会话可能没有有效的消息记录" />
        )}
      </Drawer>
    </div>
  );
}

export default SessionsAuditPage;
