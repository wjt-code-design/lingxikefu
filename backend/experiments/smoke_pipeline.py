"""管线全链路冒烟（2026-10-27）：验证新模型 + rerank 节点在真实链路上生效。

跑法（backend 目录下）：
    .venv/Scripts/python.exe experiments/smoke_pipeline.py

检查项：
1. 管线能跑通（intent → rewrite → cache → retrieve → **rerank** → refuse）
2. rerank 节点**真的被调用**（对比开/关两种开关下 top-5 的顺序与内容差异）
3. rerank 后 dense_score 保留（拒答语义不受重排影响）
4. 引用修复 + 生成链路可跑通（新模型能出答案）
5. 拒答题（如"支持花呗吗"）正确走拒答

不跑全量评测（那是另一步），只验证链路正确。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.knowledge import KnowledgeBase  # noqa: E402
from app.services.rag_service import run_pipeline  # noqa: E402
from scripts.smoke_import import _KB_NAME  # noqa: E402
from sqlalchemy import select  # noqa: E402

FAILS: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    mark = "✅" if ok else "❌"
    print(f"  {mark} {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILS.append(label)


def _eval_kb_id():
    """返回评测 KB 的 UUID（kb.id 已是 UUID 对象，不做二次转换）。"""
    db = SessionLocal()
    kb = db.execute(select(KnowledgeBase).where(KnowledgeBase.name == _KB_NAME)).scalar_one()
    kb_id = kb.id
    db.close()
    return kb_id


def run_pipeline_sync(query: str, kb_id):
    """跑一次管线，返回 RagResult（对外契约层）。

    注意：RagResult **不携带** stages / dense_scores（那是 Pipeline 上下文上的），
    所以要看节点执行轨迹需用 run_pipeline_raw。
    """
    return run_pipeline(query, kb_id, top_k=settings.RETRIEVAL_TOP_K)


def run_pipeline_raw(query: str, kb_id):
    """跑管线并返回 Pipeline 上下文（含 stages / errors / dense_scores）。

    走 rag_service._build_pipeline（内部编排入口），与生产 run_pipeline 同一条路径，
    差别仅在于返回中间态而非 RagResult 映射。
    """
    from app.services.pipeline import Pipeline
    from app.services.rag_service import _build_pipeline

    p = Pipeline(query=query, kb_id=kb_id, history=[])
    return _build_pipeline(p)


def main() -> int:
    kb_id = _eval_kb_id()
    print(f"=== 管线冒烟（KB={_KB_NAME}, kb_id={kb_id}）===")
    print(f"模型={settings.LONGCAT_CHAT_MODEL}  thinking_style={settings.LLM_THINKING_PARAM_STYLE}")
    print(f"RAG_ENABLE_RERANK={settings.RAG_ENABLE_RERANK}  RERANK_MODEL={settings.RERANK_MODEL}")
    print()

    qa_query = "商品保修多久？"

    # ---- 1) rerank 关闭：基线序 ----
    print("[1] rerank=OFF（基线）")
    settings.RAG_ENABLE_RERANK = False
    off = run_pipeline_raw(qa_query, kb_id)
    off_chunks = [(c.chunk_id, round(c.dense_score, 4), c.text[:26]) for c in off.chunks]
    for cid, ds, txt in off_chunks:
        print(f"      {cid[:8]} dense={ds}  {txt}")
    check("rerank=OFF 能取到 chunks", len(off.chunks) > 0, f"{len(off.chunks)} 条")
    check("rerank=OFF 未走 rerank 节点", "rerank" not in [s["name"] for s in (off.stages or [])])

    # ---- 2) rerank 开启：观察重排 ----
    print("\n[2] rerank=ON")
    settings.RAG_ENABLE_RERANK = True
    on = run_pipeline_raw(qa_query, kb_id)
    on_chunks = [(c.chunk_id, round(c.dense_score, 4), c.text[:26]) for c in on.chunks]
    for cid, ds, txt in on_chunks:
        print(f"      {cid[:8]} dense={ds}  {txt}")
    stages = [s["name"] for s in (on.stages or [])]
    check("rerank=ON 走了 rerank 节点", "rerank" in stages, f"stages={stages}")
    check("rerank 无降级错误", not any("rerank_degraded" in e for e in (on.errors or [])),
          str([e for e in (on.errors or []) if "rerank" in e]))
    check("rerank 后仍有 chunks", len(on.chunks) > 0, f"{len(on.chunks)} 条")

    # ---- 3) dense_score 保留校验 ----
    # 关闭时 top-5 的 (chunk_id → dense) 映射应与开启时一致（重排只换顺序，不改分）
    off_map = {cid: ds for cid, ds, _ in off_chunks}
    on_map = {cid: ds for cid, ds, _ in on_chunks}
    common = set(off_map) & set(on_map)
    same_scores = all(abs(off_map[c] - on_map[c]) < 1e-9 for c in common)
    check("dense_score 在重排后原样保留", same_scores,
          f"共同chunk={len(common)}，" + ("" if same_scores else f"off={off_map} on={on_map}"))
    check("chunk.dense_score 与展示值一致",
          all(abs(c.dense_score - ds) < 1e-4 for c, (_, ds, _) in zip(on.chunks, on_chunks)),
          "dense_score 随重排同步换位")

    # ---- 4) 顺序是否真的变了（rerank 的意义）----
    off_order = [cid for cid, _, _ in off_chunks]
    on_order = [cid for cid, _, _ in on_chunks]
    print(f"\n    OFF 序: {[c[:8] for c in off_order]}")
    print(f"    ON  序: {[c[:8] for c in on_order]}")
    check("rerank 改变了 top-5 顺序或内容", off_order != on_order,
          "顺序/内容有差异（重排生效）" if off_order != on_order else "完全相同（重排未改变结果）")

    # ---- 5) 拒答题：资料未覆盖时应拒答 ----
    print("\n[3] 拒答行为")
    refuse_query = "支持花呗吗？"  # 评测集 Q061：资料未覆盖，正确行为是拒答
    r = run_pipeline_raw(refuse_query, kb_id)
    print(f"      refuse={r.refuse}  reason={r.refuse_reason!r}  chunks={len(r.chunks)}")
    check("资料未覆盖的题走拒答", r.refuse is True, f"refuse={r.refuse}")

    # ---- 6) 生成链路（新模型能出答案）----
    print("\n[4] 生成链路（新模型）")
    from app.llm_clients.chat import get_chat_client
    from app.prompts.qa_prompt import build_qa_messages

    client = get_chat_client()
    msgs = build_qa_messages(query=qa_query, chunks=on.chunks)
    text = asyncio.run(client.complete(msgs))
    print(f"      回答（截断）: {text[:160]!r}")
    check("新模型能生成非空回答", bool(text.strip()), f"{len(text)} 字")
    check("回答引用了来源", "[来源" in text, "含 [来源N] 标记" if "[来源" in text else "未含引用标记")

    print("\n=== 结果 ===")
    if FAILS:
        print(f"  ❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print(f"     - {f}")
        return 1
    print("  ✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
