import { useQuery } from '@tanstack/react-query';
import { Spin, Typography } from 'antd';
import { BrandEmpty } from '@/components/common/BrandEmpty';
import { useNavigate } from 'react-router-dom';
import { listKnowledgeBases } from '@/api/knowledge';
import { listSessions } from '@/api/sessions';
import { useAuthStore } from '@/store/authStore';

/**
 * 三栏工作台 · 左栏：历史对话 + 知识库分类（海盐蓝）。
 * - 历史会话：listSessions（当前用户，按 updated_at 倒序）→ 点击跳 /chat?session=id 加载
 * - 知识库分类：listKnowledgeBases → 只读展示文档数（运营侧标签）
 * - 匿名态（/widget 未登录）：不发请求，显示空态（避免 401 噪音）
 * 空态/加载态：antd Empty / Spin（轻量，不做骨架屏）。
 */
export function HistoryPanel() {
  const navigate = useNavigate();
  const token = useAuthStore((s) => s.token);
  const role = useAuthStore((s) => s.role);
  const authed = Boolean(token);
  // BUG-12：agent/admin 全租户视角需区分客户（显示 email/phone 标识）
  const isStaff = role === 'agent' || role === 'admin';
  const customerOf = (s: { user_email?: string; user_phone?: string }) =>
    s.user_email || s.user_phone || '未绑定账号';
  const { data: sessions, isLoading: sessionsLoading } = useQuery({
    queryKey: ['workbench-sessions'],
    queryFn: () => listSessions({ page: 1, size: 20 }),
    enabled: authed,
  });
  const { data: kbs, isLoading: kbsLoading } = useQuery({
    queryKey: ['workbench-kbs'],
    queryFn: listKnowledgeBases,
    enabled: authed,
  });

  const fmtTime = (iso: string) => {
    const d = new Date(iso);
    const now = Date.now();
    const diff = now - d.getTime();
    if (diff < 60_000) return '刚刚';
    if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`;
    return d.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' });
  };

  return (
    <aside className="wb-left">
      <div className="wb-brand">
        <Typography.Text strong className="wb-brand__name">
          灵犀 · 星河智家
        </Typography.Text>
        <Typography.Text type="secondary" className="wb-brand__online">
          <span className="wb-brand__dot" /> 客服在线 · 7×24
        </Typography.Text>
      </div>

      <div className="wb-section">
        <div className="wb-section__head">
          <Typography.Text className="wb-section__title">历史对话</Typography.Text>
          <button
            type="button"
            className="wb-new"
            onClick={() => navigate('/chat')}
            title="新建对话"
            aria-label="新建对话"
          >
            + 新建
          </button>
        </div>
        <div className="wb-sessions">
          {sessionsLoading ? (
            <Spin size="small" className="wb-spin" />
          ) : !sessions?.items.length ? (
            <BrandEmpty title="暂无历史对话" hint="开始对话后，这里会显示历史记录" />
          ) : (
            sessions.items.map((s) => (
              <button
                key={s.id}
                type="button"
                className="wb-session"
                onClick={() => navigate(`/chat?session=${s.id}`)}
              >
                {isStaff ? (
                  <span className="wb-session__customer" title={s.user_email || s.user_phone || undefined}>
                    {customerOf(s)}
                  </span>
                ) : null}
                <span className="wb-session__title">{s.title || '新对话'}</span>
                <span className="wb-session__time">{fmtTime(s.updated_at)}</span>
              </button>
            ))
          )}
        </div>
      </div>

      <div className="wb-section">
        <Typography.Text className="wb-section__title">知识库分类</Typography.Text>
        <div className="wb-kbs">
          {kbsLoading ? (
            <Spin size="small" className="wb-spin" />
          ) : !kbs?.items.length ? (
            <BrandEmpty title="暂无知识库" hint="创建知识库后，这里会显示分类" />
          ) : (
            kbs.items.map((kb) => (
              <div key={kb.kb_id} className="wb-kb">
                <span className="wb-kb__name">{kb.name}</span>
                <span className="wb-kb__meta">{kb.doc_count} 文档</span>
              </div>
            ))
          )}
        </div>
      </div>
    </aside>
  );
}
