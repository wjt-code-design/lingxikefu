# 多 Agent 协作方案书 · 灵犀客服

> 日期：2026-08-24 | 状态：设计阶段（待实施） | 项目：灵犀 Customer Service（Lingxi）
> 前置依赖：Phase 1（RAG 管线可组合化）已完成 ✅

---

## 1. 背景与现状

### 1.1 项目现状

灵犀是星河智家智能客服平台，当前架构为 **单请求路径处理**：

```
用户请求 → chat_stream() → RAG 管线 → 工单流转 → SSE 响应
```

所有逻辑集中在 `backend/app/api/chat.py` 一个函数内，"Agent" 概念尚未显式定义。

### 1.2 已有能力（不应重复造）

| 能力 | 实现位置 | 状态 |
|------|---------|------|
| RAG 管线 | `rag_service.py` + `steps/` + `PipelineRunner` | ✅ 已完成 |
| 工单状态机 | `ticket_state_machine.py`（TRANSITIONS 表 + CAS） | ✅ 已完成 |
| 工单自动化 | `ticket_automation.py`（auto_start/resolve/close） | ✅ 已完成 |
| 会话管理 | SSE 流式 + 会话上下文 | ✅ 已完成 |
| 答案缓存 | `answer_cache.py`（精确+语义双命中） | ✅ 已完成 |
| 用户画像 | `user_profile_service.py` | ✅ 已完成 |

### 1.3 缺失能力（需要建设的 Agent）

| Agent | 职责 | 当前状态 |
|-------|------|---------|
| **QA Agent** | 封装 RAG 管线，独立对外提供问答能力 | 逻辑散在 rag_service + chat.py |
| **Ticket Agent** | 封装工单决策，情绪识别→策略选择→状态流转 | 逻辑散在 ticket_automation |
| **Image Agent** | 图片理解 + 文字融合 | 未实现 |
| **Router** | 请求级分发，决定单/多 Agent 协作 | 未实现 |

### 1.4 核心问题

> **当前所有逻辑都在一个请求路径里，"多 Agent" 需要先定义 Agent 边界，再谈协作。**

---

## 2. Agent 定义

> 实现状态校正（2026-08-27 对抗审查）：QA 路径**不是 Agent 成员**——chat 层直接
> 调用 `stream_answer`（流式/断连/配额/落库耦合在 chat 层为 §2.2 硬约束）。
> 旧版 QAAgent 包装类无生产调用方（仅测试引用），已删除；下方 §2.1 保留为概念描述。

### 2.1 QA Agent（问答 Agent）

**职责**：处理用户提问，返回知识库答案

| 项目 | 说明 |
|------|------|
| 输入 | `query`, `kb_id`, `history`, `user_profile` |
| 输出 | `answer`, `sources`, `intent`, `refuse` |
| 实现方式 | 封装现有 `PipelineRunner` + `steps/` |
| 核心流程 | intent → rewrite → cache → retrieve → refuse → generate |

**接口契约**：
```python
class QAAgent:
    async def run(self, ctx: SharedContext) -> SharedContext:
        """执行 RAG 管线，结果写入 ctx.rag_answer / ctx.rag_sources"""
        ...
```

### 2.2 Ticket Agent（工单 Agent）

**职责**：处理转人工、情绪升级、自动建单

| 项目 | 说明 |
|------|------|
| 输入 | `session_id`, `message`, `user_id`, `emotion_signal` |
| 输出 | `ticket_action`, `ticket_priority`, `emotion` |
| 实现方式 | 封装现有 `ticket_automation` + 新增情绪分类 |
| 核心流程 | 情绪分类 → 策略选择 → 状态机流转 |

**接口契约**：
```python
class TicketAgent:
    async def run(self, ctx: SharedContext) -> SharedContext:
        """执行工单决策，结果写入 ctx.ticket_action / ctx.ticket_priority"""
        ...
```

### 2.3 Image Agent（图片 Agent）

**职责**：理解用户上传图片，融合文字生成 fused query

| 项目 | 说明 |
|------|------|
| 输入 | `image_base64`, `text_query` |
| 输出 | `image_desc`, `fused_query` |
| 实现方式 | 新增，调豆包视觉 MCP |
| 核心流程 | 图片理解 → 文字融合 → 输出 fused_query |

