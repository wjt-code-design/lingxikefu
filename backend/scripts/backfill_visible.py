"""存量 point 的 visible 字段回填（门禁 v2 G1 部署顺序第一步，架构债清偿配套）。

**部署顺序铁律**：检索 filter（retrieval_service.py）加了 `visible=True` 条件后，
Qdrant 对无该字段的存量 point 判为不匹配——**代码先部署而回填未跑 = 线上检索全挂**。
因此本脚本必须先于含 filter 代码的部署执行（或与部署同窗口、部署后立即执行）。

行为：滚动 scan 全部 point → `set_payload {"visible": true}`（幂等，可重复跑）→
末尾对账：`visible=true` 计数 == 全部 point 计数，不一致 exit 1（人工排查后重跑）。

用法（本地/运维，需容器可达）：
    cd backend && python -m scripts.backfill_visible            # 默认 hybrid 集合
    python -m scripts.backfill_visible --collection lingxi_bge_768
"""
from __future__ import annotations

import argparse
import sys

from app.services.vector_service import ensure_collection, get_collection_name, get_qdrant_client
from qdrant_client.http.models import Filter, IsNullCondition, PointIdsList


def _scroll_all_points(client, collection: str, page_size: int = 256):
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=collection,
            limit=page_size,
            offset=offset,
            # 门禁 v2 G2：必须读 visible 字段才能区分"存量缺失"与"staged=False"，
            # 否则会把暂存批次误翻成已发布（此前 with_payload=False 恒视为缺失）
            with_payload=["visible"],
            with_vectors=False,
        )
        if not points:
            return
        yield from points
        if offset is None:
            return


def main() -> int:
    parser = argparse.ArgumentParser(description="存量 point 回填 visible=true（幂等，带对账）")
    parser.add_argument("--collection", default=None, help="默认取 get_collection_name()（随 RAG_ENABLE_HYBRID）")
    parser.add_argument("--page-size", type=int, default=256)
    args = parser.parse_args()

    collection = args.collection or get_collection_name()
    client = get_qdrant_client()
    ensure_collection()

    total = 0
    patched = 0
    batch_ids: list[str] = []
    for point in _scroll_all_points(client, collection, args.page_size):
        total += 1
        payload = point.payload or {}
        # 门禁 v2 G2：visible=False 是批次暂存（staged）的合法状态——只回填"缺字段"的
        # 存量 point，不得翻转 staged（否则重跑本脚本会静默发布未过门禁的批次）。
        if payload.get("visible") is not None:
            continue  # 幂等：已显式标注（True 或 staged=False）跳过
        batch_ids.append(str(point.id))
        if len(batch_ids) >= args.page_size:
            client.set_payload(
                collection_name=collection,
                payload={"visible": True},
                points=PointIdsList(points=batch_ids),
            )
            patched += len(batch_ids)
            print(f"[BACKFILL] 已回填 {patched}/{total}")
            batch_ids = []
    if batch_ids:
        client.set_payload(
            collection_name=collection,
            payload={"visible": True},
            points=PointIdsList(points=batch_ids),
        )
        patched += len(batch_ids)
    print(f"[BACKFILL] 完成：total={total} patched={patched}（其余本已 visible）")

    # --- 对账：缺 visible 字段的 point 必须为 0（G2 起 staged=False 合法，不再要求全 true）---
    all_count = client.count(collection_name=collection, exact=True).count
    missing = client.count(
        collection_name=collection,
        count_filter=Filter(must=[IsNullCondition(is_null={"key": "visible"})]),
        exact=True,
    ).count
    print(f"[VERIFY] total {all_count}，缺 visible 字段 {missing}")
    if missing:
        print("[VERIFY][FAIL] 对账不一致——存在未回填 point，排查后重跑本脚本")
        return 1
    print("[VERIFY][PASS] 对账一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
