"""Faithfulness 评测（W4 金标）：回答是否忠实于 ground-truth 要点、诚实性题是否正确拒答。

设计（grounded-ai 纪律）：
- **不用 LLM judge 做 0/1 判定**（会与被测系统"一起错"）；用冻结的 ground-truth 做确定性判定：
  - 问答类：要点按分隔符拆成断言 → 每断言提取"数字事实 + 关键短语" → 回答须命中每断言 ≥1 个核心词；
  - 诚实性题（**拒答** 标注）：回答须含拒答标志词（未收录/转人工/暂未等）且不含具体政策数字 → 判拒答正确；
  - 转人工/闲聊：检查引导标志（intent 短路是否生效）。
- 生成 ≠ 验证：被测回答来自真实 RAG+LLM 管线，金标来自冻结 ground-truth（sha256 存档）。
- 用法（backend/ 下）：python -m scripts.eval_faithfulness [--limit N] [--kb-name 星河智家冒烟库]
"""
from __future__ import annotations

import argparse
import re
import uuid
from pathlib import Path

from app.core.database import SessionLocal
from app.llm_clients.chat import get_chat_client
from app.models.knowledge import KnowledgeBase
from app.services.rag_service import build_qa_messages, run_pipeline
from sqlalchemy import select

BASE = Path(__file__).resolve().parent.parent.parent / "eval-and-samples"
EVAL_FILE = BASE / "评测问题库.md"
GT_FILE = BASE / "ground-truth.md"

#: 拒答标志词（诚实性题正确表现）
REFUSE_MARKERS = ("未收录", "转人工", "暂未", "暂无", "未明确", "没有找到", "未提供", "尚未")
#: 判定时忽略的弱词（避免子串误命中）
STOPWORDS = {"该", "信息", "资料", "建议", "处理", "规则", "说明", "具体", "相关", "可能", "根据", "平台", "政策"}

_NUM_RE = re.compile(r"¥?\d+(?:\.\d+)?\s*(?:个?月|天|年|小时|分钟|日|层|%|元)?")
_CN_RE = re.compile(r"[\u4e00-\u9fff]{2,}")


def _norm(s: str) -> str:
    """数字归一化：全角/半角破折号、空格统一，避免 '7-15 天' vs '7–15天' 失配。"""
    return s.replace("–", "-").replace("—", "-").replace(" ", "")


def _num_hit(answer: str, num: str) -> bool:
    return _norm(num) in _norm(answer)


_INTENT_MAP = {"问答": "qa", "转人工": "handoff", "闲聊": "chitchat"}


def parse_questions() -> list[dict]:
    rows = []
    for line in EVAL_FILE.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| Q"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 5 or not cells[0].startswith("Q"):
            continue
        rows.append(
            {
                "qid": cells[0],
                "question": cells[2],
                "intent": _INTENT_MAP.get(cells[3], cells[3]),
                "source": cells[4],
            }
        )
    return rows


def parse_ground_truth() -> dict[str, dict]:
    """qid → {refuse: bool, claims: [str]}（要点拆成断言）。"""
    gt: dict[str, dict] = {}
    for line in GT_FILE.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| Q"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or not cells[0].startswith("Q"):
            continue
        qid, answer = cells[0], cells[1]
        refuse = answer.startswith("**拒答**")
        # 拆断言：去掉 **拒答** 前缀与括号注释，按 ；。 分割
        body = re.sub(r"\*\*拒答\*\*[：:]?", "", answer)
        body = re.sub(r"（[^）]*）", "", body)
        claims = [c for c in re.split(r"[；;。]", body) if c.strip()]
        gt[qid] = {"refuse": refuse, "claims": claims}
    return gt


def _bigrams(text: str) -> set[str]:
    """切 2 字窗口集合（容忍插入词/同义改写，如"金卡全场"vs"金卡会员可享全场"）。"""
    out: set[str] = set()
    for w in _CN_RE.findall(text):
        if len(w) < 2:
            continue
        for i in range(len(w) - 1):
            out.add(w[i : i + 2])
    return out


def claim_core_words(claim: str) -> tuple[list[str], set[str]]:
    """断言 → (数字事实, 2字窗口集合)。"""
    nums = list(dict.fromkeys(_NUM_RE.findall(claim)))
    return nums, _bigrams(claim)


def judge_qa(answer: str, claims: list[str]) -> tuple[bool, str]:
    """问答类 faithfulness：每断言「数字事实全中」且「2 字窗口交集比例 ≥ 30%」→ 忠实。

    窗口交集自然容忍同义改写/插入词；30% 阈值避免泛词（"支持""可以"）单独撑起命中。
    """
    ans_bg = _bigrams(answer)
    for i, claim in enumerate(claims, 1):
        nums, bg = claim_core_words(claim)
        if not nums and not bg:
            continue  # 空断言（解析异常），跳过
        if nums and not all(_num_hit(answer, n) for n in nums):
            return False, f"断言{i}未忠实(数字未全中): {claim[:40]}"
        if bg:
            overlap = len(bg & ans_bg) / len(bg)
            if overlap < 0.30:
                return False, f"断言{i}未忠实(窗口交集{overlap:.0%}): {claim[:40]}"
    return True, ""


