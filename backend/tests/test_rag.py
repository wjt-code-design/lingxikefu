"""RAG 管线测试（BU-03）：intent 分类 / 检索接线 / 诚实性拒答 / prompt / 流式事件。

- mock 检索与 chat client：不依赖真实 Qdrant/百炼；
- 重点验证：无依据拒答（fail-closed）、SSE 事件顺序、prompt 含来源。
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from app.prompts.qa_prompt import build_qa_messages
from app.services.rag_service import (
    RagResult,
    _no_llm_reply,
    _to_sources,
    classify_intent,
    fix_citations,
    run_pipeline,
    stream_answer,
)
from app.services.retrieval_service import RetrievedChunk


def make_chunk(score: float, text: str = "保修条款内容", doc_id: str = "d1") -> RetrievedChunk:
    # dense_score 与 score 一致：模拟纯 dense 相关性（拒答判定用 dense_score，ADR §3.5 解耦）
    return RetrievedChunk(chunk_id="c1", doc_id=doc_id, kb_id="kb1", idx=0, text=text, score=score, dense_score=score)


class FakeChat:
    def __init__(self):
        self.calls = []

    async def stream_events(self, messages, model=None, **kw):
        """思维链透传契约：rag_service 现消费 stream_events（reasoning/content 分型）。"""
        self.calls.append((messages, model))
        for c in "你好":
            yield ("content", c)

    async def stream(self, messages, model=None, **kw):
        self.calls.append((messages, model))
        for c in "你好":
            yield c


@pytest.fixture(autouse=True)
def patch(monkeypatch):
    captured = {}

    def _fake_search(q, kb, top_k=5):
        captured["query"] = q
        return [make_chunk(0.9)]

    monkeypatch.setattr("app.services.retrieval_service.search_kb", _fake_search)
    fake = FakeChat()
    monkeypatch.setattr("app.services.rag_service.get_chat_client", lambda: fake)
    fake.captured = captured
    return fake


# --- intent 分类 ---
def test_intent_handoff_keyword():
    assert classify_intent("我要找人工客服投诉") == "handoff"


def test_intent_chitchat_keyword():
    assert classify_intent("你好呀") == "chitchat"


def test_intent_qa_default():
    assert classify_intent("退货运费谁出") == "qa"


def test_intent_artificial_intelligence_not_handoff():
    """M6 回归：「人工智能」不应被误判为转人工。"""
    assert classify_intent("你们用了人工智能技术吗") == "qa"


def test_intent_emotional_keywords_handoff():
    """T1 分流升级：情绪词命中 → handoff（高优建单）。"""
    assert classify_intent("你们服务太差我要退钱") == "handoff"
    assert classify_intent("等太慢了差评") == "handoff"


def test_intent_refund_not_handoff():
    """T1 边界：`退款` 是 qa 高频词，不得误入情绪表 → 仍为 qa。"""
    assert classify_intent("退款多久能到账") == "qa"
    assert classify_intent("退款是原路退回吗") == "qa"


# --- D3（2026-09-03 并发审计）：chitchat 裸子串误判修复——残句复扫 ---
@pytest.mark.parametrize("q", [
    "退款多久到账，谢谢",      # 业务问句 + 礼貌尾缀（审计实证样本：影子分歧同型）
    "你好，退货怎么办理",      # 开场寒暄 + 业务问句
    "天气太热商品坏了能退货吗",  # 词表词作业务语境成分
    "心情不好，想退货",        # 情绪背景 + 业务诉求
    "下班后还能退货吗",        # 时间背景 + 业务问句
    "在吗？我的订单怎么查",    # 询问在场 + 业务问句
])
def test_intent_chitchat_boundary_business_qa(q):
    """chitchat 词不得单独短路：含业务实义内容的问句必须走 qa（检索+LLM）。

    修复前：裸子串匹配（k in query）——「谢谢」「你好」命中即判 chitchat，
    业务问题被静默吞掉（不检索不调 LLM，直接闲聊模板回复）。
    """
    assert classify_intent(q) == "qa"


@pytest.mark.parametrize("q", [
    "你好",
    "你好呀",
    "谢谢",
    "再见",
    "在吗",
    "你是谁",
    "你是机器人吗",
    "天气怎么样",
    "讲个笑话",
    "讲个笑话吧",
    "你们几点下班",
    "聊聊心情",
])
def test_intent_chitchat_boundary_pure_smalltalk(q):
    """纯寒暄（剔除词表词 + 封闭助词表后无实义剩余）仍判 chitchat——行为保持。"""
    assert classify_intent(q) == "chitchat"


# --- S2 情绪词窄语境排除（外部审查 S2）：商品/故障语境问句不判情绪转人工 ---
@pytest.mark.parametrize("q", [
    "手机运行太慢怎么办",
    "垃圾处理器保修多久",
    "垃圾袋还有货吗",
    "系统经常崩溃怎么修",
])
def test_emotion_words_product_context_not_handoff(q):
    assert classify_intent(q) == "qa"


@pytest.mark.parametrize("q,exp", [
    ("我要投诉", "handoff"),
    ("转人工", "handoff"),
    ("你们就是骗子！", "handoff"),
    ("气死了，我要投诉", "handoff"),     # 排除正则不得放走真投诉
    # 残句复扫（F-2 终审修复）：语境短语剔除后同句情绪信号仍命中——
    # 骗子/赔偿/退钱/差评在情绪词表（非 HANDOFF_KEYWORDS），整块跳过方案曾放走这四类
    ("垃圾处理器质量太差，气死了", "handoff"),
    ("垃圾处理器质量太差，你们就是骗子", "handoff"),
    ("系统崩溃了，我要赔偿", "handoff"),
    ("垃圾袋是假的我要退钱", "handoff"),
    ("手机崩溃了差评", "handoff"),
    ("这个垃圾桶太垃圾了", "handoff"),   # 语境词剔除后残余"垃圾"仍命中
    ("你们的服务真垃圾", "handoff"),     # 裸情绪用法不受排除影响
    ("手机太慢", "handoff"),             # 裸"太慢"不受排除影响
    ("你好", "chitchat"),
    ("七天无理由退货怎么申请？", "qa"),
])
def test_classify_intent_existing_behavior_unchanged(q, exp):
    assert classify_intent(q) == exp


# --- 管线 ---
def test_pipeline_qa_retrieves_chunks(patch):
    r = run_pipeline("退货运费谁出", uuid4())
    assert r.intent == "qa"
    assert len(r.chunks) == 1 and not r.refuse


def test_pipeline_search_uses_rewritten_query(patch):
    """T9-S3：检索用改写文本（口语→规范），intent 仍为 qa（原文判定）。"""
    r = run_pipeline("碎屏显咋换", uuid4())
    assert r.intent == "qa"
    assert patch.captured["query"] == "碎屏险怎么换"  # 检索入参为改写后


def test_pipeline_low_score_refuses(patch, monkeypatch):
    """诚实性：top-1 分数低于阈值 → 拒答，不编造。"""
    monkeypatch.setattr(
        "app.services.retrieval_service.search_kb", lambda q, kb, top_k=5: [make_chunk(0.1)]
    )
    r = run_pipeline("星河的创始人是谁", uuid4())
    assert r.refuse is True


def test_pipeline_no_chunks_refuses(patch, monkeypatch):
    monkeypatch.setattr(
        "app.services.retrieval_service.search_kb", lambda q, kb, top_k=5: []
    )
    r = run_pipeline("完全无关的问题", uuid4())
    assert r.refuse is True


def test_pipeline_retrieval_error_degrades_to_refuse(patch, monkeypatch):
    """P2-1：检索不可用 → 降级为诚实拒答（fail-open，不再抛 RagError）。"""
    from app.services.retrieval_service import RetrievalError

    def boom(*_a, **_k):
        raise RetrievalError("Qdrant 挂了")

    monkeypatch.setattr("app.services.retrieval_service.search_kb", boom)
    r = run_pipeline("退货运费", uuid4())
    assert r.refuse is True
    assert r.retrieve_degraded is True  # 与"无依据"区分（运营可辨识为基建退化）
    assert r.degraded_kind == "retrieval"  # 降级阶梯：检索故障细分


def test_pipeline_timeout_degrades_with_timeout_kind(patch, monkeypatch):
    """降级阶梯（架构一期 5）：管线时间预算用尽 → degraded_kind="timeout"，仍 fail-open 拒答。"""
    from app.orchestrator import PipelineTimeoutError

    def budget_exhausted(pipeline):
        raise PipelineTimeoutError("管线时间预算 20.0s 用尽")

    monkeypatch.setattr("app.services.rag_service._build_pipeline", budget_exhausted)
    r = run_pipeline("退货运费", uuid4())
    assert r.refuse is True
    assert r.retrieve_degraded is True
    assert r.degraded_kind == "timeout"


def test_timeout_vs_retrieval_distinct_replies():
    """降级话术阶梯：超时=容量话术（稍后重试），检索故障=故障话术；两档均含评测锚点「转人工」且文案不同。"""
    r_timeout = _no_llm_reply(
        RagResult(intent="qa", refuse=True, refuse_reason="t", retrieve_degraded=True, degraded_kind="timeout")
    )
    r_retrieval = _no_llm_reply(
        RagResult(intent="qa", refuse=True, refuse_reason="r", retrieve_degraded=True, degraded_kind="retrieval")
    )
    assert "转人工" in r_timeout and ("稍后" in r_timeout or "繁忙" in r_timeout)
    assert "转人工" in r_retrieval
    assert r_timeout != r_retrieval


# --- prompt ---
def test_build_qa_messages_contains_sources():
    msgs = build_qa_messages("保修多久", [make_chunk(0.9, "保修期12个月"), make_chunk(0.8, "电池6个月")])
    assert msgs[0]["role"] == "system"
    assert "[来源1]" in msgs[0]["content"] and "[来源2]" in msgs[0]["content"]
    # 用户问题与历史通过 <<>> 分隔块放入 user 消息（M10 隔离）
    assert msgs[-1]["role"] == "user"
    assert "保修多久" in msgs[-1]["content"]
    assert "<<用户问题>>" in msgs[-1]["content"]


def test_prompt_forbids_metadata_and_allows_demo_data():
    """规则 6/7：禁文件名元信息；演示/模拟订单数据必须直接引用不得拒答。"""
    msgs = build_qa_messages("快递到哪儿了", [make_chunk(0.9, "订单 SO2026080118 派送中")])
    sys = msgs[0]["content"]
    assert "绝对禁止" in sys and "文件名" in sys
    assert "演示/模拟" in sys
    assert "资料未收录" in sys  # 拒答仅限资料确实无相关内容时


def test_build_qa_messages_history():
    msgs = build_qa_messages("那电池呢", [make_chunk(0.9)], history=[{"role": "user", "content": "保修多久"}, {"role": "assistant", "content": "12个月"}])
    # 历史对话现置于 user 消息的 <<历史对话>> 分隔块内（M10）
    assert msgs[-1]["role"] == "user"
    assert "用户: 保修多久" in msgs[-1]["content"]
    assert "客服: 12个月" in msgs[-1]["content"]
    assert "<<历史对话>>" in msgs[-1]["content"]


# --- 引用编号修复 fix_citations（2026-09-02 全量 eval citation 失败点归因） ---
def test_fix_citations_renumbers_to_supporting_chunk():
    """Q074 型：同文档续条内容被锚定标 [来源1]（内容实为 [来源2]）→ 校正到支撑 chunk。
    用真实 Q074 chunk/句子原文（配色小节在 idx=1），overlap 须跨过 0.30 阈值。"""
    chunks = [
        RetrievedChunk(chunk_id="c1", doc_id="d1", kb_id="kb1", idx=0, text="常见问题FAQ：商品配置与参数 屏幕 尺寸 重量 库存", score=0.9, dense_score=0.9),
        RetrievedChunk(chunk_id="c2", doc_id="d1", kb_id="kb1", idx=1, text="配色与款式：同一型号通常提供多种配色（如星河蓝/月光银/曜石黑），详情页选择规格可查看在售颜色，部分地区缺色会标注暂缺", score=0.8, dense_score=0.8),
    ]
    ans = "同一型号通常提供多种配色可选，具体在售颜色可在商品详情页的选择规格处查看 [来源1]。若部分地区缺色，会标注暂缺 [来源1]。"
    fixed = fix_citations(ans, chunks)
    assert "[来源2]" in fixed
    assert "[来源1]" not in fixed
    assert "配色可选" in fixed  # 句子文本不动


def test_fix_citations_strips_unsupported_marker():
    """无任何 chunk 支撑的引用（编造引用）→ 摘除标记。"""
    chunks = [make_chunk(0.9, "保修期12个月，自签收之日起")]
    ans = "发票可以抵扣进项税额 [来源1]。"
    fixed = fix_citations(ans, chunks)
    assert "发票可以抵扣进项税额" in fixed
    assert "[来源" not in fixed


def test_fix_citations_consecutive_markers_share_sentence():
    """[来源1][来源4] 连续引用共享同一引用点句子（Q089 型）→ 逐点校正到支撑 chunk。"""
    chunks = [
        RetrievedChunk(chunk_id="c1", doc_id="d1", kb_id="kb1", idx=0, text="以旧换新抵扣款原路退回", score=0.9, dense_score=0.9),
        RetrievedChunk(chunk_id="c2", doc_id="d2", kb_id="kb1", idx=0, text="退款去向：微信零钱/支付宝余额/原卡", score=0.8, dense_score=0.8),
    ]
    ans = "退款去向：微信支付的退回到微信零钱，支付宝退回到支付宝余额 [来源1][来源2]。"
    fixed = fix_citations(ans, chunks)
    assert "[来源2]" in fixed
    assert "[来源1]" not in fixed


def test_fix_citations_keeps_valid_and_noop():
    """正确引用不变；无标记 / 无 chunks 原样返回。"""
    chunks = [make_chunk(0.9, "保修期12个月，自签收之日起")]
    ans = "保修期 12 个月 [来源1]。"
    assert fix_citations(ans, chunks) == ans
    assert fix_citations("无标记的回答", chunks) == "无标记的回答"
    assert fix_citations(ans, []) == ans


def test_fix_citations_strips_runon_long_sentence():
    """Q052 型回归：引用点句子是长句段（LLM 用：合并引言+小节标题+首条，无句号终止），
    中文口径 overlap 不足 0.30 → 摘除（不得保留 eval 判定无效的引用）。"""
    chunks = [make_chunk(0.9, "会员折扣：银卡全场98折 金卡全场95折 钻石全场9折")]
    ans = "银卡和金卡的主要差异如下：\n\n## 1. 折扣力度\n- 银卡：全场 98 折 [来源1]\n- 金卡：全场 95 折 [来源1]。"
    fixed = fix_citations(ans, chunks)
    # 首条长句段被摘除（overlap<0.30），第二条短句保留（金卡95折可溯源）
    assert "- 银卡：全场 98 折" in fixed
    assert "- 金卡：全场 95 折 [来源1]" in fixed
    assert fixed.count("[来源") == 1


def test_sentence_overlap_matches_eval_metric():
    """口径一致性守护：rag_service._sentence_overlap 必须与 eval_faithfulness 判定同口径
    （CJK-only 2字窗口）。否则 fix_citations 保留 eval 判定无效的引用（2026-09-02 Q052 回归）。"""
    from app.services.rag_service import _sentence_overlap as prod_overlap
    from scripts.eval_faithfulness import _sentence_overlap as eval_overlap

    cases = [
        ("银卡：全场 98 折", "会员折扣：银卡全场98折 金卡全场95折"),
        ("同一型号通常提供多种配色可选，具体在售颜色可在详情页查看", "配色：星河蓝/月光银/曜石黑 详情页查看在售颜色"),
        ("直接通过 App 站内信核实即可", "官方通知仅通过App站内信与认证短信发送"),
    ]
    for s, c in cases:
        assert abs(prod_overlap(s, c) - eval_overlap(s, c)) < 1e-6, (s, prod_overlap(s, c), eval_overlap(s, c))


# --- 流式事件 ---
async def test_stream_answer_events_order(patch):
    events = [e async for e in stream_answer("保修多久", uuid4())]
    types = [t for t, _ in events]
    # 顺序（R-2）：intent → stage retrieving → stage generating → token* → sources → done
    assert types[0] == "intent"
    assert types[1] == "stage" and types[2] == "stage"
    assert "token" in types
    assert types[-2] == "sources" and types[-1] == "done"
    # R-2：intent 事件携带真实判定
    assert events[0][1]["intent"] == "qa"


async def test_stream_answer_done_carries_existing_rewritten_query(patch):
    """缓存回填必须复用管线已生成的改写文本，不能让 Chat 层再次改写。"""
    events = [e async for e in stream_answer("碎屏显咋换", uuid4())]
    done = next(data for event, data in events if event == "done")
    assert done["rewritten_query"] == "碎屏险怎么换"


async def test_stream_answer_handoff_no_llm(patch):
    events = [e async for e in stream_answer("我要投诉找人工", uuid4())]
    types = [t for t, _ in events]
    assert "token" in types and types[-1] == "done"
    # handoff 不调 LLM
    assert patch.calls == []


async def test_stream_answer_refuse_no_llm(patch, monkeypatch):
    monkeypatch.setattr(
        "app.services.retrieval_service.search_kb", lambda q, kb, top_k=5: [make_chunk(0.1)]
    )
    events = [e async for e in stream_answer("星河的创始人是谁", uuid4())]
    types = [t for t, _ in events]
    assert types[-1] == "done"
    assert patch.calls == []  # 拒答不调 LLM


async def test_stream_answer_retrieval_error_degrades_to_refusal(patch, monkeypatch):
    """P2-1：检索不可用 → 不发 error 帧，降级为拒答事件流（token=引导转人工 + done）。"""
    from app.services.retrieval_service import RetrievalError

    def boom(*_a, **_k):
        raise RetrievalError("down")

    monkeypatch.setattr("app.services.retrieval_service.search_kb", boom)
    events = [e async for e in stream_answer("退货运费", uuid4())]
    types = [t for t, _ in events]
    assert "error" not in types
    assert types[0] == "intent"
    assert events[0][1] == {"intent": "qa", "refuse": True}  # 拒答标志
    assert types[-1] == "done"
    answer = "".join(d["delta"] for t, d in events if t == "token")
    assert "转人工" in answer  # 降级话术可操作（引导人工）
    assert patch.calls == []  # 降级不调 LLM


async def test_stream_answer_retrieval_error_no_qdrant_url_leak(patch, monkeypatch):
    """P2-1+P2-④：降级拒答流中的任何字段不得透传内部 QDRANT_URL（防御：即便异常带 URL 也截断）。"""
    from app.core.config import settings
    from app.services.retrieval_service import RetrievalError

    def boom(*_a, **_k):
        raise RetrievalError(f"down {settings.QDRANT_URL}")  # 恶意/内部异常带 URL

    monkeypatch.setattr("app.services.retrieval_service.search_kb", boom)
    events = [e async for e in stream_answer("退货运费", uuid4())]
    for event_type, data in events:
        payload = str(data)
        assert settings.QDRANT_URL not in payload, f"{event_type} 泄漏内部 URL"


# --- A 修复：溯源透传 dense_score（真实余弦相似度）----------------------------


def test_to_sources_carries_dense_score():
    """_to_sources 必须同时带 score（RRF 融合分，排序位语义）与 dense_score
    （dense 原始余弦，绝对相似度语义）——SourcePanel「相似度」标签消费后者。
    旧实现只透传 score → hybrid 下 RRF 分（≈0.03-0.05）被当相似度显示成 5%。"""
    chunks = [
        RetrievedChunk(
            chunk_id="c1", doc_id="d1", kb_id="kb1", idx=0,
            text="保修条款内容", score=0.049, dense_score=0.87,
        )
    ]
    srcs = _to_sources(chunks)
    assert srcs[0]["score"] == 0.049  # 排序分保留（历史字段语义不变）
    assert srcs[0]["dense_score"] == 0.87  # 真实相似度补上


# --- B2-1 singleflight（stream_answer 接线）----------------------------------


async def test_stream_answer_winner_locks_and_clears(patch, monkeypatch):
    """winner 路径：抢到锁 → 照常生成，done 带 rewritten_query，收尾清闸一次。"""
    calls: list = []
    monkeypatch.setattr(
        "app.services.answer_cache.try_begin_generation",
        lambda k: calls.append(("begin", k)) or True,
    )
    monkeypatch.setattr(
        "app.services.answer_cache.end_generation",
        lambda k: calls.append(("end", k)),
    )
    events = [e async for e in stream_answer("保修多久", uuid4())]
    done = next(d for t, d in events if t == "done")
    assert "cache_hit" not in done  # winner 不是缓存复用
    assert patch.calls, "winner 必须照常调 LLM"
    kinds = [c[0] for c in calls]
    assert kinds == ["begin", "end"], f"抢锁一次+清闸一次，实际 {calls}"
    assert calls[0][1] == calls[1][1], "清闸的 key 必须与抢锁一致"


async def test_stream_answer_waiter_reuses_backfill_without_llm(patch, monkeypatch):
    """waiter 路径：抢锁失败 + 轮询命中 → 复用 winner 回填（token+sources+cache_hit），零 LLM。"""
    monkeypatch.setattr("app.services.answer_cache.try_begin_generation", lambda k: False)
    monkeypatch.setattr(
        "app.services.answer_cache.wait_for_exact",
        lambda q, v, kb=None: {"answer": "整机保修12个月", "sources": [{"chunk_id": "c9"}]},
    )
    ended: list = []
    monkeypatch.setattr("app.services.answer_cache.end_generation", lambda k: ended.append(k))

    events = [e async for e in stream_answer("保修多久", uuid4())]
    answer = "".join(d["delta"] for t, d in events if t == "token")
    assert answer == "整机保修12个月"
    src = next(d for t, d in events if t == "sources")
    assert src["sources"] == [{"chunk_id": "c9"}]  # 完整回填（含 sources）
    done = next(d for t, d in events if t == "done")
    assert done.get("cache_hit") is True
    assert "rewritten_query" not in done  # 防 Chat 层二次回填
    assert patch.calls == []  # waiter 不调 LLM
    assert ended == [], "waiter 不持锁，不清闸（防误删 winner 的锁）"


async def test_stream_answer_waiter_timeout_failopen_generates(patch, monkeypatch):
    """等待超时 → fail-open 照常生成（退化为旧行为，不报错不 500）。"""
    monkeypatch.setattr("app.services.answer_cache.try_begin_generation", lambda k: False)
    monkeypatch.setattr("app.services.answer_cache.wait_for_exact", lambda q, v, kb=None: None)
    events = [e async for e in stream_answer("保修多久", uuid4())]
    assert patch.calls, "等待失败必须照常生成"
    types = [t for t, _ in events]
    assert types[-1] == "done" and "token" in types


async def test_singleflight_concurrent_same_query_one_llm_call(patch, monkeypatch):
    """B2-1 验收（规划书）：并发 2 同问（fake redis）→ LLM 桩只调 1 次。

    端到端模拟生产闭环：winner 生成 → 消费者（= Chat 层回填门禁）cache_put →
    清闸；waiter 抢锁失败 → 轮询精确层复用完整回填。慢 LLM + 起跑延迟保证
    waiter 在 winner 持锁期间进入等待，复现「并发全 miss」击穿场景。
    """
    import asyncio
    import json as _json

    from app.services import answer_cache

    class _NxFake:
        def __init__(self):
            self.store: dict = {}

        def set(self, k, v, ex=None, nx=False):
            if nx and k in self.store:
                return None
            self.store[k] = v
            return True

        def get(self, k):
            return self.store.get(k)

        def delete(self, k):
            self.store.pop(k, None)

    r = _NxFake()
    monkeypatch.setattr(answer_cache, "settings", type("S", (), {
        "ANSWER_CACHE_ENABLED": True,
        "ANSWER_CACHE_THRESHOLD": 0.95,
        "ANSWER_CACHE_TTL_HOURS": 24,
    })())
    monkeypatch.setattr(answer_cache, "get_redis", lambda: r)
    monkeypatch.setattr(answer_cache, "_SF_WAIT_INTERVAL", 0.02)
    # cache_check 节点置 miss（击穿场景：两请求都 miss 进生成段）
    monkeypatch.setattr(answer_cache, "get", lambda *a, **k: None)
    # 慢 LLM（0.3s）：保证 waiter 起跑时 winner 仍持锁（否则 waiter 也抢到锁双生成）
    async def slow_stream(messages, model=None, **kw):
        patch.calls.append((messages, model))
        await asyncio.sleep(0.3)
        yield ("content", "整机保修12个月")

    monkeypatch.setattr(patch, "stream_events", slow_stream)
    monkeypatch.setattr(answer_cache, "get_qdrant_client", lambda: type("Q", (), {
        "get_collections": lambda self: type("C", (), {"collections": []})(),
        "create_collection": lambda self, **k: None,
        "upsert": lambda self, **k: None,
    })())
    monkeypatch.setattr(answer_cache, "get_embedding_client", lambda: type(
        "E", (), {"dim": 4, "embed": lambda self, a: [[0.1] * 4]}
    )())

    kb = uuid4()

    async def consume():
        """消费 stream_answer 并复刻 Chat 层回填门禁（done 带 rewritten_query → put）。"""
        async for ev, d in stream_answer("保修多久", kb, kb_version="v1"):
            if ev == "done" and isinstance(d.get("rewritten_query"), str) and d["rewritten_query"]:
                answer_cache.put(
                    d["rewritten_query"], "整机保修12个月", [], [], "v1", str(kb)
                )

    async def consume_waiter():
        await asyncio.sleep(0.05)  # 让 winner 先抢锁
        await consume()

    await asyncio.gather(consume(), consume_waiter())
    assert len(patch.calls) == 1, f"并发同问应只生成一次，实际 LLM 调用 {len(patch.calls)} 次"
    # 精确层确有回填（waiter 复用的来源）
    key = answer_cache._EXACT_PREFIX + f"{kb}:" + answer_cache._normalize_key("保修多久")
    assert _json.loads(r.store[key])["answer"] == "整机保修12个月"


async def test_singleflight_redis_down_both_generate_no_500(patch, monkeypatch):
    """B2-1 验收：Redis 挂 → 两请求照常生成不 500（fail-open 退化为旧行为）。"""
    import asyncio

    from app.services import answer_cache

    class _Down:
        def set(self, *a, **k):
            raise ConnectionError("redis down")

        def get(self, k):
            raise ConnectionError("redis down")

        def delete(self, k):
            raise ConnectionError("redis down")

    monkeypatch.setattr(answer_cache, "settings", type("S", (), {
        "ANSWER_CACHE_ENABLED": True,
        "ANSWER_CACHE_THRESHOLD": 0.95,
        "ANSWER_CACHE_TTL_HOURS": 24,
    })())
    monkeypatch.setattr(answer_cache, "get_redis", lambda: _Down())
    monkeypatch.setattr(answer_cache, "get", lambda *a, **k: None)
    monkeypatch.setattr(answer_cache, "_SF_WAIT_INTERVAL", 0.01)
    monkeypatch.setattr(answer_cache, "_SF_WAIT_TIMEOUT", 0.03)

    async def consume():
        events = [e async for e in stream_answer("保修多久", uuid4(), kb_version="v1")]
        assert [t for t, _ in events][-1] == "done"  # 无 error 事件

    await asyncio.gather(consume(), consume())
    assert len(patch.calls) == 2  # 双双照常生成（旧行为，不击穿报错）


async def test_stream_answer_personalized_skips_lock(patch, monkeypatch):
    """画像注入（回填门禁禁个性化入缓存）→ 不抢锁：抢了也没人回填，纯浪费。"""
    touched: list = []
    monkeypatch.setattr(
        "app.services.answer_cache.try_begin_generation",
        lambda k: touched.append(k) or True,
    )
    _events = [e async for e in stream_answer("保修多久", uuid4(), user_profile="常买冰箱")]
    assert touched == [], "个性化请求不得触碰生成锁"
    assert patch.calls  # 照常生成


async def test_stream_answer_does_not_force_explicit_model(patch, monkeypatch):
    """回归保护：stream_answer 不应硬塞模型名（模型名单一真源在 client 侧）。

    根因：rag_service.stream_answer 之前写死 ``client.stream(messages, model=settings.CHAT_MODEL)``，
    把 provider 无关的模型名打到端点 → 模型不存在 → 400。

    修复后应让 client 用自己的 ``_default_model()``，不传 model 或传 None。
    """
    # provider 收敛 longcat（2026-08-27）；CHAT_MODEL 字段已移除，模拟旧字段残留也无影响
    monkeypatch.setattr("app.services.rag_service.settings.CHAT_PROVIDER", "longcat", raising=False)
    events = [e async for e in stream_answer("退货", uuid4())]
    assert "token" in [t for t, _ in events]
    assert len(patch.calls) == 1, f"应恰好调用一次 chat client，实际 {len(patch.calls)} 次"
    passed_model = patch.calls[0][1]
    # 关键断言：不把任何模型名硬塞给 client（client 自选 _default_model）
    assert passed_model is None, f"stream_answer 把模型名 {passed_model!r} 硬塞给 client，应由 client 自选"
