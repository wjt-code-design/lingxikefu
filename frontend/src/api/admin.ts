import { http } from '@/api/client';
import type {
  AdminSettings,
  AdminStats,
  AuditLogListReq,
  AuditLogListResp,
  OkResp,
  Role,
  RoleListResp,
  StatsTrendResp,
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

/** 运营趋势（P1）：近 N 天会话/消息/工单按日计数。 */
export async function getStatsTrend(days = 14): Promise<StatsTrendResp> {
  const r = await http.get<StatsTrendResp>('/admin/stats/trend', { params: { days } });
  return r.data;
}

/** GET /admin/settings → AdminSettings（真源 .env，只读） */
export async function getAdminSettings(): Promise<AdminSettings> {
  const r = await http.get<AdminSettings>('/admin/settings');
  return r.data;
}

/** GET /admin/audit-logs → AuditLogListResp（筛选 + 分页） */
export async function getAuditLogs(req: AuditLogListReq = {}): Promise<AuditLogListResp> {
  const r = await http.get<AuditLogListResp>('/admin/audit-logs', { params: req });
  return r.data;
}
/** GET /admin/roles → RoleListResp（只读角色权限定义：菜单级可见性 + 数据范围） */
export async function getRoles(): Promise<RoleListResp> {
  const r = await http.get<RoleListResp>('/admin/roles');
  return r.data;
}