"""eval_faithfulness judge 判定单测（2026-08-27 修 Q082 假阳性后锁定）。

背景：LLM 主动拒答判定曾用裸子串 ("未收录","转人工","没有找到")，把正常回答尾部的
"…可转人工客服" 引导语误判为自拒 → 假"误拒答"。收窄为 _is_llm_refusal：
- 含整段拒答话术（未收录/没有找到/建议转人工/暂未/尚未）且无 [来源N] 引用 → 判自拒；
- 有 [来源 引用的正常作答（哪怕含"转人工"引导语）→ 不判自拒。
"""
from __future__ import annotations

from scripts.eval_faithfulness import _is_llm_refusal, judge_qa


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


# ---------- judge_qa：并列多断言完整性判据（Q056/Q082 漏断言回归，2026-08-27） ----------


def test_judge_qa_all_parallel_claims_pass():
    """并列两分句全答 → 忠实（Q082 期望行为：未实际使用/已安装使用 两条都给）。"""
    claims = ["未实际使用可无理由退货", "已安装使用的仅质量问题可退"]
    ok, why = judge_qa("未实际使用的大家电可无理由退货；已安装使用的仅质量问题可退。", claims)
    assert ok, why


def test_judge_qa_missing_parallel_claim_fails():
    """只答与提问字面最匹配的一句、漏并列兄弟句 → 不忠实（Q082 现实行为）。"""
    claims = ["未实际使用可无理由退货", "已安装使用的仅质量问题可退"]
    ok, _ = judge_qa("已安装使用的空调仅质量问题可退。", claims)
    assert not ok


def test_judge_qa_invoice_parallel_claims():
    """发票并列断言：全答绿，只答"个人抬头"漏"企业专票"红（Q056 期望 vs 现实）。"""
    claims = ["个人抬头仅可开电子普通发票", "企业抬头可开专票"]
    ok, why = judge_qa("个人抬头仅可开电子普通发票；企业抬头可开具增值税专用发票。", claims)
    assert ok, why
    ok2, _ = judge_qa("个人抬头仅可开电子普通发票。", claims)
    assert not ok2
