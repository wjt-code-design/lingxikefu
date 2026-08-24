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
from sqlalchemy import desc, select
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


@router.get("/eval/history")
def eval_history(
    _: dict = Depends(require_admin),
    db: OrmSession = Depends(get_db),
) -> EvalHistoryResp:
    """评测历史（最近 30 次运行，按 run_id 聚合）。"""
    rows = db.scalars(
        select(EvalResult)
        .order_by(desc(EvalResult.created_at))
        .limit(200)
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

    # 后台异步执行（不阻塞 HTTP 响应）
    asyncio.create_task(_do_eval(run_id, req.limit, req.kb_name))

    return EvalTriggerResp(
        run_id=run_id,
        status=EvalStatus.RUNNING,
        message="评测已启动，请稍后刷新历史记录查看结果",
    )


async def _do_eval(run_id: str, limit: int = 0, kb_name: str | None = None) -> None:
    """后台评测执行器。

    复用 scripts/ 里的评测逻辑，结果写入 EvalResult 表。
    """
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        # 调用评测脚本
        from scripts.eval_faithfulness import run_faithfulness_eval

        # faithfulness
        try:
            result = await run_faithfulness_eval(db, limit=limit, kb_name=kb_name)
            for metric, score, total, passed in result:
                db.add(
                    EvalResult(
                        run_id=run_id,
                        metric=metric,
                        score=score,
                        total=total,
                        passed=passed,
                        status=EvalStatus.DONE,
                        source="manual",
                    )
                )
            db.commit()
        except Exception:
            logger.exception("faithfulness eval failed")
            db.add(
                EvalResult(
                    run_id=run_id,
                    metric="faithfulness",
                    score=0.0,
                    total=0,
                    passed=0,
                    status=EvalStatus.FAILED,
                    source="manual",
                )
            )
            db.commit()

    except Exception:
        logger.exception("eval run %s failed", run_id)
    finally:
        db.close()
