# Agent 架构规划书 · 灵犀项目

> 日期：2026-08-23  
> 状态：设计阶段（未实施）  
> 项目：灵犀 Customer Service（Lingxi）  
> 基于代码阅读：`rag_service.py`、`ticket_state_machine.py`、`retrieval_service.py`、`chat.py`、`eval.py`

---

## 一、项目背景

当前灵犀 Customer Service 的 RAG 管线是**过程式硬编码**：

```python
# rag_service.py 现状：一个函数里写完整个流程
def run_pipeline(query, kb_id, top_k=None, history=None):
    intent = classify_intent(query)          # 1. 意图
    rewritten, _meta = rewrite(query, history) # 2. 改写
    cached = cache_get(rewritten, ...)        # 3. 缓存
    if cached: return result
    chunks = search_kb(rewritten, kb_id)      # 4. 检索
    # 5. 拒答判定
    # 6. 过滤
    return result
```

**痛点：**

- 加新节点（如 rerank、citation_check）需改函数内部，怕影响现有逻辑
- 调试时不知道卡在哪个阶段
- 不同场景（售前/工单/学习辅导）需要不同编排，当前一套代码通吃
- 无法独立测试某个节点

---

## 二、总体规划

```
Phase 1（P0）：RAG 管线可组合化
    ↓
Phase 2（P1）：多模态接入（图片理解）
    ↓
Phase 3（P2）：工单智能 Agent
    ↓
Phase 4（P3）：多 Agent 协调（Meta-Orchestrator）
    ↓
Phase 5（P4）：前端适配（图片上传 + 多阶段 UI）
```

---

## 三、Phase 1：RAG 管线可组合化

### 3.1 目标

把 `run_pipeline()` 拆成独立节点，用 Pipeline 数据类承载中间状态，builder 按需组装不同管线。

### 3.2 架构图

Clipboard_Screenshot.png

我的左侧导航栏没了

### 3.3 Pipeline 数据类

```python
# backend/app/services/pipeline.py
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

@dataclass
class PipelineStage:
    name: str
    started_at: datetime
    finished_at: datetime | None = None
    error: str | None = None

@dataclass
class Pipeline:
    """管线上下文：承载所有中间状态 + 阶段日志"""
    # 输入
    query: str
    kb_id: UUID
    user_id: str
    history: list[dict] = field(default_factory=list)

    # 中间状态
    intent: str = ""
    rewritten_query: str = ""
    chunks: list = field(default_factory=list)
    dense_scores: list[float] = field(default_factory=list)
    refuse: bool = False
    refuse_reason: str = ""
    from_cache: bool = False
    cached_answer: str = ""

    # 阶段日志（调试/排障用）
    stages: list[PipelineStage] = field(default_factory=list)

    # 输出
    final_answer: str = ""
    sources: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def add_stage(self, name: str, started: datetime, error: str | None = None):
        self.stages.append(PipelineStage(
            name=name, started_at=started,
            finished_at=datetime.now(), error=error
        ))
```

### 3.4 节点基约定

```python
# backend/app/services/steps/intent.py
from app.services.pipeline import Pipeline

def classify_intent(pipeline: Pipeline) -> Pipeline:
    """规则式意图分类：handoff / chitchat / qa"""
    from app.services.rag_service import classify_intent as _classify
    pipeline.intent = _classify(pipeline.query)
    pipeline.add_stage("intent", datetime.now())
    return pipeline

# backend/app/services/steps/rewrite.py
def rewrite_query(pipeline: Pipeline) -> Pipeline:
    """查询改写：T9-S3 改写只服务检索与缓存 key"""
    from app.services.query_rewrite import rewrite
    rewritten, _meta = rewrite(pipeline.query, pipeline.history)
    pipeline.rewritten_query = rewritten
    pipeline.add_stage("rewrite", datetime.now())
    return pipeline

# backend/app/services/steps/retrieve.py
def retrieve_chunks(pipeline: Pipeline) -> Pipeline:
    """hybrid 检索：dense + sparse + RRF"""
    from app.services.retrieval_service import search_kb
    from app.core.config import settings
    chunks = search_kb(pipeline.rewritten_query, pipeline.kb_id,
                       top_k=settings.RETRIEVAL_TOP_K)
    pipeline.chunks = chunks
    pipeline.dense_scores = [c.dense_score for c in chunks]
    pipeline.add_stage("retrieve", datetime.now())
    return pipeline

# backend/app/services/steps/refuse.py
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
    pipeline.add_stage("refuse_check", datetime.now())
    return pipeline

# backend/app/services/steps/generate.py
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
    pipeline.add_stage("generate", datetime.now())
    return pipeline
```

