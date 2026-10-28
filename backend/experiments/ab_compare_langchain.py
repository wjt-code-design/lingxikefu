"""LangChain vs 自研 RAG 检索层 · A/B 对比实验（2026-10-27）。

🔑 核心设计：**不重写判定逻辑**。
冻结的 ``scripts/eval_faithfulness.py`` 在 BASELINE.sha256 内、CI 有 hash 校验，
改它就等于毁掉基线可信度。故本脚本用**注入**方式切换检索层：

    patch app.services.steps.retrieve.search_kb → 各臂实现

然后直接调用冻结脚本的 ``run_faithfulness_eval`` 复用入口，**判定脚本零改动**，
输出 JSON 的 ``script_sha256_12`` 仍是冻结版 hash → 结果与历史存档直接可比。

四个臂（对照设计）：
  A0 自研 + 加权 RRF 2:1        ← 生产基线
  A1 自研 + 等权 RRF 1:1        ← 消融：隔离「权重」这一个变量
  B0 LangChain 等权 RRF         ← 框架原生默认
  B1 LangChain 加权 RRF 2:1     ← 框架 + 自研权重（隔离「融合权重」）

⚠️ 混淆因子（结论须表述为**系统级对比**，不可宣称单变量因果）：
  A* 与 B* 的 sparse 算法不同——自研 = 字符级 2-gram + crc32 哈希 + 词频 tf（无 IDF）；
  LangChain BM25 = jieba 分词 + 真 BM25（含 IDF）。
  A0 vs A1 与 B0 vs B1 才是**单变量**（仅融合权重不同）。

跑法（backend 目录下）：
    HF_HUB_OFFLINE=1 .venv/Scripts/python.exe experiments/ab_compare_langchain.py --sample 20
    HF_HUB_OFFLINE=1 .venv/Scripts/python.exe experiments/ab_compare_langchain.py   # 全量
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

# ⚠️ HF 离线必须在任何 import 之前设置——否则 langchain 导入链会先触发
# huggingface_hub 的联网探测（重试 5 次 × 多个文件，浪费分钟级时间）。
os.environ.setdefault("HF_HUB_OFFLINE", "1")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.knowledge import KnowledgeBase  # noqa: E402
from sqlalchemy import select  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent


def _resolve_kb(db, kb_name: str):
    kb = db.execute(select(KnowledgeBase).where(KnowledgeBase.name == kb_name)).scalar_one_or_none()
    if kb is None:
        raise SystemExit(f"找不到 KB {kb_name!r}")
    return kb


# ---------------- 各臂实现 ----------------

def arm_native_weighted(query: str, kb_id, top_k: int = 8):
    """A0：自研 + 加权 RRF 2:1（生产基线，原样调用）。

    ⚠️ 必须调用**模块导入时捕获的原始函数**，不能写
    ``from app.services.retrieval_service import search_kb``（那是函数体内 import，
    会取到 patch 后的值 → 自我递归 → RecursionError）。
    """

    return _ORIGINAL_SEARCH_KB(query, kb_id, top_k=top_k)


def arm_native_equal(query: str, kb_id, top_k: int = 8):
    """A1：自研 + 等权 RRF 1:1（消融臂：只改权重这一个变量）。"""
    from app.services import retrieval_service as rs

    orig = rs._rrf_fuse

    def _equal(dense_hits, sparse_hits, top_k_, **kw):
        return orig(dense_hits, sparse_hits, top_k_, w_dense=1.0, w_sparse=1.0)

    rs._rrf_fuse = _equal
    try:
        # 用原始函数（否则会取到 patch 后的自身 → 递归）
        return _ORIGINAL_SEARCH_KB(query, kb_id, top_k=top_k)
    finally:
        rs._rrf_fuse = orig


def arm_langchain_equal(query: str, kb_id, top_k: int = 8):
    """B0：LangChain 原生默认（等权 RRF，c=60）。"""
    return _langchain_search_kb(query, kb_id, top_k=top_k, weights=None)


def arm_langchain_weighted(query: str, kb_id, top_k: int = 8):
    """B1：LangChain + 加权 RRF 2:1（与自研同权重，隔离权重变量）。"""
    return _langchain_search_kb(query, kb_id, top_k=top_k, weights=[2.0, 1.0])


# 🔑 在任何 patch 之前捕获**原始** search_kb。
#    各臂若写 `from app.services.retrieval_service import search_kb`（函数体内 import），
#    会取到 patch 后的值 → 自我递归 → RecursionError（实测踩到）。
from app.services.retrieval_service import search_kb as _ORIGINAL_SEARCH_KB  # noqa: E402

# ⚠️ langchain_retrieval 必须在**模块顶层** import（任何 patch 之前）。
#   它内部会模块级捕获原始 search_kb；若在函数体内 import（patch 之后才执行），
#   捕获到的就是被 patch 的函数 → 自我递归 → RecursionError（实测踩到）。
from experiments.langchain_retrieval import langchain_search_kb as _langchain_search_kb  # noqa: E402

ARMS = {
    "A0_native_weighted": ("自研 + 加权RRF 2:1（生产基线）", arm_native_weighted),
    "A1_native_equal": ("自研 + 等权RRF 1:1（消融）", arm_native_equal),
    "B0_langchain_equal": ("LangChain 等权RRF（框架默认）", arm_langchain_equal),
    "B1_langchain_weighted": ("LangChain 加权RRF 2:1", arm_langchain_weighted),
}


async def run_arm(arm_key: str, sample: int, kb_name: str, out_path: Path) -> dict:
    """在指定臂下跑**冻结脚本的核心评测循环**（判定逻辑零改动）。

    🔑 为什么不用 CLI：CLI 会重新解析 argv、且每次跑都新建 db session；
    这里直接调用冻结脚本的内部核心 ``_run_faithfulness`` + ``_write_report``
    ——它们是 BASELINE.sha256 冻结的同一份代码，判定逻辑与生产/CI 完全一致，
    且输出 JSON 的 ``script_sha256_12`` 仍是冻结版 hash（与历史存档可比）。
    """
    from scripts.eval_faithfulness import (
        _run_faithfulness,
        _write_report,
        parse_ground_truth,
        parse_questions,
    )

    label, fn = ARMS[arm_key]

    # 🔑 注入点：patch **源模块** app.services.retrieval_service.search_kb。
    #   实测发现：steps/retrieve.py 里是
    #       `from app.services.retrieval_service import search_kb` 写在**函数体内**，
    #   所以 steps.retrieve 模块上**没有** search_kb 属性（patch 它会 AttributeError），
    #   而函数体内的 import 每次调用都会去源模块取**当时**的值 →
    #   故必须 patch 源模块的 search_kb。
    from app.services import retrieval_service as rs

    orig = rs.search_kb
    rs.search_kb = fn
    try:
        db = SessionLocal()
        kb = _resolve_kb(db, kb_name)

        # 抽样公式与 CLI --sample 完全一致（step=max(1, len//sample)）
        questions = parse_questions()
        gt = parse_ground_truth()
        if sample:
            step = max(1, len(questions) // sample)
            questions = questions[::step][:sample]

        results: list[dict] = []
        t0 = time.time()
        # ⚠️ 必须在**同一个 event loop** 内跑所有臂：chat client 是模块级共享的
        #   httpx.AsyncClient，其连接池绑定创建时的 loop。每臂各起一个
        #   asyncio.run() → 后续臂复用旧 loop 的连接池 → "Event loop is closed"
        #   （与 eval_runner.py ADR-1 记载的 worker own_client 教训同源）。
        stats, fails, cit_good, cit_total = await _run_faithfulness(
            db, kb.id, questions, gt, results
        )
        elapsed = time.time() - t0

        # 用冻结脚本的落盘函数，meta.script_sha256_12 保持冻结版
        meta_extra = {"kb_name": kb_name, "sample": sample, "limit": 0, "offset": 0}
        _write_report(str(out_path), meta_extra, stats, results, cit_good=cit_good, cit_total=cit_total)
        db.close()
    finally:
        rs.search_kb = orig

    payload = {
        "label": label,
        "elapsed": elapsed,
        "stats": stats,
        "fails": fails,
        "citation": [cit_total, cit_good],
        "results": results,
        "out": str(out_path),
    }
    return payload


def summarize(arm_key: str, payload: dict) -> dict:
    """从评测输出里抽指标。"""
    stats = payload["stats"]
    cit = payload["citation"]

    def rate(pair):
        total, ok = pair[0], pair[1]
        return (ok / total if total else None), total, ok

    qa_rate, qa_total, qa_ok = rate(stats.get("qa", [0, 0]))
    rf_rate, rf_total, rf_ok = rate(stats.get("refuse", [0, 0]))
    cit_total, cit_good = cit[0], cit[1]
    return {
        "arm": arm_key,
        "label": payload["label"],
        "elapsed_sec": round(payload["elapsed"], 1),
        "qa": f"{qa_ok}/{qa_total}" + (f" = {qa_rate:.1%}" if qa_rate is not None else ""),
        "qa_rate": qa_rate,
        "refuse": f"{rf_ok}/{rf_total}" + (f" = {rf_rate:.1%}" if rf_rate is not None else ""),
        "citation": f"{cit_good}/{cit_total}" + (f" = {cit_good / cit_total:.1%}" if cit_total else ""),
        "citation_rate": (cit_good / cit_total) if cit_total else None,
        "errors": sum(1 for r in payload["results"] if r.get("kind") == "error"),
        "refuse_qa": f"{stats.get('refuse_qa', [0, 1])[0]}/{stats.get('refuse_qa', [0, 1])[1]}",
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=20, help="抽样题数（0=全量）")
    ap.add_argument("--kb-name", default="星河智家·官方政策库")
    ap.add_argument("--arms", default=",".join(ARMS), help="要跑的臂，逗号分隔")
    args = ap.parse_args()

    print("=== LangChain vs 自研 · A/B 对比 ===")
    print(f"KB={args.kb_name}  sample={args.sample or '全量'}  模型={settings.LONGCAT_CHAT_MODEL}")
    print(f"rerank={settings.RAG_ENABLE_RERANK}  top_k={settings.RETRIEVAL_TOP_K}")
    print()

    rows = []
    for arm_key in args.arms.split(","):
        arm_key = arm_key.strip()
        if arm_key not in ARMS:
            print(f"  ⚠️ 未知臂 {arm_key}，跳过")
            continue
        print(f"--- [{arm_key}] {ARMS[arm_key][0]} ---")
        out = OUT_DIR / f"ab_{arm_key}{'_sample' + str(args.sample) if args.sample else ''}.json"
        payload = await run_arm(arm_key, args.sample, args.kb_name, out)
        row = summarize(arm_key, payload)
        rows.append(row)
        print(f"    qa={row['qa']}  refuse={row['refuse']}  citation={row['citation']}"
              f"  refuse_qa={row['refuse_qa']}  errors={row['errors']}  耗时={row['elapsed_sec']}s")
        print(f"    → {out.name}")
        print()

    print("=== 汇总对比 ===")
    print(f"{'臂':<26} {'qa':<16} {'refuse':<16} {'citation':<16} {'errors':>6} {'耗时':>8}")
    print("-" * 92)
    for r in rows:
        print(f"{r['label']:<26} {r['qa']:<16} {r['refuse']:<16} {r['citation']:<16} "
              f"{r['errors']:>6} {r['elapsed_sec']:>7}s")

    summary_path = OUT_DIR / f"ab_summary{'_sample' + str(args.sample) if args.sample else ''}.json"
    summary_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n汇总已存: {summary_path.name}")
    return 0


if __name__ == "__main__":
    # 全部臂在同一个 event loop 内跑（见 run_arm 的说明）
    sys.exit(asyncio.run(main()))
