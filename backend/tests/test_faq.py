"""FAQ API 测试（Phase 4）：/faq 公开无鉴权 + 文档名称级清单结构。"""
from __future__ import annotations

import uuid

import app.models.knowledge  # noqa: F401
import pytest
from app.core.database import get_db
from app.main import app
from app.models.base import Base
from app.models.knowledge import Chunk, Document, DocumentStatus, KnowledgeBase
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

API = "/api/v1"

KB_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
DOC1_ID = uuid.UUID("44444444-4444-4444-4444-444444444444")
DOC2_ID = uuid.UUID("55555555-5555-5555-5555-555555555555")


def _make_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine, tables=[KnowledgeBase.__table__, Document.__table__, Chunk.__table__]
    )
    return engine


@pytest.fixture
def client():
    engine = _make_engine()
    Local = sessionmaker(bind=engine, expire_on_commit=False)

    def _override():
        db = Local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    with Local() as db:
        kb = KnowledgeBase(id=KB_ID, name="退款政策", description="常见退款问题")
        db.add(kb)
        db.flush()
        db.add(Document(
            id=DOC1_ID,
            kb_id=KB_ID,
            name="七天无理由.md",
            status=DocumentStatus.indexed,
            chunk_count=3,
            sha256="a" * 64,
        ))
        db.add(Document(
            id=DOC2_ID,
            kb_id=KB_ID,
            name="退货流程.md",
            status=DocumentStatus.parsing,
            chunk_count=0,
            sha256="b" * 64,
        ))
        db.add(Chunk(
            id=uuid.uuid4(),
            kb_id=KB_ID,
            doc_id=DOC1_ID,
            idx=0,
            text="退款政策切片",
            hash="c" * 64,
        ))
        db.commit()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def empty_client():
    engine = _make_engine()
    Local = sessionmaker(bind=engine, expire_on_commit=False)

    def _override():
        db = Local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_faq_public_no_auth(client):
    """公开无鉴权：不带 token 也 200。"""
    r = client.get(f"{API}/faq")
    assert r.status_code == 200


def test_faq_structure_with_kb_and_docs(client):
    """有 KB + 文档时结构正确（名称级信息，无 chunk 全文）。"""
    r = client.get(f"{API}/faq")
    assert r.status_code == 200
    data = r.json()
    assert len(data["items"]) == 1
    kb = data["items"][0]
    assert kb["kb_id"] == str(KB_ID)
    assert kb["kb_name"] == "退款政策"
    assert kb["description"] == "常见退款问题"
    assert kb["doc_count"] == 2
    assert kb["chunk_count"] == 1  # 只插入了 1 条 Chunk 记录
    docs = kb["docs"]
    assert len(docs) == 2
    by_name = {d["name"]: d for d in docs}
    assert by_name["七天无理由.md"]["doc_id"] == str(DOC1_ID)
    assert by_name["七天无理由.md"]["status"] == "indexed"
    assert by_name["七天无理由.md"]["chunks"] == 3
    assert by_name["退货流程.md"]["status"] == "parsing"
    # 名称级字段集合（doc_id/name/status/chunks），不含 chunk 全文
    for d in docs:
        assert set(d.keys()) == {"doc_id", "name", "status", "chunks"}


def test_faq_empty_db(empty_client):
    """空库：items 为空。"""
    r = empty_client.get(f"{API}/faq")
    assert r.status_code == 200
    assert r.json()["items"] == []


def test_quick_kb_coverage_detects_drift():
    """P4：快捷话术 vs KB 双源覆盖校验——KB 覆盖话题不告警，无覆盖话题报警（漂移）。

    5-2 后 check_kb_coverage 返回 bool（门禁），逐题漂移明细改由 uncovered_questions 提供。
    """
    from app.services.quick_answers import uncovered_questions

    covered = uncovered_questions("手机整机保修 12 个月；七天无理由退货可申请。")
    assert "保修多久？" not in covered, f"保修话题有 KB 依据，不应告警: {covered}"
    assert "七天无理由退货怎么申请？" not in covered

    drifted = uncovered_questions("仅包含保修与退货相关内容")
    assert "可以开发票吗？" in drifted, f"发票话题无 KB 依据，应告警: {drifted}"
    assert "支持哪些支付方式？" in drifted


def test_quick_kb_coverage_gate_threshold():
    """5-2 门禁：过半话术有依据 → 通过（True）；过半失据 → 不通过（False）。"""
    from app.services.quick_answers import check_kb_coverage

    # 覆盖发票+保修话题（连带"怎么/多久"泛词命中）→ 过半话术有依据 → 通过
    assert check_kb_coverage("怎么开发票 保修多久") is True
    # 与全部话术零交集 → 全部未覆盖 → 不通过
    assert check_kb_coverage("量子力学波动方程与算符对易关系") is False
