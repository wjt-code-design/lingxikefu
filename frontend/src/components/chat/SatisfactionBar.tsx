import { Button, Space, Typography } from 'antd';
import { useState } from 'react';

const OPTIONS = [
  { value: 'satisfied', label: '满意', emoji: '😀' },
  { value: 'neutral', label: '一般', emoji: '😐' },
  { value: 'unsatisfied', label: '不满意', emoji: '😢' },
] as const;

type Rating = (typeof OPTIONS)[number]['value'];

/**
 * P2-2：会话级满意度评价（对话 ≥2 轮后内联出现，不弹窗打扰）。
 * - 与 ThumbsBar 互补：ThumbsBar=逐条回答质量；此处=整段会话体验
 * - 点击即落库（幂等覆盖），评分后消失
 */
export function SatisfactionBar({
  onRate,
  busy,
}: {
  onRate: (rating: Rating) => void;
  busy?: boolean;
}) {
  const [rated, setRated] = useState(false);

  if (rated) return null;
  return (
    <div className="chat-msg chat-msg--ai">
      <div className="chat-msg__bubble chat-satisfaction">
        <Typography.Text className="chat-satisfaction__text">
          这次服务还满意吗？
        </Typography.Text>
        <Space size={6}>
          {OPTIONS.map((o) => (
            <Button
              key={o.value}
              size="small"
              disabled={busy}
              onClick={() => {
                setRated(true); // 乐观隐藏，防重复提交
                onRate(o.value);
              }}
              aria-label={`${o.label}评价`}
            >
              {o.emoji} {o.label}
            </Button>
          ))}
        </Space>
        <Typography.Text className="chat-satisfaction__hint">
          不满意可点击输入框旁的「转人工」按钮
        </Typography.Text>
      </div>
    </div>
  );
}
