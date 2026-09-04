import { useState } from 'react';
import type { KeyboardEvent as ReactKeyboardEvent, MouseEvent as ReactMouseEvent } from 'react';
import { useAuthStore } from '@/store/authStore';
import { Button, Tag, Typography, message } from 'antd';
import { CopyOutlined, CustomerServiceOutlined } from '@ant-design/icons';
import type { ChatMessage } from './types';
import type { MessageSource } from '@/contracts/api';
import { MarkdownContent } from '@/components/common/MarkdownContent';
import { SourceAccordion } from './SourceAccordion';
import { ThumbsBar } from './ThumbsBar';
import { OrderCards } from './OrderCards';
import { detectOrderTrack } from './orderTrack';
import { TicketStatusBadge } from './TicketStatusBadge';

/** T3.3：工具回答来源标签映射——新增工具只加配置不改组件；未知值兜底显示原始 key。
 * 注（大扫查 2026-08-25）：当前后端仅产出 order_query（chat.py 订单直答分支）；
 * kb_lookup / llm 为预置映射，后端暂未在 done.tool 下发。 */
const TOOL_LABEL_MAP: Record<string, string> = {
  order_query: '订单查询',
  kb_lookup: '知识库',
  llm: 'AI 生成',
};

/** 单条消息气泡（角色 + 视角双维度 · 2026-08-19）
 *
 * 角色（role）：
 *   - 'user'      : 顾客消息
 *   - 'assistant' : AI 智能客服消息
 *   - 'agent'     : 人工客服消息（P2 接入）
 *
 * 视角（layout）：
 *   - 'self'    : 用户自身在对话里（用户侧 / 当前客服）
 *   - 'observe' : 客服视角，监控顾客和 AI 的会话
 *
 * 左右规则（B 视野感知 · 与 demo 对齐）：
 *   self 视角下：
 *     - user → 右（"我"）
 *     - assistant / agent → 左（对面的客服/AI）
 *   observe 视角下：
 *     - user / assistant → 左（顾客 + AI 在被监控线程）
 *     - agent → 右（"我"，当前登录客服）
 *
 * assistant 消息按 Markdown 渲染（M7），rehype-sanitize 兜底防 XSS；
 * user / agent 保持纯文本，避免富文本注入。 */
function getVariant(role: ChatMessage['role'], layout: 'self' | 'observe'): string {
  const isCustomer = role === 'user'; // user 角色消息即"顾客消息"，不论 self/observe
  const isAgent = role === 'agent';
  if (layout === 'self') {
    return isCustomer ? 'user' : isAgent ? 'agent' : 'ai';
  }
  // observe
  return isCustomer ? 'observe-customer' : isAgent ? 'agent' : 'observe-ai';
}

