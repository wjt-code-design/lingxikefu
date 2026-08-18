// ============================================================================
// 灵犀智能客服 · 前后端契约（单一真源）
// ============================================================================
// 本文件是前后端共享契约的【唯一真源】：
// - 后端 Pydantic 模型 / OpenAPI schema（contracts/api-schema.json，由 backend/scripts/generate_openapi.py 生成）
//   与本文件保持一致的接口类型；
// - 前端一律经 frontend/src/contracts/api.ts（re-export 桥）type-only 引用本文件，不维护第二份副本。
//
// 契约分类（映射表，见 contracts/README.md）：
//   A. 接口类型 —— HTTP request/response，与后端 OpenAPI components.schemas 一一对应，由校验脚本比对；
//   B. SSE 事件类型 —— /chat/stream 事件协议（SSEStage/SSEEvent），非 OpenAPI 接口 schema，单独维护；
//   C. 前端私有状态 —— 仅存在于前端 store/组件（如 ChatStage、ChatStreamState），不落契约。
//
// 变更流程：改本文件 → 同步后端 Pydantic → 重新生成 api-schema.json → 跑 scripts/check_contracts.py 校验。
// 版本：v0.3 契约收敛版（R2：新增 ChatStreamReq.client_msg_id 幂等键 + SSE done.user_message_id 消息 id 对齐；
//       回填 Tickets/Customers/Notifications/KnowledgeSearch/FAQ/Roles/AdminSettings/AuditLog/StatsTrend 等全部类型）

export const API_PREFIX = '/api/v1';

export type Role = 'admin' | 'agent' | 'user';

// ---------- Auth ----------
export interface LoginReq {
  account: string;
  password: string;
}
export interface RegisterReq {
  email?: string;
  phone?: string;
  password: string;
  role?: Role;
}
export interface AuthResp {
  user_id: string;
  access_token: string;
  refresh_token: string;
}
export interface RefreshReq {
  refresh_token: string;
}
export interface RefreshResp {
  access_token: string;
  refresh_token: string; // R-4：轮换后的新 refresh，前端须覆盖存储
}
export interface MeResp {
  user_id: string;
  email?: string;
  phone?: string;
  role: Role;
  quota_left: number;
}

// ---------- Sessions ----------
export interface Session {
  id: string;
  title?: string;
  created_at: string;
  updated_at: string;
  satisfaction?: string; // BUG-06：会话满意度（satisfied/neutral/unsatisfied）
  user_email?: string; // BUG-12：客户标识（agent/admin 视角显示）
  user_phone?: string;
}
export interface SessionListReq {
  page: number;
  size: number;
}
export interface SessionListResp {
  items: Session[];
  total: number;
}
export interface SessionDetail {
  id: string;
  title?: string;
  messages: Message[];
}

// ---------- Messages ----------
export interface Message {
  id: string;
  session_id: string;
  role: 'user' | 'assistant';
  content: string;
  created_at: string;
  intent?: string;
}
export interface MessageSource {
  chunk_id: string;
  doc_id?: string;
  doc_title: string;
  snippet: string;
  score: number;
}

// ---------- Tickets（T1 工单闭环） ----------
export type TicketStatus = 'open' | 'processing' | 'resolved' | 'closed';
export interface TicketItem {
  ticket_id: string;
  session_id: string;
  message_id?: string | null;
  status: TicketStatus;
  source?: string; // 工单来源：ai（LLM自动）/ manual（用户主动转人工）
  assignee_id?: string | null;
  created_at: string;
  updated_at: string;
}
export interface TicketListResp {
  items: TicketItem[];
  total: number;
}

// ---------- Customers（T6 客户画像） ----------
export interface CustomerItem {
  user_id: string;
  account: string;
  session_count: number;
  last_active?: string | null;
  open_tickets: number;
}
export interface CustomerListResp {
  items: CustomerItem[];
  total: number;
}

// ---------- Chat SSE ----------
export type SSEStage = 'retrieving' | 'generating' | 'done' | 'error';
export type SSEEvent =
  | { event: 'stage'; data: { stage: SSEStage; msg?: string } }
  | { event: 'intent'; data: { intent: string; refuse?: boolean } } // R-2：真实意图（qa/handoff/chitchat）
  | { event: 'token'; data: { delta: string } }
  | { event: 'sources'; data: { sources: MessageSource[] } }
  | { event: 'done'; data: { message_id: string; ticket_id?: string; user_message_id?: string } } // T1：handoff 建单后带工单号；R2/C4：user_message_id 供前端本地消息 id 对齐
  | { event: 'error'; data: { code: string; message: string } };

// ---------- Chat · Stream Request ----------
export interface ChatStreamReq {
  session_id: string;
  content: string;
  stream: true;
  client_msg_id?: string; // R2：客户端提问幂等键（前端生成、重试复用，配额幂等扣费）
}

