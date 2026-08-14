"""SQLAlchemy 2.0 声明式基类与 tenant_id 列工厂。

**全表 tenant_id（红线⑨ / ADR-2）**：每个模型在 id 之后**显式**声明 tenant_id 列，
通过 `tenant_id_column()` 统一配置（单一真源），保证列顺序为「id → tenant_id → 其余列」，
与迁移 `versions/0001_initial.py` 严格一致。
"""
from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.config import settings


class Base(DeclarativeBase):
    """所有 ORM 模型必须继承的基类。"""


def tenant_id_column() -> Mapped[str]:
    """构造 tenant_id 列定义。

    - MVP 固定单值 settings.TENANT_DEFAULT（"default"）；
    - Phase3 多租户时启用行级过滤，本列建索引支撑过滤条件；
    - server_default 让 DB 侧也有默认值，与迁移保持一致。
    """
    return mapped_column(
        String(64),
        nullable=False,
        index=True,
        default=settings.TENANT_DEFAULT,
        server_default=settings.TENANT_DEFAULT,
    )
