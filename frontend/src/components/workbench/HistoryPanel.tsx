import { useQuery } from '@tanstack/react-query';
import { Spin, Typography, Input, Drawer, Button } from 'antd';
import { SearchOutlined } from '@ant-design/icons';
import { BrandEmpty } from '@/components/common/BrandEmpty';
import { useNavigate } from 'react-router-dom';
import { listSessions } from '@/api/sessions';
import { useAuthStore } from '@/store/authStore';
import { useState, useMemo } from 'react';

const RECENT_LIMIT = 8;

type SessionItem = {
  id: string;
  title?: string;
  user_email?: string;
  user_phone?: string;
  updated_at: string;
};

type DateGroup = {
  label: string;
  items: SessionItem[];
};

/**
 * 三栏工作台 · 左栏：历史对话（海盐蓝）。
 * - 历史会话：listSessions（当前用户，按 updated_at 倒序）→ 点击跳 /chat?session=id 加载
 * - 知识库分类面板（2026-09-06 移除）：GET /knowledge-bases 是 admin-only，
 *   客服/agent 打开恒 403 → 面板永远渲染「暂无知识库」假空态，且错误被静默吞掉；
 *   库管理归 /admin/knowledge，客服检索归「知识快搜」。
 * - 匿名态（/widget 未登录）：不发请求，显示空态（避免 401 噪音）
 * 空态/加载态：antd Empty / Spin（轻量，不做骨架屏）。
 */

/** 历史会话单行；提到模块作用域，避免父组件每次渲染重建组件类型导致整列重挂载/闪烁/丢焦点。 */
function SessionRow({
  session,
  title,
  avatar,
  time,
  onOpen,
}: {
  session: SessionItem;
  title: string;
  avatar: string;
  time: string;
  onOpen: (id: string) => void;
}) {
  return (
    <button
      type="button"
      className="wb-session"
      onClick={() => onOpen(session.id)}
      title={title}
      aria-label={title}
    >
      <span className="wb-session__avatar" aria-hidden="true">{avatar}</span>
      <span className="wb-session__title">{title}</span>
      <span className="wb-session__time">{time}</span>
    </button>
  );
}

/** 日期分组标签（今天/昨天/本周/本月/更早）；纯函数，模块作用域避免组件内重建。 */
const dateLabel = (iso: string): string => {
  const d = new Date(iso);
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const yesterday = today - 86_400_000;
  const monday = today - ((now.getDay() + 6) % 7) * 86_400_000;
  const firstDayOfMonth = new Date(now.getFullYear(), now.getMonth(), 1).getTime();
  const t = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  if (t === today) return '今天';
  if (t === yesterday) return '昨天';
  if (t >= monday) return '本周';
  if (t >= firstDayOfMonth) return '本月';
  return '更早';
};

/** 按 updated_at 分组并按固定顺序输出（纯函数，模块作用域：useMemo 依赖稳定）。 */
const groupByDate = (items: SessionItem[]): DateGroup[] => {
  const groups = new Map<string, SessionItem[]>();
  items.forEach((s) => {
    const label = dateLabel(s.updated_at);
    if (!groups.has(label)) groups.set(label, []);
    groups.get(label)!.push(s);
  });
  const order = ['今天', '昨天', '本周', '本月', '更早'];
  return order
    .map((label) => ({ label, items: groups.get(label) || [] }))
    .filter((g) => g.items.length > 0);
};

/** 限额截断分组（纯函数，模块作用域）。 */
const capGroups = (groups: DateGroup[], limit: number): DateGroup[] => {
  const out: DateGroup[] = [];
  let count = 0;
  for (const g of groups) {
    const take = Math.min(g.items.length, Math.max(0, limit - count));
    if (take <= 0) break;
    out.push({ label: g.label, items: g.items.slice(0, take) });
    count += take;
  }
  return out;
};