// ---------- Knowledge ----------
export type DocStatus = 'parsing' | 'embedding' | 'indexed' | 'failed';
export interface KBItem {
  kb_id: string;
  name: string;
  doc_count: number;
  chunk_count: number;
}
export interface KBListResp {
  items: KBItem[];
}
export interface CreateKBReq {
  name: string;
  description?: string;
}
export interface DocItem {
  doc_id: string;
  name: string;
  status: DocStatus;
  chunks: number;
  error?: string;
}
export interface DocumentListResp {
  items: DocItem[];
}

// ---------- Knowledge · Semantic Search（Phase4） ----------
export interface KnowledgeSearchReq {
  query: string;
  kb_id: string;
  top_k?: number;
}
export interface KnowledgeHit {
  chunk_id: string;
  doc_id: string;
  doc_title: string;
  kb_id: string;
  kb_name: string;
  snippet: string;
  score: number;
  dense_score: number;
}
export interface KnowledgeSearchResp {
  query: string;
  hits: KnowledgeHit[];
}

// ---------- FAQ（公开 · Phase4） ----------
export interface FaqDoc {
  doc_id: string;
  name: string;
  status: DocStatus;
  chunks: number;
}
export interface FaqKBItem {
  kb_id: string;
  kb_name: string;
  description?: string;
  doc_count: number;
  chunk_count: number;
  docs: FaqDoc[];
}
export interface PublicFaqResp {
  items: FaqKBItem[];
}

// ---------- Feedback ----------
export interface FeedbackReq {
  rating: 'up' | 'down';
  comment?: string;
}

// ---------- 通用响应 ----------
export interface OkResp {
  ok: boolean;
}

// ---------- Quota ----------
export interface QuotaResp {
  date: string;
  used: number;
  limit: number;
  left: number;
}

// ---------- Admin ----------
export interface HotGap {
  question: string;
  count: number;
}
export interface AdminStats {
  sessions: number;
  messages: number;
  documents: number;
  feedback_up: number;
  feedback_down: number;
  avg_first_token_ms: number;
  hot_gaps: HotGap[];
}
export interface UserItem {
  user_id: string;
  account: string;
  role: Role;
  created_at: string;
}
export interface UserListResp {
  items: UserItem[];
  total: number;
}
export interface RoleUpdateReq {
  role: Role;
}

// ---------- Admin · Roles（权限管理 C9：菜单级可见性 + agent 数据范围，按钮级 RBAC 后置 P2） ----------
export interface RoleDef {
  role: Role;
  name: string;
  menus: string[];
  scope: string;
}
export interface RoleListResp {
  roles: RoleDef[];
}

// ---------- Admin · System Settings（Phase4） ----------
export interface AdminSettingsModel {
  provider: string;
  model: string;
  fallback?: string;
  embedding_provider?: string;
  embedding_model?: string;
}
export interface AdminSettingsRag {
  top_k: number;
  min_score: number;
  hybrid: boolean;
  chunk_size: number;
  chunk_overlap: number;
  answer_cache_enabled: boolean;
  answer_cache_threshold: number;
  max_upload_mb: number;
}
export interface AdminSettingsRateLimit {
  enabled: boolean;
}
export interface AdminSettingsQuota {
  daily_limit: number;
}
export interface AdminSettings {
  env: string;
  model: AdminSettingsModel;
  rag: AdminSettingsRag;
  rate_limit: AdminSettingsRateLimit;
  quota: AdminSettingsQuota;
}

// ---------- Admin · Audit Logs（Phase4） ----------
export interface AuditLogItem {
  audit_id: string;
  actor_email: string;
  actor_role: Role;
  action: string;
  resource: string;
  resource_id?: string | null;
  detail?: string | null;
  ip?: string | null;
  created_at: string;
}
export interface AuditLogListReq {
  action?: string;
  resource?: string;
  actor?: string;
  start?: string;
  end?: string;
  page?: number;
  size?: number;
}
export interface AuditLogListResp {
  items: AuditLogItem[];
  total: number;
}

// ---------- 统一错误模型 ----------
export interface ApiError {
  code: string;
  message: string;
  request_id: string;
}

// ---------- AdminStats Trend（P1：stats/trend） ----------
export interface TrendPoint {
  date: string; // YYYY-MM-DD
  sessions: number;
  messages: number;
  tickets: number;
}

export interface StatsTrendResp {
  days: TrendPoint[];
}

// ---------- Notifications（通知中心 SSE · P2） ----------
export interface NotificationItem {
  notification_id: string;
  event_type: string;
  title: string;
  content: string;
  resource_type?: string | null;
  resource_id?: string | null;
  is_read: boolean;
  created_at: string;
}
export interface NotificationListResp {
  items: NotificationItem[];
  total: number;
}
export interface UnreadCountResp {
  count: number;
}
