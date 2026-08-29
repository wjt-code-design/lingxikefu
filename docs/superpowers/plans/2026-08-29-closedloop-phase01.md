# 闭环架构零期+一期实施计划（还债、度量、止血）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地架构方案 v2.1 的零期（还债+度量）与一期（止血）：语义缓存否定翻转防护、quick_answers 失效面接线、移交摘要持久化、降级话术阶梯、配额 DB 化。

**Architecture:** 六个独立任务（T1-T6），全部后端，T4 含 alembic 迁移。事实基础 = `.superpowers/sdd/plan-facts.md`（必读，含 10 条现场意外修正）。

**Tech Stack:** Python 3.11（backend/.venv）、pytest、SQLAlchemy/alembic、Redis、React（本计划不动前端）。

## Global Constraints

- 判定脚本 `backend/scripts/eval_faithfulness.py` 零改动（sha256 `43934ccf2026…` 冻结）；评测集零改动。
- **话术锚点约束**：新/改拒答文案必须至少含一个 REFUSE_MARKERS 子串（"未收录/转人工/没有找到/暂未…"，eval_faithfulness.py:40）——否则诚实性题假阳性。
- 单变量纪律：禁止同批改 prompt/判定脚本/检索参数；本计划不触 qa_prompt.py。
- 迁移纪律：alembic 新 revision 照 0009 可空加列惯例；最新 revision=0015，新链从 0016 起。
- 多助手协作：禁碰 `wt/`；直接 master 提交。
- 环境：解释器 `backend/.venv/Scripts/python.exe`；全量单测 env 前缀 `POSTGRES_HOST=localhost REDIS_URL=redis://localhost:6379/0 QDRANT_URL=http://localhost:6333 NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1`；基线 550 passed / 8 skipped / 0 failed；ruff 0 error（CI 用 ruff==0.16.4 扫 app tests alembic scripts，import 排序 I001 是历史翻车点）。
- 范围外（显式不做）：误转人工标注机制、topic 扩类（催单）、影子意图分类（二期）、temperature 调整（需四件套重冻）。

---

### Task 1: 语义缓存否定/条件翻转防护（当下即存的债）

**Files:**
- Modify: `backend/app/services/answer_cache.py`（_entities_ok 附近加防护函数 + get 语义命中链调用）
- Test: `backend/tests/test_answer_cache.py`（追加用例）

**Interfaces:**
- Produces: `_polarity_conflict(query: str, cached_question: str) -> bool`（True=翻转冲突不可命中）；get() 语义命中在 _entities_ok 之后追加 `and not _polarity_conflict(...)`。

- [ ] **Step 1: 红测**（追加到 test_answer_cache.py，手法沿用文件内 monkeypatch 假对象）

```python
def test_semantic_hit_blocked_on_polarity_conflict(monkeypatch):
    """否定/条件翻转防护：'能退'与'不能退'语义高相似但极性相反，不得互相命中。"""
    # 沿用文件内 _FakeQdrant/_FakeRedis/假 embed 手法：
    # put("商品能退货吗", ...) 后 get("商品不能退货吗") 必须返回 None（余弦必然≥0.85 因仅一词之差）
```

（断言两问句 embed 相似但极性词不同 → get 返回 None；对照用例：极性一致的改写"可以退吗"仍命中。）

- [ ] **Step 2: 跑红** — `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_answer_cache.py -q --no-cov` → 新用例 FAIL（现返回命中）。
- [ ] **Step 3: 实现**（answer_cache.py，极性词表模块级常量）

```python
#: 否定/条件极性防护（2026-08-29 审核发现）：语义相似但极性相反的问句（能退/不能退、
#: 7天内/超过7天）不得互相命中——0.85 余弦挡不住一词之差的翻转，实体锁定在无数体句时放行。
_POLARITY_TERMS = ("不能", "无法", "不可", "不支持", "不提供", "超过", "以外", "之后", "非")

def _polarity_conflict(query: str, cached_question: str) -> bool:
    a = {t for t in _POLARITY_TERMS if t in query}
    b = {t for t in _POLARITY_TERMS if t in cached_question}
    return a != b
```

