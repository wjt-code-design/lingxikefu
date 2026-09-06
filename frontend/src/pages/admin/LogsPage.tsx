import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Button, Card, DatePicker, Input, Select, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import type { RangePickerProps } from 'antd/es/date-picker';
import { AppTable } from '@/components/common/AppTable';
import { BrandEmpty } from '@/components/common/BrandEmpty';
import { getAuditLogs } from '@/api/admin';
import type { AuditLogItem, AuditLogListReq, Role } from '@/contracts/api';
import './LogsPage.css';

const { RangePicker } = DatePicker;

/** 动作类型（Select 筛选选项 + 表格 Tag 语义色共用）。
 *  死功能修复（2026-09-06）：旧映射用下划线名（kb_create 等）与后端实际写入的
 *  点号动作名（audit_log action="kb.create" 等，见 backend/app/api/*.py）完全不符——
 *  后端 SQL 精确匹配（audit_logs.py）→ 任何动作筛选必空、中文标签永不命中；
 *  config_change 更是后端不存在写入方的幽灵动作。现对齐真实动作全集。 */
const ACTION_META: Record<string, { label: string; color: string }> = {
  'kb.create': { label: '知识库新增', color: 'green' },
  'kb.delete': { label: '知识库删除', color: 'red' },
  'doc.upload': { label: '文档上传', color: 'cyan' },
  'doc.delete': { label: '文档删除', color: 'volcano' },
  'kb.batch.publish': { label: '批量发布', color: 'geekblue' },
  'kb.batch.rollback': { label: '批量回滚', color: 'purple' },
  'user.role': { label: '角色变更', color: 'gold' },
  'session.delete': { label: '会话删除', color: 'orange' },
  'ticket.create': { label: '工单创建', color: 'blue' },
  'ticket.update': { label: '工单更新', color: 'lime' },
};
const ACTION_OPTIONS = Object.entries(ACTION_META).map(([value, m]) => ({
  value,
  label: m.label,
}));

const ROLE_META: Record<Role, { text: string; color: string }> = {
  admin: { text: '管理员', color: 'red' },
  agent: { text: '客服', color: 'blue' },
  user: { text: '普通用户', color: 'default' },
};

const PAGE_SIZE = 20;

/**
 * 操作审计日志页（Phase4）：接入真实审计日志接口。
 * - 数据：GET /admin/audit-logs（需 admin），queryKey ['admin-audit-logs', filters, page]
 * - 筛选：时间范围 / 动作 / 资源 / 操作人；点「查询」才提交，避免输入即查询
 * - 分页 + BrandEmpty 空态
 */
