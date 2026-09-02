import type { ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeSanitize from 'rehype-sanitize';

/**
 * [来源N] 溯源引用 → <sup>N</sup> 角标（remark 级转换，零新依赖）。
 * - 输出真实元素节点（emphasis + data.hName='sup'）：rehype-sanitize 只剥非白名单属性
 *   （className 会被剥、元素标签不会）→ 样式用元素选择器 .chat-msg__text sup，不依赖 class；
 * - 仅访问 text 节点：code/inlineCode 正文是 value 不是 children，代码内 "[来源N]" 天然豁免；
 * - 流式半截 "[来" 不匹配保持原文，收完自愈；复制按钮走原始 msg.content，不受影响。
 */
const CITATION_SPLIT = /(\[来源\d+\])/;
const CITATION_EXACT = /^\[来源(\d+)\]$/;

interface MdNode {
  type: string;
  value?: string;
  data?: { hName?: string; hProperties?: Record<string, unknown> };
  children?: MdNode[];
}

function remarkCitations() {
  return (tree: unknown) => {
    const visit = (node: MdNode): void => {
      const children = node.children;
      if (!Array.isArray(children)) return;
      for (let i = 0; i < children.length; i++) {
        const child = children[i];
        if (
          child.type === 'text' &&
          typeof child.value === 'string' &&
          child.value.includes('[来源')
        ) {
          const parts = child.value.split(CITATION_SPLIT).filter(Boolean);
          const repl: MdNode[] = parts.map((p) => {
            const m = CITATION_EXACT.exec(p);
            return m
              ? {
                  type: 'emphasis',
                  data: { hName: 'sup', hProperties: {} },
                  children: [{ type: 'text', value: m[1] }],
                }
              : { type: 'text', value: p };
          });
          children.splice(i, 1, ...repl);
          i += repl.length - 1;
        } else {
          visit(child);
        }
      }
    };
    visit(tree as MdNode);
  };
}

/**
 * 共享 Markdown 渲染组件（assistant 消息 + 流式尾部共用）。
 * - remarkGfm：GitHub 风格表格/任务列表等；
 * - remarkCitations：[来源N] → sup 角标 chip；
 * - rehypeSanitize：XSS 兜底；
 * - 交互角标（interactiveCitations）：staff + 有 sources 时 sup 升级为可点击按钮
 *   （role=button + aria-label + 键盘 Enter/Space），点击回调 onCitationClick(N)。
 *   N 从 children 文本解析（textContent 路线）——remark/sanitize 零改动；
 *   components 自定义渲染发生在 sanitize 之后，不触碰白名单。
 * - 流式场景：ReactMarkdown 每次 content 变化都会重新解析渲染 → 边输出边按约定格式排版，
 *   不再等全部流式结束后才统一转换。
 */
export function MarkdownContent({
  content,
  className,
  interactiveCitations = false,
  onCitationClick,
}: {
  content: string;
  className?: string;
  /** staff + 有 sources 时开启：sup 角标可点击（顾客端/流式尾部不开启，纯展示） */
  interactiveCitations?: boolean;
  onCitationClick?: (n: number) => void;
}) {
  const components = interactiveCitations
    ? {
        sup: ({ children }: { children?: ReactNode }) => {
          const n = Number(children);
          const fire = () => {
            if (Number.isFinite(n)) onCitationClick?.(n);
          };
          return (
            <sup
              role="button"
              tabIndex={0}
              aria-label={`来源 ${children}`}
              onClick={(e) => {
                e.stopPropagation(); // 防冒泡到根气泡误切溯源面板（双保险，role=button 本就被拦截器捕获）
                fire();
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  fire();
                }
              }}
            >
              {children}
            </sup>
          );
        },
      }
    : undefined;
  return (
    <div className={className}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkCitations]}
        rehypePlugins={[rehypeSanitize]}
        components={components}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

export default MarkdownContent;
