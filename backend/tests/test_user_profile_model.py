"""UserProfile 模型测试（Phase A，长期记忆数据层）。

覆盖：
- 模型可建/读/写（JSONB 画像字段往返）；
- profile 默认空对象 + version 初始 0；
- 乐观锁：version 不匹配更新被拒（防多 worker 并发丢更新）；
- 唯一约束 (tenant_id, user_id)：同租户同用户不重复；
- 级联删除：用户删除 → 画像级联清除。

真后端纪律：模型映射层用 SQLite 内存（create_all 建表，验证 ORM 映射与约束本身），
接线正确性由 API 层测试（Phase D）用真实编排路径覆盖。
"""
from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.user import User, UserRole
from app.models.user_profile import UserProfile

UID = uuid.UUID("22222222-2222-2222-2222-222222222222")


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # profile 是 JSONB + PG server_default（SQLite 无法编译）→ 建表前替换为 JSON（项目测试惯例）
    for c in UserProfile.__table__.columns:
        if c.name == "profile":
            c.type = sa.JSON()
            c.server_default = None
    Base.metadata.create_all(engine, tables=[User.__table__, UserProfile.__table__])
    Local = sessionmaker(bind=engine, expire_on_commit=False)
    with Local() as s:
        s.add(
            User(
                id=UID,
                email="profile@b.com",
                role=UserRole.user,
                tenant_id="default",
                password_hash="x",
            )
        )
        s.commit()
        yield s


def test_profile_create_read_jsonb_roundtrip(db) -> None:
    """写入画像 JSONB，读回结构完整。"""
    p = UserProfile(user_id=UID, tenant_id="default")
    db.add(p)
    db.commit()
    db.refresh(p)

    assert p.profile == {}  # 默认空对象
    assert p.version == 0
    # JSONB 结构往返（模拟规则聚合写入的画像结构）
    p.profile = {
        "schema_version": 1,
        "topics": {"退款": 2, "物流": 1},
        "entities": ["SO2026080118"],
    }
    db.commit()
    db.refresh(p)
    assert p.profile["topics"]["退款"] == 2
    assert p.profile["entities"] == ["SO2026080118"]


def test_profile_optimistic_lock_version_bump(db) -> None:
    """乐观锁：merge 后 version 递增；陈旧 version 更新被拒。"""
    p = UserProfile(user_id=UID, tenant_id="default")
    db.add(p)
    db.commit()
    db.refresh(p)

    # 正常更新：带当前 version
    upd = (
        sa.update(UserProfile)
        .where(
            UserProfile.user_id == UID,
            UserProfile.tenant_id == "default",
            UserProfile.version == p.version,
        )
        .values(profile={"topics": {"退款": 1}}, version=UserProfile.version + 1)
    )
    res = db.execute(upd)
    db.commit()
    assert res.rowcount == 1

    db.refresh(p)
    assert p.version == 1
    assert p.profile["topics"]["退款"] == 1

    # 陈旧 version（旧值 0）再更新 → 0 行受影响（并发丢更新防护）
    stale = (
        sa.update(UserProfile)
        .where(
            UserProfile.user_id == UID,
            UserProfile.tenant_id == "default",
            UserProfile.version == 0,
        )
        .values(profile={"topics": {"物流": 9}}, version=UserProfile.version + 1)
    )
    res2 = db.execute(stale)
    db.commit()
    assert res2.rowcount == 0
    db.refresh(p)
    assert p.profile["topics"].get("物流") is None  # 陈旧更新未生效


def test_profile_unique_tenant_user(db) -> None:
    """同租户同用户只能有一条画像。"""
    db.add(UserProfile(user_id=UID, tenant_id="default"))
    db.commit()
    db.add(UserProfile(user_id=UID, tenant_id="default"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_profile_cascade_delete_fk_declared() -> None:
    """隐私红线：画像级联删除必须声明在模型 FK（Postgres 迁移权威执行）。

    SQLite 内存测试默认关闭 FK 级联，故此处断言模型层面的 ondelete=CASCADE 已声明，
    真实的 DB 级联行为由迁移 upgrade/downgrade 在 Postgres 上验证（Phase A 迁移往返）。
    """
    fk = next(
        c.foreign_keys for c in UserProfile.__table__.columns if c.name == "user_id"
    )
    fk_obj = next(iter(fk))
    assert fk_obj.ondelete == "CASCADE", "user_profiles.user_id 必须 ondelete=CASCADE"
