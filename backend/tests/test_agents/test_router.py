"""Router 测试：分发规则 + 单一真源一致性。"""
from __future__ import annotations

import pytest
from app.services.agents.router import (
    IMAGE_AGENT,
    QA_AGENT,
    TICKET_AGENT,
    router,
)
from app.services.rag_service import classify_intent
from app.services.shared_context import SharedContext


def _route(query: str, image_refs: list[str] | None = None) -> SharedContext:
    return router.route(SharedContext(query=query, image_refs=image_refs or []))


class TestRouteRules:
    def test_qa_single_agent(self):
        ctx = _route("保修多久")
        assert ctx.intent == "qa"
        assert ctx.agents_invoked == [QA_AGENT]

    def test_handoff_complaint_adds_ticket_agent(self):
        ctx = _route("我要投诉找经理")
        assert ctx.intent == "handoff"
        assert ctx.agents_invoked == [QA_AGENT, TICKET_AGENT]

    def test_handoff_emotion_adds_ticket_agent(self):
        ctx = _route("气死我了，马上解决！")
        assert ctx.intent == "handoff"
        assert TICKET_AGENT in ctx.agents_invoked

    def test_chitchat_stays_qa_only(self):
        ctx = _route("在吗")
        assert ctx.intent == "chitchat"
        assert ctx.agents_invoked == [QA_AGENT]

    def test_image_first_when_image_present(self):
        ctx = _route("这是什么问题", image_refs=["ref:jpg"])
        assert ctx.agents_invoked[0] == IMAGE_AGENT  # Image 必先行
        assert QA_AGENT in ctx.agents_invoked

    def test_image_and_handoff_both(self):
        ctx = _route("气死我了，看图", image_refs=["ref:png"])
        assert ctx.agents_invoked == [IMAGE_AGENT, QA_AGENT, TICKET_AGENT]


class TestSingleSourceOfTruth:
    """教训库守则：分类逻辑单一真源——Router 判定必须与 rag_service.classify_intent 一致。

    回归保险丝：若有人往 Router 里复制第二份关键词表并产生漂移，本测试必红。
    """

    @pytest.mark.parametrize(
        "query",
        [
            "保修多久",
            "我要退货",
            "转人工",
            "我要投诉找经理",
            "气死我了",
            "你们就是骗子，再不退钱我就去投诉",
            "在吗在吗",
            "你好",
            "我要退火，这个商品质量太差了",
            "碎屏显咋换",
        ],
    )
    def test_router_intent_matches_classify_intent(self, query):
        ctx = router.route(SharedContext(query=query))
        assert ctx.intent == classify_intent(query), query
