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

  // A 修复：hybrid 检索下 score 是 RRF 融合分（≈0.03-0.05，无绝对语义），
  // 「相似度」必须显示 dense_score（dense 原始余弦）；旧数据无 dense_score 才回退 score。
  it('相似度显示 dense_score（真实余弦），非 RRF 排序分', () => {
    const hybrid: MessageSource = { ...SRC, score: 0.049, dense_score: 0.87 };
    render(<SourcePanel sources={[hybrid]} />);
    expect(screen.getByText(/相似度 87%/)).toBeInTheDocument();
    expect(screen.queryByText(/相似度 5%/)).not.toBeInTheDocument();
  });

  it('旧数据无 dense_score → 回退 score 口径（不显示 NaN）', () => {
    render(<SourcePanel sources={[SRC]} />);
    expect(screen.getByText(/相似度 90%/)).toBeInTheDocument();
  });
});
describe('SourcePanel 溯源选中提示（2026-08-25 点哪条看哪条）', () => {
  it('有 sources + selectedMsgId → 提示"正在查看选中回复的溯源"并正常渲染引用', () => {
    render(<SourcePanel sources={[SRC]} selectedMsgId="m2" />);
    expect(screen.getByText(/正在查看选中回复的溯源/)).toBeInTheDocument();
    expect(screen.getByText('售后政策')).toBeInTheDocument();
  });

  it('无选中（selectedMsgId 为空）→ 不显示选中提示', () => {
    render(<SourcePanel sources={[SRC]} selectedMsgId={null} />);
    expect(screen.queryByText(/正在查看选中回复的溯源/)).not.toBeInTheDocument();
  });
});
