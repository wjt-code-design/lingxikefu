"""数据库引擎与会话管理（SQLAlchemy 2.0）。

- `engine`：全局异步化前的同步引擎（FastAPI 依赖注入 + Celery worker 共用）。
- `SessionLocal`：sessionmaker 工厂。
- `get_db()`：FastAPI 依赖，用于请求级事务。
引擎为惰性创建，import 本模块不触发真实连接（单测可安全 import）。
"""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

SessionLocal = sessionmaker(
    bind=engine,
    class_=Session,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：每个请求一个 Session，请求结束统一关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
