"""评测执行统一入口（B1-2）：把 faithfulness / recall 评测从 API 进程事件循环剥离。

背景：两评测内部各有同步阻塞段（faithfulness 的 run_pipeline = 本地 embedding +
Qdrant 同步 HTTP），此前 faithfulness 直接 await 在主 loop——每次评测/发布快检
期间，全站 SSE 流式问答被逐题掐停。

方案（ADR-1）：子进程隔离。
- 不用「线程 + asyncio.run」：评测链复用模块级共享 httpx client / 单例，新 loop
  复用绑死旧 loop 的连接池（worker own_client 跨 loop 教训同源）；
- 子进程自带独立 loop 与连接池，主进程事件循环零占用；
- 冻结脚本零改动（BASELINE.sha256 含 eval_faithfulness.py）：仅以 import 方式在
  子进程调用其复用入口 run_faithfulness_eval / run_recall_eval，脚本本体一行不改；
- kb_id/sample 等入口参数经包装源码透传（CLI 无 --kb-id，快检的按 id 精确绑定
  必须走函数入口）。

契约：run_eval_stage(stage, ...) -> list[(metric, score, total, passed)]，与两脚本
复用入口返回形态一致；失败抛 EvalStageError（调用方落 FAILED 留痕，语义与旧
except 分支对齐）。输出协议：子进程 stdout 末行单行 JSON list。
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

#: 子进程墙钟超时（秒）：全量 faithfulness ~20min 的 2 倍冗余；超时按失败处理
#: （发布批次侧另有 40min 孤儿对账兜底，eval 侧 FAILED 留痕）。
_STAGE_TIMEOUT_SEC = 2400

#: faithfulness 包装：asyncio.run 冻结复用入口（sample/kb_id 参数为门禁 v2 G2 既有）
_FAITHFULNESS_WRAPPER = """
import asyncio, json
from app.core.database import SessionLocal
from scripts.eval_faithfulness import run_faithfulness_eval
from uuid import UUID

_kb_id = {kb_id!r}
db = SessionLocal()
try:
    rows = asyncio.run(run_faithfulness_eval(
        db, limit={limit!r}, kb_name={kb_name!r}, sample={sample!r},
        kb_id=(UUID(_kb_id) if _kb_id else None),
    ))
finally:
    db.close()
print(json.dumps([list(r) for r in rows]))
"""

#: recall 包装：同步冻结复用入口（top_k 单一真源由调用方传入）
_RECALL_WRAPPER = """
import json
from app.core.database import SessionLocal
from scripts.eval_recall import run_recall_eval

db = SessionLocal()
try:
    rows = run_recall_eval(db, limit={limit!r}, kb_name={kb_name!r}, top_k={top_k!r})
finally:
    db.close()
print(json.dumps([list(r) for r in rows]))
"""


class EvalStageError(RuntimeError):
    """评测子进程失败（非零退出 / 超时 / 输出不可解析）。"""


async def _spawn_python_c(code: str) -> list[tuple[str, float, int, int]]:
    """backend 根目录下跑 `python -c <code>`，解析 stdout 末行 JSON 为指标元组列表。"""
    backend_root = Path(__file__).resolve().parents[2]
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        code,
        cwd=str(backend_root),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out_b, err_b = await asyncio.wait_for(
            proc.communicate(), timeout=_STAGE_TIMEOUT_SEC
        )
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise EvalStageError(f"评测子进程超时（>{_STAGE_TIMEOUT_SEC}s），已终止") from None
    stdout = out_b.decode("utf-8", errors="replace")
    stderr = err_b.decode("utf-8", errors="replace")
    if stderr.strip():
        logger.info("eval subprocess stderr tail: %s", stderr[-2000:])
    rc = proc.returncode if proc.returncode is not None else -1
    if rc != 0:
        raise EvalStageError(f"评测子进程退出码 {rc}；stderr tail: {stderr[-500:]}")
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    if not lines:
        raise EvalStageError("评测子进程无输出")
    try:
        data = json.loads(lines[-1])
        return [(str(m), float(s), int(t), int(p)) for m, s, t, p in data]
    except Exception as e:  # noqa: BLE001 - 末行非 JSON：子进程 print 污染或崩溃前残输出
        raise EvalStageError(f"评测输出不可解析: {e}；tail: {stdout[-500:]}") from e


async def run_eval_stage(
    stage: str,
    *,
    limit: int = 0,
    sample: int = 0,
    top_k: int = 5,
    kb_name: str | None = None,
    kb_id: str | None = None,
) -> list[tuple[str, float, int, int]]:
    """在隔离子进程跑一个评测阶段，返回指标元组列表（形态同脚本复用入口）。

    失败（非零退出/超时/不可解析）抛 EvalStageError——调用方按 FAILED 留痕。
    """
    if stage == "faithfulness":
        code = _FAITHFULNESS_WRAPPER.format(
            limit=limit, kb_name=kb_name, sample=sample, kb_id=kb_id
        )
    elif stage == "recall":
        code = _RECALL_WRAPPER.format(limit=limit, kb_name=kb_name, top_k=top_k)
    else:
        raise ValueError(f"未知评测阶段: {stage!r}")
    return await _spawn_python_c(code)
