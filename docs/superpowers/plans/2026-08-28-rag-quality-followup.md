# RAG 质量后续迭代（污染防线 + 判据回正）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 巩固 2026-08-28 冻结的 CI 基线——堵住评测 KB 污染通道、回正 Q069 判据（评测集回改 + 四件套重冻结），并以新口径完成一次 CI full_eval 复核。

**Architecture:** 两个防线任务（smoke_import 文档清单审计 + seed_demo_data 目标库收权）纯代码侧、红测先行、互不依赖可并行；Q069 是评测集变更（只改 ground-truth.md，判定脚本 hash 不变），走四件套重冻结流程；最后统一本地全量回归 + CI full_eval 复核 + BASELINE 冻结数字更新。

**Tech Stack:** Python 3.11（backend/.venv）、pytest、SQLAlchemy（sqlite 内存测试）、GitHub Actions（ci.yml eval job，workflow_dispatch full_eval=true）、LongCat-2.0 评测。

## Global Constraints

- 四件套一致才可比：评测集（ground-truth.md 等）+ 判定脚本 + 检索参数 + 模型 + 评测 KB。任何一项变更必须在 BASELINE.md / BASELINE.sha256 留痕。
- **判定脚本 `backend/scripts/eval_faithfulness.py` 本计划零改动**（其 sha256 `43934ccf2026…` 保持冻结）。
- 冻结下限（CI run 33176656355 权威口径）：qa 90.2% / refuse 100%（7 题）/ citation 99.5%。Task 3 后 refuse 分母 7→8，以 Task 4 复核数字为新下限。
- 单变量纪律：禁止同一批提交同时改 prompt + 判定脚本 + 检索参数。本计划不改 prompt。
- 本地评测前置口径：评测 KB「星河智家·官方政策库」必须为 13 docs / 26 chunks（`backend/scripts/smoke_import.py` 导入 kb/ 的全集）；跑评测前先核对，不对齐先重建（`python -m scripts.smoke_import` + 手工清污染，方法见 BASELINE §五）。
- 本地跑评测的 env 覆盖（上轮同款）：`POSTGRES_HOST=localhost REDIS_URL=redis://localhost:6379/0 QDRANT_URL=http://localhost:6333 PYTHONIOENCODING=utf-8`，venv 解释器 `backend/.venv/Scripts/python.exe`。
- 多助手协作约束：另一助手在 `wt/backend` / `wt/frontend` worktree 的分支上工作，**不要**触碰这两个目录及其分支；只动 master 与本计划列出的文件。

## 范围外（明确不做，含触发条件）

- **断言/引用交集阈值校准（0.30→0.25）**：CI 失败明细中 Q063(28%)/Q084(29%)/Q088(26%) 属 band 边界，但门禁绿时不松尺（S1 分支 B 裁决沿用）。触发条件：qa 跌破 88% 且归因确认 band 误杀。
- **qa 提升冲刺（Q032「如需了解其他参数」省略话术 / Q052 金卡权益漏答等）**：均为概率性抖动题，CI #30 已过。触发条件：用户明确要求 qa≥95%，届时按 S3 方法论（单变量 prompt + 受控重放 + 全量回归）另立计划。
- **run_eval 存档附 dense_score 列**：可选改进不阻塞，留待下次触碰 eval 脚本时捎带（判定脚本冻结期不动）。

---

### Task 1: smoke_import 文档清单审计（污染防线）

**Files:**
- Modify: `backend/scripts/smoke_import.py`
- Test: `backend/tests/test_smoke_import_guard.py`（新建）

**Interfaces:**
- Consumes: `smoke_import.py` 现有变量 `files`（`sorted(KB_DIR.iterdir())` 结果，可含 PDF）、`db`（SQLAlchemy Session）、`kb.id`。
- Produces: 纯函数 `check_doc_set(kb_docs: set[str], source_files: set[str]) -> list[str]`（返回 KB 中多出的文档名排序列表）；CLI 参数 `--strict`（有差异时 exit 1）。

- [ ] **Step 1: 写红测（纯函数，不需要 DB）**

