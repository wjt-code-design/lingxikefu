import { useEffect, useState } from 'react';
import { Button, Input } from 'antd';
import { CustomerServiceOutlined, PauseCircleOutlined, RedoOutlined, SendOutlined } from '@ant-design/icons';

interface ComposerProps {
  disabled: boolean;
  /** 返回 true=已发出（清空输入）；false=失败（保留输入，P0-2 修复：创建会话失败不丢用户输入） */
  onSend: (text: string) => Promise<boolean> | boolean;
  /** 可重试（最近一次回答失败）→ 显示重试按钮，一键重发上一条 */
  retry?: { text: string; onRetry: () => void } | null;
  /** P0-4：主动转人工（会话已创建且非转人工进行中时可用） */
  onEscalate?: () => void;
  /** P1-3：快捷话术点击 → 填入输入框（callback 而非 ref，WorkbenchLayout 透传给 SourcePanel）。
   *  返回注销函数：Composer 卸载时父组件不再持有指向已卸载实例的回调。 */
  onRegisterFill?: (fill: (text: string) => void) => (() => void) | undefined;
  /** P0-4：停止生成（流式响应中可用） */
  onStop?: () => void;
  /** W5：客服视角下输入框居中显示（收窄并水平居中于中间列） */
  centered?: boolean;
}

/** 输入区：多行文本框 + 发送（流式中禁用；Enter 发送 / Shift+Enter 换行）。 */
export function Composer({ disabled, onSend, retry, onEscalate, onRegisterFill, onStop, centered }: ComposerProps) {
  const [text, setText] = useState('');
  const MAX_LEN = 4000;
  const nearLimit = text.length >= MAX_LEN * 0.8;
  const atLimit = text.length >= MAX_LEN;

  // P1-3：把"填入输入框"能力注册给父组件（快捷话术调用）；卸载时注销。
  // P4：旧版注释谎称"注销"却无 cleanup，父组件会一直持有指向已卸载实例的回调。
  useEffect(() => {
    const unregister = onRegisterFill?.((t) => setText((prev) => (prev ? `${prev}${t}` : t)));
    return () => {
      unregister?.();
    };
  }, [onRegisterFill]);

  const submit = async () => {
    const t = text.trim();
    if (!t || disabled) return;
    const ok = await onSend(t);
    if (ok !== false) setText(''); // 仅成功才清空；失败保留输入供用户修正
  };

  return (
    <div className={`chat-composer${centered ? ' chat-composer--centered' : ''}`}>
      <div className="chat-composer__field">
        <Input.TextArea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="请输入您的问题，如：七天无理由退货怎么申请？"
          autoSize={{ minRows: 1, maxRows: 4 }}
          maxLength={4000}
          onPressEnter={(e) => {
            // C3：中文输入法组词中（isComposing）回车不发送，避免半句话误发
            if (!e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              submit();
            }
          }}
          disabled={disabled}
          aria-label="问题输入"
        />
      </div>
      <div className="chat-composer__bar">
        <span className="chat-composer__hint">Enter 发送 · Shift+Enter 换行</span>
        <span className={`chat-composer__count${atLimit ? ' at-limit' : nearLimit ? ' near-limit' : ''}`}>
          {text.length} / 4000
        </span>
        <div className="chat-composer__actions">
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
          {disabled && onStop && (
            <Button
              danger
              icon={<PauseCircleOutlined />}
              onClick={onStop}
              aria-label="停止生成"
            >
              停止
            </Button>
          )}
          {onEscalate && !disabled && (
            <Button
              icon={<CustomerServiceOutlined />}
              onClick={onEscalate}
              disabled={disabled}
              className="chat-composer__escalate"
            >
              转人工
            </Button>
          )}
          {!disabled && (
            <Button
              type="primary"
              icon={<SendOutlined />}
              onClick={submit}
              loading={disabled}
              // 空内容时置灰（点亮式微交互），有输入才高亮可用
              disabled={!text.trim()}
              aria-label="发送"
              className="chat-composer__send"
            >
              发送
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
