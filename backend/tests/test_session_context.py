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
from app.services.session_context import build_handoff_summary, extract_topic


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


def test_handoff_summary_topic_entity_question():
    """交接摘要：命中主题 + 具体实体 + 最近诉求；无用户消息返回 None。"""
    history = [
        {"role": "user", "content": "我买的空调坏了，订单 SO2026080199 要退货还是换货？"},
        {"role": "assistant", "content": "已为您查询，可支持 15 天内质量问题退货"},
    ]
    s = build_handoff_summary(history)
    assert s is not None
    assert "退换货" in s["topic"]
    assert "SO2026080199" in s["entities"]
    assert "SO2026080199" in s["question"]

    # 无用户消息 → None（不展示空壳胶囊）
    assert build_handoff_summary([]) is None
    assert build_handoff_summary(None) is None


def test_handoff_summary_question_limited_and_generic_ignored():
    """诉求限长；泛化商品词不进实体；超过 120 字截断。"""
    long = "我" * 200
    s = build_handoff_summary([{"role": "user", "content": long}])
    assert s is not None and len(s["question"]) <= 120
    assert "entities" not in s  # 纯叠字无具体实体

    s2 = build_handoff_summary([{"role": "user", "content": "手机退货"}])
    assert "退换货" in s2["topic"]
    assert not s2.get("entities")  # "手机"是泛化商品词，不进实体


def test_handoff_summary_with_conv_state():
    """批次B：交接摘要并入状态机——客服看到阶段/槽位/澄清次数。"""
    from app.services.session_context import build_handoff_summary

    history = [{"role": "user", "content": "我要退款 SO2026080118"}]
    conv_state = {"stage": "resolving", "topic": "退款", "slots": {"order_no": "SO2026080118"}, "clarify_count": 1}
    s = build_handoff_summary(history, conv_state=conv_state)
    assert s["stage"] == "resolving"
    assert s["slots"] == {"order_no": "SO2026080118"}
    assert s["clarify_count"] == 1


def test_handoff_summary_without_conv_state_unchanged():
    """不传 conv_state：输出结构与旧版完全一致（兼容契约）。"""
    from app.services.session_context import build_handoff_summary

    history = [{"role": "user", "content": "我要退款"}]
    s = build_handoff_summary(history)
    assert "stage" not in s
    assert s["topic"] == "退款"
