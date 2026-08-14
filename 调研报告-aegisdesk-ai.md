# AegisDesk AI 深度调研报告

> 调研方式：克隆 `https://github.com/Itachi852/aegisdesk-ai` 整库（1 次 commit，main 分支），逐文件通读 README、项目说明、运行指南、4 份 docs、全部后端 Python 源码（约 4.8k 行）、前端 React/TS 源码，并核对 GitHub 实时元数据（stars/forks/issues/pushed_at）。结论均基于实际代码与文档，附文件级证据。

---

## 0. 一句话结论

**AegisDesk AI 是一个"教学/演示导向"的企业智能客服系统**：用 FastAPI + LangChain/LangGraph + Qdrant 搭了一套相当完整的 RAG 问答链路（多路召回 → RRF → Rerank → 相关性过滤 → 流式回答 → 知识来源展示），配 React + Ant Design 前端。它的**文档与 RAG 工程细节非常扎实**，但**工程成熟度偏低**：无测试、无 CI、无版本发布、仅 1 名作者、单 commit 后停更（2026-06-15）。属于"个人练手/课程作业级别但完成度不错的利基项目"，而非活跃维护的产品。

---

## 1. 项目是什么

### 1.1 名称与定位
- 项目名 **AegisDesk AI**，GitHub 描述为「AI智能客服系统」（`api.github.com` 返回的 `description` 字段）。
- 定位：**基于大语言模型 + RAG 架构的企业级智能客服系统**，目标是"完整展示一条企业级 AI 客服链路"（README 第 5–9 行）。

### 1.2 核心功能（官方定义）
用户注册登录、独立会话、多轮问答、知识库上传、向量检索、LLM 流式问答、回答末尾知识来源展示、回答反馈、业务意图标注、每日提问次数限制（README 第 3 行）。

### 1.3 目标用户与解决的问题
- **目标用户**：中小型企业 / 客服团队，想把企业内部文档（FAQ、退换货政策、产品资料等）变成可被对话式检索的客服知识库。
- **解决的问题**：替代/辅助人工重复答疑，用 RAG 把回答"锚定"在企业私有知识上，降低大模型幻觉（README、项目说明第 5 节、docs/AI架构设计.md 第 6 节）。

### 1.4 核心链路（官方原话）
```
用户提问 -> 业务意图分类 -> 多轮上下文 -> 知识库检索 -> Rerank -> Prompt 拼接 -> LLM 流式回答 -> 保存消息与引用来源
```
（README 第 7–9 行；项目说明.md、docs/AI架构设计.md 同义复述）

---

## 2. 做了什么（功能 / 模块）

代码分层清晰，目录即职责（`backend/app/`）：

| 模块 | 路径 | 提供的能力 |
|---|---|---|
| API 路由 | `backend/app/api/` | auth / sessions / chat / knowledge / feedback / admin 六组路由 |
| 核心配置与安全 | `backend/app/core/` | Pydantic Settings、SQLAlchemy 引擎、JWT、日志 |
| 数据模型 | `backend/app/models/` | user / chat / knowledge / message_source / feedback / usage 7 张表 |
| DTO | `backend/app/schemas/` | Pydantic 请求/响应模型 |
| 服务层 | `backend/app/services/` | RAG、LLM、Embedding、Qdrant 向量、Rerank、稀疏向量、意图、文档解析、知识导入、相邻块扩展、启动初始化 |
| 提示词 | `backend/app/prompts/qa_prompt.py` | 意图路由 / 问题改写 / QA / 无知识兜底 / 闲聊 五套 Prompt |
| 工具 | `backend/app/utils/` | 文本切分（QA 感知）、异常归一化 |
| 前端 | `frontend/src/` | 登录/注册/聊天/知识管理 4 个页面 + API 封装 + SSE 客户端 |

