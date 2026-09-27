"""模型单测（BU-01 DoD + 规划书红线⑨）：全表含 tenant_id。

注：Quota ORM 模型已于 L5 移除（配额改 Redis 原子闸门）；2026-09-06 额度系统
整体移除后 Redis 闸门亦下线，从未有 quotas 表。故下表集合为 10 张。

A6（审计 M4 2026-09-27）：本文件前三条只验 **schema 形状**，不验任何一条查询——于是
eval.py / audit_logs.py 整文件零 tenant 时这里照样全绿。末尾那条把根目录的查询级尺子
scripts/check_tenant_filters.py 接进 pytest，让红线⑨ 在「列存在」和「查询真过滤」两层
都有东西守着。（2026-09-27 修正反向幽灵标注：ci.yml 早已把该脚本接为独立 step
「Tenant filter check」，本文件的 pytest 接线是其第二执行点——本地/推 CI 前同样会红，
不是「CI 未接前」的临时替身。）
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

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


def test_tenant_query_ruler_passes() -> None:
    """红线⑨ 查询级尺子必须为绿（A6）：所有对带 tenant 模型的 select() 都显式过滤或有登记豁免。

    尺子本体在仓库根 scripts/check_tenant_filters.py（纯标准库 AST，与
    check_baseline_hashes.py 同族）。这里跑它的 main() 而非 subprocess：脚本内的 ROOT 由
    自身 __file__ 推导，与测试 cwd 无关。被守住的失效形态：任何人新增/改动一条漏 tenant
    的读查询，或把豁免条目对应的函数改名（→ 失配也算红）。
    """
    script = Path(__file__).resolve().parents[2] / "scripts" / "check_tenant_filters.py"
    assert script.is_file(), f"红线⑨ 查询级尺子缺失：{script}"
    spec = importlib.util.spec_from_file_location("check_tenant_filters", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main() == 0, "check_tenant_filters.py 判定失败（详见上方 [FAIL] 明细）"