```python
"""smoke_import 文档清单审计（2026-08-28 KB 污染事故防线）。"""
from scripts.smoke_import import check_doc_set


def test_check_doc_set_flags_pollution():
    extra = check_doc_set({"退换货政策.md", "模拟订单-物流轨迹.md"}, {"退换货政策.md"})
    assert extra == ["模拟订单-物流轨迹.md"]


def test_check_doc_set_clean_kb_passes():
    assert check_doc_set({"退换货政策.md", "隐私政策.md"}, {"退换货政策.md", "隐私政策.md"}) == []


def test_check_doc_set_extra_docs_sorted():
    extra = check_doc_set({"b.md", "a.md", "c.md"}, {"c.md"})
    assert extra == ["a.md", "b.md"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_smoke_import_guard.py -v --no-cov`
Expected: FAIL（`ImportError: cannot import name 'check_doc_set'`）

- [ ] **Step 3: 实现纯函数与审计接线**

在 `backend/scripts/smoke_import.py` 顶部 import 区之后加纯函数：

```python
def check_doc_set(kb_docs: set[str], source_files: set[str]) -> list[str]:
    """KB 内文档名 vs kb/ 源文件名差异审计，返回 KB 多出的文档名（污染嫌疑）。

    2026-08-28 事故：seed_demo_data 无参运行曾把 9 个演示文档混入评测库，
    检索分布漂移致本地/CI 口径分裂、Q042 缺陷假绿（BASELINE §五）。此审计防复发。
    """
    return sorted(kb_docs - source_files)
```

argparse 区加参数（对齐现有 `add_argument` 风格）：

```python
    parser.add_argument("--strict", action="store_true",
                        help="审计出非 kb/ 源文档时 exit 1（CI 用）；默认仅告警")
```

在主流程最终 `[RESULT]` 打印之前（`pg_chunks` 统计附近，已有 `db` 与 `kb` 在作用域内）插入：

```python
    src_names = {f.name for f in files}
    kb_doc_names = {d.name for d in db.query(Document).filter_by(kb_id=kb.id).all()}
    extra = check_doc_set(kb_doc_names, src_names)
    if extra:
        msg = (f"KB 含 {len(extra)} 个非 kb/ 源文档（疑似 seed_demo_data 污染）: {extra}；"
               "清理方法见 BASELINE.md §五")
        if args.strict:
            print(f"[GUARD][FAIL] {msg}")
            sys.exit(1)
        print(f"[GUARD][WARN] {msg}")
```

（`Document` 若未 import 则补 `from app.models.knowledge import Document`；`sys` 若未 import 则补。）

- [ ] **Step 4: 跑测试确认通过 + 冒烟**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_smoke_import_guard.py -v --no-cov`
Expected: 3 passed

Run（本地已清理的库应通过）: `cd backend && POSTGRES_HOST=localhost REDIS_URL=redis://localhost:6379/0 QDRANT_URL=http://localhost:6333 ./.venv/Scripts/python.exe -m scripts.smoke_import --strict`
Expected: `[RESULT] PASS ✅`，无 `[GUARD]` 行（13 docs 全部来自 kb/）

- [ ] **Step 5: CI eval job 启用 strict**

`.github/workflows/ci.yml` eval job 内：

```yaml
      - name: Import eval KB (kb/ + kb-pdf/)
        run: python -m scripts.smoke_import --strict
```

- [ ] **Step 6: 全量单测无回归 + 提交**

Run: `cd backend && POSTGRES_HOST=localhost REDIS_URL=redis://localhost:6379/0 QDRANT_URL=http://localhost:6333 ./.venv/Scripts/python.exe -m pytest tests/ -q --no-cov`
Expected: 517+ passed（新增 3 条），0 failed

```bash
git add backend/scripts/smoke_import.py backend/tests/test_smoke_import_guard.py .github/workflows/ci.yml
git commit -m "feat(eval): smoke_import 文档清单审计——非 kb/ 源文档混入即告警/--strict 阻断（KB 污染防线）"
```

---

### Task 2: seed_demo_data 目标库收权（不再自动选「最新库」）

**Files:**
- Modify: `backend/scripts/seed_demo_data.py`（`latest_kb` 函数，约 36-56 行）
- Test: `backend/tests/test_seed_demo_guard.py`（新建）

