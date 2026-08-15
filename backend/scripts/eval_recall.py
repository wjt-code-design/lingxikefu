"""RAG 检索侧 recall@5 评测（BU-05 立尺子）。

用法（backend/ 下，需 docker compose up postgres qdrant + 已导入 18 文件）：
    python -m scripts.eval_recall [--top-k 5] [--kb-name 星河智家冒烟库]

指标：
- recall@5：检索 top-5 命中"期望来源文档"的比例（只算 预期意图=问答 的题）
- 诚实性题单独报"未命中率"（这些题 KB 未覆盖，正确表现=检索不到→拒答；
  若检索到了反而说明来源标注失配，需核查）

评测口径与 vet-plan 一致：先立尺子再投入；基线冻结（题集已冻结，答案 W4 回填）。
"""
from __future__ import annotations

import argparse
from pathlib import Path

from app.core.database import SessionLocal
from app.models.knowledge import Document, KnowledgeBase
from app.services.retrieval_service import RetrievalError, search_kb
from sqlalchemy import select

BASE = Path(__file__).resolve().parent.parent.parent / "eval-and-samples"
EVAL_FILE = BASE / "评测问题库.md"

# 诚实性 / 拒答题（KB 未明确覆盖，正确表现是拒答而非编造）
HONESTY_IDS = {"Q042", "Q049", "Q055", "Q060", "Q061", "Q067", "Q069", "Q071"}


def parse_questions() -> list[dict]:
    """解析评测库表格：| Q001 | 分类 | 问题 | 预期意图 | 期望来源 | ground-truth |

    用 split("|") 而非正则：中文列值含各种字符，非贪婪分组易失配。
    """
    rows: list[dict] = []
    for line in EVAL_FILE.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| Q"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 6 or not cells[0].startswith("Q"):
            continue
        qid, cat, question, intent, source = cells[0], cells[1], cells[2], cells[3], cells[4]
        rows.append(
            {
                "qid": qid,
                "category": cat,
                "question": question,
                "intent": intent,
                "source": source,
                "honesty": qid in HONESTY_IDS,
            }
        )
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--kb-name", default="星河智家冒烟库")
    args = ap.parse_args()

    questions = parse_questions()
    if not questions:
        print(f"[ERR] 评测库解析失败：{EVAL_FILE}")
        return 2

    db = SessionLocal()
    try:
        # 同名 KB 可能有多个（冒烟每次 new 一个），取最新（created_at 降序）
        kb = db.scalar(
            select(KnowledgeBase)
            .where(KnowledgeBase.name == args.kb_name)
            .order_by(KnowledgeBase.created_at.desc())
        )
        if not kb:
            print(f"[ERR] 知识库不存在：{args.kb_name}（先跑 scripts.smoke_import）")
            return 2
        # doc 名 → id 映射（来源列是文档名，检索命中按 doc_id 归属判定）
        docs = db.scalars(
            select(Document).where(Document.kb_id == kb.id, Document.status == "indexed")
        ).all()
        name_variants = {d.name: str(d.id) for d in docs}

        def resolve_doc_id(source: str) -> str | None:
            """期望来源列（如 "退换货政策"）→ doc_id（doc 名带扩展名，做前缀匹配）。"""
            for name, did in name_variants.items():
                if name.startswith(source):
                    return did
            return None

        qa = [q for q in questions if q["intent"] == "问答"]
        honesty = [q for q in questions if q["honesty"]]
        hit, total, miss = 0, 0, []
        honesty_hit = 0
        for q in qa:
            did = resolve_doc_id(q["source"])
            if not did:
                print(f"  [WARN] {q['qid']} 来源无法匹配文档: {q['source']!r}")
                continue
            try:
                chunks = search_kb(q["question"], kb.id, top_k=args.top_k)
            except RetrievalError as e:
                print(f"  [ERR] {q['qid']} 检索失败: {e}")
                continue
            hit_ids = {c.doc_id for c in chunks}
            ok = did in hit_ids
            if q["honesty"]:
                # 诚实性题：KB 未覆盖 → 检索到=来源标注失配（W4 拒答评测的边界核查）
                if ok:
                    honesty_hit += 1
                    print(f"  [HONESTY-HIT] {q['qid']} {q['question']} → 检索到 {q['source']}（待核）")
                continue
            total += 1
            if ok:
                hit += 1
            else:
                miss.append(q["qid"])

        recall = hit / total if total else 0.0
        print(f"\n[RECALL@{args.top_k}] {hit}/{total} = {recall:.1%}  （问答类 {total} 题）")
        if miss:
            print(f"  [MISS] {', '.join(miss)}")
        if honesty:
            print(f"[HONESTY] {len(honesty)} 题中 {honesty_hit} 题检索命中（应≈0，>0 需核查来源标注）")
        # 立尺子判定：recall@5 ≥ 85% 为达标（规划书 DoD）
        ok = recall >= 0.85
        print(f"[RESULT] {'PASS ✅' if ok else 'BELOW-THRESHOLD ❌'}（门槛 recall@{args.top_k}≥85%）")
        return 0 if ok else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
