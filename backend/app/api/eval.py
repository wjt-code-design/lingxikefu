"""Admin 评测中心：历史趋势 + 触发评测 + 发布门禁观测。

设计：
- 历史趋势：GET /admin/eval/history → EvalResult 表聚合，供前端画折线图
- 触发评测：POST /admin/eval/run → 后台异步跑评测，结果落 EvalResult 表
- 退化告警：GET /admin/eval/latest 与历史均值对比，低于阈值返回警告
- 发布门禁 v1（三期 3）：GET /admin/eval/gate → 当前 kb_version + 该版本最近评测 +
  是否通过（观测非阻断；强制阻断导入与自动回滚留 v2）
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
    EvalGateResp,
    EvalHistoryResp,
    EvalResultItem,
    EvalTriggerReq,
    EvalTriggerResp,
)
from app.services import kb_lookup

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


@router.get("/eval/gate")
def eval_gate(
    _: dict = Depends(require_admin),
    db: OrmSession = Depends(get_db),
) -> EvalGateResp:
    """KB 发布门禁 v1（架构三期 3）：当前 KB 版本的评测通过状态一屏可见。

    三态语义：
    - passed=True/False：当前 kb_version 已有绑定评测，按 _pass_all 同阈值判（_gate_passed）；
    - passed=None：当前版本从未评测（含"有历史评测但绑定旧版本"），不误报。

    观测非阻断：不拦截导入、不自动回滚（v2 另批）。无 KB → 全 None（空态不炸）。
    """
    kb_id = kb_lookup.get_latest_kb_id(db)
    current = kb_lookup.kb_version_str(db, kb_id) if kb_id is not None else None
    if current is None:
        return EvalGateResp(kb_version=None, last_eval=None, passed=None)

    # 当前版本绑定的最近一次运行（同 eval_history 的 run_id 聚合口径）
    latest_run = db.scalar(
        select(EvalResult.run_id)
        .where(EvalResult.kb_version == current)
        .group_by(EvalResult.run_id)
        .order_by(desc(func.max(EvalResult.created_at)))
        .limit(1)
    )
    if not latest_run:
        return EvalGateResp(kb_version=current, last_eval=None, passed=None)

    rows = db.scalars(
        select(EvalResult)
        .where(EvalResult.run_id == latest_run, EvalResult.kb_version == current)
        .order_by(EvalResult.created_at)
    ).all()
    return EvalGateResp(
        kb_version=current,
        last_eval={
            "run_id": latest_run,
            "created_at": max(r.created_at for r in rows).isoformat(),
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
        },
        passed=_gate_passed(rows),
    )


def _gate_passed(rows: list[EvalResult]) -> bool:
    """发布门禁判定 v1：与 scripts.eval_faithfulness._pass_all 同阈值，按落表 stats 重算。

    EvalResult 只存每指标 score/total（无 run 级 pass_all 布尔），脚本冻结不可改签名，
    故在观测侧按字段重算：qa≥85%（无 qa 样本 → 不通过）；refuse≥90%、citation≥95%
    （有该指标行才判——citation 采样运行带引用样本时同样判 95%，比脚本 full_run-only
    略严：观测面宁可偏严不偏松）。FAILED 行（score=0/total=0）天然不通过。
    """
    done = {r.metric: r for r in rows if r.status == EvalStatus.DONE}
    qa = done.get("qa")
    if qa is None or qa.total == 0 or qa.score < 0.85:
        return False
    refuse = done.get("refuse")
    if refuse is not None and refuse.total and refuse.score < 0.9:
        return False
    cit = done.get("citation")
    if cit is not None and cit.total and cit.score < 0.95:
        return False
    return True


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
    三期 3：每阶段完成落表时把当时 kb_version 绑定到该阶段全部行（含 FAILED 留痕行），
    gate 端点据此回答"当前版本是否评测通过"；版本解析失败 fail-open（行不绑定，不阻塞评测）。
    """
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        # faithfulness（P3-⑭：脆弱导入显式兜底——缺脚本时落 FAILED，日志明示根因）
        try:
            from scripts.eval_faithfulness import run_faithfulness_eval
        except ImportError:
            logger.exception("import scripts.eval_faithfulness 失败（模块缺失/损坏），faithfulness 不可用")
            # 脚本缺失 → _resolve_kb 同源不可用，FAILED 行不绑定版本（None，gate 不误报）
            db.add(_failed_eval_row(run_id, "faithfulness", None))
            db.commit()
        else:
            await _run_stage(
                db, run_id, "faithfulness", kb_name,
                run_faithfulness_eval(db, limit=limit, kb_name=kb_name),
            )

        # recall（同步脚本经 to_thread 执行，避免阻塞事件循环）
        try:
            from scripts.eval_recall import run_recall_eval
        except ImportError:
            logger.exception("import scripts.eval_recall 失败（模块缺失/损坏），recall 不可用")
            db.add(_failed_eval_row(run_id, "recall", None))
            db.commit()
        else:
            await _run_stage(
                db, run_id, "recall", kb_name,
                asyncio.to_thread(run_recall_eval, db, limit=limit, kb_name=kb_name),
            )

    except Exception:
        logger.exception("eval run %s failed", run_id)
    finally:
        db.close()


