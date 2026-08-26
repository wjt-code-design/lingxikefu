"""工具注册表（P0-2）：业务工具显式注册（name/desc/schema/topics/executor/formatter）。

调用方经 TOOL_REGISTRY / get_tool 获取工具元数据与执行函数，不再直接依赖具体模块；
新增业务工具只需在文末「显式注册区」登记一条 ToolDescriptor。
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.services.tools import order_tool

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolDescriptor:
    """单个业务工具的完整登记信息。"""

    name: str  # 稳定标识（SSE meta / 日志 / 未来 Agent 编排）
    description: str  # 职责说明（面向未来 LLM 工具选择）
    parameters: dict[str, Any]  # 参数 JSON-Schema 风格描述
    executor: Callable[..., Any]  # 执行函数（返回结构化结果，不负责展示）
    topics: frozenset[str] = frozenset()  # 适用对话主题集合（chat 层门控）
    formatter: Callable[[Any], str] | None = None  # 结构化结果 → 回复文本


TOOL_REGISTRY: dict[str, ToolDescriptor] = {}


def register(tool: ToolDescriptor) -> ToolDescriptor:
    """显式登记工具；重名覆盖前告警（便于开发期发现重复注册）。"""
    existing = TOOL_REGISTRY.get(tool.name)
    if existing is not None:
        logger.warning(
            "工具重名覆盖: %s（%s → %s）", tool.name, existing.description, tool.description
        )
    TOOL_REGISTRY[tool.name] = tool
    return tool


def get_tool(name: str) -> ToolDescriptor | None:
    """按名取工具描述；未注册返回 None（调用方自行降级）。"""
    return TOOL_REGISTRY.get(name)


# ---- 显式注册区：新增业务工具在此登记 ----
register(
    ToolDescriptor(
        name="order_query",
        description="按订单号查询订单状态/物流/预计送达（Mock 数据源，未来接真实订单系统）",
        parameters={
            "order_no": {"type": "string", "description": "订单号（大小写不敏感）"},
        },
        executor=order_tool.query_order,
        topics=order_tool.ORDER_TOPICS,
        formatter=order_tool.format_order_reply,
    )
)