### 2.1 用户与会话
- 邮箱 **或** 手机号注册/登录，JWT 鉴权（`api/auth.py`、`core/security.py`：HS256 + passlib pbkdf2_sha256）。
- 独立 Session，首条问题前 30 字自动生成标题（`api/chat.py` `_build_session_title`）。
- 历史会话列表/详情，删除会话级联清理消息、引用来源、反馈（`api/sessions.py` `delete_session`）。
- 多轮对话：后端携带**最近 10 条**历史消息（`api/chat.py` `_load_recent_history`，`config.rag...` 文档称上限 10）。

### 2.2 AI 问答（用户可感知）
- **SSE 流式输出**，逐字展示（`api/chat.py` `stream_chat` + `services/rag_service.py` `rag_answer_stream`）。
- 阶段进度提示：`preparing / intent / rewrite / retrieve / rerank / generate`（`rag_service.py` `_progress_event`），首个 token 到达后替换为正式回答。
- 单次提问 ≤ **500 字**（`MAX_QUESTION_LENGTH`，`rag_service.py` `validate_question`）。
- 每日提问上限可配，默认 **100**（`DAILY_QUESTION_LIMIT`）。
- **本地业务意图分类**：5 类（产品咨询/售后问题/闲聊/投诉/其他），优先级 `投诉 > 售后 > 产品咨询 > 闲聊 > 其他`，负面情绪产品表达优先归投诉（`services/intent_service.py`、`api/chat.py` 把 intent 落到 `chat_messages.intent` 并在前端气泡旁展示）。

### 2.3 知识库管理
- 上传 `.txt` / `.md`，解析+清洗+智能切分（`document_service.py` + `utils/text_splitter.py`）。
- **QA 文档特殊处理**：识别到 ≥3 组 `Q:/A:`、`问:/答:`、`问题:/答案:` 时按"一问一答一个 chunk"切分，否则回退递归切分（`text_splitter.py` `split_text`）。
- 按文件内容 **sha256 去重**（`file_hash` 唯一约束）：同内容已"就绪"→拒绝重复；同名不同内容→允许（`knowledge_import_service.py`）。
- chunk 写 MySQL（`knowledge_chunks`），向量写 Qdrant（`vector_service.py` `upsert_chunks`）。
- 文档列表 + 上传时间 + 状态（处理中/就绪/失败）+ chunk 数；删除同步清理 MySQL chunk 与 Qdrant 向量（`api/knowledge.py`）。
- 依赖含 PyMuPDF，README/文档声明"后续可扩展 PDF"，但 `parse_document` 对 `.pdf` 仍 `raise NotImplementedError`（`document_service.py` 第 30–31 行）。

### 2.4 RAG 检索与生成
- LLM 意图路由：`knowledge_qa` vs `general_chat`（`rag_service.py` `classify_intent`）。
- LLM 问题改写：原问题 + 多个改写 query（`rewrite_queries` + `_dedupe_queries`）。
- **Qdrant Hybrid 检索**：dense（语义）+ sparse（关键词）双向量（`vector_service.py` `search_chunks` 用 `FusionQuery(RRF)`）。
- 多 query 召回后**外层 RRF 融合**（`_rrf_fuse`）。
- **Rerank** 重排：百炼/通义 `gte-rerank-v2`（`rerank_service.py`）。
- **相关性过滤**：有 rerank 分数按阈值 `RAG_SCORE_THRESHOLD=0.5` 过滤；否则按业务意图关键词兜底过滤（`_filter_relevant_chunks`）。
- 命中块**前后相邻块扩展**（`services/chunk_context_service.py`，`RAG_ADJACENT_CHUNK_WINDOW`）。
- 回答末尾按文档聚合展示知识来源（`qa_prompt.py` QA_PROMPT 第 4–9 条要求，前端不再单列"引用文件"行）。

### 2.5 反馈与配额
- 点赞/踩 + 可选文字反馈，刷新后保留（`api/feedback.py`、`frontend/src/pages/Chat.tsx`）。
- 每日额度独立表 `user_daily_question_usages`，**不依赖聊天消息数**，删除会话也无法绕过（`api/chat.py` `_ensure_daily_question_quota` + 行锁 `with_for_update`）。

