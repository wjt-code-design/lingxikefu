"""会话主题提取（2026-08-21，上下文多轮"状态"的轻量落地——B/C 统一）。

只从最近历史规则提取"当前主题 + 关联实体"，**不建状态机、不引入 Redis 持久化**
（客服单轮为主，重型会话状态 ROI 低——见工程纪律）。输出注入 prompt 的 `context_hint`，
帮 LLM 在多轮里保持主题连续；实体复用 reason 侧，避免重复实现。

诚实边界：
- 规则式，仅覆盖高频流程主题（退换货/退款/保修/配送/发票/价保）；
- 未命中返回 None（不注入，输出与注入式完全一致，测试可稳定）。
"""
from __future__ import annotations

from typing import Any

from app.services.query_rewrite import _extract_entities

#: 流程主题 -> 触发词（命中任一即视为该主题；顺序在此 = 优先级：退款先于退换货可共存）
FLOW_TOPICS: list[tuple[str, tuple[str, ...]]] = [
    ("退款", ("退款", "退钱", "到账")),
    ("退换货", ("退货", "换货", "退换")),
    ("保修/维修", ("保修", "质保", "维修", "换新")),
    ("配送/物流", ("物流", "发货", "配送", "快递", "到货", "收货")),
    ("发票", ("发票", "开票")),
    ("价保", ("价保", "保价", "降价", "补差")),
]


def _nearest_user_content(history: list[dict] | None) -> str | None:
    if not history:
        return None
    for m in reversed(history):
        if m.get("role") == "user" and m.get("content"):
            return m["content"]
    return None


def extract_topic(history: list[dict] | None) -> str | None:
    """从最近历史识别当前主题与关联实体；无主题返回 None。

    返回如 "用户当前主题：退款；关联实体：XOZ-12345"，供 qa_prompt 注入。
    """
    content = _nearest_user_content(history)
    if not content:
        return None
    topics: list[str] = []
    for name, keys in FLOW_TOPICS:
        if any(k in content for k in keys) and name not in topics:
            topics.append(name)
    if not topics:
        return None
    parts = [f"用户当前主题：{'/'.join(topics)}"]
    entities = _extract_entities(content)
    if entities:
        # 只保留最具体的标识实体（订单号/型号），去掉泛化商品词，避免 prompt 噪音
        concrete = [e for e in entities if not _is_generic(e)]
        if concrete:
            parts.append(f"关联实体：{'/'.join(dict.fromkeys(concrete))}")
    return "；".join(parts)


def _is_generic(entity: str) -> bool:
    """泛化商品词（手机/冰箱…）不算具体可消解实体，不进主题提示（防止把商品词当实体噪声注入）。"""
    return entity in ("手机", "冰箱", "空调", "电视", "洗衣机", "电脑", "平板", "耳机", "充电器", "显示器", "笔记本")


def _extract_topic_names(history: list[dict] | None) -> list[str]:
    """只返回命中的主题名列表（按词表优先级去重），不拼注入字符串。"""
    content = _nearest_user_content(history)
    topics: list[str] = []
    if content:
        for name, keys in FLOW_TOPICS:
            if any(k in content for k in keys) and name not in topics:
                topics.append(name)
    return topics


def build_handoff_summary(
    history: list[dict] | None, max_question: int = 120
) -> dict[str, Any] | None:
    """转人工交接摘要：本次会话的「当前主题 + 具体实体 + 最近用户诉求」。

    供客服 observe 视角会话头部展示（动态生成，不落库）。规则式、无 LLM 调用、fail-open。
    - topic: 命中的流程主题（可多个，按词表优先级）
    - entities: 具体标识实体（订单号/型号等，去泛化商品词）
    - question: 最近一条用户诉求（限长，客服一眼看到用户这次在问什么）
    无用户消息或全部为空返回 None（不展示空壳胶囊）。
    """
    question = _nearest_user_content(history)
    if not question:
        return None
    summary: dict[str, Any] = {}
    topics = _extract_topic_names(history)
    if topics:
        summary["topic"] = "/".join(topics)
    entities = _extract_entities(question)
    concrete = [e for e in entities if not _is_generic(e)]
    if concrete:
        summary["entities"] = list(dict.fromkeys(concrete))
    summary["question"] = question[:max_question]
    return summary
