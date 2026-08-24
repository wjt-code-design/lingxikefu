# 灵犀客服 · 优化项执行规划（T1 观测闭环 / T2 迁移实测 / T3 工具徽标）

> 制定：2026-08-25 · 分支 `feat/agent-assist` · HEAD `6e52f48`
> 前置状态：后端 **424 total / 419 passed / 5 failed / 0 errors / 3 skipped**（5 项 qdrant 502 环境性，非代码故障）；前端 **36/36 passed** + tsc 0；契约 **PASS**
>
> **执行结果（2026-08-25 收口）**：T1.1→e890a57、T1.2→07c00f6、T3→0f96f92、T1.3→54bef42、
> T2 本地 PG16 对称实测 + CI migrations job→2ecf6df，lint 存量清零→2ce2fac。
> 终态：后端 **426 = 423 绿 + 0 败 + 3 跳**（junitxml）、前端 45/45、tsc 0 错、契约 PASS、ruff 全绿。
> 注：上方「5 failed」后经根因排查为 Windows 系统代理劫持 httpx + PG 密码注入缺失（环境性），
> 真实基线 421 绿——见 7f1430b NO_PROXY 修复与方案书 §1.2 修订。

---

## 一、背景与目标

### 1.1 为什么现在做这三项

上一轮四批次（坐席辅助 / 会话状态机 / Clarify 澄清 / 订单工具）完成后，审查报告给出两条后续建议：

| 来源 | 建议 | 对用户的价值 |
|------|------|-------------|
| review-summary §四·C | conv_state 阶段分布 / clarify 触发率 / 工具命中率打 telemetry 进 admin stats（当前黑盒） | 运营能看到「澄清是否真的替代拒答」「工具查单省了多少人工」 |
| review-summary §四·B | 迁移 0013 加 PG 实测（CI 补） | 唯一没有运行证据的部署风险 |
| review-summary §四·B | 工具回答前端徽标展示（契约已透传 `tool` 字段，前端即取即用） | 客服一眼识别「实时订单数据 vs 知识库检索」 |

这三项分别命中三类杠杆：
- **T1（观测闭环）**：度量杠杆——让四个子系统从黑盒变可评估
- **T2（迁移实测）**：部署质量红线——当前唯一无运行证据的 PG 风险
- **T3（工具徽标）**：即时可见价值——最小成本，客服可感知

### 1.2 核心问题：Agent 辅助工作可视化的必要性分析

**结论：有必要，但需分层建设，避免一次性过度设计。**

#### 1.2.1 可视化对象分层

| 层级 | 可视化对象 | 受众 | 当前状态 | 本轮动作 |
|------|-----------|------|---------|---------|
| **L1 系统度量** | 澄清触发率、工具命中率、拒答率趋势 | 运营/产品 | 黑盒，无数据 | ✅ T1 本轮落地 |
| **L2 客服辅助** | 回答来源徽标（订单查询 / 知识库 / LLM生成） | 一线客服 | 无徽标，无法区分 | ✅ T3 本轮落地 |
| **L3 客服辅助** | AI 建议卡片来源标记（建议来自哪条知识） | 一线客服 | 有建议卡片，无来源标记 | ⚠️ 部分合并 T3（徽标按 tool 类型区分） |
| **L4 顾客透明** | 「AI 正在回答」标识 | 普通用户 | 已有 "AI 小智" 徽标 | ❌ 已有，不做 |
| **L5 运维监控** | 缓存命中率、LLM 降级次数、时延 P99 | 技术运维 | 无埋点 | ❌ 留 T1 之后按数据驱动 |

#### 1.2.2 可视化设计原则

1. **信息分层**：系统级度量（L1）进 admin 后台；客服级辅助（L2/L3）进对话界面；不混层
2. **最小侵入**：徽标用 antd `Tag` 描边样式，不破坏现有气泡布局
3. **可扩展**：`tool` 字段值域预留（`order_query` / `rag` / `llm`），前端按值映射徽标
4. **YAGNI**：历史回放（T3.4）明确不做，除非后端 schema 已透出

#### 1.2.3 本轮可视化产出（T1 + T3 合并效果）