### 3.5 场景化组装（builder.py）

```python
# backend/app/services/builder.py
from app.services.pipeline import Pipeline
from app.services.steps import intent, rewrite, retrieve, cache_check, refuse, generate

def sales_pipeline(query, kb_id, user_id, history) -> Pipeline:
    """售前问答：标准流程"""
    p = Pipeline(query=query, kb_id=kb_id, user_id=user_id, history=history)
    p = intent.classify_intent(p)
    if p.intent != "qa":
        return p
    p = rewrite.rewrite_query(p)
    p = cache_check.check_cache(p)
    if p.from_cache:
        return p
    p = retrieve.retrieve_chunks(p)
    p = refuse.check_refuse(p)
    return p

def ticket_pipeline(query, kb_id, user_id, history) -> Pipeline:
    """工单辅助：加ticket_history查询"""
    p = sales_pipeline(query, kb_id, user_id, history)
    # 额外：查历史工单
    from app.services.steps import ticket_history
    p = ticket_history.lookup(p)
    return p

def study_pipeline(query, kb_id, user_id, history) -> Pipeline:
    """学习辅导：加citation_check"""
    p = sales_pipeline(query, kb_id, user_id, history)
    # 额外：引用验证
    from app.services.steps import citation_check
    p = citation_check.verify(p)
    return p
```

### 3.6 文件结构

```
backend/app/services/
├── pipeline.py              # Pipeline 数据类 + 阶段日志
├── builder.py               # 场景化组装
├── steps/
│   ├── __init__.py
│   ├── intent.py            # 意图分类
│   ├── rewrite.py           # 查询改写
│   ├── retrieve.py          # 向量检索
│   ├── cache_check.py       # 缓存命中
│   ├── refuse.py            # 拒答判定
│   ├── generate.py          # LLM 生成
│   ├── ticket_history.py    # 工单历史（可选）
│   └── citation_check.py    # 引用验证（可选）
├── rag_service.py           # 保留（向后兼容，内部调 builder）
└── retrieval_service.py     # 保留（检索侧独立）
```

### 3.7 与现有代码兼容

```python
# rag_service.py 改为调 builder（向后兼容）
def run_pipeline(query, kb_id, top_k=None, history=None, kb_version=None):
    from app.services.builder import sales_pipeline
    p = sales_pipeline(query, kb_id, user_id="", history=history)
    # 映射回 RagResult 供现有 chat.py 使用
    return RagResult(
        intent=p.intent,
        chunks=p.chunks,
        refuse=p.refuse,
        refuse_reason=p.refuse_reason,
        from_cache=p.from_cache,
        cached_answer=p.cached_answer,
        rewritten_query=p.rewritten_query,
    )
```

---

## 四、Phase 2：多模态接入（图片理解）

### 4.1 目标

用户发图片 + 可选文字 → 系统理解图片内容 → 融合文字进主线。

### 4.2 架构图

```
用户发图（base64）+ 文字"这个有货吗"
        │
        ▼
┌─────────────────────────────┐
│  image_agent.py              │
│  1. 调豆包视觉：图片→文字描述  │
│  2. 文字优先：文字当 query     │
│  3. 图片描述注入 context      │
└─────────────────────────────┘
        │
        ▼
┌─────────────────────────────┐
│  Pipeline（主线）             │
│  query = 用户文字             │
│  context_hint = 图片描述      │
│  正常走 intent→rewrite→...   │
└─────────────────────────────┘
```

### 4.3 技术细节

