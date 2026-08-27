"""Router 测试：分发规则 + 单一真源一致性 + 执行计划诚实性。

对抗审查 2026-08-27：执行计划（agents_invoked）只说真话——只含 chat 层真实
编排的 Agent（image/ticket）；QA 由 chat 层直调 stream_answer，不是 Agent 成员。
旧版计划恒含 "qa_agent" 但生产从不执行，属假契约，已随审查删除。
"""
from __future__ import annotations

import pytest
from app.services.agents.router import IMAGE_AGENT, TICKET_AGENT, router
from app.services.rag_service import classify_intent
from app.services.shared_context import SharedContext


def _route(query: str, image_refs: list[str] | None = None) -> SharedContext:
    return router.route(SharedContext(query=query, image_refs=image_refs or []))


class TestRouteRules:
    def test_qa_pure_plan_is_empty(self):
        """纯 qa：无 Agent 编排（chat 层直走 stream_answer，计划不列假成员）。"""
        ctx = _route("保修多久")
        assert ctx.intent == "qa"
        assert ctx.agents_invoked == []

    def test_handoff_complaint_adds_ticket_agent(self):
        ctx = _route("我要投诉找经理")
        assert ctx.intent == "handoff"
        assert ctx.agents_invoked == [TICKET_AGENT]

    def test_handoff_emotion_adds_ticket_agent(self):
        ctx = _route("气死我了，马上解决！")
        assert ctx.intent == "handoff"
        assert TICKET_AGENT in ctx.agents_invoked

    def test_chitchat_no_agent(self):
        ctx = _route("在吗")
        assert ctx.intent == "chitchat"
        assert ctx.agents_invoked == []

    def test_image_first_when_image_present(self):
        ctx = _route("这是什么问题", image_refs=["ref:jpg"])
        assert ctx.agents_invoked[0] == IMAGE_AGENT  # Image 必先行

    def test_image_and_handoff_both(self):
        ctx = _route("气死我了，看图", image_refs=["ref:png"])
        assert ctx.agents_invoked == [IMAGE_AGENT, TICKET_AGENT]


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


class TestPlanHonesty:
    """执行计划诚实性契约（对抗审查 2026-08-27 落地）：
    agents_invoked 只含 chat 层真实编排的 Agent（image/ticket），无假成员。
    """

    @pytest.mark.parametrize(
        "query",
        [
            "保修多久",
            "我要退货",
            "转人工",
            "我要投诉找经理",
            "气死我了",
            "在吗在吗",
            "你好",
        ],
    )
    def test_plan_members_only_image_or_ticket(self, query):
        ctx = router.route(SharedContext(query=query))
        for agent in ctx.agents_invoked:
            assert agent in (IMAGE_AGENT, TICKET_AGENT), f"计划含假成员 {agent}"

    # 一致性契约：handoff ⇔ 计划必排 TicketAgent（建单触发链 Router 判定与
    # stream 内 intent 事件同源；若未来两次分类漂移，此处先红）
    @pytest.mark.parametrize(
        "query,expect_handoff",
        [
            ("保修多久", False),
            ("我要退货", False),
            ("转人工", True),
            ("我要投诉找经理", True),
            ("气死我了", True),
            ("在吗", False),
            ("你们就是骗子，再不退钱我就去投诉", True),
        ],
    )
    def test_ticket_agent_scheduled_iff_handoff(self, query, expect_handoff):
        ctx = router.route(SharedContext(query=query))
        assert (TICKET_AGENT in ctx.agents_invoked) == expect_handoff, query
