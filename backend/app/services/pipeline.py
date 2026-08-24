"""RAG 管线上下文：承载所有中间状态 + 阶段日志。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID


@dataclass
class Pipeline:
    """管线上下文：承载所有中间状态 + 阶段日志"""

    # 输入
    query: str
    kb_id: UUID
    user_id: str = ""
    history: list[dict] = field(default_factory=list)
    kb_version: str | None = None

    # 中间状态（各节点写入）
    intent: str = ""
    rewritten_query: str = ""
    chunks: list = field(default_factory=list)
    dense_scores: list[float] = field(default_factory=list)
    refuse: bool = False
    refuse_reason: str = ""
    from_cache: bool = False
    cached_answer: str = ""
    cached_sources: list[dict] = field(default_factory=list)

    # 阶段日志（调试/排障用）
    stages: list[dict] = field(default_factory=list)

    # 输出
    final_answer: str = ""
    sources: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def add_stage(self, name: str, error: str | None = None) -> None:
        self.stages.append(
            {"name": name, "at": datetime.now().isoformat(), "error": error}
        )
