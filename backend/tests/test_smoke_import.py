"""smoke_import 自愈测试（2026-08-27）：幂等 skip 前校验向量完整性，缺向量强制重导。

背景事故：首次导入时部分 chunk 向量写入失败而文档仍标 indexed，幂等 skip（只查 sha256）
让缺向量永远补不齐（会员权益.md 实测丢 2 向量）。本测试锁定：
- ``_count_doc_points`` 按 kb_id+doc_id 过滤统计（count_filter 构造断言）；
- ``_doc_vector_integrity`` 判齐 / 判缺两场景（fake db + monkeypatch，不连真实 Qdrant）。
"""
from __future__ import annotations

import pytest
from app.models.knowledge import DocumentStatus
from scripts.smoke_import import _count_doc_points, _doc_vector_integrity, _skip_or_repair


class _CountRes:
    def __init__(self, n: int) -> None:
        self.count = n


class _FakeQdrant:
    def __init__(self, n: int) -> None:
        self._n = n
        self.calls: list[dict] = []

    def count(self, **kwargs) -> _CountRes:
        self.calls.append(kwargs)
        return _CountRes(self._n)


class _FakeDb:
    def __init__(self, pg: int) -> None:
        self._pg = pg

    def query(self, _model):
        return _FakeQuery(self._pg)


class _FakeQuery:
    def __init__(self, pg: int) -> None:
        self._pg = pg

    def filter_by(self, **_kw):
        return self

    def count(self) -> int:
        return self._pg


class _FakeDoc:
    def __init__(self, status: DocumentStatus) -> None:
        self.status = status
        self.id = "doc-1"


def test_count_doc_points_filters_by_kb_and_doc() -> None:
    client = _FakeQdrant(3)
    n = _count_doc_points(client, "lingxi_hybrid_bge_768", "kb-1", "doc-1")
    assert n == 3
    call = client.calls[0]
    assert call["collection_name"] == "lingxi_hybrid_bge_768"
    assert call["exact"] is True
    must = call["count_filter"].must
    assert {m.key: m.match.value for m in must} == {"kb_id": "kb-1", "doc_id": "doc-1"}


def test_integrity_matching_returns_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("scripts.smoke_import._count_doc_points", lambda _c, _col, _k, _d: 2)
    assert _doc_vector_integrity(_FakeDb(2), "kb-1", "doc-1") is True


def test_integrity_missing_vectors_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("scripts.smoke_import._count_doc_points", lambda _c, _col, _k, _d: 0)
    assert _doc_vector_integrity(_FakeDb(2), "kb-1", "doc-1") is False


def test_skip_indexed_and_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("scripts.smoke_import._doc_vector_integrity", lambda _db, _k, _d: True)
    assert _skip_or_repair(_FakeDoc(DocumentStatus.indexed), None, "kb-1") is True


def test_repair_indexed_but_missing_vectors(monkeypatch: pytest.MonkeyPatch) -> None:
    """indexed 但向量缺失（历史事故主场景）→ 需 REPAIR，不 SKIP。"""
    monkeypatch.setattr("scripts.smoke_import._doc_vector_integrity", lambda _db, _k, _d: False)
    assert _skip_or_repair(_FakeDoc(DocumentStatus.indexed), None, "kb-1") is False


def test_repair_failed_stub_doc() -> None:
    """failed stub（chunks=0/vec=0）不得误判为可 SKIP——否则自愈被卡死（实测）。"""
    assert _skip_or_repair(_FakeDoc(DocumentStatus.failed), None, "kb-1") is False
