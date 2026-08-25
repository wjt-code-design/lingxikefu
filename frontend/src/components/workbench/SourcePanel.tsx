import { Typography } from 'antd';
import { BrandEmpty } from '@/components/common/BrandEmpty';
import type { MessageSource } from '@/contracts/api';

/** 快捷话术：点击填入输入框（onUse），供用户在 Composer 修改后发送。 */
const QUICK_REPLIES = [
  '我想转人工客服',
  '帮我查一下订单物流',
  '我要申请退款',
  '积分怎么使用？',
];

/**
 * 三栏工作台 · 右栏：RAG 溯源 + 快捷话术（海盐蓝）。
 * - 溯源：ChatContainer 经 onSourcesChange 推送的最新 sources（doc_title + 相似度 + 片段）
 * - 空态区分（2026-08-25）：answerSource=quick（快捷话术短路，不检索）→ 明示「预置话术无引用」，
 *   避免与「暂无引用」混同被当成故障；普通空态维持原提示。
 * - 快捷话术（P1-3）：点击填入输入框（callback，非 ref；WorkbenchLayout 注册）
 */
export function SourcePanel({
  sources,
  answerSource,
  onUseReply,
}: {
  sources: MessageSource[];
  /** 最近一轮完成的回答来源标记：quick = 快捷话术预置答案（无知识库引用） */
  answerSource?: string;
  /** P1-3：点击话术 → 填入输入框（WorkbenchLayout 透传的 fill） */
  onUseReply?: (text: string) => void;
}) {
  const isQuickAnswer = !sources.length && answerSource === 'quick';
  return (
    <aside className="wb-right">
      <div className="wb-section">
        <Typography.Text className="wb-section__title">RAG 溯源</Typography.Text>
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
                <div className="wb-source__score">相似度 {Math.round(s.score * 100)}%</div>
                <div className="wb-source__snippet">{s.snippet}</div>
              </div>
            ))
          )}
        </div>
      </div>

      <div className="wb-section">
        <Typography.Text className="wb-section__title">快捷话术</Typography.Text>
        <div className="wb-replies">
          {QUICK_REPLIES.map((r) => (
            <button
              key={r}
              type="button"
              className="wb-reply"
              onClick={() => onUseReply?.(r)}
              title="点击填入输入框"
            >
              {r}
            </button>
          ))}
        </div>
        <Typography.Text type="secondary" className="wb-replies-hint">
          点击话术填入输入框，可修改后发送
        </Typography.Text>
      </div>
    </aside>
  );
}
