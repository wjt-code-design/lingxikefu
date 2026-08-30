"""KB 发布流编排服务（门禁 v2 G2）：batch 状态机 + 抽样快检 + 发布/回滚翻转。

编排链路：
1. 上传带 batch_id → 文档 staged 导入（visible=False + batch_tag=batch_id）+ 批次登记
   （本模块 :func:`upsert_batch_membership`，首个上传隐式建行，状态 pending）；
2. publish → 校验批次文档全部 indexed → status=evaluating → fire-and-forget 快检
   （:func:`_quick_check_job`，asyncio.create_task 挂模块级引用防 GC，eval.py P3-⑭
   同款；独立 SessionLocal 会话，_do_eval 先例）→ 立即 202；
3. 快检完成侧：faithfulness sample=20（kb_id 精确绑定）→ EvalResult 落表
   （source=publish，绑定评测时刻 kb_version，FAILED 也留痕）→ 按 _gate_passed
   同阈值判定：PASS → Qdrant set_payload 一次 filter 翻转 visible=true + released；
   FAIL/异常 → 保持 staged 不可见 + failed + 坐席（admin）通知；
4. rollback（仅 released）：set_payload 翻转 visible=false（batch_tag 恢复 staged 标记）
   → rolled_back；翻转失败 500 可重试（状态不变）。

快检耗时 ~20min（sample=20 真实 LLM 调用），故 publish 必须 fire-and-forget；
评测导入链复用 scripts.eval_faithfulness.run_faithfulness_eval（本任务为其加可选
sample/kb_id 参数，CLI 本体零改动——设计冲突已标注偏差，详见任务报告）。
"""
from __future__ import annotations

import asyncio
import logging
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.models.eval_result import EvalResult, EvalStatus
from app.models.kb_publish import KBBatchStatus, KBPublishBatch
from app.models.knowledge import Document, DocumentStatus, KnowledgeBase
from app.services import kb_lookup, vector_service
from app.services.notification_service import create_notification
from app.services.vector_service import VectorStoreError

logger = logging.getLogger(__name__)

#: 快检抽样题数（brief 定值：等效 CLI --sample 20，控制耗时）
QUICK_CHECK_SAMPLE = 20
#: 快检 run_id 前缀（EvalResult.source=publish 与 run_id 双重可溯源）
_RUN_PREFIX = "publish"


def get_batch(db: Session, batch_id: str) -> KBPublishBatch | None:
    """按 batch_id 取批次（tenant_id 过滤，红线⑨）。"""
    from app.core.config import settings

    return (
        db.query(KBPublishBatch)
        .filter_by(batch_id=batch_id, tenant_id=settings.TENANT_DEFAULT)
        .first()
    )


def upsert_batch_membership(db: Session, kb_id: UUID, batch_id: str, doc_id: UUID) -> KBPublishBatch:
    """把文档记入批次；批次不存在则隐式创建（status=pending，doc_ids=[doc_id]）。

    并发首传防重复建行：batch_id 唯一索引兜底（撞 IntegrityError → 回滚改追加）。
    """
    from sqlalchemy.exc import IntegrityError

    batch = get_batch(db, batch_id)
    if batch is None:
        batch = KBPublishBatch(
            kb_id=kb_id,
            batch_id=batch_id,
            status=KBBatchStatus.pending,
            doc_ids=[str(doc_id)],
        )
        db.add(batch)
        try:
            db.commit()
        except IntegrityError:
            # 并发同 batch_id 首传：唯一索引拦下重复行 → 回滚后按"已存在"追加
            db.rollback()
            batch = get_batch(db, batch_id)
            if batch is None:  # pragma: no cover - 索引存在则必能取回
                raise
        else:
            db.refresh(batch)
            return batch
    if str(batch.kb_id) != str(kb_id):
        raise ValueError(f"batch_id {batch_id} 已绑定其他知识库")
    if batch.status in (KBBatchStatus.evaluating, KBBatchStatus.released):
        # 门禁完整性：评测中/已发布批次不接受追加——否则新文档绕过本次快检
        # （batch_tag 翻转会把整批 staged 一并发布）。新增请先回滚/等评测完成。
        raise ValueError(
            f"批次 {batch_id} 当前状态为 {batch.status.value}，不接受新增文档"
            + ("；请先回滚再重新上传" if batch.status == KBBatchStatus.released else "；请等评测结束后再上传")
        )
    doc_str = str(doc_id)
    if doc_str not in [str(d) for d in batch.doc_ids]:
        batch.doc_ids = [*batch.doc_ids, doc_str]
        db.commit()
    return batch


