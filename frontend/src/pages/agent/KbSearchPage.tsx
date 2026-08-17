import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Input, Spin, Tag, Tooltip, Typography } from 'antd';
import { SearchOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { AppTable } from '@/components/common/AppTable';
import { BrandEmpty } from '@/components/common/BrandEmpty';
import { listDocuments, listKnowledgeBases } from '@/api/knowledge';
import type { DocItem, DocStatus, KBItem } from '@/contracts/api';
import './KbSearchPage.css';

/** 文档状态语义（parsing/embedding/indexed/failed 一色一义） */
const DOC_STATUS_META: Record<DocStatus, { color: string; text: string }> = {
  parsing: { color: 'processing', text: '解析中' },
  embedding: { color: 'processing', text: '向量化中' },
  indexed: { color: 'success', text: '已索引' },
  failed: { color: 'error', text: '失败' },
};

/**
 * 知识库快搜（Phase3）：客服在对话中快速查知识库文档。
 * 后端暂无独立 RAG 检索 API（仅 chat 流式内置检索），因此本页实现为
 * 「知识库浏览 + 关键词过滤」：左栏知识库列表，右栏文档表格；
 * 顶部搜索框对文档名做前端过滤（includes 忽略大小写）。
 * 数据统一 @tanstack/react-query，空态用 BrandEmpty。
 */
export function KbSearchPage() {
  const [kbId, setKbId] = useState<string | undefined>();
  const [keyword, setKeyword] = useState('');

  const { data: kbsData, isLoading: kbsLoading } = useQuery({
    queryKey: ['kbs'],
    queryFn: listKnowledgeBases,
  });
  const kbs: KBItem[] = kbsData?.items ?? [];

  const { data: docsData, isFetching: docsLoading } = useQuery({
    queryKey: ['docs', kbId],
    queryFn: () => listDocuments(kbId!),
    enabled: !!kbId,
  });
  const docs: DocItem[] = docsData?.items ?? [];

  // 默认选中第一个知识库，方便直接浏览文档
  useEffect(() => {
    if (kbs.length > 0) {
      setKbId((cur) => cur ?? kbs[0].kb_id);
    }
  }, [kbs]);

  // 关键词对文档名前端过滤（忽略大小写）
  const filteredDocs = useMemo(() => {
    const kw = keyword.trim().toLowerCase();
    if (!kw) return docs;
    return docs.filter((d) => d.name.toLowerCase().includes(kw));
  }, [docs, keyword]);

  const activeKb = kbs.find((k) => k.kb_id === kbId);

  const columns: ColumnsType<DocItem> = [
    {
      title: '文档名',
      dataIndex: 'name',
      ellipsis: true,
      render: (v: string, r) =>
        r.status === 'failed' && r.error ? (
          <Tooltip title={r.error}>
            <span className="kbs-doc-name">{v}</span>
          </Tooltip>
        ) : (
          <span className="kbs-doc-name">{v}</span>
        ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (s: DocStatus) => (
        <Tag color={DOC_STATUS_META[s]?.color ?? 'default'}>
          {DOC_STATUS_META[s]?.text ?? s}
        </Tag>
      ),
    },
    {
      title: '分块数',
      dataIndex: 'chunks',
      width: 90,
      render: (v: number) => <span className="kbs-chunk">{v}</span>,
    },
  ];

  let main: ReactNode;
  if (kbsLoading) {
    main = <Spin className="kbs-main__spin" />;
  } else if (!kbId) {
    main = <BrandEmpty title="请选择知识库" hint="从左侧选择一个知识库浏览文档" />;
  } else if (docsLoading) {
    main = <Spin className="kbs-main__spin" />;
  } else if (filteredDocs.length === 0) {
    main = (
      <BrandEmpty
        title={keyword ? '没有匹配的文档' : '该知识库暂无文档'}
        hint={keyword ? '换个关键词试试，或切换知识库' : '文档导入后会自动出现在这里'}
      />
    );
  } else {
    main = (
      <AppTable<DocItem>
        rowKey="doc_id"
        columns={columns}
        dataSource={filteredDocs}
        pagination={{ pageSize: 20, showSizeChanger: false }}
      />
    );
  }

  return (
    <div className="kbs-page">
      <div className="kbs-page__header">
        <Typography.Title level={4} className="kbs-page__title">
          知识库快搜
        </Typography.Title>
        <Typography.Text className="kbs-page__subtitle">
          客服对话中快速查阅知识库文档
        </Typography.Text>
      </div>

      <div className="kbs-notice">
        当前为文档级检索（按文档名过滤），RAG 语义检索接口待后端开放。
      </div>

      <div className="kbs-layout">
        <aside className="kbs-side">
          <header className="kbs-side__head">
            <span className="kbs-side__title">知识库</span>
            <span className="kbs-side__count">{kbs.length}</span>
          </header>
          {kbsLoading ? (
            <div className="kbs-side__status">
              <Spin size="small" />
            </div>
          ) : kbs.length === 0 ? (
            <BrandEmpty title="暂无知识库" hint="知识库为空，请先上传文档" />
          ) : (
            <div className="kbs-list">
              {kbs.map((k) => (
                <button
                  type="button"
                  key={k.kb_id}
                  className={[
                    'kbs-list__item',
                    k.kb_id === kbId ? 'kbs-list__item--active' : '',
                  ]
                    .filter(Boolean)
                    .join(' ')}
                  onClick={() => setKbId(k.kb_id)}
                >
                  <span className="kbs-list__name">{k.name}</span>
                  <span className="kbs-list__meta">
                    {k.doc_count} 文档 · {k.chunk_count} 分块
                  </span>
                </button>
              ))}
            </div>
          )}
        </aside>

        <section className="kbs-main">
          <div className="kbs-main__bar">
            <Input
              allowClear
              prefix={<SearchOutlined />}
              placeholder="搜索文档名（忽略大小写）"
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              className="kbs-search"
            />
            <Typography.Text className="kbs-main__hint">
              {activeKb ? `当前库：${activeKb.name}` : '未选择知识库'}
            </Typography.Text>
          </div>
          {main}
        </section>
      </div>
    </div>
  );
}

export default KbSearchPage;
