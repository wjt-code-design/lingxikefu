"""eval_faithfulness judge 判定单测（2026-08-27 修 Q082 假阳性后锁定）。

背景：LLM 主动拒答判定曾用裸子串 ("未收录","转人工","没有找到")，把正常回答尾部的
"…可转人工客服" 引导语误判为自拒 → 假"误拒答"。收窄为 _is_llm_refusal：
- 含整段拒答话术（未收录/没有找到/建议转人工/暂未/尚未）且无 [来源N] 引用 → 判自拒；
- 有 [来源 引用的正常作答（哪怕含"转人工"引导语）→ 不判自拒。
"""
from __future__ import annotations

from scripts.eval_faithfulness import _is_llm_refusal, judge_citations, judge_qa, judge_refuse


class _Chunk:
    """最小 chunk 桩：judge_citations 只读 .text。"""

    def __init__(self, text: str) -> None:
        self.text = text


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


def test_negation_answer_counts_as_refuse():
    """否定式如实回答（不含拒答话术）也算拒答正确（Q061 期望：如实告知不支持）。"""
    ok, _ = judge_refuse("目前不支持花呗或分期付款。")
    assert ok
    ok2, _ = judge_refuse("该功能暂未开通，建议转人工客服处理。")
    assert ok2


def test_refuse_with_bare_number_not_fabrication():
    """拒答话术后跟序号/裸数字（如"1."）不算编造数字政策（Q071 回归）。"""
    ok, _ = judge_refuse("该信息资料未收录，建议转人工客服处理。如需了解相关权利请继续告诉我。")
    assert ok


def test_refuse_with_policy_number_is_fabrication():
    """拒答话术内包含带单位数字（"7 天可退"）判定为编造。"""
    ok, why = judge_refuse("该信息资料未收录，建议转人工客服处理。目前支持 7 天无理由退货。")
    assert not ok
    assert "编造" in why


def test_judge_citations_counts_all_cited_points():
    """引用统计分母 = 答案中全部 [来源N] 点数（2026-08-27 修统计偏置回归）。

    此前 eval_one 只在整题全合法时才返回 cit，导致合法题不进分母，
    引用合法率被系统性低估（48.3% 实为失败题内部合法率）。本测试锁定
    judge_citations 的 count 是全量计数语义：合法与非法点都计入 total。
    """
    chunks = [
        _Chunk("未实际使用的大家电可无理由退货；已安装使用的仅质量问题可退。"),
        _Chunk("个人抬头仅可开电子普通发票；企业抬头可开具增值税专用发票。"),
    ]
    # 2 个引用点：来源1合法、来源2非法（句子与 chunk2 无关）
    answer = "未实际使用的大家电可无理由退货 [来源1]。最新科技曲线 [来源2]。"
    all_ok, good, total = judge_citations(answer, chunks)
    assert total == 2
    assert good == 1
    assert all_ok is False


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


# ---------- --out 导出与逐引用点明细（2026-08-28 RAG 质量深化 Task 1） ----------


def test_judge_citations_detail_records_each_point():
    """detail 参数逐引用点记录（n/句子/重叠率/判定），供 --out 导出与归因。"""
    chunks = [
        _Chunk("未实际使用的大家电可无理由退货；已安装使用的仅质量问题可退。"),
        _Chunk("个人抬头仅可开电子普通发票；企业抬头可开具增值税专用发票。"),
    ]
    answer = "未实际使用的大家电可无理由退货 [来源1]。最新科技曲线 [来源2]。"
    detail: list[dict] = []
    all_ok, good, total = judge_citations(answer, chunks, detail=detail)
    assert total == 2 and good == 1 and all_ok is False
    assert detail[0]["n"] == 1 and detail[0]["ok"] is True and detail[0]["overlap"] >= 0.30
    assert "退货" in detail[0]["sentence"]
    assert detail[1]["n"] == 2 and detail[1]["ok"] is False and detail[1]["overlap"] < 0.30


def test_judge_citations_detail_none_keeps_old_behavior():
    """不传 detail 时行为与旧签名完全一致（admin 调用方/既有测试兼容）。"""
    chunks = [_Chunk("未实际使用的大家电可无理由退货。")]
    all_ok, good, total = judge_citations("未实际使用的大家电可无理由退货 [来源1]。", chunks)
    assert (all_ok, good, total) == (True, 1, 1)


def test_write_report_structure(tmp_path):
    """--out JSON 结构：meta(四件套自描述)/summary/results 三层。"""
    from scripts.eval_faithfulness import _write_report

    stats = {"qa": [2, 2], "refuse": [0, 0], "refuse_qa": [0, 0], "handoff": [0, 0], "chitchat": [0, 0]}
    results = [
        {
            "qid": "Q001", "kind": "qa", "ok": True, "why": "", "answer": "已答",
            "chunks": [{"i": 1, "text": "原文", "score": 0.5, "dense_score": 0.5, "doc_id": "d1"}],
            "cit": (1, 1, True),
            "cit_detail": [{"n": 1, "sentence": "已答", "overlap": 0.9, "ok": True}],
        }
    ]
    p = _write_report(
        str(tmp_path / "r.json"),
        {"kb_name": "星河智家·官方政策库", "sample": 0, "limit": 0, "offset": 0},
        stats, results, cit_good=1, cit_total=1,
    )
    import json

    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["meta"]["provider"] == "longcat"
    assert data["meta"]["top_k"] == 5
    assert data["meta"]["model"] == "LongCat-2.0"
    assert data["meta"]["kb_name"] == "星河智家·官方政策库"
    assert len(data["meta"]["script_sha256_12"]) == 12
    assert data["summary"]["citation"] == [1, 1]
    assert data["summary"]["stats"]["qa"] == [2, 2]
    assert data["results"][0]["cit_detail"][0]["n"] == 1
    assert data["results"][0]["chunks"][0]["text"] == "原文"
