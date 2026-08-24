# Agent 架构规划书 · 灵犀项目

> 日期：2026-08-23 | 状态：设计阶段（未实施） | 项目：灵犀 Customer Service（Lingxi）

---

## Phase 1：RAG 管线可组合化

### 1.1 目标

把 `run_pipeline()` 拆成独立节点，用 Pipeline 数据类承载中间状态。不影响现有 `chat.py` 调用。

### 1.2 Pipeline 数据类

新建 `backend/app/services/pipeline.py`：

```python
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

@dataclass
class Pipeline:
    query: str
    kb_id: UUID
    user_id: str
    history: list[dict] = field(default_factory=list)

    # 中间状态（各节点写入）
    intent: str = ""
    rewritten_query: str = ""
    chunks: list = field(default_factory=list)
    dense_scores: list[float] = field(default_factory=list)
    refuse: bool = False
    refuse_reason: str = ""
    from_cache: bool = False
    cached_answer: str = ""

    # 阶段日志（调试用）
    stages: list[dict] = field(default_factory=list)
    final_answer: str = ""
    sources: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def add_stage(self, name: str, error: str | None = None):
        self.stages.append({
            "name": name, "at": datetime.now().isoformat(), "error": error
        })
```

### 1.3 节点拆分

新建 `backend/app/services/steps/` 目录，每个节点是一个纯函数：

```python
# steps/intent.py
def classify_intent(pipeline: Pipeline) -> Pipeline:
    """规则式意图分类：handoff / chitchat / qa"""
    from app.services.rag_service import classify_intent as _classify
    pipeline.intent = _classify(pipeline.query)
    pipeline.add_stage("intent")
    return pipeline

# steps/rewrite.py
def rewrite_query(pipeline: Pipeline) -> Pipeline:
    """查询改写：T9-S3 改写只服务检索与缓存 key"""
    from app.services.query_rewrite import rewrite
    rewritten, _ = rewrite(pipeline.query, pipeline.history)
    pipeline.rewritten_query = rewritten
    pipeline.add_stage("rewrite")
    return pipeline

# steps/cache_check.py
def check_cache(pipeline: Pipeline) -> Pipeline:
    """缓存命中：精确 + 语义双命中"""
    from app.services.answer_cache import get as cache_get
    from app.core.config import settings
    cached = cache_get(pipeline.rewritten_query, None, str(pipeline.kb_id))
    if cached:
        pipeline.from_cache = True
        pipeline.cached_answer = cached.get("answer", "")
        pipeline.cached_sources = cached.get("sources", [])
    pipeline.add_stage("cache_check")
    return pipeline

# steps/retrieve.py
def retrieve_chunks(pipeline: Pipeline) -> Pipeline:
    """hybrid 检索：dense + sparse + RRF"""
    from app.services.retrieval_service import search_kb
    from app.core.config import settings
    chunks = search_kb(pipeline.rewritten_query, pipeline.kb_id,
                       top_k=settings.RETRIEVAL_TOP_K)
    pipeline.chunks = chunks
    pipeline.dense_scores = [c.dense_score for c in chunks]
    pipeline.add_stage("retrieve")
    return pipeline

# steps/refuse.py
def check_refuse(pipeline: Pipeline) -> Pipeline:
    """诚实性拒答：best_dense < MIN_SCORE → refuse"""
    from app.core.config import settings
    best_dense = max(pipeline.dense_scores, default=0.0)
    if not pipeline.chunks or best_dense < settings.MIN_SCORE:
        pipeline.refuse = True
        pipeline.refuse_reason = "未找到可靠依据"
    # 降噪：低分近义片段不进 prompt
    pipeline.chunks = [c for c in pipeline.chunks
                       if c.dense_score >= settings.MIN_SCORE]
    pipeline.add_stage("refuse_check")
    return pipeline

# steps/generate.py
async def generate_answer(pipeline: Pipeline) -> Pipeline:
    """LLM 流式生成"""
    from app.prompts.qa_prompt import build_qa_messages
    from app.llm_clients.chat import get_chat_client
    messages = build_qa_messages(
        query=pipeline.query,
        chunks=pipeline.chunks,
        history=pipeline.history,
    )
    client = get_chat_client()
    answer_parts = []
    async for delta in client.stream(messages):
        answer_parts.append(delta)
    pipeline.final_answer = "".join(answer_parts)
    pipeline.add_stage("generate")
    return pipeline
```

### 1.4 向后兼容

修改 `rag_service.py`，内部调 Pipeline：

