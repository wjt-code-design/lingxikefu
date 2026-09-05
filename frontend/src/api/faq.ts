import { http } from './client';
import type { FaqDocContentResp, PublicFaqResp } from '@/contracts/api';

/** GET /faq（公开 · 无鉴权）→ PublicFaqResp（知识库 → 结构化 FAQ） */
export async function getPublicFaq(): Promise<PublicFaqResp> {
  const r = await http.get<PublicFaqResp>('/faq');
  return r.data;
}

/** GET /faq/docs/{doc_id}/content（公开 · 方案A 政策原文浏览）→ 单篇已索引文档原文 */
export async function getFaqDocContent(docId: string): Promise<FaqDocContentResp> {
  const r = await http.get<FaqDocContentResp>(`/faq/docs/${docId}/content`);
  return r.data;
}
