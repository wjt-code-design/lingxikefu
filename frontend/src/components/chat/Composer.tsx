import { useEffect, useState } from 'react';
import { Button, Input, Space } from 'antd';
import { CustomerServiceOutlined, RedoOutlined, SendOutlined } from '@ant-design/icons';

interface ComposerProps {
  disabled: boolean;
  /** 返回 true=已发出（清空输入）；false=失败（保留输入，P0-2 修复：创建会话失败不丢用户输入） */
  onSend: (text: string) => Promise<boolean> | boolean;
  /** 可重试（最近一次回答失败）→ 显示重试按钮，一键重发上一条 */
  retry?: { text: string; onRetry: () => void } | null;
  /** P0-4：主动转人工（会话已创建且非转人工进行中时可用） */
  onEscalate?: () => void;
  /** P1-3：快捷话术点击 → 填入输入框（callback 而非 ref，WorkbenchLayout 透传给 SourcePanel） */
  onRegisterFill?: (fill: (text: string) => void) => void;
}

/** 输入区：多行文本框 + 发送（流式中禁用；Enter 发送 / Shift+Enter 换行）。 */
export function Composer({ disabled, onSend, retry, onEscalate, onRegisterFill }: ComposerProps) {
  const [text, setText] = useState('');

  // P1-3：把"填入输入框"能力注册给父组件（快捷话术调用）；卸载时注销
  useEffect(() => {
    onRegisterFill?.((t) => setText((prev) => (prev ? `${prev}${t}` : t)));
  }, [onRegisterFill]);

  const submit = async () => {
    const t = text.trim();
    if (!t || disabled) return;
    const ok = await onSend(t);
    if (ok !== false) setText(''); // 仅成功才清空；失败保留输入供用户修正
  };

  return (
    <div className="chat-composer">
      <Input.TextArea
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="请输入您的问题，如：七天无理由退货怎么申请？"
        autoSize={{ minRows: 1, maxRows: 4 }}
        maxLength={4000}
        showCount
        onPressEnter={(e) => {
          if (!e.shiftKey) {
            e.preventDefault();
            submit();
          }
        }}
        disabled={disabled}
        aria-label="问题输入"
      />
      <Space.Compact block>
        {retry && (
          <Button
            icon={<RedoOutlined />}
            onClick={retry.onRetry}
            disabled={disabled}
            title={`重试：${retry.text.slice(0, 20)}`}
          >
            重试
          </Button>
        )}
        {onEscalate && (
          <Button
            icon={<CustomerServiceOutlined />}
            onClick={onEscalate}
            disabled={disabled}
            className="chat-composer__escalate"
          >
            转人工
          </Button>
        )}
        <Button
          type="primary"
          icon={<SendOutlined />}
          onClick={submit}
          loading={disabled}
          disabled={disabled}
          aria-label="发送"
        >
          发送
        </Button>
      </Space.Compact>
    </div>
  );
}
