# RAG 质量深化 · 交接工作文档（Task 3 本地基线双跑完成）

> **交接日期**：2026-08-28（晚）
> **项目**：灵犀 Customer Service（backend/，PostgreSQL + Qdrant + Redis + FastAPI）
> **目标**：LongCat-2.0 + 新 prompt 基线上重建评测基线，三项指标达标
> **计划**：`docs/superpowers/plans/2026-08-28-rag-quality-deepening.md`
> **Spec**：`docs/superpowers/specs/2026-08-28-rag-quality-deepening-design.md`

---

## 〇、当前整体进度（一览）

| 任务 | 内容 | 状态 | 提交 |
|---|---|---|---|
| Task 1 | `--out` 结构化导出（JSON 三层 + chunks 快照 + 逐引用点明细） | ✅ 完成 | 45a9046 |
| Task 2 | citation≥95% 进全量模式硬门禁（full_run 键防 --limit 绕过） | ✅ 完成 | 8762a03 |
| Task 3 | 本地 LongCat 全量基线双跑（run1 + run2 抖动复核） | ✅ 完成 | 250e208, c4ca269 |
| Task 4 | 归因清单 + BASELINE 冻结 | ✅ 完成 | c5431fe |
| Task 5 | 三个专项冲刺（S1 引用 → S2 拒答 → S3 faithfulness；+ S4 增补） | ✅ 完成 | df79cb1, bf2d865, fc5572e, 889682f, 523160b, 3241013 |
| Task 6 | full_eval 复核 + 二次冻结 + 文档同步 | ✅ 完成 | 3241013 + 文档同步提交 |

相关依赖改造（评估工具链之外、早前完成）：KB 口径统一（e27edb9）、ruff 清零（d752cf1）、盲区测试补齐（cb2b0a1/d62b8e3）、规划文档（02249c6/15302ed/f846e47）。

---

## 一、环境与四件套（冻结口径）

| 维度 | 冻结值 | 位置 |
|---|---|---|
| 评测集 | 评测问题库.md（100 题）/ ground-truth.md / 口语化评测集.md | eval-and-samples/ |
| 判定脚本 | backend/scripts/eval_faithfulness.py @ **sha256 `28ea8c955d08`**（run1/run2 双跑一致） | git commit |
| 检索参数 | RETRIEVAL_TOP_K=5, MIN_SCORE=0.30 | backend/app/core/config.py（常量经 pydantic 字段） |
| 模型 | LongCat-2.0（CHAT_PROVIDER=longcat） | backend/.env（LONGCAT_* env） |
| 评测 KB | `星河智家·官方政策库`（= smoke_import._KB_NAME，唯一真源） | backend/scripts/smoke_import.py 模块级常量 |

**本地环境**（本次实际运行方式）：
- 容器：`docker compose up -d postgres redis qdrant`（backend/ 下，compose 项目名 `lingxi`）
- 覆盖 env：`POSTGRES_HOST=localhost`、`QDRANT_URL=http://localhost:6333`、`REDIS_URL=redis://localhost:6379/0`、`NO_PROXY/no_proxy=localhost,127.0.0.1`
- 评测命令：
  ```
  cd backend && python -m scripts.eval_faithfulness --kb-name "星河智家·官方政策库" \
    --out ../eval-and-samples/results/baseline-longcat-20260828.json
  ```

---

## 二、Task 3 基线跑结果（本周关键交付）

### 2.1 类目指标（run1 vs run2，脚本 sha 一致可比）

| 指标 | run1 | run2 | 差异 | 门禁(≥) |
|---|---|---|---|---|
| qa faithfulness | 76/81 = **93.8%** | 75/81 = **92.6%** | -1.2pp | 85% ✅ |
| refuse 合理拒答 | 7/7 = **100%** | 7/7 = **100%** | 0 | 90% ✅ |
| 引用合法率 | 219/230 = **95.2%** | 204/206 = **99.0%** | +3.8pp | 95% ✅（双跑均过） |
| refuse_qa | 0/1 | 1/2 | 分母小 | — |
| handoff / chitchat | 5/5 / 5/5 | 5/5 / 5/5 | 0 | — |

