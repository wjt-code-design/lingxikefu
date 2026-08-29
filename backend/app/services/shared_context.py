"""SharedContext：单次请求的多 Agent 共享上下文（v1.1 方案书 §2.4）。

设计决策：
- 写入按所有者划分（见文件尾写入权限表）；Agent 不直接互调，只经 ctx 交换数据。
- 不设 final_events（攒事件列表）：SSE 必须逐事件流式发出，攒列表会击穿
  断连检测 / 配额回滚 / 首字时延埋点（chat.py 既有行为）。
- 图片存引用不存 base64 全文（内存卫生；图片通道尚未接入，字段预留）。
- degraded 显式留痕：降级禁止静默改路径（教训库守则）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from app.services.rag_service import RagResult


@dataclass
class SharedContext:
    """单次请求的所有 Agent 共享上下文。"""

    # 输入（chat 层写入）
    trace_id: str = ""  # P0-1：入口 request_id 贯通业务链路（日志 / SSE done 透出）
    query: str = ""
    kb_id: UUID | None = None
    kb_version: str | None = None
    user_id: str = ""
    session_id: UUID | None = None
    message_id: UUID | None = None
    history: list[dict] = field(default_factory=list)
    # 架构一期 4：移交摘要（chat 层组装时由 build_handoff_summary(history+当前消息, conv_state)
    # 生成，Ticket Agent 建单时持久化进 tickets.summary）；None = 无摘要可打包（无历史时）。
    handoff_summary: dict[str, Any] | None = None
    # 架构二期 1（L2 预起草）：handoff 风险判别结果（"low"/"high"/""=未判别）。
    # chat 层组装时由 classify_handoff_risk(当前消息, conv_state) 填写；Ticket Agent 读：
    # low → 建单后 fire-and-forget AI 预起草（draft_suggestion），high/"" → 不预起草。
    handoff_risk: str = ""
    image_refs: list[str] = field(default_factory=list)  # 图片引用预留（未启用，恒空）；实际入口 image_paths
    image_paths: list[str] = field(default_factory=list)  # 图片文件路径列表（chat 注入，Image Agent 使用）

    # 请求级资源句柄（非共享数据）：chat 层注入的请求 DB 会话，供 Ticket Agent 建单。
    # 用 Any 避免本模块硬依赖 SQLAlchemy；测试可注入 SQLite 会话。
    db: Any = None

    # Router 写入（前置分类，单一真源 = rag_service.classify_intent）
    intent: str = ""  # qa / handoff / chitchat
    agents_invoked: list[str] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)  # 降级留痕，如 ["image:count>2"]

    # Image Agent 写入
    image_desc: str = ""
    image_descriptions: list[str] = field(default_factory=list)  # 多张图片的描述列表
    fused_query: str = ""

    # chat 层（stream_answer）写入——QA 路径非 Agent 成员（对抗审查 2026-08-27，
    # qa_agent 包装类已删；复用现有 RagResult，不造平行字段结构）
    rag_result: RagResult | None = None

    # Ticket Agent 写入
    ticket_id: str | None = None


# 写入权限表（约定级；新增字段必须在此登记）：
# | 字段                                  | 写入方      | 读取方              |
# |---------------------------------------|-------------|---------------------|
# | trace_id                              | chat 层     | Router、各 Agent、日志、SSE done |
# | 输入区（query/kb_*/session_*/history） | chat 层     | Router、各 Agent     |
# | handoff_summary                       | chat 层     | Ticket Agent        |
# | handoff_risk                          | chat 层     | Ticket Agent        |
# | intent / agents_invoked / degraded    | Router      | 各 Agent、chat 层    |
# | image_desc / fused_query              | Image Agent | Router、chat 层      |
# | rag_result                            | chat 层      | Router、chat 层      |
# | ticket_id                             | Ticket Agent| Router、chat 层      |
