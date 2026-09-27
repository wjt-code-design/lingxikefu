"""匿名 FAQ 可见性判定（门禁 v2 的 PG 侧真源）：从 KBPublishBatch 推导，不加迁移。

背景（审计 2026-09-27 高危越权）：staged/未发布只存在于 Qdrant point payload
（vector_service 写 visible / retrieval 过滤 visible），PG ``Document`` 没有可见性列
——staged 导入同样走 ``mark_indexed``（status=indexed）。于是匿名 FAQ 端点只看
``status == indexed`` 会把「已索引但从未发布（甚至快检未过）」的草案原文对全网放行。
批次状态是 PG 里唯一真源（KBPublishBatch.status + doc_ids），本模块据此推导
"匿名可见"，与检索侧 payload 过滤同语义。

规则（Orchestrator 裁定，勿自行放宽）：
1. doc 不属于任何批次 → 可见（直通导入路径，batch_tag 恒 None，无批次行）；
2. doc 属于批次 → 仅当它**所属的最新批次**（created_at 最大；同刻按 batch_id 字典序
   兜底，保证确定性）status == released 才可见。staged/evaluating/pending/failed/
   rolled_back 均不可见。

为何"最新批次"而非"任一批次 released"：Qdrant payload 是后翻转覆盖（发布翻 true、
回滚/重导入翻 false），批次 created_at 顺序即这条时间线——"任一批次"会把
「已发布内容随后被回滚、或被 staged 更新覆盖中」的文档错误放出来。回滚后重新
发布进新批次（新批次 released）则应重新可见，"最新"规则天然成立。

实现约束（红线⑨ / 防 N+1）：一次 ``select(KBPublishBatch...)`` 按 tenant 取回全部
批次，doc→最新批次映射在 Python 侧组装；禁止逐 doc 查批次。doc_ids 为 JSON 列，
条目是 **uuid 字符串**（upsert_batch_membership 存 str(doc_id)）而 Document.id 是
UUID——比对前统一经 UUID() 解析规范化（大小写/格式不敏感），非法条目忽略并告警。
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.kb_publish import KBBatchStatus, KBPublishBatch

logger = logging.getLogger(__name__)


def _norm_doc_id(entry: object) -> str | None:
    """把 doc_ids 条目规范化为 str(UUID)（小写无括号）。

    列里存的应是 str(uuid)，但 JSON 无类型约束：大写带连字符/花括号变体经 UUID()
    解析后统一；已解析为 UUID 的对象直接取 str；非法/None 返回 None（不参与判定）。
    """
    if entry is None:
        return None
    try:
        return str(entry if isinstance(entry, UUID) else UUID(str(entry)))
    except (ValueError, TypeError, AttributeError):
        logger.warning("批次 doc_ids 含非法条目 %r（可见性判定忽略）", entry)
        return None


def latest_batch_status_map(db: Session, doc_ids: Iterable[UUID]) -> dict[UUID, KBBatchStatus]:
    """doc → 其所属最新批次的 status（一次查询组装；不在任何批次的 doc 不出现在结果里）。

    返回 {} 时调用方按「无批次 = 直通导入 = 可见」处理，勿把缺失误读为不可见。
    """
    wanted: dict[str, UUID] = {str(d): d for d in doc_ids}
    if not wanted:
        return {}
    tenant = settings.TENANT_DEFAULT
    latest: dict[str, tuple[datetime, str, KBBatchStatus]] = {}
    rows = db.execute(
        select(
            KBPublishBatch.created_at,
            KBPublishBatch.batch_id,
            KBPublishBatch.status,
            KBPublishBatch.doc_ids,
        )
        .where(KBPublishBatch.tenant_id == tenant)
        .order_by(KBPublishBatch.created_at, KBPublishBatch.batch_id)
    ).all()
    # 升序遍历，后见批次覆盖先见 → 留下的就是"最新批次"（created_at 最大，
    # 同刻按 batch_id 字典序，确定性兜底）。
    for created_at, batch_id, status, batch_doc_ids in rows:
        for entry in batch_doc_ids or []:
            key = _norm_doc_id(entry)
            if key is None or key not in wanted:
                continue
            latest[key] = (created_at, batch_id, status)
    return {wanted[key]: state[2] for key, state in latest.items()}


def anonymous_visible_doc_ids(db: Session, doc_ids: Iterable[UUID]) -> set[UUID]:
    """doc_ids 中对匿名（无鉴权）读者可见的子集——规则见模块 docstring。"""
    ids = list(dict.fromkeys(doc_ids))  # 去重保序，保持 UUID 对象身份可哈希
    if not ids:
        return set()
    latest = latest_batch_status_map(db, ids)
    return {d for d in ids if d not in latest or latest[d] == KBBatchStatus.released}
