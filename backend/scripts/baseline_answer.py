"""答案回归测试脚本：验证 Router 改造前后答案一致性。

用法：
    python scripts/baseline_answer.py
    python scripts/baseline_answer.py --golden tests/golden/answer_golden_set.txt

输出：
    - 通过率（intent / refuse / response_type 三项全匹配）
    - 失败用例详情
    - 建议：通过率应达 100%

数据源：tests/golden/answer_golden_set.txt
格式：query | expected_intent | expected_refuse | expected_response_type
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from uuid import uuid4

# 允许从 scripts/ 子目录直接运行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 使用 setdefault 避免覆盖容器内真实配置
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")

from app.services.rag_service import RagResult, _no_llm_reply, classify_intent, run_pipeline
from app.services.retrieval_service import RetrievedChunk

logger = logging.getLogger(__name__)


def make_chunk(score: float, text: str = "保修条款内容", doc_id: str = "d1") -> RetrievedChunk:
    """构造测试用 chunk。"""
    return RetrievedChunk(
        chunk_id="c1",
        doc_id=doc_id,
        kb_id="kb1",
        idx=0,
        text=text,
        score=score,
        dense_score=score,
    )


def parse_golden_line(line: str) -> tuple[str, str, bool, str] | None:
    """解析 golden 集的一行。

    Returns:
        (query, expected_intent, expected_refuse, expected_response_type) 或 None（注释/空行）
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return None

    parts = [p.strip() for p in line.split("|")]
    if len(parts) != 4:
        logger.warning(f"格式错误（跳过）: {line}")
        return None

    query, intent, refuse_str, resp_type = parts
    refuse = refuse_str.lower() == "true"

    return query, intent, refuse, resp_type


def test_single_case(
    query: str,
    expected_intent: str,
    expected_refuse: bool,
    expected_resp_type: str,
    mock_score: float = 0.9,
) -> dict:
    """测试单个用例。

    Args:
        query: 用户问题
        expected_intent: 期望意图
        expected_refuse: 期望是否拒答
        expected_resp_type: 期望响应类型（llm / fixed_*）
        mock_score: mock 检索分数（用于控制 refuse 行为）

    Returns:
        dict: 包含 pass/fail 状态与详情
    """
    # Mock 检索（固定分数，避免真实 Qdrant 依赖）
    import unittest.mock as mock

    def fake_search(q, kb, top_k=5):
        return [make_chunk(mock_score)]

    with mock.patch("app.services.retrieval_service.search_kb", side_effect=fake_search):
        # 运行管线
        try:
            result = run_pipeline(query, uuid4())
        except Exception as e:
            return {
                "pass": False,
                "error": f"管线异常: {e}",
                "query": query,
            }

        # 检查 intent
        intent_match = result.intent == expected_intent

        # 检查 refuse
        refuse_match = result.refuse == expected_refuse

        # 检查 response_type
        if expected_resp_type == "llm":
            # 应调用 LLM（非 refuse 且非 handoff/chitchat）
            resp_type_match = not result.refuse and result.intent == "qa"
        elif expected_resp_type == "fixed_handoff":
            resp_type_match = result.intent == "handoff"
        elif expected_resp_type == "fixed_chitchat":
            resp_type_match = result.intent == "chitchat"
        elif expected_resp_type == "fixed_refuse":
            resp_type_match = result.refuse
        else:
            resp_type_match = False

        all_match = intent_match and refuse_match and resp_type_match

        return {
            "pass": all_match,
            "query": query,
            "expected": {
                "intent": expected_intent,
                "refuse": expected_refuse,
                "response_type": expected_resp_type,
            },
            "actual": {
                "intent": result.intent,
                "refuse": result.refuse,
                "response_type": (
                    "llm" if not result.refuse and result.intent == "qa"
                    else "fixed_handoff" if result.intent == "handoff"
                    else "fixed_chitchat" if result.intent == "chitchat"
                    else "fixed_refuse" if result.refuse
                    else "unknown"
                ),
            },
            "intent_match": intent_match,
            "refuse_match": refuse_match,
            "resp_type_match": resp_type_match,
        }


def run_golden_set(golden_path: Path) -> dict:
    """运行整个 golden 集。

    Returns:
        dict: 包含通过率、失败用例等统计信息
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

            query, expected_intent, expected_refuse, expected_resp_type = parsed

            # 拒答用例用低分 mock（触发 refuse）
            mock_score = 0.1 if expected_refuse else 0.9

            result = test_single_case(
                query,
                expected_intent,
                expected_refuse,
                expected_resp_type,
                mock_score=mock_score,
            )
            result["line_num"] = line_num
            results.append(result)

            total += 1
            if result["pass"]:
                passed += 1

    pass_rate = (passed / total * 100) if total > 0 else 0.0

    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(pass_rate, 2),
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="答案回归测试")
    parser.add_argument(
        "--golden",
        type=Path,
        default=Path("tests/golden/answer_golden_set.txt"),
        help="golden 集路径（默认 tests/golden/answer_golden_set.txt）",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("答案回归测试")
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
    print(f"通过率:   {stats['pass_rate']:.2f}%")
    print("-" * 60)

    if stats["failed"] > 0:
        print("\n失败用例详情:")
        for r in stats["results"]:
            if not r["pass"]:
                print(f"\n  行 {r['line_num']}: {r['query']}")
                print(f"    期望: {r['expected']}")
                print(f"    实际: {r['actual']}")
                if not r["intent_match"]:
                    print(f"    ❌ intent 不匹配")
                if not r["refuse_match"]:
                    print(f"    ❌ refuse 不匹配")
                if not r["resp_type_match"]:
                    print(f"    ❌ response_type 不匹配")
    else:
        print("\n✅ 所有用例通过！")

    print()
    print("=" * 60)
    print("验收标准：通过率应达 100%")
    if stats["pass_rate"] >= 100.0:
        print("✅ 达标")
    else:
        print(f"❌ 未达标（当前 {stats['pass_rate']:.2f}%）")
    print("=" * 60)


if __name__ == "__main__":
    main()