def validate_batch_ready(db: Session, batch: KBPublishBatch) -> tuple[bool, str]:
    """发布前校验：批次非空、全部文档 indexed。返回 (ok, 错误信息)。

    - 已删除文档的悬空引用：自动从 doc_ids 清理（其向量已随删除清空，无从翻转）；
    - failed → 400 语义（提示先修复）；parsing/embedding → 400（须全部就绪）。
    """
    doc_ids = [str(d) for d in batch.doc_ids]
    parsed: list[UUID] = []
    for raw in doc_ids:
        try:
            parsed.append(UUID(raw))
        except (ValueError, AttributeError):
            logger.warning("批次 %s 含非法 doc_id %r（发布时忽略）", batch.batch_id, raw)
    docs = (
        db.query(Document)
        .filter(Document.id.in_(parsed), Document.tenant_id == _tenant())
        .all()
    )
    by_id = {str(d.id): d for d in docs}
    live = [p for p in parsed if str(p) in by_id]
    dangling = len(parsed) - len(live)
    if dangling:
        logger.warning(
            "批次 %s 有 %d 个已删除文档引用，发布时自动清理", batch.batch_id, dangling
        )
        batch.doc_ids = [str(p) for p in live]
    if not live:
        return False, "批次无有效文档（文档均已删除或清单为空）"
    failed = [by_id[str(p)].name for p in live if by_id[str(p)].status == DocumentStatus.failed]
    if failed:
        return (
            False,
            f"批次存在导入失败文档：{'、'.join(failed)}；请先删除后重新上传，再发布",
        )
    not_ready = [
        by_id[str(p)].name
        for p in live
        if by_id[str(p)].status != DocumentStatus.indexed
    ]
    if not_ready:
        return False, f"以下文档尚未完成导入（须全部 indexed）：{'、'.join(not_ready)}"
    return True, ""


def _tenant() -> str:
    from app.core.config import settings

    return settings.TENANT_DEFAULT


# ---------------------------------------------------------------------------
# fire-and-forget 快检（asyncio 任务 + 模块级引用，eval.py P3-⑭ 先例）
# ---------------------------------------------------------------------------

_publish_tasks: set[asyncio.Task] = set()


def spawn_quick_check(batch_id: str) -> None:
    """发布快检 fire-and-forget 启动（调用方已返回 202）。

    用 asyncio.create_task 而非裸线程：评测链是 async（run_faithfulness_eval 为
    协程、AsyncClient 绑定事件循环），跨 loop 重跑有单例漂移风险——与 eval.py
    _do_eval 同款；引用挂模块级集合防 GC 中杀，完成/异常后由 done_callback 移除。
    """
    task = asyncio.create_task(_quick_check_job(batch_id))
    task.set_name(f"publish-{batch_id}")
    _publish_tasks.add(task)
    task.add_done_callback(_publish_tasks.discard)


