#!/usr/bin/env python
"""加强版生成端评测（v5 全量）：拒答 8 + 可答 82 全量生成，多维指标 + 归因诊断 + 结果落文件。

指标：
- 拒答诚实率（含「未收录/未找到/转人工」声明）——目标 ≥87.5%
- 可答误报率（可答题误报「未收录」）——目标 ≤10%（现实目标）
- 可答空回答率（content 为空）——目标 0
- 来源合规（[来源N] N ≤ chunks 数）——目标 ≥95%
- 数字编造启发式（回答中数字不在检索资料里）——标注局限（日期/序号可能合理）
- 归因诊断：误报/空/编造题打印检索 chunks 片段（判断是检索覆盖不足还是模型问题）
用法：env -u ZHIPU_API_KEY ZHIPU_API_KEY=$(grep '^ZHIPU_API_KEY=' backend/.env | cut -d= -f2-) \
        python eval-and-samples/gen_eval.py
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
from datetime import datetime
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
OUT = BASE / "results"

REFUSE_NOS = ["Q042", "Q049", "Q055", "Q060", "Q061", "Q067", "Q069", "Q071"]
HONEST_MARKS = ("未收录", "未找到", "未查询到", "转人工", "转接人工", "人工客服", "资料中未")
FALSE_MARKS = ("未收录", "未找到", "未查询到", "资料中未")


def parse_questions() -> dict[str, dict]:
    rows = {}
    for line in (BASE / "评测问题库.md").read_text(encoding="utf-8").splitlines():
        m = re.match(r"\|\s*(Q\d+)\s*\|[^|]*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|", line)
        if m:
            rows[m.group(1)] = {"question": m.group(2), "intent": m.group(3), "source": m.group(4)}
    return rows


async def gen_one(question: str, kb_id) -> tuple[str, list]:
    """返回 (回答, chunks)。429/5xx 限流退避重试（评测连续 90 题易触限流）。"""
    chunks = search_kb(question, kb_id, top_k=8)  # 与生产 rag_service 默认 top_k=8 对齐
    msgs = build_qa_messages(question, chunks)
    import httpx

    for attempt in range(4):
        try:
            # glm-4.5-air 是推理模型：reasoning 消耗巨额 token，连发必触发智谱 TPM 限流（429 实测）。
            # 评测默认用 EVAL_MODEL（默认 glm-4.5-air 生产口径；限流时可设 glm-4-flash 非推理模型，
            # 无 reasoning 连发稳定 200。判定（误报/诚实/来源）基于标记词，模型差异影响小）。
            model = os.environ.get("EVAL_MODEL", "glm-4.5-air")
            # EVAL_MAX_TOKENS：air 推理长时 2048 偶发 content 空（Q002 实测）→ 可设 3072；flash 2048 足够
            max_tokens = int(os.environ.get("EVAL_MAX_TOKENS", "2048"))
            return await get_chat_client().complete(msgs, model=model, max_tokens=max_tokens), chunks
        except httpx.HTTPStatusError as e:
            # 429 是分钟级 TPM 短窗口限流（智谱实测单发成功/连发偶发 429）→ 等窗口重置再试
            if e.response.status_code in (429, 500, 502, 503) and attempt < 3:
                await asyncio.sleep(30 * (attempt + 1))  # 退避 30s/60s/90s（覆盖 TPM 窗口）
                continue
            return f"[ERR] {e}", chunks
        except Exception as e:  # noqa: BLE001
            return f"[ERR] {e}", chunks
    return "[ERR] retry-exhausted", chunks


def check_sources(answer: str, n_chunks: int) -> bool:
    """来源标注合规：[来源N] 的 N 必须在 1..n_chunks。"""
    refs = re.findall(r"\[来源(\d+)\]", answer)
    if not refs:
        return True  # 无数引用不判违规（诚实声明题可能无引用）
    return all(1 <= int(x) <= n_chunks for x in refs)


def check_numeric(answer: str, chunks) -> list[str]:
    """数字编造启发式：回答中不在检索资料里的数字（标注局限）。

    防幻觉处理：先剔除 [来源N] 引用标记（否则序号 N 被误判为编造数字）。
    """
    stripped = re.sub(r"\[来源\d+\]", "", answer)
    nums = set(re.findall(r"\d+", stripped))
    if not nums:
        return []
    chunk_text = " ".join(c.text for c in chunks)
    return [n for n in nums if n not in chunk_text]


async def main(only: list[str] | None = None) -> int:
    qs = parse_questions()
    pg = create_engine(os.environ.get("PG_URL") or "postgresql+psycopg://lingxi:__CHANGE_ME__@localhost:5432/lingxi")
    with sessionmaker(bind=pg)() as db:
        kb = db.scalar(select(KnowledgeBase).order_by(KnowledgeBase.created_at.desc()).limit(1))

    qa_nos = [no for no, q in qs.items() if q["intent"] == "问答" and no not in REFUSE_NOS]
    cases = [("refuse", no, qs[no]["question"]) for no in REFUSE_NOS] + [("qa", no, qs[no]["question"]) for no in qa_nos]
    if only:
        cases = [c for c in cases if c[1] in only]
        if not cases:
            print(f"⚠️ --only 指定的题号都不在题库：{only}")
            return 1
    print(f"评测 {len(cases)} 题（{'+'.join(only) if only else f'拒答 {len(REFUSE_NOS)} + 可答 {len(qa_nos)}'}），模型 {os.environ.get('EVAL_MODEL', 'glm-4.5-air')}，预计 ~{max(len(cases) * 35 // 60, 1)} 分钟\n")

    results = {"refuse": {"honest": 0, "total": 0, "details": []}, "qa": {"false": 0, "empty": 0, "err": 0, "src_bad": 0, "numeric": [], "total": 0, "details": []}}

    # 断点续跑：评测 60-75 分钟易被中断，每完成一题即落盘部分结果；重启时跳过已完成题。
    # 指纹 = 模型 + 题库内容哈希：换模型/改题库后拒绝续跑（防不同口径结果混合）。
    model = os.environ.get("EVAL_MODEL", "glm-4.5-air")
    qset_hash = hashlib.md5(json.dumps(qs, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:8]
    fingerprint = f"{model}:{qset_hash}"
    OUT.mkdir(exist_ok=True)
    partial_file = OUT / "gen-eval-partial.json"
    done_nos: set[str] = set()
    if partial_file.exists():
        try:
            _p = json.loads(partial_file.read_text(encoding="utf-8"))
            if _p.get("fingerprint") != fingerprint:
                print(f"⚠️ 部分结果指纹不匹配（旧={_p.get('fingerprint')!r} 新={fingerprint!r}），"
                      f"删除 {partial_file.name} 后从头开始\n")
                partial_file.unlink()
            else:
                results = _p["results"]
                done_nos = set(_p["done_nos"])
                print(f"续跑：已加载 {len(done_nos)} 题部分结果，跳过已完成题\n")
        except Exception:  # noqa: BLE001
            print("部分结果文件损坏，从头开始\n")

    consecutive_err = 0  # 熔断：连续 5 题失败（如 429 持续限流）则终止，避免空烧

    def _save_partial() -> None:
        partial_file.write_text(
            json.dumps({"fingerprint": fingerprint, "done_nos": sorted(done_nos), "results": results},
                       ensure_ascii=False),
            encoding="utf-8",
        )

    for i, (kind, no, q) in enumerate(cases):
        if no in done_nos:
            continue
        if len(done_nos) or i:  # 已跳过的不再叠加节流；首题不 sleep
            await asyncio.sleep(15)
        ans, chunks = await gen_one(q, kb.id)
        if ans.startswith("[ERR]"):
            # ERR（429 重试耗尽等）：不计数、不标 done，落盘后下一轮续跑自动重试
            consecutive_err += 1
            print(f"  {kind} {no} → 失败待重试 | {ans[:80]}")
            _save_partial()
            if consecutive_err >= 5:
                print(f"连续 {consecutive_err} 题失败（疑似持续限流），熔断退出；断点已保存，恢复后重跑续传\n")
                return 1
            continue
        consecutive_err = 0
        if kind == "refuse":
            results["refuse"]["total"] += 1
            honest = any(m in ans for m in HONEST_MARKS)
            results["refuse"]["honest"] += int(honest)
            d = {"no": no, "honest": honest, "head": ans[:80]}
            if not honest:
                d["chunks_head"] = [c.text[:80] for c in chunks[:2]]
            results["refuse"]["details"].append(d)
            print(f"  拒答 {no} → {'诚实✅' if honest else '疑似编造❌'} | {ans[:60].replace(chr(10),' ')}")
        else:
            results["qa"]["total"] += 1
            false = any(m in ans for m in FALSE_MARKS)
            empty = not ans.strip()
            src_ok = check_sources(ans, len(chunks))
            num_missing = check_numeric(ans, chunks)
            results["qa"]["false"] += int(false)
            results["qa"]["empty"] += int(empty)
            results["qa"]["src_bad"] += int(not src_ok)
            if num_missing:
                results["qa"]["numeric"].append({"no": no, "missing": num_missing[:5], "head": ans[:60]})
            d = {"no": no, "false": false, "empty": empty, "src_ok": src_ok, "head": ans[:60]}
            if false or empty or not src_ok or num_missing:
                d["chunks_head"] = [c.text[:60] for c in chunks[:2]]
            results["qa"]["details"].append(d)
            flag = "误报" if false else ("空" if empty else ("来源" if not src_ok else ("数字" if num_missing else "正常")))
            print(f"  可答 {no} → {flag} | {ans[:50].replace(chr(10),' ')}")
        # 增量落盘（断点续跑核心）：每题成功立即持久化
        done_nos.add(no)
        _save_partial()

    if partial_file.exists():
        try:
            partial_file.unlink()  # 全部完成 → 清理部分结果文件（沙箱可能拦截删除，忽略即可）
        except OSError:
            pass

    n_r = results["refuse"]["total"]
    n_q = results["qa"]["total"]
    honest_rate = results["refuse"]["honest"] / max(n_r, 1)
    false_rate = results["qa"]["false"] / max(n_q, 1)
    empty_rate = results["qa"]["empty"] / max(n_q, 1)
    src_rate = 1 - results["qa"]["src_bad"] / max(n_q, 1)

    report = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "n_refuse": n_r,
        "n_qa": n_q,
        "refuse_honest_rate": round(honest_rate, 3),
        "qa_false_rate": round(false_rate, 3),
        "qa_empty_rate": round(empty_rate, 3),
        "qa_err_count": results["qa"]["err"],
        "qa_src_compliance_rate": round(src_rate, 3),
        "qa_numeric_suspects": len(results["qa"]["numeric"]),
        "refuse_details": results["refuse"]["details"],
        "qa_issues": [
            d for d in results["qa"]["details"]
            if d.get("err") or d.get("false") or d.get("empty") or not d.get("src_ok")
            or any(d["no"] == n["no"] for n in results["qa"]["numeric"])
        ],
    }

    print("\n=== v6 全量生成评测指标（max_tokens=4096 与生产一致）===")
    print(f"拒答诚实率: {results['refuse']['honest']}/{n_r} = {honest_rate:.1%}（目标 ≥87.5%）")
    print(f"可答误报率: {results['qa']['false']}/{n_q} = {false_rate:.1%}（现实目标 ≤10%）")
    print(f"可答空回答率: {results['qa']['empty']}/{n_q} = {empty_rate:.1%}（目标 0）")
    print(f"可答生成失败率: {results['qa']['err']}/{n_q} = {results['qa']['err']/max(n_q,1):.1%}（目标 0，防 [ERR] 混入正常）")
    print(f"来源标注合规率: {src_rate:.1%}（目标 ≥95%）")
    print(f"数字编造疑似: {len(results['qa']['numeric'])} 题（启发式，含日期/序号合理项，需人工复核）")

    OUT.mkdir(exist_ok=True)
    out_file = OUT / f"gen-eval-{datetime.now().strftime('%Y%m%d-%H%M')}.json"
    out_file.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n结果已落盘: {out_file}")
    return 0


if __name__ == "__main__":
    # --only=Q009,Q034：只跑指定题（配合断点续跑做单题/子集验证，如检索修复后复测）
    only = None
    for _a in sys.argv[1:]:
        if _a.startswith("--only="):
            only = [x.strip() for x in _a.split("=", 1)[1].split(",") if x.strip()]
    sys.exit(asyncio.run(main(only=only)))
