import { ChatWidget } from '@/components/chat/ChatWidget';

/** 嵌入态对话挂件页（最小 chrome，可经 iframe 嵌入第三方页）。 */
export function WidgetPage() {
  return (
    <div className="page page--widget">
      <ChatWidget />
    </div>
  );
}

export default WidgetPage;
