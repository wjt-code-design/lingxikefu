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
  // P4：删除 role 可选字段——注册恒为 user，契约不再暴露可"提权"的假入口
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
  quota_total: number; // 每日配额上限（2026-08-20 后端补齐，此前恒 undefined）
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
  keyword?: string; // 第三批 #7：服务端模糊搜索（标题/客户邮箱/电话），admin 审计页用
  satisfaction?: 'satisfied' | 'neutral' | 'unsatisfied';
  order?: 'updated' | 'created'; // 排序键：updated（默认，工作台）| created（审计页）
}
export interface SessionListResp {
  items: Session[];
  total: number;
}
/** 用户画像摘要（Phase D，长期记忆）：仅 agent/admin 视角返回；顾客端为 undefined。 */
export interface SessionProfile {
  schema_version?: number;
  topics?: Record<string, number>; // 常问主题 -> 次数
  entities?: string[]; // 历史订单号/型号
  satisfaction?: { up?: number; down?: number };
  handoff?: { count?: number; last_at?: string };
  preferences?: { 品类?: string[] };
}
/** 转人工交接摘要（本次会话上下文压缩打包）：仅 agent/admin 视角返回。 */
export interface SessionHandoffSummary {
  topic?: string; // 命中流程主题（可多个，/ 分隔）
  entities?: string[]; // 具体实体（订单号/型号）
  question?: string; // 最近一条用户诉求
}
/** 批次B：会话状态机（阶段+槽位跨轮记忆）。旧会话为 null。 */
export interface SessionConvState {
  stage?: string; // greeting / info_collecting / resolving / clarifying
  topic?: string; // 当前流程主题（退款/退换货/保修维修…）
  slots?: Record<string, string>; // 已收集槽位（如 order_no）
  clarify_count?: number; // 澄清追问次数（批次C 用）
}
export interface SessionDetail {
  id: string;
  title?: string;
  messages: Message[];
  profile?: SessionProfile;
  handoff_summary?: SessionHandoffSummary;
  conv_state?: SessionConvState | null; // 批次B：会话状态机（客服观察用）
}
/** Branch 3：人工客服代发消息请求体（POST /sessions/{id}/messages，仅 admin/agent 可用）。 */
export interface AgentMessageReq {
  content: string;
}
/** 批次A 坐席辅助：AI 推荐回复请求体（POST /sessions/{id}/suggest，仅 admin/agent）。 */
export interface SuggestReq {
  /** 需要建议的问题；缺省取会话最近一条顾客消息 */
  question?: string;
  /** 绕过结果缓存强制重新生成（前端「重新生成」按钮用） */
  refresh?: boolean;
}
/** 批次A 坐席辅助响应：草拟回复 + 引用来源（fail-open：失败返回空 text）。 */
export interface SuggestResp {
  text: string;
  sources: MessageSource[];
}

