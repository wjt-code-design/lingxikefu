import { Collapse } from 'antd';
import type { MessageSource } from '@/contracts/api';

/** 来源折叠面板：展示每条来源的文档标题 + 片段（可溯源）。 */
export function SourceAccordion({ sources }: { sources: MessageSource[] }) {
  if (!sources || sources.length === 0) return null;
  const items = sources.map((s, i) => ({
    key: String(i),
    label: s.doc_title || `来源 ${i + 1}`,
    children: <div className="chat-source__snippet">{s.snippet}</div>,
  }));
  return (
    <div className="chat-source">
      <Collapse size="small" ghost items={items} className="chat-source__collapse" />
    </div>
  );
}