export function MessageBubble({
  msg,
  onRate,
  layout = 'self',
  selected = false,
  onSelect,
}: {
  msg: ChatMessage;
  onRate: (rating: 'up' | 'down') => void;
  /** 'self' = 用户侧（user 右 / AI+agent 左）；'observe' = 客服视角（customer+ai 左 / agent 右） */
  layout?: 'self' | 'observe';
  /** 溯源选中态（2026-08-25）：当前被右栏溯源面板查看的 AI 回复 → 气泡高亮 */
  selected?: boolean;
  /** 点击 AI 回复 → 通知右栏溯源面板切换（点哪条看哪条；answerSource 透出该条快捷话术标记） */
  onSelect?: (msgId: string, sources: MessageSource[], answerSource?: string) => void;
}) {
  const variant = getVariant(msg.role, layout);
  const isUser = msg.role === 'user';
  const isAgent = msg.role === 'agent';
  const isAi = msg.role === 'assistant';
  const selfSide = layout === 'self' ? isUser : isAgent; // self 视角的右栏身份
  const authUser = useAuthStore((s) => s.user);
  const role = useAuthStore((s) => s.role);
  const userInitial = (authUser?.email?.charAt(0) ?? '我').toUpperCase();
  // 2026-08-21：检索来源仅客服/管理员可见（工作台溯源），顾客端不展示来源（用户确认的方向）。
  const isStaff = role === 'admin' || role === 'agent';
  // 溯源选中（2026-08-25）：客服视角下 AI 回复可点击，右栏溯源面板切换到该条来源。
  // 顾客消息 / 人工客服消息不可点（无溯源）；点击无来源的 AI 回复 → 面板显示"暂无引用来源"。
  const clickable = isAi && isStaff;
  const handleSelect = () => {
    if (clickable) onSelect?.(msg.id, msg.sources ?? [], msg.answerSource);
  };
  // code-review F1（2026-08-25）：气泡内含复制/溯源折叠/点赞等交互控件——点击这些控件不应误切溯源面板；
  // 用 closest 拦截事件源头，避免按钮点击冒泡触发 handleSelect 的副作用。
  const handleRootClick = (e: ReactMouseEvent<HTMLDivElement>) => {
    if (!clickable) return;
    // 从点击目标向上遍历（不含根容器自身）：命中内部交互控件（复制/折叠/点赞等）→ 不误切溯源面板。
    // 注意：根容器自身 role="button" 不能计入拦截，否则任何内部点击都会被拦掉导致选中失效。
    let el: HTMLElement | null = e.target as HTMLElement;
    while (el && el !== e.currentTarget) {
      if (el.matches('button, a, [role="button"], input, textarea, select')) return;
      el = el.parentElement;
    }
    handleSelect();
  };
  // code-review F1（2026-08-25）：role=button 需键盘可激活（WCAG 2.1.1）——Enter/Space 触发面板切换。
  const handleKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    if (!clickable) return;
    if (e.key !== 'Enter' && e.key !== ' ') return;
    // 焦点在内部交互控件上时，交由其自身处理，不触发面板切换（与点击拦截保持一致）
    let el: HTMLElement | null = e.target as HTMLElement;
    while (el && el !== e.currentTarget) {
      if (el.matches('button, a, [role="button"], input, textarea, select')) return;
      el = el.parentElement;
    }
    e.preventDefault();
    handleSelect();
  };
  const [copied, setCopied] = useState(false);
  // 批次 1：引用角标点击联动——正文 <sup>N</sup> 点击 → 展开来源面板并高亮对应条目
  const [sourceOpen, setSourceOpen] = useState(false);
  const [highlightN, setHighlightN] = useState<number | null>(null);
  // 交互角标仅 staff 且有 sources 时启用（顾客端保持纯展示 chip，天然降级）
  const hasSources = isAi && isStaff && !!msg.sources && msg.sources.length > 0;
  const onCitationClick = (n: number) => {
    // 守卫：N 超界（LLM 幻觉来源）或流式期间 sources 未达 → 静默 no-op
    if (!isStaff || !msg.sources || n < 1 || n > msg.sources.length) return;
    setSourceOpen(true);
    setHighlightN(n);
  };
  // P0-1：sending 态半透明 + failed 态红色提示（可重试）
  const statusCls = msg.status === 'sending' ? ' chat-msg__bubble--sending' : msg.status === 'failed' ? ' chat-msg__bubble--failed' : '';

  // U3：assistant 消息 hover 复制全文（clipboard 在无头环境缺失时静默降级）
  const onCopy = async () => {
    try {
      await navigator.clipboard?.writeText(msg.content);
      setCopied(true);
      message.success('已复制');
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* 剪贴板不可用（非 https/权限）→ 静默，不打断阅读 */
    }
  };

  // 头像渲染：自我侧头像用 email 首字母；其他侧按角色区分（customer→暖橙圆、agent→工牌、assistant→AI 机器人 SVG）
  // observe 视角下用户消息统一用 --customer 头像（暖橙），与 AI 冷蓝头像区分
  const renderAvatar = () => {
    if (isUser) {
      // self 视角：用户自己 → 海盐蓝圆形 + 本人首字母；observe 视角：顾客 → 暖橙圆形 + 固定"客"
      // （2026-08-20：observe 下此前误用当前登录客服的首字母，语义错位 → 改"客"）
      const avatarCls = layout === 'observe' ? 'chat-msg__avatar--customer' : 'chat-msg__avatar--user';
      return (
        <div
          className={`chat-msg__avatar ${avatarCls}`}
          aria-hidden="true"
        >
          {layout === 'observe' ? '客' : userInitial}
        </div>
      );
    }
    if (isAgent) {
      return (
        <div className="chat-msg__avatar chat-msg__avatar--agent" aria-hidden="true">
          <CustomerServiceOutlined />
        </div>
      );
    }
    // assistant
    return (
      <div className="chat-msg__avatar" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="28" height="28" fill="none">
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
    );
  };

  // 身份标签内容（observe 视角下区分顾客 / AI / 客服；self 视角下只为 other 侧角色加标签）
  const renderIdentity = () => {
    if (isAgent) {
      // agent 始终有标签（不分视图）：self 时"我"，observe 时"客服 XX"
      return (
        <div className="chat-msg__identity">
          {layout === 'observe' ? (
            <>🎧 {msg.agentName ? `客服 ${msg.agentName}` : '人工客服'}</>
          ) : (
            <span className="chat-msg__identity--self">我（客服）</span>
          )}
        </div>
      );
    }
    if (isAi && (layout === 'observe' || !selfSide)) {
      return (
        <div className="chat-msg__identity">
          <span className="chat-msg__ai-badge">🤖</span> AI 小智
        </div>
      );
    }
    if (isUser && layout === 'observe') {
      return <div className="chat-msg__identity">顾客</div>;
    }
    return null;
  };

  return (
    <div
      className={`chat-msg chat-msg--${variant}${selected ? ' chat-msg--selected' : ''}`}
      data-layout={layout}
      onClick={clickable ? handleRootClick : undefined}
      role={clickable ? 'button' : undefined}
      tabIndex={clickable ? 0 : undefined}
      aria-selected={clickable ? selected : undefined}
      title={clickable ? '点击查看该回复的溯源来源' : undefined}
      onKeyDown={clickable ? handleKeyDown : undefined}
      style={
        selected
          ? {
              // a11y：选中态唯一视觉边界（WCAG 1.4.11 需 ≥3:1）→ brand-dark 4.95:1
              // （全站 focus 指示框同款 brand-dark 惯例；brand 2.87:1 是漏网）
              outline: '2px solid var(--color-brand-dark)',
              outlineOffset: 2,
              borderRadius: 12,
            }
          : undefined
      }
    >
      {renderAvatar()}
      <div className={`chat-msg__bubble${statusCls}`}>
        {renderIdentity()}
        {msg.status === 'sending' && (
          <span className="chat-msg__status">发送中…</span>
        )}
        {msg.status === 'failed' && (
          <span className="chat-msg__status chat-msg__status--failed">发送失败</span>
        )}
        {isAi ? (
          <>
            {/* 工具来源 pill：紧跟气泡顶部通用 renderIdentity 之后，正文之前
                复制按钮不在此处——移到底部 .chat-msg__actions 与点赞条同行（2026-09-03 修正） */}
            {msg.tool && (
              <Tag className="chat-msg__tool-badge" color="blue" bordered>
                {TOOL_LABEL_MAP[msg.tool] ?? msg.tool}
              </Tag>
            )}
            {(() => {
              // 订单轨迹识别：流式渐进输出时也能即时切换为卡片视图，绕过 Markdown 纯文本。
              // detectOrderTrack 契约：detected ⇒ items ≥ 1（preamble/footer 已剔除来源标记）；
              // 溯源不受影响：来源仍落 message_sources，客服侧 SourceAccordion 照常展示。
              const order = detectOrderTrack(msg.content);
              if (order.detected) {
                return (
                  <div className="chat-msg__text chat-msg__text--order">
                    <OrderCards result={order} />
                  </div>
                );
              }
              return <MarkdownContent
                content={msg.content}
                className="chat-msg__text"
                interactiveCitations={hasSources}
                onCitationClick={onCitationClick}
              />;
            })()}
          </>
        ) : (
          <Typography.Paragraph className="chat-msg__text">{msg.content}</Typography.Paragraph>
        )}
        {isStaff && !isUser && !isAgent && msg.sources && msg.sources.length > 0 && (
          <SourceAccordion
            sources={msg.sources}
            open={sourceOpen}
            onToggle={() => {
              setSourceOpen((v) => {
                if (v) setHighlightN(null); // 收起时清高亮
                return !v;
              });
            }}
            highlightN={sourceOpen ? highlightN : null}
          />
        )}
        {!isUser && !isAgent && msg.ticketId && (
          <div className="chat-msg__ticket">
            <div className="chat-msg__ticket-body">
              <div className="chat-msg__ticket-line1">已为您创建工单，客服将尽快跟进</div>
              <div className="chat-msg__ticket-line2">
                <span className="chat-msg__ticket-no">
                  工单 <b>#{msg.ticketId.slice(0, 8)}</b>
                </span>
                <TicketStatusBadge ticketId={msg.ticketId} />
              </div>
            </div>
          </div>
        )}
        {/* AI 底部操作行：左点赞条 + 右复制按钮（hover 浮现，触屏常显）
            复制不依赖 messageId（流式进行中即可复制），点赞条仍需 messageId */}
        {isAi && (
          <div className="chat-msg__actions">
            {msg.messageId ? (
              <ThumbsBar value={msg.feedback} onRate={onRate} />
            ) : (
              <span aria-hidden="true" />
            )}
            <Button
              type="text"
              size="small"
              className="chat-msg__copy"
              icon={<CopyOutlined />}
              onClick={onCopy}
              aria-label="复制回答"
            >
              {copied ? '已复制' : '复制'}
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}

export default MessageBubble;
