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
  const listRef = useRef<HTMLDivElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  // L8：用户上滑离开底部 → 暂停自动滚底；回到底部附近（80px 内）→ 恢复跟随。
  // 用 ref 而非 state：不因滚动触发重渲染，token 流式期间保持 60fps。
  const stickToBottom = useRef(true);

  const isStreaming =
    !!stream && (stream.stage === 'retrieving' || stream.stage === 'generating');

  // 滚动容器是 .chat-list 的父级（.chat-container__body，overflow-y: auto），结构见 ChatContainer
  useEffect(() => {
    const scroller = listRef.current?.parentElement;
    if (!scroller) return;
    const onScroll = () => {
      const dist = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight;
      stickToBottom.current = dist < 80;
    };
    scroller.addEventListener('scroll', onScroll, { passive: true });
    return () => scroller.removeEventListener('scroll', onScroll);
  }, []);

  useEffect(() => {
    if (!stickToBottom.current) return;
    // C6：rAF 节流合并同一帧内多次滚动 —— 高速吐字时避免 smooth 动画排队抖动；
    // 流式中用 auto 即时定位（逐 token 不排队），非流式（新消息/历史加载）保留 smooth 平滑体验。
    const raf = requestAnimationFrame(() => {
      // jsdom 等无头环境没有 scrollIntoView（测试/SSR），存在性保护
      endRef.current?.scrollIntoView?.({
        behavior: isStreaming ? 'auto' : 'smooth',
      });
    });
    return () => cancelAnimationFrame(raf);
  }, [messages.length, stream?.tokens.length, isStreaming]);

  return (
    <div ref={listRef} className="chat-list">
      {messages.map((m) => (
        <MessageBubble key={m.id} msg={m} onRate={(r) => onRate(m.id, r)} />
      ))}
      {isStreaming && stream && (
        <div className="chat-msg chat-msg--ai">
          {/* V8：流式占位与正式消息保持头像一致性 */}
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
          <div className="chat-msg__bubble">
            <StageIndicator stage={stream.stage as 'retrieving' | 'generating'} />
            {stream.tokens && (
              <Typography.Paragraph className="chat-msg__text">
                {stream.tokens}
                <span className="chat-cursor" aria-hidden="true" />
              </Typography.Paragraph>
            )}
          </div>
        </div>
      )}
      <div ref={endRef} />
    </div>
  );
}
