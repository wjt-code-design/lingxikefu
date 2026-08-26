"""Admin 评测中心：历史趋势 + 触发评测。

设计：
- 历史趋势：GET /admin/eval/history → EvalResult 表聚合，供前端画折线图
- 触发评测：POST /admin/eval/run → 后台异步跑评测，结果落 EvalResult 表
- 退化告警：GET /admin/eval/latest 与历史均值对比，低于阈值返回警告
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session as OrmSession

from app.api.deps import require_admin
from app.core.database import get_db
from app.models.eval_result import EvalResult, EvalStatus
from app.schemas.eval import (
    EvalHistoryResp,
    EvalResultItem,
    EvalTriggerReq,
    EvalTriggerResp,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

#: 后台评测任务引用集合（P3-⑭）：create_task 返回的任务若不持有引用，
#: 事件循环可能随时 GC 中断执行（后台评测静默失效）；挂到模块级 set，
#: 任务完成/异常后由 done_callback 移除，防 GC 中杀 + 防集合泄漏。
_eval_tasks: set[asyncio.Task] = set()


@router.get("/eval/history")
def eval_history(
    _: dict = Depends(require_admin),
    db: OrmSession = Depends(get_db),
) -> EvalHistoryResp:
    """评测历史：按 run_id 聚合返回最近 30 次运行（同一次运行的全部指标行不分离）。

    P3-⑭：docstring 如实 vs 行为——此前只取最近 200 行的"裸行"，
    与"按 run_id 聚合"的描述不符；现按 (run_id, 最近运行时间) 分组取前 30 次运行
    的全部指标行（SQL 聚合，PG/SQLite 双兼容）。
    """
    run_ids = db.execute(
        select(EvalResult.run_id)
        .group_by(EvalResult.run_id)
        .order_by(desc(func.max(EvalResult.created_at)))
        .limit(30)
    ).scalars().all()
    items: list[EvalResultItem] = []
    if run_ids:
        rows = db.scalars(
            select(EvalResult)
            .where(EvalResult.run_id.in_(run_ids))
            .order_by(desc(EvalResult.created_at))
        ).all()
        items = [EvalResultItem.model_validate(r) for r in rows]
    return EvalHistoryResp(items=items)


@router.get("/eval/latest")
def eval_latest(
    _: dict = Depends(require_admin),
    db: OrmSession = Depends(get_db),
) -> dict:
    """最近一次评测结果 + 退化告警。

    返回最新一次运行的分数，以及与历史均值的对比。
    """
    # 取最新 run_id
    latest_run = db.scalar(
        select(EvalResult.run_id)
        .order_by(desc(EvalResult.created_at))
        .limit(1)
    )
    if not latest_run:
        return {"has_history": False, "latest": None, "alerts": []}

    rows = db.scalars(
        select(EvalResult).where(EvalResult.run_id == latest_run)
    ).all()

    # 计算历史均值（最近 10 次运行）
    history = db.scalars(
        select(EvalResult)
        .where(EvalResult.metric == "faithfulness")
        .order_by(desc(EvalResult.created_at))
        .limit(10)
    ).all()

    history_avg = (
        sum(r.score for r in history) / len(history) if history else 0.0
    )

    alerts = []
    for r in rows:
        if r.metric == "faithfulness" and r.score < history_avg * 0.95:
            alerts.append(
                f"faithfulness 退化：当前 {r.score:.1%} < 历史均值 {history_avg:.1%} × 0.95"
            )

    return {
        "has_history": True,
        "latest": {
            "run_id": latest_run,
            "metrics": [
                {"metric": r.metric, "score": r.score, "passed": r.passed, "total": r.total}
                for r in rows
            ],
        },
        "alerts": alerts,
    }


@router.post("/eval/run")
async def run_eval(
    req: EvalTriggerReq,
    payload: dict = Depends(require_admin),
) -> EvalTriggerResp:
    """后台异步触发评测。

    评测逻辑复用 scripts/eval_faithfulness.py 和 scripts/eval_recall.py 的函数，
    结果落 EvalResult 表，前端 GET /eval/history 查看。
    """
    run_id = f"manual-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"

    # 后台异步执行（不阻塞 HTTP 响应）；P3-⑭：持有引用防 GC 中杀，完成即释放
    task = asyncio.create_task(_do_eval(run_id, req.limit, req.kb_name))
    task.set_name(f"eval-{run_id}")
    _eval_tasks.add(task)
    task.add_done_callback(_eval_tasks.discard)

    return EvalTriggerResp(
        run_id=run_id,
        status=EvalStatus.RUNNING,
        message="评测已启动，请稍后刷新历史记录查看结果",
    )


async def _do_eval(run_id: str, limit: int = 0, kb_name: str | None = None) -> None:
    """后台评测执行器。

    复用 scripts/ 里的评测逻辑，结果写入 EvalResult 表。
    P3-⑭：评测脚本导入失败 / 执行异常均落 FAILED 记录并日志明示，不静默。
    """
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        # faithfulness（P3-⑭：脆弱导入显式兜底——缺脚本时落 FAILED，日志明示根因）
        try:
            from scripts.eval_faithfulness import run_faithfulness_eval
        except ImportError:
            logger.exception("import scripts.eval_faithfulness 失败（模块缺失/损坏），faithfulness 不可用")
            db.add(_failed_eval_row(run_id, "faithfulness"))
            db.commit()
        else:
            await _run_stage(
                db, run_id, "faithfulness", run_faithfulness_eval(db, limit=limit, kb_name=kb_name)
            )

        # recall（同步脚本经 to_thread 执行，避免阻塞事件循环）
        try:
            from scripts.eval_recall import run_recall_eval
        except ImportError:
            logger.exception("import scripts.eval_recall 失败（模块缺失/损坏），recall 不可用")
            db.add(_failed_eval_row(run_id, "recall"))
            db.commit()
        else:
            await _run_stage(
                db, run_id, "recall", asyncio.to_thread(run_recall_eval, db, limit=limit, kb_name=kb_name)
            )

    except Exception:
        logger.exception("eval run %s failed", run_id)
    finally:
        db.close()


def _failed_eval_row(run_id: str, metric: str) -> EvalResult:
    """评测阶段失败占位记录（score=0, status=FAILED）。"""
    return EvalResult(
        run_id=run_id,
        metric=metric,
        score=0.0,
        total=0,
        passed=0,
        status=EvalStatus.FAILED,
        source="manual",
    )


async def _run_stage(db: OrmSession, run_id: str, metric: str, awaitable) -> None:
    """执行单阶段评测并落表；异常 → 该阶段 FAILED 记录（P3-⑭ 显式兜底，不静默）。"""
    try:
        result = await awaitable
        for m, score, total, passed in result:
            db.add(
                EvalResult(
                    run_id=run_id,
                    metric=m,
                    score=score,
                    total=total,
                    passed=passed,
                    status=EvalStatus.DONE,
                    source="manual",
                )
            )
        db.commit()
    except Exception:
        logger.exception("%s eval stage failed (run=%s)", metric, run_id)
        db.add(_failed_eval_row(run_id, metric))
        db.commit()
