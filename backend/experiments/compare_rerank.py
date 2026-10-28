"""rerank 效果的确定性对比（2026-10-27）。

为什么不用 recall@5 衡量 rerank：
recall@5 判定的是「期望来源文档是否出现在 top-5 集合内」——这是**集合成员**问题。
rerank 只重排**已进入 top-5 的候选**，不改变集合成员。故 recall@5 对 rerank
天然不敏感（实测 OFF/ON 均为 81/82 = 98.8%，完全相同——这不是 rerank 无效，
是指标量纲不对）。

rerank 真正该被衡量的：**排序质量**（期望文档排到第几位）与**引用准确率**
（citation 合法率，需 LLM）。本脚本测前者——零 LLM 成本、确定性可复现。

指标定义：
- top1_hit：期望来源文档是否排在**第 1 位**（rerank 最直接的收益）
- mrr：期望来源文档的倒数排名（多题名次综合，MRR 越接近 1 越好）
- rank 分布：期望文档的名次直方图

跑法（backend 目录下）：
    .venv/Scripts/python.exe experiments/compare_rerank.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.knowledge import Document, KnowledgeBase  # noqa: E402
from app.services.query_rewrite import rewrite  # noqa: E402
from app.services.rag_service import run_pipeline  # noqa: E402
from scripts.eval_faithfulness import parse_questions  # noqa: E402
from scripts.smoke_import import _KB_NAME  # noqa: E402
from sqlalchemy import select  # noqa: E402

BASE = Path(__file__).resolve().parent.parent.parent / "eval-and-samples"


def load_questions():
    """从冻结的评测问题库读题（只读，不改动冻结文件）。

    parse_questions() 返回每题 {qid, question, intent, source}，
    其中 source 即「期望来源」文档名（问题库第 5 列）。
    """

    return parse_questions()


def build_doc_index(kb_id) -> dict[str, list[str]]:
    """doc_id → 该文档的 chunk_id 列表（用于判断"期望文档是否被召回"）。"""
    db = SessionLocal()
    docs = db.execute(select(Document).where(Document.kb_id == kb_id)).scalars().all()
    index = {}
    for d in docs:
        name = getattr(d, "name", None) or getattr(d, "filename", None)
        index[str(d.id)] = name or ""
    db.close()
    return index


def main() -> int:
    print("=== rerank 效果对比（检索层，确定性，零 LLM 成本）===")
    print(f"KB={_KB_NAME}  模型={settings.LONGCAT_CHAT_MODEL}")
    print()

    db = SessionLocal()
    kb = db.execute(select(KnowledgeBase).where(KnowledgeBase.name == _KB_NAME)).scalar_one()
    kb_id = kb.id
    docs = db.execute(select(Document).where(Document.kb_id == kb_id)).scalars().all()
    # 文档名 → doc_id
    # ⚠️ 问题库的「期望来源」列不带扩展名（"退换货政策"），DB 里带（"退换货政策.md"）
    #    → 建归一化映射（去扩展名），否则一张都对不上。
    name2doc = {}
    for d in docs:
        nm = getattr(d, "name", None) or getattr(d, "filename", None)
        if not nm:
            continue
        name2doc[nm] = str(d.id)
        name2doc[nm.rsplit(".", 1)[0]] = str(d.id)   # 去扩展名
    db.close()

    # 题目 + 期望来源（source 来自问题库第 5 列「期望来源」）
    questions = load_questions()
    print(f"题目数={len(questions)}")

    # 只处理问答类：期望来源能在 KB 文档名里对上
    items = []
    skipped = Counter()
    for q in questions:
        src = (q.get("source") or "").strip()
        if not src:
            skipped["无期望来源"] += 1
            continue
        if src not in name2doc:
            skipped[f"来源不在KB({src[:12]})"] += 1
            continue
        if q.get("intent") != "qa":
            skipped[f"非qa({q.get('intent')})"] += 1
            continue
        items.append((q["qid"], q["question"], name2doc[src]))
    print(f"可判定题数={len(items)}  跳过明细={dict(skipped)}\n")

    results = {}
    for label, enable in (("rerank=OFF", False), ("rerank=ON", True)):
        settings.RAG_ENABLE_RERANK = enable
        top1 = 0
        rr_sum = 0.0
        ranks = Counter()
        misses = []
        for qid, question, doc_id in items:
            rewritten, _ = rewrite(question)
            res = run_pipeline(question, kb_id, top_k=settings.RETRIEVAL_TOP_K)
            chunks = res.chunks or []
            # chunk.doc_id 对上期望文档即算命中
            rank = None
            for i, c in enumerate(chunks, 1):
                if str(c.doc_id) == doc_id:
                    rank = i
                    break
            if rank == 1:
                top1 += 1
            if rank:
                ranks[rank] += 1
                rr_sum += 1.0 / rank
            else:
                misses.append(qid)
        n = len(items)
        if n == 0:
            print(f"[{label}] 可判定题数为 0，无法比较——检查文档名映射")
            results[label] = {"top1": 0, "top1_rate": 0.0, "mrr": 0.0, "ranks": {}, "misses": []}
            continue
        results[label] = {
            "top1": top1,
            "top1_rate": top1 / n if n else 0.0,
            "mrr": rr_sum / n if n else 0.0,
            "ranks": dict(sorted(ranks.items())),
            "misses": misses,
        }
        print(f"[{label}]")
        print(f"  top-1 命中率 = {top1}/{n} = {top1 / n:.1%}")
        print(f"  MRR          = {rr_sum / n:.4f}")
        print(f"  名次分布     = {dict(sorted(ranks.items()))}")
        print(f"  未命中       = {len(misses)} 题 {misses[:10]}")
        print()

    settings.RAG_ENABLE_RERANK = True  # 复位
    off, on = results["rerank=OFF"], results["rerank=ON"]
    print("=== 结论 ===")
    d_top1 = on["top1"] - off["top1"]
    d_mrr = on["mrr"] - off["mrr"]
    print(f"  top-1 命中率: {off['top1_rate']:.1%} → {on['top1_rate']:.1%}  ({d_top1:+d} 题)")
    print(f"  MRR:          {off['mrr']:.4f} → {on['mrr']:.4f}  ({d_mrr:+.4f})")
    print()
    print("  注：recall@5（集合成员）对 rerank 不敏感，两种开关均为 81/82 = 98.8%；")
    print("      rerank 的收益体现在**排序质量**（上表）与**引用准确率**（citation，需 LLM）。")

    out = Path(__file__).resolve().parent / "rerank_comparison.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  明细已存: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
