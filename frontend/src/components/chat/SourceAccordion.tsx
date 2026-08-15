import { Collapse } from 'antd';
import type { MessageSource } from '@/contracts/api';

/** 去掉文件扩展名，仅显示文档名（退换货政策.md → 退换货政策） */
function docName(title: string): string {
  return title.replace(/\.(md|pdf|txt|docx?|xlsx?|csv)$/i, '');
}

/** 来源折叠面板：按文档去重合并，标题去扩展名，片段按正文 [来源N] 序号展示（可溯源）。 */
export function SourceAccordion({ sources }: { sources: MessageSource[] }) {
  if (!sources || sources.length === 0) return null;

  // 按文档名分组（同名文档合并为一项），保留各 chunk 在正文中的来源序号
  const groups = new Map<string, { title: string; refs: { n: number; snippet: string }[] }>();
  sources.forEach((s, i) => {
    const title = s.doc_title || `来源 ${i + 1}`;
    if (!groups.has(title)) groups.set(title, { title, refs: [] });
    groups.get(title)!.refs.push({ n: i + 1, snippet: s.snippet });
  });

  const items = [...groups.values()].map((g, gi) => {
    const refsLabel = g.refs.length === 1 ? `[来源${g.refs[0].n}]` : `[来源${g.refs.map((r) => r.n).join('、')}]`;
    return {
      key: String(gi),
      label: `${docName(g.title)} ${refsLabel}`,
      children: (
        <div className="chat-source__snippet">
          {g.refs.map((r) => (
            <div key={r.n} className="chat-source__chunk">
              <span className="chat-source__ref">[来源{r.n}]</span> {r.snippet}
            </div>
          ))}
        </div>
      ),
    };
  });

  return (
    <div className="chat-source">
      <Collapse size="small" ghost items={items} className="chat-source__collapse" />
    </div>
  );
}
