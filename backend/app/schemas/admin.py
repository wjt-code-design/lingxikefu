"""管理后台响应模型（BU-09），与 contracts/api.ts Admin 段对齐。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Role = Literal["admin", "agent", "user"]


class UserItem(BaseModel):
    user_id: str
    account: str
    role: Role
    created_at: str


class UserListResp(BaseModel):
    items: list[UserItem]
    total: int


class RoleUpdateReq(BaseModel):
    role: Role


class HotGap(BaseModel):
    """F1：待补录问题（refuse 意图高频问句——QA 检索无依据被拒答，运营补 KB 信号）。
    handoff（转人工/情绪）是正常分流，不属于知识缺口，不计入。"""

    question: str
    count: int


class FeedbackGap(BaseModel):
    """点踩缺口（架构三期 1）：down 反馈连被踩消息原文聚类——高频被踩原文，
    与 hot_gaps（refuse 源）互补的运营补录/优化信号。"""

    question: str  # 组内出现最多的消息原文变体（归一化归并后展示）
    count: int  # 组内 down 反馈总次数
    last_at: str  # 组内最近一次 down 反馈时间（ISO8601）


class AdminStats(BaseModel):
    sessions: int
    messages: int
    documents: int
    feedback_up: int
    feedback_down: int
    avg_first_token_ms: float
    hot_gaps: list[HotGap] = []
    # 三期 1：点踩源聚类（默认值保证旧调用方兼容；时间窗与 hot_gaps 共用 ?days）
    feedback_gaps: list[FeedbackGap] = []
    # T1.2 运营观测扩展（默认值保证旧调用方兼容）：
    # 拒答口径：refuse_count 即真拒答轮数（澄清轮 intent 落 'qa' 不计入，勿再减 clarify_rounds）
    tool_dist: dict[str, int] = {}
    clarify_rounds: int = 0
    topic_dist: dict[str, int] = {}
    refuse_count: int = 0


class TrendPoint(BaseModel):
    """单日计数（P1：stats/trend）。"""

    date: str  # YYYY-MM-DD
    sessions: int
    messages: int
    tickets: int
    # T1.3 时序扩展（默认值保证旧调用方兼容；口径与 stats 同名字段一致）：
    tool_dist: dict[str, int] = {}  # 当日 assistant 工具回答分布
    clarify_rounds: int = 0  # 当日澄清轮数（meta.clarify=True 的 assistant 消息）


class StatsTrendResp(BaseModel):
    days: list[TrendPoint]


class IntentShadowBucket(BaseModel):
    """单规则意图桶（架构二期 3：意图影子一致率观测）。"""

    total: int  # 影子样本数（meta.intent_shadow 存在的用户消息）
    agree: int  # LLM 意图与规则意图（user_msg.intent）一致的样本数
    agree_rate: float  # agree / total（4 位小数；total=0 时为 0.0）


class IntentShadowStats(BaseModel):
    """ADR-1 第一步观测口径：LLM 影子意图 vs 规则式意图一致率（只记不驱动的验证数据）。

    H4 观测（架构数据积累期）：min_total/remaining 供"距切换决策门槛还差多少样本"
    一屏可读——门槛值 INTENT_SHADOW_MIN_TOTAL（config），remaining<=0 即样本量达标。
    """

    total: int
    agree: int
    agree_rate: float
    by_intent: dict[str, IntentShadowBucket] = {}
    min_total: int = 0
    remaining: int = 0
