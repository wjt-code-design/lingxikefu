import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { MessageSource } from '@/contracts/api';
import { SourceAccordion } from './SourceAccordion';

const SOURCES: MessageSource[] = [
  { chunk_id: 'c1', doc_title: '售后政策.md', snippet: '维修周期 5-10 个工作日', score: 0.9 },
  { chunk_id: 'c2', doc_title: '售后政策.md', snippet: '过保收材料费与上门费', score: 0.8 },
  { chunk_id: 'c3', doc_title: '物流说明.md', snippet: '同城次日达', score: 0.7 },
];

describe('SourceAccordion 受控化（批次 1：角标点击联动）', () => {
  it('open=false 仅 toggle 可见，panel 不渲染', () => {
    render(<SourceAccordion sources={SOURCES} open={false} onToggle={() => {}} highlightN={null} />);
    expect(screen.getByRole('button')).toBeInTheDocument();
    expect(screen.queryByText('维修周期 5-10 个工作日')).not.toBeInTheDocument();
  });

  it('open=true 展开分组并正确标注 [来源N]', () => {
    render(<SourceAccordion sources={SOURCES} open onToggle={() => {}} highlightN={null} />);
    // [来源3] 同时出现在组标题与 chunk 行（物流说明.md 单条组）
    expect(screen.getAllByText(/\[来源3\]/).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/\[来源1、2\]/).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('维修周期 5-10 个工作日')).toBeInTheDocument();
  });

  it('highlightN 命中的 chunk 带 active 类，其余不带', () => {
    render(<SourceAccordion sources={SOURCES} open onToggle={() => {}} highlightN={2} />);
    const active = document.querySelector('.chat-source__chunk--active');
    expect(active).not.toBeNull();
    expect(active!.textContent).toContain('过保收材料费');
    expect(document.querySelectorAll('.chat-source__chunk--active')).toHaveLength(1);
  });

  it('点击 toggle 调用 onToggle 回调', async () => {
    const onToggle = vi.fn();
    render(<SourceAccordion sources={SOURCES} open={false} onToggle={onToggle} highlightN={null} />);
    await userEvent.click(screen.getByRole('button'));
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it('sources 为空返回 null', () => {
    const { container } = render(
      <SourceAccordion sources={[]} open onToggle={() => {}} highlightN={null} />
    );
    expect(container).toBeEmptyDOMElement();
  });
});
