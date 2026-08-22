/**
 * 骨架屏组件（替代 Spin 转圈，减少等待焦虑）。
 * 纯 CSS 渐变闪烁动画，零额外依赖。
 *
 * 设计原则：
 * - 骨架形状尽量接近真实内容布局，让用户有"马上就好"的预期
 * - 动画柔和（1.4s 周期，低对比度渐变），不刺眼
 * - 尊重 prefers-reduced-motion：减弱动效下改为静态浅灰
 */

/** 单行骨架条（基础原子） */
export function SkeletonLine({
  width = '100%',
  height = 14,
  className = '',
  style,
}: {
  width?: string | number;
  height?: string | number;
  className?: string;
  style?: React.CSSProperties;
}) {
  const computedStyle: React.CSSProperties = {
    width: typeof width === 'number' ? `${width}px` : width,
    height: typeof height === 'number' ? `${height}px` : height,
    ...style,
  };
  return <div className={`skeleton-line ${className}`} style={computedStyle} />;
}

/** KPI 卡片骨架（与 KpiCard 同尺寸同布局） */
export function KpiCardSkeleton() {
  return (
    <div className="kpi-card kpi-card--skeleton">
      <SkeletonLine width="60%" height={13} className="kpi-card__label" />
      <SkeletonLine width="55%" height={28} style={{ marginTop: 6, marginBottom: 2 }} />
      <SkeletonLine width="40%" height={12} style={{ marginTop: 8 }} />
    </div>
  );
}

/** 表格行骨架（用于 AppTable 加载态，渲染 5 行占位） */
export function TableSkeleton({
  rows = 5,
  columns = 4,
}: {
  rows?: number;
  columns?: number;
}) {
  return (
    <div className="skeleton-table">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="skeleton-table__row">
          {Array.from({ length: columns }).map((_, j) => (
            <SkeletonLine
              key={j}
              width={j === 0 ? '30%' : `${40 + Math.random() * 40}%`}
              height={14}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

/** 聊天消息骨架（AI 消息形态，左头像 + 右气泡） */
export function ChatMessageSkeleton() {
  return (
    <div className="chat-msg chat-msg--ai chat-msg--skeleton">
      <div className="chat-msg__avatar skeleton-avatar" aria-hidden="true" />
      <div className="chat-msg__bubble skeleton-bubble">
        <SkeletonLine width="90%" height={14} />
        <SkeletonLine width="75%" height={14} style={{ marginTop: 6 }} />
        <SkeletonLine width="50%" height={14} style={{ marginTop: 6 }} />
      </div>
    </div>
  );
}

export default {
  Line: SkeletonLine,
  KpiCard: KpiCardSkeleton,
  Table: TableSkeleton,
  ChatMessage: ChatMessageSkeleton,
};
