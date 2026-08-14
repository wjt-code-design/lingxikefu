// Lingxi 智能客服系统 · 前后端共享契约（单一真源 / Single Source of Truth）
// 来源：后端规划 §3（request/response schema + SSE 事件协议），与前端规划 §1.4 一致
// 规则：字段名 / 枚举值必须与后端 Pydantic 模型、api-schema.json 完全一致。
//      任何字段变更先改此处 → 同步后端 pydantic → 同步前端 client，禁止手写漂移。
// 版本：v0.2 决策收敛版

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
  doc_title: string;
  snippet: string;
  score: number;
}

// ---------- Chat SSE ----------
export type SSEStage = 'retrieving' | 'generating' | 'done' | 'error';
export type SSEEvent =
  | { event: 'stage'; data: { stage: SSEStage; msg?: string } }
  | { event: 'token'; data: { delta: string } }
  | { event: 'sources'; data: { sources: MessageSource[] } }
  | { event: 'done'; data: { message_id: string } }
  | { event: 'error'; data: { code: string; message: string } };

export interface ChatStreamReq {
  session_id: string;
  content: string;
  stream: true;
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
export interface AdminStats {
  sessions: number;
  messages: number;
  documents: number;
  feedback_up: number;
  feedback_down: number;
  avg_first_token_ms: number;
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

// ---------- 统一错误模型 ----------
export interface ApiError {
  code: string;
  message: string;
  request_id: string;
}
