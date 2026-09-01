import { WorkbenchLayout } from '@/components/workbench/WorkbenchLayout';

/** 站内对话完整页（三栏工作台：历史/对话/溯源）。 */
export function ChatPage() {
  return (
    <div className="page page--chat">
      {/* a11y：对话页无可见标题 UI，用视觉隐藏 h1 补页面主标题语义（axe page-has-heading-one） */}
      <h1 className="sr-only">智能对话</h1>
      <WorkbenchLayout />
    </div>
  );
}

export default ChatPage;
