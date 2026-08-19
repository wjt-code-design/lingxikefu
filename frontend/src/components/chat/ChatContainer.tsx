import { useCallback, useEffect, useRef, useState } from 'react';
import { useSearchParams, Link } from 'react-router-dom';
import { Button } from 'antd';
import { SwapOutlined, TruckOutlined, ToolOutlined, SafetyCertificateOutlined, UserOutlined } from '@ant-design/icons';
import { sendFeedback } from '@/api/chat';
import { createSession, getSessionDetail, rateSatisfaction } from '@/api/sessions';
import { escalateSession } from '@/api/tickets';
import { useAuthStore } from '@/store/authStore';
import { useChatStream } from '@/hooks/useChatStream';
import type { MessageSource } from '@/contracts/api';
import { Composer } from './Composer';
import { MessageList } from './MessageList';
import { SatisfactionBar } from './SatisfactionBar';
import type { ChatMessage } from './types';

/** F3：首屏快捷问题场景化卡片（图标 + 标题 + 描述，点击直接发送，移动端零门槛） */
const HOT_SCENARIOS = [
  {
    key: 'return',
    icon: <SwapOutlined />,
    title: '售后退换',
    questions: [
      { q: '七天无理由退货怎么申请？', d: '在线申请 · 快速退款' },
      { q: '退款一般多久到账？', d: '原路退回 · 1-3 个工作日' },
    ],
  },
  {
    key: 'logistics',
    icon: <TruckOutlined />,
    title: '配送物流',
    questions: [
      { q: '支持哪些支付方式？', d: '微信 / 支付宝 / 银联' },
      { q: '可以开发票吗？', d: '电子发票 · 随时申请' },
    ],
  },
  {
    key: 'warranty',
    icon: <ToolOutlined />,
    title: '保修维修',
    questions: [{ q: '保修多久？', d: '整机保修 · 全国联保' }],
  },
  {
    key: 'account',
    icon: <SafetyCertificateOutlined />,
    title: '账户支付',
    questions: [{ q: '如何修改收货地址？', d: '个人中心 · 随时修改' }],
  },
];

/**
 * 对话挂件容器（FE-03 核心）：
 * - 会话懒创建：首次发问时 POST /sessions 拿 session_id；
 * - 带参进入（M8）：/chat?session={id} 加载既有会话历史（agent 查看用户会话）；
 * - SSE 流式：useChatStream 消费 stage/token/sources/done/error；
 * - 消息归并：流式 done/error 后将 user + assistant 消息并入历史，反馈条接 sendFeedback。
 */
