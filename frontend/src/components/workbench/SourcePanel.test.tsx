import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { SourcePanel } from './SourcePanel';
import type { MessageSource } from '@/contracts/api';

/** 空态区分（2026-08-25 溯源空面板排查）：
 * 快捷话术回答（answer_source=quick，不检索、sources 恒空）必须明示「预置话术」，
 * 与普通「暂无引用来源」区分——此前用户把空面板当故障上报。 */

const SRC: MessageSource = {
  chunk_id: 'c1',
  doc_id: 'd1',
  doc_title: '售后政策',
  score: 0.9,
  snippet: '保修期 12 个月',
};

describe('SourcePanel 溯源空态区分（answer_source）', () => {
  it('无 sources + 无标记 → 默认「暂无引用来源」', () => {
    render(<SourcePanel sources={[]} />);
    expect(screen.getByText('暂无引用来源')).toBeInTheDocument();
  });

  it('无 sources + answer_source=quick → 明示「预置话术回答」（旧实现混同空态 → 红）', () => {
    render(<SourcePanel sources={[]} answerSource="quick" />);
    expect(screen.getByText('预置话术回答')).toBeInTheDocument();
    expect(screen.getByText(/命中常见问题库/)).toBeInTheDocument();
    expect(screen.queryByText('暂无引用来源')).not.toBeInTheDocument();
  });

  it('有 sources 时正常渲染引用列表（不受标记影响）', () => {
    render(<SourcePanel sources={[SRC]} answerSource={undefined} />);
    expect(screen.getByText('售后政策')).toBeInTheDocument();
    expect(screen.getByText(/相似度 90%/)).toBeInTheDocument();
  });
});
