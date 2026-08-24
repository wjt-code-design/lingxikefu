"""LLM 生成节点：流式输出。"""
from __future__ import annotations

from app.services.pipeline import Pipeline


async def generate_answer(pipeline: Pipeline) -> Pipeline:
    """LLM 流式生成"""
    from app.prompts.qa_prompt import build_qa_messages
    from app.llm_clients.chat import get_chat_client

    messages = build_qa_messages(
        query=pipeline.query,
        chunks=pipeline.chunks,
        history=pipeline.history,
    )
    client = get_chat_client()
    answer_parts = []
    async for delta in client.stream(messages):
        answer_parts.append(delta)
    pipeline.final_answer = "".join(answer_parts)
    pipeline.add_stage("generate")
    return pipeline
