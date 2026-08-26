import { useCallback, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Button } from 'antd';
import { BulbOutlined, CloseOutlined, SwapOutlined, TruckOutlined, ToolOutlined, SafetyCertificateOutlined, UserOutlined, FileAddOutlined } from '@ant-design/icons';
import { sendFeedback } from '@/api/chat';
import { createSession, getSessionDetail, rateSatisfaction, sendAgentMessage, suggestReply } from '@/api/sessions';
import { escalateSession, createTicket } from '@/api/tickets';
import { useAuthStore } from '@/store/authStore';
import { useChatStream } from '@/hooks/useChatStream';
import type { Message, MessageSource, SessionDetail } from '@/contracts/api';
import { Composer } from './Composer';
import { MessageList } from './MessageList';
import { SatisfactionBar } from './SatisfactionBar';
import { TicketStatusBadge } from './TicketStatusBadge';
import type { ChatMessage } from './types';

/** 快捷问题单一数据源（2026-08-21 合并原 HOT_SCENARIOS + MORE_GROUPS，消除双数组漂移）。
 * 每问题可带 `featured:true`（首屏精品卡片）+ `d` 描述；其余进"更多常见问题"折叠分组。
 * 改问题/加分组一律改这里，一处维护。 */
const QUESTION_GROUPS = [
  {
    key: 'aftersale',
    icon: <SwapOutlined />,
    title: '售后退换',
    questions: [
      { q: '七天无理由退货怎么申请？', d: '在线申请 · 快速退款', featured: true },
      { q: '退款一般多久到账？', d: '原路退回 · 1-3 个工作日', featured: true },
      { q: '换货要怎么做？' },
      { q: '退货进度在哪看？' },
    ],
  },
  {
    key: 'logistics',
    icon: <TruckOutlined />,
    title: '配送物流',
    questions: [
      { q: '支持哪些支付方式？', d: '微信 / 支付宝 / 银联', featured: true },
      { q: '可以开发票吗？', d: '电子发票 · 随时申请', featured: true },
      { q: '物流单号在哪查？' },
      { q: '下单后多久发货？' },
      { q: '预售要等多久？' },
    ],
  },
  {
    key: 'warranty',
    icon: <ToolOutlined />,
    title: '保修维修',
    questions: [
      { q: '保修多久？', d: '整机保修 · 全国联保', featured: true },
      { q: '屏幕有坏点保修吗？' },
      { q: '电池健康度低于80%保修吗？' },
      { q: '手机配置参数在哪看？' },
      { q: '系统怎么升级？' },
    ],
  },
  {
    key: 'account',
    icon: <UserOutlined />,
    title: '账户支付',
    questions: [
      { q: '如何修改收货地址？', d: '个人中心 · 随时修改', featured: true },
      { q: '怎么修改登录密码？' },
      { q: '如何绑定或解绑手机号？' },
      { q: '短信验证码收不到怎么办？' },
    ],
  },
  {
    key: 'value',
    icon: <SafetyCertificateOutlined />,
    title: '价保与回收',
    questions: [
      { q: '价保怎么申请？退差价多久到账？' },
      { q: '优惠券能叠加使用吗？' },
      { q: '手机可以以旧换新吗？' },
      { q: '以旧换新怎么估价？' },
    ],
  },
];

/** 工单系统通知气泡（转人工 / 建单统一）。抽自 manualTicket / ticketCreated 两段重复 JSX。
 * - kind: 'escalate' 转人工 | 'create' 建单
 * - state: { id, loading?, error }（建单无 loading 态）
 * - isStaff: staff/observe 视图下措辞用"为顾客"，普通用户用"为您"。
 * 排版（2026-08-22）：两行结构——第一行事件描述，第二行"工单号 + 状态角标"元信息，
 * 修复长句 + 块级角标混排导致的怪异换行。 */