**结论**：三项门禁双跑全绿，基线已达标可冻结（Task 4 执行）。

### 2.2 失败题（run1 全量 7 题）

| 题 | 类别 | 原因 | run2同败? |
|---|---|---|---|
| Q002 | error | ConnectError 偶发网络（getaddrinfo failed），非模型抖动 | 否（run2 通过） |
| Q011 | qa | 断言2未忠实(窗口交集11%): 预售不支持无理由退款 | 否（run2 通过） |
| Q032 | qa | 断言1未忠实(数字未全中): 星河 Z9 Pro 骁龙/12+256GB/6.7英寸OLED | ✅ 双跑稳定败 |
| Q062 | qa | 断言1未忠实(窗口交集22%): 全额退款未用券抵扣运费时券原路退回 | ✅ 双跑稳定败 |
| Q064 | refuse_qa | 误拒答(资料含答案仍拒答) | 否（run2 转合理） |
| Q093 | qa | 断言1未忠实(窗口交集15%): 碎屏险+延保可叠加、不影响旧机回收抵扣 | 否（run2 变 refuse_qa 误拒答） |
| Q096 | qa | 断言1未忠实(窗口交集25%): 大家电送货含基础搬运至楼下/电梯口 | ✅ 双跑稳定败 |

**双跑稳定失败题（重点归因）**：Q032、Q062、Q096 —— 全是"断言未忠实"，chunks 含料但生成不贴原文，指向生成侧而非检索。

**高抖动题（run1 PASS → run2 FAIL）**：Q052（金卡专属客服/生日礼券断言）、Q088（延保/碎屏险开票按页面对准断言）、Q092（延保 12→24 个月数字断言）——LLM 生成波动，归因时单独标注、优化优先级降低。

### 2.3 检索层评测（eval-and-samples/run_eval.py）

- recall@5（问答 83 题）：rewrite OFF/ON 均 78/83 = **94.0%**；口语变体(rewrite ON) 80/83 = **96.4%**
- 诚实性 7 题未命中（合理，KB 未覆盖 → 拒答正确路径）
- **⚠️ 待调查异常**：top1 与 margin 分桶**全部落在 0.00-0.10**（83 可答 + 7 拒答全部），avg_top1=0.049 —— 分数绝对值异常的扁平化，怀疑 bge embedding 归一化/Qdrant 索引问题。recall 达标说明排序可用，但绝对值低会干扰 MIN_SCORE=0.30 拒答阈值语义，需 Task 4 调查（见五、风险）。

### 2.4 产物清单（已 commit c4ca269）

- `eval-and-samples/results/baseline-longcat-20260828.json`（run1，results 100 条全量明细）
- `eval-and-samples/results/baseline-longcat-20260828-run2.json`（run2 抖动复核）
- `eval-and-samples/results/baseline-longcat-20260828-run1-vs-run2.md`（对比报告）
- `eval-and-samples/results/baseline-run_eval-20260828.txt`（检索层评测存档）
- `eval-and-samples/results/*.console.log`（两次 console 日志）

---

## 三、Task 3 执行中发现并修复的 bug（重要）

**`_run_faithfulness` 成功路径漏收集 results**（commit 250e208）：
- 现象：`--out` 导出 JSON 的 `results` 恒为空数组（stats 正常累计、逐题明细全丢）→ Task 4 归因/双跑无数据可用。
- 根因：循环内只有 skip/error 分支 `results.append`，成功路径遗漏。
- 修复：成功路径统一 `results.append(res)`（含 answer/chunks/cit_detail 快照）。
- 回归测试：`test_run_faithfulness_success_appends_results`（20 用例全绿，ruff 干净）。
- **教训**：`--out` 产物必须解开 results 计数验证非空，否则"门禁绿但明细空"的假达标。

