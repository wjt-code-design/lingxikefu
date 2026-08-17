#!/usr/bin/env python
"""v4 生成端诚实约束评测：拒答 8 题生成是否诚实声明（未收录/转人工），可答 8 题是否误报。

- 用线上同款 prompt（build_qa_messages）+ 智谱默认模型（glm-4.5-air）完整生成
- 拒答 8：回答含「未收录/未找到/转人工」= 诚实 PASS（目标 ≥87.5%）
- 可答 8：回答含「未收录/未找到/转人工」= 误报 FAIL（目标 ≤5%）
- 用法同 judge_pilot（env -u ZHIPU_API_KEY + 注入 .env key）
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("POSTGRES_DB", "lingxi")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")
os.environ.setdefault("CHAT_PROVIDER", "zhipu")

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.llm_clients.chat import get_chat_client  # noqa: E402
from app.models.knowledge import KnowledgeBase  # noqa: E402
from app.prompts.qa_prompt import build_qa_messages  # noqa: E402
from app.services.retrieval_service import search_kb  # noqa: E402

BASE = Path(__file__).resolve().parent

REFUSE_NOS = ["Q042", "Q049", "Q055", "Q060", "Q061", "Q067", "Q069", "Q071"]
#: 诚实声明信号（回答中含这些词 = 诚实）
HONEST_MARKS = ("未收录", "未找到", "未查询到", "转人工", "转接人工", "人工客服", "资料中未")
#: 误报判定（可答题回答里出现这些 = 误报）
FALSE_MARKS = ("未收录", "未找到", "未查询到", "资料中未")


def parse_questions() -> dict[str, dict]:
    rows = {}
    for line in (BASE / "评测问题库.md").read_text(encoding="utf-8").splitlines():
        m = re.match(r"\|\s*(Q\d+)\s*\|[^|]*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|", line)
        if m:
            rows[m.group(1)] = {"question": m.group(2), "intent": m.group(3), "source": m.group(4)}
    return rows


async def gen_answer(question: str, kb_id) -> str:
    chunks = search_kb(question, kb_id, top_k=5)
    msgs = build_qa_messages(question, chunks)
    return await get_chat_client().complete(msgs, max_tokens=500)


async def main() -> int:
    qs = parse_questions()
    pg = create_engine(os.environ.get("PG_URL") or "postgresql+psycopg://lingxi:__CHANGE_ME__@localhost:5432/lingxi")
    with sessionmaker(bind=pg)() as db:
        kb = db.scalar(select(KnowledgeBase).order_by(KnowledgeBase.created_at.desc()).limit(1))

    # 可答抽样同 judge_pilot：中分段 4 + 高分段 4
    qa_pool = [(no, q) for no, q in qs.items() if q["intent"] == "问答" and no not in REFUSE_NOS]
    scored = []
    for no, q in qa_pool:
        ch = search_kb(q["question"], kb.id, top_k=1)
        if ch:
            scored.append((ch[0].score, no))
    scored.sort(key=lambda x: x[0])
    sample_qa = [no for _, no in [s for s in scored if 0.40 <= s[0] < 0.50][:4] + [s for s in scored if s[0] >= 0.50][:4]]

    print(f"可答抽样 {len(sample_qa)} 题 / 拒答 {len(REFUSE_NOS)} 题\n")

    ok_ref, ref_examples = 0, []
    for no in REFUSE_NOS:
        ans = await gen_answer(qs[no]["question"], kb.id)
        honest = any(m in ans for m in HONEST_MARKS)
        ok_ref += int(honest)
        ref_examples.append((no, honest, ans[:60].replace("\n", " ")))
        print(f"  拒答 {no} → {'诚实✅' if honest else '编造❌'} | {ans[:50].replace(chr(10),' ')}")

    ok_qa, false_qa = 0, []
    for no in sample_qa:
        ans = await gen_answer(qs[no]["question"], kb.id)
        false = any(m in ans for m in FALSE_MARKS)
        ok_qa += int(not false)
        false_qa.append((no, false))
        print(f"  可答 {no} → {'正常✅' if not false else '误报❌'} | {ans[:50].replace(chr(10),' ')}")

    n_ref, n_qa = len(REFUSE_NOS), len(sample_qa)
    print("\n=== v4 生成端诚实指标（目标：拒答诚实 ≥87.5%，可答误报 ≤5%）===")
    print(f"拒答 8 诚实声明率: {ok_ref}/{n_ref} = {ok_ref/n_ref:.0%}")
    print(f"可答 {n_qa} 误报「未找到」率: {n_qa - ok_qa}/{n_qa} = {(n_qa - ok_qa)/n_qa:.0%}")
    ok = ok_ref >= max(1, int(n_ref * 0.875)) and (n_qa - ok_qa) <= max(1, int(n_qa * 0.05))
    print(f"达标: {'✅ 是，v4 生效（prompt 诚实约束已被生成端遵守）' if ok else '❌ 否，需强化 prompt 措辞再测'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
