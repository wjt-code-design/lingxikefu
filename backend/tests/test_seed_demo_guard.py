"""seed_demo_data 目标库收权（2026-08-28 评测库污染事故防线）。"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.models.base import Base  # noqa: F401 确保模型注册
from app.models.knowledge import KnowledgeBase


def _session():
    engine = create_engine("sqlite://")
    # 只建 knowledge_bases：导入 scripts.seed_demo_data 会连带注册全量模型，
    # 其含 JSONB 列（user_profiles.profile）SQLite 无法编译（项目测试惯例见 test_user_profile_model.py）
    Base.metadata.create_all(engine, tables=[KnowledgeBase.__table__])
    return sessionmaker(bind=engine)()


def _kb(name: str) -> KnowledgeBase:
    return KnowledgeBase(name=name, description=name, tenant_id=settings.TENANT_DEFAULT)


def test_latest_kb_creates_demo_on_empty():
    from scripts.seed_demo_data import latest_kb
    db = _session()
    kb_id = latest_kb(db)
    kb = db.get(KnowledgeBase, kb_id)
    db.close()
    assert kb is not None and kb.name == "demo"


def test_latest_kb_prefers_demo_over_business_kb():
    from scripts.seed_demo_data import latest_kb
    db = _session()
    db.add(_kb("星河智家·官方政策库"))
    db.add(_kb("demo"))
    db.commit()
    kb_id = latest_kb(db)
    kb = db.get(KnowledgeBase, kb_id)
    db.close()
    assert kb.name == "demo"


def test_latest_kb_refuses_when_only_business_kbs():
    from scripts.seed_demo_data import latest_kb
    db = _session()
    db.add(_kb("星河智家·官方政策库"))
    db.commit()
    with pytest.raises(SystemExit, match="拒绝自动选库"):
        latest_kb(db)
    db.close()