---

## 四、待执行项（Task 4-6，按顺序）

### 4.1 Task 4：归因清单 + BASELINE 冻结
1. 逐题归因失败题（Q032/Q062/Q096 优先 + 高抖动题 Q052/Q088/Q092 + Q093 类目漂移），写入
   `eval-and-samples/results/baseline-longcat-20260828-attribution.md`
2. 更新 `eval-and-samples/BASELINE.md`（新四件套：模型=LongCat-2.0，判定脚本=@sha 28ea8c955d08）
3. 更新 `eval-and-samples/BASELINE.sha256`（补判定脚本/检索参数/模型 hash）
4. **调查检索分数扁平化**（0.00-0.10）——检查 bge embedding 配置与 Qdrant 索引（见风险 R1）
5. Commit

### 4.2 Task 5：三个专项冲刺（S1 → S2 → S3）
- **S1 引用合法率**：基线已 ≥95%（95.2%/99.0%），大概率直接闭合；若守线有风险再走决策树（引用格式→prompt 加固；判据过时→`--rejudge` 双跑校准）
- **S2 拒答**：关注 Q064/Q093 误拒答类（资料含答案仍拒答）；`--rejudge` 工具判定器改动双跑留档
- **S3 faithfulness**：Q032/Q062/Q096 稳败题 → 生成不贴原文 → prompt 手段（规则 10/11/13 复用）
- 纪律：prompt/判定器/检索**禁止同批次同时变**；每专项收尾全量 100 题回归，指标只升不降否则回退
- 工具：开发期用 `--limit/--offset/--sample` 快跑，收尾才全量（全量单次约 25-35 分钟）

### 4.3 Task 6：full_eval 复核 + 二次冻结
1. `gh workflow run CI --ref master -f full_eval=true` + `gh run watch`（本地需先 push 本次两个 commit）
2. 全量绿后 BASELINE.md 追加"二次冻结（达标）新下限"段落
3. 过时文档状态标注（multi-agent-collaboration-design.md / optimization-top3-todo.md）
4. 最终 commit

---

## 五、风险与待办关注

| # | 风险/事项 | 影响 | 缓解/行动 |
|---|---|---|---|
| R1 | **检索分数全部 0.00-0.10（avg_top1=0.049）** | recall 达标但拒答阈值 MIN_SCORE=0.30 语义失真（现有 top1 全部 <0.30，拒答全靠 query 改写/语义兜底） | 查 bge-base-zh-v1.5 归一化、Qdrant 索引参数（v1.9.1 dense 索引）、与历史 run_eval 对照；Task 4 首办 |
| R2 | LongCat 输出抖动（6 题双跑翻转） | 单次全量波动 ±1-2pp | 关键批次双跑取一致再冻结；高抖动题（Q052/Q088/Q092）归因时降级处理 |
| R3 | Docker Desktop 中途退出致容器 Exited(255) | run2 首跑 PG 连接超时失败 | 评测前 `docker ps` 自检；本机已验证重启容器组后正常 |
| R4 | 250e208 / c4ca269 未 push | CI eval job（含 citation 全量门禁）未复核本次改动 | push 后触发 `full_eval=true` 全量复核（Task 6） |
| R5 | Q093 类目在 run1(run2) 间漂移（qa→refuse_qa） | 类目归因不稳定 | 归因时以双跑交集为准，单独标注类目漂移题 |

---

## 六、快速上手指南

```bash
# 1. 起容器
cd backend && docker compose up -d postgres redis qdrant

# 2. 全量评测（约 30 分钟）
cd backend && python -m scripts.eval_faithfulness --kb-name "星河智家·官方政策库" \
  --out ../eval-and-samples/results/baseline-longcat-<date>.json

# 3. 抖动复核（第二次跑，对比 summary）
python -m scripts.eval_faithfulness --kb-name "星河智家·官方政策库" \
  --out ../eval-and-samples/results/baseline-longcat-<date>-run2.json

# 4. 检索层评测
python ../eval-and-samples/run_eval.py   # 需 PG_URL env

# 5. 快跑指定题（失败题归因）
cd backend && python -m scripts.eval_faithfulness --sample 20 --out /tmp/fast.json
```

