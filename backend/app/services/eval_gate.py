"""评测门禁判定单一真源（B1-6：从 app.api.eval 下沉，消除 services→api 反向依赖）。

消费方（同向依赖本模块，禁止再互相引用）：
- app/api/eval.py        —— GET /admin/eval/gate 观测面
- app/services/kb_publish_service.py —— 批次发布快检门禁

阈值与冻结脚本 scripts/eval_faithfulness.py 内嵌判定同源（qa≥85% / refuse≥90% /
citation≥95%）。脚本在 BASELINE.sha256 冻结清单内：若脚本阈值经合规补登流程变更，
必须同步本模块常量（本文件是唯一可编辑的阈值真源）。
"""
from __future__ import annotations

from app.models.eval_result import EvalResult, EvalStatus

#: 门禁阈值（单一真源；与冻结脚本同源，见模块 docstring）
QA_MIN = 0.85
REFUSE_MIN = 0.90
CITATION_MIN = 0.95


def gate_passed(rows: list[EvalResult]) -> bool:
    """发布门禁判定 v1：与 scripts.eval_faithfulness._pass_all 同阈值，按落表 stats 重算。

    EvalResult 只存每指标 score/total（无 run 级 pass_all 布尔），脚本冻结不可改签名，
    故在观测侧按字段重算：qa≥85%（无 qa 样本 → 不通过）；refuse≥90%、citation≥95%
    （有该指标行才判——citation 采样运行带引用样本时同样判 95%，比脚本 full_run-only
    略严：观测面宁可偏严不偏松）。FAILED 行（score=0/total=0）天然不通过。
    """
    done = {r.metric: r for r in rows if r.status == EvalStatus.DONE}
    qa = done.get("qa")
    if qa is None or qa.total == 0 or qa.score < QA_MIN:
        return False
    refuse = done.get("refuse")
    if refuse is not None and refuse.total and refuse.score < REFUSE_MIN:
        return False
    cit = done.get("citation")
    if cit is not None and cit.total and cit.score < CITATION_MIN:
        return False
    return True
