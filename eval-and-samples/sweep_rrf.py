#!/usr/bin/env python
"""RRF 权重 + MIN_SCORE 参数扫描（评估用，不改生产代码）。

背景：精确率上限受"文档高度相似"卡死，此前多样性/模板移除实测无效（见 project_memory）。
本脚本只回答务实问题：扫描 RRF 权重能否在 recall@5 不掉前提下提升 hit@1/区分度；
以及提高 MIN_SCORE 会不会误删期望来源。

口径声明：评测库每问只标 1 个期望来源文档、无逐题负样本 → precision@5 测不准，
用 recall@5 + hit@1 + 区分度作代理（与 run_eval 同一口径）。结论若无收益则接受现状。

用法（容器内，依赖 DB/Qdrant/embedding 就绪）：
    docker cp eval-and-samples/sweep_rrf.py 评测问题库.md ground-truth.md lingxi-api-1:/tmp/
    docker exec lingxi-api-1 sh -c "cd /tmp && python sweep_rrf.py"
"""
from __future__ import annotations

import os
import re
import statistics
import sys
from pathlib import Path

from qdrant_client.http.models import FieldCondition, Filter, MatchValue, NamedSparseVector

APP_ROOT = os.environ.get("APP_ROOT", "/app")  # 容器内 backend 挂载点
sys.path.insert(0, APP_ROOT)

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.llm_clients.embedding import BGE_QUERY_PREFIX, get_embedding_client  # noqa: E402
from app.models.knowledge import Document, KnowledgeBase  # noqa: E402
from app.services.sparse_util import text_to_sparse  # noqa: E402
from app.services.vector_service import get_collection_name, get_qdrant_client  # noqa: E402

BASE = Path("/tmp")
TOP_K = int(os.environ.get("SWEEP_TOP_K", "5"))
CANDIDATES = 24  # 与 retrieval_service._HYBRID_CANDIDATES 对齐
RRF_K = 60
WEIGHT_GRID = [(1.0, 0.5), (1.5, 0.5), (2.0, 1.0), (2.0, 0.5), (3.0, 1.0), (3.0, 0.5), (4.0, 1.0), (4.0, 0.5)]
MIN_SCORE_GRID = [0.30, 0.35, 0.40, 0.45]


def parse_questions() -> list[dict]:
    rows = []
    for line in (BASE / "评测问题库.md").read_text(encoding="utf-8").splitlines():
        m = re.match(r"\|\s*(Q\d+)\s*\|[^|]*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|", line)
        if m:
            rows.append({"no": m.group(1), "question": m.group(2), "intent": m.group(3), "source": m.group(4)})
    return rows


def parse_refuse() -> set[str]:
    rows = set()
    for line in (BASE / "ground-truth.md").read_text(encoding="utf-8").splitlines():
        if "**拒答**" in line:
            m = re.match(r"\|\s*(Q\d+)", line)
            if m:
                rows.add(m.group(1))
    return rows


def hit_by_pids(pids: list[str], source: str, doc_titles: dict, pid2doc: dict) -> bool:
    if not source:
        return False
    return any(source in doc_titles.get(pid2doc.get(p, ""), "") for p in pids)


def fuse(dense_hits, sparse_hits, wd: float, ws: float) -> list[tuple[str, float]]:
    acc: dict[str, float] = {}
    for hits, w in ((dense_hits, wd), (sparse_hits, ws)):
        for rank, h in enumerate(hits):
            pid = str(h.id)
            acc[pid] = acc.get(pid, 0.0) + w / (RRF_K + rank + 1)
    return sorted(acc.items(), key=lambda x: -x[1])[:TOP_K]


