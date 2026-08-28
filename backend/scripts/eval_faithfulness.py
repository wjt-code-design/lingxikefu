"""Faithfulness 评测（W4 金标）：回答是否忠实于 ground-truth 要点、诚实性题是否正确拒答。

设计（grounded-ai 纪律）：
- **不用 LLM judge 做 0/1 判定**（会与被测系统"一起错"）；用冻结的 ground-truth 做确定性判定：
  - 问答类：要点按分隔符拆成断言 → 每断言提取"数字事实 + 关键短语" → 回答须命中每断言 ≥1 个核心词；
  - 诚实性题（**拒答** 标注）：回答须含拒答标志词（未收录/转人工/暂未等）且不含具体政策数字 → 判拒答正确；
  - 转人工/闲聊：检查引导标志（intent 短路是否生效）。
- 生成 ≠ 验证：被测回答来自真实 RAG+LLM 管线，金标来自冻结 ground-truth（sha256 存档）。
- 用法（backend/ 下）：python -m scripts.eval_faithfulness [--limit N] [--sample N] [--kb-name 星河智家冒烟库]
  --sample 为确定性均匀抽样（跨位置覆盖各主题），CI 硬门禁用它控制耗时；全量评测用于手动/定时。"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

# 容器内直跑引导（与 seed 脚本同款）：把 backend 根加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from app.core.config import settings
from app.core.database import SessionLocal
from app.llm_clients.chat import get_chat_client
from app.models.knowledge import KnowledgeBase
from app.services.rag_service import build_qa_messages, run_pipeline
from scripts.smoke_import import _KB_NAME  # 建库名唯一真源（P0：KB 口径统一）
from sqlalchemy import select

BASE = Path(__file__).resolve().parent.parent.parent / "eval-and-samples"
EVAL_FILE = BASE / "评测问题库.md"
GT_FILE = BASE / "ground-truth.md"

#: 拒答标志词（诚实性题正确表现）
REFUSE_MARKERS = ("未收录", "转人工", "暂未", "暂无", "未明确", "没有找到", "未提供", "尚未")
#: 诚实性"如实否定"话术（资料未覆盖实际能力时，明确否定式回答同样算拒答正确，
#: e.g. Q061"支持花呗吗"→"目前不支持花呗或分期付款"，GT 期望如实告知不支持）
NEGATION_MARKERS = ("不支持", "不提供", "未提供该", "无法", "不可用", "暂未开通")
#: 判定时忽略的弱词（避免子串误命中）
STOPWORDS = {"该", "信息", "资料", "建议", "处理", "规则", "说明", "具体", "相关", "可能", "根据", "平台", "政策"}

_NUM_RE = re.compile(r"¥?\d+(?:\.\d+)?\s*(?:个?月|天|年|小时|分钟|日|层|%|元)?")
#: 带单位数字（编造信号专用）：强化"必须带政策单位才视为编造"，裸序号/纯数字（如"1."）不算
_NUM_POLICY_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:个月|天|年|小时|分钟|日|层|%)")
_CN_RE = re.compile(r"[\u4e00-\u9fff]{2,}")


def _norm(s: str) -> str:
    """数字归一化：全角/半角破折号、空格、¥ 符号统一，避免 '¥1' vs '1 元' 失配。"""
    return s.replace("–", "-").replace("—", "-").replace(" ", "").replace("¥", "")


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
    """诚实性题：含拒答标志（或否定式如实回答）且不含带单位的编造数字 → 拒答正确。"""
    if not any(m in answer for m in REFUSE_MARKERS):
        # 资料未覆盖实际能力时，明确否定式回答（"不支持X / 不提供Y"）同样算合格拒答
        if not any(m in answer for m in NEGATION_MARKERS):
            return False, f"未拒答(无标志词): {answer[:60]}"
    # 若编造了具体数字政策（如"7 天可退""12 个月"）→ 判定编造；
    # 带单位才触发（2026-08-27 修复：裸数字/序号如"1."不再误判）
    nums = _NUM_POLICY_RE.findall(answer)
    if nums:
        return False, f"拒答但含数字政策(疑编造): {nums[:5]} {answer[:60]}"
    return True, ""


def _is_llm_refusal(answer: str) -> bool:
    """LLM 主动拒答判定（收窄版，2026-08-27 修 Q082 假阳性）。

    历史实现用裸子串 ("未收录","转人工","没有找到") 匹配，会把正常回答尾部的
    "如需…可转人工客服" 引导语误判为自拒 → 假"误拒答"。收紧为：
    - 含整段拒答话术（未收录/没有找到/建议转人工/暂未收录 等），"转人工"裸词不再生效；
    - 且不含 [来源N] 引用（有引用的正常回答必带出处，拒答话术不带）。
    例：Q082 "空调已安装使用…可退 [来源2]。如需协助可转人工客服" → 有 [来源 → 判为作答，
    如实归位为 qa 漏点而非"误拒答"。
    """
    if "[来源" in answer:
        return False
    return any(u in answer for u in ("未收录", "没有找到", "建议转人工", "暂未", "尚未"))


def _chunks_have_answer(chunks, claims: list[str]) -> bool:
    """检索结果是否含 GT 任一断言的核心信息（判断 LLM 拒答是否合理）。

    任一断言「数字全中 且 2字窗口交集≥30%」即视为资料含答案。
    """
    ctx = " ".join(c.text for c in chunks)
    ctx_bg = _bigrams(ctx)
    for claim in claims:
        nums, bg = claim_core_words(claim)
        if bg and len(bg & ctx_bg) / len(bg) >= 0.30:
            if not nums or all(_num_hit(ctx, n) for n in nums):
                return True
    return False


def _sentence_overlap(sentence: str, chunk_text: str) -> float:
    """引用点句子与 chunk 的 2 字窗口交集比例（0-1）。"""
    s_bg = _bigrams(sentence)
    c_bg = _bigrams(chunk_text)
    if not s_bg or not c_bg:
        return 0.0
    return len(s_bg & c_bg) / len(s_bg)


def _sentence_supported(sentence: str, chunk_text: str) -> bool:
    """引用点句子是否被对应 chunk 实质支撑（2字窗口交集≥30%）。"""
    return _sentence_overlap(sentence, chunk_text) >= 0.30


def judge_citations(answer: str, chunks, detail: list | None = None) -> tuple[bool, int, int]:
    """[来源N] 引用合法性（grounded-ai：引文必须可溯源到 chunk）。

    每个 [来源N] 的引用点句子须与 chunks[N-1].text 有实质内容重叠，
    防「引文编造」（把内容安到无关来源上）。返回 (是否全合法, 合法数, 总数)。
    detail 非 None 时逐点追加 {"n", "sentence", "overlap", "ok"}（--out 导出/归因用）。

    细节：引用点取标记前最近一个句子（避免多句累积稀释交集）；
    连续引用 [来源1][来源2] 时第二个引用点为空 → 跳过不计（共享同一句子）。
    """
    if not chunks:
        return True, 0, 0
    parts = re.split(r"(\[来源\d+\])", answer)
    ok = total = 0
    cur = ""
    for part in parts:
        m = re.fullmatch(r"\[来源(\d+)\]", part)
        if m:
            n = int(m.group(1))
            if 1 <= n <= len(chunks) and cur.strip():
                total += 1
                sentence = re.split(r"(?<=[。！？；])", cur.strip())[-1]
                overlap = _sentence_overlap(sentence, chunks[n - 1].text)
                supported = overlap >= 0.30
                if supported:
                    ok += 1
                if detail is not None:
                    detail.append({"n": n, "sentence": sentence, "overlap": round(overlap, 3), "ok": supported})
            cur = ""
        else:
            cur += part
    return total == 0 or ok == total, ok, total


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
        return {"qid": q["qid"], "kind": "refuse", "ok": ok, "why": why, "answer": answer}
    if q["intent"] == "qa":
        # 正常题拒答（管线 refuse 或 LLM 主动拒答）：不编造=忠实，但可用性差。
        # 从 faithfulness 分母排除，单列 refuse_qa 统计（合理拒答=资料真没有；误拒答=资料有仍拒）。
        if r.refuse or _is_llm_refusal(answer):
            if gt and _chunks_have_answer(r.chunks, gt["claims"]):
                return {"qid": q["qid"], "kind": "refuse_qa", "ok": False, "why": "误拒答(资料含答案仍拒答)", "answer": answer}
            return {"qid": q["qid"], "kind": "refuse_qa", "ok": True, "why": "合理拒答(资料未含答案)", "answer": answer}
        ok, why = judge_qa(answer, gt["claims"] if gt else [])
        cit_detail: list[dict] = []
        cit_all_ok, cit_good, cit_total = judge_citations(answer, r.chunks, detail=cit_detail)
        return {
            "qid": q["qid"],
            "kind": "qa",
            "ok": ok,
            "why": why,
            "answer": answer,
            # 引用统计必须全量累计（合法题同样计入分母）——此前只在整题全合法时
            # 置 None，导致分母只含失败题，引用合法率被系统性高估/失真（2026-08-27 实测）。
            "cit": (cit_good, cit_total, cit_all_ok),
            "cit_detail": cit_detail,
            # chunks 快照：归因/双跑不依赖重检索（spec D2）
            "chunks": [
                {"i": i + 1, "text": c.text, "score": c.score, "dense_score": c.dense_score, "doc_id": c.doc_id}
                for i, c in enumerate(r.chunks)
            ],
        }
    # 闲聊/转人工：有引导词即可
    ok = any(m in answer for m in ("人工", "客服", "解答", "咨询", "帮助"))
    return {"qid": q["qid"], "kind": q["intent"], "ok": ok, "why": "" if ok else "无引导词", "answer": answer}


async def eval_one_retry(db, kb_id: uuid.UUID, q: dict, gt: dict | None, retries: int = 5) -> dict:
    """LLM 偶发 429 限流 / 超时重试，避免单题拖垮整批。

    - 429 限流退避更长（10s×attempt，LongCat 档位 QPS 较紧）；
    - 超时（ReadTimeout/ConnectTimeout）短退避（2s×attempt）；
    - 其他 HTTP 错误（401/404/500）**不重试**，立即暴露配置/端点错误，不白等。
    """
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return await eval_one(db, kb_id, q, gt)
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 429:
                raise
            last = e
            if attempt < retries:
                await asyncio.sleep(10 * attempt)
                continue
        except httpx.TimeoutException as e:  # ReadTimeout / ConnectTimeout 都重试
            last = e
            if attempt < retries:
                await asyncio.sleep(2 * attempt)
                continue
    raise last


def _resolve_kb(db, kb_name: str | None) -> KnowledgeBase | None:
    """按名取最新同名 KB；找不到退租户最新 KB（与 eval_recall/seed_demo_data 同规则）。"""
    # KB 口径唯一真源 = smoke_import._KB_NAME；旧名"售后与订单全量库"是悬空库名
    # （seed_demo_data 建库为 "demo"），回退到它会误找最新库（P0 修复）。
    name = kb_name or _KB_NAME
    kb = db.scalar(
        select(KnowledgeBase)
        .where(KnowledgeBase.name == name)
        .order_by(KnowledgeBase.created_at.desc())
    )
    if not kb:
        kb = db.scalar(
            select(KnowledgeBase)
            .where(KnowledgeBase.tenant_id == settings.TENANT_DEFAULT)
            .order_by(KnowledgeBase.created_at.desc())
        )
        if kb:
            print(f"[WARN] 未找到 {name!r}，回退最新 KB：{kb.name} ({kb.id})")
    return kb


def _write_report(out_path: str, meta: dict, stats: dict, results: list[dict], cit_good: int, cit_total: int) -> Path:
    """评测结果结构化落盘（归因/双跑对比的单一数据源）。

    meta 自带四件套信息（provider/model/top_k/脚本 sha256 + 调用参数），JSON 自描述；
    必须在 pass_all 判定与 exit 之前调用——门禁 FAIL 也要有完整 JSON 可查（spec D3）。
    results 每题含全文 answer 与 chunks 快照（D2），约 250KB/100 题。
    """
    payload = {
        "meta": {
            "timestamp": datetime.now(UTC).isoformat(),
            "provider": settings.CHAT_PROVIDER,
            "model": settings.LONGCAT_CHAT_MODEL,
            "top_k": settings.RETRIEVAL_TOP_K,
            "script_sha256_12": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12],
            **meta,
        },
        "summary": {"stats": {k: list(v) for k, v in stats.items()}, "citation": [cit_total, cit_good]},
        "results": results,
    }
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _pass_all(stats: dict, cit_good: int, cit_total: int, full_run: bool) -> bool:
    """门禁判定（纯函数，供单测）：qa≥85% 且 refuse≥90%；citation≥95% 仅全量模式判定。

    citation 挂全量模式的原因：抽样 20 题仅 ~15-30 个引用点，95% 门禁=最多错 1 点，
    单点抖动 3-7pp，抽样下判定不可靠（spec A2）。
    full_run 须由调用方以 not(sample or limit or offset) 计算（spec D1，--limit 绕过防护）。
    """
    qa_total, qa_ok = stats["qa"]
    refuse_total, refuse_ok = stats["refuse"]
    if qa_total == 0:
        return False
    if qa_ok / qa_total < 0.85:
        return False
    if refuse_total and refuse_ok / refuse_total < 0.9:
        return False
    if full_run and cit_total and cit_good / cit_total < 0.95:
        return False
    return True


async def _run_faithfulness(
    db, kb_id: uuid.UUID, questions: list[dict], gt: dict, results: list[dict] | None = None
) -> tuple:
    """核心循环：逐题评测并汇总（供 main 与 run_faithfulness_eval 复用）。

    results 非 None 时逐题追加（含 skip/error），供 --out 导出。返回 (stats, fails, cit_good, cit_total)。
    """
    stats = {"qa": [0, 0], "refuse": [0, 0], "refuse_qa": [0, 0], "handoff": [0, 0], "chitchat": [0, 0]}
    fails: list[str] = []
    cit_good = cit_total = 0
    for q in questions:
        g = gt.get(q["qid"])
        if q["intent"] == "qa" and g is None:
            print(f"  [SKIP] {q['qid']} 无 ground-truth")
            if results is not None:
                results.append({"qid": q["qid"], "kind": "skip", "ok": False, "why": "无 ground-truth", "answer": ""})
            continue
        try:
            res = await eval_one_retry(db, kb_id, q, g)
        except Exception as e:  # noqa: BLE001
            print(f"  [ERR] {q['qid']} {type(e).__name__}: {e}")
            if results is not None:
                results.append({"qid": q["qid"], "kind": "error", "ok": False, "why": f"{type(e).__name__}: {e}", "answer": ""})
            res = None
        await asyncio.sleep(0.8)  # 题间轻间隔（成功/失败统一），降低 429 概率
        if res is None:
            continue
        kind = res["kind"] if res["kind"] in stats else "qa"
        stats[kind][0] += 1
        if res["ok"]:
            stats[kind][1] += 1
        else:
            fails.append(f"{res['qid']} [{kind}] {res['why']}")
        if res.get("cit"):
            g0, t0, all_ok = res["cit"]
            cit_good += g0
            cit_total += t0
            if not all_ok and g0 < t0:
                fails.append(f"{res['qid']} [cite] 引用不合法 {g0}/{t0}")
        if results is not None:
            # 成功路径也必须进 results（此前只有 skip/error 收集，--out 导出 results 恒空）
            results.append(res)
        tag = "PASS" if res["ok"] else "FAIL"
        print(f"  [{tag}] {res['qid']} ({kind}) {res['answer'][:60]}")
    return stats, fails, cit_good, cit_total


async def run_faithfulness_eval(
    db, limit: int = 0, kb_name: str | None = None
) -> list[tuple[str, float, int, int]]:
    """后台评测中心复用入口：跑 faithfulness 全量，返回指标元组列表。

    [(metric, score, total, passed), ...]，score=passed/total，供 EvalResult 落表。
    """
    questions = parse_questions()
    gt = parse_ground_truth()
    print(f"[INPUT] 问题 {len(questions)} 题 | ground-truth {len(gt)} 题")
    if limit:
        questions = questions[:limit]
    kb = _resolve_kb(db, kb_name)
    if kb is None:
        print("[ERR] 无任何知识库（先跑 scripts.smoke_import 或 seed_demo_data）")
        return []
    stats, fails, cit_good, cit_total = await _run_faithfulness(db, kb.id, questions, gt)
    print("\n=== FAITHFULNESS 汇总 ===")
    for kind, (total, ok) in stats.items():
        rate = ok / total if total else 0.0
        print(f"  {kind:9s} {ok}/{total} = {rate:.1%}")
    if cit_total:
        print(f"  引用合法率 {cit_good}/{cit_total} = {cit_good / cit_total:.1%}（[来源N] 可溯源，目标≥95%）")
    out: list[tuple[str, float, int, int]] = []
    for kind in ("qa", "refuse", "handoff", "chitchat"):
        total, ok = stats[kind]
        if total:
            out.append((kind, ok / total, total, ok))
    if cit_total:
        out.append(("citation", cit_good / cit_total, cit_total, cit_good))
    if fails:
        print("失败明细:")
        for f in fails:
            print(f"  - {f}")
    return out


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="仅评测前 N 题（0=全部）")
    ap.add_argument("--offset", type=int, default=0, help="跳过前 N 题（续跑用）")
    ap.add_argument(
        "--sample",
        type=int,
        default=0,
        help="确定性均匀抽样 N 题（按列表位置步长取，覆盖各主题；0=全量）。"
        "CI 硬门禁默认 sample 20 控制耗时；需全量请用 workflow_dispatch（full_eval=true）",
    )
    ap.add_argument("--kb-name", default=_KB_NAME)  # 与 smoke_import 建库名一致（P0）
    ap.add_argument(
        "--out", default="",
        help="结果 JSON 落盘路径（相对当前目录；门禁判定前写入，FAIL 也有完整数据）",
    )
    args = ap.parse_args()

    questions = parse_questions()
    gt = parse_ground_truth()
    print(f"[INPUT] 问题 {len(questions)} 题 | ground-truth {len(gt)} 题")
    if args.offset:
        questions = questions[args.offset:]
    if args.limit:
        questions = questions[: args.limit]
    if args.sample:
        step = max(1, len(questions) // args.sample)
        questions = questions[::step][: args.sample]
        print(f"[SAMPLE] 均匀抽样 {len(questions)} 题（步长 {step}，确定性）")

    db = SessionLocal()
    results: list[dict] = []
    try:
        kb = _resolve_kb(db, args.kb_name)
        if kb is None:
            print("[ERR] 无任何知识库（先跑 scripts.smoke_import 或 seed_demo_data）")
            return 2
        stats, fails, cit_good, cit_total = await _run_faithfulness(db, kb.id, questions, gt, results=results)
        if args.out:
            p = _write_report(
                args.out,
                {"kb_name": args.kb_name, "sample": args.sample, "limit": args.limit, "offset": args.offset},
                stats, results, cit_good, cit_total,
            )
            print(f"[OUT] 结果已写入 {p}")
    finally:
        db.close()

    print("\n=== FAITHFULNESS 汇总 ===")
    for kind, (total, ok) in stats.items():
        rate = ok / total if total else 0.0
        print(f"  {kind:9s} {ok}/{total} = {rate:.1%}")
    if cit_total:
        print(f"  引用合法率 {cit_good}/{cit_total} = {cit_good / cit_total:.1%}（[来源N] 可溯源，目标≥95%）")
    rq_total, rq_ok = stats["refuse_qa"]  # noqa: E501  # stats[kind]=[total, ok]
    if rq_total:
        print(f"  [info] 正常题拒答 {rq_total} 题（合理拒答 {rq_ok}，误拒答 {rq_total - rq_ok}，不计入 faithfulness）")
    full_run = not (args.sample or args.limit or args.offset)
    pass_all = _pass_all(stats, cit_good, cit_total, full_run)
    gate_note = "citation≥95% 参与判定" if full_run else "citation 仅报告，不判定（子集模式）"
    print(f"[RESULT] {'PASS ✅' if pass_all else 'FAIL ❌'}（qa≥85% 且 refuse≥90%；{gate_note}）")
    if fails:
        print("失败明细:")
        for f in fails:
            print(f"  - {f}")
    return 0 if pass_all else 1


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(main()))