get() 语义命中处（`_entities_ok(...)` 判定点，answer_cache.py:105 附近）追加 `and not _polarity_conflict(query, hit_payload_question)`（payload 里的原始问句字段名以现实现为准，plan-facts A1）。

- [ ] **Step 4: 绿 + 全量** — 该文件全绿；全量 550+新增 / 0 failed；ruff 0 error。
- [ ] **Step 5: 提交** — `fix(cache): 语义缓存否定/条件极性防护——一词之差的翻转问句不得互相命中（架构审核债 5-1）`

---

### Task 2: quick_answers 失效面接线（kb_version 漂移防护）

**Files:**
- Modify: `backend/app/services/quick_answers.py`（check_kb_coverage 返回 bool + 模块级「最近通过版本」状态）
- Modify: `backend/app/api/chat.py:359` 附近（短路前校验 coverage 通过版本）
- Test: `backend/tests/test_quick_answers.py`（若无则新建）

**Interfaces:**
- Produces: `check_kb_coverage(blob: str) -> bool`（现返回 None 则改 bool）；`is_enabled_for(kb_version: str | None) -> bool`（最近一次 coverage 通过的 kb_version == 当前则 True，从未通过则 True 保持向后兼容）。

- [ ] **Step 1: 红测**

```python
def test_quick_disabled_after_kb_change_without_coverage():
    """KB 版本变化且新 KB 未通过覆盖检查 → quick 话术禁用（走 RAG），防陈旧答案。"""
    quick_answers._COVERED_KB_VERSION = "5:2026-01-01"   # 模拟上次通过
    assert quick_answers.is_enabled_for("6:2026-02-01") is False
    assert quick_answers.is_enabled_for("5:2026-01-01") is True
    assert quick_answers.is_enabled_for(None) is True    # 无版本环境向后兼容

def test_quick_reenabled_after_coverage_pass():
    quick_answers._COVERED_KB_VERSION = None
    assert quick_answers.check_kb_coverage("怎么开发票 保修多久") is True  # 命中话术关键词
    assert quick_answers.is_enabled_for("9:2026-03-01") is True
```

- [ ] **Step 2: 跑红** → FAIL（现无 is_enabled_for / check 返回 None）。
- [ ] **Step 3: 实现**：quick_answers.py 加 `_COVERED_KB_VERSION: str | None = None`；check_kb_coverage 现逻辑末尾按命中数置 `_COVERED_KB_VERSION`（需把 kb_version 传进来——knowledge_import_service.py:160 调用点补传当前版本，函数签名加 `kb_version: str | None = None`）；新增 `is_enabled_for`。chat.py:359 短路前：`if quick_ans and quick_answers.is_enabled_for(kb_version):`（不满足则不短路自然落 RAG，warning 日志一次）。
- [ ] **Step 4: 绿 + 全量 + ruff**；确认 knowledge_import 现有测试不受影响。
- [ ] **Step 5: 提交** — `fix(quick): 快捷话术纳入 kb_version 失效面——KB 变更未过覆盖检查即禁用走 RAG（架构审核债 5-2）`

---

### Task 3: 移交摘要持久化 + 工单流转时间戳

**Files:**
- Create: `backend/alembic/versions/0016_ticket_summary_and_timestamps.py`
- Modify: `backend/app/models/ticket.py`（+summary text 可空、+processing_at/resolved_at 可空 timezone 时间戳）
- Modify: `backend/app/services/ticket_service.py:20-49`（ensure_active_ticket 加 `summary: str | None = None` 参数，构造注入）
- Modify: `backend/app/services/shared_context.py`（+handoff_summary: str | None = None 字段）
- Modify: `backend/app/api/chat.py`（组装 ctx 处填 `handoff_summary=build_handoff_summary(history, conv_state)`，conv_state 从 `s.conv_state` 读；import session_context）
- Modify: `backend/app/services/agents/ticket_agent.py:57`（ensure_active_ticket(..., summary=ctx.handoff_summary)）
- Modify: `backend/app/services/ticket_state_machine.py:91-99`（CAS update 补 `processing_at=`/`resolved_at=` 按目标状态）
- Test: `backend/tests/test_ticket_handoff_summary.py`（新建）

