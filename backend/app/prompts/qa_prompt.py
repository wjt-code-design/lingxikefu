"""RAG 问答 prompt 组装（BU-03）。

- 系统提示含：角色（星河智家客服）、诚实性红线（只依据资料回答、不确定不编造、
  引导转人工）、引用来源格式（回答末尾标 [来源N]）。
- 资料按序号嵌入，供模型引用；历史对话拼在 user 之前（最近 6 条）。
"""
from __future__ import annotations

from app.services.retrieval_service import RetrievedChunk

SYSTEM_PROMPT = """你是「星河智家」（StarRiver）官方智能客服，负责解答 3C 数码与家电的售后、保修、配送等问题。

回答规则（必须严格遵守）：
1. 只依据下方「资料」回答，资料中没有的信息一律不得编造；不确定就明确说"建议转人工客服处理"。
2. 回答要简洁、口语化、直接给结论；涉及数字/政策引用资料原文。
3. 每个事实性结论后标注来源编号，格式 [来源N]（N 对应资料序号）。
4. 用户问的是闲聊、转人工诉求，或资料完全无关时，礼貌引导，不强行回答。

=== 资料 ===
{context}

=== 历史对话 ===
{history}

请回答用户当前问题（结尾保留来源标注）。"""


def build_qa_messages(
    query: str,
    chunks: list[RetrievedChunk],
    history: list[dict] | None = None,
) -> list[dict]:
    """组装 chat messages：system + 历史 + user。history 为 [{"role","content"}]。"""
    context = "\n\n".join(f"[来源{i + 1}] {c.text}" for i, c in enumerate(chunks))

    hist_lines = []
    for m in (history or [])[-6:]:
        role = "用户" if m.get("role") == "user" else "客服"
        hist_lines.append(f"{role}: {m.get('content', '')}")
    history_text = "\n".join(hist_lines) or "（无）"

    return [
        {"role": "system", "content": SYSTEM_PROMPT.format(context=context, history=history_text)},
        {"role": "user", "content": query},
    ]
