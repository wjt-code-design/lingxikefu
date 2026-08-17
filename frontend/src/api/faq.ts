import { http } from './client';
import type { PublicFaqResp } from '@/contracts/api';

/** GET /faq（公开 · 无鉴权）→ PublicFaqResp（知识库 → 结构化 FAQ） */
export async function getPublicFaq(): Promise<PublicFaqResp> {
  const r = await http.get<PublicFaqResp>('/faq');
  return r.data;
}