**接口契约**：
```python
class ImageAgent:
    async def run(self, ctx: SharedContext) -> SharedContext:
        """执行图片理解，结果写入 ctx.image_desc / ctx.fused_query"""
        ...
```

### 2.4 Router（路由 Agent）

**职责**：决定由哪个 Agent 处理，是否多 Agent 协作

| 项目 | 说明 |
|------|------|
| 输入 | `SharedContext`（query, image_base64, history） |
| 输出 | `agents_invoked` 列表 + 执行策略（串行/并行） |
| 实现方式 | 规则式分发（意图+情绪+图片） |
| 核心流程 | 规则匹配 → Agent 选择 → 编排执行 → 结果合并 |

**分发规则**：
```
1. 有 image_base64 → Image Agent 必走
2. 投诉词/情绪词命中 → Ticket Agent 并行
3. 默认 → QA Agent 单走
```

---

## 3. 协作场景矩阵

| 场景 | 触发条件 | 主 Agent | 协作 Agent | 协作方式 |
|------|---------|---------|-----------|---------|
| **纯知识问答** | intent=qa, 无图片 | QA Agent | — | 单 agent 独立处理 |
| **图片问答** | 有 image_base64 | Image Agent → QA Agent | Image → QA 串联 | Image 输出 desc 作为 QA 输入 |
| **情绪升级转人工** | 情绪词命中 / 愤怒 | QA Agent → Ticket Agent | 并行：QA 返回安抚话术，Ticket 建单 | Router 同时调用两者 |
| **用户主动转人工** | 点击转人工按钮 / intent=handoff | Ticket Agent | — | 单 agent 独立处理 |
| **投诉+问答** | 投诉关键词 + 具体问题 | QA Agent + Ticket Agent | 并行：QA 回答具体问题，Ticket 创建高优工单 | Router 同时调用两者 |
| **复杂退换货** | 多轮对话+纠纷信号 | QA Agent + Ticket Agent | 先 QA 尝试解答，用户不满意 → Ticket 升级 | 串行，条件触发 |

---

## 4. 架构设计

### 4.1 系统架构图

```
                    ┌─────────────────────────────────────┐
                    │          chat.py (入口)              │
                    │  SSE 流式 + 配额 + 会话管理           │
                    │  （保持不变，内部改为调用 Router）     │
                    └─────────────┬───────────────────────┘
                                  │
                    ┌─────────────▼───────────────────────┐
                    │         Router (新增)                │
                    │  规则式分发：                         │
                    │  - 有图 → Image Agent 必走           │
                    │  - 投诉词 → Ticket Agent 并行        │
                    │  - 默认 → QA Agent 单走              │
                    │  - 结果合并 → SSE 事件序列           │
                    └─────────────┬───────────────────────┘
                                  │
              ┌───────────────────┼───────────────────┐
              │                   │                   │
    ┌─────────▼─────────┐ ┌──────▼──────┐ ┌─────────▼─────────┐
    │   QA Agent        │ │ Ticket Agent│ │  Image Agent      │
    │                   │ │             │ │                   │
    │ PipelineRunner    │ │ 情绪分类    │ │ 豆包视觉 MCP      │
    │  ├ intent         │ │ ↓           │ │ ↓                 │
    │  ├ rewrite        │ │ 策略选择    │ │ 图片描述          │
    │  ├ cache          │ │ ↓           │ │ ↓                 │
    │  ├ retrieve       │ │ 状态机流转  │ │ 文字融合          │
    │  ├ refuse         │ │             │ │                   │
    │  └ generate       │ │ 复用现有    │ │ 新增调用          │
    │                   │ │ ticket_sm   │ │                   │
    │ 已有代码封装      │ │             │ │                   │
    └───────────────────┘ └─────────────┘ └───────────────────┘
```

### 4.2 请求处理流程

```
1. 用户请求进入 chat_stream()
2. 构建 SharedContext（query, image, user_id, session_id, history）
3. Router 根据规则决定调用哪些 Agent
4. Agent(s) 并行或串行执行
5. Router 合并结果，生成 SSE 事件序列
6. chat_stream() 按原契约返回 SSE 事件
```