**Interfaces:**
- Consumes: `KnowledgeBase` 模型、`SessionLocal`、`settings.TENANT_DEFAULT`。
- Produces: `latest_kb(db) -> uuid.UUID` 新契约——空环境建 demo 库；有 demo 库选它；只有业务库时 `SystemExit`（列出可选库，要求显式 `kb_id`）。

- [ ] **Step 1: 写红测（sqlite 内存库，不依赖 PG）**

```python
"""seed_demo_data 目标库收权（2026-08-28 评测库污染事故防线）。"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.models.base import Base  # noqa: F401 确保模型注册
from app.models.knowledge import KnowledgeBase


def _session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _kb(name: str) -> KnowledgeBase:
    return KnowledgeBase(name=name, description=name, tenant_id=settings.TENANT_DEFAULT)


def test_latest_kb_creates_demo_on_empty():
    from scripts.seed_demo_data import latest_kb
    db = _session()
    kb_id = latest_kb(db)
    kb = db.get(KnowledgeBase, kb_id)
    db.close()
    assert kb is not None and kb.name == "demo"


def test_latest_kb_prefers_demo_over_business_kb():
    from scripts.seed_demo_data import latest_kb
    db = _session()
    db.add(_kb("星河智家·官方政策库"))
    db.add(_kb("demo"))
    db.commit()
    kb_id = latest_kb(db)
    kb = db.get(KnowledgeBase, kb_id)
    db.close()
    assert kb.name == "demo"


def test_latest_kb_refuses_when_only_business_kbs():
    from scripts.seed_demo_data import latest_kb
    db = _session()
    db.add(_kb("星河智家·官方政策库"))
    db.commit()
    with pytest.raises(SystemExit, match="拒绝自动选库"):
        latest_kb(db)
    db.close()
```

（若 `Base.metadata` 未含全部模型导致 sqlite 建表缺列，按报错补 import 对应模型模块即可；`KnowledgeBase.created_at` 有 server_default 或 Python default 时无需手工赋值，否则在 `_kb` 里补 `created_at=datetime.now(UTC)`。）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_seed_demo_guard.py -v --no-cov`
Expected: `test_latest_kb_refuses_when_only_business_kbs` FAIL（现实现会返回评测库 id 而非 SystemExit）

- [ ] **Step 3: 重写 latest_kb**

替换 `backend/scripts/seed_demo_data.py` 的 `latest_kb` 整体：

```python
def latest_kb(db) -> uuid.UUID:
    """无参 seed 的目标库选择（收权版，2026-08-28 污染事故防线）：

    1) 空环境 → 自动建 demo 库（CI unit tests 无库分支，保留）；
    2) 有 name=='demo' 库 → 选它（演示数据归位）；
    3) 只有业务库 → 拒绝并退出，要求显式 kb_id。
       事故复盘：旧逻辑取「租户最新库」，评测库恰为最新时 9 个演示文档混入
       「星河智家·官方政策库」，检索分布漂移致本地/CI 口径分裂（BASELINE §五）。
    """
    kbs = db.scalars(
        sqlalchemy.select(KnowledgeBase).order_by(KnowledgeBase.created_at.desc())
    ).all()
    if not kbs:
        demo = KnowledgeBase(
            name="demo",
            description="自动创建：承载演示订单数据，供订单检索集成回归（test_demo_orders.py）",
            tenant_id=settings.TENANT_DEFAULT,
        )
        db.add(demo)
        db.commit()
        db.refresh(demo)
        logger.info("自动创建 demo 知识库 %s", demo.id)
        return demo.id
    demo_kb = next((k for k in kbs if k.name == "demo"), None)
    if demo_kb is not None:
        return demo_kb.id
    listing = "\n".join(f"  {k.id}  {k.name}" for k in kbs)
    raise SystemExit(
        "[seed_demo_data] 拒绝自动选库：环境无 demo 库，现有库：\n"
        f"{listing}\n"
        "为防演示数据污染评测库（2026-08-28 事故），请显式指定目标："
        "python scripts/seed_demo_data.py <kb_id>（可先建独立 demo 库后再 seed）"
    )
```

（`main()` 中 `kb_id = uuid.UUID(sys.argv[1]) if len(sys.argv) > 1 else latest_kb(db)` 不变。）

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_seed_demo_guard.py -v --no-cov`
Expected: 3 passed