```python
# backend/app/services/image_agent.py
from __future__ import annotations
import base64
import logging

logger = logging.getLogger(__name__)

async def understand_image(image_base64: str, text_query: str = "") -> dict:
    """图片理解入口：豆包视觉 + 文字优先融合

    返回：
    {
        "query": str,           # 最终 query（文字优先）
        "image_desc": str,      # 图片描述（注入 context）
        "confidence": float,    # 理解置信度
        "error": str | None,    # 失败信息
    }
    """
    # 1. 调豆包视觉模型
    try:
        desc = await _call_doubao_vision(image_base64)
    except Exception as e:
        logger.warning("图片理解失败: %s", e)
        return {
            "query": text_query,
            "image_desc": "",
            "confidence": 0.0,
            "error": str(e),
        }

    # 2. 文字优先融合
    if text_query:
        # 有文字 → 文字当 query，图片描述当 context
        return {
            "query": text_query,
            "image_desc": desc,
            "confidence": 0.9,
            "error": None,
        }
    else:
        # 无文字 → 图片描述当 query
        return {
            "query": desc,
            "image_desc": desc,
            "confidence": 0.7,
            "error": None,
        }

async def _call_doubao_vision(image_base64: str) -> str:
    """调豆包视觉模型

    豆包视觉 API 细节需查阅官方文档：
    - 支持 base64 输入
    - 建议参数：prompt="详细描述图片内容"
    - 返回：文字描述
    """
    # TODO: 接入豆包视觉 SDK
    # 临时占位，实际需调 MCP 或 HTTP API
    raise NotImplementedError("需接入豆包视觉模型")
```

### 4.4 SSE 事件扩展

```python
# chat.py 新增事件类型
_SSE_EVENTS = frozenset({
    "stage", "intent", "token", "sources", "done", "error",
    "understanding",  # 新增：图片理解中
})

# 流式响应新增阶段
yield ("stage", {"stage": "understanding", "msg": "正在理解图片..."})
# ... 图片理解完成后 ...
yield ("stage", {"stage": "retrieving", "msg": "已检索知识库"})
```

### 4.5 前端适配

```typescript
// frontend/src/api/chat.ts
export interface ChatStreamReq {
  session_id: string;
  content: string;
  stream?: boolean;
  client_msg_id?: string | null;
  image_base64?: string | null;  // 新增：图片 base64
}

// frontend/src/components/chat/ChatInput.tsx
// 新增：图片上传按钮 + 预览
// 发送时：如果有图，把图转 base64 放入 image_base64 字段
```

### 4.6 文件结构

```
backend/app/services/
├── image_agent.py          # 图片理解入口（新增）
└── steps/
    └── ...                 # 同上

frontend/src/
├── api/chat.ts             # 新增 image_base64 字段
├── components/chat/
│   └── ChatInput.tsx       # 新增图片上传 + 预览
└── pages/ChatPage.tsx      # 处理 understanding 阶段 UI
```

---

## 五、Phase 3：工单智能 Agent

### 5.1 目标

在现有工单状态机之上加一层"大脑"：自动识别情绪 → 选择策略 → 执行操作。

### 5.2 架构图

```
用户消息（投诉/咨询）
        │
        ▼
┌─────────────────────────────────────────────────────┐
│  ticket_agent.py（决策层）                             │
│  1. 情绪识别（小模型）→ 愤怒/不满/平静                  │
│  2. 问题分类（小模型）→ 质量/物流/售后/其他             │
│  3. 策略选择（规则引擎）→ 安抚/给方案/转人工/建单        │
└─────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────┐
│  ticket_tools.py（执行层）                             │
│  - 查用户历史工单                                      │
│  - 查用户画像（购买力/历史投诉）                        │
│  - 执行状态转换（调 ticket_state_machine.transition）   │
│  - 查知识库（给方案时检索话术）                          │
└─────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────┐
│  ticket_state_machine.py（执行层，已有，不动）           │
│  CAS 写 + 版本号 + 幂等                               │
└─────────────────────────────────────────────────────┘
```

