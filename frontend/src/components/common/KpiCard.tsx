import type { ReactNode } from 'react';

/**
 * KPI 指标卡（工作台公共组件，收敛三份重复实现：admin/Dashboard、admin/Stats、agent/Dashboard）。
 * 自绘白卡 + 左侧渐变色条 + tabular-nums 数字，样式在 globals.css 的 `.kpi-card` 公共块。
 * 不含栅格布局——外层 Col/grid 由页面按需包裹（admin 页用 Col xs12 lg6，agent 页用 grid）。
 *
 * accent 色条类型：
 * - 'brand'   海盐蓝（默认，通用指标）
 * - 'success' 薄荷绿（正向指标：解决数、满意度等）
 * - 'warning' 琥珀橙（处理中、待办等）
 * - 'danger'  珊瑚红（异常、失败等）
 */
export function KpiCard({
  label,
  value,
  suffix,
  caption,
  foot,
  accent = 'brand',
}: {
  label: string;
  value: number | string;
  /** 数字后缀（如"单 / 篇 / %"），小号灰字 */
  suffix?: string;
  /** 卡片底部说明文字（简单场景）；与 foot 二选一，foot 优先渲染在最后 */
  caption?: ReactNode;
  /** 卡片底部自定义区（复杂场景：caption + sparkline / 占比条） */
  foot?: ReactNode;
  /** 左侧色条类型，默认 brand 海盐蓝 */
  accent?: 'brand' | 'success' | 'warning' | 'danger';
}) {
  const cls = `kpi-card${accent !== 'brand' ? ` kpi-card--${accent}` : ''}`;
  return (
    <div className={cls}>
      <div className="kpi-card__label">{label}</div>
      <div className="kpi-card__value">
        {value}
        {suffix ? <span className="kpi-card__suffix">{suffix}</span> : null}
      </div>
      {caption ? <div className="kpi-card__caption">{caption}</div> : null}
      {foot ? <div className="kpi-card__foot">{foot}</div> : null}
    </div>
  );
}

export default KpiCard;
