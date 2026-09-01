import { useEffect, useRef, useState } from 'react';
import type { ChatStage } from '@/hooks/useChatStream';
import type { ChatMessage } from './types';
import type { MessageSource } from '@/contracts/api';
import { MessageBubble } from './MessageBubble';
import { MarkdownContent } from '@/components/common/MarkdownContent';
import { StageIndicator } from './StageIndicator';

/** 流式中状态（来自 useChatStream，用于尾部渲染）。 */
export interface StreamView {
  stage: ChatStage;
  tokens: string;
  reasoning?: string; // 思维链累积（开思考时先于 tokens 到达，"思考中"气泡展示）
  error?: { code: string; message: string };
}

/** 消息列表：历史消息 + 流式尾部（stage 指示 + 实时 token），自动滚底。 */
export function MessageList({
  messages,
  stream,
  onRate,
  layout = 'self',
  selectedMsgId,
  onSelectMessage,
}: {
  messages: ChatMessage[];
  stream?: StreamView;
  onRate: (id: string, rating: 'up' | 'down') => void;
  /** 'self' = 用户侧；'observe' = 客服视角（顾客+AI 均在左） */
  layout?: 'self' | 'observe';
  /** 溯源选中（2026-08-25）：当前被右栏面板查看的 AI 回复 id（气泡高亮） */
  selectedMsgId?: string | null;
  /** 点击 AI 回复 → 右栏溯源面板切换（点哪条看哪条；answerSource 透出该条快捷话术标记） */
  onSelectMessage?: (msgId: string, sources: MessageSource[], answerSource?: string) => void;
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
    // UI 审查中10：欢迎页（无消息且非流式）不自动滚底——endRef 在欢迎块之后，
    // mount 即 scrollIntoView 会把欢迎语滚出视口（586px 窄视口最明显）。
    if (messages.length === 0 && !isStreaming) return;
    // C6：rAF 节流合并同一帧内多次滚动 —— 高速吐字时避免 smooth 动画排队抖动；
    // 流式中用 auto 即时定位（逐 token 不排队），非流式（新消息/历史加载）保留 smooth 平滑体验。
    const raf = requestAnimationFrame(() => {
      // jsdom 等无头环境没有 scrollIntoView（测试/SSR），存在性保护
      endRef.current?.scrollIntoView?.({
        behavior: isStreaming ? 'auto' : 'smooth',
      });
    });
    return () => cancelAnimationFrame(raf);
  }, [messages.length, stream?.tokens.length, stream?.reasoning?.length, isStreaming]);

  // 历史消息折叠：默认只显示最近 8 条，超过则折叠
  const RECENT_COUNT = 8;
  const shouldCollapse = messages.length > RECENT_COUNT && !expanded;
  const collapsedMessages = shouldCollapse ? messages.slice(0, messages.length - RECENT_COUNT) : [];
  const recentMessages = shouldCollapse ? messages.slice(-RECENT_COUNT) : messages;

  // P4：折叠/全量两支的逐条渲染此前重复两遍，抽为单一函数保证行为一致
  const renderMessages = (list: ChatMessage[]) => {
    let lastTime = 0;
    return list.map((m) => {
      const time = m.createdAt || 0;
      const showDivider = !!(time && time - lastTime > 5 * 60 * 1000);
      if (time) lastTime = time;
      return (
        <div key={m.id}>
          {showDivider && (
            <div className="chat-time-divider">
              <span>{new Date(time).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}</span>
            </div>
          )}
          <MessageBubble
            msg={m}
            layout={layout}
            onRate={(r) => onRate(m.id, r)}
            selected={selectedMsgId === m.id}
            onSelect={onSelectMessage}
          />
        </div>
      );
    });
  };

  return (
    <div ref={listRef} className="chat-list">
      {shouldCollapse && collapsedMessages.length > 0 ? (
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

          {/* 折叠后的时间分隔线：判断折叠区最后一条与最近区第一条的时间间隔 */}
          {(() => {
            const lastCollapsedTime = collapsedMessages[collapsedMessages.length - 1]?.createdAt || 0;
            const firstRecentTime = recentMessages[0]?.createdAt || 0;
            return !!(firstRecentTime && lastCollapsedTime &&
              firstRecentTime - lastCollapsedTime > 5 * 60 * 1000);
          })() && (
            <div className="chat-time-divider">
              <span>{new Date(recentMessages[0]?.createdAt || 0).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}</span>
            </div>
          )}

          {/* 最近消息 */}
          {renderMessages(recentMessages)}
        </>
      ) : (
        renderMessages(messages)
      )}
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
            {/* 思维链气泡（感知 TTFT）：reasoning 先于内容到达（~2s），滚动展示"思考中"；
                有内容输出后自动收起为一行摘要，不干扰正文阅读。不落历史（finalize 不保留）。 */}
            {stream.reasoning && !stream.tokens && (
              <div className="chat-msg__reasoning" aria-live="polite">
                <div className="chat-msg__reasoning-head">
                  <span className="chat-msg__reasoning-dot" aria-hidden="true" />
                  思考中…
                </div>
                <div className="chat-msg__reasoning-body">{stream.reasoning.slice(-160)}</div>
              </div>
            )}
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