### 5.3 决策模型：小模型 + 规则兜底

```python
# backend/app/services/ticket_agent.py
from __future__ import annotations
import logging
from enum import StrEnum
from dataclasses import dataclass

from app.services.ticket_state_machine import transition
from app.models.ticket import TicketStatus

logger = logging.getLogger(__name__)

class Emotion(StrEnum):
    ANGRY = "angry"
    UNHAPPY = "unhappy"
    CALM = "calm"

class IssueType(StrEnum):
    QUALITY = "quality"
    LOGISTICS = "logistics"
    AFTER_SALES = "after_sales"
    OTHER = "other"

class Strategy(StrENUM):
    COMFORT = "comfort"          # 安抚 + 补偿券
    GIVE_SOLUTION = "solution"   # 给方案（知识库检索）
    TRANSFER = "transfer"        # 转人工
    CREATE_TICKET = "ticket"     # 建单

@dataclass
class TicketDecision:
    emotion: Emotion
    issue_type: IssueType
    strategy: Strategy
    confidence: float
    reason: str

def decide_strategy(
    emotion: Emotion,
    issue_type: IssueType,
    user_history: dict,
) -> TicketDecision:
    """规则引擎决策：情绪 + 问题类型 + 用户历史 → 策略"""

    # 情绪愤怒 → 立即转人工（不犹豫）
    if emotion == Emotion.ANGRY:
        return TicketDecision(
            emotion=emotion, issue_type=issue_type,
            strategy=Strategy.TRANSFER,
            confidence=0.95, reason="用户情绪愤怒，立即转人工",
        )

    # 情绪不满 + 质量问题 → 安抚 + 补偿
    if emotion == Emotion.UNHAPPY and issue_type == IssueType.QUALITY:
        return TicketDecision(
            emotion=emotion, issue_type=issue_type,
            strategy=Strategy.COMFORT,
            confidence=0.85, reason="质量问题+不满情绪，安抚并给补偿券",
        )

    # 物流问题 → 给方案（查物流话术）
    if issue_type == IssueType.LOGISTICS:
        return TicketDecision(
            emotion=emotion, issue_type=issue_type,
            strategy=Strategy.GIVE_SOLUTION,
            confidence=0.80, reason="物流问题，给方案",
        )

    # 高价值用户 → 优先转人工
    if user_history.get("ltv", 0) > 1000:
        return TicketDecision(
            emotion=emotion, issue_type=issue_type,
            strategy=Strategy.TRANSFER,
            confidence=0.75, reason="高价值用户，优先转人工",
        )

    # 默认：给方案
    return TicketDecision(
        emotion=emotion, issue_type=issue_type,
        strategy=Strategy.GIVE_SOLUTION,
        confidence=0.60, reason="默认策略：给方案",
    )

async def run_ticket_agent(
    db,
    session_id: str,
    user_id: str,
    message: str,
) -> TicketDecision:
    """工单 Agent 入口"""
    # 1. 情绪识别（小模型，待选型）
    emotion = await _classify_emotion(message)

    # 2. 问题分类（小模型，待选型）
    issue_type = await _classify_issue(message)

    # 3. 查用户历史（tool）
    user_history = await _get_user_history(db, user_id)

    # 4. 规则决策
    decision = decide_strategy(emotion, issue_type, user_history)

    # 5. 执行策略
    await _execute_strategy(db, session_id, decision)

    return decision

async def _classify_emotion(message: str) -> Emotion:
    """情绪识别：小模型或规则"""
    # 先用规则兜底，后续接小模型
    angry_keywords = ("投诉", "退钱", "赔偿", "差评", "骗子", "欺诈")
    unhappy_keywords = ("太慢", "不好", "失望", "不满意", "差")

    if any(k in message for k in angry_keywords):
        return Emotion.ANGRY
    if any(k in message for k in unhappy_keywords):
        return Emotion.UNHAPPY
    return Emotion.CALM

async def _classify_issue(message: str) -> IssueType:
    """问题分类：小模型或规则"""
    quality_keywords = ("质量", "坏了", "破损", " defective", "不合格")
    logistics_keywords = ("物流", "快递", "配送", "发货", "送货")
    after_sales_keywords = ("退货", "退款", "换货", "保修", "售后")

    if any(k in message for k in quality_keywords):
        return IssueType.QUALITY
    if any(k in message for k in logistics_keywords):
        return IssueType.LOGISTICS
    if any(k in message for k in after_sales_keywords):
        return IssueType.AFTER_SALES
    return IssueType.OTHER

async def _execute_strategy(db, session_id: str, decision: TicketDecision):
    """执行策略：调状态机 + 工具"""
    if decision.strategy == Strategy.TRANSFER:
        # 转人工：建单 + 标高优
        from app.api.tickets import ensure_active_ticket
        await ensure_active_ticket(db, session_id, priority="high")
    elif decision.strategy == Strategy.COMFORT:
        # 安抚：发安抚话术 + 补偿券
        pass  # 调补偿券系统
    elif decision.strategy == Strategy.GIVE_SOLUTION:
        # 给方案：知识库检索话术
        pass  # 调知识库

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
```

