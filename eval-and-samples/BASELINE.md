# 灵犀评测基线冻结清单（BASELINE）

> 生效：2026-08-28。规则：**四件套一致才可比**（评测集 + 判定脚本 + 检索参数 + 模型 + 评测 KB）。

## 一、四件套冻结

| 维度 | 冻结值 | 位置 |
|---|---|---|
| 评测集 | 评测问题库.md / ground-truth.md / 口语化评测集.md | hash 见 BASELINE.sha256 |
| 判定脚本 | backend/scripts/eval_faithfulness.py @ df79cb1（sha256 `43934ccf2026`；S1 空句子窗口修复版） | git commit |
| 检索参数 | RETRIEVAL_TOP_K=5（2026-08-21 降噪 8→5）+ MIN_SCORE=0.30（拒答判定走 dense_score 解耦；RRF score 仅排序，无绝对语义） | backend/app/core/config.py |
| 模型 | LongCat-2.0（CHAT_PROVIDER=longcat） | backend/.env |
| 评测 KB | 星河智家·官方政策库（= smoke_import._KB_NAME，唯一真源） | backend/scripts/smoke_import.py |

## 二、faithfulness 新基线（2026-08-28，LongCat-2.0 + 判定脚本@df79cb1）

- qa：run1 76/81 = **93.8%**；run2 抖动复核 75/81 = **92.6%**（目标 ≥85% ✅ 双跑均过）
- refuse：run1/run2 均 7/7 = **100%**（目标 ≥90% ✅）
- 引用合法率（S1 判定器修复后冻结口径）：run1 223/230 = **97.0%**；run2 205/206 = **99.5%**（目标 ≥95% ✅ 双跑均过）
- refuse_qa：run1 0/1（误拒答 Q064）；run2 1/2（Q093 误拒答）；handoff run1/run2 均 5/5；chitchat 均 5/5
- 口径：qa 分母剔除 refuse_qa；LongCat-2.0 + top_k=5 + eval_faithfulness.py@df79cb1 + KB「星河智家·官方政策库」
- S1 判定器修复说明：@250e208 版存在"[来源N] 紧跟句末标点→句子窗口取空→误判非法"缺陷（run1 误伤 4 点 / run2 1 点，见归因清单·发现 2）。@df79cb1 修复后对原两份基线 JSON 离线 rejudge：219/230→**223/230**、204/206→**205/206**，CHANGED 集合与归因预测逐点一致（复算留档见归因清单·五）；qa/refuse 不受影响。
- 失败题逐题归因：`results/baseline-longcat-20260828-attribution.md`（S2/S3 冲刺输入）
- 旧基线（2026-08-26，glm-4.5-air）：qa 82.1% / refuse 87.5% / 引用 21.4%——模型与判定脚本均变，**不可比**

## 三、历史口径（退役存档，不可与上表直接对比）

- **2026-08-26 基线**（glm-4.5-air + 判定脚本@e20014e）：qa 64/78 = 82.1%、refuse 7/8 = 87.5%、引用 3/14 = 21.4%。被 2026-08-28 新基线替代。
- **8/16 基线**（gen-eval-final-flash-20260816.json）：eval-and-samples/gen_eval.py 宽松标记词（HONEST_MARKS=未收录/转人工/人工客服 + 只验[来源]标记存在）→ qa_false_rate 4.9%、src_compliance 1.0。**95.1% PASS 是这把宽松尺子的结果，与 backend 严格脚本不同源。**
- **flash 复现**（2026-08-26）：backend 严格脚本 + glm-4-flash → qa 78.8% / refuse 12.5% / 引用 5.6%，仅用于同模型归因（证明非模型退化）。

## 四、门禁说明

- CI eval job 已是硬门禁（qa≥85% + refuse≥90%，抽样 20 题）；citation≥95% 在全量模式判定（workflow_dispatch full_eval=true），抽样模式仅报告。
- 新基线三项双跑达标（本地全量），Task 6 将以 full_eval=true 复核 CI 全量门禁后做二次冻结。
- 已知待优化项（S1 后余量）：引用错位 2 题（Q074/Q043，挂账）；稳败题 Q032/Q096（列举/漏场景，prompt 补丁未翻转，S3 后续迭代）；高抖动题 Q011/Q052/Q088/Q084/Q092/Q093 降级处理。

## 五、体系缺口（已暴露，待补）

- BASELINE.sha256 原只冻结评测集 hash，未冻结判定脚本/检索参数/模型 → 2026-08-26 起以本文件补齐四件套；2026-08-28 起补录判定脚本 hash 与检索参数至 sha256 文件。
- 检索分数口径注意：run_eval 存档中的 top1/margin 是 RRF 融合分（理论最大 0.049，无绝对语义），拒答阈值语义看 dense_score（归因清单·发现 1，R1 已结案）。
- **⚠️ 本地评测 KB 污染事件（2026-08-28 晚发现，已清理）**：本地同名评测库曾混入 9 个演示文档（seed_demo_data 无参运行导入"租户最新 KB"所致，22docs/40chunks），上方第二节各本地冻结数字均为**污染库口径**（恰好均达标，但检索分布与 CI fresh 库 13docs/26chunks 不同，不可严格比；Q042 缺陷在污染库上不可复现=假绿）。已清除污染并恢复 CI 对齐口径；**后续冻结一律以 CI full_eval 数字为权威**。详见归因清单 §六。
- **Q069 判据挂账**：ae0eb3b 依污染源文档（高频补充-账户与售后.md，非 kb/ 正式源）把 Q069 改判回答类，CI 纯库下该判据失真（模型正确拒答被判误拒答）。refuse_qa 不进门禁分母，暂不阻塞；修复需动评测集（四件套重冻结）。

## 六、Task 5 冲刺增量记录（2026-08-28）

- **S1 引用合法率**（df79cb1 + bf2d865）：修复判定器"空句子窗口"误判 → 冻结口径 run1 223/230 = 97.0%、run2 205/206 = 99.5%。分支 A（prompt 加固）/ 分支 B（阈值 0.30→0.25）不触发（达标，YAGNI）。复算留档：归因清单 §五。
- **S2 拒答 + S3 faithfulness**（fc5572e + 889682f，prompt 单变量两批）：
  - 归因修正：Q064/Q093 误拒答**不是检索阈值边界**（实测 dense 0.542/0.678 确定性远高于 MIN_SCORE=0.30），是 LLM 拿含答案 chunks 仍输出拒答话术 → prompt 侧修复。
  - 规则 13 补"同义改写≠相近条款"（必答侧）+ 对偶面"主题相近≠同一事项"（必拒侧，Q071 反例锚点）；规则 10 补"同句列举要素完整转述"。
  - 首轮回归暴露过修正副作用（Q071 诚实性破线 refuse 85.7%）→ 对偶补丁修复。
  - **终审全量（sprint23-prompt-20260828-run2.json）：qa 78/83 = 94.0% ✅ / refuse 7/7 = 100% ✅ / citation 219/221 = 99.1% ✅，全量模式 PASS**；误拒答连续两轮 0 起（首轮 0/0，终审 0/0）。
  - 对照冻结基线：qa 93.8/92.6 → 95.2/94.0（升）、refuse 100% 持平、citation 97.0/99.5 → 98.1/99.1（同带）、误拒答 1-2 起/轮 → 0。验收通过。
