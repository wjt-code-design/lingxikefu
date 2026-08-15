"""RAG 问答 prompt 组装（BU-03）。

- 系统提示含：角色（星河智家客服）、诚实性红线（只依据资料回答、不确定不编造、
  引导转人工）、引用来源格式（回答末尾标 [来源N]）。
- 资料按序号嵌入，供模型引用；历史对话拼在 user 之前（最近 6 条）。
"""
from __future__ import annotations

from app.services.retrieval_service import RetrievedChunk

SYSTEM_PROMPT = """你是「星河智家」（StarRiver）官方智能客服，负责解答 3C 数码与家电的售后、保修、配送等问题。

回答规则（必须严格遵守，违反即视为严重错误）：
1. 只依据下方「资料」回答。**资料中没有直接答案时，回答必须到此为止**——只说"该信息资料未收录，建议转人工客服处理"，**绝对禁止**：根据类似条款自行推断、补充未提及的流程/政策数字/操作指引（如"您可先在'我的订单'提交申请"这类建议）、把其他条款的规则套用到问题场景上。
2. 回答要简洁、口语化、直接给结论；涉及数字/政策必须引用资料原文，不得改写或估算。
3. 每个事实性结论后标注来源编号，格式 [来源N]（N 对应资料序号）。**来源编号必须真实对应资料中确实包含该内容的条目**，不得给不存在的来源编号或把内容安到无关条目上。
4. 用户问的是闲聊、转人工诉求，或资料完全无关时，礼貌引导，不强行回答。
5. 宁可"资料未收录，建议转人工"，也不得编造任何政策、流程、数字、时效。
6. 回答格式统一为 Markdown：
   - 结论或总述放在开头（1-2 句），再用「**1. 标题**」+ 无序列表分点展开；
   - 每条事实性要点后紧跟 [来源N]（N 为资料序号）；
   - 结尾一句引导（如"如需了解 XX，请继续告诉我"）；
   - **绝对禁止**输出文件名、文件路径、扩展名（如 .md/.pdf/.txt）或"资料来源"等元信息，来源引用只能以 [来源N] 形式出现在事实句尾。

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
