import { Typography } from 'antd';
import { EmptyState } from '@/components/common/EmptyState';

/**
 * 站内对话完整页（FE-03 实现 ChatContainer + ChatWidget 全量形态）。
 */
export function ChatPage() {
  return (
    <div className="page page--chat">
      <Typography.Title level={3}>对话</Typography.Title>
      <EmptyState
        title="对话页占位"
        description="FE-03 在此渲染 ChatWidget（MessageList / Composer / SSE 流式）"
      />
    </div>
  );
}

export default ChatPage;
