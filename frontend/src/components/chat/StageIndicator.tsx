import { Spin } from 'antd';

/** 阶段指示：检索中 / 生成中（SSE stage 事件驱动）。 */
export function StageIndicator({ stage }: { stage: 'retrieving' | 'generating' }) {
  const text = stage === 'retrieving' ? '正在检索知识库…' : '正在生成回答…';
  return (
    <div className="chat-stage">
      <Spin size="small" />
      <span className="chat-stage__text">{text}</span>
    </div>
  );
}
