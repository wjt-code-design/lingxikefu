import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { FaqPage } from '@/pages/FaqPage';
import { getFaqDocContent } from '@/api/faq';

/**
 * 方案A（2026-09-05）：FAQ 页定位从「伪问答」改为「政策原文浏览入口」。
 * - 真实知识库态：说明条文案诚实标注「政策原文」；已索引文档条目可展开阅读原文
 *   （GET /faq/docs/{id}/content），未索引（parsing/failed）不提供阅读入口。
 * - 兜底静态 FAQ 态：不出现「阅读原文」。
 */

vi.mock('@/api/faq', () => ({
  getPublicFaq: vi.fn(() =>
    Promise.resolve({
      items: [
        {
          kb_id: 'kb-1',
          kb_name: '官方政策库',
          description: '平台政策文档',
          doc_count: 2,
          chunk_count: 5,
          docs: [
            { doc_id: 'doc-1', name: '会员权益.md', status: 'indexed', chunks: 3 },
            { doc_id: 'doc-2', name: '退货流程.md', status: 'parsing', chunks: 0 },
          ],
        },
      ],
    }),
  ),
  getFaqDocContent: vi.fn(() =>
    Promise.resolve({
      doc_id: 'doc-1',
      name: '会员权益.md',
      kb_name: '官方政策库',
      status: 'indexed',
      content: '会员享全场 95 折，生日当月双倍积分。',
    }),
  ),
}));

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <FaqPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('方案A：政策原文浏览', () => {
  beforeEach(() => {
    vi.mocked(getFaqDocContent).mockClear();
  });

  it('真实数据态：说明条诚实标注政策原文定位', async () => {
    renderPage();
    expect(
      await screen.findByText('以下政策原文来自企业知识库，点击条目可展开阅读原文'),
    ).toBeInTheDocument();
  });

  it('已索引文档展开后拉取并渲染原文内容', async () => {
    renderPage();
    const q = await screen.findByText('会员权益.md');
    await userEvent.click(q.closest('button')!);
    expect(await screen.findByText('阅读原文')).toBeInTheDocument();
    await userEvent.click(screen.getByText('阅读原文'));
    await waitFor(() => expect(getFaqDocContent).toHaveBeenCalledWith('doc-1'));
    expect(
      await screen.findByText('会员享全场 95 折，生日当月双倍积分。'),
    ).toBeInTheDocument();
  });

  it('未索引文档（parsing）不提供阅读入口', async () => {
    renderPage();
    const q = await screen.findByText('退货流程.md');
    await userEvent.click(q.closest('button')!);
    expect(await screen.findByText(/解析中/)).toBeInTheDocument();
    expect(screen.queryByText('阅读原文')).not.toBeInTheDocument();
  });
});