export function LogsPage() {
  const [dateRange, setDateRange] = useState<RangePickerProps['value']>(null);
  const [action, setAction] = useState<string | undefined>();
  const [resource, setResource] = useState('');
  const [actor, setActor] = useState('');
  // 已提交的筛选条件（查询按钮触发）
  const [filters, setFilters] = useState<AuditLogListReq>({});
  const [page, setPage] = useState(1);

  const { data, isFetching } = useQuery({
    queryKey: ['admin-audit-logs', filters, page],
    queryFn: () => getAuditLogs({ ...filters, page, size: PAGE_SIZE }),
    placeholderData: (prev) => prev,
  });

  const handleQuery = () => {
    setPage(1);
    setFilters({
      action: action || undefined,
      resource: resource.trim() || undefined,
      actor: actor.trim() || undefined,
      // 死功能修复（2026-09-06）：后端 start/end 是 date 类型（YYYY-MM-DD，见
      // audit_logs.py），旧发 toISOString() 完整 datetime → 选日期查询必 422。
      // 按天粒度格式化（RangePicker 同步去掉 showTime）。
      start: dateRange?.[0] ? dateRange[0].format('YYYY-MM-DD') : undefined,
      end: dateRange?.[1] ? dateRange[1].format('YYYY-MM-DD') : undefined,
    });
  };

  const handleReset = () => {
    setDateRange(null);
    setAction(undefined);
    setResource('');
    setActor('');
    setPage(1);
    setFilters({});
  };

  const columns: ColumnsType<AuditLogItem> = [
    {
      title: '时间',
      dataIndex: 'created_at',
      width: 168,
      render: (v: string) => new Date(v).toLocaleString('zh-CN'),
    },
    { title: '操作者', dataIndex: 'actor_email', width: 170, ellipsis: true },
    {
      title: '角色',
      dataIndex: 'actor_role',
      width: 92,
      render: (r: Role) => {
        const meta = ROLE_META[r];
        return <Tag color={meta?.color ?? 'default'}>{meta?.text ?? r}</Tag>;
      },
    },
    {
      title: '动作',
      dataIndex: 'action',
      width: 120,
      render: (a: string) => {
        const meta = ACTION_META[a];
        return <Tag color={meta?.color ?? 'default'}>{meta?.label ?? a}</Tag>;
      },
    },
    { title: '资源', dataIndex: 'resource', width: 110, ellipsis: true },
    {
      title: '资源ID',
      dataIndex: 'resource_id',
      width: 140,
      ellipsis: true,
      render: (v?: string | null) => v || '—',
    },
    {
      title: '详情',
      dataIndex: 'detail',
      ellipsis: true,
      render: (v?: string | null) => v || '—',
    },
    {
      title: 'IP',
      dataIndex: 'ip',
      width: 130,
      ellipsis: true,
      render: (v?: string | null) => v || '—',
    },
  ];

  return (
    <div className="page logs-page page-atmo">
      <div className="logs-page__header">
        <div className="logs-page__head">
          <Typography.Title level={1} className="logs-page__title">
            操作审计日志
          </Typography.Title>
          <Typography.Text className="logs-page__subtitle">
            知识库 / 文档 / 工单 / 会话 / 角色变更等操作记录查询
          </Typography.Text>
        </div>
      </div>

      {/* 筛选栏（查询按钮提交） */}
      <Card className="logs-filter">
        <div className="logs-filter__row">
          <div className="logs-filter__item">
            <span className="logs-filter__label">时间范围</span>
            <RangePicker
              value={dateRange}
              onChange={(v) => setDateRange(v)}
              allowClear
              showTime={{ format: 'HH:mm:ss' }}
            />
          </div>
          <div className="logs-filter__item">
            <span className="logs-filter__label">动作</span>
            <Select
              className="logs-filter__select"
              placeholder="全部动作"
              aria-label="按动作筛选"
              allowClear
              value={action}
              onChange={setAction}
              options={ACTION_OPTIONS}
            />
          </div>
          <div className="logs-filter__item">
            <span className="logs-filter__label">资源</span>
            <Input
              className="logs-filter__input"
              placeholder="输入资源名"
              allowClear
              value={resource}
              onChange={(e) => setResource(e.target.value)}
              onPressEnter={handleQuery}
            />
          </div>
          <div className="logs-filter__item">
            <span className="logs-filter__label">操作人</span>
            <Input
              className="logs-filter__input"
              placeholder="输入操作人账号"
              allowClear
              value={actor}
              onChange={(e) => setActor(e.target.value)}
              onPressEnter={handleQuery}
            />
          </div>
          <div className="logs-filter__actions">
            <Button type="primary" onClick={handleQuery}>
              查询
            </Button>
            <Button onClick={handleReset}>重置</Button>
          </div>
        </div>
      </Card>

      {/* 日志表格（真实数据 + 分页 + 品牌化空态） */}
      <Card className="logs-table">
        <AppTable<AuditLogItem>
          rowKey="audit_id"
          loading={isFetching}
          columns={columns}
          dataSource={data?.items ?? []}
          pagination={{
            current: page,
            total: data?.total ?? 0,
            pageSize: PAGE_SIZE,
            showSizeChanger: false,
            onChange: (p) => setPage(p),
          }}
          locale={{
            emptyText: <BrandEmpty title="暂无审计日志" hint="调整筛选条件试试，或等待更多操作记录" />,
          }}
        />
      </Card>
    </div>
  );
}

export default LogsPage;
