import { useCallback, useEffect, useRef, useState } from 'react';
import { Empty } from 'antd';
import { sendFeedback } from '@/api/chat';
import { createSession } from '@/api/sessions';
import { useChatStream } from '@/hooks/useChatStream';
import { Composer } from './Composer';
import { MessageList } from './MessageList';
import type { ChatMessage } from './types';

/**
 * 对话挂件容器（FE-03 核心）：
 * - 会话懒创建：首次发问时 POST /sessions 拿 session_id；
 * - SSE 流式：useChatStream 消费 stage/token/sources/done/error；
 * - 消息归并：流式 done/error 后将 user + assistant 消息并入历史，反馈条接 sendFeedback。
 */
export function ChatContainer() {
  const { stage, tokens, sources, messageId, error, reset, stream } = useChatStream();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [pendingUser, setPendingUser] = useState<ChatMessage | null>(null);
  const [creating, setCreating] = useState(false);
  const finalizedRef = useRef(false);

  const streaming = stage === 'retrieving' || stage === 'generating';

  // 流式结束（done/error）→ 并入消息历史（只并一次，finalizedRef 防重）
  useEffect(() => {
    if ((stage === 'done' || stage === 'error') && !finalizedRef.current) {
      finalizedRef.current = true;
      const assistant: ChatMessage = {
        id: `a-${Date.now()}`,
        role: 'assistant',
        content: tokens || (stage === 'error' ? error?.message || '服务异常，请稍后重试' : ''),
        sources,
        ...(stage === 'done' && messageId ? { messageId } : {}),
      };
      setMessages((prev) => [...prev, ...(pendingUser ? [pendingUser] : []), assistant]);
      setPendingUser(null);
      reset();
    }
  }, [stage, tokens, sources, messageId, error, pendingUser, reset]);

  const onSend = useCallback(
    async (text: string) => {
      finalizedRef.current = false;
      let sid = sessionId;
      if (!sid) {
        setCreating(true);
        try {
          const s = await createSession();
          sid = s.id;
          setSessionId(sid);
        } catch {
          setCreating(false);
          return;
        }
        setCreating(false);
      }
      setPendingUser({ id: `u-${Date.now()}`, role: 'user', content: text });
      await stream({ session_id: sid!, content: text });
    },
    [sessionId, stream]
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

  return (
    <div className="chat-container">
      <div className="chat-container__body">
        {messages.length === 0 && !streaming && (
          <Empty
            className="chat-container__empty"
            description="您好，我是星河智家智能客服，请问有什么可以帮您？"
          />
        )}
        <MessageList
          messages={messages}
          stream={{ stage, tokens, error }}
          onRate={onRate}
        />
      </div>
      <div className="chat-container__footer">
        <Composer disabled={streaming || creating} onSend={onSend} />
      </div>
    </div>
  );
}
