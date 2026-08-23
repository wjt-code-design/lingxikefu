import { http } from '@/api/client';

export interface EvalHistoryItem {
  run_id: string;
  metric: string;
  score: number;
  total: number;
  passed: number;
  status: string;
  source: string;
  created_at: string;
}

export interface EvalHistoryResp {
  items: EvalHistoryItem[];
}

export interface EvalLatestResp {
  has_history: boolean;
  latest: {
    run_id: string;
    metrics: {
      metric: string;
      score: number;
      passed: number;
      total: number;
    }[];
  } | null;
  alerts: string[];
}

/** GET /admin/eval/history → 评测历史 */
export async function getEvalHistory(): Promise<EvalHistoryResp> {
  const r = await http.get<EvalHistoryResp>('/admin/eval/history');
  return r.data;
}

/** GET /admin/eval/latest → 最新评测结果 + 退化告警 */
export async function getEvalLatest(): Promise<EvalLatestResp> {
  const r = await http.get<EvalLatestResp>('/admin/eval/latest');
  return r.data;
}

/** POST /admin/eval/run → 触发评测 */
export async function runEval(params: { limit?: number }): Promise<{ run_id: string; status: string; message: string }> {
  const r = await http.post('/admin/eval/run', params);
  return r.data;
}