```python
def run_pipeline(query, kb_id, top_k=None, history=None, kb_version=None):
    from app.services.pipeline import Pipeline
    from app.services.steps import intent, rewrite, retrieve, refuse
    p = Pipeline(query=query, kb_id=kb_id, history=history or [])
    p = intent.classify_intent(p)
    if p.intent == "qa":
        p = rewrite.rewrite_query(p)
        p = retrieve.retrieve_chunks(p)
        p = refuse.check_refuse(p)
    # 映射回 RagResult（chat.py 现有调用不变）
    return RagResult(
        intent=p.intent, chunks=p.chunks, refuse=p.refuse,
        refuse_reason=p.refuse_reason, from_cache=p.from_cache,
        cached_answer=p.cached_answer, rewritten_query=p.rewritten_query,
    )
```

### 1.5 文件变更清单

| 文件 | 操作 |
|---|---|
| `backend/app/services/pipeline.py` | 新增 |
| `backend/app/services/steps/__init__.py` | 新增 |
| `backend/app/services/steps/intent.py` | 新增 |
| `backend/app/services/steps/rewrite.py` | 新增 |
| `backend/app/services/steps/cache_check.py` | 新增 |
| `backend/app/services/steps/retrieve.py` | 新增 |
| `backend/app/services/steps/refuse.py` | 新增 |
| `backend/app/services/steps/generate.py` | 新增 |
| `backend/app/services/rag_service.py` | 修改 |

### 1.6 验证方式

- 对比 `run_pipeline()` 前后返回的 `RagResult` 字段一致
- `pytest tests/` 全量通过
- 新增 `tests/test_steps.py` 覆盖各节点

---

## Phase 2：多模态接入（图片理解）

### 2.1 目标

用户发图片 + 可选文字 → 系统理解图片内容 → 融合文字进主线。

### 2.2 图片理解 API

新建 `backend/app/services/image_agent.py`：

```python
from __future__ import annotations
import logging

logger = logging.getLogger(__name__)

async def understand_image(image_base64: str, text_query: str = "") -> dict:
    """图片理解入口：豆包视觉 + 文字优先融合

    返回：{"query": str, "image_desc": str, "confidence": float, "error": str | None}
    """
    try:
        desc = await _call_doubao_vision(image_base64)
    except Exception as e:
        logger.warning("图片理解失败: %s", e)
        return {"query": text_query, "image_desc": "", "confidence": 0.0, "error": str(e)}

    if text_query:
        return {"query": text_query, "image_desc": desc, "confidence": 0.9, "error": None}
    return {"query": desc, "image_desc": desc, "confidence": 0.7, "error": None}

async def _call_doubao_vision(image_base64: str) -> str:
    """调豆包视觉 MCP 或 HTTP API（接入方式待确认）"""
    raise NotImplementedError("需确认豆包视觉接入方式")
```

### 2.3 chat.py 入口分流

```python
# chat.py chat_stream() 新增逻辑
if req.image_base64:
    yield _sse({"event": "stage", "data": {"stage": "understanding", "msg": "正在理解图片..."}})
    img_result = await understand_image(req.image_base64, req.content)
    if img_result["error"]:
        yield _sse({"event": "error", "data": {"code": "IMAGE_ERROR", "message": "图片理解失败"}})
    if not req.content:
        req.content = img_result["query"]
```

### 2.4 SSE 事件扩展

```python
# chat.py _SSE_EVENTS 新增
_SSE_EVENTS = frozenset({
    "stage", "intent", "token", "sources", "done", "error",
    "understanding",  # 图片理解中
})
```

### 2.5 前端适配

```typescript
// frontend/src/api/chat.ts
export interface ChatStreamReq {
  session_id: string;
  content: string;
  stream?: boolean;
  client_msg_id?: string | null;
  image_base64?: string | null;
}

// frontend/src/components/chat/ChatInput.tsx
// Upload 组件 + base64 转换 + 图片预览
```

### 2.6 文件变更清单

| 文件 | 操作 |
|---|---|
| `backend/app/services/image_agent.py` | 新增 |
| `backend/app/api/chat.py` | 修改 |
| `frontend/src/api/chat.ts` | 修改 |
| `frontend/src/components/chat/ChatInput.tsx` | 修改 |
| `frontend/src/pages/ChatPage.tsx` | 修改 |

### 2.7 验证方式

- 单元测试 mock 豆包视觉返回，确认接口契约
- 手动发图 + 文字，确认 query 不被覆盖
- 前端预览正常

---

## Phase 3：工单智能 Agent

### 3.1 目标

