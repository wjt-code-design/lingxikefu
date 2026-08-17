import { Empty, Typography } from 'antd';
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
 * - 快捷话术（P1-3）：点击填入输入框（callback，非 ref；WorkbenchLayout 注册）
 */
export function SourcePanel({
  sources,
  onUseReply,
}: {
  sources: MessageSource[];
  /** P1-3：点击话术 → 填入输入框（WorkbenchLayout 透传的 fill） */
  onUseReply?: (text: string) => void;
}) {
  return (
    <aside className="wb-right">
      <div className="wb-section">
        <Typography.Text className="wb-section__title">RAG 溯源</Typography.Text>
        <div className="wb-sources">
          {!sources.length ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="暂无引用来源"
              className="wb-empty"
            />
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
