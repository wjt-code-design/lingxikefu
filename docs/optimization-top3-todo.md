# 最高优先级 3 项优化 · 待办清单与实施步骤

> 来源：`docs/review-summary-agent-orchestration.md` 优化建议
> 选择依据：三项分别命中「度量杠杆」「部署质量红线」「即时可见价值」

---

## 待办清单总览

| # | 待办 | 类别 | 价值/杠杆 | 成本 |
|---|------|------|----------|------|
| **T1** | 运营观测闭环：clarify/工具命中加观测点 + admin 聚合 | 度量杠杆 | 高（让四系统从黑盒变可评估） | 中 |
| **T2** | 迁移 0013 与 alembic 链在 PG 实测 + CI 补位 | 部署质量红线 | 高（当前唯一无运行证据的部署风险） | 中 |
| **T3** | 前端工具回答徽标（消费已透传的 `tool` 契约） | 即时价值 | 中高（最小成本，客服可感知工具回答） | 低 |

---

## T1 · 运营观测闭环

**现状**：[admin.py get_stats](file:///C:/Users/33393/WorkBuddy/2026-08-15-00-39-34/backend/app/api/admin.py#L116) 已按 `Message.intent` 聚合（含 refuse），但**澄清触发数、工具命中数、会话状态分布**无观测点——四批次新能力当前是黑盒。

**目标**：客服/运营能在 admin 数据统计页看到三个新维度的趋势，从而评估「澄清降转人工」「工具查单」实际效果。

### 实施步骤

| 步骤 | 内容 | 验证 |
|------|------|------|
| **1.1** | `chat.py` done 落库时 `meta` 补 `clarify: true`（澄清轮）——当前澄清轮 assistant 消息 intent 落 refuse，meta 无标记，无法与「真拒答」区分。修改 [chat.py done 分支](file:///C:/Users/33393/WorkBuddy/2026-08-15-00-39-34/backend/app/api/chat.py#L465) 的 meta 构造处 | `test_chat_api.py` 澄清用例补断言 meta.clarify |
| **1.2** | `admin.py get_stats` 扩展聚合：
  - `tool_hits`：`Message.meta.tool` 非空计数（先只认 `order_query`，防幻觉字段）
  - `clarify_count`：`meta.clarify` 为 True 计数
  - `topic_dist`：从会话 conv_state 聚合主题分布（走 `count(distinct session)` 或明细查询）
  在 stats 响应 schema（`admin.schemas`）加对应字段 | 新增 `test_admin_stats.py` 用例断言三字段 |
| **1.3** | `get_stats_trend` 补对应时间序列（复用既有 trend 查询模式） | 趋势端点测试 |
| **1.4**（可选） | 前端 `StatsPage` 加「工具命中 / 澄清触发」两组指标卡（复用 KpiCard/TrendChart） | 前端测试 + 手工看板 |

**验收**：客服在 `/admin/stats` 能看到「澄清澄清数 / 工具查单数 / 会话主题分布」且可随时间看趋势。拒答率（refuse）与澄清数合看能证明「澄清是否真的替代了部分拒答」。

---

## T2 · 迁移 0013 与 alembic 链 PG 实测 + CI 补位

**现状**：批次 B 的迁移 0013（`sessions.conv_state` JSON 列）全程**从未在真实 PostgreSQL 上执行**——本地 alembic.ini 恒拼 PG 连接串无凭证，仅靠 `create_all` 兜底的 SQLite 测试。这是唯一没有运行证据的部署风险（`sa.JSON` 在 PG 落 JSON 类型，0013 实测才能确认）。

**目标**：迁移链 `0001→0013` 在真实 PG 全量 up/down/re-up 无错，且纳入 CI 防止回退。

### 实施步骤

| 步骤 | 内容 | 验证 |
|------|------|------|
| **2.1** | 用 `docker-compose` 的 `lingxi-postgres-1`（已在跑）：配置临时 `.env` 指向该 PG，跑 `alembic upgrade head`，确认 0013 应用成功 | `alembic current` 显示 `0013 (head)` |
| **2.2** | `alembic downgrade 0012` → 验证 `conv_state` 列被删 → 再 `upgrade head` 列重建（对称性） | 三步无错 + `\d sessions` 确认列增删 |
| **2.3** | 数据兼容：往旧行（无 conv_state）跑 `upgrade head` 确认不炸；insert 一行带 conv_state JSON 后 select 读回 | psql 手查 JSONB 语义正确 |
| **2.4** | CI（`.github/workflows/ci.yml`）加迁移冒烟 job：起 PG 服务 → `alembic upgrade head` → 断言 `current==0013`。若 CI 无 PG 服务，用 `testcontainers` 或 compose 起临时 PG | CI 绿 + 迁移步骤通过 |
| **2.5** | 把「迁移本地验证」从台账降级项提升为必跑项（写进 ci.yml），防止未来迁移再次无证据 | CI 含迁移 job |

**验收**：迁移 0013 及整条链在 PG 有可复现的执行证据，CI 起 PG 自动验证，回退对称性被锁定。

---

## T3 · 前端工具回答徽标

**现状**：契约已透传 `done.tool`（订单工具=`,`order_query`，见大扫查 O2），落库 meta 也有 `tool`；但前端不消费——客服/用户看到工具回答与普通 RAG 回答无区别。

**目标**：工具回答在气泡上显示「订单查询」徽标，让客服一眼识别「这是实时订单数据而非知识库检索」。

### 实施步骤

| 步骤 | 内容 | 验证 |
|------|------|------|
| **T3.1** | `useChatStream.ts` `applyEvent` 的 `case 'done'` 存 `tool: ev.data.tool` 到 stream state | 单测断言 done 后 tool 写入 |
| **T3.2** | `ChatContainer` finalize 时把 `tool` 带入 assistant 消息对象（详见 [ChatContainer finalize 段](file:///C:/Users/33393/WorkBuddy/2026-08-15-00-39-34/frontend/src/components/chat/ChatContainer.tsx#L312)） | chat-container 测试 |
| **T3.3** | `components/chat/types.ts` 的 `ChatMessage` 加 `tool?: string`；`MessageBubble` 渲染徽标（`tool==='order_query'` → `<Tag>订单查询</Tag>` 描边样式） | MessageBubble 测试 + 手看气泡 |
| **T3.4** | 历史消息回放：getSessionDetail 的消息若是工具来源（后端可选择性透出 meta.tool → 消息 sources/字段），历史气泡也能显示徽标。**YAGNI 判定**：若后端 SessionDetail 不返回 tool，本期仅新流式消息显示，历史不回显——明确取舍 | 契约字段评估 |

**验收**：新一次订单查询的回答气泡带「订单查询」徽标；重放历史（如后端透出）可选。

---

## 执行建议

- **优先级顺序**：T1 → T2 → T3（T1 是度量地基，先建才能评估后续；T2 是部署红线尽早排雷；T3 成本最低可随时插入）
- **过程纪律**：延续本分支模式——每步 TDD 红测先行、契约单一真源、批次末亲测 junitxml 计数
- **不做的边界**：T1 的 topic_dist 若 conv_state 聚合复杂度高，先做 clarify/tool 两个字段（YAGNI），主题分布留观测数据积累后再建模