import { WorkbenchLayout } from '@/components/workbench/WorkbenchLayout';

/** 嵌入态对话挂件页（三栏工作台；窄屏自动折叠为单栏）。 */
export function WidgetPage() {
  return (
    <div className="page page--widget">
      <WorkbenchLayout />
    </div>
  );
}

export default WidgetPage;
