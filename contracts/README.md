# 灵犀智能客服 · 前后端契约映射表

本目录是前后端共享契约的**唯一真源**：

- `api.ts` — TS 类型真源（所有接口/事件/状态的类型定义，人工维护）
- `api-schema.json` — 后端 FastAPI 自动生成的 OpenAPI 3.1 schema（**不要手工改**，由 `backend/scripts/generate_openapi.py` 或容器内命令重新生成）

前端一律经 `frontend/src/contracts/api.ts`（re-export 桥 `export * from '../../../contracts/api'`）type-only 引用，
不维护第二份副本。

## 契约分类

| 分类 | 含义 | 校验方式 | 示例 |
|---|---|---|---|
| **A 接口类型** | HTTP request/response 模型 | 与 OpenAPI `components.schemas` 同名，做字段级比对 | LoginReq、AuthResp、Session、Ticket、ChatStreamReq |
| **B SSE 事件类型** | `/chat/stream` 事件协议 | OpenAPI 无对应，单独维护（与后端 chat.py emit 对齐） | SSEStage、SSEEvent、SSEData |
| **C 前端私有状态** | 仅存在于前端 store/组件 | 不落契约，不参与校验 | ChatStage、ChatStreamState、ChatMessage |

## 校验命令

```bash
# 生成后端 OpenAPI schema（本地有后端依赖时）
python backend/scripts/generate_openapi.py --out contracts/api-schema.json

# 契约校验（A 类字段级比对 + 后端漂移检查）
python scripts/check_contracts.py
# RESULT: PASS → 无差异；RESULT: FAIL → 见差异清单，需同步后端 Pydantic 或契约
```

> 容器环境（`/app` 只读，无法直接跑脚本）：
> ```bash
> docker exec lingxi-api-1 python -c "from app.main import app; import json; open('/tmp/api-schema.json','w',encoding='utf-8').write(json.dumps(app.openapi(), ensure_ascii=False, indent=2))"
> docker cp lingxi-api-1:/tmp/api-schema.json contracts/api-schema.json
> ```

## 变更流程

1. 改 `contracts/api.ts`（新增/删除/重命名字段或类型）
2. 同步修改后端 Pydantic 模型（`backend/app/...`）
3. 重新生成 `api-schema.json`（见上）
4. 跑 `python scripts/check_contracts.py`，确认 PASS
5. 前端 typecheck + test（re-export 桥零改动，但引用新字段处需同步）

## 已知后端未回填模型（KNOWN_GAP，跟踪项）

以下后端 OpenAPI 模型暂未回填到 `api.ts`（已加入校验白名单 `IGNORE_EXTRA`，不产生噪音；待后续轮次补齐）：

- **命名差异（契约已有对应）**：`AdminSettingsResp`↔AdminSettings、`KnowledgeSearchHit`↔KnowledgeHit、`FaqDocItem`↔FaqDoc、`FaqKbItem`↔FaqKBItem、`FaqListResp`↔PublicFaqResp、`UserRole`↔Role
- **枚举 → TS 内联 union（同名忽略）**：`FeedbackRating`、`SuggestionType`（TS 端以字面量 union 表达，无独立 interface）
- **未回填（待后续轮次补齐）**：`CreateSessionReq`、`CreateTicketReq`、`SatisfactionReq`、`FrontendErrorReq`、`SessionItem`、`SessionMessage`、`FeedbackItem`、`FeedbackListResp`、`FeedbackResp`、`ModelSettings`、`QuotaSettings`、`RagSettings`、`RateLimitSettings`
- **字段命名偏差（前端 API 适配层映射）**：`SessionItem.session_id`（后端）↔ 契约 `Session.id`（前端 `sessions.ts` 的 `toSession` 负责 session_id→id 映射，避免渗透到组件层）

> 校验脚本只比对「契约有且后端有」的 A 类类型字段；后端新增未列入白名单的模型会 FAIL 提示，保证门禁不被静默绕过。

## 版本历史

- **v0.3（R2 契约收敛，2026-08-18）**：回填 Tickets/Customers/Notifications/KnowledgeSearch/FAQ/Roles/AdminSettings/AuditLog/StatsTrend 等全部类型；
  新增 `ChatStreamReq.client_msg_id`（配额幂等键）、SSE `done.user_message_id`（C4 消息 id 对齐）；
  前端副本改 re-export 桥；api-schema.json 由后端自动生成（替换原手写 JSON Schema 版）。
- **v0.2**：手写 JSON Schema 双源版（已废弃，被自动生成版替代）。
