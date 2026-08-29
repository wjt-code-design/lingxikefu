# 质量攻坚总方案（外部审查修复 + 挂账清账）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 2026-08-28 冻结的 RAG 评测基线之上，整合外部代码审查（code-review-2026-08-28-FINAL.md，15 条发现）与本轮 followup 执行积累的挂账项，形成一批次化、按验证结论分级的修复执行方案；修复全程不得破坏已冻结的评测门禁。

**Architecture:** 三批次推进——批次 E 收尾在途的 followup（三次冻结 + 全分支终审，已近完成）；批次 F 外部审查修复（严格按交叉验证结论分级：P0 用户资产类立即修、评测耦合类独立窗口修、设计争议类提请决策）；范围外项显式声明含触发条件。所有修复走 TDD + 针对性测试 + 修复后全量单测。

**Tech Stack:** Python 3.11（backend/.venv）、pytest、FastAPI、Redis、GitHub Actions CI（full_eval 门禁）。

## Global Constraints

- **评测门禁红线**：qa / refuse / citation 三项以 CI full_eval 权威数字为下限（最近冻结：qa 90.2% / refuse 100%(7题) / citation 99.5% @ run 33176656355；批次 E 三次冻结后将更新为 refuse 8 题口径）。任何修复若涉及 `rag_service.py`（intent 路由 / prompt / 检索路径）、评测集、判定脚本，**必须**走独立批次 + 本地全量回归 + CI full_eval 复核，禁止与纯后端修复混批次（单变量纪律）。
- 判定脚本 `backend/scripts/eval_faithfulness.py` sha256 `43934ccf2026…` 冻结；评测集 hash 见 `BASELINE.sha256`（2026-08-28 二期修订后）。
- 多助手协作：`wt/backend`、`wt/frontend` 两个 worktree 及其分支（build/backend、build/frontend）为另一助手工作区，禁碰。
- 外部审查报告的"期望"部分来自业务常识而非项目设计意图，**每条修复前必须先核对既有测试与评测集锁定的行为**（交叉验证报告：`.superpowers/sdd/external-review-verify.md`）。
- 本地评测环境前置：评测 KB 13 docs / 26 chunks（`smoke_import --strict` 核对）；env 覆盖 `POSTGRES_HOST=localhost REDIS_URL=redis://localhost:6379/0 QDRANT_URL=http://localhost:6333`；解释器 `backend/.venv/Scripts/python.exe`。
- 全量单测基线：523 passed / 8 skipped（批次 E Task 2 后）；每批次修复后不得出现回归。

---

## 一、现状问题全景（输入源）

### 1.1 已完成与在途（批次 E，docs/superpowers/plans/2026-08-28-rag-quality-followup.md）

| Task | 内容 | 状态 |
|---|---|---|
| 1 | smoke_import 文档清单审计（`--strict`，CI 启用） | ✅ 565a304，审查 Approved |
| 2 | seed_demo_data 收权（无参只选 demo 库） | ✅ 2497cf5，审查 Approved |
| 3 | Q069 判据回正（诚实性 7→8 题，hash 重冻结） | ✅ 4468204，审查 Approved；本地全量 qa 95.1% / refuse 8/8 / citation 97.9% |
| 4 | CI full_eval 复核（run 33187434752）+ 三次冻结 | 🔄 CI 跑评中 |

### 1.2 本轮执行积累的 Minor 挂账（终审 triage）

1. Task 1：`smoke_import` 的 main() 审计接线无自动化回归测试（仅纯函数 3 测 + 一次性 E2E）；全量测试重定向无汇总行（Windows 已知怪象）；ci.yml 注释与 docstring 背景重复。
2. Task 2：`latest_kb` 查询移除了 `tenant_id` 过滤（plan-mandated，跨租户选择风险，单租户下等价）；测试 `db.close()` 无 try/finally。
3. Task 3：BASELINE.md §四"Q069 判据挂账"索引行过时（Task 4 冻结时顺手改）；q069-verify.json CRLF warning（cosmetic）。
4. 更早批次（progress.md 旧账）：缓存无上限、tickets/my 路由消失未点名、位置性断言等——见 `.superpowers/sdd/progress.md`。

### 1.3 体系缺口挂账（BASELINE §五，不修清单见 §四）

- 断言/引用交集阈值 band 校准（0.30→0.25）：门禁绿不松尺。
- run_eval 存档附 dense_score 列：待触碰 eval 脚本时捎带。
- Q069 判据：**已结案**（4468204）。

### 1.4 外部审查 15 条（处置表见批次 F）

