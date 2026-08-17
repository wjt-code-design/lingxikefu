/** 阶段指示：检索中 / 生成中（SSE stage 事件驱动）。
 * 2026-08-17 海盐蓝定稿：加载态从 antd Spin 转圈替换为海盐蓝三点呼吸动画（轻量化）。
 * P1-5（评审降级）：检索为 <2s 批量查询，用中性等待文案（不伪造逐条文档滚动）。 */
export function StageIndicator({ stage }: { stage: 'retrieving' | 'generating' }) {
  const text =
    stage === 'retrieving' ? '已收到，正在匹配知识库（预计 1–2 秒）…' : '正在生成回答…';
  return (
    <div className="chat-stage">
      <span className="chat-dots" aria-hidden="true">
        <span />
        <span />
        <span />
      </span>
      <span className="chat-stage__text">{text}</span>
    </div>
  );
}