### 4.3 与原架构的兼容性

| 原契约 | 保持/变更 |
|--------|----------|
| SSE 事件类型（intent, stage, token, sources, done, error） | **保持不变** |
| `client_msg_id` 幂等 | **保持不变** |
| 配额扣减与回滚 | **保持不变** |
| 会话管理 | **保持不变** |
| 内部处理逻辑 | **从单函数 → Router 编排** |

---

## 5. SharedContext 设计

### 5.1 数据结构

```python
@dataclass
class SharedContext:
    """单次请求的所有 agent 共享上下文"""
    # 输入
    query: str
    image_base64: str | None
    user_id: str
    session_id: str
    history: list[dict]
    
    # QA Agent 写入
    intent: str = ""              # qa / handoff / chitchat
    rag_answer: str = ""
    rag_sources: list = field(default_factory=list)
    refuse: bool = False
    
    # Ticket Agent 写入
    ticket_action: str = ""       # create / escalate / none
    ticket_priority: str = ""     # low / normal / high
    emotion: str = ""             # calm / unhappy / angry
    
    # Image Agent 写入
    image_desc: str = ""
    fused_query: str = ""         # 图片+文字融合后的 query
    
    # Router 写入
    agents_invoked: list[str] = field(default_factory=list)
    
    # 输出
    final_events: list[dict] = field(default_factory=list)  # SSE 事件序列
```

### 5.2 写入权限

| 字段 | 写入方 | 读取方 |
|------|--------|--------|
| `intent`, `rag_answer`, `rag_sources`, `refuse` | QA Agent | Router, chat.py |
| `ticket_action`, `ticket_priority`, `emotion` | Ticket Agent | Router, chat.py |
| `image_desc`, `fused_query` | Image Agent | QA Agent, Router |
| `agents_invoked`, `final_events` | Router | chat.py |

---

## 6. 工具 / MCP 需求

### 6.1 工具清单

| 工具 | 用途 | Agent | 接入方式 | 状态 |
|------|------|-------|---------|------|
| **知识库检索** | 向量检索 | QA Agent | `retrieval_service.py` | ✅ 已有 |
| **答案缓存** | 回答缓存 | QA Agent | `answer_cache.py` | ✅ 已有 |
| **工单状态机** | 工单流转 | Ticket Agent | `ticket_state_machine.py` | ✅ 已有 |
| **用户画像** | 个性化注入 | QA Agent | `user_profile_service.py` | ✅ 已有 |
| **通知推送** | 实时通知 | Ticket Agent | `notification_service.py` | ✅ 已有 |
| **豆包视觉** | 图片理解 | Image Agent | MCP 或 HTTP API | ❌ 待接入 |

### 6.2 结论

**无需新接 MCP**——现有工具已覆盖核心需求。豆包视觉 MCP 是唯一的外部新依赖。

---

## 7. 实施路线（增量）

### 7.1 步骤分解

| 步骤 | 内容 | 工作量 | 价值 | 依赖 |
|------|------|--------|------|------|
| **Step 1** | 封装 QA Agent：将 `rag_service.run_pipeline` 包装为独立 Agent 类 | 0.5d | 定义 Agent 接口契约 | 无 |
| **Step 2** | 封装 Ticket Agent：将 `ticket_automation` 包装为独立 Agent 类 | 0.5d | 复用现有状态机 | 无 |
| **Step 3** | 实现 Router：规则式分发（意图+情绪+图片） | 0.5d | 协作调度中枢 | Step 1, 2 |
| **Step 4** | 实现 Image Agent：豆包视觉 MCP 接入 | 1d | 多模态能力 | 无 |
| **Step 5** | 修改 chat.py：从单函数 → Router 编排 | 0.5d | 入口切换 | Step 3 |
| **Step 6** | 集成测试 + 回归 | 0.5d | 质量保障 | Step 5 |

**总计：~3.5d**

### 7.2 推荐执行顺序

```
Step 1 (QA Agent)  ─┐
Step 2 (Ticket Agent)─┼→ Step 3 (Router) → Step 5 (chat.py 改造) → Step 6 (测试)
Step 4 (Image Agent) ─┘（可与 Step 1-3 并行）
```

---

## 8. 与原方案（Phase 4）的关键差异

