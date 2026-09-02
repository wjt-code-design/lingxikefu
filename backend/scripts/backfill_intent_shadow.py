"""意图影子样本离线回填（意图切换数据依赖加速，批次 I / handoff §六待办#2）。

背景：影子采样（INTENT_SHADOW_SAMPLE=0.2）靠真实流量积累极慢——本地 18 天仅
攒 7 条，距 500 门槛遥不可及。本脚本把历史 ``rule=qa`` 且无影子标记的用户消息
补跑 LLM 影子分类（复用 intent_shadow.shadow_classify 原子操作：fail-open、
独立短会话、meta 合并不覆盖），使 agree_rate 与样本量评估成为可能。

边界（防幻觉/数据诚实）：
- **只回填真实历史流量**，绝不合成/改写样本——影子数据的意义就是真实分布；
- 只碰 ``intent='qa'`` 的 user 消息（与在线采样门同口径），handoff/chitchat/
  refuse 不碰；已有 ``meta["intent_shadow"]`` 的不重复（幂等，可重跑）；
- 单条失败（LLM 异常/输出不可解析）计入 failed 继续下一条，不中断；
- 全量回填 = 100% 采样历史消息，分布与在线采样一致（同一流量来源）。

用法（cwd=backend）::

    python -m scripts.backfill_intent_shadow [--limit N] [--dry-run] [--sleep 0.8]

LLM 走生产同款 LongCat chat client（362 条 × ~2s ≈ 20 分钟）；429 防护靠条间
sleep。回填完成后用 ``GET /admin/intent-shadow/stats`` 看真实 agree_rate。
"""
from __future__ import annotations

import argparse
import time
from typing import Any

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.message import Message, MessageRole
from app.services.intent_shadow import shadow_classify
from sqlalchemy import select


def _candidates_query():
    llm_col = Message.meta["intent_shadow"]["intent"].as_string()
    return (
        select(Message)
        .where(
            Message.tenant_id == settings.TENANT_DEFAULT,
            Message.role == MessageRole.user,
            Message.intent == "qa",  # 与在线采样门同口径：只影子 qa 类
            llm_col.is_(None),  # 无影子标记 = 待回填（幂等）
        )
        .order_by(Message.created_at.asc())
    )


def run_backfill(
    *,
    session_factory: Any = None,
    client: Any = None,
    limit: int = 0,
    dry_run: bool = False,
    sleep_s: float = 0.8,
) -> dict:
    """回填主流程（核心函数，测试注入 factory/client）。

    返回 ``{"candidates": 待回填数, "backfilled": 成功数, "failed": 失败数}``。
    """
    factory = session_factory or SessionLocal
    with factory() as db:
        q = _candidates_query()
        if limit > 0:
            q = q.limit(limit)
        rows = db.execute(q).scalars().all()
    out = {"candidates": len(rows), "backfilled": 0, "failed": 0}
    if dry_run:
        return out
    for i, row in enumerate(rows):
        intent = shadow_classify(
            row.id, row.content or "", trace_id="backfill",
            session_factory=factory, client=client,
        )
        if intent is None:
            out["failed"] += 1
        else:
            out["backfilled"] += 1
        print(f"[BACKFILL] {i + 1}/{len(rows)} mid={row.id} intent={intent or 'FAILED'}")
        if sleep_s > 0 and i < len(rows) - 1:
            time.sleep(sleep_s)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="意图影子样本离线回填")
    parser.add_argument("--limit", type=int, default=0, help="最多回填 N 条（0=全部）")
    parser.add_argument("--dry-run", action="store_true", help="只统计候选数，不调 LLM 不写库")
    parser.add_argument("--sleep", type=float, default=0.8, help="条间间隔秒（429 防护）")
    args = parser.parse_args(argv)

    out = run_backfill(limit=args.limit, dry_run=args.dry_run, sleep_s=args.sleep)
    tag = "DRY-RUN" if args.dry_run else "DONE"
    print(f"[{tag}] 候选 {out['candidates']} 条，回填成功 {out['backfilled']}，失败 {out['failed']}")
    if not args.dry_run and out["candidates"]:
        print("[NEXT] GET /api/v1/admin/intent-shadow/stats 查看真实 agree_rate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
