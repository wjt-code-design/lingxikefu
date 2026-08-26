"""会话状态机纯函数测试（批次B）：阶段推进 / 槽位填充 / 主题切换 / 提示生成。"""
from __future__ import annotations

from app.services.conversation_state import (
    MAX_CLARIFY,
    STAGE_CLARIFYING,
    STAGE_COLLECTING,
    STAGE_GREETING,
    STAGE_RESOLVING,
    missing_slots,
    new_state,
    to_prompt_hint,
    update,
)


def test_new_state_defaults():
    s = new_state()
    assert s == {"stage": "greeting", "topic": "", "slots": {}, "clarify_count": 0}


def test_update_none_state_returns_fresh():
    s = update(None, "你好")
    assert s["stage"] == STAGE_GREETING  # 闲聊无主题
    assert s["topic"] == ""


def test_update_topic_hit_moves_to_collecting():
    """主题命中但缺订单号 → info_collecting。"""
    s = update(None, "我要退款")
    assert s["topic"] == "退款"
    assert s["stage"] == STAGE_COLLECTING
    assert missing_slots(s) == ["order_no"]


def test_update_slot_fill_moves_to_resolving():
    """主题 + 订单号齐 → resolving。"""
    s = update(None, "我要退款")
    s = update(s, "订单号 SO2026080118")
    assert s["slots"]["order_no"] == "SO2026080118"
    assert s["stage"] == STAGE_RESOLVING
    assert missing_slots(s) == []


def test_slot_fill_without_topic_stays_greeting():
    """先给订单号后说主题（倒序）——槽位照存，阶段随主题变化。"""
    s = update(None, "SO2026080118 怎么回事")
    assert s["slots"]["order_no"] == "SO2026080118"
    assert s["stage"] == STAGE_GREETING  # 无主题仍 greeting（槽位已存，等主题命中即 resolving）
    s2 = update(s, "我要退款")
    assert s2["stage"] == STAGE_RESOLVING  # 槽位已在，主题命中直接齐


def test_no_required_slots_topic_goes_resolving():
    """发票主题无必需槽位 → 命中即 resolving。"""
    s = update(None, "发票怎么开")
    assert s["topic"] == "发票"
    assert s["stage"] == STAGE_RESOLVING


def test_topic_switch_follows_latest_message():
    """换话题：最新消息命中新主题则切换；未命中保留旧主题。"""
    s = update(None, "我要退款")
    s = update(s, "物流到哪了")  # 命中「配送/物流」
    assert s["topic"] == "配送/物流"
    s = update(s, "嗯嗯")  # 无主题词 → 保留
    assert s["topic"] == "配送/物流"


def test_order_slot_replaced_old_moved_to_past():
    """P3-⑬：新订单号替换旧槽位（保留最近一个、正在处理的那单），旧号移入 past_entities。

    旧契约"首单不被覆盖"在多单逐单咨询时会让槽位停留在首单、坐席回答错单；
    新契约为"新替旧 + 旧号可回溯"，多单场景逐单处理不串单。
    """
    s = update(None, "SO2026080118 退款")
    s = update(s, "物流 XOZ-12345 到哪了")
    assert s["slots"]["order_no"] == "XOZ-12345"  # 新订单号替换当前槽位
    assert "SO2026080118" in s.get("past_entities", [])  # 旧号进过去实体供指代回溯


def test_update_returns_new_dict_not_mutation():
    """契约：update 不原地修改入参（防调用方持有旧引用被意外变更）。"""
    s = new_state()
    out = update(s, "我要退款")
    assert s["topic"] == ""  # 原状态未被改
    assert out["topic"] == "退款"


def test_clarifying_stage_reset_on_new_topic_message():
    """clarifying 阶段收到新消息：有主题按槽位判定回落（clarifying 是等回复的瞬态）。"""
    s = {"stage": STAGE_CLARIFYING, "topic": "退款", "slots": {}, "clarify_count": 1}
    out = update(s, "退款到账多久")
    assert out["stage"] == STAGE_COLLECTING  # 仍缺订单号
    assert out["clarify_count"] == 1  # 计数不重置（累计口径，上限由调用方判）


def test_max_clarify_constant():
    assert MAX_CLARIFY == 2


def test_to_prompt_hint_variants():
    assert to_prompt_hint(None) is None
    assert to_prompt_hint(new_state()) is None  # 无主题
    s = update(None, "我要退款")
    hint = to_prompt_hint(s)
    assert hint is not None
    assert "退款" in hint
    assert "订单号" not in hint or "未提供" in hint  # 未提供时不误导（None 或明示未提供）
    s2 = update(s, "SO2026080118")
    hint2 = to_prompt_hint(s2)
    assert "SO2026080118" in hint2
