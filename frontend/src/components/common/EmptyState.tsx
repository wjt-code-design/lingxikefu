import type { ReactNode } from 'react';
import { Empty, Typography } from 'antd';

interface EmptyStateProps {
  title?: string;
  description?: string;
  /** 预留自定义占位图标/插槽 */
  extra?: ReactNode;
}

/**
 * 通用空态：列表/数据未加载时展示，标题 + 描述 + 可选插槽。
 */
export function EmptyState({ title = '暂无数据', description, extra }: EmptyStateProps) {
  return (
    <div className="empty-state">
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={
          <div className="empty-state__desc">
            <Typography.Text strong>{title}</Typography.Text>
            {description ? (
              <div>
                <Typography.Text type="secondary">{description}</Typography.Text>
              </div>
            ) : null}
          </div>
        }
      >
        {extra}
      </Empty>
    </div>
  );
}

export default EmptyState;
