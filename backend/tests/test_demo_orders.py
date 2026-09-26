"""订单类 demo 数据检索回归（B 层）：真实检索 demo KB，断言召回正确 chunk。

依据决策（2026-08-20）：订单 demo 数据已灌入 demo KB（scripts/demo_data/ 新增 2 份订单文档）。
- 断言用 **检索召回**（chunks 命中正确业务键），不依赖 LLM 语义 → 确定性、零 LLM 成本；
- 前置：目标 KB 需已导入订单 demo 文档；**未导入即 fail**（见 A4 说明），不静默少跑；
- 走真实 Qdrant + 本地 embedding（测试环境有依赖；CI 需先 seed demo 数据）。

seed：docker compose exec api python scripts/seed_demo_data.py <kb_id>（幂等）。
"""
from __future__ import annotations

import pytest
import sqlalchemy as sa
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.knowledge import Document, KnowledgeBase
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

#: A4（审计 M4 2026-09-27）：只有「拿不到可用 DB 会话」才允许 skip。旧实现 `except Exception`
#: 把一切兜成 skip 且文案写「依赖未就绪」——本机真因其实是服务器在监听、口令被拒。
#: 凭据类失败同样归入 skip（无凭据的开发机/容器首启本就跑不了真库），但文案必须说真因；
#: 表缺失（ProgrammingError）与 seed 数据缺失必须 fail——CI 裸 `pytest` 不报 skip，
#: 兜成 skip 等于 seed 步骤坏了还静默少跑 5 条、job 照绿。
_UNREACHABLE_MARKERS = (
    "connection refused",
    "connection failed",
    "could not connect",
    "no connection could be made",
    "getaddrinfo failed",
    "timed out",
    "timeout expired",
    "server closed the connection unexpectedly",
    "authentication failed",  # 含 password/peer 两类：服务器可达但我方无有效凭据
)


def _db_unreachable(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(m in text for m in _UNREACHABLE_MARKERS)


@pytest.fixture(scope="module")
def demo_kb_id() -> str:
    """定位最新 demo KB；连不上 DB 才 skip（文案含真因），无 KB/无订单文档一律 fail。"""
    db = SessionLocal()
    try:
        kb = db.scalar(
            sa.select(KnowledgeBase)
            .where(KnowledgeBase.tenant_id == settings.TENANT_DEFAULT)
            .order_by(KnowledgeBase.created_at.desc())
            .limit(1)
        )
        has_order_doc = (
            db.scalar(
                sa.select(Document.id)
                .where(
                    Document.kb_id == kb.id,
                    Document.status == "indexed",
                    Document.name.like("%改地址%"),
                )
                .limit(1)
            )
            if kb
            else None
        )
    except Exception as exc:
        if _db_unreachable(exc):
            pytest.skip(f"PG 会话不可用（非 seed 问题，真因如下）: {exc}")
        raise
    finally:
        db.close()

    if kb is None:
        pytest.fail("租户内无任何知识库 —— seed 步骤坏掉了，请执行 scripts/seed_demo_data.py")
    if not has_order_doc:
        pytest.fail(f"KB {kb.id} 未导入订单 demo 文档（标题需含「改地址」）—— seed 步骤坏掉了")
    return str(kb.id)


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
