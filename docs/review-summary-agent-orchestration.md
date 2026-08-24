# 灵犀客服 · 智能体编排全流程总结报告

> 范围：`feat/agent-assist` 分支（基点 `438bdff` → HEAD `2d9650c`，**23 个提交**）
> 覆盖：坐席辅助（A）+ 会话状态机（B）+ Clarify 澄清（C）+ 订单工具（D）四批次 + 两轮审查闭环

---

## 一、交付成果总览

| 批次 | 功能 | 提交数 | 测试增量 | 关键产出 |
|------|------|-------|---------|---------|
| **A 坐席辅助** | AI 草拟客服回复 + 一键填入 | 6 | +17 | `POST /sessions/{id}/suggest`（fail-open + 60s 缓存）、推荐卡片 |
| **B 会话状态机** | conv_state 列/阶段+槽位/交接摘要升级 | 5 | +21 | 状态机纯函数、迁移 0013、prompt 注入 |
| **C Clarify** | 拒答前澄清追问（额度门控+状态联动） | 3 | +10 | `clarify.py`、clearify_left、done.clarify |
| **D 订单工具** | 槽位驱动零 LLM 查单 + 三步 e2e | 3 | +13 | OrderTool、demo_orders.json、闭环 e2e |
| **审查修复** | 双轴审查+大扫查修复 | 2 | +2 | 前端 refresh 断链、契约同步、kb_lookup 下沉 |
| **优化 + 环境修复** | 干净化 + 依赖回归 | 1 | +2 | bcrypt 降级、O1-O3 |

**终态（亲测，junitxml 权威计数）**：后端 **419 tests / 0 failures / 3 skipped**，前端 **36 passed** + tsc 0，契约 **PASS 无漂移**，批次文件 lint **零报错**。数字可复现：`419 = 372(基线) + 47(新增)`，逐批次数学自洽。

---

## 二、审查发现（两轮）

### 第一轮 · 大扫查（3 路并行）

**代码问题**
- 📌 **Important×1**：`done.clarify` 后端实发但契约未声明 → `82baac8` 补齐
- 📌 **行为缺陷×1**：suggest「重新生成」60s 内命中缓存失效（后端 refresh 能力悬空）→ `82baac8`
- Minor×4：suggest prompt 未并入 conv_state、测试 docstring 与实现矛盾、quick_ans 优先级压过工具、golden 指纹过期
- **7 个点名疑点经实证排除**（缓存×澄清交互/计数循环/工具闭环/租户隔离等）

**性能（诚实结论）**
- 3/5 是**伪优化**（澄清 LLM 缓存「0→1 非 1→2」、query_order 内联、conv_state 增量写）——裁定不做
- 2 项值得做：commit 合并（低收益后续）、重新生成绕缓存（已修）

**Agent 协作流程复盘（最有价值）**
- **6 个计划 bug 全部被实现者红测自抓，审查者 0 首抓**——真正的兜底是 TDD，审查者核心价值是**偏离正当性裁定 + 独立最小复现**（B-Task3 的 `s` 闭包实证是典范）
- 根因：纸面代码不跑就写 + 测试/实现分段写不交叉对照
- 流程评分：*"一套靠实现者红测兜住计划缺陷的高效流水线；计划侧逐字代码欠账应还"*

### 第二轮 · 双轴审查（Standards + Spec 并行）

| 轴 | 发现 |
|---|------|
| **Standards** | ①契约断链（refresh 前端未接，与 Spec 双轴命中）②ORDER_TOPICS/REQUIRED_SLOTS 手工双源 ③doc_titles 三份拷贝 + sessions 横向导入 chat 私有符号 ④mark_clarifying 状态泄漏到 API 层 |
| **Spec** | ①同 refresh 断链 ②scope creep 4 项（3 项合理/已裁定，Eval×4 为 KNOWN_GAP）③**Global Constraints 逐条对照全部合规** |

---

## 三、修复记录

### 第一轮修复（`82baac8`）
1. `done.clarify` 契约同步（SSE done 数据字段补 declare）
2. `SuggestReq.refresh` 绕 60s 缓存（TDD 红→绿，10 tests）

