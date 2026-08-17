#!/usr/bin/env python
"""灵犀检索质量评测（T7 自动化）：评测集 100 问 + 口语 100 变体 → recall@5 A/B（QueryRewrite on/off）。

- 只测检索层（search_kb），不跑 LLM/缓存——评测口径纯净（grounded-ai：生成≠验证，缓存 on/off 分开）。
- 指标：recall@5（top-5 chunk 的 doc 命中「期望来源」）、平均 top1 score、诚实性 8 题 top1 分布。
- 依赖：宿主直连容器 PG（doc title）+ Qdrant + 本地 bge embedding。
- 用法：POSTGRES_PASSWORD=... python eval-and-samples/run_eval.py
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("POSTGRES_DB", "lingxi")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.models.knowledge import KnowledgeBase, Document  # noqa: E402
from app.services.query_rewrite import rewrite  # noqa: E402
from app.services.retrieval_service import search_kb  # noqa: E402

BASE = Path(__file__).resolve().parent
TOP_K = 5


def parse_questions() -> list[dict]:
    """解析 评测问题库.md 表格：编号/问题/意图/期望来源。"""
    rows = []
    for line in (BASE / "评测问题库.md").read_text(encoding="utf-8").splitlines():
        m = re.match(r"\|\s*(Q\d+)\s*\|[^|]*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|", line)
        if m:
            rows.append({"no": m.group(1), "question": m.group(2), "intent": m.group(3), "source": m.group(4)})
    return rows


def parse_variants() -> list[dict]:
    """解析 口语化评测集.md：变体/原题号。"""
    rows = []
    for line in (BASE / "口语化评测集.md").read_text(encoding="utf-8").splitlines():
        m = re.match(r"\|\s*(S\d+)\s*\|\s*(Q\d+)\s*\|\s*([^|]+?)\s*\|", line)
        if m:
            rows.append({"no": m.group(1), "orig": m.group(2), "variant": m.group(3)})
    return rows


def parse_refuse() -> set[str]:
    """诚实性拒答题：ground-truth.md 中标注 **拒答** 的编号（意图列不区分，据实修正）。"""
    rows = set()
    for line in (BASE / "ground-truth.md").read_text(encoding="utf-8").splitlines():
        if "**拒答**" in line:
            m = re.match(r"\|\s*(Q\d+)", line)
            if m:
                rows.add(m.group(1))
    return rows


def main() -> int:
    questions = parse_questions()
    variants = parse_variants()
    refuse_set = parse_refuse()
    print(f"问题库 {len(questions)} 条 / 口语变体 {len(variants)} 条 / 诚实性拒答题 {len(refuse_set)} 条")

    pg = create_engine(os.environ.get("PG_URL") or "postgresql+psycopg://lingxi:__CHANGE_ME__@localhost:5432/lingxi")
    with sessionmaker(bind=pg)() as db:
        kb = db.scalar(select(KnowledgeBase).order_by(KnowledgeBase.created_at.desc()).limit(1))
        if not kb:
            print("无知识库，先导入")
            return 1
        doc_titles = {str(d.id): d.name for d in db.scalars(select(Document)).all()}
    print(f"KB={kb.id} 文档数={len(doc_titles)}")

    def _hit(chunks, source: str) -> bool:
        if not source:
            return False  # 空期望来源 → 不算命中（防假命中）
        return any(source in doc_titles.get(c.doc_id, "") for c in chunks)

    qa_qs = [q for q in questions if q["intent"] == "问答" and q["no"] not in refuse_set]
    refuse_qs = [q for q in questions if q["no"] in refuse_set]
    print(f"问答 {len(qa_qs)} 条 / 诚实性(拒答/转人工) {len(refuse_qs)} 条")

    stats = {"on": {"hit": 0, "top1": []}, "off": {"hit": 0, "top1": []}, "refuse_top1": []}
    # T12 分布分析：可答 vs 拒答的 top1 与 margin（top1-top2）
    dist = {"qa_top1": [], "qa_margin": [], "ref_top1": [], "ref_margin": []}

    for q in qa_qs:
        rewritten, _ = rewrite(q["question"])
        on = search_kb(rewritten, kb.id, top_k=5)
        off = search_kb(q["question"], kb.id, top_k=5)
        if _hit(on, q["source"]):
            stats["on"]["hit"] += 1
        if _hit(off, q["source"]):
            stats["off"]["hit"] += 1
        if on:
            stats["on"]["top1"].append(on[0].score)
            dist["qa_top1"].append(on[0].score)
            dist["qa_margin"].append(on[0].score - (on[1].score if len(on) > 1 else 0.0))
        if off:
            stats["off"]["top1"].append(off[0].score)

    for q in refuse_qs:
        ch = search_kb(q["question"], kb.id, top_k=2)
        if ch:
            stats["refuse_top1"].append(ch[0].score)
            dist["ref_top1"].append(ch[0].score)
            dist["ref_margin"].append(ch[0].score - (ch[1].score if len(ch) > 1 else 0.0))

    n = len(qa_qs)
    print("\n=== 检索质量（recall@5，期望来源命中）===")
    print(f"rewrite OFF: recall@5 = {stats['off']['hit']}/{n} = {stats['off']['hit']/n:.1%}  avg_top1={sum(stats['off']['top1'])/max(len(stats['off']['top1']),1):.3f}")
    print(f"rewrite ON : recall@5 = {stats['on']['hit']}/{n} = {stats['on']['hit']/n:.1%}  avg_top1={sum(stats['on']['top1'])/max(len(stats['on']['top1']),1):.3f}")
    if stats["refuse_top1"]:
        avg = sum(stats["refuse_top1"]) / len(stats["refuse_top1"])
        print(f"诚实性{len(refuse_qs)}题 avg_top1={avg:.3f}（<MIN_SCORE={0.30} 越多越合理=拒答正确）")

    # 口语变体（rewrite on 口径）
    v_hit, v_n = 0, 0
    by_orig = {v["orig"]: v["variant"] for v in variants}
    for q in qa_qs:
        v = by_orig.get(q["no"])
        if not v:
            continue
        v_n += 1
        rewritten, _ = rewrite(v)
        ch = search_kb(rewritten, kb.id, top_k=TOP_K)
        if _hit(ch, q["source"]):
            v_hit += 1
    if v_n:
        print(f"口语变体(rewrite ON): recall@5 = {v_hit}/{v_n} = {v_hit/v_n:.1%}")

    # 分布分析（T12：可答 vs 拒答的 top1/margin 分桶，用于定兜底切点）
    def _hist(vals: list[float], buckets: list[tuple[float, float]], label: str) -> None:
        counts = {b: 0 for b in buckets}
        for v in vals:
            for b in buckets:
                if b[0] <= v < b[1]:
                    counts[b] += 1
                    break
        parts = [f"{b[0]:.2f}-{b[1]:.2f}:{counts[b]}" for b in buckets if counts[b]]
        print(f"  {label} (n={len(vals)}): {' '.join(parts)}")

    buckets10 = [(i / 10, (i + 1) / 10) for i in range(10)]
    print("\n=== 分布分析（top1 分桶，rewrite ON）===")
    _hist(dist["qa_top1"], buckets10, "可答 top1")
    _hist(dist["ref_top1"], buckets10, "拒答 top1")
    print("=== 分布分析（margin=top1-top2）===")
    mb = [(i / 10, (i + 1) / 10) for i in range(10)]
    _hist(dist["qa_margin"], mb, "可答 margin")
    _hist(dist["ref_margin"], mb, "拒答 margin")
    return 0


if __name__ == "__main__":
    sys.exit(main())
