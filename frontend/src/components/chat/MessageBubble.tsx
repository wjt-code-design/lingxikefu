import { Typography } from 'antd';
import type { ChatMessage } from './types';
import { SourceAccordion } from './SourceAccordion';
import { ThumbsBar } from './ThumbsBar';

/** 单条消息气泡：user 右对齐 / assistant 左对齐 + 来源折叠 + 反馈条。 */
export function MessageBubble({
  msg,
  onRate,
}: {
  msg: ChatMessage;
  onRate: (rating: 'up' | 'down') => void;
}) {
  const isUser = msg.role === 'user';
  return (
    <div className={`chat-msg chat-msg--${isUser ? 'user' : 'ai'}`}>
      <div className="chat-msg__bubble">
        <Typography.Paragraph className="chat-msg__text">{msg.content}</Typography.Paragraph>
        {!isUser && msg.sources && msg.sources.length > 0 && <SourceAccordion sources={msg.sources} />}
        {!isUser && msg.messageId && <ThumbsBar value={msg.feedback} onRate={onRate} />}
      </div>
    </div>
  );
}