### 第二轮修复（`efaa0bd`·双轴审查四项，全部红测先行）
| # | 问题 | 修复 |
|---|------|------|
| **P1** | 前端 refresh 断链（真 bug） | `suggestReply(id, question?, refresh)` + 「重新生成」传 `true`；**拦截次生坑**——`onClick={onAskSuggest}` 直传会把 MouseEvent 当 refresh（恒真），两处包箭头函数 |
| **P2** | ORDER_TOPICS 手工双源 | 改从 `REQUIRED_SLOTS` 派生（含 order_no 槽位的主题） |
| **P3** | mark_clarifying 状态泄漏 + 魔法串 | `mark_clarifying()` 收回单一真源 + `SLOT_ORDER_NO` 常量替代 3 模块散落 |
| **P4** | suggest prompt 未并入状态 | `<<会话状态>>` 块注入（顾客已给订单号不再重复索要） |

### 优化 + 环境修复（`2d9650c`）
- **O1** `kb_lookup.py` 服务层下沉：KB 定位缓存 + 文档标题查询合一，解除 API 横向私有导入；**别名导入保住全部既有 mock 契约，零测试改动**
- **O2** `done.tool` 透传 SSE + 契约 `tool?: string`
- **O3** 澄清问句整段 token 下发（修复实体词被 8 字分片拆散）
- **环境** `.venv` bcrypt 5.0.0 违反 pyproject 锁定 `bcrypt<5.0` → 重装 4.3.0（此 bug 由**全量回归**抓住）

---

## 四、优化建议（执行 + 后续）

### ✅ 已执行
1. 前端重新生成绕缓存（P1）
2. 契约连续三轮对齐（clarify→refresh→tool）
3. kb_lookup 服务层下沉（借贷清理）
4. 澄清 token 整段化（与订单分支同构）

### 🔮 后续建议（按优先级）

**A. Agent 协作流程**
1. **计划代码降级为伪代码 + 关键签名 + 契约**（mock 目标/事件序列/断言意图），逐字实现还给实现者 TDD 循环——6 个计划 bug 的根因
2. 简报交付前三项自洽 lint：`patch 目标↔import 式`、`断言↔事件流`、`mock 契约↔调用式`
3. 批次末**最小真实链路冒烟**（1 条 curl SSE + create_all↔alembic 对拍）——PG 迁移降级后全程无补位
4. 台账设**风险登记簿**：Minor/Concerns 强制批次末 triage 闭环，防"留后续"无限堆积
5. 子代理动环境后**必须全量回归**（本次 bcrypt 漂移由全量测试抓住）

**B. 产品能力**
- 迁移 0013 加 PG 实测（CI 补）＋ `check_contracts` 声明需 Python≥3.12（PEP 701）
- 澄清分支 token 分片统一（O3 只改澄清，其余 `_split_tokens` 非整段路径待统一）
- `_suggest_cache` 容量上限（TTL 清理）
- 工具回答前端徽标展示（契约已透传 `tool` 字段，前端即取即用）
- 多订单号槽位（当前只取首个，批次 D 已留接口）

**C. 观测闭环**
- conv_state 阶段分布 / clarify 触发率 / 工具命中率打 telemetry 点进 admin stats（当前黑盒）
- 线上采样评测 + bad case 回流 + 转人工率漏斗（北极星指标监控）

---

## 五、逐流程质量门禁证据

| 门禁 | 结果 |
|------|------|
| 每任务红测 | 24 个任务全有红→绿记录（含 6 次计划 bug 先红） |
| 每任务审查 | 规格符合性 + 代码质量双维度，Critical/Important 零遗留 |
| 批次末亲测 | junitxml 权威计数 372→393→403→416→419 逐级自洽 |
| 契约 | 三轮 PASS 无漂移 |
| Lint | 批次文件零报错（全仓历史债 17-20 项均批次外，已在台账登记） |

---

**结论**：分支就绪可合入 master。四条子系统（AI 建议 / 会话记忆 / 澄清拒答 / 订单应答）功能闭环、测试全绿、契约无漂移；主干流程验证过硬（红测兜住一切计划缺陷），但**计划侧逐字代码的"可执行性债"应还**——把 bugs 死在红测之前，而非让实现者 TDD 循环来兜底。