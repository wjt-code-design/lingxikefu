"""KB 发布流编排路由（门禁 v2 G2，admin 面）。

- POST /admin/kb/batches/{batch_id}/publish：发布触发（fire-and-forget 抽样快检，
  立即 202 返回 evaluating；快检 PASS 自动翻转发布 / FAIL 保持 staged 并通知）；
- POST /admin/kb/batches/{batch_id}/rollback：回滚已发布批次（visible 翻转回 staged）；
- GET  /admin/kb/batches：批次列表（status/doc 数/评测摘要）。

全部端点 require_admin；批次 CRUD 不暴露（上传隐式建批，见 knowledge.py）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.database import get_db
from app.schemas.kb_publish import BatchActionResp, BatchListResp
from app.services import kb_publish_service as svc
from app.services.audit_service import audit_log
from app.services.vector_service import VectorStoreError

router = APIRouter(prefix="/admin/kb", tags=["admin"])


@router.post(
    "/batches/{batch_id}/publish",
    response_model=BatchActionResp,
    status_code=status.HTTP_202_ACCEPTED,
)
async def publish_batch(
    batch_id: str,
    payload: dict = Depends(require_admin),
    db: Session = Depends(get_db),
) -> BatchActionResp:
    """发布批次：校验就绪 → evaluating → 后台快检（sample=20）→ 自动发布/拦截。

    快检真实耗时 ~20min（LLM 逐题），故立即 202 返回；完成侧经通知中心 + 批次列表
    可查。并发/重复语义：evaluating / released 再 publish → 409。
    """
    # m5（bughunt-concurrency）：同步 DB 操作搬出事件循环（H2 纪律同 chat.py；
    # PG 慢 5s 时 async 端点内直呼会冻结事件循环，chat SSE 全线停顿）
    batch = await run_in_threadpool(svc.get_batch, db, batch_id)
    if batch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "batch not found")
    if batch.status in (svc.KBBatchStatus.evaluating, svc.KBBatchStatus.released):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"批次当前状态为 {batch.status.value}，不可重复发布"
            + ("（如需重发请先回滚）" if batch.status == svc.KBBatchStatus.released else ""),
        )
    ok, err = await run_in_threadpool(svc.validate_batch_ready, db, batch)
    if not ok:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, err)
    batch.status = svc.KBBatchStatus.evaluating
    batch.eval_result_id = None  # 重发布清旧锚点（新快检完成后回填）
    await run_in_threadpool(db.commit)
    audit_log(
        db,
        actor_id=payload["sub"],
        actor_role=payload.get("role"),
        action="kb.batch.publish",
        resource="kb_publish_batch",
        resource_id=batch_id,
        detail=f"docs={len(batch.doc_ids)}",
    )
    svc.spawn_quick_check(batch_id)
    return BatchActionResp(
        batch_id=batch_id,
        status="evaluating",
        message="抽样快检已启动（sample=20），PASS 自动发布 / FAIL 拦截并通知",
    )


@router.post("/batches/{batch_id}/rollback", response_model=BatchActionResp)
def rollback_batch(
    batch_id: str,
    payload: dict = Depends(require_admin),
    db: Session = Depends(get_db),
) -> BatchActionResp:
    """回滚已发布批次：visible 翻转回 staged（检索即刻不可见）→ rolled_back。"""
    batch = svc.get_batch(db, batch_id)
    if batch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "batch not found")
    if batch.status != svc.KBBatchStatus.released:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"仅 released 批次可回滚（当前 {batch.status.value}）",
        )
    try:
        svc.rollback_batch(db, batch)
    except VectorStoreError as e:
        # 翻转失败 500 保持 released 可重试（不留半翻转镜像，同文档删除语义）
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e)) from e
    audit_log(
        db,
        actor_id=payload["sub"],
        actor_role=payload.get("role"),
        action="kb.batch.rollback",
        resource="kb_publish_batch",
        resource_id=batch_id,
        detail=f"docs={len(batch.doc_ids)}",
    )
    return BatchActionResp(
        batch_id=batch_id, status="rolled_back", message="批次已回滚（文档对检索不可见）"
    )


@router.get("/batches", response_model=BatchListResp)
def list_batches(
    _: dict = Depends(require_admin),
    db: Session = Depends(get_db),
) -> BatchListResp:
    """批次列表：全部批次含 status / 文档数 / 评测摘要（按锚点 run_id 聚合）。"""
    return BatchListResp(items=svc.list_batches(db))
