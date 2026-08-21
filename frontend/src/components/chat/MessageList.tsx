import { useEffect, useRef, useState } from 'react';
import type { ChatStage } from '@/hooks/useChatStream';
import type { ChatMessage } from './types';
import { MessageBubble } from './MessageBubble';
import { MarkdownContent } from '@/components/common/MarkdownContent';
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
  layout = 'self',
}: {
  messages: ChatMessage[];
  stream?: StreamView;
  onRate: (id: string, rating: 'up' | 'down') => void;
  /** 'self' = 用户侧；'observe' = 客服视角（顾客+AI 均在左） */
  layout?: 'self' | 'observe';
}) {
  const listRef = useRef<HTMLDivElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  // L8：用户上滑离开底部 → 暂停自动滚底；回到底部附近（80px 内）→ 恢复跟随。
  // 用 ref 而非 state：不因滚动触发重渲染，token 流式期间保持 60fps。
  const stickToBottom = useRef(true);
  const [expanded, setExpanded] = useState(false);

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

  // 历史消息折叠：默认只显示最近 8 条，超过则折叠
  const RECENT_COUNT = 8;
  const shouldCollapse = messages.length > RECENT_COUNT && !expanded;
  const collapsedMessages = shouldCollapse ? messages.slice(0, messages.length - RECENT_COUNT) : [];
  const recentMessages = shouldCollapse ? messages.slice(-RECENT_COUNT) : messages;

  return (
    <div ref={listRef} className="chat-list">
      {(() => {
        let lastTime = 0;
        const messagesToRender = shouldCollapse ? recentMessages : messages;
        
        // 折叠提示按钮
        if (shouldCollapse && collapsedMessages.length > 0) {
          // 计算折叠区域最后一条消息的时间，用于判断是否显示分隔线
          const lastCollapsedTime = collapsedMessages[collapsedMessages.length - 1]?.createdAt || 0;
          const firstRecentTime = recentMessages[0]?.createdAt || 0;
          const showDividerAfterCollapse = firstRecentTime && lastCollapsedTime && 
            firstRecentTime - lastCollapsedTime > 5 * 60 * 1000;
          
          return (
            <>
              {/* 折叠按钮 */}
              <div className="chat-collapse-zone">
                <button
                  type="button"
                  className="chat-collapse-btn"
                  onClick={() => setExpanded(true)}
                >
                  ▲ 展开 {collapsedMessages.length} 条更早的消息
                </button>
              </div>
              
              {/* 折叠后的时间分隔线 */}
              {showDividerAfterCollapse && (
                <div className="chat-time-divider">
                  <span>{new Date(firstRecentTime).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}</span>
                </div>
              )}
              
              {/* 最近消息 */}
              {messagesToRender.map((m) => {
                const time = m.createdAt || 0;
                const showDivider = time && time - lastTime > 5 * 60 * 1000;
                if (time) lastTime = time;
                return (
                  <div key={m.id}>
                    {showDivider && (
                      <div className="chat-time-divider">
                        <span>{new Date(time).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}</span>
                      </div>
                    )}
                    <MessageBubble key={m.id} msg={m} layout={layout} onRate={(r) => onRate(m.id, r)} />
                  </div>
                );
              })}
            </>
          );
        }
        
        // 未折叠时渲染全部消息
        return messages.map((m) => {
          const time = m.createdAt || 0;
          const showDivider = time && time - lastTime > 5 * 60 * 1000;
          if (time) lastTime = time;
          return (
            <div key={m.id}>
              {showDivider && (
                <div className="chat-time-divider">
                  <span>{new Date(time).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}</span>
                </div>
              )}
              <MessageBubble key={m.id} msg={m} layout={layout} onRate={(r) => onRate(m.id, r)} />
            </div>
          );
        });
      })()}
      {isStreaming && stream && (
        <div className={`chat-msg ${layout === 'observe' ? 'chat-msg--observe-ai' : 'chat-msg--ai'}`} data-layout={layout}>
          {/* V8：流式占位与正式消息保持头像一致性；observe 模式下用 observe-ai class 居左 */}
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
          <div className="chat-msg__bubble">
            <StageIndicator stage={stream.stage as 'retrieving' | 'generating'} />
            {stream.tokens && (
              <MarkdownContent
                content={stream.tokens}
                className="chat-msg__text chat-msg__text--stream"
              />
            )}
          </div>
        </div>
      )}
      <div ref={endRef} />
    </div>
  );
}
