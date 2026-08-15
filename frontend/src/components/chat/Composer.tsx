import { useState } from 'react';
import { Button, Input } from 'antd';
import { SendOutlined } from '@ant-design/icons';

/** 输入区：多行文本框 + 发送（流式中禁用；Enter 发送 / Shift+Enter 换行）。 */
export function Composer({ disabled, onSend }: { disabled: boolean; onSend: (text: string) => void }) {
  const [text, setText] = useState('');

  const submit = () => {
    const t = text.trim();
    if (!t || disabled) return;
    onSend(t);
    setText('');
  };

  return (
    <div className="chat-composer">
      <Input.TextArea
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="请输入您的问题，如：七天无理由退货怎么申请？"
        autoSize={{ minRows: 1, maxRows: 4 }}
        onPressEnter={(e) => {
          if (!e.shiftKey) {
            e.preventDefault();
            submit();
          }
        }}
        disabled={disabled}
        aria-label="问题输入"
      />
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
    </div>
  );
}
