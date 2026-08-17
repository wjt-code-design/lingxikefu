"""Audit Logs API 测试（Phase 4）：埋点后查询可见 / 筛选 / 分页 / 权限。

- 通过真实端点触发埋点（改角色 / 建 KB / 传文档 / 删文档），再查 /admin/audit-logs；
- actor_email 由 audit_service 从 User 表按 actor_id 补全。
"""
from __future__ import annotations

import uuid

import app.models.knowledge  # noqa: F401
import pytest
from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.audit import AuditLog
from app.models.base import Base
from app.models.knowledge import Chunk, Document, KnowledgeBase
from app.models.user import User, UserRole
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

API = "/api/v1"

ADMIN = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
USER = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def _h(uid: uuid.UUID, role: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(subject=str(uid), role=role)}"}


class _FakeTask:
    """模拟 Celery AsyncResult：delay 成功返回（不走后台线程导入）。"""

    @staticmethod
    def delay(doc_id: str):
        return None


@pytest.fixture
def client(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            AuditLog.__table__,
            KnowledgeBase.__table__,
            Document.__table__,
            Chunk.__table__,
        ],
    )
    Local = sessionmaker(bind=engine, expire_on_commit=False)

    def _override():
        db = Local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    with Local() as db:
        db.add(User(id=ADMIN, email="admin@b.com", role=UserRole.admin, tenant_id="default", password_hash="x"))
        db.add(User(id=USER, email="u@b.com", role=UserRole.user, tenant_id="default", password_hash="x"))
        db.commit()

    # 上传后导入调度 mock 为同步成功；删除时清向量 mock 为空操作
    monkeypatch.setattr("app.workers.import_worker.import_document_task", _FakeTask())
    monkeypatch.setattr("app.api.knowledge.vector_service.delete_by_doc_id", lambda doc_id: None)

    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _trigger_audits(client) -> None:
    """触发 4 条埋点：user.role（改角色）+ kb.create + doc.upload + doc.delete。"""
    admin_headers = _h(ADMIN, "admin")
    # user.role：把 user 提升为 agent（绕过 admin 自保护 + 最后一个 admin 校验）
    r = client.put(f"{API}/admin/users/{USER}/role", json={"role": "agent"}, headers=admin_headers)
    assert r.status_code == 200
    # kb.create
    r = client.post(f"{API}/knowledge-bases", json={"name": "审计测试库"}, headers=admin_headers)
    assert r.status_code == 201
    kb_id = r.json()["kb_id"]
    # doc.upload（mock 导入，文档停在 parsing）
    r = client.post(
        f"{API}/knowledge-bases/{kb_id}/documents",
        files={"file": ("退款.md", "退款政策内容".encode(), "text/markdown")},
        headers=admin_headers,
    )
    assert r.status_code == 201
    doc_id = r.json()["doc_id"]
    # doc.delete
    r = client.delete(f"{API}/documents/{doc_id}", headers=admin_headers)
    assert r.status_code == 200


def test_audit_logs_visible_after_hooks(client):
    """埋点后查询可见：4 条 action（user.role/kb.create/doc.upload/doc.delete）都在。"""
    _trigger_audits(client)
    r = client.get(f"{API}/admin/audit-logs", headers=_h(ADMIN, "admin"))
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 4
    actions = {item["action"] for item in data["items"]}
    assert actions == {"user.role", "kb.create", "doc.upload", "doc.delete"}
    # actor_email 从 User 表按 actor_id 补全（操作者恒为 admin）
    assert all(item["actor_email"] == "admin@b.com" for item in data["items"])


def test_audit_logs_filter_action_resource(client):
    """action / resource 筛选生效。"""
    _trigger_audits(client)
    headers = _h(ADMIN, "admin")
    r = client.get(f"{API}/admin/audit-logs?action=kb.create", headers=headers)
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    assert data["items"][0]["action"] == "kb.create"
    assert data["items"][0]["resource"] == "knowledge_base"

    r = client.get(f"{API}/admin/audit-logs?resource=document", headers=headers)
    data = r.json()
    assert data["total"] == 2
    assert {i["action"] for i in data["items"]} == {"doc.upload", "doc.delete"}


def test_audit_logs_filter_actor(client):
    """actor（模糊 actor_email）筛选生效。"""
    _trigger_audits(client)
    r = client.get(f"{API}/admin/audit-logs?actor=b.com", headers=_h(ADMIN, "admin"))
    assert r.status_code == 200
    assert r.json()["total"] == 4
    r = client.get(f"{API}/admin/audit-logs?actor=u@", headers=_h(ADMIN, "admin"))
    assert r.json()["total"] == 0  # u@b.com 不是操作者（操作者恒为 admin）


def test_audit_logs_pagination_total(client):
    """分页：total 为真实总数，items 按页返回。"""
    _trigger_audits(client)
    headers = _h(ADMIN, "admin")
    r = client.get(f"{API}/admin/audit-logs?page=1&size=2", headers=headers)
    data = r.json()
    assert data["total"] == 4
    assert len(data["items"]) == 2
    r = client.get(f"{API}/admin/audit-logs?page=2&size=2", headers=headers)
    data = r.json()
    assert data["total"] == 4
    assert len(data["items"]) == 2


def test_audit_logs_forbidden_for_user(client):
    """非 admin → 403。"""
    r = client.get(f"{API}/admin/audit-logs", headers=_h(USER, "user"))
    assert r.status_code == 403
