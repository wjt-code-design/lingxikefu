import { ChatWidget } from '@/components/chat/ChatWidget';

/** 站内对话完整页（完整高度，复用挂件组件）。 */
export function ChatPage() {
  return (
    <div className="page page--chat">
      <ChatWidget />
    </div>
  );
}

export default ChatPage;
