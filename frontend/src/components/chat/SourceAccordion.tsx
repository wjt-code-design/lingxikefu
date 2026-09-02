import { useEffect, useRef } from 'react';
import { FolderOpenOutlined, FolderOutlined } from '@ant-design/icons';
import type { MessageSource } from '@/contracts/api';

/** 去掉文件扩展名，仅显示文档名（退换货政策.md → 退换货政策） */
function docName(title: string): string {
  return title.replace(/\.(md|pdf|txt|docx?|xlsx?|csv)$/i, '');
}

/** 来源入口：收起/展开由父级受控（MessageBubble），支持正文角标点击联动展开+高亮。 */
export function SourceAccordion({
  sources,
  open,
  onToggle,
  highlightN,
}: {
  sources: MessageSource[];
  open: boolean;
  onToggle: () => void;
  /** 正文角标点击的来源序号（1-based）：命中条目高亮；null = 无高亮 */
  highlightN: number | null;
}) {
  const activeRef = useRef<HTMLDivElement | null>(null);

  // 角标联动：高亮条目滚动到可视区（面板内就近滚动，不打断页面）。
  // useEffect 必须在 early return 之前调用（hooks 规则：无条件调用）；
  // scrollIntoView 用可选调用（jsdom 无实现，防测试炸）。
  useEffect(() => {
    if (open && highlightN != null) activeRef.current?.scrollIntoView?.({ block: 'nearest' });
  }, [open, highlightN]);

  if (!sources || sources.length === 0) return null;

  // 按文档名分组去重，保留各 chunk 在正文中的来源序号
  const groups = new Map<string, { title: string; refs: { n: number; snippet: string }[] }>();
  sources.forEach((s, i) => {
    const title = s.doc_title || `来源 ${i + 1}`;
    if (!groups.has(title)) groups.set(title, { title, refs: [] });
    groups.get(title)!.refs.push({ n: i + 1, snippet: s.snippet });
  });
  const groupList = [...groups.values()];

  return (
    <div className="chat-source">
      <button
        type="button"
        className="chat-source__toggle"
        onClick={onToggle}
        aria-expanded={open}
      >
        {open ? <FolderOpenOutlined /> : <FolderOutlined />}
        <span>{open ? '收起来源' : `来源 ${sources.length} 条`}</span>
      </button>
      {open && (
        <div className="chat-source__panel">
          {groupList.map((g, gi) => {
            const refsLabel =
              g.refs.length === 1 ? `[来源${g.refs[0].n}]` : `[来源${g.refs.map((r) => r.n).join('、')}]`;
            return (
              <div key={gi} className="chat-source__group">
                <div className="chat-source__group-title">
                  {docName(g.title)} <span className="chat-source__refs">{refsLabel}</span>
                </div>
                {g.refs.map((r) => (
                  <div
                    key={r.n}
                    ref={r.n === highlightN ? activeRef : undefined}
                    className={`chat-source__chunk${r.n === highlightN ? ' chat-source__chunk--active' : ''}`}
                  >
                    <span className="chat-source__ref">[来源{r.n}]</span> {r.snippet}
                  </div>
                ))}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
