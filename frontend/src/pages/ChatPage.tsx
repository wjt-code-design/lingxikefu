import { WorkbenchLayout } from '@/components/workbench/WorkbenchLayout';

/** 站内对话完整页（三栏工作台：历史/对话/溯源）。 */
export function ChatPage() {
  return (
    <div className="page page--chat">
      <WorkbenchLayout />
    </div>
  );
}

export default ChatPage;