```
┌─────────────────────────────────────────────────────────┐
│  运营后台 /admin/stats（T1 新增）                        │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐       │
│  │ 会话数  │ │ 消息数  │ │澄清触发 │ │工具命中 │       │
│  │  1,234  │ │  5,678  │ │   89    │ │   45    │       │
│  └─────────┘ └─────────┘ └─────────┘ └─────────┘       │
│  + 趋势折线图（近 14 天）                                │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  客服对话界面（T3 新增）                                 │
│                                                         │
│  🤖 AI 小智                                [订单查询] │  ← 徽标
│  ┌─────────────────────────────────────────────┐       │
│  │ 您的订单 #20240801001 已发货，预计 8 月...  │       │
│  └─────────────────────────────────────────────┘       │
│                                                         │
│  🤖 AI 小智                                             │  ← 无徽标（RAG 回答）
│  ┌─────────────────────────────────────────────┐       │
│  │ 根据退换货政策，七天无理由退货需满足...      │       │
│  └─────────────────────────────────────────────┘       │
└─────────────────────────────────────────────────────────┘
```

---

## 二、待执行项与步骤

### 2.1 T1 · 运营观测闭环

**现状**：`admin.py get_stats` 已按 `Message.intent` 聚合（含 refuse 待补录 Top10），但**澄清触发数、工具命中数、会话状态分布**无观测点。

**目标**：客服/运营能在 `/admin/stats` 看到「澄清触发数 / 工具命中数 / 会话主题分布」及趋势。

#### 步骤 1.1 — 澄清标记落库（红测先行）

