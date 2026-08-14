"""Celery worker（BU-04 填充文档导入任务）。

MVP 阶段仅声明 app 与 broker/backend（Redis），任务在后续单元注册。
"""
from __future__ import annotations

from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "lingxi",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)

celery_app.conf.update(
    task_track_started=True,
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)

# BU-04 起在此 `import` 任务模块注册（如 app.workers.import_worker）。
celery_app.autodiscover_tasks(["app.workers"])
