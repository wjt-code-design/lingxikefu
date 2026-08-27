"""eval_faithfulness judge 判定单测（2026-08-27 修 Q082 假阳性后锁定）。

背景：LLM 主动拒答判定曾用裸子串 ("未收录","转人工","没有找到")，把正常回答尾部的
"…可转人工客服" 引导语误判为自拒 → 假"误拒答"。收窄为 _is_llm_refusal：
- 含整段拒答话术（未收录/没有找到/建议转人工/暂未/尚未）且无 [来源N] 引用 → 判自拒；
- 有 [来源 引用的正常作答（哪怕含"转人工"引导语）→ 不判自拒。
"""
from __future__ import annotations

from scripts.eval_faithfulness import _is_llm_refusal


def test_true_refusal_detected():
    """真自拒（无引用+拒答话术）仍必须命中。"""
    assert _is_llm_refusal("该信息资料未收录，建议转人工客服处理。")
    assert _is_llm_refusal("抱歉，我暂时没有找到关于这个问题的可靠信息，为避免误导您，建议转人工客服处理。")


def test_cited_answer_not_refusal():
    """有 [来源N] 引用的正常作答不得误判为自拒（Q082 假阳性回归）。"""
    assert not _is_llm_refusal("空调已安装使用（含通电自检）的仅质量问题可退 [来源2]。")
    assert not _is_llm_refusal("支持退款，如需进一步协助可转人工咨询 [来源1]")


def test_bare_refuse_word_not_enough():
    """裸"转人工"出现（可/如需）但非整段拒答话术时不判自拒。"""
    assert not _is_llm_refusal("退款已受理，如需进度查询可转人工客服")
