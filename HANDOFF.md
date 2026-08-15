# HANDOFF · 灵犀（Lingxi）智能客服 · 交接状态

> 用途：**防上下文 token 耗尽 / 无缝切换模型 / 新会话接续**。任何新模型或新会话开始前，先读本文件即可无缝接上，无需重问用户。
> 最后更新：2026-08-16 01:30

---

## 1. 项目是什么

灵犀 = 星河智家 3C 数码/家电智能客服系统（RAG 问答 + 知识库管理）。
- 后端：FastAPI + SQLAlchemy + PostgreSQL + Redis + Qdrant，AI 层用 httpx 直连智谱/百炼（双 provider）
- 前端：React 18 + Vite + TypeScript + Ant Design + React Router
- 注意：**另有旧项目「智服」也占过 5173 端口**（已 kill 其 dev server，勿再混淆）

## 2. 当前运行状态（关键！）

| 服务 | 地址 | 状态 |
|---|---|---|
| 前端 dev | http://localhost:5173 | ✅ 运行中（PID 22900，IPv4+IPv6 双栈） |
| 后端 API | http://localhost:8003 | ✅ 运行中（PID 2900） |
| PostgreSQL / Redis / Qdrant | localhost 5432 / 6379 / 6333 | 宿主机已起 |

- 登录账号：`admin@lingxi.com` / `lingxi123`（注册制，无种子账号）
- 前端 API 走 vite 代理：`/api` → `http://localhost:8003`（vite.config.ts server.proxy）
- 后端启动方式：`backend\start_dev.bat`（已内置 ZHIPU_API_KEY 覆盖 + localhost 主机配置）
- 后端热重启后需手动确认端口存活；前端 vite 重启后 5173 正常

## 3. 已修复的问题（含根因，别再踩）

1. **5173 打开是智服不是灵犀**：两项目 dev server 抢端口（灵犀 IPv4+IPv6 全网卡 vs 智服仅 IPv6），浏览器默认走 IPv6 命中智服。→ kill 智服 PID。
2. **登录/API 404**：前端 `.env` 空 → `VITE_API_BASE` 回退相对路径，vite 无代理。→ vite.config.ts 加 `server.proxy '/api'→8003`。（曾尝试 define/transform 插件均无效，已回滚）
3. **对话 400 Bad Request（双根因）**：
   - Bug A（代码）：`rag_service.stream_answer` 硬塞 `settings.CHAT_MODEL`（百炼名 qwen3.7-flash）给智谱 provider → 智谱报 modelCode 不存在。修复：不传 model，让 `OpenAILikeChatClient._default_model()` 按 provider 选（commit 50746e9）。
   - Bug B（环境）：Windows 用户环境变量 `ZHIPU_API_KEY=你的Key`（占位符）> pydantic-settings 优先级 > .env 真实 key → Bearer 中文 → httpx ASCII 编码 UnicodeEncodeError。修复：start_dev.bat 内 `set ZHIPU_API_KEY=真实key` 覆盖（commit 前已生效）。
4. **来源展示乱 + 片段空**（本次 01:30 修复，未提交）：
   - 后端 `_to_sources` 发 `text` 字段，前端类型声明 `snippet` → 契约漂移，片段渲染空。
   - 前端 SourceAccordion 把 8 个 chunk 的 doc_title 平铺（带扩展名、未去重）→ 回复尾部挂一长串文件名。
   - 修复：后端字段统一 `snippet`；前端按文档名分组去重、标题去扩展名、片段按 [来源N] 展示；prompt 禁止输出文件名元信息。
   - ⚠️ **此改动尚未 git commit，需验证后提交。**

## 4. 测试与验证

- 后端：`cd backend && python -m pytest tests/ -x -q --no-cov`（沙箱需 --no-cov，Windows 下 coverage erase 被安全策略拦）
- 前端：`cd frontend && npx tsc --noEmit && npm run build`
- 端到端冒烟：后端起 8003 + 前端起 5173 → 登录 → 对话问"退货" → 应返回结构化 Markdown + 折叠来源（按文档去重、无扩展名、片段有内容）
- 智谱 key（.env 内）：`[REDACTED-ZHIPU-KEY]`，模型 `glm-5.1`，provider=zhipu

## 5. 代码位置速查

- RAG 管线：`backend/app/services/rag_service.py`（stream_answer / run_pipeline / _to_sources）
- Prompt 组装：`backend/app/prompts/qa_prompt.py`（SYSTEM_PROMPT 格式规则）
- Chat SSE 路由：`backend/app/api/chat.py`（事件协议 stage→token→sources→done）
- LLM 客户端：`backend/app/llm_clients/chat.py`（provider-aware `_default_model()`）
- 前端 SSE：`frontend/src/hooks/useChatStream.ts`（fetch + ReadableStream 解析）
- 前端来源面板：`frontend/src/components/chat/SourceAccordion.tsx`（去重分组）
- SSE 契约：`frontend/src/contracts/api.ts`（MessageSource / SSEEvent）

## 6. 下一步（未决事项）

1. **本次格式修复未提交**：跑完验证后 `git add backend/app/services/rag_service.py backend/app/api/chat.py backend/app/prompts/qa_prompt.py backend/tests/test_chat_api.py frontend/src/components/chat/SourceAccordion.tsx frontend/src/contracts/api.ts frontend/src/styles/globals.css && git commit`
2. ~~检查 message_sources 历史消息回显是否也用了 snippet~~ → 已确认前端不加载历史消息回显（get_session 仅返回 session 元信息，ChatContainer 纯本地态），无此路径
3. 前两处 vite.config.ts.timestamp-*.mjs 是残留文件，可清理

## 7. 模型切换约定（防 token 耗尽 / 新会话接续必须知道）

- **默认模型**：`deepseek-v4-flash`（省 token 优先，90% 工作用它）
- **切 `deepseek-v4-pro` 的唯一条件**（满足其一）：
  1. 跨 ≥5 个文件的重构
  2. 同一问题连续失败 2 次仍未解决
  3. 复杂算法 / 架构权衡类重活
- **红线**：切换必须由用户手动发起（`/model` 或 WorkBuddy 模型切换），**Agent 不得自切模型**（用户硬约束）
- **长上下文场景**：若走 Claude Code 兼容端点且需长上下文，模型名带 `[1m]` 后缀（如 `deepseek-v4-flash[1m]`）；WorkBuddy 会话内由平台管理，无需手动加后缀
- **切换后**：新会话/新模型先读本 HANDOFF.md 即可无缝接上（运行状态、账号、bug 根因、代码速查已全量固化）