在现有工单状态机之上加一层"大脑"：自动识别情绪 → 选择策略 → 执行操作。

### 3.2 决策模型

```python
# backend/app/services/ticket_agent.py（待建）
from enum import StrEnum
from dataclasses import dataclass

class Emotion(StrEnum):
    ANGRY = "angry"
    UNHAPPY = "unhappy"
    CALM = "calm"

class IssueType(StrEnum):
    QUALITY = "quality"
    LOGISTICS = "logistics"
    AFTER_SALES = "after_sales"
    OTHER = "other"

class Strategy(StrEnum):
    COMFORT = "comfort"
    GIVE_SOLUTION = "solution"
    TRANSFER = "transfer"

@dataclass
class TicketDecision:
    emotion: Emotion
    issue_type: IssueType
    strategy: Strategy
    confidence: float
    reason: str

def decide_strategy(emotion: Emotion, issue_type: IssueType, user_history: dict) -> TicketDecision:
    """规则引擎决策"""
    if emotion == Emotion.ANGRY:
        return TicketDecision(emotion, issue_type, Strategy.TRANSFER, 0.95, "用户情绪愤怒，立即转人工")
    if emotion == Emotion.UNHAPPY and issue_type == IssueType.QUALITY:
        return TicketDecision(emotion, issue_type, Strategy.COMFORT, 0.85, "质量问题+不满情绪，安抚并给补偿券")
    if issue_type == IssueType.LOGISTICS:
        return TicketDecision(emotion, issue_type, Strategy.GIVE_SOLUTION, 0.80, "物流问题，给方案")
    if user_history.get("ltv", 0) > 1000:
        return TicketDecision(emotion, issue_type, Strategy.TRANSFER, 0.75, "高价值用户，优先转人工")
    return TicketDecision(emotion, issue_type, Strategy.GIVE_SOLUTION, 0.60, "默认策略：给方案")
```

### 3.3 工单 Agent 入口

```python
async def run_ticket_agent(db, session_id, user_id, message: str) -> TicketDecision:
    """工单智能决策入口"""
    emotion = await _classify_emotion(message)
    issue_type = await _classify_issue(message)
    user_history = await _get_user_history(db, user_id)
    decision = decide_strategy(emotion, issue_type, user_history)

    # 执行：调已有状态机
    if decision.strategy == Strategy.TRANSFER:
        from app.api.tickets import ensure_active_ticket
        await ensure_active_ticket(db, session_id, priority="high")

    # 记录决策日志
    db.add(TicketDecisionLog(
        session_id=session_id,
        emotion=decision.emotion.value,
        issue_type=decision.issue_type.value,
        strategy=decision.strategy.value,
        confidence=decision.confidence,
        reason=decision.reason,
    ))
    db.commit()
    return decision
```

### 3.4 决策日志表

```python
# backend/app/models/ticket_decision.py
class TicketDecisionLog(Base):
    __tablename__ = "ticket_decision_logs"
    id = mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    tenant_id = tenant_id_column()
    session_id = mapped_column(sa.Uuid(), sa.ForeignKey("sessions.id"))
    emotion = mapped_column(sa.String(16), nullable=False)
    issue_type = mapped_column(sa.String(16), nullable=False)
    strategy = mapped_column(sa.String(16), nullable=False)
    confidence = mapped_column(sa.Float(), nullable=False)
    reason = mapped_column(sa.Text(), nullable=False)
    user_feedback = mapped_column(sa.Integer(), nullable=True)
    created_at = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())
```

### 3.5 文件变更清单

| 文件 | 操作 |
|---|---|
| `backend/app/services/ticket_agent.py` | 新增 |
| `backend/app/services/ticket_tools.py` | 新增 |
| `backend/app/models/ticket_decision.py` | 新增 |
| `backend/app/schemas/ticket_decision.py` | 新增 |
| `backend/app/api/tickets.py` | 修改 |

### 3.6 验证方式

- 单元测试覆盖各 emotion × issue_type 组合
- 集成测试：转人工 → 确认 ticket 创建
- 查 `ticket_decision_logs` 表确认日志落库

---

## Phase 4：多 Agent 协调

### 4.1 目标

Front-Router 分发 + Meta-Orchestrator 跨域协作 + SharedContext 共享状态。

### 4.2 实施前提

**Phase 4 依赖 Phase 1-3 先完成**。在没有独立 agent 之前，"多 Agent 协调"没有主体。

### 4.3 SharedContext