function TicketNotice({
  kind,
  state,
  isStaff,
}: {
  kind: 'escalate' | 'create';
  state: { id: string; loading?: boolean; error: string | null };
  isStaff: boolean;
}) {
  const who = isStaff ? '顾客' : '您';
  return (
    <div className="chat-msg chat-msg--ai">
      <div className="chat-msg__bubble">
        <div className={`chat-msg__ticket${state.error ? ' chat-msg__ticket--error' : ''}`}>
          {state.loading ? (
            `正在为${who}转人工…`
          ) : state.error ? (
            state.error
          ) : (
            <div className="chat-msg__ticket-body">
              <div className="chat-msg__ticket-line1">
                {kind === 'escalate' ? `已为${who}转人工` : `已为${who}创建工单`}，客服将尽快跟进
              </div>
              <div className="chat-msg__ticket-line2">
                <span className="chat-msg__ticket-no">
                  工单 <b>#{state.id.slice(0, 8)}</b>
                </span>
                <TicketStatusBadge ticketId={state.id} />
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/** 2026-08-25（code-review F4）：API 消息 → 前端 ChatMessage 的单一映射。
 * 历史加载与 observe 轮询追加重载共用，避免字段映射漂移（Shotgun Surgery 收敛）。 */
function toChatMessage(m: Message): ChatMessage {
  return {
    id: m.id,
    role: m.role as 'user' | 'assistant' | 'agent',
    content: m.content,
    status: 'done',
    createdAt: new Date(m.created_at || Date.now()).getTime(),
    sources: m.sources ?? [],
    ...(m.role === 'assistant' ? { messageId: m.id } : {}),
    ...(m.role === 'agent' ? { agentName: m.agent_name } : {}),
    // 大扫查 F-major：工具徽章读路径（刷新/历史加载不丢徽章）
    ...(m.tool ? { tool: m.tool } : {}),
    // 2026-08-25：快捷话术标记读路径（不丢「预置话术无引用」判断依据）
    ...(m.answer_source ? { answerSource: m.answer_source } : {}),
  };
}

/**
 * 对话挂件容器（FE-03 核心）：
 * - 会话懒创建：首次发问时 POST /sessions 拿 session_id；
 * - 带参进入（M8）：/chat?session={id} 加载既有会话历史（agent 查看用户会话）；
 * - SSE 流式：useChatStream 消费 stage/token/sources/done/error；
 * - 消息归并：流式 done/error 后将 user + assistant 消息并入历史，反馈条接 sendFeedback。
 */
export function ChatContainer({
  onSourcesChange,
  onAnswerSourceChange,
  onRegisterFill,
  selectedMsgId,
  onSelectMessage,
}: {
  /** 右栏溯源面板订阅 sources（三栏工作台用）；不传则忽略。 */
  onSourcesChange?: (s: MessageSource[]) => void;
  /** 快捷话术回答标记推送（done.answer_source，每轮 finalize 覆盖；SourcePanel 区分空态用） */
  onAnswerSourceChange?: (v: string | undefined) => void;
  /** P1-3：快捷话术 → 填入输入框能力注册（WorkbenchLayout 透传给 SourcePanel） */
  onRegisterFill?: (fill: (text: string) => void) => void;
  /** 溯源选中（2026-08-25）：当前被右栏面板查看的 AI 回复 id（来自 WorkbenchLayout，气泡高亮用） */
  selectedMsgId?: string | null;
  /** 点击 AI 回复 → 右栏溯源面板切换（点哪条看哪条；answerSource 透出选中消息的快捷话术标记） */
  onSelectMessage?: (msgId: string, sources: MessageSource[], answerSource?: string) => void;
}) {
  const { stage, tokens, sources, messageId, userMessageId, ticketId, tool, answerSource, error, reset, stop, stream } = useChatStream();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  // P3-⑫：会话 ref（每 render 同步）——历史加载/建议等在途回调用它做 stale 守卫
  const sessionIdRef = useRef<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null); // I-1：会话创建失败提示
  const [retryText, setRetryText] = useState<{ text: string; clientMsgId: string } | null>(null); // U1：最近失败消息 → 一键重试（含幂等键，重试复用不重复扣费）
  const [searchParams] = useSearchParams();
  const sessionParam = searchParams.get('session');
  // P3-⑫：sessionParam 的 ref 镜像（每 render 同步）——异步回调读「最新参数」做 stale 守卫，
  // 闭包里的 sessionParam 与发起时捕获值恒等，直接比对永远为 true（不守卫）。
  const sessionParamRef = useRef<string | null>(null);
  sessionParamRef.current = sessionParam;
  const role = useAuthStore((s) => s.role);
  // 客服视角：agent/admin 打开既有会话（?session=）时进入 observe 模式——
  // 顾客与 AI 的对话统一在左，人工介入区在右（demo 招牌布局）；用户侧（自己聊天）保持原样。
  const observeMode = (role === 'agent' || role === 'admin') && !!sessionParam;
  // W4：气泡对齐视角按"登录角色"决定，与 observe 监控 UI 解耦——
  // 管理员/客服账号一律以客服视角呈现（顾客+AI 在左、人工客服在右）；普通用户以自身视角（自己在右）。
  const isStaff = role === 'agent' || role === 'admin';
  // W7：气泡对齐视角直接由登录角色决定——管理员/客服账号一律以客服视角呈现
  // （顾客+AI 在左、人工客服在右），不论是否有 ?session= 参数。
  // 只有普通用户（user 角色）才用 self 视角（自己在右、AI 在左）。
  const chatLayout: 'self' | 'observe' = isStaff ? 'observe' : 'self';
  // 当前流式对应的用户消息（P0-1：done/error 时按 id 定位并更新；text 用于失败重试兜底）
  // C1/C2：记录发起流时的 user 消息 + 会话；finalize 时比对 sessionId 防串台、防 ref 覆盖丢回答
  // R2：clientMsgId 为客户端提问幂等键（重试复用，配额幂等扣费）
  const streamingUserRef = useRef<{ id: string; text: string; sessionId: string | null; clientMsgId: string | null } | null>(null);
  // P0-4：手动转人工结果气泡（独立于 SSE 流，HTTP 响应驱动）
  const [manualTicket, setManualTicket] = useState<{ id: string; loading: boolean; error: string | null } | null>(null);
  // W5：建单结果气泡（与转人工独立；仅记录工单，不改变介入视角）
  const [ticketCreated, setTicketCreated] = useState<{ id: string; error: string | null } | null>(null);
  // W5：转人工后客服发送的消息切换为"人工客服"角色（右侧）；点击前一律模拟为"顾客"消息（左侧）
  const [intervened, setIntervened] = useState(false);
  // P2-2：会话满意度——对话轮次 ≥2 后内联出现，评分一次后隐藏
  const [turnCount, setTurnCount] = useState(0);
  const [satisfactionRated, setSatisfactionRated] = useState(false);
  // 2026-08-21 方案B：首屏精品外的"更多常见问题"折叠展开
  const [moreOpen, setMoreOpen] = useState(false);
  // Phase D：用户画像摘要（客服视角 observeMode 时展示；来自 getSessionDetail.profile）
  const [userProfile, setUserProfile] = useState<SessionDetail['profile']>(undefined);
  // 转人工交接摘要（本次会话上下文压缩打包；来自 getSessionDetail.handoff_summary）
  const [handoffSummary, setHandoffSummary] = useState<SessionDetail['handoff_summary']>(undefined);
  // 批次A：坐席辅助 AI 推荐（observe 视角；手动触发、fail-open 静默）
  const [aiSuggest, setAiSuggest] = useState<{ text: string; sources: MessageSource[]; loading: boolean } | null>(null);
  // 本地持有 Composer 的填入能力（建议卡片「填入输入框」用）；透传给父级 WorkbenchLayout（SourcePanel 快捷话术）
  const fillRef = useRef<((t: string) => void) | null>(null);
  // 溯源选中（2026-08-25）：ref 保持最新 onSelectMessage（历史加载 .then 异步回调内使用，避免闭包过期）
  const onSelectMessageRef = useRef(onSelectMessage);
  useEffect(() => {
    onSelectMessageRef.current = onSelectMessage;
  }, [onSelectMessage]);
  const registerFill = useCallback(
    (f: (t: string) => void) => {
      fillRef.current = f;
      onRegisterFill?.(f);
    },
    [onRegisterFill],
  );

  // 三栏工作台：sources 变化时同步给右栏溯源面板（引用稳定，避免重复渲染）
  useEffect(() => {
    onSourcesChange?.(sources);
  }, [sources, onSourcesChange]);

  const streaming = stage === 'retrieving' || stage === 'generating';

  // M8：带 ?session= 进入时加载历史（agent 查看用户会话）；参数变化则重新加载
  // P1-1：点"新建"从 ?session=A 跳到 /chat（无参）→ 清空旧消息开新对话。
  // 用 ref 记录前一个 sessionParam：仅当"从有参历史切到无参"才清空（新会话首次创建 setSessionId 不应触发清空）
  const prevSessionParamRef = useRef<string | null>(null);
  useEffect(() => {
    const prev = prevSessionParamRef.current;
    prevSessionParamRef.current = sessionParam;
    if (!sessionParam) {
      // P3-⑫：从「?session=A」切到无参（新建）时无条件清理——详情仍在途也允许把状态清成新会话，
      // 之后异步 .then 靠 stale 守卫丢弃过期详情，杜绝旧会话消息写入新会话。
      if (prev) {
        setSessionId(null);
        setMessages([]);
        setUserProfile(undefined); // Phase D：切会话重置画像
        setHandoffSummary(undefined); // 切会话重置交接摘要
        streamingUserRef.current = null;
        setTurnCount(0); // P2-2：新会话重置满意度轮次
        setSatisfactionRated(false);
        setIntervened(false); // W5：切会话重置介入视角
        setTicketCreated(null);
        setManualTicket(null);
        setAiSuggest(null); // 批次A：切会话重置 AI 建议卡片（防旧会话建议残留/填入新会话输入框）
        reset(); // C1：切到新会话 → abort 旧流
      }
      return;
    }
    if (sessionParam === sessionId) return;
    reset(); // C1：切换到另一会话 → abort 旧流
    setIntervened(false); // W5：切会话重置介入视角
    setTicketCreated(null);
    setManualTicket(null);
    setAiSuggest(null); // 批次A：切会话重置 AI 建议卡片（防旧会话建议残留/填入新会话输入框）
    const sid = sessionParam; // P3-⑫：请求发起时捕获，回调内比对最新参数/会话
    getSessionDetail(sid)
      .then((d) => {
        // P3-⑫：stale 守卫——详情仍处等待时用户已切走（新建/换会话），丢弃过期历史，
        // 用 sessionParamRef 读「最新参数」比对（闭包 sessionParam 恒等于 sid，直接比对无效）。
        if (sessionParamRef.current !== sid || sessionIdRef.current === d.id) return;
        setSessionId(d.id);
        setUserProfile(d.profile); // Phase D：客服视角展示画像
        setHandoffSummary(d.handoff_summary); // 转人工交接摘要（客服视角展示）
        setMessages(d.messages.map(toChatMessage));
        // 溯源面板（2026-08-25）：历史加载默认选中最后一条 AI 回复 → 右栏显示其溯源；
        // 客服可再点其他 AI 回复切换（点哪条看哪条）。无 AI 回复（如纯人工会话）不选中。
        const lastAssistant = [...d.messages].reverse().find((m) => m.role === 'assistant');
        if (lastAssistant) {
          // 2026-08-25：默认选中同时透出该消息的快捷话术标记（answer_source）→
          // 右栏「预置话术无引用」空态跟随选中消息，不依赖实时流残留 state。
          onSelectMessageRef.current?.(lastAssistant.id, lastAssistant.sources ?? [], lastAssistant.answer_source);
        }
      })
      .catch(() => {
        // 加载失败保留空态，不阻断页面
      });
  }, [sessionParam, sessionId, reset]);

  // Branch 3：顾客端实时接收人工客服消息——3s 轮询 getSessionDetail，
  // 仅追加 role==='agent' 的新消息（按 id 去重；顾客自己的消息由发送/SSE 维护，不参与合并，
  // 因此流式期间轮询也不会产生重复）。观察视角（客服自己打开会话）不轮询：客服是发送方。
  // 审计 #5：轮询按需启动——仅在人工介入后（主动转人工 / AI handoff 建单 / 已有人工消息），
  // 纯 AI 对话不再全量轮询（省无谓请求）。客服在别处建单而顾客端未感知的场景不在此列（下次消息/刷新可见）。
  const intervention =
    !!manualTicket?.id || !!ticketId || messages.some((m) => m.role === 'agent');
  useEffect(() => {
    if (!sessionId || observeMode || !intervention) return;
    const timer = setInterval(() => {
      getSessionDetail(sessionId)
        .then((d) => {
          setMessages((prev) => {
            const existing = new Set(prev.map((m) => m.id));
            const fresh = d.messages
              .filter((m) => m.role === 'agent' && !existing.has(m.id))
              .map((m) => ({
                id: m.id,
                role: 'agent' as const,
                content: m.content,
                status: 'done' as const,
                createdAt: new Date(m.created_at).getTime(),
                ...(m.agent_name ? { agentName: m.agent_name } : {}),
              }));
            return fresh.length ? [...prev, ...fresh] : prev;
          });
        })
        .catch(() => {
          // 轮询失败静默，下一轮重试
        });
    }, 3000);
    return () => clearInterval(timer);
  }, [sessionId, observeMode, intervention]);

  // 2026-08-22：客服观察视角实时刷新——用户/AI 新消息 + 用户画像 + 交接摘要都要随会话演进
  // 更新。此前画像/摘要在打开会话时只拉一次：用户随后点快捷问题产生的画像和新消息，
  // 客服界面永远看不到，只有从待处理工单/历史会话重新进入才刷新（本次修复的由来）。
  // 客服自己发消息走乐观上屏（本地临时 id，落库后对齐服务端 id）——发送后短暂停一轮
  // 轮询，避免"服务端已落库 + 本地临时 id 未对齐"的窗口期拉出双份。
  const lastAgentSendRef = useRef(0);
  useEffect(() => {
    if (!sessionId || !observeMode) return;
    const timer = setInterval(() => {
      if (Date.now() - lastAgentSendRef.current < 4_000) return;
      getSessionDetail(sessionId)
        .then((d) => {
          setUserProfile(d.profile);
          setHandoffSummary(d.handoff_summary);
          setMessages((prev) => {
            const known = new Set(prev.map((m) => m.id));
            const fresh = d.messages
              .filter((m) => !known.has(m.id))
              .map(toChatMessage);
            return fresh.length ? [...prev, ...fresh] : prev;
          });
        })
        .catch(() => {
          /* 轮询失败静默：网络抖动下一轮重试 */
        });
    }, 4_000);
    return () => clearInterval(timer);
  }, [sessionId, observeMode]);

  // P0-1：流式结束（done/error）→ 按 user 消息 id 更新状态 + 追加 assistant（不重复追加 user）
  // 防重：streamingUserRef 在首次 finalize 时被置空；uid 为空 → 已 finalize，直接 return（防止
  // done 后 messages 变化（如点赞）触发 effect 重复追加 assistant——原 finalizedRef 语义等价物）
  useEffect(() => {
    if (stage !== 'done' && stage !== 'error') return;
    const u = streamingUserRef.current;
    if (!u) return; // 已 finalize 过 → 不重复追加
    streamingUserRef.current = null;
    // C1：流发起时的会话已切换 → 丢弃本流 finalize（旧回答不写入新会话）
    if (u.sessionId !== sessionId) return;
    const uid = u.id;
    const assistant: ChatMessage = {
      // C4：done 后本地 id 直接采用后端 message_id（唯一可引用）；失败态无后端 id 用本地占位
      id: stage === 'done' && messageId ? messageId : `a-${Date.now()}`,
      role: 'assistant',
      content: tokens || (stage === 'error' ? error?.message || '服务异常，请稍后重试' : ''),
      sources,
      status: stage === 'done' ? 'done' : 'failed',
      ...(stage === 'done' && messageId ? { messageId } : {}),
      ...(ticketId ? { ticketId } : {}), // T1：handoff 建单工单号 → 气泡提示
      ...(tool ? { tool } : {}), // 批次D/T3：工具回答标记 → 气泡来源徽章
    };
    setMessages((prev) => {
      const next = prev.map((m) =>
        m.id === uid
          ? {
              ...m,
              status: (stage === 'done' ? 'done' : 'failed') as ChatMessage['status'],
              // C4：done 后用后端真 id 对齐本地 user 消息 id（供引用/编辑/定位）
              ...(stage === 'done' && userMessageId ? { id: userMessageId } : {}),
            }
          : m
      );
      return [...next, assistant];
    });
    // U1：失败时记住用户消息 → Composer 旁"重试"按钮；成功/新发送时清空（重试复用幂等键，不重复扣费）
    setRetryText(stage === 'error' && u.clientMsgId ? { text: u.text, clientMsgId: u.clientMsgId } : null);
    // P2-2：成功完成一轮对话 → 轮次 +1（done 才计，error 不计）
    if (stage === 'done') setTurnCount((n) => n + 1);
    // 快捷话术标记推送：每轮 finalize 覆盖（done 带 answer_source=quick；普通轮 undefined 清除）
    onAnswerSourceChange?.(stage === 'done' ? answerSource : undefined);
    reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- messages 不参与依赖（防 done 后点赞等触发重复 finalize）
  }, [stage, tokens, sources, messageId, userMessageId, ticketId, tool, answerSource, error, reset, sessionId, onAnswerSourceChange]);

  const onSend = useCallback(
    async (text: string, clientMsgId?: string): Promise<boolean> => {
      // C2：流式中拒绝并发新发送（返回 false 保留用户输入，避免误清空）
      if (streaming) return false;
      setCreateError(null); // 新发送清空创建错误
      setRetryText(null); // 新发送即清空重试标记

      // W5 + Branch 3：客服视角 + 已转人工 → 发送"人工客服"消息（右侧）。
      // 乐观上屏保手感；随即 POST /sessions/{id}/messages 落库（role=agent），
      // 成功后用后端 id 对齐本地消息 → 顾客端轮询按 id 去重，不会出现双份。
      if (isStaff && intervened) {
        lastAgentSendRef.current = Date.now(); // 观察视角轮询让位乐观上屏窗口（见该 effect 注释）
        const aid = `ag-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
        const agentName = useAuthStore.getState().user?.email ?? '人工客服';
        setMessages((prev) => [
          ...prev,
          { id: aid, role: 'agent', content: text, status: 'done', createdAt: Date.now(), agentName },
        ]);
        if (sessionId) {
          sendAgentMessage(sessionId, text)
            .then((m) => {
              setMessages((prev) =>
                prev.map((msg) =>
                  msg.id === aid
                    ? { ...msg, id: m.id, messageId: m.id, agentName: m.agent_name ?? agentName }
                    : msg
                )
              );
            })
            .catch(() => {
              // 落库失败：保留本地气泡（刷新即丢，属已知降级，不打断客服输入）
            });
        }
        return true;
      }

      // 顾客 / AI 路径：未转人工前，客服也以"顾客"身份模拟发问（左侧），触发 AI 回答（左侧）
      let sid = sessionId;
      if (!sid) {
        setCreating(true);
        try {
          // P1-2：会话标题 = 用户第一句话截前 12 字（后端已支持持久化）
          const title = text.trim().slice(0, 12);
          const s = await createSession(title);
          sid = s.id;
          setSessionId(sid);
          // P0-3：首次发问成功后把 session_id 同步到 URL（replaceState 不触发路由重渲染）
          try {
            window.history.replaceState({}, '', `?session=${sid}`);
          } catch {
            /* URL 更新失败不影响对话 */
          }
        } catch {
          setCreating(false);
          setCreateError('会话创建失败，请重试');
          return false; // P0-2：失败 → Composer 保留输入
        }
        setCreating(false);
      }
      // R2/C4：客户端提问幂等键（重试复用，配额幂等扣费）；本地消息 id 用稳定值，done 后对齐后端真 id
      const cmid = clientMsgId || `c-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      const uid = `u-${cmid}`;
      streamingUserRef.current = { id: uid, text, sessionId: sid, clientMsgId: cmid };
      setMessages((prev) => [
        ...prev,
        { id: uid, role: 'user', content: text, status: 'sending', createdAt: Date.now() },
      ]);
      try {
        await stream({ session_id: sid!, content: text, client_msg_id: cmid });
      } catch {
        // stream 内部错误已通过 SSE error 事件处理；此处兜底
      }
      return true;
    },
    [sessionId, stream, streaming, isStaff, intervened]
  );

  // U1：一键重试——重发失败的那条用户消息（复用幂等键，后端不重复扣费）
  const onRetry = useCallback(() => {
    if (retryText) onSend(retryText.text, retryText.clientMsgId);
  }, [retryText, onSend]);

  // P0-4 / W5：主动转人工。视角切换(干预)立即乐观生效，不受后端成败影响；
  // 建单按角色分支：staff 看的是顾客会话，不能走 escalate 的"自己的会话"校验 → 用建单 POST /tickets；
  // user 用自己的会话 → 用 escalate。两端点后端均已支持（此前 staff 走 escalate 必 403 且 setIntervened 被 catch 吞掉）。
  const onEscalate = useCallback(async () => {
    if (!sessionId) return;
    setIntervened(true); // W5：转人工后，客服发送的消息切换为"人工客服"角色（右侧）—— 先切换，保证可见行为必定发生
    setManualTicket({ id: '', loading: true, error: null });
    try {
      const t = isStaff ? await createTicket(sessionId) : await escalateSession(sessionId);
      setManualTicket({ id: t.ticket_id, loading: false, error: null });
    } catch {
      setManualTicket({ id: '', loading: false, error: '转人工失败，请稍后重试' });
    }
  }, [sessionId, isStaff]);

  // W5：客服手动建单（独立动作，仅记录工单，不改变介入视角）
  const onCreateTicket = useCallback(async () => {
    if (!sessionId) return;
    setTicketCreated({ id: '', error: null });
    try {
      const t = await createTicket(sessionId);
      setTicketCreated({ id: t.ticket_id, error: null });
    } catch {
      setTicketCreated({ id: '', error: '建单失败，请稍后重试' });
    }
  }, [sessionId]);

  // 批次A：请求 AI 建议。失败静默（建议是辅助能力，不打断客服主流程）
  // 终审②在途竞态守卫：请求发起时捕获 sessionId，await 返回后与最新会话比对，不一致（客服
  // 已切会话）则丢弃。闭包里的 sessionId 与捕获值同源恒等（deps 含 sessionId），故用 ref 读最新值。
  sessionIdRef.current = sessionId;
  const onAskSuggest = useCallback(
    async (refresh = false) => {
      if (!sessionId) return;
      const reqSid = sessionId; // 请求发起时的会话
      setAiSuggest({ text: '', sources: [], loading: true });
      try {
        // 大扫查修复：「重新生成」传 refresh=true 绕过 60s 结果缓存，否则 TTL 内返回同一文本
        const r = await suggestReply(reqSid, undefined, refresh);
        if (reqSid !== sessionIdRef.current) return; // 会话已切换 → 丢弃旧建议
        setAiSuggest({ text: r.text, sources: r.sources, loading: false });
      } catch {
        if (reqSid === sessionIdRef.current) setAiSuggest(null);
      }
    },
    [sessionId],
  );

  const onRate = useCallback(
    async (id: string, rating: 'up' | 'down') => {
      setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, feedback: rating } : m)));
      const target = messages.find((m) => m.id === id);
      if (target?.messageId) {
        try {
          await sendFeedback(target.messageId, { rating });
        } catch {
          // 乐观更新已生效；上报失败静默，避免打断对话
        }
      }
    },
    [messages]
  );

  // P2-2：会话满意度评分（幂等覆盖；失败静默——满意度是补充指标，不打断对话）
  const onSatisfaction = useCallback(
    async (rating: 'satisfied' | 'neutral' | 'unsatisfied') => {
      if (!sessionId) return;
      try {
        await rateSatisfaction(sessionId, rating);
        setSatisfactionRated(true);
      } catch {
        // 失败静默（条已隐藏，避免重复提交）
      }
    },
    [sessionId]
  );

  return (
    <div className="chat-container">
      {observeMode && (
        <div className="chat-header">
          <div className="chat-header__info">
            <div className="chat-header__avatar" aria-hidden="true">
              <UserOutlined />
            </div>
            <div>
              <div className="chat-header__name">会话 {sessionId?.slice(0, 8) ?? '...'}</div>
              <div className="chat-header__status">
                <span className="chat-header__dot" />
                {streaming
                  ? 'AI 回复中…'
                  : manualTicket?.id
                    ? '已转人工'
                    : intervened
                      ? '人工客服接待中'
                      : 'AI 自动接待'}
              </div>
              {/* 转人工交接摘要（本次会话上下文中压缩打包）；仅客服视角 */}
              {handoffSummary && <HandoffSummary summary={handoffSummary} />}
              {/* Phase D：用户画像摘要（客服视角；后端仅 agent/admin 返回 profile，顾客端无此块） */}
              {userProfile && <UserProfileSummary profile={userProfile} />}
            </div>
          </div>
        </div>
      )}
      <div className="chat-container__body">
        {/* 显示快捷问题：在 AI 首次回复前都显示，允许用户连续点击多个快捷问题 */}
        {!messages.some(m => m.role === 'assistant') && (
          <div className="chat-container__empty-wrap">
            {/* V5：品牌化欢迎块（替代 antd Empty 的"空态感"） */}
            <div className="chat-welcome">
              <div className="chat-welcome__icon" aria-hidden="true">
                <svg viewBox="0 0 24 24" width="34" height="34" fill="none">
                  <path
                    d="M12 3C7 3 3 6.8 3 11.5c0 2.6 1.3 4.9 3.4 6.4V21l3.2-1.9c.7.2 1.5.3 2.4.3 5 0 9-3.8 9-8.5S17 3 12 3z"
                    fill="var(--chat-avatar-fill)"
                    opacity="0.55"
                  />
                  <circle cx="8.7" cy="11.3" r="1.2" fill="var(--chat-avatar-dot)" />
                  <circle cx="12.3" cy="11.3" r="1.2" fill="var(--chat-avatar-dot)" />
                  <circle cx="15.9" cy="11.3" r="1.2" fill="var(--chat-avatar-dot)" />
                </svg>
              </div>
              <div className="chat-welcome__title">您好，我是灵犀智能客服</div>
              <div className="chat-welcome__sub">可点击下方问题快速开始，或直接输入您的问题</div>
            </div>
            <div className="chat-container__hot">
              {QUESTION_GROUPS.filter((g) => g.questions.some((q) => q.featured)).map((g) => (
                <div key={g.key} className="hot-card">
                  <div className="hot-card__head">
                    <span className="hot-card__icon">{g.icon}</span>
                    <span className="hot-card__title">{g.title}</span>
                  </div>
                  <div className="hot-card__list">
                    {g.questions.filter((it) => it.featured).map((it) => (
                      <button
                        key={it.q}
                        type="button"
                        className="hot-card__item"
                        onClick={() => onSend(it.q)}
                        disabled={creating || streaming}
                      >
                        <span className="hot-card__q">{it.q}</span>
                        <span className="hot-card__d">{it.d}</span>
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </div>
            <div className="hot-more">
              <button
                type="button"
                className="hot-more__toggle"
                onClick={() => setMoreOpen((v) => !v)}
                disabled={creating || streaming}
                aria-expanded={moreOpen}
              >
                {moreOpen ? '收起常见问题' : `更多常见问题（共 ${QUESTION_GROUPS.reduce((n, g) => n + g.questions.filter((x) => !x.featured).length, 0)} 个）`}
              </button>
              {moreOpen && (
                <div className="hot-more__body">
                  {QUESTION_GROUPS.map((g) => {
                    const others = g.questions.filter((it) => !it.featured);
                    if (!others.length) return null;
                    return (
                      <div key={g.key} className="hot-more__group">
                        <div className="hot-more__group-title">
                          <span className="hot-card__icon">{g.icon}</span>
                          <span>{g.title}</span>
                          <span className="hot-more__count">{others.length}</span>
                        </div>
                        <div className="hot-more__list">
                          {others.map((it) => (
                            <button
                              key={it.q}
                              type="button"
                              className="hot-more__item"
                              onClick={() => onSend(it.q)}
                              disabled={creating || streaming}
                            >
                              {it.q}
                            </button>
                          ))}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        )}
        <MessageList
          messages={messages}
          stream={{ stage, tokens, error }}
          layout={chatLayout}
          onRate={onRate}
          selectedMsgId={selectedMsgId}
          onSelectMessage={onSelectMessage}
        />
        {manualTicket && (
          <TicketNotice kind="escalate" state={manualTicket} isStaff={isStaff} />
        )}
        {/* P2-2：会话满意度（≥2 轮完成且未评分时内联出现） */}
        {turnCount >= 2 && !satisfactionRated && sessionId && !manualTicket?.loading && !observeMode && (
          <SatisfactionBar onRate={onSatisfaction} />
        )}
        {/* W5：建单结果气泡（与转人工独立） */}
        {ticketCreated && (
          <TicketNotice kind="create" state={ticketCreated} isStaff={isStaff} />
        )}
      </div>
      {/* 批次A：坐席辅助建议卡片（手动触发出现；空建议提示语由后端 fail-open 语义决定） */}
      {aiSuggest && !aiSuggest.loading && (
        <div className="chat-suggest-card" role="region" aria-label="AI 建议回复">
          <div className="chat-suggest-card__head">
            <BulbOutlined />
            <span>AI 建议回复</span>
            <Button type="text" size="small" aria-label="关闭建议" onClick={() => setAiSuggest(null)}>
              <CloseOutlined />
            </Button>
          </div>
          <div className="chat-suggest-card__text">
            {aiSuggest.text || '暂无建议：知识库未覆盖该问题，可人工答复或向顾客确认更多信息。'}
          </div>
          {aiSuggest.sources.length > 0 && (
            <div className="chat-suggest-card__sources">
              {aiSuggest.sources.map((s, i) => (
                <span key={`${s.chunk_id}-${i}`} className="chat-suggest-card__src" title={s.snippet}>
                  {s.doc_title} · {Math.round(s.score * 100)}%
                </span>
              ))}
            </div>
          )}
          <div className="chat-suggest-card__actions">
            <Button
              size="small"
              type="primary"
              disabled={!aiSuggest.text}
              onClick={() => fillRef.current?.(aiSuggest.text)}
            >
              填入输入框
            </Button>
            <Button size="small" onClick={() => onAskSuggest(true)}>
              重新生成
            </Button>
          </div>
        </div>
      )}
      <div className="chat-container__footer">
        {createError && (
          <div className="chat-container__error" role="alert">
            {createError}
          </div>
        )}
        {/* W5：客服视角（observe）下保留输入框，并把"转人工 / 建单"按钮移到输入框左侧同一行 */}
        {observeMode ? (
          <div className="chat-observe-row">
            <div className="chat-observe-actions">
              <Button
                type="primary"
                loading={manualTicket?.loading}
                disabled={intervened || !!manualTicket?.loading}
                onClick={onEscalate}
              >
                {intervened ? '已转人工' : '转人工'}
              </Button>
              <Button
                icon={<FileAddOutlined />}
                loading={ticketCreated !== null && !ticketCreated.id && !ticketCreated.error}
                disabled={!!ticketCreated?.id}
                onClick={onCreateTicket}
              >
                {ticketCreated?.id ? '已建单' : '建单'}
              </Button>
              <Button
                icon={<BulbOutlined />}
                loading={aiSuggest?.loading}
                disabled={streaming || creating}
                onClick={() => onAskSuggest()}
              >
                AI 推荐
              </Button>
            </div>
            <div className="chat-observe-composer">
              <Composer
                disabled={streaming || creating}
                onSend={onSend}
                retry={retryText ? { text: retryText.text, onRetry } : null}
                onEscalate={undefined}
                onRegisterFill={registerFill}
                onStop={stop}
                centered={false}
              />
            </div>
          </div>
        ) : (
          <Composer
            disabled={streaming || creating}
            onSend={onSend}
            retry={retryText ? { text: retryText.text, onRetry } : null}
            onEscalate={sessionId && !manualTicket?.loading ? onEscalate : undefined}
            onRegisterFill={registerFill}
            onStop={stop}
            centered={false}
          />
        )}
      </div>
    </div>
  );
}

/** 转人工交接摘要胶囊（本次会话上下文压缩打包）：主题 + 关联实体 + 最近用户诉求。
 * 仅客服视角（observeMode）展示，帮助人工客服在介入前快速掌握本次对话上下文。 */
function HandoffSummary({ summary }: { summary: SessionDetail['handoff_summary'] }) {
  if (!summary) return null;
  const { topic, entities, question } = summary;
  if (!topic && !entities?.length && !question) return null;
  return (
    <div className="chat-handoff" aria-label="转人工交接摘要">
      {topic && (
        <div className="chat-handoff__row">
          <span className="chat-handoff__label">主题</span>
          <span className="chat-handoff__topic">{topic}</span>
        </div>
      )}
      {entities && entities.length > 0 && (
        <div className="chat-handoff__row">
          <span className="chat-handoff__label">关联</span>
          <span>{entities.join('、')}</span>
        </div>
      )}
      {question && (
        <div className="chat-handoff__row">
          <span className="chat-handoff__label">诉求</span>
          <span className="chat-handoff__question">{question}</span>
        </div>
      )}
    </div>
  );
}

/** Phase D：用户画像摘要胶囊（客服视角头部展示，替代裸 JSON——客服一眼看懂用户画像）。 */
function UserProfileSummary({ profile }: { profile: SessionDetail['profile'] }) {
  if (!profile) return null;
  const tags: string[] = [];
  const topics = profile.topics;
  if (topics && Object.keys(topics).length) {
    // 取 Top3 常问主题
    const top = Object.entries(topics)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 3)
      .map(([k]) => k);
    if (top.length) tags.push(`常问:${top.join('/')}`);
  }
  const entities = profile.entities;
  if (entities && entities.length) {
    tags.push(`订单:${entities.slice(0, 2).join('/')}`);
  }
  const sat = profile.satisfaction;
  if (sat && (sat.up || sat.down)) {
    tags.push(`满意度:赞${sat.up ?? 0}/踩${sat.down ?? 0}`);
  }
  const handoff = profile.handoff;
  if (handoff?.count) {
    tags.push(handoff.count >= 2 ? '高优服务' : '曾转人工');
  }
  const prefs = profile.preferences?.['品类'];
  if (prefs && prefs.length) {
    tags.push(`偏好:${prefs.slice(0, 2).join('/')}`);
  }
  if (!tags.length) return null;
  return (
    <div className="chat-header__profile" aria-label="用户画像摘要">
      {tags.map((t) => (
        <span key={t} className="chat-header__profile-tag">
          {t}
        </span>
      ))}
    </div>
  );
}