def _eval_kb_version(db: OrmSession, kb_name: str | None) -> str | None:
    """评测所评 KB 的版本指纹（三期 3 发布门禁 v1 绑定用）。

    KB 定位与评测脚本同规则（_resolve_kb：按名 → 同名最新 → 租户最新回退）——直接复用
    脚本实现避免解析口径漂移（只读导入，判定脚本本体零改动约束不受影响）；版本公式用
    kb_lookup.kb_version_str 单一真源（与 chat 缓存失效锚点同式）。
    fail-open：定位失败 / 无 KB / 任何异常 → None（行不绑定版本，不阻塞评测）。
    """
    try:
        from scripts.eval_faithfulness import _resolve_kb

        kb = _resolve_kb(db, kb_name)
        if kb is None:
            return None
        return kb_lookup.kb_version_str(db, kb.id)
    except Exception:  # noqa: BLE001 - fail-open：版本解析失败不阻塞评测
        logger.exception("评测 kb_version 解析失败（该阶段行不绑定版本，不阻塞评测）")
        return None


def _failed_eval_row(run_id: str, metric: str, kb_version: str | None = None) -> EvalResult:
    """评测阶段失败占位记录（score=0, status=FAILED）。

    失败也留痕（现状行为保持）并绑定版本（三期 3）——kb_version 由调用方在 db 可用处
    解析后传入（导入失败分支传 None：脚本缺失时版本解析同源不可用）。
    """
    return EvalResult(
        run_id=run_id,
        metric=metric,
        score=0.0,
        total=0,
        passed=0,
        status=EvalStatus.FAILED,
        source="manual",
        kb_version=kb_version,
    )


async def _run_stage(
    db: OrmSession, run_id: str, metric: str, kb_name: str | None, awaitable
) -> None:
    """执行单阶段评测并落表；异常 → 该阶段 FAILED 记录（P3-⑭ 显式兜底，不静默）。

    三期 3：阶段完成时解析 kb_version 绑定到本阶段全部行（含 FAILED 留痕行——现状
    行为保持：失败也落表）；版本解析失败 fail-open → 行不绑定（None）。
    """
    try:
        result = await awaitable
        kb_version = _eval_kb_version(db, kb_name)
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
                    kb_version=kb_version,
                )
            )
        db.commit()
    except Exception:
        logger.exception("%s eval stage failed (run=%s)", metric, run_id)
        db.add(_failed_eval_row(run_id, metric, _eval_kb_version(db, kb_name)))
        db.commit()
