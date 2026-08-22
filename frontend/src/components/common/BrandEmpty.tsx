import type { ReactNode } from 'react';

/**
 * 品牌化空态（替代 antd 默认 Empty）——海盐蓝机器人插画 + 引导文案。
 * - 柔和光晕图标 + 渐变色底，与对话欢迎态视觉语言一致
 * - 可选 action 插槽：空态下提供快捷操作（如"新建"、"去导入"）
 */
export function BrandEmpty({
  title,
  hint,
  action,
}: {
  title: string;
  hint?: string;
  /** 空态操作按钮，如 <Button type="primary">新建</Button> */
  action?: ReactNode;
}) {
  return (
    <div className="brand-empty">
      <div className="brand-empty__icon" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="30" height="30" fill="none">
          <path
            d="M12 3C7 3 3 6.8 3 11.5c0 2.6 1.3 4.9 3.4 6.4V21l3.2-1.9c.7.2 1.5.3 2.4.3 5 0 9-3.8 9-8.5S17 3 12 3z"
            fill="var(--chat-avatar-fill)"
            opacity="0.55"
          />
          <circle cx="8.7" cy="11.3" r="1.2" fill="var(--chat-avatar-dot)" />
          <circle cx="12.3" cy="11.3" r="1.2" fill="var(--chat-avatar-dot)" />
          <circle cx="15.9" cy="11.3" r="1.2" fill="var(--chat-avatar-dot)" />
        </svg>
      </div>
      <div className="brand-empty__title">{title}</div>
      {hint && <div className="brand-empty__hint">{hint}</div>}
      {action && <div className="brand-empty__action">{action}</div>}
    </div>
  );
}

export default BrandEmpty;
