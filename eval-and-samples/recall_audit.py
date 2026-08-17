#!/usr/bin/env python
"""检索召回审计（Step 3 尺子）：对题库每题检索 top_k，检查「题库标注的期望来源文档」是否被召回。

背景：评测 4 题误报（Q009/Q034/Q045/Q053）根因是答案段存在但 dense 检索 top8 未召回
（或 chunk 过粗）。本脚本把「期望来源文档在 top-k 的命中率」固化为可重复基线，
供 hybrid 检索（sparse+dense）投入决策前后对比。

用法：
    QDRANT_URL=http://localhost:6333 python eval-and-samples/recall_audit.py [--top-k 8] [--only Q009,Q053]

输出：每题命中与否 + 汇总召回率（尺子）+ 未命中的缺口清单。
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("POSTGRES_DB", "lingxi")
os.environ.setdefault("CHAT_PROVIDER", "zhipu")

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.models.knowledge import Document, KnowledgeBase  # noqa: E402
from app.services.retrieval_service import search_kb  # noqa: E402

BASE = Path(__file__).resolve().parent


def parse_questions() -> dict[str, dict]:
    rows = {}
    for line in (BASE / "评测问题库.md").read_text(encoding="utf-8").splitlines():
        m = re.match(r"\|\s*(Q\d+)\s*\|[^|]*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|", line)
        if m:
            rows[m.group(1)] = {"question": m.group(2), "intent": m.group(3), "source": m.group(4)}
    return rows


def main() -> int:
    top_k = 8
    only: list[str] | None = None
    for a in sys.argv[1:]:
        if a.startswith("--top-k="):
            top_k = int(a.split("=", 1)[1])
        elif a.startswith("--only="):
            only = [x.strip() for x in a.split("=", 1)[1].split(",") if x.strip()]

    qs = parse_questions()
    pg = create_engine(os.environ.get("PG_URL") or "postgresql+psycopg://lingxi:__CHANGE_ME__@localhost:5432/lingxi")
    with sessionmaker(bind=pg)() as db:
        kb = db.scalar(select(KnowledgeBase).order_by(KnowledgeBase.created_at.desc()).limit(1))
        # doc_id → 文档名映射
        rows = db.execute(select(Document.id, Document.name).where(Document.kb_id == kb.id)).all() if kb else []
        name_by_doc = {str(r[0]): r[1] for r in rows}

    qa_nos = [no for no, q in qs.items() if q["intent"] == "问答"]
    if only:
        qa_nos = [n for n in qa_nos if n in only]

    misses: list[tuple[str, str, str]] = []
    hits = 0
    print(f"召回审计：{len(qa_nos)} 题 × top_k={top_k}（KB {str(kb.id)[:8] if kb else '-'}）\n")
    for no in qa_nos:
        q = qs[no]
        chunks = search_kb(q["question"], kb.id, top_k=top_k)
        # 检索命中的文档名集合（去重）
        hit_docs = {name_by_doc.get(c.doc_id, "?") for c in chunks}
        expected = q["source"]
        ok = any(expected in d or d in expected for d in hit_docs if d != "?")
        if ok:
            hits += 1
        else:
            misses.append((no, q["question"], expected))
            print(f"  ❌ {no} [{q['question'][:24]}] 期望来源「{expected}」未进 top{top_k}")
    rate = hits / max(len(qa_nos), 1)
    print(f"\n=== 召回率（尺子）: {hits}/{len(qa_nos)} = {rate:.1%}（期望来源文档进入 top_k 的比例）===")
    print(f"缺口清单（{len(misses)} 题）: {[m[0] for m in misses]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
