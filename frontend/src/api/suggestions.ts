import { http } from '@/api/client';
import type { OkResp, SuggestionItem, SuggestionListResp, SuggestionReq } from '@/contracts/api';

/**
 * 意见反馈接口（P2-修复#2）：FeedbackPage 假提交 → 真实落库。
 * POST 任意登录用户；GET 列表仅 admin（运营查看）。
 */

/** POST /suggestions → 提交意见反馈（201，每用户 5 分钟 5 条防刷） */
export async function submitSuggestion(req: SuggestionReq): Promise<OkResp> {
  const { data } = await http.post<OkResp>('/suggestions', req);
  return data;
}

/** GET /suggestions → 意见反馈列表（admin，时间倒序分页） */
export async function listSuggestions(page = 1, size = 20): Promise<SuggestionListResp> {
  const r = await http.get<SuggestionListResp>('/suggestions', { params: { page, size } });
  return r.data;
}

export type { SuggestionItem };