| 项 | 内容 |
|---|------|
| **改** | `chat.py` done 落库时 `meta` 补 `clarify: true`（澄清轮） |
| **位置** | [chat.py#L436](file:///c:/Users/33393/WorkBuddy/2026-08-15-00-39-34/backend/app/api/chat.py#L436) 的 `meta` 构造处 |
| **当前** | 澄清轮 assistant 消息 intent 落 refuse，meta 无标记，无法与「真拒答」区分 |
| **方案** | `done.data.get("clarify")` 为真 → `meta["clarify"] = True` |
| **红测** | `test_chat_api.py` 澄清用例补断言 `meta.clarify == True` |

#### 步骤 1.2 — admin stats 扩展聚合

| 项 | 内容 |
|---|------|
| **改** | `admin.py get_stats` 扩展三个聚合字段 + `AdminStats` schema 加字段 |
| **tool_hits** | `Message.meta["tool"]` 非空计数（先只认 `order_query`，防幻觉字段） |
| **clarify_count** | `Message.meta["clarify"]` 为 True 计数 |
| **topic_dist** | 从 `Session.conv_state["topic"]` 聚合分布（GROUP BY，JSON 提取走 SQL） |
| **schema** | `AdminStats` 加 `tool_hits: int`、`clarify_count: int`、`topic_dist: dict[str, int]` |
| **验证** | 新增 `test_admin_stats.py` 用例断言三字段；SQLite `json_extract` / PG `->>` 双兼容 |

#### 步骤 1.3 — trend 端点补对应时间序列

复用 `get_stats_trend` 的按日分桶模式，新增 `clarify_hits` / `tool_hits` 两个时间序列字段。

#### 步骤 1.4（可选，YAGNI）— 前端 StatsPage 加指标卡

复用 KpiCard / TrendChart 组件，在运营后台页加「工具命中 / 澄清触发」两组卡。

**T1 DoD**：客服在 `/admin/stats` 看到三个新维度 + 趋势；refuse 率与澄清数合看能证明「澄清是否真的替代部分拒答」。

---

### 2.2 T2 · 迁移 0013 与 alembic 链 PG 实测 + CI 补位

**现状**：迁移 0013（`sessions.conv_state` JSON 列）全程从未在真实 PostgreSQL 上执行。`alembic.ini` 无默认连接串，CI 无迁移冒烟 job。

**目标**：`0001→0013` 在真实 PG 全量 up/down/re-up 无错，且纳入 CI。

#### 步骤 2.1 — 本地 PG 上量迁移

| 项 | 内容 |
|---|------|
| **环境** | 用 `docker-compose` 的 `lingxi-postgres-1`（已在跑） |
| **配置** | 临时 `.env` 指向该 PG（`POSTGRES_HOST=localhost`） |
| **操作** | `alembic upgrade head`，确认 0013 应用成功 |
| **验证** | `alembic current` 显示 `0013 (head)` |

#### 步骤 2.2 — 回退对称性（down-up-reup）

| 项 | 内容 |
|---|------|
| **操作** | `alembic downgrade 0012` → `\d sessions` 确认 `conv_state` 列被删 → `alembic upgrade head` 列重建 |
| **验证** | 三步无错 + 列增删对称 |

#### 步骤 2.3 — 数据兼容性

| 项 | 内容 |
|---|------|
| **空值兼容** | 旧行（无 conv_state）跑 `upgrade head` 确认不炸 |
| **读写一致** | INSERT 一行带 conv_state JSON → SELECT 读回，PG JSONB 语义正确 |
| **验证** | `psql` 手查 + Python `client.conv_state` 读取比对 |

#### 步骤 2.4 — CI 补迁移冒烟 job

| 项 | 内容 |
|---|------|
| **改** | `.github/workflows/ci.yml` 的 test job 已含 `alembic upgrade head`（见[L92](file:///c:/Users/33393/WorkBuddy/2026-08-15-00-39-34/.github/workflows/ci.yml#L92)） |
| **问题** | 当前 CI 的 `pytest` 使用 SQLite（env 里无 `DATABASE_URL`），迁移跑的是 SQLite 的 `create_all`，**不是 PG** |
| **方案** | 新增独立 migration job：起 PG service → `alembic upgrade head` → `alembic downgrade 0012` → `alembic upgrade head` → 断言 `current == 0013` |
| **验证** | CI 绿 + 迁移步骤通过 |

**T2 DoD**：迁移 0013 及整条链在 PG 有可复现的执行证据；CI 起 PG 自动验证 up/down/re-up；回退对称性被锁定。

---

### 2.3 T3 · 前端工具回答徽标

**现状**：契约已透传 `done.tool`（订单工具 = `order_query`，见 contracts/api.ts L195），落库 meta 也有 `tool`；但前端不消费——客服看到工具回答与普通 RAG 回答无区别。

**目标**：工具回答气泡上显示「订单查询」徽标。

#### 步骤 T3.1 — useChatStream 存 tool 到 state

| 项 | 内容 |
|---|------|
| **改** | `useChatStream.ts` `applyEvent` 的 `case 'done'` 加 `tool: ev.data.tool` |
| **契约对齐** | `done` 事件已有 `tool?: string`（contracts/api.ts L195） |
| **验证** | 单测断言 done 后 `tool` 写入 state |

#### 步骤 T3.2 — ChatContainer finalize 带入 assistant 消息

| 项 | 内容 |
|---|------|
| **改** | `ChatContainer.tsx` [finalize 段](file:///c:/Users/33393/WorkBuddy/2026-08-15-00-39-34/frontend/src/components/chat/ChatContainer.tsx#L333) 构造 `assistant` 对象时加 `tool` 字段 |
| **位置** | `assistant: ChatMessage` 构造处 |
| **验证** | chat-container 测试断言 tool 透传 |

#### 步骤 T3.3 — types.ts + MessageBubble 渲染徽标

| 项 | 内容 |
|---|------|
| **改** | `components/chat/types.ts` 的 `ChatMessage` 加 `tool?: string` |
| **改** | `MessageBubble.tsx` 气泡渲染徽标：`msg.tool === 'order_query'` → `<Tag>订单查询</Tag>`（描边样式） |
| **徽标样式** | 用 antd `Tag`，`color="blue"` 描边，置于气泡顶部或底部（按设计微调） |
| **验证** | MessageBubble 单测 + 手工看板 |

#### 步骤 T3.4（YAGNI）— 历史消息回放

| 项 | 内容 |
|---|------|
| **判定** | 后端 `getSessionDetail` 是否返回 `tool` 字段？ |
| **若返回** | 历史气泡也能显示徽标（types.ts 已支持，MessageBubble 已渲染） |
| **若不返回** | 本期仅新流式消息显示，历史不回显——明确取舍 |
| **当前状态** | 后端 `MessageResponse` schema（session_detail 返回）**未含 tool 字段** |

**T3 DoD**：新一次订单查询的回答气泡带「订单查询」徽标；历史回放视后端透出情况可选。

---

## 三、执行顺序与依赖

```
T1.1 ─┐
T1.2 ─┼─► T1.3 (trend 补位) ─► [T1.4 前端可选]
       │
T2.1 ─┐
T2.2 ─┼─► T2.3 (数据兼容) ─► T2.4 (CI 补位)  ← 与 T1 可并行
       │
T3.1 ─┐
T3.2 ─┼─► T3.3 (徽标渲染) ─► [T3.4 历史回放可选]  ← 与 T1/T2 可并行
```

**推荐执行顺序**：

1. **T1.1 + T1.2**（澄清/工具聚合）— 红测先行，核心度量地基
2. **T3.1 + T3.2 + T3.3**（工具徽标）— 成本最低、即时可见，建团队信心
3. **T1.3**（trend 时间序列）— 复用模式
4. **T2.1 + T2.2 + T2.3 + T2.4**（迁移实测 + CI）— 部署红线收尾

**并行机会**：T1 / T3 可并行由不同子 agent 执行（无共享文件冲突）；T2 需独占 PG，建议单独执行。

---

## 四、过程纪律

延续本分支四批次模式：

| 纪律 | 要求 |
|------|------|
| **TDD 红测先行** | 每个步骤先写失败测试（`assert response.tool_hits == 0` → 改代码 → 转绿） |
| **契约单一真源** | 字段增删一律改 `contracts/api.ts` → 同步后端 schema → 跑 `check_contracts.py` |
| **双兼容** | 观测聚合 SQL 同时兼容 SQLite（测试）与 PG（生产）：`func.coalesce` + `func.json_extract` |
| **YAGNI 标注** | 标注为「可选」的步骤（T1.4 / T3.4）明确不纳入首批 |
| **批次末亲测** | 每项完成后跑 junitxml 权威计数，记录增量 |
| **独立期望值** | 覆盖率 ≥70% 硬门禁（当前已达标，不得回退） |

---

## 五、风险与对策

| 风险 | 影响 | 对策 |
|------|------|------|
| conv_state JSON 提取在 SQLite 语义不同 | topic_dist 聚合 SQL 在双环境行为不一致 | 用 SQLAlchemy `func.json_extract` 抽象层；测试断言双环境各自正确 |
| CI 新 migration job 跑时 PG service 启动慢 | 超时失败 | health-cmd 已配置（`pg_isready`），`--health-retries 10` |
| 工具徽标文案未来扩展（不止 order_query） | 徽标需支持多工具 | Tag 颜色按 tool 值映射：`order_query→blue`、`kb_lookup→green`（预留） |
| 历史回放后端 schema 需同步扩展 | T3.4 需要 MessageResponse 加 tool 字段 | 明确判定：若历史暂不需要，T3.4 不做；避免一次性跨层改动 |

---

## 六、DoD 验收清单

### T1 验收
- [ ] `test_chat_api.py` 澄清用例断言 `meta.clarify == True`（红→绿）
- [ ] `test_admin_stats.py` 新文件：断言 `tool_hits`、`clarify_count`、`topic_dist` 三字段
- [ ] 手工 `GET /api/v1/admin/stats` 看到新增字段
- [ ] 趋势端点 `GET /api/v1/admin/stats/trend` 含 `tool_hits` / `clarify_count` 时间序列
- [ ] 契约 `check_contracts.py` PASS

### T2 验收
- [ ] `alembic upgrade head` 在本地 PG 成功（`0013 (head)`）
- [ ] `downgrade 0012` + `upgrade head` 对称性无错
- [ ] 旧行（无 conv_state）兼容升级
- [ ] CI migration job 起 PG 验证 up/down/re-up 全绿

### T3 验收
- [ ] `useChatStream` 单测断言 done 后 `tool` 写入
- [ ] `ChatContainer` finalize 后 assistant 消息含 `tool`
- [ ] `MessageBubble` 单测：`tool === 'order_query'` → 徽标渲染
- [ ] 手工触发订单查询，气泡出现「订单查询」徽标
- [ ] tsc 0 + 前端测试全绿

### 全量回归
- [ ] 后端 pytest ≥ 当前 baseline（419+ passed，0 errors）
- [ ] 前端 vitest 36+ passed + tsc 0
- [ ] 契约 PASS
- [ ] HEAD 提交，分支 `feat/agent-assist`

---

## 七、关于「Agent 辅助工作可视化」的最终建议

| 维度 | 当前 | 本轮是否纳入 |
|------|------|-------------|
| **系统视角**（澄清/工具命中率 → admin stats） | 黑盒 | ✅ 本轮 T1 落地 |
| **客服视角**（建议来源徽标：知识库 vs LLM） | 无徽标 | ⚠️ 部分合并 T3（徽标按 tool 类型区分，天然覆盖 RAG vs 工具 vs LLM 三类来源） |
| **顾客视角**（AI 透明度） | 已有 "AI 小智" | ❌ 不做 |
| **运维视角**（缓存命中率、降级次数） | 无埋点 | ❌ 留 T1 之后按数据驱动 |

**本轮产出**：
- T1 让运营看到系统级效果
- T3 让客服看到回答来源徽标（`order_query` 徽标；RAG 回答无徽标，自然区分）

如果后续需要「知识库检索」徽标 vs 「LLM 自由生成」徽标，可在 T3 基础上扩展 `tool` 字段值域（如 `tool: "rag"` 或 `tool: "llm"`），前端按值映射徽标颜色/文案——当前架构已支持，无需重构。

---

> 规划完毕，待用户确认后进入执行。每项严格 TDD 红测先行，批次末 junitxml 权威计数。
