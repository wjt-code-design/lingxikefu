import type { OrderTrackItem, OrderTrackResult } from './orderTrack';

/** 订单状态 → 视觉主题（emoji + 关键词 + 色调）。
 * 色彩基于现有设计 token（--color-brand / --color-success / --color-warning / --color-danger）。 */
const STATUS_THEME: Record<string, { icon: string; tone: string; label: string }> = {
  已签收:   { icon: '📦', tone: 'success', label: '已签收' },
  已发货:   { icon: '🚚', tone: 'brand',   label: '已发货' },
  派送中:   { icon: '🛵', tone: 'brand',   label: '派送中' },
  运输中:   { icon: '🚛', tone: 'brand',   label: '运输中' },
  待送装:   { icon: '🔧', tone: 'warning', label: '待送装' },
  待发货:   { icon: '📋', tone: 'warning', label: '待发货' },
  已预约:   { icon: '📅', tone: 'warning', label: '已预约' },
  已下单:   { icon: '✅', tone: 'brand',   label: '已下单' },
  处理中:   { icon: '⏳', tone: 'warning', label: '处理中' },
  已取消:   { icon: '🚫', tone: 'danger',  label: '已取消' },
  已退款:   { icon: '↩️', tone: 'danger',  label: '已退款' },
  已完成:   { icon: '🎉', tone: 'success', label: '已完成' },
};

function themeOf(item: OrderTrackItem) {
  if (item.status && STATUS_THEME[item.status]) return STATUS_THEME[item.status];
  return { icon: '📦', tone: 'neutral', label: item.status ?? '状态未知' };
}

function OrderCard({ item, index, total }: { item: OrderTrackItem; index: number; total: number }) {
  const theme = themeOf(item);
  // 渐进出场：多张卡片时依序错峰进场动画
  const delay = Math.min(index * 60, total * 60);
  return (
    <div
      className={`order-card order-card--${theme.tone}`}
      style={{ animationDelay: `${delay}ms` }}
      data-order={item.orderNo}
    >
      <div className="order-card__head">
        <div className="order-card__icon" aria-hidden="true">{theme.icon}</div>
        <div className="order-card__title">
          {item.product ? (
            <span className="order-card__product">{item.product}</span>
          ) : (
            <span className="order-card__product">订单</span>
          )}
          {item.orderNo && <span className="order-card__order-no">{item.orderNo}</span>}
        </div>
        <span className="order-card__status">{theme.label}</span>
      </div>
      {item.detail && <div className="order-card__detail">{item.detail}</div>}
    </div>
  );
}

export function OrderCards({ result }: { result: OrderTrackResult }) {
  if (!result.detected || result.items.length === 0) return null;
  return (
    <div className="order-track" aria-label="订单轨迹">
      {result.preamble && <div className="order-track__preamble">{result.preamble}</div>}
      <div className="order-track__list" role="list">
        {result.items.map((it, i) => (
          <div role="listitem" key={`${it.orderNo ?? i}-${i}`}>
            <OrderCard item={it} index={i} total={result.items.length} />
          </div>
        ))}
      </div>
      {result.footer && <div className="order-track__footer">{result.footer}</div>}
    </div>
  );
}

export default OrderCards;
