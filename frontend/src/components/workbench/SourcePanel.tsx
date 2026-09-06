import { Typography } from 'antd';
import { BrandEmpty } from '@/components/common/BrandEmpty';
import { ANSWER_SOURCE_QUICK, type MessageSource } from '@/contracts/api';

/**
 * 三栏工作台 · 右栏：RAG 溯源（海盐蓝）。
 * - 溯源：ChatContainer 经 onSourcesChange 推送的最新 sources（doc_title + 相似度 + 片段）
 * - 空态区分（2026-08-25）：answerSource=quick（快捷话术短路，不检索）→ 明示「预置话术无引用」，
 *   避免与「暂无引用」混同被当成故障；普通空态维持原提示。
 * - 快捷话术（P1-3 · 方案 A 2026-09-06）：已移到输入框上方（ChatContainer 内渲染），
 *   本面板回归纯溯源，与工具栏「溯源来源」按钮的入口语义一致。
 */
export function SourcePanel({
  sources,
  answerSource,
  selectedMsgId,
}: {
  sources: MessageSource[];
  /** 最近一轮完成的回答来源标记：quick = 快捷话术预置答案（无知识库引用） */
  answerSource?: string;
  /** 溯源选中（2026-08-25）：非空表示面板正跟随某条选中的 AI 回复 */
  selectedMsgId?: string | null;
}) {
  const isQuickAnswer = !sources.length && answerSource === ANSWER_SOURCE_QUICK;
  return (
    <aside className="wb-right">
      <div className="wb-section">
        <Typography.Text className="wb-section__title">RAG 溯源</Typography.Text>
        {selectedMsgId && (
          <Typography.Text
            type="secondary"
            style={{ display: 'block', fontSize: 12, marginBottom: 8 }}
          >
            正在查看选中回复的溯源，点击对话中其他 AI 回复可切换
          </Typography.Text>
        )}
        <div className="wb-sources">
          {!sources.length ? (
            isQuickAnswer ? (
              <BrandEmpty
                title="预置话术回答"
                hint="该问题命中常见问题库，直接返回标准答案，不经知识库检索，故无引用来源"
              />
            ) : (
              <BrandEmpty title="暂无引用来源" hint="开始对话后，这里会显示引用的知识来源" />
            )
          ) : (
            sources.map((s, i) => (
              <div key={`${s.chunk_id}-${i}`} className="wb-source">
                <div className="wb-source__head">
                  <span className="wb-source__doc">{s.doc_title}</span>
                  <span className="wb-source__tag">已引用</span>
                </div>
                {/* A 修复：相似度=dense 原始余弦（绝对语义）；hybrid 下 score 是 RRF
                    融合分（≈0.03-0.05）无相似度语义，仅作缺省回退（存量旧数据） */}
                <div className="wb-source__score">
                  相似度 {Math.round((s.dense_score ?? s.score) * 100)}%
                </div>
                <div className="wb-source__snippet">{s.snippet}</div>
              </div>
            ))
          )}
        </div>
      </div>
    </aside>
  );
}
