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
    # H2（架构遗留债清偿）：连接阶段超时上界。psycopg3 走 libpq conninfo 参数；
    # 若无此界，DB 停摆时 connect 阻塞无上界，quota 等锁内 DB 读会把共享锁
    # 串行堵死全部 chat 热路径。照 main.py 健康检查引擎先例（connect_timeout=2）。
    connect_args={"connect_timeout": 5},
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
