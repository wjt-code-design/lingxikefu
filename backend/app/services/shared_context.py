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
    query: str = ""
    kb_id: UUID | None = None
    kb_version: str | None = None
    user_id: str = ""
    session_id: UUID | None = None
    message_id: UUID | None = None
    history: list[dict] = field(default_factory=list)
    image_refs: list[str] = field(default_factory=list)  # 图片通道预留（当前无上传入口，恒空）
    image_paths: list[str] = field(default_factory=list)  # 图片文件路径列表（Image Agent 使用）

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

    # QA Agent 写入（复用现有 RagResult，不造平行字段结构）
    rag_result: RagResult | None = None

    # Ticket Agent 写入
    ticket_id: str | None = None


# 写入权限表（约定级；新增字段必须在此登记）：
# | 字段                                  | 写入方      | 读取方              |
# |---------------------------------------|-------------|---------------------|
# | 输入区（query/kb_*/session_*/history） | chat 层     | Router、各 Agent     |
# | intent / agents_invoked / degraded    | Router      | 各 Agent、chat 层    |
# | image_desc / fused_query              | Image Agent | QA Agent、Router     |
# | rag_result                            | QA Agent    | Router、chat 层      |
# | ticket_id                             | Ticket Agent| Router、chat 层      |
