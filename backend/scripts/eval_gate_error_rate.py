"""eval 结果 error 率门禁（防分母缩水假绿）。

背景（BASELINE.md §四 2026-08-30 登记）：LongCat 402 欠费期部分题 error 后不进
faithfulness 分母——20 题抽样若 15 题 402，余 5 题全过即 100% 假绿。本门禁在 CI
层独立判定：解析 eval_faithfulness --out 导出的 JSON 逐题明细，error 题占比
>20% 直接 FAIL（eval_faithfulness 自身 FAIL 时 CI 已红，此处防的是缩分母后 PASS）。

刻意不改 eval_faithfulness.py：判定脚本 hash 在 BASELINE.sha256 冻结清单内，
门禁逻辑放独立脚本（本文件不在冻结清单），既有判定语义零变化。

fail-closed：JSON 不可解析 / results 键缺失或非数组 / 条目缺 kind 字段 → 一律
FAIL（防上游字段改名后门禁被静默跳过）。skip（无 ground-truth）是预期剔除，
不算 error 但计入分母——skip 异常暴增同样触发上限，方向安全。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# error 占比上限（BASELINE.md §四 建议值）：20 题抽样下 error >4 题即 FAIL
MAX_ERROR_RATE = 0.20


def gate(results: list[dict], max_error_rate: float = MAX_ERROR_RATE) -> tuple[bool, str]:
    """判定 error 率。返回 (pass, 理由)。results 为 eval_faithfulness 逐题明细。"""
    if not results:
        return False, "results 为空（评测未产出任何明细）——fail-closed"
    errors = skips = malformed = 0
    for r in results:
        if not isinstance(r, dict) or "kind" not in r:
            malformed += 1  # 形态异常按 error 计（防字段改名静默放行）
        elif r["kind"] == "error":
            errors += 1
        elif r["kind"] == "skip":
            skips += 1
    total = len(results)
    bad = errors + malformed
    rate = bad / total
    ok = rate <= max_error_rate
    note = f"error {bad}/{total} = {rate:.1%}（上限 {max_error_rate:.0%}），skip {skips} 题"
    return ok, note


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    path = argv[0] if argv else ""
    max_rate = MAX_ERROR_RATE
    if "--max-error-rate" in argv:
        i = argv.index("--max-error-rate")
        max_rate = float(argv[i + 1])
    if not path:
        print("[GATE] 未提供 eval JSON 路径——fail-closed")
        return 1
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        results = data["results"]
        if not isinstance(results, list):
            raise TypeError("results 非数组")
    except Exception as e:  # noqa: BLE001
        print(f"[GATE] eval JSON 不可解析（{type(e).__name__}: {e}）——fail-closed")
        return 1
    ok, note = gate(results, max_rate)
    print(f"[GATE] {'PASS ✅' if ok else 'FAIL ❌'}：{note}")
    if not ok:
        print("[GATE] 分母缩水防护：error 占比超上限，本轮评测指标不可信（BASELINE.md §四）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