**关键文件**：
- 评测脚本：`backend/scripts/eval_faithfulness.py`（judge 纯函数 / _write_report / _pass_all / --out）
- 判定测试：`backend/tests/test_eval_faithfulness.py`（20 用例）
- Prompt：`backend/app/prompts/qa_prompt.py`（规则 3/6/10/11/12/13）
- 拒答阈值：`backend/app/services/steps/refuse.py`（MIN_SCORE=0.30）
- 诇据提交历史：Task 3 完成短链 `master@c4ca269`

---

## 七、交接确认

- ✅ 本地基线双跑已完成，三项门禁双跑全绿（qa 92.6-93.8% / refuse 100% / citation 95.2-99.0%）
- ✅ 产物 + 对比报告已 commit（250e208 修复、c4ca269 存档），工作区干净
- ✅ 评测工具链（--out / 门禁 / KB 口径）全部就绪，可直接进入 Task 4
- ✅ R1 检索分数扁平化已结案（RRF 融合分按设计，无缺陷；BASELINE §五 / 归因清单·发现 1）
- ✅ 已 push master 并完成 CI full_eval 复核（Task 6 收尾，见下方回执）

## 八、Task 4-6 执行回执（2026-08-28）

- **Task 4**（c5431fe）：逐题归因清单 + LongCat-2.0 四件套冻结；R1 结案；判定器空句子窗口 bug 登记。
- **Task 5**：S1 判定器修复（df79cb1，rejudge 双跑校准 97.0%/99.5%）；S2/S3 prompt 边界补丁（fc5572e/889682f，同义改写≠条款类推 + 对偶面 + 列举完整性），本地终审全量 PASS（qa 94.0%/refuse 100%/citation 99.1%，污染库口径）。
- **S4 增补**（3241013）：CI 首跑 run33170670510 FAIL（refuse 85.7%）→ Q042「场景归类」真实缺陷（受控实验旧 8/10 vs 新 10/10）+ 本地评测 KB 污染事件（seed_demo_data latest_kb 策略，9 演示文档混入，已清理对齐 CI 口径；Q069 判据失真挂账）。详见 BASELINE §五/§七、归因清单 §六。
- **Task 6**：CI full_eval run **33176656355**（3241013）全量 PASS：**qa 90.2% / refuse 100% / citation 99.5%**（7 job 全绿）→ BASELINE 二次冻结（三项为新下限）。冻结以 CI fresh 库口径为权威。
- **后续迭代（2026-08-28/29，计划 docs/superpowers/plans/2026-08-28-rag-quality-followup.md）**：污染防线双闸（smoke_import 文档清单审计 `--strict` 565a304 + seed_demo_data 收权 2497cf5）+ Q069 判据回正（4468204，诚实性 7→8 题，顺带修复 ae0eb3b 遗留的 hash 失配）。**三次冻结**：CI run **33187434752** Eval gate PASS（**qa 95.1% / refuse 8/8 = 100% / citation 98.7%**，与本地完全一致；该 run Lint 红=新测试文件 I001，1dc36ca 已修）。终审与外部审查处置见 docs/superpowers/plans/2026-08-29-quality-hardening.md。
- **批次验收闭环（2026-08-29 终审）**：全分支终审（253613f..aae56cf）结论 Ready（无 Critical；唯一 Important=缺修复后绿 run 证据，已闭环：lint 修复后 **#36（1dc36ca）/ #37（aae56cf=master HEAD）连续两绿**，抽样 eval 亦绿）。6 条 Minor 挂账 triage 全"留"（理由见 progress.md）；防线残留通道两条登记 BASELINE §五。后续：外部审查 S1 修复（F-1 批次）+ S2 处置待拍板（总方案 §三 F-2）。