**Interfaces:**
- Consumes: `build_handoff_summary(history, conv_state)`（session_context.py:77-107，已存在）。
- Produces: `tickets.summary`（坐席首屏摘要数据源）；`ensure_active_ticket(db, session_id, message_id=None, source="ai", notify=True, summary=None)`。

- [ ] **Step 1: 红测**

```python
def test_ticket_persists_handoff_summary(db_with_pg):  # 手法沿用既有工单测试的 DB fixture
    from app.services.session_context import build_handoff_summary
    summary = build_handoff_summary([], {"topic": "退款", "slots": {"order_no": "SO123"}, "clarify_count": 1, "stage": "resolving"})
    t = ensure_active_ticket(db, session_id, message_id=None, summary=summary)
    assert t.summary and "退款" in t.summary and "SO123" in t.summary

def test_state_transition_timestamps(db_with_pg):
    t = ensure_active_ticket(db, session_id)
    ticket_state_machine.transition(db, t, "processing")   # 以现状态机真实签名为准
    assert t.processing_at is not None
```

- [ ] **Step 2: 跑红** → FAIL（无 summary 字段/参数）。
- [ ] **Step 3: 迁移**（0016，照 0009 惯例，down_revision="0015"）

```python
def upgrade():
    op.add_column("tickets", sa.Column("summary", sa.Text(), nullable=True))
    op.add_column("tickets", sa.Column("processing_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tickets", sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True))
def downgrade():
    op.drop_column("tickets", "resolved_at")
    op.drop_column("tickets", "processing_at")
    op.drop_column("tickets", "summary")
```

本地验证迁移：`alembic upgrade head` → `alembic downgrade -1` → `upgrade head`（对称性，CI Migration job 同款）。

- [ ] **Step 4: 实现**模型/服务/ctx 注入（D 组事实的注入点）；TicketAgent 路径带摘要，manual 路径（tickets.py:277）不传保持现状。
- [ ] **Step 5: 绿 + 全量 + ruff + 迁移对称**；提交 — `feat(tickets): 移交摘要持久化 + 流转时间戳——AI handoff 打包槽位/主题/澄清状态入工单（架构一期 4）`

---

### Task 4: 降级话术阶梯（区分故障与不知道）

**Files:**
- Modify: `backend/app/services/rag_service.py:141-151`（except 拆分 RetrievalError vs PipelineTimeoutError；RagResult 加 `degraded_kind: str = ""` 字段）
- Modify: `backend/app/services/rag_service.py:276-284`（_no_llm_reply 按 degraded_kind 分档）
- Test: `backend/tests/test_rag.py` 或新建 `test_degraded_reply.py`

**Interfaces:**
- Produces: `RagResult.degraded_kind: str`（""=非降级 | "retrieval" | "timeout"）；三档话术（每档含"转人工"锚点）。

- [ ] **Step 1: 红测**

```python
def test_timeout_vs_retrieval_distinct_replies():
    """超时=容量话术（稍后重试），检索失败=故障话术（稍后重试+转人工）；都须含评测锚点'转人工'。"""
    r_timeout = rag_service._no_llm_reply(RagResult(intent="qa", refuse=True, refuse_reason="t", retrieve_degraded=True, degraded_kind="timeout"))
    r_retrieval = rag_service._no_llm_reply(RagResult(intent="qa", refuse=True, refuse_reason="r", retrieve_degraded=True, degraded_kind="retrieval"))
    assert "转人工" in r_timeout and ("稍后" in r_timeout or "繁忙" in r_timeout)
    assert "转人工" in r_retrieval
    assert r_timeout != r_retrieval
```

