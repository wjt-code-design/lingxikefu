# 闭环三期实施计划（知识闭环：信号聚类 + 发布门禁 v1）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** 落地架构方案 v2.1 三期——信号聚类升级（时间窗+点踩源）、quick covered 版本持久化（清 Celery 债）、KB 发布门禁 v1（版本绑定评测 + 门禁状态可见）。

**Architecture:** 三个实现任务 + 收尾。事实基础 = `.superpowers/sdd/plan-facts-p3.md`（必读）。**范围声明**：发布门禁 v1 = 观测与记录（当前 KB 版本的评测通过状态可见、API 可查），**不含**强制阻断导入与自动回滚（红队 M6 指出需 staged 通道设计，v2 另批）。

**Tech Stack:** 同前批。基线 614 passed / 8 skipped / 0 failed。

## Global Constraints

- 判定脚本/评测集/qa_prompt 零改动；**契约检查必跑**（generate_openapi + check_contracts——上批 CI #57 教训，凡动 schema 的任务收尾必跑）。
- hot_gaps 口径沿用 admin.py:154-192 注释（handoff 排除、澄清不计入、NFKC 归一）——升级不得改变既有数字口径。
- Redis 操作沿用 answer_cache 精确层先例（get_redis 单例 + fail-open 全捕获）。
- 禁碰 `wt/`；直接 master 提交；venv/env 前缀同前批；ruff 0.16.4（I001）。

---

### Task 1: 信号聚类升级（时间窗 + 点踩源并入）

**Files:**
- Modify: `backend/app/api/admin.py`（hot_gaps 查询 + 时间窗过滤；新增点踩源查询并入响应）
- Modify: `backend/app/schemas/admin.py`（响应 schema 加字段——**收尾必跑契约检查**）
- Test: `backend/tests/test_admin_stats.py`（既有手法追加）

**Interfaces:**
- Produces: GET /admin/stats 的 hot_gaps 支持 `?days=7` 时间窗（默认 7，0=不限保持旧口径）；新增 `feedback_gaps`（down 反馈聚类 Top10：问题原文/次数/最近时间）。

- [ ] **Step 1: 红测**（时间窗过滤：N 天外的 refuse 不计入；feedback_gaps 聚合正确；days=0 口径与旧版一致）
- [ ] **Step 2: 跑红** → **Step 3: 实现** → **Step 4: 绿 + 全量 + ruff + generate_openapi + check_contracts** → **Step 5: 提交** `feat(admin): 信号聚类升级——hot_gaps 时间窗 + 点踩源 feedback_gaps（架构三期 1）`

---

### Task 2: quick covered 版本持久化（清 Celery 债）

**Files:**
- Modify: `backend/app/services/quick_answers.py`（check_kb_coverage 通过时写 Redis `quick:covered_kb_version`（无 TTL，fail-open）；is_enabled_for 读序：Redis → 模块级回退 → None 恒 True）
- Modify: `backend/app/api/chat.py`（无改动——is_enabled_for 签名不变）
- Test: `backend/tests/test_quick_answers.py`（追加：Redis 写读/Redis 挂回退模块态/无 Redis 无模块态恒 True）

**Interfaces:**
- 行为：Celery worker 进程写入 covered 版本后，API/chat 进程经 Redis 可见——跨进程门控生效。

- [ ] **Step 1: 红测** → **Step 2: 跑红** → **Step 3: 实现**（Redis 不可用时 log 一次并回退模块级行为，warning 去重沿用 _WARNED_STALE_VERSION）→ **Step 4: 绿 + 全量 + ruff** → **Step 5: 提交** `fix(quick): covered 版本持久化 Redis——跨进程门控生效，清 Celery 导入路径债（架构三期 2）`

---

### Task 3: KB 发布门禁 v1（版本绑定评测 + 状态可见）

**Files:**
- Create: `backend/alembic/versions/0019_evalresult_kb_version.py`（eval_results 加 `kb_version` 可空 String(255)——照 0009 惯例）
- Modify: `backend/app/models/eval_result.py`（+kb_version 列）
- Modify: 评测触发链（admin.py EvalTrigger 的 run 路径 → run_faithfulness_eval 完成时把当前 `_kb_version_str` 写入 EvalResult；**CLI 路径 eval_faithfulness.py 不动**——判定脚本零改动约束只锁脚本本体，触发链在 admin 侧改）
  - ⚠️ 边界澄清：run_faithfulness_eval 若在 eval.py/独立 service 模块则改该处；`_kb_version_str` 在 chat.py:98-116——抽出为共享 helper（kb_lookup 或新 util）供两处复用
- Modify: `backend/app/api/admin.py`（+GET /admin/eval/gate：当前 kb_version + 该版本最新评测结果 + pass_all 布尔——"当前版本是否评测通过"一屏可见）
- Test: `backend/tests/test_eval_gate.py`（新建）

**Interfaces:**
- Produces: EvalResult.kb_version；GET /admin/eval/gate → {"kb_version", "last_eval": {...} | None, "passed": bool | None}（None=当前版本从未评测）。

- [ ] **Step 1: 红测**（触发评测后 EvalResult.kb_version 非空且等于当前值；gate 端点三态：通过/未通过/从未评测；无评测时 passed=None 不误报）
- [ ] **Step 2: 跑红** → **Step 3: 实现**（migration 0019 + 触发链写版本 + gate 端点；`_kb_version_str` 抽共享时保持 chat.py 行为零变化——现测试锁定）→ **Step 4: 绿 + 全量 + ruff + 迁移对称 + generate_openapi + check_contracts** → **Step 5: 提交** `feat(eval): KB 发布门禁 v1——EvalResult 绑定 kb_version + gate 状态端点（架构三期 3；强制阻断留 v2）`

---

### Task 4: 批次收尾（控制器执行）

- [ ] 全量单测 + ruff + 0019 迁移对称 + 契约检查（必跑）
- [ ] 全量评测实测存档（本批触评测触发链——按 show-your-work 纪律跑全量而非论证）
- [ ] 推送 + CI 绿 + 方案文档三期回执 + progress 记账（含遗留小修：_ORDER_RE Unicode \b、engine connect_timeout——若预算允许顺手，否则续挂）

---

## Self-Review（已自查）

1. **事实对齐**：评测触发/热水印/hot_gaps 口径/kb_version 派生指纹四发现已吸收；门禁 v1 边界（观测非阻断）显式声明。
2. **判定脚本零改动**：T3 改的是 admin 触发链与模型，eval_faithfulness.py CLI 本体不动。
3. **依赖**：T1/T2/T3 独立；T3 的契约检查为批次级必跑项（T1 也动 schema）。
