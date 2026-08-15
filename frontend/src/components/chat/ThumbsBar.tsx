import { Button, Space } from 'antd';
import { DislikeOutlined, LikeOutlined } from '@ant-design/icons';

/** 点赞/点踩反馈条（调用 sendFeedback，幂等更新）。 */
export function ThumbsBar({
  value,
  onRate,
}: {
  value?: 'up' | 'down' | null;
  onRate: (rating: 'up' | 'down') => void;
}) {
  return (
    <Space size={4} className="chat-thumbs">
      <Button
        type={value === 'up' ? 'primary' : 'text'}
        size="small"
        icon={<LikeOutlined />}
        aria-label="有帮助"
        onClick={() => onRate('up')}
      />
      <Button
        type={value === 'down' ? 'primary' : 'text'}
        size="small"
        icon={<DislikeOutlined />}
        aria-label="没帮助"
        onClick={() => onRate('down')}
      />
    </Space>
  );
}
