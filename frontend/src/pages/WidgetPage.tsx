import { Typography } from 'antd';
import { EmptyState } from '@/components/common/EmptyState';

/**
 * 嵌入态对话挂件页（FE-03 实现 ChatContainer + ChatWidget 嵌入形态）。
 */
export function WidgetPage() {
  return (
    <div className="page page--widget">
      <Typography.Title level={3}>对话挂件</Typography.Title>
      <EmptyState
        title="挂件占位"
        description="FE-03 在此渲染可嵌入 iframe 的 ChatWidget（最小 chrome）"
      />
    </div>
  );
}

export default WidgetPage;
