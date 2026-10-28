"""评测基线冻结口径 · 单一真源（2026-10-27 抽取代号硬编码）。

背景：守卫测试原先把模型名写成字面量（``"LongCat-2.0"``）散落在三处断言里。
这种「硬编码字面量」在换模型时有两个坏处：
1. 换模型 → 三处断言同时红 → 维护者只知道「红了」，不知道该改成什么；
2. 更糟的改法是「把字面量换成新模型名」——那等于让期望值跟着实现走，
   **基线守卫退化成同义反复**（这正是 2026-09-27 审计 M5 任务 2 想根治的问题：
   当时评测口径一族全部写成 ``== settings.X``，常量改坏时两侧一起错、测试仍全绿）。

正确形态：**期望值来自独立的冻结记录，不来自被测实现**。本模块就是那份独立记录
的代码化载体——换模型时只改这里一处，守卫依然钉住「当前生效模型 == 冻结模型」。

与 ``eval-and-samples/BASELINE.sha256`` 的关系：
- BASELINE.sha256 冻结 **6 个文件的 hash**（评测集三件 + 判定脚本三件），
  模型名**不在 hash 范围内**（它是运行期配置，不进文件 hash）；
- 模型/检索参数/评测 KB 这三项「非文件型四件套」记在 BASELINE.sha256 注释头
  与 BASELINE.md 表格里——本模块把其中**模型**一项代码化，供守卫测试引用。

改动纪律：换模型时须同时更新 ① 本模块 ② backend/.env ③ eval-and-samples/BASELINE.md
的冻结表，并在 BASELINE.md 记录变更理由与日期（沿用项目既有先例：
先记录理由，后更新 hash）。
"""
from __future__ import annotations

#: 冻结的对话模型（评测与生产同一模型）。
#: 2026-08-28 起：LongCat-2.0（backend/.env，CHAT_PROVIDER=longcat）
#: 2026-10-27 起：qwen3.8-flash（阿里云百炼业务空间专属端点，OpenAI 兼容）
#:    —— 换模型属「四件套变更」，旧冻结数字不可与新模型结果并列比较，须重跑全量并重新冻结。
FROZEN_CHAT_MODEL = "qwen3.8-flash"

#: 冻结的对话 provider（OpenAI 兼容端点；变量名沿用 LONGCAT_* 指向百炼）。
FROZEN_CHAT_PROVIDER = "longcat"

#: 冻结的 embedding 模型（本地 bge，0 成本不出境；自 2026-08-27 起未变）。
FROZEN_EMBEDDING_MODEL = "BAAI/bge-base-zh-v1.5"

#: 冻结的检索参数（自 2026-08-21 起未变）。
FROZEN_RETRIEVAL_TOP_K = 5
FROZEN_MIN_SCORE = 0.30