async def _quick_check_job(batch_id: str) -> None:
    """快检后台作业：独立会话（不依赖请求会话生命周期）→ 评测 → 落表 → 判定 → 翻转。"""
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        batch = get_batch(db, batch_id)
        # 幂等护栏：非 evaluating（被并发翻转/删除）不处理
        if batch is None or batch.status != KBBatchStatus.evaluating:
            logger.warning("快检跳过：批次 %s 不处于 evaluating（status=%s）", batch_id, batch and batch.status)
            return
        kb = db.get(KnowledgeBase, batch.kb_id)
        if kb is None:
            _finish_failed(db, batch, "KB 不存在（已被删除）", None)
            return
        run_id = f"{_RUN_PREFIX}-{uuid4().hex[:16]}"
        rows = await _run_quick_check_stage(db, run_id, kb, batch_id)
        anchor = rows[0] if rows else None
        passed = _gate_passed(rows) if rows else False
        if not passed:
            _finish_failed(db, batch, _fail_summary(rows), anchor)
            return
        # PASS → 翻转（失败则批次标 failed 可重试；评测行保留供观测）
        try:
            vector_service.set_visible_by_doc_ids(
                [UUID(str(d)) for d in batch.doc_ids], True, batch_tag=batch.batch_id
            )
        except VectorStoreError as e:
            logger.exception("批次 %s 发布翻转失败（评测已 PASS）", batch_id)
            _finish_failed(db, batch, f"评测通过但向量翻转失败：{e}", anchor)
            return
        batch.status = KBBatchStatus.released
        batch.eval_result_id = anchor.id if anchor is not None else None
        db.commit()
        _notify(db, "kb.batch.released", f"批次 {batch_id} 发布成功",
                f"快检通过（{_summarize_metrics(rows)}），文档已对检索可见。", batch_id)
        logger.info("批次 %s 发布完成（run=%s）", batch_id, run_id)
    except Exception:  # noqa: BLE001 - 后台作业兜底：不静默，批次标 failed 留痕
        logger.exception("批次 %s 快检作业异常", batch_id)
        try:
            batch = get_batch(db, batch_id)
            if batch is not None and batch.status == KBBatchStatus.evaluating:
                _finish_failed(db, batch, "快检作业异常（见服务端日志）", None)
        except Exception:  # noqa: BLE001
            logger.exception("批次 %s 异常兜底失败", batch_id)
    finally:
        db.close()


async def _run_quick_check_stage(
    db: Session, run_id: str, kb: KnowledgeBase, batch_id: str
) -> list[EvalResult]:
    """跑 faithfulness 快检（sample=20 + kb_id 绑定）并落 EvalResult，返回本次运行行。

    形态对齐 eval.py._run_stage：异常 → FAILED 占位行（同样绑定 kb_version）；
    kb_version 取"评测时刻"值（发布前文档全部 indexed，版本稳定），fail-open → None。
    """
    try:
        from scripts.eval_faithfulness import run_faithfulness_eval

        result = await run_faithfulness_eval(
            db, kb_name=kb.name, sample=QUICK_CHECK_SAMPLE, kb_id=kb.id
        )
    except Exception as e:  # noqa: BLE001 - 单阶段失败显式兜底（P3-⑭ 先例）
        logger.exception("批次 %s 快检执行失败（FAILED 留痕）", batch_id)
        return _persist_eval_rows(db, run_id, [], _eval_version(db, kb), failed_reason=str(e))

    rows = _persist_eval_rows(db, run_id, result, _eval_version(db, kb))
    logger.info("批次 %s 快检落表 %d 行（run=%s）", batch_id, len(rows), run_id)
    return rows


def _eval_version(db: Session, kb: KnowledgeBase) -> str | None:
    """评测时刻 KB 版本指纹（fail-open：解析失败 → None，不阻塞判定）。"""
    try:
        return kb_lookup.kb_version_str(db, kb.id)
    except Exception:  # noqa: BLE001
        logger.exception("快检 kb_version 解析失败（行不绑定版本）")
        return None


def _persist_eval_rows(
    db: Session,
    run_id: str,
    result: list[tuple[str, float, int, int]],
    kb_version: str | None,
    failed_reason: str | None = None,
) -> list[EvalResult]:
    """把评测结果落 EvalResult（source=publish）；空结果/异常 → FAILED 占位行。

    多指标行共享 run_id（列表端点按锚点行 run_id 聚合全部指标）；返回全部行，
    首行供批次 eval_result_id 锚点（与 _run_stage 落表形态一致）。
    """
    if failed_reason or not result:
        row = EvalResult(
            run_id=run_id,
            metric="faithfulness",
            score=0.0,
            total=0,
            passed=0,
            status=EvalStatus.FAILED,
            source="publish",
            kb_version=kb_version,
        )
        db.add(row)
        db.commit()
        return [row]
    rows: list[EvalResult] = []
    for metric, score, total, passed in result:
        row = EvalResult(
            run_id=run_id,
            metric=metric,
            score=score,
            total=total,
            passed=passed,
            status=EvalStatus.DONE,
            source="publish",
            kb_version=kb_version,
        )
        db.add(row)
        rows.append(row)
    db.commit()
    return rows


