"""SessionContext 测试（2026-08-21）：会话主题提取 + prompt 注入。

A 层确定性断言：不依赖真实 LLM。覆盖：
- extract_topic 从最近历史识别流程主题与关联实体（订单号优先、泛化商品词不注入）；
- 无命中 → None（不注入）；
- build_qa_messages(context_hint) 正确注入 <<会话上下文>> 块；None 时不注入（兼容旧输出）。
"""
from __future__ import annotations

from app.prompts.qa_prompt import build_qa_messages
from app.services.query_rewrite import rewrite
from app.services.retrieval_service import RetrievedChunk
from app.services.session_context import extract_topic


def make_chunk(text: str = "退款原路退回") -> list[RetrievedChunk]:
    return [RetrievedChunk(chunk_id="c1", doc_id="d1", kb_id="kb1", idx=0, text=text, score=0.9, dense_score=0.9)]


def test_extract_topic_refund_with_order():
    """退款主题 + 关联订单号。"""
    topic = extract_topic([{"role": "user", "content": "我的订单 XOZ-12345 怎么退款"}])
    assert topic and "退款" in topic and "XOZ-12345" in topic


def test_extract_topic_flow_subject():
    """仅流程主题（无具体实体）也可识别。"""
    topic = extract_topic([{"role": "assistant", "content": "在处理"}, {"role": "user", "content": "退货运费谁出"}])
    assert topic and "退换货" in topic


def test_extract_topic_none_when_no_flow():
    """无流程词 → None（不注入）。"""
    assert extract_topic([{"role": "user", "content": "今天天气怎么样"}]) is None
    assert extract_topic(None) is None


def test_extract_topic_ignores_generic_product_as_entity():
    """泛化商品词（手机）不当具体实体注入。"""
    topic = extract_topic([{"role": "user", "content": "手机保修多久"}])
    assert topic and "保修/维修" in topic and "手机" not in topic


def test_rewrite_business_order_coref():
    """端到端：订单号经指代消解进入改写（检索 key 带实体）。"""
    history = [{"role": "user", "content": "我的订单 XOZ-12345 到哪了"}]
    r, meta = rewrite("这个订单什么时候送到", history=history)
    assert "XOZ-12345" in r and meta["adopted"]


def test_build_qa_messages_injects_context_hint():
    """context_hint 注入 <<会话上下文>> 块；None 时不注入（兼容旧输出）。"""
    msgs = build_qa_messages("它怎么退款", make_chunk(), history=[{"role": "user", "content": "订单 XOZ-12345"}], context_hint="用户当前主题：退款；关联实体：XOZ-12345")
    user_content = msgs[-1]["content"]
    assert "<<会话上下文>>" in user_content
    assert "退款" in user_content and "XOZ-12345" in user_content

    msgs2 = build_qa_messages("它怎么退款", make_chunk())
    assert "<<会话上下文>>" not in msgs2[-1]["content"]  # 无 hint 不注入
    assert "<<历史对话>>" in msgs2[-1]["content"] and "<<用户问题>>" in msgs2[-1]["content"]