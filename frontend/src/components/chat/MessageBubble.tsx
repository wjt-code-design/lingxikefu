import { useState } from 'react';
import { useAuthStore } from '@/store/authStore';
import { Button, Typography, message } from 'antd';
import { CopyOutlined, CustomerServiceOutlined } from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeSanitize from 'rehype-sanitize';
import type { ChatMessage } from './types';
import { SourceAccordion } from './SourceAccordion';
import { ThumbsBar } from './ThumbsBar';

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
}: {
  msg: ChatMessage;
  onRate: (rating: 'up' | 'down') => void;
  /** 'self' = 用户侧（user 右 / AI+agent 左）；'observe' = 客服视角（customer+ai 左 / agent 右） */
  layout?: 'self' | 'observe';
}) {
  const variant = getVariant(msg.role, layout);
  const isUser = msg.role === 'user';
  const isAgent = msg.role === 'agent';
  const isAi = msg.role === 'assistant';
  const selfSide = layout === 'self' ? isUser : isAgent; // self 视角的右栏身份
  const authUser = useAuthStore((s) => s.user);
  const userInitial = (authUser?.email?.charAt(0) ?? '我').toUpperCase();
  const [copied, setCopied] = useState(false);
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

  // 头像渲染：自我侧头像用 email 首字母；其他侧按角色区分（customer→UserOutlined、agent→工牌、assistant→AI 机器人 SVG）
  const renderAvatar = () => {
    if (isUser) {
      return (
        <div
          className={`chat-msg__avatar ${layout === 'observe' ? 'chat-msg__avatar--customer' : 'chat-msg__avatar--user'}`}
          aria-hidden="true"
        >
          {userInitial}
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
            fill="#96C8E8"
            opacity="0.55"
          />
          <circle cx="8.7" cy="11.3" r="1.2" fill="#539FD8" />
          <circle cx="12.3" cy="11.3" r="1.2" fill="#539FD8" />
          <circle cx="15.9" cy="11.3" r="1.2" fill="#539FD8" />
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
    <div className={`chat-msg chat-msg--${variant}`} data-layout={layout}>
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
            <div className="chat-msg__text">
              <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSanitize]}>
                {msg.content}
              </ReactMarkdown>
            </div>
          </>
        ) : (
          <Typography.Paragraph className="chat-msg__text">{msg.content}</Typography.Paragraph>
        )}
        {!isUser && msg.sources && msg.sources.length > 0 && <SourceAccordion sources={msg.sources} />}
        {!isUser && msg.ticketId && (
          <div className="chat-msg__ticket">
            已为您创建工单 <b>#{msg.ticketId.slice(0, 8)}</b>，客服将尽快跟进
          </div>
        )}
        {isAi && msg.messageId && <ThumbsBar value={msg.feedback} onRate={onRate} />}
      </div>
    </div>
  );
}

export default MessageBubble;