### 5.4 监控日志表

```python
# backend/app/models/ticket_decision.py
class TicketDecisionLog(Base):
    __tablename__ = "ticket_decision_logs"

    id = mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    tenant_id = tenant_id_column()
    session_id = mapped_column(sa.Uuid(), sa.ForeignKey("sessions.id"), nullable=False)
    emotion = mapped_column(sa.String(16), nullable=False)
    issue_type = mapped_column(sa.String(16), nullable=False)
    strategy = mapped_column(sa.String(16), nullable=False)
    confidence = mapped_column(sa.Float(), nullable=False)
    reason = mapped_column(sa.Text(), nullable=False)
    user_feedback = mapped_column(sa.Integer(), nullable=True)  # 1=点赞, -1=踩
    created_at = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())
```

### 5.5 文件结构

```
backend/app/services/
├── ticket_agent.py          # Agent 决策入口（新增）
├── ticket_tools.py          # 工具集封装（新增）
├── ticket_monitor.py        # 监控日志查询（新增）
├── ticket_state_machine.py  # 已有（不动）
└── ticket_automation.py     # 已有（不动）

backend/app/models/
├── ticket_decision.py       # 决策记录表（新增）
└── ticket.py                # 已有

backend/app/schemas/
└── ticket_decision.py       # schema（新增）

backend/app/api/
└── tickets.py               # 新增 /tickets/{id}/agent-decision 端点
```

---

## 六、Phase 4：多 Agent 协调

### 6.1 目标

Front-Router 分发 + Meta-Orchestrator 跨域协作 + SharedContext 共享状态。

### 6.2 架构图

```
用户输入（文字/图/投诉/法律咨询）
        │
        ▼
┌─────────────────────────────────────────────────────┐
│  router.py（Front-Router）                            │
│  规则式分类：                                          │
│    - 有图 → image_agent → 结果回主线                   │
│    - 投诉词 → ticket_agent                            │
│    - 法律疑问词 → rag_agent + citation_check           │
│    - 混合/复杂 → 升级到 meta_orchestrator               │
└─────────────────────────────────────────────────────┘
        │
        ├── 单 agent → 直接返回结果
        │
        ▼
┌─────────────────────────────────────────────────────┐
│  meta_orchestrator.py（Meta-Orchestrator）             │
│  结果合并：                                            │
│    收集 image_desc + rag_answer + ticket_action        │
│    → 合并为统一响应                                    │
│  部分返回：                                            │
│    任一 agent 失败 → 返回已完成部分 + 错误提示           │
└─────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────┐
│  SharedContext                                        │
│  {                                                    │
│    query, image_base64, user_id, history,             │
│    image_description: "",                             │
│    intent: "", emotion: "",                           │
│    rag_chunks: [], rag_answer: "",                    │
│    ticket_action: "",                                 │
│    final_answer: "", sources: [],                     │
│    errors: []                                         │
│  }                                                    │
└─────────────────────────────────────────────────────┘
```

### 6.3 技术细节