外部报告分级：严重 2（S1 配额泄漏 🟢 / S2 情绪词误伤 🟢）、中等 7（M1-M7 🔵）、轻微 6（L1-L6，L5 🟢）。外部审查自认限制：未跑项目测试套件、M2/L6 为理论推演、**未审 scripts/ 与 eval-and-samples/**（该区域已被本团队三轮深翻，含 4468204 刚修复的 hash 失配缺陷）。交叉验证代理已对 S1/M6/S2/M1 做运行级核实（`.superpowers/sdd/external-review-verify.md`），处置严格按核实结论。

---

## 二、批次 E：followup 收尾（进行中，本文档交付时点应已完成）

- [ ] **E-1**：CI #34（run 33187434752）出结论。PASS → 按数字做三次冻结；FAIL → 按 2026-08-28 同款流程（拉日志→归因→单变量修复→重跑）。
- [ ] **E-2**：BASELINE.md 三次冻结（§四追加：refuse 8 题口径数字 + run id）+ §四"Q069 判据挂账"索引行更新为"已回正（4468204）"。
- [ ] **E-3**：handoff r2 §八追加 followup 执行回执（三防线 + 判据回正 + CI run id）。
- [ ] **E-4**：提交推送；触发 push CI（抽样模式）确认绿。
- [ ] **E-5**：全分支终审（subagent-driven-development 终审环节）：review-package `<followup 起点>..HEAD` + 派终审代理，triage §1.2 全部 Minor 挂账（修/留各给理由）；外部审查验证报告作为终审输入之一。

**验收**：CI 全绿 + BASELINE 三次冻结落档 + 终审无 Critical/Important 未决。

---

## 三、批次 F：外部审查修复（按验证结论分级执行）

> 处置表基于交叉验证报告（external-review-verify.md）的核实结论。每项修复走 TDD：先写失败测试（锁行为）→ 最小实现 → 针对性测试 → 全量单测 → 独立 commit。

### F-1 纯后端修复组（不动评测路径，可一批执行）

处置依据：交叉验证报告 `.superpowers/sdd/external-review-verify.md`（运行级核实）。

| 编号 | 外部声称 | 验证结论 | 处置 |
|---|---|---|---|
| S1 | SSE error 事件不退配额 | **属实**（chat.py:535 只 yield；扣费 :237 在 gen 前，两个 error 源 :190/:259 均在扣费后→无多退风险；refund 签名匹配 quota.py:96 且 marker 防双退） | **立即修**：`consumed` 标记 + finally 统一退款（gen 已有 finally :541），附回归测试锁「error 事件 → 额度复原」 |
| M1 | kb 缓存无租户维度 | **部分属实**（docstring"按租户分桶"是 6381cd5 漏改残留；tenant_middleware 恒 DEFAULT，无现实泄漏路径；重新分桶违背该提交"不采信动态租户"裁定） | **只改文档**：kb_lookup.py docstring 改为"单租户期单条目，多租户化前须按 tenant 分桶"；顺带修 main.py:168 过期注释。不改实现（YAGNI） |
| M6 | 删除会话审计在 commit 后 | **事实对但外部修法危险**（audit_service.py:56 内部自带 commit+失败 rollback，挪到 commit 前会出现"审计失败→删除连滚→接口仍 200"的反向失真；fail-open 是 Phase 4 显式设计，全仓 8 调用点统一） | **不修**，登记为已知限制（见 §四）；如需强化，独立设计"审计失败告警"而非调换顺序 |
| L5 | 本地 coverage.xml 假数字 | 报告已查证为局部运行产物（🟢），门禁本身有效 | **随手删** `backend/coverage.xml`（gitignore 已排除），不立项 |

- [x] **Step F1-1（S1 TDD 红测）**：test_chat_api.py 净计数器 FakeQuota 用例，RED（assert 1==0）。
- [x] **Step F1-2（S1 最小实现）**：`consumed` 标记 + finally 统一退款（e275ad4）；断连/无KB 两处显式退款位于 try 作用域外保留，异常路径并入 finally；GeneratorExit 断开亦退款（审查确证结构性单次退款）。
- [x] **Step F1-3（验证）**：532 tests / 0 failed；ruff 绿。
- [x] **Step F1-4（M1/L5 顺手项）**：kb_lookup docstring（b10bb01）；seed_demo_data/eval_recall 注释对齐（3b687c0，终审建议并入）；coverage.xml 本就未入库（gitignored），仅文件系统删除。
- [x] **Step F1-5（提交）**：3 commits，任务审查 Approved（单次退款结构性证明）。

### F-2 S2 情绪词处置（挂账 P2，窄排除 + 双向用例）

验证结论：误伤实测存在（饱和用例 12/12 判 handoff），但**这是 T1 有意策略**（rag_service.py:43-52 注释自证），且 test_rag.py:69-71 / test_agent_behavior.py:84-100 共 **13 条既有用例锁定 handoff**；评测集 100 题无情绪子串题、意图路由**不在 CI eval 门禁内**。外部审查的"12 用例回归测试"若照搬，会把与既有设计相反的期望写进测试。

- [x] **Step F2-1**：用户采纳推荐项 b（窄排除）。
- [x] **Step F2-2**：初版整块跳过（532c120）→ 任务审查抓出 Important×2：①简报错误声称"骗子属 HANDOFF_KEYWORDS"（实为情绪词表），整块跳过放走骗子/赔偿/退钱/差评四类真投诉句（探针实证）；②改**残句复扫**（`_EMOTION_EXCLUDE.sub("", query)` 后重查情绪词表）两全（346924e）。探针 12/12：4 真投诉句恢复 handoff、裁定句恢复简报原预设、商品语境保持 qa、裸情绪不受影响。docstring/注释如实重写，测试 +7 条锁定。
- [x] **F-2 验证**：全量 550 tests / 0 failed / 8 skipped；ruff 绿；评测集"太慢/崩溃/垃圾"三词 grep 零命中（路由零影响双保险，无需评测复核）。

> S2 不进 F-1 批次的原因：它改的是业务行为（词表语义），须用户拍板设计取舍，且改动须与 13 条锁定用例对质。

### F-3 其余外部条目（批次 G 已核实并处置，2026-08-29）

核实报告：`.superpowers/sdd/external-review-verify-2.md`（运行级验证，8 条全核）。处置结果：

**G1 已修（5906ab8 + 855d1f7 + bff1d37，审查 Approved×2）**：
- L2：chat.py sources 迭代改名 src，删 session_obj 别名（含历史遮蔽注释如实化）
- L3：answer_cache `_ensure_collection` 加锁双检（同 `_kb_lock` 先例）
- L3-一致性：vector_service `ensure_collection` 同款加锁（审查点名定夺项；实现中 global 作用域错误被既有 Bug A 红测当场抓获并修正）
- M4：warmup task 保存引用 + lifespan 关闭时 cancel（cancel 空操作无害）

**验证证伪不修**：
- M3（限流 fail-open）：deps.py is_revoked fail-closed 使 Redis 故障期爆破收益趋零；fail-open 是测试钉死的既定决策。仅挂账"加降级告警"。
- L6（缓存 doc_title 陈旧）：chat.py :449-456 对每次 sources 事件（含缓存命中）按 doc_id 服务时重查 DB 覆写标题——外部审查者没读消费方代码。

**G2 挂账（各需新测试/设计，独立批次）**：
- M7：quota incr 成功后 decr/marker 异常吞为 (False,0) 致计数虚高（±1 次、48h TTL 自愈）；外部补偿 decr 有负计数反例，正确修法需 executed 标志 + 2 个新红测。
- M5：refresh 端点无限流；但 refresh token 是 HS256 JWT（prod 密钥≥32 位）爆破在 decode 即败，外部"token 指纹 key"是无效设计（垃圾 token 每次新 key），若做用 IP 维度。
- L1：RATE_LIMIT_ENABLED 4 类消费点语义重载（含 admin_settings 暴露面）；拆分破坏面小（conftest 一行），做时顺手定夺 M3 降级告警。

### F-4 修复后统一验证

- [x] 全量单测：F-1 后 532/0/8 → F-2 终审修复后 **550/0/8**（两轮均为 junitxml 实测）
- [x] ruff 全绿
- [x] F-2 路由改动评测影响：评测集三词 grep 零命中 + 意图路由不在 CI eval 门禁内（验证报告 §S2）→ 不触发评测复核；push CI 抽样 eval 作最终佐证
- [x] 任务级审查：F-1 Approved；F-2 初版 Needs fixes → 残句复扫修复（346924e），审查两条 Important 全部落地

---

## 四、不修清单（显式声明，含触发条件）

| 项 | 不修理由 | 触发条件 |
|---|---|---|
| 断言/引用阈值 band 校准 | 门禁绿不松尺（S1 分支 B 裁决沿用） | qa < 88% 且归因确认 band 误杀 |
| run_eval 存档加 dense_score 列 | 判定脚本冻结期不动 | 下次合法变更判定脚本时捎带 |
| M2 Session 线程安全（async driver 迁移） | 现状串行 await 无实害，迁移成本高 | 出现并发投递需求时 |
| L6 缓存 doc_title 陈旧（⚪推演） | 理论推演未复现 | 实际出现文档改名投诉 |
| L5 删除本地 coverage.xml | 本地产物、gitignore 已排除 | 已并入 F-1 Step F1-4 顺手清理 |
| M6 删除会话审计调换顺序 | **验证证伪外部修法**：audit_log 内部自带 commit+失败回滚，挪到 commit 前会出现"审计失败→删除连滚→接口仍 200"的反向失真；fail-open 是 Phase 4 显式设计（全仓 8 调用点统一） | 需要更强审计保证时，独立设计"审计失败告警/重试"，不做顺序调换 |
| 评测集/判定脚本任意变更 | 四件套冻结期 | 仅限评测失真实证（Q069 先例流程） |

---

## 五、批次 F 执行纪律

1. **每项独立 commit**，提交信息带外部审查编号（如 `fix(api): S1 SSE error 事件路径补配额退还（外部审查 S1）`）。
2. 修复与测试同 commit；先红后绿。
3. **F-1 组内不触碰** `rag_service.py` / `eval_faithfulness.py` / 评测集——S2 单独走 F-2。
4. 批次完成后由终审代理复审，发现 Critical/Important 即回炉。
5. 全程不碰 `wt/`；BASELINE 数字变化只允许来自批次 F-2 的评测复核。
