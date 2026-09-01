import { useQuery } from '@tanstack/react-query';
import { Alert, Card, Spin, Typography } from 'antd';
import type { ReactNode } from 'react';
import { getAdminSettings } from '@/api/admin';
import { QueryErrorState } from '@/components/common/QueryErrorState';
import './SettingsPage.css';

/** 只读键值行（label 左、value 右，数字 tabular-nums） */
function KVRow({
  label,
  children,
  mono,
}: {
  label: string;
  children: ReactNode;
  mono?: boolean;
}) {
  return (
    <div className="settings-kv">
      <span className="settings-kv__label">{label}</span>
      <span className={mono ? 'settings-kv__value settings-kv__value--mono' : 'settings-kv__value'}>
        {children}
      </span>
    </div>
  );
}

/** 布尔徽标（开启/关闭，语义色区分） */
function BoolBadge({ value }: { value: boolean }) {
  return (
    <span className={`settings-bool ${value ? 'settings-bool--on' : 'settings-bool--off'}`}>
      {value ? '开启' : '关闭'}
    </span>
  );
}

/** 空值统一渲染为占位破折号 */
const dash = (v: unknown) => (v == null || v === '' ? '—' : String(v));

/**
 * 系统设置页（Phase4）：只读展示后端真实配置（真源 .env）。
 * - 数据：GET /admin/settings（需 admin），queryKey ['admin-settings']
 * - 4 卡分组：模型 / RAG / 限流 / 配额；运行时不可在线修改。
 */
export function SettingsPage() {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['admin-settings'],
    queryFn: getAdminSettings,
    staleTime: 30_000,
  });

  let body: ReactNode;
  if (isLoading) {
    body = <Spin className="settings-page__spin" />;
  } else if (isError || !data) {
    body = <QueryErrorState title="系统配置读取失败" onRetry={() => refetch()} />;
  } else {
    body = (
      <div className="settings-grid">
        {/* ① 模型配置 */}
        <Card
          className="settings-card"
          title="模型配置"
          extra={<span className="settings-card__tag">只读</span>}
        >
          <p className="settings-card__desc">对话与向量模型（真源 .env）</p>
          <KVRow label="Provider">{dash(data.model.provider)}</KVRow>
          <KVRow label="主模型">{dash(data.model.model)}</KVRow>
          <KVRow label="备用模型">{dash(data.model.fallback)}</KVRow>
          <KVRow label="Embedding Provider">{dash(data.model.embedding_provider)}</KVRow>
          <KVRow label="Embedding 模型">{dash(data.model.embedding_model)}</KVRow>
        </Card>

        {/* ② RAG 配置 */}
        <Card
          className="settings-card"
          title="RAG 配置"
          extra={<span className="settings-card__tag">只读</span>}
        >
          <p className="settings-card__desc">检索召回与答案缓存策略</p>
          <KVRow label="检索 top_k" mono>
            {data.rag.top_k}
          </KVRow>
          <KVRow label="相似度阈值 min_score" mono>
            {data.rag.min_score}
          </KVRow>
          <KVRow label="混合检索 hybrid">
            <BoolBadge value={data.rag.hybrid} />
          </KVRow>
          <KVRow label="分块大小 chunk_size" mono>
            {data.rag.chunk_size}
          </KVRow>
          <KVRow label="分块重叠 chunk_overlap" mono>
            {data.rag.chunk_overlap}
          </KVRow>
          <KVRow label="答案缓存 answer_cache">
            <BoolBadge value={data.rag.answer_cache_enabled} />
          </KVRow>
          <KVRow label="缓存阈值 answer_cache_threshold" mono>
            {data.rag.answer_cache_threshold}
          </KVRow>
          <KVRow label="上传大小上限 max_upload_mb" mono>
            {data.rag.max_upload_mb} MB
          </KVRow>
        </Card>

        {/* ③ 限流配置 */}
        <Card
          className="settings-card"
          title="限流配置"
          extra={<span className="settings-card__tag">只读</span>}
        >
          <p className="settings-card__desc">接口调用频率控制</p>
          <KVRow label="限流开关">
            <BoolBadge value={data.rate_limit.enabled} />
          </KVRow>
        </Card>

        {/* ④ 配额配置 */}
        <Card
          className="settings-card"
          title="配额配置"
          extra={<span className="settings-card__tag">只读</span>}
        >
          <p className="settings-card__desc">用户每日使用额度</p>
          <KVRow label="每日对话配额" mono>
            {data.quota.daily_limit}
          </KVRow>
        </Card>
      </div>
    );
  }

  return (
    <div className="page settings-page page-atmo">
      <div className="settings-page__header">
        <div className="settings-page__head">
          <Typography.Title level={1} className="settings-page__title">
            系统设置
          </Typography.Title>
          <Typography.Text className="settings-page__subtitle">
            模型 / RAG / 限流 / 配额配置
          </Typography.Text>
          {data?.env ? <span className="settings-page__env">{data.env}</span> : null}
        </div>
      </div>

      <Alert
        className="settings-page__alert"
        type="info"
        showIcon
        message="配置真源在 .env（环境变量），此处只读展示"
        description="以下为后端当前生效的真实配置，运行时不可在线修改；调整配置需修改 .env 后重启服务。"
      />

      {body}
    </div>
  );
}

export default SettingsPage;