```python
# backend/app/services/shared_context.py
from __future__ import annotations
from dataclasses import dataclass, field
from uuid import UUID

@dataclass
class SharedContext:
    """所有 agent 共享的上下文"""
    # 输入
    query: str
    image_base64: str | None
    user_id: str
    history: list[dict] = field(default_factory=list)

    # 中间结果（各 agent 写入）
    image_description: str = ""
    intent: str = ""
    emotion: str = ""
    ticket_action: str = ""
    rag_chunks: list = field(default_factory=list)
    rag_answer: str = ""

    # 输出
    final_answer: str = ""
    sources: list = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def add_error(self, agent_name: str, error: str):
        self.errors.append(f"[{agent_name}] {error}")

# backend/app/services/router.py
from app.services.shared_context import SharedContext
from app.services.image_agent import understand_image
from app.services.ticket_agent import run_ticket_agent
from app.services.rag_agent import run_rag_agent

async def route(ctx: SharedContext) -> SharedContext:
    """Front-Router：规则式分发"""
    # 1. 有图 → image_agent
    if ctx.image_base64:
        img_result = await understand_image(ctx.image_base64, ctx.query)
        ctx.image_description = img_result["image_desc"]
        if img_result["error"]:
            ctx.add_error("image_agent", img_result["error"])
        if not ctx.query:
            ctx.query = img_result["query"]

    # 2. 投诉词 → ticket_agent
    complaint_keywords = ("投诉", "退钱", "差评", "赔偿")
    if any(k in ctx.query for k in complaint_keywords):
        decision = await run_ticket_agent(ctx)
        ctx.ticket_action = decision.strategy

    # 3. 法律咨询词 → rag_agent + citation_check
    legal_keywords = ("法律", "法条", "权益", "消费者", "违反")
    if any(k in ctx.query for k in legal_keywords):
        ctx = await run_rag_agent(ctx, enable_citation=True)

    # 4. 默认 → rag_agent 主线
    if not ctx.rag_answer and not ctx.ticket_action:
        ctx = await run_rag_agent(ctx)

    return ctx

# backend/app/services/meta_orchestrator.py
from app.services.shared_context import SharedContext

async def orchestrate(ctx: SharedContext) -> SharedContext:
    """Meta-Orchestrator：结果合并 + 部分返回"""

    # 1. 收集各 agent 结果
    parts = []
    if ctx.image_description:
        parts.append(f"[图片内容] {ctx.image_description}")
    if ctx.rag_answer:
        parts.append(f"[知识库回答] {ctx.rag_answer}")
    if ctx.ticket_action:
        parts.append(f"[工单处理] {ctx.ticket_action}")

    # 2. 合并
    if parts:
        ctx.final_answer = "\n\n".join(parts)
    else:
        ctx.final_answer = "抱歉，暂时无法处理您的请求，已转人工。"

    # 3. 部分返回提示
    if ctx.errors:
        ctx.final_answer += "\n\n[提示] 部分功能处理异常："
        for e in ctx.errors:
            ctx.final_answer += f"\n- {e}"

    return ctx
```

### 6.4 文件结构

```
backend/app/services/
├── shared_context.py        # SharedContext 数据结构（新增）
├── router.py                # Front-Router（新增）
├── meta_orchestrator.py     # Meta-Orchestrator（新增）
├── image_agent.py           # 图片 Agent（新增）
├── ticket_agent.py          # 工单 Agent（新增）
├── rag_agent.py             # RAG Agent（新增，封装 pipeline.py）
└── builder.py               # 已有
```

---

## 七、Phase 5：前端适配

### 7.1 目标

支持图片上传 + 多阶段 SSE 事件展示。

### 7.2 技术细节

