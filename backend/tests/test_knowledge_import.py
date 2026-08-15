"""知识库导入服务测试（BU-04）：状态机 / 切片 / 失败回滚 + document_service 解析。

- SQLite StaticPool（同 test_auth 约定），只建 knowledge 相关表；
- mock embedding 与 vector 层：不依赖真实 bge 模型与 Qdrant；
- PDF 解析用 reportlab 生成真实可解析 PDF（venv 已装；未装则 skip）。
"""
from __future__ import annotations

import io
from uuid import uuid4

import app.models.knowledge  # noqa: F401  注册表到 Base.metadata
import pytest
from app.models.base import Base
from app.models.knowledge import Chunk, Document, DocumentStatus, KnowledgeBase
from app.repositories.document_repo import ChunkRepository, DocumentRepository, KnowledgeBaseRepository
from app.services.document_service import UnsupportedFileError, extract_text
from app.services.knowledge_import_service import ImportError_, import_document
from app.services.vector_service import VectorStoreError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


class FakeEmbedding:
    dim = 768

    def embed(self, texts):
        return [[0.1] * self.dim for _ in texts]


class FakeVector:
    upsert_calls = 0
    delete_calls = 0

    def reset(self):
        self.upsert_calls = 0
        self.delete_calls = 0

    def upsert_document(self, *_a, **_k):
        self.upsert_calls += 1
        return 0

    def delete_by_doc_id(self, *_a, **_k):
        self.delete_calls += 1


fake_vector = FakeVector()


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[KnowledgeBase.__table__, Document.__table__, Chunk.__table__],
    )
    Local = sessionmaker(bind=engine, expire_on_commit=False)
    session = Local()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def _mock_ext(monkeypatch):
    fake_vector.reset()
    monkeypatch.setattr(
        "app.services.knowledge_import_service.get_embedding_client", lambda: FakeEmbedding()
    )
    monkeypatch.setattr("app.services.knowledge_import_service.vector_service", fake_vector)
    yield


def _make_doc(db, *, name="a.txt", text="内容一。\n\n内容二。\n\n内容三。", status=DocumentStatus.parsing):
    kb = KnowledgeBaseRepository(db).create(name="测试库")
    return kb, DocumentRepository(db).create(
        kb_id=kb.id, name=name, sha256="x" * 64, status=status, raw_text=text
    )


# ---------- 导入状态机 ----------


def test_import_txt_success(db, _mock_ext):
    _, doc = _make_doc(db)
    result = import_document(doc.id, db)
    assert result.status == DocumentStatus.indexed
    assert result.chunk_count > 0
    assert fake_vector.upsert_calls == 1
    assert ChunkRepository(db).count_by_doc(doc.id) == result.chunk_count


def test_import_md_success(db, _mock_ext):
    _, doc = _make_doc(db, name="政策.md", text="# 标题\n\n正文段落足够长以产生多个切片。\n\n## 小标题\n\n更多内容。")
    result = import_document(doc.id, db)
    assert result.status == DocumentStatus.indexed
    assert result.chunk_count >= 1


def test_import_empty_text_fails(db, _mock_ext):
    _, doc = _make_doc(db, text="")
    with pytest.raises(ImportError_):
        import_document(doc.id, db)
    db.refresh(doc)
    assert doc.status == DocumentStatus.failed
    assert doc.error


def test_import_embedding_failure_no_chunks_left(db, _mock_ext, monkeypatch):
    _, doc = _make_doc(db)

    def _boom(_texts):
        raise RuntimeError("embedding 不可用")

    monkeypatch.setattr(
        "app.services.knowledge_import_service.get_embedding_client",
        lambda: type("B", (), {"dim": 768, "embed": _boom})(),
    )
    with pytest.raises(ImportError_):
        import_document(doc.id, db)
    db.refresh(doc)
    assert doc.status == DocumentStatus.failed
    assert ChunkRepository(db).count_by_doc(doc.id) == 0


def test_import_qdrant_failure_rolls_back_chunks(db, _mock_ext, monkeypatch):
    _, doc = _make_doc(db)

    def _fail(*_a, **_k):
        raise VectorStoreError("Qdrant 不可达")

    monkeypatch.setattr("app.services.knowledge_import_service.vector_service.upsert_document", _fail)
    with pytest.raises(ImportError_):
        import_document(doc.id, db)
    db.refresh(doc)
    assert doc.status == DocumentStatus.failed
    assert ChunkRepository(db).count_by_doc(doc.id) == 0  # 无假索引


def test_import_idempotent_rerun(db, _mock_ext):
    _, doc = _make_doc(db)
    import_document(doc.id, db)
    first = ChunkRepository(db).count_by_doc(doc.id)
    import_document(doc.id, db)
    db.refresh(doc)
    assert doc.status == DocumentStatus.indexed
    assert ChunkRepository(db).count_by_doc(doc.id) == first
    assert fake_vector.delete_calls >= 2


def test_import_missing_document(db, _mock_ext):
    with pytest.raises(ImportError_, match="不存在"):
        import_document(uuid4(), db)


# ---------- document_service 解析 ----------


def test_extract_text_txt():
    assert extract_text("a.txt", "你好".encode()) == "你好"


def test_extract_text_gbk_fallback():
    assert extract_text("b.txt", "中文".encode("gbk")) == "中文"


def test_extract_text_unsupported_extension():
    with pytest.raises(UnsupportedFileError, match="不支持"):
        extract_text("c.docx", b"xx")


def test_extract_text_pdf_success():
    pytest.importorskip("reportlab")
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfgen import canvas

    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.setFont("STSong-Light", 12)
    c.drawString(72, 720, "七天无理由退货政策")
    c.save()
    text = extract_text("退换货.pdf", buf.getvalue())
    assert "七天无理由退货政策" in text


def test_extract_text_pdf_no_text_fails():
    """扫描件 PDF（无文本层）→ UnsupportedFileError（不假成功，修 AegisDesk 坑）。"""
    pytest.importorskip("reportlab")
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.setTitle("空页")
    c.save()
    with pytest.raises(UnsupportedFileError, match="扫描件"):
        extract_text("scan.pdf", buf.getvalue())