### 2.6 运维/初始化
- `BOOTSTRAP_ON_STARTUP=true` 时 FastAPI lifespan 自动建库建表并导入 `testdocs/`（`main.py` `lifespan` + `bootstrap_service.py`）。
- 可手动 `python testdocs/bootstrap.py` 或执行 `数据库初始化脚本/init.sql`。
- RAG 各阶段调试日志（recall/rrf/rerank/filtered/expanded）写入 `backend/logs/app.log`（`rag_service.py` `_log_rag_chunks`）。

### 2.7 预留/未完功能
- `GET /admin/stats` 仅返回空结构占位（`api/admin.py`），后台统计未实现。

---

## 3. 怎样做的（技术架构 / 关键代码 / 设计思路）

### 3.1 技术栈（源码与 requirements 实证）
**后端**
- Python + **FastAPI 0.115.6** + **SQLAlchemy 2.0.36** + **MySQL**(pymysql)
- **Qdrant 1.12.1**（向量库，dense+sparse hybrid）
- **LangChain 0.3.13 / LangGraph 0.2.60**（编排 + OpenAI 兼容 Chat/ Embeddings 客户端）
- **httpx 0.28.1**（Rerank 调用）
- JWT：`python-jose` + `passlib`；`PyMuPDF` 已装待扩展 PDF（`requirements.txt`）
- SSE：`fastapi.responses.StreamingResponse`

**前端**
- **React 19 + TypeScript 5.7 + Vite 6 + Ant Design 5.22**（`frontend/package.json`）

**AI 能力**
- LLM：OpenAI 兼容接口，默认通义千问 `qwen3.6-flash`（`LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1`）
- Embedding：OpenAI 兼容，默认 `text-embedding-v4`
- Rerank：百炼 `gte-rerank-v2`
- 向量库：Qdrant Cloud 或本地

### 3.2 整体架构思想
> "前端不直接调用 LLM，所有模型调用、向量检索和 Rerank 均在后端完成。"（docs/AI架构设计.md 第 5 行）——典型的"薄前端 + 厚后端"BFF 模式，密钥与编排逻辑不出后端。

### 3.3 RAG 主流程（LangGraph 状态机）
`services/rag_service.py` `build_rag_graph()` 把链路编排成 7 节点有向图：
```
validate_question → classify_intent → rewrite_queries → multi_retrieve → rrf_fusion → rerank_results → build_answer_messages → END
```
状态在 `RagState`(TypedDict) 中跨节点传递，每个节点只补充自己负责的字段。前端通过 `rag_answer_stream()` 逐步 `yield` SSE 进度/增量/来源事件。

### 3.4 关键技术决策与代码逻辑

**(a) Hybrid 向量 + 双层 RRF**
- `vector_service.py`：每个 chunk 同时写 `dense`（Embedding 语义向量）和 `sparse`（自研稀疏向量，`sparse_service.py` 用 **MD5(token) → 稀疏维度** 的 log 加权词频，无需维护词表；中文做单字+二元切分）。
- Qdrant 内部用 `FusionQuery(RRF)` 融合 dense/sparse prefetch；`rag_service.py` `_rrf_fuse` 再对各改写 query 的召回结果做**外层 RRF** 融合（公式 `1/(K+rank)`，`K=60`）。

**(b) 多 query 改写召回**
- `rewrite_queries` 调 LLM 把问题扩写成多个 query（默认 3，README 一处写 2 见下方一致性问题），每个 query 独立 hybrid 检索后外层融合，覆盖同义/上下文省略。

**(c) Rerank 非阻塞降级**
- `rerank_service.py`：未配置 key 或调用失败 → **直接沿用 RRF 排序**，不阻断回答。Rerank 分写入 `rerank_score`，下游过滤用。

**(d) 相关性过滤（防"退款问题引用马克思主义文档"）**
- `_filter_relevant_chunks`：优先用 rerank 分数 ≥ 0.5 取 top_k；无可靠分数时按**业务意图关键词**兜底过滤；完全不匹配则**清空引用**，不进 Prompt 也不展示（项目说明第 6 节明确点名该场景）。