```typescript
// frontend/src/api/chat.ts
export interface ChatStreamReq {
  session_id: string;
  content: string;
  stream?: boolean;
  client_msg_id?: string | null;
  image_base64?: string | null;  // 新增
}

// frontend/src/components/chat/ChatInput.tsx
import { Upload, message } from 'antd';
import { CameraOutlined } from '@ant-design/icons';

export function ChatInput({ onSend }: { onSend: (req: ChatStreamReq) => void }) {
  const [imageBase64, setImageBase64] = useState<string | null>(null);

  const handleImageUpload = (file: File) => {
    const reader = new FileReader();
    reader.onload = () => setImageBase64(reader.result as string);
    reader.readAsDataURL(file);
    return false; // 阻止自动上传
  };

  return (
    <div className="chat-input">
      {imageBase64 && (
        <div className="image-preview">
          <img src={imageBase64} alt="预览" />
          <button onClick={() => setImageBase64(null)}>移除</button>
        </div>
      )}
      <Upload accept="image/*" showUploadList={false} beforeUpload={handleImageUpload}>
        <CameraOutlined />
      </Upload>
      <input type="text" placeholder="输入问题..." />
      <button onClick={() => onSend({ session_id, content, image_base64 })}>发送</button>
    </div>
  );
}

// frontend/src/pages/ChatPage.tsx
// 处理新增的 SSE 事件
SSE_EVENT_HANDLERS.understanding = (data) => {
  setStage({ stage: 'understanding', msg: '正在理解图片...' });
};
```

### 7.3 文件结构

```
frontend/src/
├── api/chat.ts              # 新增 image_base64 字段
├── components/chat/
│   ├── ChatInput.tsx        # 新增图片上传 + 预览
│   └── MessageBubble.tsx    # 可选：展示图片
└── pages/ChatPage.tsx       # 处理 understanding 阶段 UI
```

---

## 八、实施计划

### 8.1 时间线

```
Week 1（Phase 1-2）
├── Day 1-2: pipeline.py + steps/ 拆出来
├── Day 3: builder.py + 场景化组装
├── Day 4: image_agent.py + 豆包视觉接入
└── Day 5: 前端 ChatInput 图片上传

Week 2（Phase 3-4）
├── Day 1-2: ticket_agent.py + ticket_tools.py
├── Day 3: ticket_decision_logs 表 + 监控
├── Day 4: shared_context.py + router.py
└── Day 5: meta_orchestrator.py + 跨域协作

Week 3（Phase 5 + 联调）
├── Day 1-2: ChatPage 多阶段 UI
├── Day 3: 全链路联调（文本/图片/投诉/法律咨询）
└── Day 4-5: 监控面板 + 日志分析
```

### 8.2 验证清单

每个 Phase 完成时验证：

- [ ] `npm run typecheck` 通过
- [ ] `npm test` 前端测试通过
- [ ] `pytest tests/` 后端测试通过
- [ ] 新增代码覆盖率 > 70%
- [ ] 手动测试：文本问答 / 图片理解 / 工单处理 / 跨域协作

---

## 九、风险与限制

| 风险                       | 影响                 | 缓解                                |
| ------------------------ | ------------------ | --------------------------------- |
| 豆包视觉模型 base64 支持未验证      | 图片 Agent 无法工作      | 先做 API 调通测试，确认输入格式                |
| 小模型（情绪/问题分类）选型未定         | 工单 Agent 决策质量不确定   | 先用规则兜底，后续接小模型                     |
| SharedContext 并发安全       | 多 agent 并行写时数据竞争   | 单 agent 串行执行，避免并行；或加 asyncio.Lock |
| Meta-Orchestrator 合并逻辑模糊 | 两个 agent 答案矛盾时输出混乱 | 定义合并优先级：ticket > rag > image      |
| 现有 chat.py 兼容性           | 管线重构影响现有问答         | rag_service.py 保留为兼容层，内部调 builder |

---

## 十、未来扩展（暂不做）

| 功能           | 触发条件                         |
| ------------ | ---------------------------- |
| MCP 工具接入     | 多 agent 共享工具 / 第三方系统接入       |
| LangGraph 编排 | 管线出现循环/条件分支/需要 checkpointing |
| 大模型端到端决策     | 小模型 + 规则兜底搞不定的复杂场景           |
| A/B 测试       | 需要量化评估 Agent 决策质量时           |

---

**文档版本：** v1.0  
**最后更新：** 2026-08-23  
**维护者：** 架构组
