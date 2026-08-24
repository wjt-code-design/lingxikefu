"""性能基线提取脚本：从 messages.meta.first_token_ms 统计 P95。

用法：
    python scripts/baseline_perf.py
    python scripts/baseline_perf.py --days 30

输出：
    - 首字时延 P50 / P90 / P95 / P99 / 均值（毫秒）
    - 样本数（含/不含 first_token_ms）
    - 建议门槛（基于 P95 + 10% 余量）

数据源：messages 表 role=assistant 且 meta 含 first_token_ms 字段。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# 允许从 scripts/ 子目录直接运行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 使用 setdefault 避免覆盖容器内真实配置
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")

from app.core.database import SessionLocal
from app.models.message import Message, MessageRole
from sqlalchemy import select

logger = logging.getLogger(__name__)


def percentile(values: list[float], p: float) -> float:
    """计算百分位数（线性插值）。"""
    if not values:
        return 0.0
    sorted_v = sorted(values)
    n = len(sorted_v)
    k = (n - 1) * (p / 100.0)
    f = int(k)
    c = f + 1
    if c >= n:
        return sorted_v[-1]
    d = k - f
    return sorted_v[f] + d * (sorted_v[c] - sorted_v[f])


def extract_perf_baseline(days: int | None = None) -> dict:
    """从 messages 表提取首字时延统计。

    Args:
        days: 统计最近 N 天的数据，None 表示全量

    Returns:
        dict: 包含 P50/P90/P95/P99/均值/样本数等统计信息
    """
    db = SessionLocal()
    try:
        # 构建查询：role=assistant 且 meta 含 first_token_ms
        query = select(Message).where(
            Message.role == MessageRole.assistant,
            Message.meta["first_token_ms"].isnot(None),
        )

        if days is not None:
            cutoff = datetime.now(UTC) - timedelta(days=days)
            query = query.where(Message.created_at >= cutoff)

        rows = db.scalars(query).all()

        # 提取 first_token_ms 值
        latencies: list[float] = []
        for row in rows:
            meta = row.meta or {}
            val = meta.get("first_token_ms")
            if val is not None:
                try:
                    latencies.append(float(val))
                except (ValueError, TypeError):
                    continue

        if not latencies:
            return {
                "error": "no_data",
                "message": "没有找到含 first_token_ms 的消息记录",
                "total_messages": len(rows),
                "valid_samples": 0,
            }

        # 计算统计指标
        stats = {
            "total_messages": len(rows),
            "valid_samples": len(latencies),
            "min_ms": round(min(latencies), 1),
            "max_ms": round(max(latencies), 1),
            "mean_ms": round(sum(latencies) / len(latencies), 1),
            "p50_ms": round(percentile(latencies, 50), 1),
            "p90_ms": round(percentile(latencies, 90), 1),
            "p95_ms": round(percentile(latencies, 95), 1),
            "p99_ms": round(percentile(latencies, 99), 1),
        }

        # 建议门槛：P95 + 10% 余量
        stats["suggested_threshold_ms"] = round(stats["p95_ms"] * 1.1, 1)

        if days is not None:
            stats["days"] = days

        return stats

    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="提取性能基线（首字时延 P95）")
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="统计最近 N 天的数据（默认全量）",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="输出 JSON 格式",
    )
    args = parser.parse_args()

    stats = extract_perf_baseline(days=args.days)

    if args.json:
        print(json.dumps(stats, indent=2, ensure_ascii=False))
    else:
        if "error" in stats:
            print(f"❌ {stats['message']}")
            print(f"   总消息数: {stats['total_messages']}")
            return

        print("=" * 60)
        print("性能基线报告（首字时延）")
        print("=" * 60)
        if "days" in stats:
            print(f"统计范围: 最近 {stats['days']} 天")
        else:
            print("统计范围: 全量数据")
        print(f"总消息数: {stats['total_messages']}")
        print(f"有效样本: {stats['valid_samples']}")
        print("-" * 60)
        print(f"最小值:   {stats['min_ms']:>8.1f} ms")
        print(f"最大值:   {stats['max_ms']:>8.1f} ms")
        print(f"均值:     {stats['mean_ms']:>8.1f} ms")
        print(f"P50:      {stats['p50_ms']:>8.1f} ms")
        print(f"P90:      {stats['p90_ms']:>8.1f} ms")
        print(f"P95:      {stats['p95_ms']:>8.1f} ms  ← 建议门槛基准")
        print(f"P99:      {stats['p99_ms']:>8.1f} ms")
        print("-" * 60)
        print(f"建议门槛: {stats['suggested_threshold_ms']:>8.1f} ms  (P95 + 10%)")
        print("=" * 60)
        print()
        print("验收标准：改造后相对基线劣化 ≤ 10%")
        print(f"  即：改造后 P95 ≤ {stats['suggested_threshold_ms']:.1f} ms")


if __name__ == "__main__":
    main()
