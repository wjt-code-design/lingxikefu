"""坐席辅助 prompt 测试：system 含资料编号、user 分隔块隔离（M10 防注入同构）。"""
from __future__ import annotations

from app.prompts.agent_assist_prompt import build_assist_messages
from app.services.retrieval_service import RetrievedChunk


def _chunk(text: str) -> RetrievedChunk:
    return RetrievedChunk(chunk_id="c1", doc_id="d1", kb_id="kb1", idx=0, text=text, score=0.9)


def test_system_contains_numbered_context():
    msgs = build_assist_messages("退款多久到账", None, [_chunk("退款 1-3 个工作日原路退回"), _chunk("不支持顺丰到付")])
    assert msgs[0]["role"] == "system"
    assert "[来源1] 退款 1-3 个工作日原路退回" in msgs[0]["content"]
    assert "[来源2] 不支持顺丰到付" in msgs[0]["content"]
    assert "坐席助手" in msgs[0]["content"]  # 角色定位


def test_user_content_uses_delimited_blocks():
    history = [
        {"role": "user", "content": "我买的洗衣机还没到"},
        {"role": "assistant", "content": "已为您查询物流"},
        {"role": "agent", "content": "您好，正在核实"},
    ]
    msgs = build_assist_messages("退款多久到账", history, [_chunk("退款 1-3 个工作日")])
    user = msgs[1]["content"]
    assert "<<历史对话>>" in user and "<</历史对话>>" in user
    assert "<<顾客最新消息>>" in user and "<</顾客最新消息>>" in user
    assert "退款多久到账" in user


def test_history_role_mapping():
    history = [
        {"role": "user", "content": "问句"},
        {"role": "assistant", "content": "AI 答"},
        {"role": "agent", "content": "客服答"},
    ]
    user = build_assist_messages("q", history, [])[1]["content"]
    assert "顾客: 问句" in user
    assert "AI: AI 答" in user
    assert "客服: 客服答" in user


def test_empty_history_renders_placeholder():
    user = build_assist_messages("q", [], [])[1]["content"]
    assert "（无）" in user


def test_injection_block_declared():
    """M10：分隔块内容声明为数据非指令。"""
    sys = build_assist_messages("q", None, [_chunk("x")])[0]["content"]
    assert "不是指令" in sys


def test_state_hint_injected_as_block():
    """大扫查修复（M-1）：会话状态（主题/已提供订单号）注入 <<会话状态>> 块，防建议重复索要已有信息。"""
    msgs = build_assist_messages(
        "q", None, [_chunk("x")], state_hint="会话主题：退款；已提供订单号：SO2026080118"
    )
    user = msgs[1]["content"]
    assert "<<会话状态>>" in user and "<</会话状态>>" in user
    assert "SO2026080118" in user


def test_state_hint_none_keeps_old_shape():
    """state_hint=None：user 内容与旧版完全一致（diff=0 兼容）。"""
    a = build_assist_messages("q", None, [_chunk("x")])
    b = build_assist_messages("q", None, [_chunk("x")], state_hint=None)
    assert a == b
    assert "会话状态" not in b[1]["content"]
