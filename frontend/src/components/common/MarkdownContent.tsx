import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeSanitize from 'rehype-sanitize';

/**
 * 共享 Markdown 渲染组件（assistant 消息 + 流式尾部共用）。
 * - remarkGfm：GitHub 风格表格/任务列表等；
 * - rehypeSanitize：XSS 兜底；
 * - 流式场景：ReactMarkdown 每次 content 变化都会重新解析渲染 → 边输出边按约定格式排版，
 *   不再等全部流式结束后才统一转换。
 */
export function MarkdownContent({ content, className }: { content: string; className?: string }) {
  return (
    <div className={className}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSanitize]}>
        {content}
      </ReactMarkdown>
    </div>
  );
}

export default MarkdownContent;