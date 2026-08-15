"""Knowledge API 测试（BU-04）：权限 / KB CRUD / 上传去重 / 删除。

- SQLite StaticPool + get_db 覆盖；admin token 用 create_access_token 直造（不依赖 DB 用户）；
- 上传后导入调度 mock 为"同步立即完成"（不走 Celery / Qdrant / bge）。
"""
from __future__ import annotations

import app.models.knowledge  # noqa: F401
import pytest
from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.base import Base
from app.models.knowledge import Chunk, Document, KnowledgeBase
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

API = "/api/v1"


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine, tables=[KnowledgeBase.__table__, Document.__table__, Chunk.__table__]
    )
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


def _headers(role: str = "admin"):
    return {"Authorization": f"Bearer {create_access_token('u1', role)}"}


class _FakeTask:
    """模拟 Celery AsyncResult：delay 成功返回（不走 API 降级同步路径）。"""

    @staticmethod
    def delay(doc_id: str):
        return None


def _create_kb(client, name="测试库") -> str:
    r = client.post(f"{API}/knowledge-bases", json={"name": name}, headers=_headers())
    assert r.status_code == 201
    return r.json()["kb_id"]


def test_non_admin_forbidden(client):
    r = client.get(f"{API}/knowledge-bases", headers=_headers("user"))
    assert r.status_code == 403
    r = client.post(f"{API}/knowledge-bases", json={"name": "x"}, headers=_headers("user"))
    assert r.status_code == 403


def test_unauthenticated_401(client):
    assert client.get(f"{API}/knowledge-bases").status_code == 401


def test_kb_crud(client):
    # 创建
    kb_id = _create_kb(client)
    # 列表
    r = client.get(f"{API}/knowledge-bases", headers=_headers())
    assert r.status_code == 200
    data = r.json()
    assert data["items"][0]["kb_id"] == kb_id
    assert data["items"][0]["name"] == "测试库"
    assert data["items"][0]["doc_count"] == 0


def test_upload_and_list_documents(client, monkeypatch):
    kb_id = _create_kb(client)

    # mock 导入调度：delay 成功返回（文档停在 parsing，异步完成由服务层测试覆盖）
    monkeypatch.setattr("app.workers.import_worker.import_document_task", _FakeTask())

    content = "七天无理由退货政策。\n\n质量问题十五天内可退。".encode()
    r = client.post(
        f"{API}/knowledge-bases/{kb_id}/documents",
        files={"file": ("退换货.md", content, "text/markdown")},
        headers=_headers(),
    )
    assert r.status_code == 201
    doc = r.json()
    assert doc["name"] == "退换货.md"
    assert doc["status"] == "parsing"  # 异步未完成（mock delay 未跑）

    # 列表
    r = client.get(f"{API}/knowledge-bases/{kb_id}/documents", headers=_headers())
    assert r.status_code == 200
    assert len(r.json()["items"]) == 1


def test_upload_duplicate_sha256_idempotent(client, monkeypatch):
    kb_id = _create_kb(client)
    content = "相同内容".encode()

    monkeypatch.setattr("app.workers.import_worker.import_document_task", _FakeTask())
    headers = _headers()
    r1 = client.post(
        f"{API}/knowledge-bases/{kb_id}/documents",
        files={"file": ("a.md", content, "text/markdown")},
        headers=headers,
    )
    r2 = client.post(
        f"{API}/knowledge-bases/{kb_id}/documents",
        files={"file": ("a.md", content, "text/markdown")},
        headers=headers,
    )
    assert r1.status_code == 201 and r2.status_code == 201
    assert r1.json()["doc_id"] == r2.json()["doc_id"]  # 幂等：同文档
    # 列表仅 1 条
    r = client.get(f"{API}/knowledge-bases/{kb_id}/documents", headers=headers)
    assert len(r.json()["items"]) == 1


def test_upload_unsupported_type_400(client):
    kb_id = _create_kb(client)
    r = client.post(
        f"{API}/knowledge-bases/{kb_id}/documents",
        files={"file": ("a.docx", b"xx", "application/octet-stream")},
        headers=_headers(),
    )
    assert r.status_code == 400


def test_delete_document(client, monkeypatch):
    kb_id = _create_kb(client)
    from app.services import vector_service as vs

    deleted = []

    def _delete(doc_id):
        deleted.append(str(doc_id))

    monkeypatch.setattr(vs, "delete_by_doc_id", _delete)
    monkeypatch.setattr("app.workers.import_worker.import_document_task", _FakeTask())

    # 通过 API 上传后删除
    r = client.post(
        f"{API}/knowledge-bases/{kb_id}/documents",
        files={"file": ("b.txt", b"content", "text/plain")},
        headers=_headers(),
    )
    doc_id = r.json()["doc_id"]
    r = client.delete(f"{API}/documents/{doc_id}", headers=_headers())
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert deleted == [doc_id]
    # 列表为空
    r = client.get(f"{API}/knowledge-bases/{kb_id}/documents", headers=_headers())
    assert len(r.json()["items"]) == 0


def test_delete_missing_document_404(client):
    r = client.delete(f"{API}/documents/00000000-0000-0000-0000-000000000000", headers=_headers())
    assert r.status_code == 404
