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

    # 输出（生成阶段在 Chat 层，不落本上下文；errors 供 Runner 失败路径记录）
    errors: list[str] = field(default_factory=list)

    def add_stage(self, name: str, error: str | None = None) -> None:
        self.stages.append(
            {"name": name, "at": datetime.now().isoformat(), "error": error}
        )

    def to_dict(self) -> dict:
        """序列化为日志/排障用字典（不含 chunks 大数组，避免日志膨胀）。"""
        return {
            "query": self.query,
            "intent": self.intent,
            "rewritten_query": self.rewritten_query,
            "from_cache": self.from_cache,
            "refuse": self.refuse,
            "refuse_reason": self.refuse_reason,
            "retrieved_chunks": len(self.chunks),
            "dense_scores": [round(s, 4) for s in self.dense_scores[:5]],
            "stages": self.stages,
            "errors": self.errors,
        }
