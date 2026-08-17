"""Knowledge Search API 测试（Phase 4）：/knowledge/search 参数校验 + 检索结构 + 503。

- search_kb（同步阻塞 embedding）mock 为纯函数，不依赖真实 Qdrant / bge；
- 需要登录（get_current_user），非 admin（agent 客服用）。
"""
from __future__ import annotations

import uuid

import app.models.knowledge  # noqa: F401
import pytest
from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.base import Base
from app.models.knowledge import Document, DocumentStatus, KnowledgeBase
from app.services.retrieval_service import RetrievedChunk, RetrievalError
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

API = "/api/v1"

KB_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
DOC_ID = uuid.UUID("44444444-4444-4444-4444-444444444444")


def _h(role: str = "agent") -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token('u1', role)}"}


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[KnowledgeBase.__table__, Document.__table__])
    Local = sessionmaker(bind=engine, expire_on_commit=False)

    def _override():
        db = Local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    with Local() as db:
        db.add(KnowledgeBase(id=KB_ID, name="客服知识库", description=""))
        db.add(Document(
            id=DOC_ID,
            kb_id=KB_ID,
            name="退款政策.md",
            sha256="a" * 64,
            status=DocumentStatus.indexed,
        ))
        db.commit()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _post(client, **body):
    return client.post(f"{API}/knowledge/search", json=body, headers=_h())


def test_search_missing_kb_id(client):
    r = _post(client, query="退货")
    assert r.status_code == 422


def test_search_invalid_kb_id(client):
    r = _post(client, query="退货", kb_id="not-a-uuid")
    assert r.status_code == 422


def test_search_empty_query(client):
    r = _post(client, query="", kb_id=str(KB_ID))
    assert r.status_code == 422


def test_search_structure(client, monkeypatch):
    def fake_search(query, kb_id, top_k=8):
        return [
            RetrievedChunk(
                chunk_id="chunk-1",
                doc_id=str(DOC_ID),
                kb_id=str(KB_ID),
                idx=0,
                text="七天无理由退货政策，质量问题十五天内可退。",
                score=0.91,
                dense_score=0.88,
            )
        ]

    monkeypatch.setattr("app.api.knowledge_search.search_kb", fake_search)
    r = _post(client, query="退货政策", kb_id=str(KB_ID), top_k=5)
    assert r.status_code == 200
    data = r.json()
    assert data["query"] == "退货政策"
    assert len(data["hits"]) == 1
    hit = data["hits"][0]
    assert hit["chunk_id"] == "chunk-1"
    assert hit["doc_id"] == str(DOC_ID)
    assert hit["doc_title"] == "退款政策.md"
    assert hit["kb_id"] == str(KB_ID)
    assert hit["kb_name"] == "客服知识库"
    assert hit["score"] == 0.91
    assert hit["dense_score"] == 0.88
    assert "七天无理由退货" in hit["snippet"]


def test_search_empty_hits(client, monkeypatch):
    monkeypatch.setattr("app.api.knowledge_search.search_kb", lambda query, kb_id, top_k=8: [])
    r = _post(client, query="不存在的知识", kb_id=str(KB_ID))
    assert r.status_code == 200
    assert r.json()["hits"] == []


def test_search_retrieval_error_503(client, monkeypatch):
    def boom(query, kb_id, top_k=8):
        raise RetrievalError("检索服务不可用")

    monkeypatch.setattr("app.api.knowledge_search.search_kb", boom)
    r = _post(client, query="退货", kb_id=str(KB_ID))
    assert r.status_code == 503


def test_search_requires_auth(client):
    r = client.post(f"{API}/knowledge/search", json={"query": "退货", "kb_id": str(KB_ID)})
    assert r.status_code == 401