**(e) Prompt 工程降幻觉**
- 5 套 Prompt（`qa_prompt.py`）：意图路由（`knowledge_qa/general_chat`）、问题改写（JSON 数组）、QA（严格基于知识、禁止编造、强制"知识来源："按文档聚合）、无知识兜底（禁止编造企业规则）、闲聊。

**(f) 每日配额隔离防绕过**
- 独立用量表 + `(user_id, usage_date)` 唯一约束 + `with_for_update()` 行锁，并发与删会话都不绕过（`api/chat.py`）。

**(g) 知识库去重与失败回滚**
- `import_knowledge_document`：sha256 唯一约束；"失败"文档重试复用原记录并先清残留向量；向量写入失败则回滚 chunk 入库并标记失败（`knowledge_import_service.py` 第 152–168 行）。

**(h) SSE 与流式保存的生命周期处理**
- `stream_chat` 在 `event_generator` 中先取标量 `session_id/user_id`，流式结束后用**新的 `SessionLocal()`** 写 AI 消息与 `message_sources`，规避请求级 db 依赖已关闭的问题（`api/chat.py` 第 207–283 行，注释明确说明）。

**(i) 错误脱敏**
- LLM/Embedding/Rerank 异常全部 `logger.exception` 写日志，前端只展示友好文案（如 `LLM_FAILED_MESSAGE`）；错误归一化给出可读中文（`embedding_service._normalize_embedding_error`）。

### 3.5 数据库设计（MySQL）
7 张表（ER 图见 docs/数据库设计.md）：`users / chat_sessions / chat_messages / knowledge_documents / knowledge_chunks / message_sources / feedbacks / user_daily_question_usages`。
要点：知识库**企业共享**（不按用户隔离），会话/反馈/每日额度**按用户隔离**；`knowledge_chunks.id` 复用为 Qdrant point id 便于按文档删向量；`message_sources` 保存文档名快照，文档删除后历史引用不空。

---

## 4. 结果是什么（效果 / 状态 / 社区 / 实测发现）

### 4.1 当前状态：**停更的个人项目，非活跃维护**
| 指标 | 实测值 | 来源 |
|---|---|---|
| 提交数 | **1**（单 commit，2026-06-15「修改配置项」） | `git rev-list --count` |
| 首次创建 | 2026-06-11 | `api.github.com created_at` |
| 最后推送 | 2026-06-15 08:10 | `pushed_at` |
| 最后更新 | 2026-06-25 | `updated_at`（应为 star/metadata 变动） |
| Releases/Tags | **无** | `git tag` 空 + releases API 返回 `[]` |
| 分支 | 仅 main | `git branch -a` |
| 贡献者 | **1 人**（Shanzha / 1241928215@qq.com） | `git shortlog` |
| 测试 | **无**（无 test 文件、无 .github/CI） | `find` 实测 |
| License | **无**（null） | `api.github.com license` |
| 语言 | Python（主） | GitHub linguist |

> 对比上一轮调研的 Customer-Agent：后者有 8 个测试文件、`.github/workflows` CI、v1.4.0 tag、763 star、单人但持续提交。AegisDesk AI 在工程纪律上明显弱一档。

### 4.2 社区反馈与影响力：**极小**
- **Star 3 / Fork 0 / Watchers 3 / Open Issues 0**（`api.github.com` 实时）。
- 无 issue、无 PR、无讨论区（has_discussions=false）。
- 结论：属于作者个人/课程用途的利基项目，外部影响力可忽略。

### 4.3 代码质量亮点（客观肯定）
1. **文档极度完整**：README(324 行) + 项目说明 + 运行指南 + 4 份 docs（AI 架构/API/数据库/业务流程），含 Mermaid 图与人工验证清单——对一个 3 star 项目而言异常用心。
2. **RAG 防幻觉设计务实**：rerank 降级、业务意图关键词兜底过滤、相邻块扩展、配额隔离，均针对真实故障场景（文档明确举例"退款误引马克思主义"）。
3. **失败/降级路径写得严谨**：知识导入回滚、向量清理、SSE 生命周期、行锁防超发。
4. **自我反思**：`项目说明.md` 第 6 节专写"使用 AI 编程工具的体会"，诚实记录哪些逻辑是 AI 生成后被人工修正的（多路召回、QA 切分、配额独立表等）。

