import { ChatContainer } from './ChatContainer';

/**
 * 对话挂件（/widget 嵌入态与 /chat 完整页共用）。
 * 差异仅在外壳（WidgetShell 顶条 / 侧栏），对话逻辑全部在 ChatContainer。
 */
export function ChatWidget() {
  return <ChatContainer />;
}

export { ChatContainer };