- [ ] **Step 5: 全量单测无回归 + 提交**

Run: `cd backend && POSTGRES_HOST=localhost REDIS_URL=redis://localhost:6379/0 QDRANT_URL=http://localhost:6333 ./.venv/Scripts/python.exe -m pytest tests/ -q --no-cov`
Expected: 0 failed（CI unit tests job 走空环境分支不受影响）

```bash
git add backend/scripts/seed_demo_data.py backend/tests/test_seed_demo_guard.py
git commit -m "fix(scripts): seed_demo_data 收权——无参只选 demo 库，业务库环境拒绝并要求显式 kb_id（污染防线）"
```

---

### Task 3: Q069 判据回正（评测集回改 + 四件套重冻结）

**Files:**
- Modify: `eval-and-samples/ground-truth.md`（123 行 Q069 行 + 168-172 修订说明区）
- Modify: `eval-and-samples/BASELINE.sha256`（ground-truth hash 行 + 头注）
- Modify: `eval-and-samples/BASELINE.md`（§五 Q069 挂账结案 + 冻结口径说明）
- Read-only: `backend/scripts/eval_faithfulness.py`（**不改**；`parse_ground_truth` 以 `**拒答**` 前缀分类，94 行）

**Interfaces:**
- Consumes: ground-truth 行格式 `| Q069 | <期望> | <来源> |`，期望以 `**拒答**` 开头即归诚实性拒答类。
- Produces: refuse 分母 7→8；新 ground-truth sha256 写入 BASELINE.sha256。

- [ ] **Step 1: 修改 ground-truth.md 123 行（改回拒答类）**

```markdown
| Q069 | **拒答**：kb/ 正式源（账号与安全.md）无"更换绑定手机号"流程条款，应告知未收录/转人工，不得以演示文档充当依据 | 账号与安全（未覆盖） |
```

- [ ] **Step 2: 在 ground-truth.md 修订说明区（168 行附近）追加回改记录**

```markdown
## 冻结 hash（2026-08-28 二期修订：Q069 回改拒答类，推翻 2026-08-27 一期修订）

> 一期修订（ae0eb3b）以「高频补充-账户与售后.md 已收录换绑路径」为由把 Q069 改判回答类；
> 2026-08-28 查明该文档属 demo_data 演示文档，曾污染本地评测库（BASELINE §五），**不在 kb/ 正式源**。
> CI fresh 库（13docs）无此文档，Q069 正确表现是拒答——一期判据失真，故回改。
> 同步把诚实性拒答题 7→8 题（Q042/Q049/Q055/Q060/Q061/Q067/Q069/Q071）。
```

- [ ] **Step 3: 重算并更新 BASELINE.sha256 的 ground-truth 行**

Run: `cd eval-and-samples && python -c "import hashlib; print(hashlib.sha256(open('ground-truth.md','rb').read()).hexdigest())"`

把输出写入 `BASELINE.sha256` 对应行（`<新hash> *ground-truth.md`），头注追加一行：

```
# 2026-08-28 Q069 回改拒答类（二期修订，详见 ground-truth.md 修订说明）；判定脚本不变
```

- [ ] **Step 4: BASELINE.md 登记结案**

§五「Q069 判据挂账」条目改为：

```markdown
- **Q069 判据已回正（2026-08-28）**：一期修订（ae0eb3b）依污染文档改判回答类，CI 纯库下失真；
  已回改拒答类（诚实性 7→8 题），评测集 hash 更新，判定脚本不变。效果以 Task 4 CI 复核数字为准。
```

- [ ] **Step 5: 本地定点验证 Q069 翻绿**

Run（跑单题可用全量脚本的 `--limit`/`--offset` 组合，或直接全量观察 Q069 行）:
`cd backend && POSTGRES_HOST=localhost REDIS_URL=redis://localhost:6379/0 QDRANT_URL=http://localhost:6333 PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe -m scripts.eval_faithfulness --kb-name "星河智家·官方政策库" --limit 100 --out ../eval-and-samples/results/q069-verify.json`
Expected: 汇总 `refuse 8/8 = 100%`（Q069 归 refuse 类且 PASS），无 `refuse_qa 误拒答` 行；`[RESULT] PASS`

