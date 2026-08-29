# 闭环二期实施计划（L2 补全 + 意图分类影子）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** 落地架构方案 v2.1 二期——低风险 handoff 的 AI 预起草（坐席打开即见草稿）、坐席"填入"发送通道的角色修正、LLM 意图分类影子模式（只记不驱动）。

**Architecture:** 三个实现任务 + 收尾。事实基础 = `.superpowers/sdd/plan-facts-p2.md`（必读）。**关键设计修正**（相对方案 v2.1 原文）：不采用"低风险 handoff 不建单走起草流"——查证证实不建单会脱离工单队列/SLA/created 通知（plan-facts-p2 C 组），改为"**仍建单 + 建单时 AI 预起草自动挂单**"：用户侧流程零变化，坐席效率提升，M7 风险自然消解。

**Tech Stack:** 同 phase01。基线 579 passed / 8 skipped / 0 failed（环境 skip 构成见 progress.md）。

## Global Constraints

- 判定脚本/评测集/qa_prompt 零改动；话术新文案须含 REFUSE_MARKERS 之一（"转人工"）。
- **显式转人工不可改道**（plan-facts-p2 意外①）：`_RE_ARTIFICIAL`（rag_service.py:74「人工(?!智能)」）命中的 handoff 是用户明确意图，永不走预起草改道。
- 禁碰 `wt/`；直接 master 提交；venv 与 env 前缀同 phase01；ruff 0.16.4（I001 必查）。
- 影子分类硬约束：**不得驱动路由、不得阻塞响应、失败只 log**（fail-open）；成本设采样上限。
- 范围外：意图分类切换驱动路由（影子一致率达标后另批）、多渠道、催单 topic 扩类。

---

### Task 1: 低风险 handoff 判别 + 建单 AI 预起草

**Files:**
- Modify: `backend/app/services/session_context.py`（+`classify_handoff_risk(...) -> "low" | "high"`：低=非显式转人工（_RE_ARTIFICIAL/HANDOFF_KEYWORDS 不含"转人工/人工客服"类显式词）且 conv_state.topic ∈ FLOW_TOPICS 有值；高=其余（情绪词/投诉/显式人工））
- Modify: `backend/app/services/shared_context.py`（+handoff_risk: str = ""）
- Modify: `backend/app/api/chat.py`（组装 ctx 处填 handoff_risk）
- Modify: `backend/app/services/agents/ticket_agent.py` 或 chat.py 建单后路径（low risk 时异步起草：复用 suggest 端点的内部逻辑——**抽出 suggest 的核心为可复用函数**（sessions.py:457-538 的检索+assist prompt 部分，移到 service 层），建单后 `run_in_threadpool` 起草并写 `Ticket.draft_suggestion`（新列，migration 0018 加可空 Text）+ `draft_kind="ai"`）
- Modify: `backend/app/models/ticket.py` + `alembic/versions/0018_ticket_draft_suggestion.py`（可空 Text 列，照 0009 惯例）
- Modify: `backend/app/api/tickets.py` `_item()`/TicketItem schema（**下发 draft_suggestion——补 T3 遗留的 summary 下发一并做**）
- Test: `backend/tests/test_handoff_draft.py`（新建）

**Interfaces:**
- Produces: `classify_handoff_risk(query, conv_state) -> str`；`Ticket.draft_suggestion: str | None`；TicketItem 下发 `summary`/`draft_suggestion`/`processing_at`/`resolved_at`。

- [ ] **Step 1: 红测**（判别函数：显式人工/投诉→high；topic+知识型问句→low；无 topic→high。建单预填：low risk handoff 建单后 draft_suggestion 非空、high risk 为空。下发自一生效。）
- [ ] **Step 2: 跑红** → **Step 3: 实现**（suggest 核心抽 service 层时保持 HTTP 端点行为不变——现测试锁定）→ **Step 4: 绿 + 全量 + ruff + 迁移对称** → **Step 5: 提交** `feat(l2): 低风险 handoff 建单 AI 预起草——draft_suggestion 挂单 + 风险判别 + TicketItem 下发补全（架构二期 1）`

