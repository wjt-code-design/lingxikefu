import type { TrendPoint } from '@/contracts/api';

const SERIES = [
  { key: 'sessions', label: '会话', color: '#539FD8' },
  { key: 'messages', label: '消息', color: '#96C8E8' },
  { key: 'tickets', label: '工单', color: '#73C9A8' },
] as const;

const W = 640;
const H = 220;
const PAD = { top: 16, right: 16, bottom: 28, left: 32 };

/**
 * 运营趋势折线图（P1）：自绘 SVG，零依赖（决策：不引 echarts；@ant-design/charts 对 3 线×14 天过重）。
 * - 三条线（会话/消息/工单）+ 图例 + 最大最小值标注；
 * - 无数据日期补零由后端保证（连续轴）。
 */
export function TrendChart({ days }: { days: TrendPoint[] }) {
  if (!days.length) return null;

  const max = Math.max(...days.map((d) => Math.max(d.sessions, d.messages, d.tickets)), 1);
  const iw = W - PAD.left - PAD.right;
  const ih = H - PAD.top - PAD.bottom;
  const x = (i: number) => PAD.left + (days.length === 1 ? iw / 2 : (i * iw) / (days.length - 1));
  const y = (v: number) => PAD.top + ih - (v / max) * ih;

  const line = (key: 'sessions' | 'messages' | 'tickets') =>
    days.map((d, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(d[key]).toFixed(1)}`).join(' ');

  // X 轴刻度：最多标 7 个日期（防拥挤）
  const step = Math.ceil(days.length / 7);
  const ticks = days.map((d, i) => ({ d, i })).filter((_, i) => i % step === 0 || i === days.length - 1);

  return (
    <div className="trend-chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="运营趋势折线图">
        {/* 网格线（4 档）+ Y 轴标签 */}
        {[0, 1, 2, 3, 4].map((g) => {
          const gy = PAD.top + (ih * g) / 4;
          return (
            <g key={g}>
              <line x1={PAD.left} y1={gy} x2={W - PAD.right} y2={gy} stroke="#E3E9EF" strokeWidth="1" />
              <text x={PAD.left - 6} y={gy + 3} textAnchor="end" fontSize="10" fill="var(--text-4)">
                {Math.round(max - (max * g) / 4)}
              </text>
            </g>
          );
        })}
        {/* 三条线 */}
        {SERIES.map((s) => (
          <path
            key={s.key}
            d={line(s.key)}
            fill="none"
            stroke={s.color}
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        ))}
        {/* X 轴刻度 */}
        {ticks.map(({ d, i }) => (
          <text key={i} x={x(i)} y={H - 8} textAnchor="middle" fontSize="10" fill="var(--text-4)">
            {d.date.slice(5)}
          </text>
        ))}
      </svg>
      <div className="trend-chart__legend">
        {SERIES.map((s) => (
          <span key={s.key} className="trend-chart__legend-item">
            <span className="trend-chart__dot" style={{ background: s.color }} />
            {s.label}
          </span>
        ))}
      </div>
    </div>
  );
}
