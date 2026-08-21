"""订单类 demo 数据检索回归（B 层）：真实检索 demo KB，断言召回正确 chunk。

依据决策（2026-08-20）：订单 demo 数据已灌入 demo KB（scripts/demo_data/ 新增 2 份订单文档）。
- 断言用 **检索召回**（chunks 命中正确业务键），不依赖 LLM 语义 → 确定性、零 LLM 成本；
- 前置：目标 KB 需已导入订单 demo 文档；否则 `pytest.skip`（提示先 seed），不炸无关环境；
- 走真实 Qdrant + 本地 embedding（测试环境有依赖；CI 需先 seed demo 数据）。

seed：docker compose exec api python scripts/seed_demo_data.py <kb_id>（幂等）。
"""
from __future__ import annotations

import sqlalchemy as sa
import pytest
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.knowledge import KnowledgeBase, Document
from app.services.retrieval_service import search_kb

#: 订单 demo 数据的检索锚点（预期召回的业务键）
CASES = {
    "已发货改地址": ("订单已发货想改收货地址", "拦截"),
    "物流超时催单": ("快递快三天没更新帮我催一下", "48 小时"),
    "查无订单": ("帮我查订单 999999", "999999"),
    # 退款延迟属退款政策文档，不在"改地址"文档（原 golden 错位导致 top_k=5 后必失配）。
    "退款超过 7 天": ("退款说三天到账都七天了还没到", "支付与退款"),
    "多实体整合(尾号8823/洗衣机/签收未收到)": (
        "订单尾号 8823 洗衣机显示已签收但我没收到货",
        "8823",
    ),
}


@pytest.fixture(scope="module")
def demo_kb_id() -> str:
    """定位最新 demo KB；若库中无任何订单 demo 文档则跳过整组。"""
    db = SessionLocal()
    try:
        kb = db.scalar(
            sa.select(KnowledgeBase)
            .where(KnowledgeBase.tenant_id == settings.TENANT_DEFAULT)
            .order_by(KnowledgeBase.created_at.desc())
            .limit(1)
        )
        assert kb is not None, "知识库为空，请先创建并导入 demo 数据"
        # 确认订单类 demo 文档已导入（改地址/综合案例标题特征）
        has_order_doc = db.scalar(
            sa.select(Document.id)
            .where(
                Document.kb_id == kb.id,
                Document.status == "indexed",
                Document.name.like("%改地址%"),
            )
            .limit(1)
        )
        if not has_order_doc:
            pytest.skip("未导入订单 demo 数据，请先执行 seed_demo_data.py 后重跑")
        return str(kb.id)
    finally:
        db.close()


@pytest.mark.parametrize("label,spec", list(CASES.items()), ids=list(CASES.keys()))
def test_order_demo_recall(demo_kb_id: str, label: str, spec: tuple[str, str]):
    """检索应命中指定业务键（打断点：业务内容被改/误删时红）。"""
    from uuid import UUID

    query, must_hit = spec
    chunks = search_kb(query, UUID(demo_kb_id), top_k=settings.RETRIEVAL_TOP_K)  # 跟随降噪口径(=5)
    assert chunks, f"{label}: 无检索结果（KB 空/嵌入异常）"
    joined = " ".join(c.text for c in chunks)
    assert must_hit in joined, (
        f"{label}: 检索未命中业务键「{must_hit}」。命中内容示例: {joined[:200]}"
    )