```python
# backend/app/services/shared_context.py
@dataclass
class SharedContext:
    """所有 agent 共享的上下文"""
    query: str
    image_base64: str | None
    user_id: str
    history: list[dict] = field(default_factory=list)

    # 各 agent 写入
    image_description: str = ""
    intent: str = ""
    emotion: str = ""
    ticket_action: str = ""
    rag_answer: str = ""

    # 输出
    final_answer: str = ""
    sources: list = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def add_error(self, agent_name: str, error: str):
        self.errors.append(f"[{agent_name}] {error}")
```

### 4.4 Front-Router

```python
# backend/app/services/router.py
async def route(ctx: SharedContext) -> SharedContext:
    """规则式分发：有图→image, 投诉→ticket, 默认→rag"""
    if ctx.image_base64:
        img_result = await understand_image(ctx.image_base64, ctx.query)
        ctx.image_description = img_result["image_desc"]
        if img_result["error"]:
            ctx.add_error("image_agent", img_result["error"])
        if not ctx.query:
            ctx.query = img_result["query"]

    complaint_keywords = ("投诉", "退钱", "差评", "赔偿")
    if any(k in ctx.query for k in complaint_keywords):
        decision = await run_ticket_agent(ctx)
        ctx.ticket_action = decision.strategy

    legal_keywords = ("法律", "法条", "权益", "消费者", "违反")
    if any(k in ctx.query for k in legal_keywords):
        ctx = await run_rag_agent(ctx, enable_citation=True)

    if not ctx.rag_answer and not ctx.ticket_action:
        ctx = await run_rag_agent(ctx)

    return ctx
```

### 4.5 Meta-Orchestrator

```python
# backend/app/services/meta_orchestrator.py
async def orchestrate(ctx: SharedContext) -> SharedContext:
    """结果合并 + 部分返回"""
    parts = []
    if ctx.image_description:
        parts.append(f"[图片内容] {ctx.image_description}")
    if ctx.rag_answer:
        parts.append(f"[知识库回答] {ctx.rag_answer}")
    if ctx.ticket_action:
        parts.append(f"[工单处理] {ctx.ticket_action}")

    ctx.final_answer = "\n\n".join(parts) if parts else "抱歉，暂时无法处理您的请求，已转人工。"

    if ctx.errors:
        ctx.final_answer += "\n\n[提示] 部分功能处理异常：\n" + "\n".join(f"- {e}" for e in ctx.errors)

    return ctx
```

### 4.6 文件变更清单

| 文件 | 操作 |
|---|---|
| `backend/app/services/shared_context.py` | 新增 |
| `backend/app/services/router.py` | 新增 |
| `backend/app/services/meta_orchestrator.py` | 新增 |
| `backend/app/services/rag_agent.py` | 新增 |
| `backend/app/api/chat.py` | 修改 |

### 4.7 验证方式

- 文本问答：走 RAG Agent 主线
- 图片+文字：Image Agent → RAG Agent 串联
- 投诉：Ticket Agent 建单 + 状态流转
- 跨域协作：三个 agent 结果合并

---

## 文件结构总览

```
backend/app/services/
├── pipeline.py              # Phase 1: Pipeline 数据类
├── builder.py               # Phase 1: 场景化组装（可选）
├── steps/                   # Phase 1: 独立节点
│   ├── intent.py
│   ├── rewrite.py
│   ├── cache_check.py
│   ├── retrieve.py
│   ├── refuse.py
│   └── generate.py
├── image_agent.py           # Phase 2: 图片理解
├── ticket_agent.py          # Phase 3: 工单决策
├── ticket_tools.py          # Phase 3: 工具集
├── shared_context.py        # Phase 4: 共享上下文
├── router.py                # Phase 4: 前端路由
├── meta_orchestrator.py     # Phase 4: 编排器
├── rag_agent.py             # Phase 4: RAG Agent 封装
├── rag_service.py           # 已有（保留兼容）
├── retrieval_service.py     # 已有
├── ticket_state_machine.py  # 已有
└── ticket_automation.py     # 已有

backend/app/models/
├── ticket_decision.py       # Phase 3: 决策日志表
├── ticket.py                # 已有
└── ...

backend/app/api/
├── chat.py                  # Phase 2/4: 入口分流
└── tickets.py               # Phase 3: 新增 agent-decision 端点

frontend/src/
├── api/chat.ts              # Phase 2: image_base64 字段
├── components/chat/
│   └── ChatInput.tsx        # Phase 2: 图片上传 + 预览
└── pages/ChatPage.tsx       # Phase 2: understanding 阶段 UI
```

---

**文档版本：** v2.0 | **最后更新：** 2026-08-23
