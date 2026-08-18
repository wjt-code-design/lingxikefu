"""生成 OpenAPI schema 到 contracts/api-schema.json —— 契约收敛校验的数据源。

用法（容器内，见项目说明）：
    python scripts/generate_openapi.py --out /tmp/api-schema.json
本地：
    python scripts/generate_openapi.py          # 默认写 ../contracts/api-schema.json

输出 JSON 与 contracts/api.ts 的「接口类型」（A 类）由 scripts/check_contracts.py 比对。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 确保可独立运行（不依赖 cwd 在 backend/ 下）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate OpenAPI schema")
    parser.add_argument("--out", default=None, help="输出路径；默认 ../contracts/api-schema.json")
    args = parser.parse_args()

    if args.out:
        out = Path(args.out)
    else:
        out = Path(__file__).resolve().parents[2] / "contracts" / "api-schema.json"
    out.parent.mkdir(parents=True, exist_ok=True)

    schema = app.openapi()
    out.write_text(json.dumps(schema, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"written {out} ({len(json.dumps(schema))} bytes, {len(schema.get('paths', {}))} paths)")


if __name__ == "__main__":
    main()