export function HistoryPanel() {
  const navigate = useNavigate();
  const [searchQuery, setSearchQuery] = useState('');
  const [showAll, setShowAll] = useState(false);
  const token = useAuthStore((s) => s.token);
  const role = useAuthStore((s) => s.role);
  const authed = Boolean(token);
  const isStaff = role === 'admin' || role === 'agent';
  const customerOf = (s: { user_email?: string; user_phone?: string }) => 
    s.user_email || s.user_phone || '未绑定账号';
  const { data: sessions, isLoading: sessionsLoading, isError: sessionsError, refetch: refetchSessions } = useQuery({
    queryKey: ['workbench-sessions'],
    queryFn: () => listSessions({ page: 1, size: 20 }),
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

  const filteredSessions = useMemo(() => {
    if (!sessions?.items) return [];
    if (!searchQuery.trim()) return sessions.items;
    const q = searchQuery.toLowerCase();
    return sessions.items.filter((s) => {
      // UI 审查中9：标题与客户标识都可搜（staff 视角此前只搜邮箱）
      const title = (s.title || '').toLowerCase();
      const customer = customerOf(s).toLowerCase();
      return title.includes(q) || customer.includes(q);
    });
  }, [sessions, searchQuery]);

  const sessionGroups = useMemo(() => groupByDate(filteredSessions), [filteredSessions]);
  const recentGroups = useMemo(() => capGroups(sessionGroups, RECENT_LIMIT), [sessionGroups]);

  const renderSessionRow = (s: SessionItem, keyPrefix: string) => {
    // UI 审查中9：会话标题优先（内容摘要，辨识度高），无标题时 staff 回落客户标识、用户回落"新对话"
    const title = s.title || (isStaff ? customerOf(s) : '新对话');
    const avatar = (title[0] || '客').toUpperCase();
    return (
      <SessionRow
        key={`${keyPrefix}${s.id}`}
        session={s}
        title={title}
        avatar={avatar}
        time={fmtTime(s.updated_at)}
        onOpen={(id) => navigate(`/chat?session=${id}`)}
      />
    );
  };

  return (
    <aside className="wb-left">
      <div className="wb-brand">
        <Typography.Text strong className="wb-brand__name">
          灵犀
        </Typography.Text>
        <Typography.Text type="secondary" className="wb-brand__online">
          <span className="wb-brand__dot" /> 客服在线 · 7×24
        </Typography.Text>
      </div>

      <div className="wb-section wb-section--history">
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
        <Input
          prefix={<SearchOutlined />}
          placeholder="搜索对话..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          allowClear
          size="small"
          style={{ marginBottom: 12 }}
        />
        <div className="wb-sessions">
          {sessionsError ? (
            <div className="wb-sessions__error" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4, padding: '12px 0' }}>
              <Typography.Text type="danger">历史对话加载失败</Typography.Text>
              <Button size="small" type="link" onClick={() => refetchSessions()}>重试</Button>
            </div>
          ) : sessionsLoading ? (
            <Spin size="small" className="wb-spin" />
          ) : !recentGroups.length ? (
            <BrandEmpty title={searchQuery ? '未找到匹配对话' : '暂无历史对话'} hint={searchQuery ? '换个关键词试试' : '开始对话后，这里会显示历史记录'} />
          ) : (
            recentGroups.map((g) => (
              <div key={g.label} className="wb-session-group">
                <div className="wb-session-group__title">{g.label}</div>
                {g.items.map((s) => renderSessionRow(s, ''))}
              </div>
            ))
          )}
        </div>
        {!sessionsLoading && filteredSessions.length > RECENT_LIMIT && (
          <div className="wb-history-footer">
            <button
              type="button"
              onClick={() => setShowAll(true)}
              aria-label={`查看已加载的 ${filteredSessions.length} 条历史对话`}
              title="查看已加载的历史对话"
            >
              查看更多 ({filteredSessions.length})
            </button>
          </div>
        )}
      </div>

      <Drawer
        title="历史对话"
        open={showAll}
        onClose={() => setShowAll(false)}
        placement="left"
        width={360}
        styles={{ body: { padding: '16px 12px' } }}
      >
        {/* 口径诚实（2026-09-06）：本面板只拉最近 20 条（listSessions size=20），
            旧文案「全部历史对话 / 查看全部」是夸大承诺——超出的会话此处不可见，
            完整记录在「会话列表」页（agent）或后台审计页（admin）。搜索同样是本地过滤，
            仅作用于这 20 条。 */}
        {(sessions?.total ?? 0) > (sessions?.items?.length ?? 0) && (
          <Typography.Text type="secondary" style={{ display: 'block', fontSize: 11, marginBottom: 8 }}>
            仅加载最近 {sessions?.items?.length ?? 0} 条（共 {sessions?.total} 条），
            完整记录请见「会话列表」
          </Typography.Text>
        )}
        {sessionsError ? (
          <div className="wb-sessions__error" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4, padding: '12px 0' }}>
            <Typography.Text type="danger">历史对话加载失败</Typography.Text>
            <Button size="small" type="link" onClick={() => refetchSessions()}>重试</Button>
          </div>
        ) : sessionsLoading ? (
          <Spin size="small" className="wb-spin" />
        ) : !sessionGroups.length ? (
          <BrandEmpty title="暂无历史对话" hint="开始对话后，这里会显示历史记录" />
        ) : (
          sessionGroups.map((g) => (
            <div key={`all-${g.label}`} className="wb-session-group">
              <div className="wb-session-group__title">{g.label}</div>
              {g.items.map((s) => renderSessionRow(s, 'all-'))}
            </div>
          ))
        )}
      </Drawer>
    </aside>
  );
}