- [ ] **Step 2: 跑红** → FAIL（degraded_kind 不存在，两档同文）。
- [ ] **Step 3: 实现**：RagResult 加字段；except 元组拆两个 except 分支分别置 degraded_kind（保留 retrieve_degraded=True 语义与澄清跳过逻辑不变）；_no_llm_reply 按 degraded_kind 先判再落原 handoff/chitchat/兜底分支。话术文案（均含"转人工"锚点）：
  - retrieval：沿用现"知识库检索服务暂时不可用，请稍后重试；如急需处理，可转人工客服帮您解决。"
  - timeout："当前咨询量较大，回复出现延迟。您可以稍后再试，或转人工客服立即处理。"
- [ ] **Step 4: 绿 + 全量 + ruff**（全量确认诚实性题不受影响——话术含锚点）。
- [ ] **Step 5: 提交** — `feat(rag): 降级话术阶梯——区分系统故障与容量超时，各有独立文案且保评测锚点（架构一期 5）`

---

### Task 5: 配额 DB 化（设置写通道 + 动态上限）

**Files:**
- Create: `backend/alembic/versions/0017_app_settings_kv.py`（表 `app_settings`: key String(64) PK, value JSONB, updated_at）
- Create: `backend/app/models/app_setting.py`（AppSetting 模型）
- Modify: `backend/app/services/quota.py`（daily_limit() 先查 KV，60s 进程内 TTL 缓存，回退 settings）
- Modify: `backend/app/api/admin_settings.py`（+PUT /settings/quota，require_admin，写 KV+清缓存；GET 的 quota 组读生效值）
- Modify: `backend/app/schemas/admin_settings.py`（如需 PUT body schema）
- Test: `backend/tests/test_quota_settings.py`（新建）

**Interfaces:**
- Produces: `QuotaService.daily_limit()`（KV 覆盖优先）；`PUT /api/v1/admin/settings/quota {"daily_quota_limit": 500}`。

- [ ] **Step 1: 红测**（TestClient + admin JWT，手法照 test_admin_settings.py:18-25）

```python
def test_put_quota_updates_daily_limit(test_admin_client):
    r = test_admin_client.put("/api/v1/admin/settings/quota", json={"daily_quota_limit": 500})
    assert r.status_code == 200
    assert get_quota_service().daily_limit() == 500      # KV 覆盖生效
    assert test_admin_client.get("/api/v1/admin/settings").json()["quota"]["daily_quota_limit"] == 500

def test_daily_limit_falls_back_to_settings(test_admin_client):
    assert get_quota_service().daily_limit() == settings.DAILY_QUOTA_LIMIT  # 未设置时回退
```

- [ ] **Step 2: 跑红** → FAIL（404 无 PUT 路由）。
- [ ] **Step 3: 迁移 0017 + 模型 + 实现**（daily_limit 的 60s TTL 缓存进程内即可，PUT 后主动失效；admin_settings.py:43 直读 settings 的漂移点一并改为读生效值）。
- [ ] **Step 4: 绿 + 全量 + ruff + 迁移对称**（注意 test_admin_settings.py 既有断言若因 GET 语义变化需同步微调，属预期）。
- [ ] **Step 5: 提交** — `feat(quota): 配额上限 DB 化——app_settings KV + admin 写通道 + 60s 生效缓存，大促可动态上调（架构一期 6）`

---

### Task 6: 批次收尾（控制器执行）

- [ ] 全量单测 + ruff + 双迁移对称（0016/0017 up-down-up）
- [ ] 推送 → CI 绿确认（unit/migration job 重点）
- [ ] BASELINE/方案文档回执（零期+一期完成状态）
- [ ] 全分支终审（subagent-driven 终审环节）

---

## Self-Review（已自查）

1. **事实对齐**：全部任务基于 plan-facts.md 的 file:line 现场事实；10 条意外修正已吸收（如 T3 缩为时间戳并入 T4、T6 复用判断被否——quotas 表是 per-user 用量表不适合全局设置，改新建 app_settings KV）。
2. **占位符**：无 TBD；Step 内代码为关键实现+精确指令混合（沿用 followup 计划模式，执行效果已验证）。
3. **锚点约束贯穿**：T4 话术三档全部含"转人工"；T2 不动话术本体。
4. **范围**：零期+一期共 5 个实现任务；指标采集的"误转人工标注"等重机制显式范围外。
