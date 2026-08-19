import { useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Input, Spin, Typography } from 'antd';
import { SearchOutlined } from '@ant-design/icons';
import { BrandEmpty } from '@/components/common/BrandEmpty';
import { listKnowledgeBases, searchKnowledge } from '@/api/knowledge';
import type { KBItem, KnowledgeHit } from '@/contracts/api';
import './KbSearchPage.css';

/**
 * 知识库快搜（Phase4）：客服在对话中快速语义检索知识库。
 * - 左侧选择知识库（必选），右侧输入 query 调用 POST /knowledge/search
 * - 命中列表：snippet + 来源文档名 + 相似度徽标（tabular-nums）
 * - queryKey ['kb-search', kbId, query]；未选中 KB 时保留引导态。
 */
export function KbSearchPage() {
  const [kbId, setKbId] = useState<string | undefined>();
  const [keyword, setKeyword] = useState(''); // 输入框实时值
  const [query, setQuery] = useState(''); // 已提交的检索词

  const { data: kbsData, isLoading: kbsLoading } = useQuery({
    queryKey: ['kbs'],
    queryFn: listKnowledgeBases,
  });
  const kbs: KBItem[] = kbsData?.items ?? [];

  const { data: searchData, isFetching: searching } = useQuery({
    queryKey: ['kb-search', kbId, query],
    queryFn: () => searchKnowledge({ query, kb_id: kbId!, top_k: 10 }),
    enabled: !!kbId && query.trim().length > 0,
  });

  const activeKb = kbs.find((k) => k.kb_id === kbId);
  const hits: KnowledgeHit[] = searchData?.hits ?? [];

  const submitSearch = () => {
    if (kbId && keyword.trim()) setQuery(keyword.trim());
  };

  const switchKb = (id: string) => {
    setKbId(id);
    setKeyword('');
    setQuery('');
  };

  let main: ReactNode;
  if (!kbId) {
    main = <BrandEmpty title="请选择知识库" hint="从左侧选择一个知识库后即可进行语义检索" />;
  } else if (!query) {
    main = (
      <BrandEmpty
        title="输入问题开始检索"
        hint="支持自然语言提问，检索结果会展示命中片段与相似度"
      />
    );
  } else if (searching) {
    main = <Spin className="kbs-main__spin" />;
  } else if (hits.length === 0) {
    main = <BrandEmpty title="未找到相关内容" hint="换个问法试试，或切换知识库" />;
  } else {
    main = (
      <div className="kbs-hits">
        {hits.map((hit) => (
          <article className="kbs-hit" key={hit.chunk_id}>
            <header className="kbs-hit__head">
              <span className="kbs-hit__title" title={hit.doc_title}>
                {hit.doc_title}
              </span>
              <span className="kbs-hit__kb">{hit.kb_name}</span>
              <span className="kbs-hit__score">{hit.score.toFixed(2)}</span>
            </header>
            <p className="kbs-hit__snippet">{hit.snippet}</p>
          </article>
        ))}
      </div>
    );
  }

  return (
    <div className="kbs-page">
      <div className="kbs-page__header">
        <Typography.Title level={3} className="kbs-page__title">
          知识库快搜
        </Typography.Title>
        <Typography.Text className="kbs-page__subtitle">
          输入问题，对知识库进行语义检索
        </Typography.Text>
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
                  onClick={() => switchKb(k.kb_id)}
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
            <Input.Search
              className="kbs-search"
              allowClear
              prefix={<SearchOutlined />}
              placeholder={kbId ? '输入问题，语义检索当前知识库' : '请先在左侧选择知识库'}
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              onSearch={submitSearch}
              disabled={!kbId}
              enterButton="检索"
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
