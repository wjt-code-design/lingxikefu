import { Typography } from 'antd';
import { EmptyState } from '@/components/common/EmptyState';

/**
 * 运营统计页（FE-04 实现 StatCard + TrendChart，数据来自 /admin/stats 真实查询）。
 */
export function StatsPage() {
  return (
    <div className="page">
      <Typography.Title level={3}>运营统计</Typography.Title>
      <EmptyState title="统计面板占位" description="FE-04 在此渲染真实统计（会话/消息/文档/赞踩/首字时延）" />
    </div>
  );
}

export default StatsPage;
