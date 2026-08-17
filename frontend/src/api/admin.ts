import { http } from '@/api/client';
import type {
  AdminStats,
  OkResp,
  Role,
  UserListResp,
} from '@/contracts/api';

/** GET /admin/users → UserListResp */
export async function listUsers(page = 1, size = 20): Promise<UserListResp> {
  const r = await http.get<UserListResp>('/admin/users', { params: { page, size } });
  return r.data;
}

/** PUT /admin/users/{userId}/role → OkResp */
export async function updateUserRole(userId: string, role: Role): Promise<OkResp> {
  const r = await http.put<OkResp>(`/admin/users/${userId}/role`, { role });
  return r.data;
}

/** GET /admin/stats → AdminStats */
export async function getAdminStats(): Promise<AdminStats> {
  const r = await http.get<AdminStats>('/admin/stats');
  return r.data;
}

export interface FeedbackItem {
  message_content: string;
  role: string;
  comment?: string | null;
  created_at: string;
}

/** GET /admin/feedback → 踩反馈列表（运营排查用） */
export async function listFeedback(page = 1, size = 20): Promise<{ items: FeedbackItem[]; total: number }> {
  const r = await http.get<{ items: FeedbackItem[]; total: number }>('/admin/feedback', {
    params: { page, size },
  });
  return r.data;
}
