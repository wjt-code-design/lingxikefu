import { useState } from 'react';
import {
  Alert,
  Button,
  Card,
  DatePicker,
  Input,
  Select,
  Tag,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import type { RangePickerProps } from 'antd/es/date-picker';
import { AppTable } from '@/components/common/AppTable';
import { BrandEmpty } from '@/components/common/BrandEmpty';
import './LogsPage.css';

const { RangePicker } = DatePicker;

/** 操作类型常量（Select 选项 + 表格 Tag 语义色共用） */
const OPERATION_TYPES = [
  { value: 'kb_create', label: '知识库新增', color: 'green' },
  { value: 'kb_delete', label: '知识库删除', color: 'red' },
  { value: 'role_change', label: '角色变更', color: 'gold' },
  { value: 'config_change', label: '配置变更', color: 'blue' },
] as const;

/** Select 选项（仅 value/label） */
const OPERATION_TYPE_OPTIONS = OPERATION_TYPES.map((t) => ({
  value: t.value,
  label: t.label,
}));

/** 审计日志行结构（示例骨架，接口开放后由后端填充） */
interface AuditLogRow {
  time: string;
  operator: string;
  type: string;
  target: string;
  detail: string;
}

const typeMeta = (value: string) => OPERATION_TYPES.find((t) => t.value === value);

/**
 * 操作审计日志页（Phase3）：知识库增删 / 角色变更 / 系统配置变更记录查询。
 * 后端当前无审计日志接口 → 表格骨架 + 筛选栏 + 空态：
 * 筛选控件可交互但仅本地 state（不查询），表格数据留空显示品牌化空态。
 * 不调用任何不存在的 API。
 */
export function LogsPage() {
  const [dateRange, setDateRange] = useState<RangePickerProps['value']>(null);
  const [opType, setOpType] = useState<string | undefined>();
  const [operator, setOperator] = useState('');

  const handleQuery = () => {
    message.info('审计日志接口待后端开放，开放后自动查询');
  };

  const handleReset = () => {
    setDateRange(null);
    setOpType(undefined);
    setOperator('');
  };

  const columns: ColumnsType<AuditLogRow> = [
    { title: '时间', dataIndex: 'time', width: 180 },
    { title: '操作人', dataIndex: 'operator', width: 140 },
    {
      title: '操作类型',
      dataIndex: 'type',
      width: 140,
      render: (t: string) => {
        const meta = typeMeta(t);
        return <Tag color={meta?.color ?? 'default'}>{meta?.label ?? t}</Tag>;
      },
    },
    { title: '对象', dataIndex: 'target', width: 220 },
    { title: '详情', dataIndex: 'detail' },
  ];

  return (
    <div className="page logs-page">
      <div className="logs-page__header">
        <div className="logs-page__head">
          <Typography.Title level={3} className="logs-page__title">
            操作审计日志
          </Typography.Title>
          <Typography.Text className="logs-page__subtitle">
            知识库增删 / 角色变更 / 系统配置变更记录查询
          </Typography.Text>
        </div>
      </div>

      <Alert
        className="logs-page__alert"
        type="info"
        showIcon
        message="审计日志接口待后端开放，当前展示为示例结构"
        description="筛选控件可交互但仅作用于本地状态，接口开放后将自动查询并填充。"
      />

      {/* 筛选栏（仅本地 state，不查询） */}
      <Card className="logs-filter">
        <div className="logs-filter__row">
          <div className="logs-filter__item">
            <span className="logs-filter__label">时间范围</span>
            <RangePicker value={dateRange} onChange={(v) => setDateRange(v)} allowClear />
          </div>
          <div className="logs-filter__item">
            <span className="logs-filter__label">操作类型</span>
            <Select
              className="logs-filter__select"
              placeholder="全部类型"
              allowClear
              value={opType}
              onChange={setOpType}
              options={OPERATION_TYPE_OPTIONS}
            />
          </div>
          <div className="logs-filter__item">
            <span className="logs-filter__label">操作人</span>
            <Input
              className="logs-filter__input"
              placeholder="输入操作人账号"
              allowClear
              value={operator}
              onChange={(e) => setOperator(e.target.value)}
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

      {/* 日志表格（空数组 + 品牌化空态） */}
      <Card className="logs-table">
        <AppTable<AuditLogRow>
          rowKey="time"
          columns={columns}
          dataSource={[]}
          pagination={false}
          locale={{
            emptyText: <BrandEmpty title="暂无审计日志" hint="接口开放后自动填充" />,
          }}
        />
      </Card>
    </div>
  );
}

export default LogsPage;