（注意评测 KB 必须仍是 13docs/26chunks；跑前可用 Task 1 的 `smoke_import --strict` 冒烟核对。）

- [ ] **Step 6: 提交**

```bash
git add eval-and-samples/ground-truth.md eval-and-samples/BASELINE.sha256 eval-and-samples/BASELINE.md eval-and-samples/results/q069-verify.json
git commit -m "fix(eval): Q069 判据回正——一期修订依污染文档误改回答类，回改拒答（诚实性 7→8 题，四件套重冻结）"
```

---

### Task 4: 全量回归 + CI full_eval 复核 + 冻结数字更新

**Files:**
- Create: `eval-and-samples/results/followup-final-<date>.json`（本地全量存档）
- Modify: `eval-and-samples/BASELINE.md`（新冻结数字）
- Modify: `docs/handoff-rag-quality-deepening-20260828-r2.md`（§八回执追加一行）

**Interfaces:**
- Consumes: Task 1-3 全部落地且单测全绿；本地 KB 13docs/26chunks。
- Produces: 新一轮 CI 权威冻结数字（预期 refuse 8/8）。

- [ ] **Step 1: 本地全量 100 题**

Run（后台，约 30-40 分钟）:
`cd backend && POSTGRES_HOST=localhost REDIS_URL=redis://localhost:6379/0 QDRANT_URL=http://localhost:6333 PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe -m scripts.eval_faithfulness --kb-name "星河智家·官方政策库" --out ../eval-and-samples/results/followup-final-20260828.json`
Expected: `[RESULT] PASS`；qa ≥90%、refuse 8/8、citation ≥95%；与冻结下限对比无系统性回退（失败题集合与 CI #30 失败明细同分布）

- [ ] **Step 2: 提交推送 + 触发 CI full_eval**

```bash
git add eval-and-samples/results/followup-final-20260828.json
git commit -m "test(eval): 防线+判据回正后本地全量回归存档（followup-final）"
git push origin master
```

用 GitHub API dispatch（git credential 取 token，302 手工跟随——上轮同款脚本）：
`POST /repos/wjt-code-design/lingxikefu/actions/workflows/ci.yml/dispatches`，body `{"ref":"master","inputs":{"full_eval":"true"}}`，记录新 run id。

- [ ] **Step 3: 轮询 CI 至完成（预期 ~30 分钟）**

Expected: 全部 job success。若 eval FAIL：下载该 job 日志（API 302 方式），按失败明细归因——防线改动（Task 1/2）不触碰检索与判定路径，理论上零影响；若 Q069 未归 refuse 类，核查 ground-truth 123 行格式（`**拒答**` 前缀是否在表格 cell 开头）。

- [ ] **Step 4: BASELINE 三次冻结 + 最终提交**

BASELINE.md §四追加：

```markdown
- **2026-08-28 三次冻结（防线+判据回正后）**：CI full_eval run <id>（commit <sha>）：qa <x>% / refuse 8/8 = 100% / citation <x>%。
  refuse 分母 7→8（Q069 回正）。qa/citation 相对二次冻结（90.2%/99.5%）波动在抖动带内即视为守住下限。
```

handoff r2 §八追加执行行。提交推送：

```bash
git add eval-and-samples/BASELINE.md eval-and-samples/results/ci-full-eval-run<id>.log docs/handoff-rag-quality-deepening-20260828-r2.md
git commit -m "docs(eval): 防线落地+Q069 回正 CI 复核通过，BASELINE 三次冻结（refuse 8/8）"
git push origin master
```

---

## Self-Review 结论（已自查）

1. **Spec 覆盖**：BASELINE §五两条体系缺口（smoke_import 校验 / seed_demo_data 收权）→ Task 1/2；Q069 挂账 → Task 3；归因清单 §六 6.4 全部闭环；band 校准 / dense_score 列 / qa 冲刺 → 「范围外」显式声明含触发条件。
2. **占位符扫描**：无 TBD/「参照上文」；Task 3 Step 3 的 hash 值须运行时计算（已给出精确命令）。
3. **类型/命名一致性**：`check_doc_set(set[str], set[str]) -> list[str]` 与 Task 1 测试/接线一致；`latest_kb(db) -> uuid.UUID` 契约与 `main()` 调用点兼容。