| 维度 | 原方案（v2 Phase 4） | 本方案 |
|------|---------------------|--------|
| **SharedContext 范围** | 全局状态 | 单次请求上下文 |
| **Router 位置** | 前端路由（Front-Router） | 后端请求级分发 |
| **结果合并** | Meta-Orchestrator 独立层 | Router 内直接合并 |
| **实施前提** | Phase 1-3 全部完成 | Phase 1 已完成即可开始 |
| **Agent 建设顺序** | 先建 3 个独立 agent 再协调 | 协调逻辑先行，agent 封装后续衔接 |
| **复杂度** | 高（3 层抽象） | 中（Router + Agent 两层） |
| **落地难度** | 需 3-5d | 需 ~3.5d |

---

## 9. 风险与限制

### 9.1 已知风险

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 豆包视觉 MCP 接入不稳定 | Image Agent 失败 | fail-open：图片理解失败时降级为纯文字 |
| 多 Agent 并行延迟增加 | 用户体验下降 | 设置超时（如 5s），超时后降级为单 Agent |
| Router 规则膨胀 | 维护成本上升 | 规则配置化（数据库/配置文件），避免硬编码 |
| Agent 间依赖过紧 | 耦合度高 | 通过 SharedContext 解耦，Agent 不直接调用彼此 |

### 9.2 设计上限

| 限制 | 说明 |
|------|------|
| **Agent 数量** | 当前 4 个 Agent 足够，未来新增只需实现 Agent 接口并注册到 Router |
| **协作深度** | 当前仅支持 2 层协作（Router → Agent），不支持 Agent 间直接通信 |
| **状态持久化** | SharedContext 为内存级，不持久化；跨请求状态依赖会话历史 |

---

## 10. 验收标准

### 10.1 功能验收

- [ ] QA Agent 独立运行，返回与现有 `run_pipeline()` 一致的 RagResult
- [ ] Ticket Agent 独立运行，能正确触发工单状态流转
- [ ] Image Agent 能处理图片输入，输出图片描述
- [ ] Router 正确分发：纯文字→QA，有图→Image+QA，投诉→QA+Ticket
- [ ] chat.py 改造后，原有 SSE 契约保持不变

### 10.2 测试验收

- [ ] 单元测试：各 Agent 独立测试覆盖率 > 80%
- [ ] 集成测试：多 Agent 协作场景端到端通过
- [ ] 回归测试：现有 `test_rag.py` / `test_agent_behavior.py` 全量通过

### 10.3 性能验收

- [ ] 单 Agent 响应时间 < 2s（P95）
- [ ] 双 Agent 并行响应时间 < 3s（P95）
- [ ] 图片理解响应时间 < 5s（P95）

---

## 11. 文件结构规划

```
backend/app/services/
├── agents/                     # NEW: Agent 层
│   ├── __init__.py
│   ├── base.py                 # Agent 抽象基类
│   ├── qa_agent.py             # QA Agent
│   ├── ticket_agent.py         # Ticket Agent
│   ├── image_agent.py          # Image Agent
│   └── router.py               # Router
├── shared_context.py           # NEW: SharedContext
├── pipeline.py                 # EXISTING: Pipeline 数据类
├── steps/                      # EXISTING: RAG 节点
│   ├── intent.py
│   ├── rewrite.py
│   ├── cache_check.py
│   ├── retrieve.py
│   ├── refuse.py
│   └── generate.py
├── orchestrator/               # EXISTING: PipelineRunner
│   └── __init__.py
├── rag_service.py              # EXISTING（保留兼容）
├── ticket_automation.py        # EXISTING
├── ticket_state_machine.py     # EXISTING
└── ...

backend/app/api/
├── chat.py                     # MODIFY: 内部改为调用 Router
└── ...

backend/tests/
├── test_pipeline.py            # EXISTING
├── test_pipeline_integration.py # EXISTING
├── test_agents/                # NEW: Agent 测试
│   ├── test_qa_agent.py
│   ├── test_ticket_agent.py
│   ├── test_image_agent.py
│   └── test_router.py
└── ...
```

---

**文档版本：** v1.0 | **最后更新：** 2026-08-24 | **作者：** AgentOrchestrator
