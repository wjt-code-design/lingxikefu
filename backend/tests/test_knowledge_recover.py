"""启动恢复钩子测试（第6组项2）：recover_stale_imports 把滞留中间态文档标 failed。

失败路径（pitfall C）：进程被强杀后 parsing/embedding 文档会永久卡——
恢复钩子必须只处理这两个中间态，且不误伤 indexed/failed，二次调用幂等。
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.knowledge import Document, DocumentStatus, KnowledgeBase
from app.models.base import Base
from app.services.knowledge_import_service import recover_stale_imports, _STALE_ERROR

KB_ID = uuid.uuid4()


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[KnowledgeBase.__table__, Document.__table__])
    Local = sessionmaker(bind=engine, expire_on_commit=False)

    def mk(status: DocumentStatus, name: str) -> Document:
        return Document(
            id=uuid.uuid4(), tenant_id="default", kb_id=KB_ID, name=name,
            sha256=uuid.uuid4().hex, status=status, chunk_count=0,
        )

    with Local() as s:
        s.add(KnowledgeBase(id=KB_ID, tenant_id="default", name="kb"))
        for st in (DocumentStatus.parsing, DocumentStatus.embedding,
                   DocumentStatus.indexed, DocumentStatus.failed):
            s.add(mk(st, f"doc-{st.value}"))
        s.commit()
    yield Local


def _statuses(Local) -> dict[str, DocumentStatus]:
    with Local() as s:
        return {d.name: d.status for d in s.scalars(select(Document)).all()}


def test_recover_marks_intermediate_states_failed(db):
    """parsing/embedding → failed；indexed/failed 不变；返回被恢复数。"""
    with db() as s:
        n = recover_stale_imports(s)
    assert n == 2
    statuses = _statuses(db)
    assert statuses["doc-parsing"] == DocumentStatus.failed
    assert statuses["doc-embedding"] == DocumentStatus.failed
    assert statuses["doc-indexed"] == DocumentStatus.indexed
    assert statuses["doc-failed"] == DocumentStatus.failed


def test_recover_sets_operational_error(db):
    """恢复后 error 为可操作提示（供用户知道需重新上传）。"""
    with db() as s:
        recover_stale_imports(s)
    with db() as s:
        doc = s.scalars(select(Document).where(Document.name == "doc-parsing")).one()
        assert doc.error == _STALE_ERROR


def test_recover_idempotent(db):
    """二次调用：无滞留中间态 → 返回 0，且不动任何文档。"""
    with db() as s:
        assert recover_stale_imports(s) == 2
        assert recover_stale_imports(s) == 0
    statuses = _statuses(db)
    assert all(v in (DocumentStatus.indexed, DocumentStatus.failed) for v in statuses.values())