"""路由准确率测试脚本：验证 Router 分发规则准确性。

用法：
    python scripts/baseline_route.py
    python scripts/baseline_route.py --golden tests/golden/route_eval_set.txt

输出：
    - 准确率（agents_invoked 与 expected_agents 完全匹配）
    - 失败用例详情
    - 建议：准确率应达 95%+

数据源：tests/golden/route_eval_set.txt
格式：query | expected_agents | expected_intent
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# 允许从 scripts/ 子目录直接运行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 使用 setdefault 避免覆盖容器内真实配置
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")

from app.services.agents.router import Router
from app.services.shared_context import SharedContext

logger = logging.getLogger(__name__)


def parse_golden_line(line: str) -> tuple[str, list[str], str] | None:
    """解析 golden 集的一行。

    Returns:
        (query, expected_agents, expected_intent) 或 None（注释/空行）
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return None

    parts = [p.strip() for p in line.split("|")]
    if len(parts) != 3:
        logger.warning(f"格式错误（跳过）: {line}")
        return None

    query, agents_str, intent = parts
    expected_agents = [a.strip() for a in agents_str.split(",")]

    return query, expected_agents, intent


def test_single_case(
    query: str,
    expected_agents: list[str],
    expected_intent: str,
) -> dict:
    """测试单个用例。

    Args:
        query: 用户问题
        expected_agents: 期望调用的 Agent 列表
        expected_intent: 期望意图

    Returns:
        dict: 包含 pass/fail 状态与详情
    """
    router = Router()
    ctx = SharedContext(query=query)
    router.route(ctx)

    # 检查 agents_invoked
    agents_match = ctx.agents_invoked == expected_agents

    # 检查 intent
    intent_match = ctx.intent == expected_intent

    all_match = agents_match and intent_match

    return {
        "pass": all_match,
        "query": query,
        "expected": {
            "agents": expected_agents,
            "intent": expected_intent,
        },
        "actual": {
            "agents": ctx.agents_invoked,
            "intent": ctx.intent,
        },
        "agents_match": agents_match,
        "intent_match": intent_match,
    }


def run_golden_set(golden_path: Path) -> dict:
    """运行整个 golden 集。

    Returns:
        dict: 包含准确率、失败用例等统计信息
    """
    if not golden_path.exists():
        return {"error": f"文件不存在: {golden_path}"}

    results = []
    total = 0
    passed = 0

    with open(golden_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            parsed = parse_golden_line(line)
            if parsed is None:
                continue

            query, expected_agents, expected_intent = parsed
            result = test_single_case(query, expected_agents, expected_intent)
            result["line_num"] = line_num
            results.append(result)

            total += 1
            if result["pass"]:
                passed += 1

    accuracy = (passed / total * 100) if total > 0 else 0.0

    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "accuracy": round(accuracy, 2),
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="路由准确率测试")
    parser.add_argument(
        "--golden",
        type=Path,
        default=Path("tests/golden/route_eval_set.txt"),
        help="golden 集路径（默认 tests/golden/route_eval_set.txt）",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("路由准确率测试")
    print("=" * 60)
    print(f"Golden 集: {args.golden}")
    print()

    stats = run_golden_set(args.golden)

    if "error" in stats:
        print(f"❌ {stats['error']}")
        return

    print(f"总用例数: {stats['total']}")
    print(f"通过:     {stats['passed']}")
    print(f"失败:     {stats['failed']}")
    print(f"准确率:   {stats['accuracy']:.2f}%")
    print("-" * 60)

    if stats["failed"] > 0:
        print("\n失败用例详情:")
        for r in stats["results"]:
            if not r["pass"]:
                print(f"\n  行 {r['line_num']}: {r['query']}")
                print(f"    期望: {r['expected']}")
                print(f"    实际: {r['actual']}")
                if not r["agents_match"]:
                    print(f"    ❌ agents_invoked 不匹配")
                if not r["intent_match"]:
                    print(f"    ❌ intent 不匹配")
    else:
        print("\n✅ 所有用例通过！")

    print()
    print("=" * 60)
    print("验收标准：准确率应达 95%+")
    if stats["accuracy"] >= 95.0:
        print("✅ 达标")
    else:
        print(f"❌ 未达标（当前 {stats['accuracy']:.2f}%）")
    print("=" * 60)


if __name__ == "__main__":
    main()
