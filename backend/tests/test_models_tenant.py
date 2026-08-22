"""模型单测（BU-01 DoD + 规划书红线⑨）：全表含 tenant_id。

注：Quota ORM 模型已于 L5 移除（配额改为 Redis 原子闸门实现，
见 services/quota.py 与 test_quota.py），故下表集合为 10 张。
"""
from __future__ import annotations

from app.models import Base


def test_all_tables_have_tenant_id_column() -> None:
    """Base.metadata 中每一张表都必须含 tenant_id 列。"""
    tables = Base.metadata.tables
    assert len(tables) >= 10, f"预期至少 10 张表，实际 {len(tables)}"
    for name, table in tables.items():
        assert "tenant_id" in table.columns, f"表 {name} 缺少 tenant_id 列"


def test_tenant_id_is_first_non_pk_column() -> None:
    """每个模型的第一个非主键列必须是 tenant_id（BU-01 spec §2.2）。"""
    for name, table in Base.metadata.tables.items():
        pk_cols = {c.name for c in table.primary_key.columns}
        first_non_pk = next(c.name for c in table.columns if c.name not in pk_cols)
        assert first_non_pk == "tenant_id", f"表 {name} 第一个非主键列应为 tenant_id，实际为 {first_non_pk}"


def test_all_tables_have_tenant_id_index() -> None:
    """每张表的 tenant_id 都建了索引（支撑 Phase3 行级过滤）。"""
    for name, table in Base.metadata.tables.items():
        index_names = {ix.name for ix in table.indexes}
        assert f"ix_{name}_tenant_id" in index_names, f"表 {name} 缺少 ix_{name}_tenant_id 索引"


def test_expected_table_set_present() -> None:
    """覆盖规划 §4.1 的全部 10 张表（L5 后 Quota 改 Redis，不再有 quotas 表）。"""
    tables = set(Base.metadata.tables)
    assert {
        "users",
        "sessions",
        "messages",
        "message_sources",
        "knowledge_bases",
        "documents",
        "chunks",
        "chunk_context",
        "feedback",
        "tickets",
    } <= tables