def main() -> int:
    questions = parse_questions()
    refuse = parse_refuse()
    qa_qs = [q for q in questions if q["intent"] == "问答" and q["no"] not in refuse]
    if not qa_qs:
        print("评测库解析为空")
        return 1

    pguser = os.environ.get("POSTGRES_USER", "lingxi")
    pgpass = os.environ.get("POSTGRES_PASSWORD", "")
    pghost = os.environ.get("POSTGRES_HOST", "postgres")
    pgdb = os.environ.get("POSTGRES_DB", "lingxi")
    pg = create_engine(f"postgresql+psycopg://{pguser}:{pgpass}@{pghost}:5432/{pgdb}")
    with sessionmaker(bind=pg)() as db:
        kb = db.scalar(select(KnowledgeBase).order_by(KnowledgeBase.created_at.desc()).limit(1))
        if not kb:
            print("无知识库")
            return 1
        doc_titles = {str(d.id): d.name for d in db.scalars(select(Document)).all()}
    print(f"KB={str(kb.id)[:8]} 文档数={len(doc_titles)} 问答题={len(qa_qs)}\n")

    name = get_collection_name()
    qdrant = get_qdrant_client()
    embed = get_embedding_client()
    qfilter = Filter(
        must=[
            FieldCondition(key="tenant_id", match=MatchValue(value=settings.TENANT_DEFAULT)),
            FieldCondition(key="kb_id", match=MatchValue(value=str(kb.id))),
        ]
    )

    # 每问取一次原始双路 hits，权重只影响排序合成
    records = []  # (no, source, dense_hits, sparse_hits, pid2doc, dense_score_by_pid)
    for q in qa_qs:
        dense_vec = embed.embed([BGE_QUERY_PREFIX + q["question"]])[0]
        dense_hits = qdrant.search(collection_name=name, query_vector=("dense", dense_vec), limit=CANDIDATES, query_filter=qfilter)
        sparse_vec = text_to_sparse(q["question"])
        sparse_hits = qdrant.search(collection_name=name, query_vector=NamedSparseVector(name="sparse", vector=sparse_vec), limit=CANDIDATES, query_filter=qfilter)
        ds = {str(h.id): float(h.score) for h in dense_hits}
        pid2doc = {str(h.id): str(h.payload.get("doc_id", "")) for h in dense_hits}
        records.append((q["no"], q["source"], dense_hits, sparse_hits, pid2doc, ds))

    print("=== 1) RRF 权重扫描（recall=期望来源进 top5 / hit1=期望来源在 top1）===")
    print(f"{'w_dense':>8} {'w_sparse':>8} {'recall@5':>9} {'hit@1':>7}  ")
    base = None
    for wd, ws in WEIGHT_GRID:
        hit = hit1 = 0
        for _no, src, dh, sh, p2d, ds in records:
            ranked = fuse(dh, sh, wd, ws)
            pids = [pid for pid, _ in ranked]
            if hit_by_pids(pids, src, doc_titles, p2d):
                hit += 1
            if pids and hit_by_pids([pids[0]], src, doc_titles, p2d):
                hit1 += 1
        n = len(records)
        rec, h1 = hit / n, hit1 / n
        mark = " ←基线" if (wd, ws) == (2.0, 1.0) else ""
        tail = ""
        if base and rec >= base[0] - 1e-9 and h1 > base[1] + 0.01:
            tail = "  *hit@1 提升"
        print(f"{wd:>8.1f} {ws:>8.1f} {rec*100:>8.1f}% {h1*100:>6.1f}%{mark}{tail}")
        if (wd, ws) == (2.0, 1.0):
            base = (rec, h1)

    print("\n=== 2) MIN_SCORE 过滤影响（期望来源 doc 的 max dense score < 阈值 → 该题被过滤=recall 损失）===")
    print(f"{'MIN_SCORE':>9} {'期望来源被过滤题数':>16} {'占比':>7}")
    for th in MIN_SCORE_GRID:
        lost = sum(1 for r in records if max((r[5].get(str(h.id), 0.0) for h in r[2]), default=0.0) < th)
        print(f"{th:>9.2f} {lost:>16} {lost/len(records)*100:>6.1f}%")

    print("\n=== 3) 区分度（基线 2.0/1.0）：可答题 vs 拒答题 top1 dense_score ===")
    qa_top1 = []
    for r in records:
        ranked = fuse(r[2], r[3], 2.0, 1.0)
        if ranked:
            top1_pid = ranked[0][0]
            if top1_pid in r[5] and r[5][top1_pid] > 0:
                qa_top1.append(r[5][top1_pid])
    if qa_top1:
        lo = sum(1 for d in qa_top1 if d < 0.30)
        print(f"n={len(qa_top1)} 均值={statistics.mean(qa_top1):.3f} 中位={statistics.median(qa_top1):.3f} "
              f"top1<0.30={lo} ({lo/len(qa_top1)*100:.0f}%)  其余≥0.30")
    return 0


if __name__ == "__main__":
    sys.exit(main())