### 4.4 实测发现的真实问题与风险（代码级）
1. **配置默认值三处不一致**
   - `rag_rewrite_query_count`：`config.py` 默认 **2**，`README` 与 `.env.example` 写 **3**。
   - `MYSQL_DB_NAME`：`.env.example` 写 `aegisdeskAI`（大写 I），`config.py` 默认 `aegisdeskai`、README 写 `aegisdeskai`——首次按 example 跑会"unknown database"。
   - Embedding 模型：`config.py` 默认 `text-embedding-3-small`，`.env.example` 用 `text-embedding-v4`。
2. **空壳管理接口**：`/admin/stats` 只返回 `{"daily_questions":[],"feedback":{"like":0,"dislike":0}}`，统计功能未实现（`api/admin.py`）。
3. **知识来源"双轨"脆弱**：模型被 Prompt 要求手动拼"知识来源："，同时后端另发 `source` 事件。两者都来自 RAG 过滤后的 chunk，但**模型可能漏写/改写格式**，前端只渲染模型正文、不渲染独立引用区——存在展示与数据不一致风险。
4. **密钥/安全默认值弱**：`config.py` `jwt_secret_key` 默认 `replace-this-in-production`；`.env.example` 虽给了 dev secret 但仍标注"change-me"。`QDRANT_URL` 在 `.env.example` 硬编码了一个真实 cloud 实例地址（api_key 为空，属占位但泄露了作者 endpoint）。
5. **无测试、无 CI、无 License**：不利协作与复用；许可证缺失使二次分发法律地位不明。
6. **PDF 未真正支持**：依赖装了 PyMuPDF，但 `parse_document` 对 `.pdf` 仍 `NotImplementedError`，与 README"后续可扩展"一致，属未交付项。
7. **单 commit 历史**：无法追溯演进，疑似整库一次性 force-push，不利于审计/贡献。

---

## 5. 证据索引（关键结论 → 文件定位）

| 结论 | 文件 |
|---|---|
| RAG LangGraph 7 节点编排 | `backend/app/services/rag_service.py`（`build_rag_graph`, `rag_answer_stream`） |
| Hybrid + 双层 RRF | `backend/app/services/vector_service.py`、`rag_service.py`(`_rrf_fuse`)、`sparse_service.py` |
| 相关性过滤（防误引） | `rag_service.py`(`_filter_relevant_chunks`) |
| QA 感知切分 | `backend/app/utils/text_splitter.py`(`split_text`) |
| 知识库去重/回滚 | `backend/app/services/knowledge_import_service.py` |
| 每日配额隔离+行锁 | `backend/app/api/chat.py`(`_ensure_daily_question_quota`) |
| SSE 流式保存生命周期 | `backend/app/api/chat.py`(`stream_chat`, `event_generator`) |
| 本地业务意图 | `backend/app/services/intent_service.py` |
| 5 套 Prompt | `backend/app/prompts/qa_prompt.py` |
| 前端 SSE 解析 | `frontend/src/api/client.ts`(`streamChat`) |
| 状态/社区数据 | `git` 元数据 + `api.github.com/repos/Itachi852/aegisdesk-ai` |
| 配置不一致 | `backend/app/core/config.py` vs `backend/.env.example` vs `README.md` |

---

## 6. 与同类项目对比（简要）
- 相较上一轮调研的 **Customer-Agent**（拼多多桌面客服、自研 Agent 内核、DPAPI 加密、Windows-only）：AegisDesk AI 走的是**更通用、更标准的 Web RAG 客服栈**（FastAPI+LangGraph+Qdrant+React），技术选型更"教科书"，但工程成熟度（测试/CI/版本/活跃度）更低，且无即时通讯平台集成、纯知识库问答。
- 两者共同点：**都强调防幻觉与来源可追溯**，且都**单人维护**——这类项目最大风险都是"作者停更 + 无测试"。