// ---------- Messages ----------
// role 含义（P2：人工客服消息融合 · 2026-08-19）：
//   - 'user'      : 顾客（C 端）发的消息
//   - 'assistant' : AI 智能客服回复（含 RAG 引用）
//   - 'agent'     : 人工客服回复（由人工介入会话后产生，契约 P2 起支持）
// 前端 layout 规则（B 视野感知）：
//   - user 视角：user → 右（"我"）；assistant / agent → 左（对面的人）
//   - observe 视角（agent/admin 打开既有会话）：user / assistant → 左（顾客+AI）；
//     agent → 右（"我"，当前登录的客服）
export interface Message {
  id: string;
  session_id: string;
  role: 'user' | 'assistant' | 'agent';
  content: string;
  created_at: string;
  intent?: string;
  /** 仅 agent 角色携带：客服标识（工号 / 昵称），用于气泡身份标签 */
  agent_id?: string;
  agent_name?: string;
  /** 大扫查 F-major：工具回答标记（meta.tool，如 order_query）→ 历史加载/observe 徽章；普通回答无 */
  tool?: string;
  /** 2026-08-21：AI 回复的引用来源（message_sources，session 详情透出）。user/agent 通常为空 */
  sources?: MessageSource[];
  /** 2026-08-25：快捷话术回答标记（meta.answer_source=quick）→ 历史加载/observe「预置话术无引用」空态判断；普通回答无 */
  answer_source?: string;
}
/** 2026-08-25（code-review F3）：answer_source 取值常量——'quick' = 快捷话术预置答案（不经检索）。统一判断，避免裸字符串跨文件漂移。 */
export const ANSWER_SOURCE_QUICK = 'quick' as const;
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
  version: number; // S2 乐观锁版本号：流转时回传，服务端原子比较防并发覆盖
  // 移交摘要（build_handoff_summary 产物的 JSON 文本；一期 T3 遗留补发）
  summary?: string | null;
  // AI 预起草（架构二期 1）：low risk handoff 建单后后台草拟的回复；draft_kind "ai"=AI 预起草
  draft_suggestion?: string | null;
  draft_kind?: string | null;
  /** UI 审查低19：关联会话主题（仅列表端点填充） */
  session_title?: string | null;
  // 逐状态流转时间戳（一期 4 补发；closed 无独立列，用 updated_at）
  processing_at?: string | null;
  resolved_at?: string | null;
}
export interface TicketListResp {
  items: TicketItem[];
  total: number;
}
export interface StatusUpdateReq {
  status: TicketStatus;
  assignee_id?: string; // 可选：分配客服
  version: number; // S2 乐观锁：客户端回传当前版本，与服务端不匹配返回 409
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
  | { event: 'done'; data: { message_id: string; ticket_id?: string; user_message_id?: string; cache_hit?: boolean; clarify?: boolean; tool?: string; answer_source?: string } } // T1：handoff 建单后带工单号；R2/C4：user_message_id 供前端本地消息 id 对齐；T10：cache_hit 标记答案来自缓存；批次C 澄清轮标记；批次D 工具回答标记（如 order_query，与落库 meta 同源）；answer_source=quick 快捷话术回答（SourcePanel 区分「预置话术无引用」空态）
  | { event: 'error'; data: { code: string; message: string } };

// ---------- Chat · Stream Request ----------
export interface ChatStreamReq {
  session_id: string;
  content: string;
  stream: true;
  client_msg_id?: string; // R2：客户端提问幂等键（前端生成、重试复用，配额幂等扣费）
  image_paths?: string[]; // v1.3 图片理解：前端上传后的图片路径列表（后端 default_factory=list，缺省空）
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

// ---------- Suggestions（意见反馈页 · P2 修复） ----------
export interface SuggestionReq {
  type: 'bug' | 'suggestion' | 'other';
  content: string;
  contact?: string;
}
export interface SuggestionItem {
  id: string;
  user_account?: string | null; // 提交人 email/phone（运营联系用）
  type: 'bug' | 'suggestion' | 'other';
  content: string;
  contact?: string | null;
  created_at: string;
}
export interface SuggestionListResp {
  items: SuggestionItem[];
  total: number;
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
/** 架构三期 1：点踩缺口——down 反馈连被踩消息原文聚类（与 hot_gaps 的 refuse 源互补） */
export interface FeedbackGap {
  question: string;
  count: number;
  /** 组内最近一次 down 反馈时间（ISO8601） */
  last_at: string;
}
export interface AdminStats {
  sessions: number;
  messages: number;
  documents: number;
  feedback_up: number;
  feedback_down: number;
  avg_first_token_ms: number;
  hot_gaps: HotGap[];
  /** 三期 1：down 反馈聚类 Top10（被踩消息原文/次数/最近 down 时间）；?days 时间窗与 hot_gaps 共用（默认 7，0=不限） */
  feedback_gaps?: FeedbackGap[];
  /** T1.2 运营观测：工具回答分布（如 order_query→45），空对象=无数据 */
  tool_dist?: Record<string, number>;
  /** T1.2：澄清轮 assistant 消息数（meta.clarify=true）；独立观测口径，与 refuse_count 无推导关系 */
  clarify_rounds?: number;
  /** T1.2：会话主题分布（conv_state.topic 聚合，仅含已进入流程的会话） */
  topic_dist?: Record<string, number>;
  /** T1.2：refuse 用户消息总数 = 真拒答轮数（澄清轮 intent 落 qa 不计入，勿再减 clarify_rounds） */
  refuse_count?: number;
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
  /** T1.3 时序扩展：当日 assistant 工具回答分布（如 {order_query: 3}）；旧后端无此字段 */
  tool_dist?: Record<string, number>;
  /** T1.3：当日澄清轮数（meta.clarify=true 的 assistant 消息数）；旧后端无此字段 */
  clarify_rounds?: number;
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