**注意**：起草失败 fail-open（draft_suggestion 留空，不影响建单）；异步起草不得阻塞 SSE 响应（建单本身已在响应流内——起草放建单后同步 run_in_threadpool 会加延迟，**改为 fire-and-forget 线程 + 异常 log**，参考 M4 教训保存引用或用 daemon 线程池，实现者按现状选择并报告理由）。

---

### Task 2: 坐席"填入"发送通道角色修正

**Files:**
- Verify/Modify: `frontend/src/components/chat/ChatContainer.tsx:440-472`（填入后的发送路径：坐席会话视图应走 sendAgentMessage 而非顾客发送路径）
- 可能 Modify: `frontend/src/api/sessions.ts`（若需补参数）
- Test: 前端 vitest（ChatContainer 既有测试文件手法）

**Interfaces:**
- 行为：坐席角色打开会话 → 建议卡"填入"→ 编辑 → 发送落库 role='agent'（顾客端轮询可见）；顾客角色路径完全不变。

- [ ] **Step 1: 现状核实**（实现者先读 ChatContainer 的角色分支：sendAgentMessage 在 :423 何条件下调用、"填入"填充的目标 ref 是哪个发送函数；输出核实结论再动刀——若现状其实已按角色分流则本任务缩为补测试）
- [ ] **Step 2-4: 红测（若缺陷实锤）→ 修 → vitest 绿 + tsc 过**
- [ ] **Step 5: 提交** `fix(frontend): 坐席"填入"建议发送走 agent 通道——角色分支修正（架构二期 2）`

---

### Task 3: LLM 意图分类影子模式

**Files:**
- Create: `backend/app/services/intent_shadow.py`（`shadow_classify(query: str) -> None`：调 get_chat_client().complete 输出 JSON {intent}，落 Message.meta["intent_shadow"]，全 fail-open）
- Modify: `backend/app/api/chat.py`（intent 事件回写处（:434-436 附近）fire-and-forget 调影子，**采样开关** `INTENT_SHADOW_SAMPLE: float = 0.2`（config.py 加字段）+ 显式 bypass：handoff/chitchat 类不跑（只影子 qa 类——改道决策只关心 qa 侧误判））
- Modify: `backend/app/api/admin.py` 或新端点（影子一致率统计：meta.intent_shadow.llm_intent vs user_msg.intent 的聚合，require_admin）
- Test: `backend/tests/test_intent_shadow.py`

**Interfaces:**
- Produces: Message.meta["intent_shadow"] = {"intent": str, "latency_ms": int}；GET /admin/intent-shadow/stats → {total, agree, agree_rate, by_intent}。

- [ ] **Step 1: 红测**（影子结果落 meta 且不改变路由/响应；LLM 失败时 meta 无键且无异常外泄；采样=0 时不调用；统计端点聚合正确）
- [ ] **Step 2: 跑红** → **Step 3: 实现**（complete 调用包 try/except 全捕获 + 超时用 client 现有配置；fire-and-forget 的并发安全：无共享可变状态即天然安全，落库复用请求级 db 会话**不可行**（响应结束即关）——用独立短会话 SessionLocal()，照 T5 的 session_factory 注入先例）
- [ ] **Step 4: 绿 + 全量 + ruff** → **Step 5: 提交** `feat(intent): LLM 意图分类影子模式——采样落 meta 不驱动路由 + admin 一致率统计（架构二期 3）`

---

### Task 4: 批次收尾（控制器执行）

- [ ] 全量单测 + ruff + 0018 迁移对称
- [ ] 全量评测（二期不触评测路径的论证：路由改道只加预起草不改判定/话术；影子不驱动路由——但仍实测存档，show-your-work 纪律）
- [ ] 推送 + CI 绿 + 方案文档二期回执 + progress 记账
- [ ] 全分支终审（本批次范围）

---

## Self-Review（已自查）

1. **事实对齐**：全部基于 plan-facts-p2；三意外吸收（显式人工白名单→T1 判别约束；填入通道→T2；幂等首建为准→T1 预起草写新列不碰 summary 幂等语义）。
2. **设计修正有据**：不建单方案被 C 组证据否决（脱离队列/SLA/通知），修正为建单+预起草，用户流程零变化。
3. **占位符**：T2 Step 1 是核实步骤（现状可能已正确），非占位符。
4. **依赖**：T1/T2/T3 相互独立可并行；T2 依赖 T1 的下发仅在前端展示层（非阻塞）。
