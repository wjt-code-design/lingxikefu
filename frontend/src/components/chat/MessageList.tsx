import { useEffect, useRef } from 'react';
import { Typography } from 'antd';
import type { ChatStage } from '@/hooks/useChatStream';
import type { ChatMessage } from './types';
import { MessageBubble } from './MessageBubble';
import { StageIndicator } from './StageIndicator';

/** 流式中状态（来自 useChatStream，用于尾部渲染）。 */
export interface StreamView {
  stage: ChatStage;
  tokens: string;
  error?: { code: string; message: string };
}

/** 消息列表：历史消息 + 流式尾部（stage 指示 + 实时 token），自动滚底。 */
export function MessageList({
  messages,
  stream,
  onRate,
}: {
  messages: ChatMessage[];
  stream?: StreamView;
  onRate: (id: string, rating: 'up' | 'down') => void;
}) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // jsdom 等无头环境没有 scrollIntoView（测试/SSR），存在性保护
    endRef.current?.scrollIntoView?.({ behavior: 'smooth' });
  }, [messages.length, stream?.tokens.length]);

  const isStreaming =
    !!stream && (stream.stage === 'retrieving' || stream.stage === 'generating');

  return (
    <div className="chat-list">
      {messages.map((m) => (
        <MessageBubble key={m.id} msg={m} onRate={(r) => onRate(m.id, r)} />
      ))}
      {isStreaming && stream && (
        <div className="chat-msg chat-msg--ai">
          <div className="chat-msg__bubble">
            <StageIndicator stage={stream.stage as 'retrieving' | 'generating'} />
            {stream.tokens && (
              <Typography.Paragraph className="chat-msg__text">{stream.tokens}</Typography.Paragraph>
            )}
          </div>
        </div>
      )}
      <div ref={endRef} />
    </div>
  );
}
