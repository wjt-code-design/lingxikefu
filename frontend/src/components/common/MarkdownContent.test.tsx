import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { MarkdownContent } from './MarkdownContent';

/** [来源N] → sup 角标转换（remarkCitations）行为守护：
 *  - 正文引用变真实 <sup> 节点（样式走 .chat-msg__text sup 元素选择器）；
 *  - inlineCode / code 块内的 "[来源N]" 豁免（正文是 value 不是 children）；
 *  - XSS 兜底（rehypeSanitize）不回归。 */
describe('MarkdownContent 溯源引用角标', () => {
  it('正文 [来源N] 转为 <sup>N</sup>，原文标记不再出现', () => {
    const { container } = render(
      <MarkdownContent content={'维修周期 5–10 个工作日 [来源4]'} className="chat-msg__text" />
    );
    const sup = container.querySelector('.chat-msg__text sup');
    expect(sup).not.toBeNull();
    expect(sup!.textContent).toBe('4');
    expect(container.querySelector('.chat-msg__text')!.textContent).not.toContain('[来源4]');
  });

  it('一段文本多处引用逐一转换', () => {
    const { container } = render(
      <MarkdownContent content={'A [来源1] 和 B [来源12]'} className="chat-msg__text" />
    );
    const sups = container.querySelectorAll('.chat-msg__text sup');
    expect(sups).toHaveLength(2);
    expect(sups[0].textContent).toBe('1');
    expect(sups[1].textContent).toBe('12');
  });

  it('inlineCode 内 [来源N] 豁免（不转角标）', () => {
    const { container } = render(
      <MarkdownContent content={'使用 `[来源1]` 占位'} className="chat-msg__text" />
    );
    expect(container.querySelector('.chat-msg__text sup')).toBeNull();
    expect(container.querySelector('code')!.textContent).toContain('[来源1]');
  });

  it('半截流式标记（"[来"）保持原文不误转', () => {
    const { container } = render(
      <MarkdownContent content={'维修周期 [来'} className="chat-msg__text" />
    );
    expect(container.querySelector('.chat-msg__text sup')).toBeNull();
    expect(container.querySelector('.chat-msg__text')!.textContent).toContain('[来');
  });

  it('XSS 兜底不回归（script 注入被 sanitize 剥离）', () => {
    const { container } = render(
      <MarkdownContent content={'hello <script>alert(1)</script> world'} className="chat-msg__text" />
    );
    // react-markdown 默认不执行原始 HTML：script 不会成为元素
    expect(container.querySelector('script')).toBeNull();
  });
});