def _gate_passed(rows: list[EvalResult]) -> bool:
    """快检门禁判定：复用 eval.py._gate_passed 同阈值同实现（qa≥85%/refuse≥90%/citation≥95%）。

    阈值单一真源在 eval.py（与发布门禁 v1 观测面同式）；服务层引用 API 模块属既有
    异味（kb_lookup 下沉前 sessions→chat 同型），多消费方时一并下沉。
    空 rows / 无 qa 样本 → False（宁可拦下可疑发布，不做部分发布）。
    """
    from app.api.eval import _gate_passed as _eval_gate_passed

    return _eval_gate_passed(rows)


def _fail_summary(rows: list[EvalResult]) -> str:
    if not rows:
        return "快检无结果（无 KB / 无样本）"
    parts = [f"{r.metric} {r.passed}/{r.total}" for r in rows if r.status == EvalStatus.DONE]
    return f"快检未达标（{'；'.join(parts) or '执行失败'}），批次保持未发布"


def _summarize_metrics(rows: list[EvalResult]) -> str:
    parts = [f"{r.metric} {r.passed}/{r.total}" for r in rows if r.status == EvalStatus.DONE]
    return "；".join(parts) or "无样本"


def _finish_failed(db: Session, batch: KBPublishBatch, reason: str, anchor: EvalResult | None) -> None:
    """快检 FAIL/异常收尾：批次标 failed + 锚点回填 + admin 通知（全 fail-open）。"""
    batch.status = KBBatchStatus.failed
    if anchor is not None:
        batch.eval_result_id = anchor.id
    db.commit()
    _notify(db, "kb.batch.failed", f"批次 {batch.batch_id} 发布被门禁拦截", f"{reason}。修复后可重新发布。", batch.batch_id)


def _notify(db: Session, event_type: str, title: str, content: str, batch_id: str) -> None:
    """坐席（admin）通知（fire-and-forget 结果可达性依赖它，fail-open 同 notification_service）。"""
    create_notification(
        db,
        recipient_role="admin",
        event_type=event_type,
        title=title,
        content=content,
        resource_type="kb_publish_batch",
        resource_id=batch_id,
    )


# ---------------------------------------------------------------------------
# rollback
# ---------------------------------------------------------------------------


def rollback_batch(db: Session, batch: KBPublishBatch) -> None:
    """回滚已发布批次：Qdrant 翻转 visible=false（batch_tag 恢复 staged 标记）→ rolled_back。

    翻转失败抛 VectorStoreError（端点转 500）：状态不变、可重试，不留半翻转镜像。
    """
    vector_service.set_visible_by_doc_ids(
        [UUID(str(d)) for d in batch.doc_ids], False, batch_tag=batch.batch_id
    )
    batch.status = KBBatchStatus.rolled_back
    db.commit()


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def eval_summary_for(db: Session, batch: KBPublishBatch) -> dict | None:
    """批次评测摘要：按锚点行 run_id 聚合全部指标行 + _gate_passed 判定。"""
    if batch.eval_result_id is None:
        return None
    anchor = db.get(EvalResult, batch.eval_result_id)
    if anchor is None:
        return None
    rows = (
        db.query(EvalResult)
        .filter_by(run_id=anchor.run_id, source="publish")
        .order_by(EvalResult.created_at)
        .all()
    )
    return {
        "run_id": anchor.run_id,
        "kb_version": anchor.kb_version,
        "passed": _gate_passed(rows),
        "metrics": [
            {
                "metric": r.metric,
                "score": r.score,
                "total": r.total,
                "passed": r.passed,
                "status": r.status,
            }
            for r in rows
        ],
    }


def list_batches(db: Session) -> list[dict]:
    """全部批次（created_at 倒序）+ 文档数 + 评测摘要 + KB 名。"""
    batches = (
        db.query(KBPublishBatch)
        .filter_by(tenant_id=_tenant())
        .order_by(KBPublishBatch.created_at.desc())
        .all()
    )
    items: list[dict] = []
    for b in batches:
        kb = db.get(KnowledgeBase, b.kb_id)
        items.append(
            {
                "batch_id": b.batch_id,
                "kb_id": str(b.kb_id),
                "kb_name": kb.name if kb else None,
                "status": b.status.value if isinstance(b.status, KBBatchStatus) else str(b.status),
                "doc_ids": [str(d) for d in b.doc_ids],
                "doc_count": len(b.doc_ids),
                "eval_result_id": str(b.eval_result_id) if b.eval_result_id else None,
                "eval": eval_summary_for(db, b),
                "created_at": b.created_at,
                "updated_at": b.updated_at,
            }
        )
    return items
