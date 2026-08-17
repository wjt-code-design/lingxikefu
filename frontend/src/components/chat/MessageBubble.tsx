import { useState } from 'react';
import { Button, Typography, message } from 'antd';
import { CopyOutlined } from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeSanitize from 'rehype-sanitize';
import type { ChatMessage } from './types';
import { SourceAccordion } from './SourceAccordion';
import { ThumbsBar } from './ThumbsBar';

/** 单条消息气泡：user 右对齐 / assistant 左对齐 + 来源折叠 + 反馈条。
 * 助手消息按 Markdown 渲染（M7：还原加粗、斜体、列表等格式），rehype-sanitize 兜底防 XSS；
 * 用户消息保持纯文本，避免任何富文本注入。 */
export function MessageBubble({
  msg,
  onRate,
}: {
  msg: ChatMessage;
  onRate: (rating: 'up' | 'down') => void;
}) {
  const isUser = msg.role === 'user';
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

  return (
    <div className={`chat-msg chat-msg--${isUser ? 'user' : 'ai'}`}>
      {!isUser && (
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
      )}
      <div className={`chat-msg__bubble${statusCls}`}>
        {isUser ? (
          <>
            <Typography.Paragraph className="chat-msg__text">{msg.content}</Typography.Paragraph>
            {msg.status === 'sending' && (
              <span className="chat-msg__status">发送中…</span>
            )}
            {msg.status === 'failed' && (
              <span className="chat-msg__status chat-msg__status--failed">发送失败</span>
            )}
          </>
        ) : (
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
        )}
        {!isUser && msg.sources && msg.sources.length > 0 && <SourceAccordion sources={msg.sources} />}
        {!isUser && msg.ticketId && (
          <div className="chat-msg__ticket">
            已为您创建工单 <b>#{msg.ticketId.slice(0, 8)}</b>，客服将尽快跟进
          </div>
        )}
        {!isUser && msg.messageId && <ThumbsBar value={msg.feedback} onRate={onRate} />}
      </div>
    </div>
  );
}

export default MessageBubble;