export function ChatContainer({
  onSourcesChange,
  onRegisterFill,
}: {
  /** 右栏溯源面板订阅 sources（三栏工作台用）；不传则忽略。 */
  onSourcesChange?: (s: MessageSource[]) => void;
  /** P1-3：快捷话术 → 填入输入框能力注册（WorkbenchLayout 透传给 SourcePanel） */
  onRegisterFill?: (fill: (text: string) => void) => void;
}) {
  const { stage, tokens, sources, messageId, userMessageId, ticketId, error, reset, stop, stream } = useChatStream();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null); // I-1：会话创建失败提示
  const [retryText, setRetryText] = useState<{ text: string; clientMsgId: string } | null>(null); // U1：最近失败消息 → 一键重试（含幂等键，重试复用不重复扣费）
  const [searchParams] = useSearchParams();
  const sessionParam = searchParams.get('session');
  const role = useAuthStore((s) => s.role);
  // 客服视角：agent/admin 打开既有会话（?session=）时进入 observe 模式——
  // 顾客与 AI 的对话统一在左，人工介入区在右（demo 招牌布局）；用户侧（自己聊天）保持原样。
  const observeMode = (role === 'agent' || role === 'admin') && !!sessionParam;
  // 当前流式对应的用户消息（P0-1：done/error 时按 id 定位并更新；text 用于失败重试兜底）
  // C1/C2：记录发起流时的 user 消息 + 会话；finalize 时比对 sessionId 防串台、防 ref 覆盖丢回答
  // R2：clientMsgId 为客户端提问幂等键（重试复用，配额幂等扣费）
  const streamingUserRef = useRef<{ id: string; text: string; sessionId: string | null; clientMsgId: string | null } | null>(null);
  // P0-4：手动转人工结果气泡（独立于 SSE 流，HTTP 响应驱动）
  const [manualTicket, setManualTicket] = useState<{ id: string; loading: boolean; error: string | null } | null>(null);
  // P2-2：会话满意度——对话轮次 ≥2 后内联出现，评分一次后隐藏
  const [turnCount, setTurnCount] = useState(0);
  const [satisfactionRated, setSatisfactionRated] = useState(false);

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
      if (prev && sessionId) {
        setSessionId(null);
        setMessages([]);
        streamingUserRef.current = null;
        setTurnCount(0); // P2-2：新会话重置满意度轮次
        setSatisfactionRated(false);
        reset(); // C1：切到新会话 → abort 旧流
      }
      return;
    }
    if (sessionParam === sessionId) return;
    reset(); // C1：切换到另一会话 → abort 旧流
    getSessionDetail(sessionParam)
      .then((d) => {
        setSessionId(d.id);
        setMessages(
          d.messages.map((m) => ({
            id: m.id,
            // P2：契约扩展 agent role 后透传所有角色（含人工客服消息）
            role: m.role as 'user' | 'assistant' | 'agent',
            content: m.content,
            status: 'done',
            createdAt: new Date(m.created_at || Date.now()).getTime(),
            ...(m.role === 'assistant' ? { messageId: m.id } : {}),
            ...(m.role === 'agent' ? { agentName: m.agent_name } : {}),
          }))
        );
      })
      .catch(() => {
        // 加载失败保留空态，不阻断页面
      });
  }, [sessionParam, sessionId, reset]);

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
    reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- messages 不参与依赖（防 done 后点赞等触发重复 finalize）
  }, [stage, tokens, sources, messageId, userMessageId, ticketId, error, reset, sessionId]);

  const onSend = useCallback(
    async (text: string, clientMsgId?: string): Promise<boolean> => {
      // C2：流式中拒绝并发新发送（Composer/热门卡片已 disabled，此处兜底防 ref 覆盖丢回答）
      if (streaming) return true;
      setCreateError(null); // 新发送清空创建错误
      setRetryText(null); // 新发送即清空重试标记
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
    [sessionId, stream, streaming]
  );

  // U1：一键重试——重发失败的那条用户消息（复用幂等键，后端不重复扣费）
  const onRetry = useCallback(() => {
    if (retryText) onSend(retryText.text, retryText.clientMsgId);
  }, [retryText, onSend]);

  // P0-4：主动转人工 → escalate 端点（独立 HTTP 响应气泡，非 SSE）
  const onEscalate = useCallback(async () => {
    if (!sessionId) return;
    setManualTicket({ id: '', loading: true, error: null });
    try {
      const t = await escalateSession(sessionId);
      setManualTicket({ id: t.ticket_id, loading: false, error: null });
    } catch {
      setManualTicket({ id: '', loading: false, error: '转人工失败，请稍后重试' });
    }
  }, [sessionId]);

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
                {streaming ? 'AI 回复中…' : manualTicket?.id ? '已转人工' : 'AI 自动接待'}
              </div>
            </div>
          </div>
          <div className="chat-header__actions">
            <Button
              size="small"
              icon={<SwapOutlined />}
              disabled={!!manualTicket?.loading}
              onClick={onEscalate}
            >
              {manualTicket?.loading ? '转接中…' : '转人工'}
            </Button>
          </div>
        </div>
      )}
      <div className="chat-container__body">
        {messages.length === 0 && !streaming && (
          <div className="chat-container__empty-wrap">
            {/* V5：品牌化欢迎块（替代 antd Empty 的"空态感"） */}
            <div className="chat-welcome">
              <div className="chat-welcome__icon" aria-hidden="true">
                <svg viewBox="0 0 24 24" width="34" height="34" fill="none">
                  <path
                    d="M12 3C7 3 3 6.8 3 11.5c0 2.6 1.3 4.9 3.4 6.4V21l3.2-1.9c.7.2 1.5.3 2.4.3 5 0 9-3.8 9-8.5S17 3 12 3z"
                    fill="#96C8E8"
                    opacity="0.55"
                  />
                  <circle cx="8.7" cy="11.3" r="1.2" fill="#539FD8" />
                  <circle cx="12.3" cy="11.3" r="1.2" fill="#539FD8" />
                  <circle cx="15.9" cy="11.3" r="1.2" fill="#539FD8" />
                </svg>
              </div>
              <div className="chat-welcome__title">您好，我是灵犀智能客服</div>
              <div className="chat-welcome__sub">可点击下方问题快速开始，或直接输入您的问题</div>
            </div>
            <div className="chat-container__hot">
              {HOT_SCENARIOS.map((g) => (
                <div key={g.key} className="hot-card">
                  <div className="hot-card__head">
                    <span className="hot-card__icon">{g.icon}</span>
                    <span className="hot-card__title">{g.title}</span>
                  </div>
                  <div className="hot-card__list">
                    {g.questions.map((it) => (
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
          </div>
        )}
        <MessageList
          messages={messages}
          stream={{ stage, tokens, error }}
          layout={observeMode ? 'observe' : 'self'}
          onRate={onRate}
        />
        {manualTicket && (
          <div className="chat-msg chat-msg--ai">
            <div className="chat-msg__bubble">
              <div className={`chat-msg__ticket${manualTicket.error ? ' chat-msg__ticket--error' : ''}`}>
                {manualTicket.loading ? (
                  '正在为您转人工…'
                ) : manualTicket.error ? (
                  manualTicket.error
                ) : (
                  <>
                    已为您转人工，工单号 <b>#{manualTicket.id.slice(0, 8)}</b>，客服将尽快跟进{' '}
                    <Link to="/tickets">查看我的工单</Link>
                  </>
                )}
              </div>
            </div>
          </div>
        )}
        {/* P2-2：会话满意度（≥2 轮完成且未评分时内联出现） */}
        {turnCount >= 2 && !satisfactionRated && sessionId && !manualTicket?.loading && !observeMode && (
          <SatisfactionBar onRate={onSatisfaction} />
        )}
      </div>
      <div className="chat-container__footer">
        {createError && (
          <div className="chat-container__error" role="alert">
            {createError}
          </div>
        )}
        {observeMode ? (
          <div className="chat-intervene">
            <div className="chat-observe-banner" role="status">
              客服视角 · 左侧为顾客与 AI 的对话，您可在此介入
            </div>
            <div className="chat-intervene__actions">
              <Button
                type="primary"
                loading={manualTicket?.loading}
                disabled={!!manualTicket?.id}
                onClick={onEscalate}
              >
                转人工 / 建单
              </Button>
              {/* 契约无 agent 回复端点（Message.role 仅 user/assistant），代客回复待后端接入，先禁用并标注，绝不假接线 */}
              <Button disabled title="后端 agent 回复端点待接入（规划中）">
                代客回复
              </Button>
            </div>
          </div>
        ) : (
          <Composer
            disabled={streaming || creating}
            onSend={onSend}
            retry={retryText ? { text: retryText.text, onRetry } : null}
            onEscalate={sessionId && !manualTicket?.loading ? onEscalate : undefined}
            onRegisterFill={onRegisterFill}
            onStop={stop}
          />
        )}
      </div>
    </div>
  );
}
