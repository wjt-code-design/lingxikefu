#!/usr/bin/env python
"""v3.1 前置评测：LLM 相关度判定小样（拒答 8 + 可答 8 → 判定召回/准确率）。

- 判定：问题 + top1 chunk（截断 500 字）→ LLM 判「覆盖/不覆盖」（保守偏置：不确定即不覆盖）
- 达标线（v3.1 DoD）：拒答 8 判「不覆盖」召回 100%；可答 8 判「覆盖」准确率 ≥87.5%
- 用智谱 complete（非流式）；判定失败记为 fail（验证降级路径）
- 用法：env -u ZHIPU_API_KEY ZHIPU_API_KEY=$(grep '^ZHIPU_API_KEY=' backend/.env | cut -d= -f2-) \
        python eval-and-samples/judge_pilot.py
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
from app.models.knowledge import KnowledgeBase, Document  # noqa: E402
from app.services.retrieval_service import search_kb  # noqa: E402

BASE = Path(__file__).resolve().parent

#: 诚实性拒答题（ground-truth 标注）
REFUSE_NOS = ["Q042", "Q049", "Q055", "Q060", "Q061", "Q067", "Q069", "Q071"]

JUDGE_SYSTEM = (
    "你是客服知识库质检员。判断用户问题是否被提供的知识内容「充分且直接」回答。"
    "若知识内容只相关但不包含该问题的具体答案条款，判「不覆盖」。"
    "若不确定，判「不覆盖」（保守，宁可转人工）。只回复：覆盖 或 不覆盖。"
)


async def judge(question: str, chunk_text: str) -> bool | None:
    """返回 True=覆盖 / False=不覆盖 / None=判定失败（降级路径）。"""
    messages = [
        {"role": "system", "content": JUDGE_SYSTEM},
        {"role": "user", "content": f"问题：{question}\n知识内容：{chunk_text[:500]}"},
    ]
    try:
        # 判定用非推理快模型（glm-4-flash）：0/1 判定更稳更快（推理模型 glm-4.5-air 过度思考且慢）
        out = await get_chat_client().complete(messages, model="glm-4-flash", max_tokens=50)
        if "不覆盖" in out:
            return False
        if "覆盖" in out:
            return True
        return None
    except Exception:  # noqa: BLE001 - 降级路径验证
        return None


def parse_questions() -> dict[str, dict]:
    rows = {}
    for line in (BASE / "评测问题库.md").read_text(encoding="utf-8").splitlines():
        m = re.match(r"\|\s*(Q\d+)\s*\|[^|]*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|", line)
        if m:
            rows[m.group(1)] = {"question": m.group(2), "intent": m.group(3), "source": m.group(4)}
    return rows


async def main() -> int:
    qs = parse_questions()
    pg = create_engine(os.environ.get("PG_URL") or "postgresql+psycopg://lingxi:__CHANGE_ME__@localhost:5432/lingxi")
    with sessionmaker(bind=pg)() as db:
        kb = db.scalar(select(KnowledgeBase).order_by(KnowledgeBase.created_at.desc()).limit(1))
        doc_titles = {str(d.id): d.name for d in db.scalars(select(Document)).all()}

    # 可答抽样：8 道（4 道 0.40-0.50 + 4 道 0.50+，覆盖分数段）
    qa_pool = [(no, q) for no, q in qs.items() if q["intent"] == "问答" and no not in REFUSE_NOS]
    scored = []
    for no, q in qa_pool:
        ch = search_kb(q["question"], kb.id, top_k=1)
        if ch:
            scored.append((ch[0].score, no, q["question"], ch[0].text))
    scored.sort(key=lambda x: x[0])
    mid = [s for s in scored if 0.40 <= s[0] < 0.50][:4]
    hi = [s for s in scored if s[0] >= 0.50][:4]
    sample_qa = mid + hi
    print(f"可答抽样 {len(sample_qa)}（中分段 {len(mid)} + 高分段 {len(hi)}）")

    # 拒答 8：检索 top1 chunk
    refuse_cases = []
    for no in REFUSE_NOS:
        ch = search_kb(qs[no]["question"], kb.id, top_k=1)
        refuse_cases.append((no, qs[no]["question"], ch[0].text if ch else ""))

    print("\n=== 判定小样（期望：拒答=不覆盖，可答=覆盖）===")
    ok_ref, fail_ref = 0, 0
    for no, q, text in refuse_cases:
        r = await judge(q, text)
        mark = "PASS" if r is False else ("FAIL" if r is True else "ERR")
        if r is False:
            ok_ref += 1
        elif r is None:
            fail_ref += 1
        print(f"  拒答 {no} → {r} [{mark}] top1={search_kb(q, kb.id, top_k=1)[0].score if search_kb(q, kb.id, top_k=1) else 0:.3f}")

    ok_qa, fail_qa = 0, 0
    for _, no, q, text in sample_qa:
        r = await judge(q, text)
        mark = "PASS" if r is True else ("FAIL" if r is False else "ERR")
        if r is True:
            ok_qa += 1
        elif r is None:
            fail_qa += 1
        print(f"  可答 {no} → {r} [{mark}]")

    n_ref = len(refuse_cases)
    n_qa = len(sample_qa)
    print("\n=== 判定小样指标（v3.1 DoD：拒答召回 100%，可答准确率 ≥87.5%）===")
    print(f"拒答判「不覆盖」召回: {ok_ref}/{n_ref} = {ok_ref/n_ref:.0%}（判定失败 {fail_ref}）")
    print(f"可答判「覆盖」准确率: {ok_qa}/{n_qa} = {ok_qa/n_qa:.0%}（判定失败 {fail_qa}）")
    ok = ok_ref == n_ref and ok_qa >= max(1, int(n_qa * 0.875))
    print(f"达标: {'✅ 是，继续 v3.1' if ok else '❌ 否，回退方案'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
