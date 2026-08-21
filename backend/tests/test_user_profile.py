"""UserProfile 采集服务测试（Phase B，长期记忆规则聚合）。

覆盖（A 层确定性断言，不依赖真实 LLM）：
- extract_signals：主题命中（复用 FLOW_TOPICS）、具体实体（订单号/型号）保留、泛化商品词剔除、
  品类偏好识别、handoff 信号；
- _merge_one / merge_profile：增量合并不丢、实体去重限量、满意度计数、幂等键不翻倍、
  开关关闭不采集、fail-open 不抛异常；
- to_prompt_text：聚合摘要正确、无内容返回 None；
- reset_profile：清空后读取 None。
"""
from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.models.base import Base
from app.models.user import User, UserRole
from app.models.user_profile import UserProfile
from app.services.user_profile_service import (
    _merge_one,
    extract_signals,
    get_profile,
    merge_profile,
    reset_profile,
    to_prompt_text,
)

UID = uuid.UUID("22222222-2222-2222-2222-222222222222")


@pytest.fixture
def db(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # profile 是 JSONB + PG server_default（SQLite 无法编译）→ 建表前替换（项目测试惯例）
    import sqlalchemy as _sa

    for c in UserProfile.__table__.columns:
        if c.name == "profile":
            c.type = _sa.JSON()
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


# ---------- 一、extract_signals（纯函数） ----------

def test_extract_signals_topic_entity_category():
    """主题 + 具体实体 + 品类偏好同时识别；泛化商品词不进实体。"""
    s = extract_signals("我的订单 SO2026080118 退款怎么处理，洗衣机有问题", intent="qa")
    assert s["topics"]["退款"] == 1
    assert "SO2026080118" in s["entities"]  # 订单号保留
    assert "洗衣机" not in s["entities"]  # 泛化商品词剔除（品类里才有）
    assert "洗衣机" in s["categories"]  # 品类偏好识别
    assert s["handoff"] == 0


def test_extract_signals_handoff():
    """intent=handoff → 转人工信号 +1。"""
    s = extract_signals("我要投诉转人工", intent="handoff")
    assert s["handoff"] == 1


def test_extract_signals_empty_query():
    """空 query 返回全空信号（不崩）。"""
    s = extract_signals("", intent="qa")
    assert s["topics"] == {} and s["entities"] == [] and s["handoff"] == 0


def test_extract_signals_model_entity_kept():
    """具体型号（非泛化词）保留进实体。"""
    s = extract_signals("W5 洗衣机送装预约", intent="qa")
    assert "W5" in s["entities"]


# ---------- 二、merge 增量合并 ----------

def test_merge_one_accumulates_and_dedups(db):
    """增量合并不丢：多次 merge 主题累加、实体去重。"""
    profile = {"schema_version": 1}
    profile = _merge_one(profile, extract_signals("订单 SO2026080118 退款", "qa"))
    profile = _merge_one(profile, extract_signals("退款一般多久到账", "qa"))
    profile = _merge_one(profile, extract_signals("订单 SO2026080118 物流到哪了", "qa"))
    assert profile["topics"]["退款"] == 2
    assert profile["topics"]["配送/物流"] == 1
    assert profile["entities"].count("SO2026080118") == 1  # 去重
    assert profile["entities"][0] == "SO2026080118"


def test_merge_profile_satisfaction(db, monkeypatch):
    """满意度信号单独传入并累加。"""
    monkeypatch.setattr(settings, "USER_PROFILE_ENABLED", True)
    ok = merge_profile(db, UID, "问题 A", sat_rating="up")
    assert ok is True
    ok2 = merge_profile(db, UID, "问题 B", sat_rating="down")
    assert ok2 is True
    p = get_profile(db, UID)
    assert p["satisfaction"]["up"] == 1
    assert p["satisfaction"]["down"] == 1


def test_merge_profile_idempotent_by_idem_key(db, monkeypatch):
    """幂等键：同一 message_id 重复 merge 不重复计数。

    用随机幂等键（真实 Redis 环境可能残留历史键，唯一键保证测试自洽）。
    """
    monkeypatch.setattr(settings, "USER_PROFILE_ENABLED", True)
    key = f"msg-{uuid.uuid4()}"
    merge_profile(db, UID, "订单 SO2026080118 退款", idem_key=key)
    merge_profile(db, UID, "订单 SO2026080118 退款", idem_key=key)
    p = get_profile(db, UID)
    assert p["topics"]["退款"] == 1  # 不翻倍


def test_merge_profile_disabled_skips(db, monkeypatch):
    """开关关闭：不采集、不创建画像。"""
    monkeypatch.setattr(settings, "USER_PROFILE_ENABLED", False)
    merge_profile(db, UID, "订单 SO2026080118 退款")
    assert get_profile(db, UID) is None


def test_merge_profile_fail_open_on_missing_user(db, monkeypatch):
    """user_id 无对应用户（FK 约束）→ fail-open 返回 False，不抛。"""
    monkeypatch.setattr(settings, "USER_PROFILE_ENABLED", True)
    ghost = uuid.UUID("99999999-9999-9999-9999-999999999999")
    # SQLite 未开 FK 约束，实际能写入；改用显式异常路径验证 fail-open：传 None user_id
    ok = merge_profile(db, None, "测试")
    assert ok is False  # 异常被捕获，返回 False 不抛


# ---------- 三、to_prompt_text ----------

def test_to_prompt_text_summary():
    """画像 → 聚合摘要（含高优服务标记）。"""
    profile = {
        "topics": {"退款": 2, "物流": 1},
        "entities": ["SO2026080118", "W5"],
        "satisfaction": {"up": 3, "down": 1},
        "handoff": {"count": 2},
        "preferences": {"品类": ["洗衣机"]},
    }
    text = to_prompt_text(profile)
    assert "常问主题" in text and "退款(2)" in text
    assert "SO2026080118" in text
    assert "满意度 赞3/踩1" in text
    assert "高优服务" in text  # handoff>=2
    assert "洗衣机" in text


def test_to_prompt_text_none_for_empty():
    """无画像/空画像 → None（不注入）。"""
    assert to_prompt_text(None) is None
    assert to_prompt_text({}) is None


# ---------- 四、reset_profile ----------

def test_reset_profile(db, monkeypatch):
    """reset 清空画像（隐私）。"""
    monkeypatch.setattr(settings, "USER_PROFILE_ENABLED", True)
    merge_profile(db, UID, "订单 SO2026080118 退款")
    assert get_profile(db, UID) is not None
    assert reset_profile(db, UID) is True
    assert get_profile(db, UID) is None
    assert reset_profile(db, UID) is False  # 已清空再清 → False