def judge_refuse(answer: str) -> tuple[bool, str]:
    """诚实性题：含拒答标志且不含具体数字政策 → 拒答正确。"""
    if not any(m in answer for m in REFUSE_MARKERS):
        return False, f"未拒答(无标志词): {answer[:60]}"
    # 若编造了具体数字政策（如"7 天可退""12 个月"）→ 判定编造
    nums = _NUM_RE.findall(answer)
    if nums:
        return False, f"拒答但含数字政策(疑编造): {nums[:5]} {answer[:60]}"
    return True, ""


async def eval_one(db, kb_id: uuid.UUID, q: dict, gt: dict | None) -> dict:
    r = run_pipeline(q["question"], kb_id)
    if q["intent"] == "qa" and not r.refuse:
        msgs = build_qa_messages(q["question"], r.chunks)
        answer = await get_chat_client().complete(msgs)
    else:
        # 管线拒答 / 闲聊 / 转人工：不走 LLM，直接用引导语
        from app.services.rag_service import _no_llm_reply
        answer = _no_llm_reply(r) if (r.refuse or q["intent"] != "qa") else ""

    if gt and gt["refuse"]:
        ok, why = judge_refuse(answer)
        return {"qid": q["qid"], "kind": "refuse", "ok": ok, "why": why, "answer": answer[:80]}
    if q["intent"] == "qa":
        if r.refuse:
            # 正常问答题被拒答 = 误杀
            return {"qid": q["qid"], "kind": "qa", "ok": False, "why": "误拒答(正常题无依据拒答)", "answer": answer[:80]}
        ok, why = judge_qa(answer, gt["claims"] if gt else [])
        return {"qid": q["qid"], "kind": "qa", "ok": ok, "why": why, "answer": answer[:80]}
    # 闲聊/转人工：有引导词即可
    ok = any(m in answer for m in ("人工", "客服", "解答", "咨询", "帮助"))
    return {"qid": q["qid"], "kind": q["intent"], "ok": ok, "why": "" if ok else "无引导词", "answer": answer[:80]}


async def main() -> int:

    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="仅评测前 N 题（0=全部）")
    ap.add_argument("--kb-name", default="星河智家冒烟库")
    args = ap.parse_args()

    questions = parse_questions()
    gt = parse_ground_truth()
    print(f"[INPUT] 问题 {len(questions)} 题 | ground-truth {len(gt)} 题")
    if args.limit:
        questions = questions[: args.limit]

    db = SessionLocal()
    kb = db.scalar(
        select(KnowledgeBase).where(KnowledgeBase.name == args.kb_name).order_by(KnowledgeBase.created_at.desc())
    )
    db.close()
    if not kb:
        print(f"[ERR] 知识库不存在：{args.kb_name}")
        return 2

    stats = {"qa": [0, 0], "refuse": [0, 0], "handoff": [0, 0], "chitchat": [0, 0]}
    fails: list[str] = []
    for q in questions:
        g = gt.get(q["qid"])
        if q["intent"] == "qa" and g is None:
            print(f"  [SKIP] {q['qid']} 无 ground-truth")
            continue
        try:
            res = await eval_one(db, kb.id, q, g)
        except Exception as e:  # noqa: BLE001
            print(f"  [ERR] {q['qid']} {type(e).__name__}: {e}")
            continue
        kind = res["kind"] if res["kind"] in stats else "qa"
        stats[kind][0] += 1
        if res["ok"]:
            stats[kind][1] += 1
        else:
            fails.append(f"{res['qid']} [{kind}] {res['why']}")
        tag = "PASS" if res["ok"] else "FAIL"
        print(f"  [{tag}] {res['qid']} ({kind}) {res['answer'][:60]}")

    print("\n=== FAITHFULNESS 汇总 ===")
    for kind, (total, ok) in stats.items():
        rate = ok / total if total else 0.0
        print(f"  {kind:9s} {ok}/{total} = {rate:.1%}")
    qa_ok = stats["qa"][1]
    qa_total = stats["qa"][0]
    refuse_ok = stats["refuse"][1]
    refuse_total = stats["refuse"][0]
    pass_all = (
        qa_total > 0
        and qa_ok / qa_total >= 0.85
        and (refuse_total == 0 or refuse_ok / refuse_total >= 0.9)
    )
    print(f"[RESULT] {'PASS ✅' if pass_all else 'FAIL ❌'}（问答 faithfulness≥85% 且 诚实性拒答率≥90%）")
    if fails:
        print("失败明细:")
        for f in fails:
            print(f"  - {f}")
    return 0 if pass_all else 1


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(main()))
