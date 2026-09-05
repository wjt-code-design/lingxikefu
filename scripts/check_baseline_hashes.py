"""BASELINE.sha256 冻结清单完整性校验（B2-6：防评测判定脚本被篡改后 CI 仍绿）。

背景：eval-and-samples/BASELINE.sha256 冻结「评测集三件套 + 判定脚本 eval_faithfulness.py」
四文件哈希（防 metric-gaming）。但此前无任何 CI step / 测试校验该清单——判定脚本被改
后 hash 失配，CI 照样全绿，"冻结"承诺形同虚设（典型假绿防线）。

manifest 路径混用两种基准（历史遗留，本脚本兼容而非改 manifest）：
- `*backend/scripts/eval_faithfulness.py` —— 相对 repo 根；
- `*评测问题库.md` / `*ground-truth.md` / `*口语化评测集.md` —— 相对 eval-and-samples/。
解析策略：每行先按 repo 根解析，文件不存在再按 eval-and-samples/ 解析；都找不到 = 失败。

退出码：0 = 全部匹配；1 = 任一失配 / 文件缺失 / manifest 缺失（fail-closed）。
纯标准库，无第三方依赖。
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "eval-and-samples" / "BASELINE.sha256"
EVAL_DIR = ROOT / "eval-and-samples"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _resolve(rel: str) -> Path | None:
    """双基准解析：先 repo 根，再 eval-and-samples/。返回存在的路径或 None。"""
    for base in (ROOT, EVAL_DIR):
        p = base / rel
        if p.is_file():
            return p
    return None


def main() -> int:
    if not MANIFEST.is_file():
        print(f"[FAIL] manifest 缺失：{MANIFEST}")
        return 1
    failures: list[str] = []
    checked = 0
    for raw in MANIFEST.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # 格式：<hash> <path>，path 前可能有 sha256sum binary 模式标记 '*'
        parts = line.split(None, 1)
        if len(parts) != 2 or len(parts[0]) != 64:
            failures.append(f"无法解析行：{raw!r}")
            continue
        expected, rel = parts[0].lower(), parts[1].lstrip("*").strip()
        path = _resolve(rel)
        if path is None:
            failures.append(f"文件缺失：{rel}")
            continue
        checked += 1
        actual = _sha256(path)
        if actual != expected:
            failures.append(f"哈希失配：{rel}\n    期望 {expected}\n    实际 {actual}")
        else:
            print(f"[OK] {rel}")
    if failures:
        print(f"\n[FAIL] BASELINE 完整性校验未过（{len(failures)} 项）：", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        print(
            "\n若确为有意变更：按 BASELINE.sha256 头部「变更流程」记录理由后重算 hash 补登。",
            file=sys.stderr,
        )
        return 1
    print(f"\nRESULT: PASS（{checked} 个冻结文件哈希全部匹配）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
