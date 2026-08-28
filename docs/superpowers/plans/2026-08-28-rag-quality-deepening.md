# RAG 质量深化 · 最终执行方案（Implementation Plan）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 LongCat-2.0 + 新 prompt 基线上重建评测基线，归因攻坚三项指标（qa ≥85%、拒答 ≥90%、引用合法率 ≥95%），并把引用合法率补进 CI 全量模式硬门禁。

**Architecture:** 三段推进——先补齐评测工具地基（`--out` 结构化导出 + citation 全量门禁 + `--rejudge` 双跑工具），再本地重建 LongCat 基线并逐题归因，然后按三个专项（引用合法率→拒答→faithfulness）以"红测先行→单一变量改动→全量回归"循环攻坚，最后 full_eval 复核 + BASELINE 二次冻结。

**Tech Stack:** Python 3.13 / FastAPI 后端（`backend/`）、LongCat-2.0（CHAT_PROVIDER=longcat）、bge-base-zh-v1.5 本地 embedding、Qdrant + PostgreSQL + Redis（docker-compose）、pytest + ruff 0.16.4、GitHub Actions CI。

**Spec:** `docs/superpowers/specs/2026-08-28-rag-quality-deepening-design.md`（v2 + D 级终审修订）

## Global Constraints

- 阈值（verbatim）：qa faithfulness ≥ 85%、诚实性拒答率 ≥ 90%、引用合法率 ≥ 95%
- 四件套一致才可比：评测集 + 判定脚本 + RETRIEVAL_TOP_K=5 + LongCat-2.0（`CHAT_PROVIDER=longcat`）
- KB 口径统一：`--kb-name "星河智家·官方政策库"`（CI 与本地一致，smoke_import 建库名）
- 单一变量纪律：prompt / 判定器 / 检索三处**禁止同批次同时变化**
- 判定器改动必须新旧尺子双跑对比留档（`--rejudge`，见 Task 5）
- 指标只升不降：每专项收尾全量回归，任一指标下降即回退该批次（git bisect）
- 判定脚本/检索参数任何变动 → 当次更新 `eval-and-samples/BASELINE.md` 并注明旧值不可比
- `LONGCAT_API_KEY` 只存本地 `.env`（gitignore）/ GitHub Secrets，绝不入库
- 每个 code 任务收尾必过：`ruff check app tests alembic scripts`（backend/ 下）+ 对应 pytest
- 提交信息沿用项目风格：`type(scope): 中文描述`（如 `feat(eval): ...`）

---

### Task 1: eval_faithfulness `--out` JSON 导出（工具地基）

**Files:**
- Modify: `backend/scripts/eval_faithfulness.py`（`_sentence_supported` 拆出 `_sentence_overlap`；`judge_citations` 加 `detail` 参数；`eval_one` 返回全文 answer + chunks 快照 + 逐引用点明细；`_run_faithfulness` 加可选 `results` 收集参数并记录 skip/error 题；新增 `_write_report`；`main` 加 `--out`）
- Test: `backend/tests/test_eval_faithfulness.py`（追加）

**Interfaces:**
- Consumes: 现有 `judge_citations(answer, chunks) -> (all_ok, good, total)` 三元组语义（已有测试锁定，签名只追加可选参数）
- Produces（后续任务依赖）:
  - `judge_citations(answer, chunks, detail: list[dict] | None = None) -> tuple[bool, int, int]`，detail 每项 `{"n": int, "sentence": str, "overlap": float, "ok": bool}`
  - `_write_report(out_path: str, meta: dict, stats: dict, results: list[dict], cit_good: int, cit_total: int) -> Path`
  - `_sentence_overlap(sentence: str, chunk_text: str) -> float`
  - `--out <path>` CLI 参数；JSON 结构：`{meta, summary, results[]}`，results 每项含 `qid/kind/ok/why/answer(chunks 为 list[dict])/cit/cit_detail`
  - Task 5 依赖：JSON 中每题 `chunks`（含 text）与 `cit_detail`，供 `--rejudge` 与归因使用

- [ ] **Step 1: 写失败测试——judge_citations 明细**

在 `backend/tests/test_eval_faithfulness.py` 末尾追加：

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && python -m pytest tests/test_eval_faithfulness.py::test_judge_citations_detail_records_each_point -v`
Expected: FAIL，`TypeError: judge_citations() got an unexpected keyword argument 'detail'`

- [ ] **Step 3: 实现 `_sentence_overlap` + `judge_citations` detail 参数**

`backend/scripts/eval_faithfulness.py` 中，把现有 `_sentence_supported`（L179-185）替换为：

```python
def _sentence_overlap(sentence: str, chunk_text: str) -> float:
    """引用点句子与 chunk 的 2 字窗口交集比例（0-1）。"""
    s_bg = _bigrams(sentence)
    c_bg = _bigrams(chunk_text)
    if not s_bg or not c_bg:
        return 0.0
    return len(s_bg & c_bg) / len(s_bg)


def _sentence_supported(sentence: str, chunk_text: str) -> bool:
    """引用点句子是否被对应 chunk 实质支撑（2字窗口交集≥30%）。"""
    return _sentence_overlap(sentence, chunk_text) >= 0.30
```

`judge_citations`（L188-214）签名加 `detail`，循环体改用 `_sentence_overlap`：

```python
def judge_citations(answer: str, chunks, detail: list | None = None) -> tuple[bool, int, int]:
    """[来源N] 引用合法性（grounded-ai：引文必须可溯源到 chunk）。

    每个 [来源N] 的引用点句子须与 chunks[N-1].text 有实质内容重叠，
    防「引文编造」（把内容安到无关来源上）。返回 (是否全合法, 合法数, 总数)。
    detail 非 None 时逐点追加 {"n", "sentence", "overlap", "ok"}（--out 导出/归因用）。

    细节：引用点取标记前最近一个句子（避免多句累积稀释交集）；
    连续引用 [来源1][来源2] 时第二个引用点为空 → 跳过不计（共享同一句子）。
    """
    if not chunks:
        return True, 0, 0
    parts = re.split(r"(\[来源\d+\])", answer)
    ok = total = 0
    cur = ""
    for part in parts:
        m = re.fullmatch(r"\[来源(\d+)\]", part)
        if m:
            n = int(m.group(1))
            if 1 <= n <= len(chunks) and cur.strip():
                total += 1
                sentence = re.split(r"(?<=[。！？；])", cur.strip())[-1]
                overlap = _sentence_overlap(sentence, chunks[n - 1].text)
                supported = overlap >= 0.30
                if supported:
                    ok += 1
                if detail is not None:
                    detail.append({"n": n, "sentence": sentence, "overlap": round(overlap, 3), "ok": supported})
            cur = ""
        else:
            cur += part
    return total == 0 or ok == total, ok, total
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && python -m pytest tests/test_eval_faithfulness.py -v`
Expected: 全部 PASS（含既有 14 个用例）

- [ ] **Step 5: 写失败测试——_write_report 结构**

```python
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
```

- [ ] **Step 6: 实现 `_write_report` + eval_one/_run_faithfulness/main 改造**

在 `eval_faithfulness.py` 顶部 import 区追加 `import hashlib`、`import json`、`from datetime import datetime, timezone`（`uuid` 已有）。模块级新增：

```python
def _write_report(out_path: str, meta: dict, stats: dict, results: list[dict], cit_good: int, cit_total: int) -> Path:
    """评测结果结构化落盘（归因/双跑对比的单一数据源）。

    meta 自带四件套信息（provider/model/top_k/脚本 sha256 + 调用参数），JSON 自描述；
    必须在 pass_all 判定与 exit 之前调用——门禁 FAIL 也要有完整 JSON 可查（spec D3）。
    results 每题含全文 answer 与 chunks 快照（D2），约 250KB/100 题。
    """
    payload = {
        "meta": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "provider": settings.CHAT_PROVIDER,
            "model": settings.LONGCAT_CHAT_MODEL,
            "top_k": settings.RETRIEVAL_TOP_K,
            "script_sha256_12": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12],
            **meta,
        },
        "summary": {"stats": {k: list(v) for k, v in stats.items()}, "citation": [cit_total, cit_good]},
        "results": results,
    }
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
```

`eval_one`（L217-251）三处改动——返回全文 answer（截断移到打印端）、qa 分支补 chunks 快照与 cit_detail：

```python
    if gt and gt["refuse"]:
        ok, why = judge_refuse(answer)
        return {"qid": q["qid"], "kind": "refuse", "ok": ok, "why": why, "answer": answer}
    if q["intent"] == "qa":
        if r.refuse or _is_llm_refusal(answer):
            if gt and _chunks_have_answer(r.chunks, gt["claims"]):
                return {"qid": q["qid"], "kind": "refuse_qa", "ok": False, "why": "误拒答(资料含答案仍拒答)", "answer": answer}
            return {"qid": q["qid"], "kind": "refuse_qa", "ok": True, "why": "合理拒答(资料未含答案)", "answer": answer}
        ok, why = judge_qa(answer, gt["claims"] if gt else [])
        cit_detail: list[dict] = []
        cit_all_ok, cit_good, cit_total = judge_citations(answer, r.chunks, detail=cit_detail)
        return {
            "qid": q["qid"],
            "kind": "qa",
            "ok": ok,
            "why": why,
            "answer": answer,
            # 引用统计必须全量累计（合法题同样计入分母）——此前只在整题全合法时
            # 置 None，导致分母只含失败题，引用合法率被系统性高估/失真（2026-08-27 实测）。
            "cit": (cit_good, cit_total, cit_all_ok),
            "cit_detail": cit_detail,
            # chunks 快照：归因/双跑不依赖重检索（spec D2）
            "chunks": [
                {"i": i + 1, "text": c.text, "score": c.score, "dense_score": c.dense_score, "doc_id": c.doc_id}
                for i, c in enumerate(r.chunks)
            ],
        }
```

（refuse 分支的 `"answer": answer[:80]` 同步改为 `"answer": answer`；chitchat/handoff 兜底分支同理。打印端 `_run_faithfulness` 已有 `[:60]` 截断，无需改。）

`_run_faithfulness`（L299-334）加可选参数并记录 skip/error 题（100 题全留痕，防静默丢失）：

```python
async def _run_faithfulness(
    db, kb_id: uuid.UUID, questions: list[dict], gt: dict, results: list[dict] | None = None
) -> tuple:
    """核心循环：逐题评测并汇总（供 main 与 run_faithfulness_eval 复用）。

    results 非 None 时逐题追加（含 skip/error），供 --out 导出。返回 (stats, fails, cit_good, cit_total)。
    """
    stats = {"qa": [0, 0], "refuse": [0, 0], "refuse_qa": [0, 0], "handoff": [0, 0], "chitchat": [0, 0]}
    fails: list[str] = []
    cit_good = cit_total = 0
    for q in questions:
        g = gt.get(q["qid"])
        if q["intent"] == "qa" and g is None:
            print(f"  [SKIP] {q['qid']} 无 ground-truth")
            if results is not None:
                results.append({"qid": q["qid"], "kind": "skip", "ok": False, "why": "无 ground-truth", "answer": ""})
            continue
        try:
            res = await eval_one_retry(db, kb_id, q, g)
        except Exception as e:  # noqa: BLE001
            print(f"  [ERR] {q['qid']} {type(e).__name__}: {e}")
            if results is not None:
                results.append({"qid": q["qid"], "kind": "error", "ok": False, "why": f"{type(e).__name__}: {e}", "answer": ""})
            res = None
        # ……（以下循环体不变）
```

（循环体其余部分原样保留；`run_faithfulness_eval` 是 admin 调用方 `api/eval.py:165` 的入口，不传 results，行为不变——spec D4。）

`main()`：argparse 加参数 + 结果落盘（落盘位置在汇总打印后、`pass_all` 之前）：

```python
    ap.add_argument(
        "--out", default="",
        help="结果 JSON 落盘路径（相对当前目录；门禁判定前写入，FAIL 也有完整数据）",
    )
```

```python
    db = SessionLocal()
    results: list[dict] = []
    try:
        kb = _resolve_kb(db, args.kb_name)
        if kb is None:
            print("[ERR] 无任何知识库（先跑 scripts.smoke_import 或 seed_demo_data）")
            return 2
        stats, fails, cit_good, cit_total = await _run_faithfulness(db, kb.id, questions, gt, results=results)
        if args.out:
            p = _write_report(
                args.out,
                {"kb_name": args.kb_name, "sample": args.sample, "limit": args.limit, "offset": args.offset},
                stats, results, cit_good, cit_total,
            )
            print(f"[OUT] 结果已写入 {p}")
    finally:
        db.close()
```

- [ ] **Step 7: 跑全量测试 + ruff**

Run: `cd backend && python -m pytest tests/test_eval_faithfulness.py -v && ruff check scripts`
Expected: 全 PASS，ruff 无违规

- [ ] **Step 8: Commit**

```bash
git add backend/scripts/eval_faithfulness.py backend/tests/test_eval_faithfulness.py
git commit -m "feat(eval): eval_faithfulness 加 --out 结构化导出——全文 answer+chunks 快照+逐引用点明细，JSON 自含四件套元数据"
```

---

### Task 2: citation 全量模式硬门禁

**Files:**
- Modify: `backend/scripts/eval_faithfulness.py`（新增纯函数 `_pass_all`；`main()` 门禁判定改用它；`[RESULT]` 打印分模式说明）
- Modify: `.github/workflows/ci.yml`（Faithfulness eval step 注释同步）
- Test: `backend/tests/test_eval_faithfulness.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 `_write_report`（落盘先行）；`stats[kind] = [total, ok]` 结构
- Produces: `_pass_all(stats: dict, cit_good: int, cit_total: int, full_run: bool) -> bool`；CI 全量模式（workflow_dispatch full_eval=true）下 citation <95% → exit 1；Task 6 的 full_eval 复核依赖此行为

- [ ] **Step 1: 写失败测试**

```python
# ---------- citation 全量模式门禁（Task 2，spec A2/D1） ----------


def _gate_stats(qa_total=10, qa_ok=10, refuse_total=0, refuse_ok=0):
    return {"qa": [qa_total, qa_ok], "refuse": [refuse_total, refuse_ok]}


def test_pass_all_sample_mode_ignores_citation():
    """抽样模式（full_run=False）citation 不参与判定：20 题仅 ~15-30 引用点，95% 门禁单点抖动 3-7pp 不可靠。"""
    from scripts.eval_faithfulness import _pass_all

    assert _pass_all(_gate_stats(), cit_good=0, cit_total=10, full_run=False) is True


def test_pass_all_full_mode_gates_citation():
    from scripts.eval_faithfulness import _pass_all

    assert _pass_all(_gate_stats(), cit_good=8, cit_total=10, full_run=True) is False  # 80% < 95%
    assert _pass_all(_gate_stats(), cit_good=10, cit_total=10, full_run=True) is True


def test_pass_all_full_mode_no_citations_passes():
    """全量但无引用点（退化情况，理论上 qa 题必有）不因 citation 挂。"""
    from scripts.eval_faithfulness import _pass_all

    assert _pass_all(_gate_stats(), cit_good=0, cit_total=0, full_run=True) is True


def test_pass_all_qa_and_refuse_thresholds_unchanged():
    from scripts.eval_faithfulness import _pass_all

    assert _pass_all(_gate_stats(qa_total=100, qa_ok=84), 10, 10, full_run=True) is False  # 84%<85%
    assert _pass_all(_gate_stats(refuse_total=10, refuse_ok=8), 10, 10, full_run=True) is False  # 80%<90%
    assert _pass_all(_gate_stats(), 10, 10, full_run=False) is True


def test_pass_all_empty_qa_fails():
    from scripts.eval_faithfulness import _pass_all

    assert _pass_all(_gate_stats(qa_total=0), 0, 0, full_run=False) is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && python -m pytest tests/test_eval_faithfulness.py -k pass_all -v`
Expected: FAIL，`ImportError: cannot import name '_pass_all'`

- [ ] **Step 3: 实现 `_pass_all` 并接入 main()**

模块级新增（放在 `_write_report` 之后）：

```python
def _pass_all(stats: dict, cit_good: int, cit_total: int, full_run: bool) -> bool:
    """门禁判定（纯函数，供单测）：qa≥85% 且 refuse≥90%；citation≥95% 仅全量模式判定。

    citation 挂全量模式的原因：抽样 20 题仅 ~15-30 个引用点，95% 门禁=最多错 1 点，
    单点抖动 3-7pp，抽样下判定不可靠（spec A2）。
    full_run 须由调用方以 not(sample or limit or offset) 计算（spec D1，--limit 绕过防护）。
    """
    qa_total, qa_ok = stats["qa"]
    refuse_total, refuse_ok = stats["refuse"]
    if qa_total == 0:
        return False
    if qa_ok / qa_total < 0.85:
        return False
    if refuse_total and refuse_ok / refuse_total < 0.9:
        return False
    if full_run and cit_total and cit_good / cit_total < 0.95:
        return False
    return True
```

`main()` 末尾的 pass_all 块（现 L416-428）替换为：

```python
    full_run = not (args.sample or args.limit or args.offset)
    pass_all = _pass_all(stats, cit_good, cit_total, full_run)
    gate_note = "citation≥95% 参与判定" if full_run else "citation 仅报告，不判定（子集模式）"
    print(f"[RESULT] {'PASS ✅' if pass_all else 'FAIL ❌'}（qa≥85% 且 refuse≥90%；{gate_note}）")
```

（`refuse_qa` 的 `[info]` 打印行保留在 pass_all 块之前，原样不动。）

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && python -m pytest tests/test_eval_faithfulness.py -v && ruff check scripts`
Expected: 全 PASS

- [ ] **Step 5: 同步 ci.yml 注释**

`.github/workflows/ci.yml` Faithfulness eval step 名与注释更新：

```yaml
      # faithfulness：真实 LLM 调用（LongCat LongCat-2.0，走 CHAT_PROVIDER=longcat）。
      # 硬门禁：未达阈值脚本 exit 1 → step 失败 → 阻断 CI。
      # 抽样模式（push，--sample 20）：判 qa≥85% + refuse≥90%，citation 仅报告（样本量不足，单点抖动 3-7pp）；
      # 全量模式（workflow_dispatch full_eval=true，100 题约 1h）：追加 citation≥95% 判定。
      - name: Faithfulness eval (qa >= 85% / refuse >= 90%; citation >= 95% @ full_eval)
```

（`if:` 与 `run:` 内容不变，只改 name 与注释。）

- [ ] **Step 6: Commit**

```bash
git add backend/scripts/eval_faithfulness.py backend/tests/test_eval_faithfulness.py .github/workflows/ci.yml
git commit -m "feat(eval): citation≥95% 进全量模式硬门禁——full_run 键防 --limit 绕过，抽样模式仅报告防抖动误红"
```

---

### Task 3: 本地 LongCat 基线全量跑（执行任务，无 TDD）

**Files:**
- Create: `eval-and-samples/results/baseline-longcat-20260828.json`（产物，不入库也行——results/ 已有历史 json 在库，保持一致入库存档）
- Create: `eval-and-samples/results/baseline-longcat-20260828-run2.json`（抖动复核）

**Interfaces:**
- Consumes: Task 1 的 `--out`；Task 2 的门禁语义（本地全量跑 exit 1 属预期，JSON 已先落盘）
- Produces: 基线 JSON ×2（Task 4 归因与 Task 5 冲刺的数据源）；run_eval 控制台记录（检索层 recall + T12 分布，Task 5 Sprint 2 切点依据）

- [ ] **Step 1: 环境就绪检查**

```bash
cd backend && docker compose ps
```
Expected: postgres / redis / qdrant 三容器 Up（health 状态）。未起则 `docker compose up -d` 后等 30s 复查。确认 `backend/.env` 含 `CHAT_PROVIDER=longcat` 与有效 `LONGCAT_API_KEY`（key 严禁输出到日志/提交）。

- [ ] **Step 2: 迁移 + 评测 KB 导入（幂等）**

```bash
cd backend && alembic upgrade head && python -m scripts.smoke_import
```
Expected: alembic 无错；smoke_import 输出导入/skip 计数（sha256 幂等，二次跑应全部 skip），库名「星河智家·官方政策库」。

- [ ] **Step 3: 全量 100 题基线跑（约 1h）**

```bash
cd backend && python -m scripts.eval_faithfulness \
  --kb-name "星河智家·官方政策库" \
  --out ../eval-and-samples/results/baseline-longcat-20260828.json
```
Expected: 结尾打印 `[OUT] 结果已写入 ...` + `[RESULT] PASS/FAIL`。**exit 1 属预期**（基线未达标正常，JSON 在判定前已落盘）。记录控制台汇总：qa / refuse / refuse_qa / citation 四组数字。

- [ ] **Step 4: 抖动复核跑（第二次全量）**

```bash
cd backend && python -m scripts.eval_faithfulness \
  --kb-name "星河智家·官方政策库" \
  --out ../eval-and-samples/results/baseline-longcat-20260828-run2.json
```
Expected: 与 run1 的 summary 对比，qa/refuse/citation 差异 ≤2pp。若 >2pp：记录差异题号清单（jq 或 python 对比两 JSON results），标记为"高抖动题"，Task 4 归因时单列（这些题的优化优先级降低——先修确定性失败）。

- [ ] **Step 5: 检索层评测 + 口语化 recall + T12 分布**

```bash
PG_URL="postgresql+psycopg://lingxi:<密码取 backend/.env 的 POSTGRES_PASSWORD>@localhost:5432/lingxi" \
  python ../eval-and-samples/run_eval.py
```
注意：run_eval.py 代码读的是 `PG_URL`（其 docstring 写 POSTGRES_PASSWORD 系笔误，spec D6）。Expected 输出：rewrite ON/OFF recall@5、口语变体 recall@5、可答 vs 拒答 top1/margin 分桶——**完整保存控制台输出**到 `eval-and-samples/results/baseline-run_eval-20260828.txt`（Task 5 Sprint 2 切点依据）。

- [ ] **Step 6: CI 门禁状态核实**

```bash
gh run list --branch master --limit 5
gh run view <最新 run id> --log-failed 2>/dev/null | head -50
```
Expected: 记录 eval job（`Eval gate (hard)`）绿/红状态。若红：导出失败 step 日志，红因（抽样 20 题的代表性差异）记入 Task 4 归因清单头部。

- [ ] **Step 7: Commit（产物存档）**

```bash
git add eval-and-samples/results/baseline-longcat-20260828.json eval-and-samples/results/baseline-longcat-20260828-run2.json eval-and-samples/results/baseline-run_eval-20260828.txt
git commit -m "chore(eval): LongCat-2.0 全量基线快照×2（抖动复核）+ 检索层 recall/T12 分布存档"
```

---

### Task 4: 归因清单 + BASELINE 冻结（执行任务）

**Files:**
- Create: `eval-and-samples/results/baseline-longcat-20260828-attribution.md`
- Modify: `eval-and-samples/BASELINE.md`
- Modify: `eval-and-samples/BASELINE.sha256`

**Interfaces:**
- Consumes: Task 3 的两个基线 JSON + run_eval 记录 + CI 状态
- Produces: 归因清单（Task 5 三个 sprint 的输入）；冻结的新四件套（Task 6 二次冻结的底本）

- [ ] **Step 1: 提取失败题清单（含引用明细）**

```bash
python - <<'EOF'
import json
d = json.load(open("eval-and-samples/results/baseline-longcat-20260828.json", encoding="utf-8"))
print("summary:", d["summary"])
for r in d["results"]:
    if not r["ok"] or (r.get("cit") and r["cit"][0] < r["cit"][1]):
        print(r["qid"], r["kind"], "|", r.get("why", ""), "|", " ".join(
            f"[{c['n']}]{c['sentence'][:20]}@{c['overlap']}" for c in r.get("cit_detail", [])))
EOF
```
Expected: 逐行失败题 + 每题引用点（句子前 20 字 + 重叠率）。高抖动题（run1/run2 结论不一致的）单独标注。

- [ ] **Step 2: 逐题归因标注**

写 `baseline-longcat-20260828-attribution.md`，按此表逐题填写（类别是 Task 5 冲刺决策树的输入）：

```markdown
# LongCat 基线归因清单（2026-08-28）

> 数据源：baseline-longcat-20260828.json（±run2 抖动复核）+ run_eval T12 分布 + CI eval job 状态
> CI 门禁状态：<绿/红 + 红因摘要>
> 高抖动题（run1/run2 结论不一致）：<题号列表，优化优先级降低>

| qid | kind | 失败摘要 | 归因类别 | 证据 | 入哪个 sprint |
|-----|------|---------|---------|------|--------------|
| Q0xx | qa | 断言1未忠实(窗口交集25%) | 生成不贴原文 / 判据过时 / 检索未召回（三选一，依据：chunks 是否含 GT 断言、overlap 数值） | chunks 摘录 + overlap | S3 / S1 |
| Q0xx | qa | 引用不合法 1/3 | 引用格式问题 / 生成不贴原文 | cit_detail | S1 |
| Q064 | refuse_qa | 误拒答(资料含答案仍拒答) | 检索未召回（top1<0.30）/ 判据过时（MIN_SCORE 边界） | 检索分数 | S2 |
```

归因判定规则（写入清单头部说明）：
- chunks 含 GT 断言核心词（`_chunks_have_answer` 语义）但回答缺 → 生成不贴原文
- chunks 不含 → 检索未召回
- 引用点 overlap 落在 0.20-0.35 且人工读句子确属实质支撑 → 判据过时（候选 D 级调参样本）
- 无 [来源N] 或 N 越界 → 引用格式问题

- [ ] **Step 3: 更新 BASELINE.md**

用实测数字替换（结构保持）：

```markdown
## 二、faithfulness 新基线（2026-08-28，LongCat-2.0 + 判定脚本@<git短hash>）

- qa：N/M = **xx.x%**（目标 ≥85%）
- refuse：N/M = **xx.x%**（目标 ≥90%）
- 引用合法率：N/M = **xx.x%**（目标 ≥95%）
- refuse_qa N/M（合理拒答 x、误拒答 y）；handoff N/N；chitchat N/N
- 口径：qa 分母剔除 refuse_qa；LongCat-2.0 + top_k=5 + eval_faithfulness.py@<hash> + KB「星河智家·官方政策库」
- 旧基线（2026-08-26，glm-4.5-air）：qa 82.1% / refuse 87.5% / 引用 21.4%——模型与判定脚本均变，**不可比**

## 四、门禁说明

- CI eval job 已是硬门禁（qa≥85% + refuse≥90%，抽样 20 题）；citation≥95% 已在全量模式判定
  （workflow_dispatch full_eval），抽样模式仅报告。指标达标前 full_eval 手动跑预期 FAIL（留数据不阻塞）。
```

同时更新「一、四件套冻结」表：模型行改 `LongCat-2.0（CHAT_PROVIDER=longcat）`，判定脚本行改当前 commit，并新增一行 `评测 KB | 星河智家·官方政策库（smoke_import 导入，与本地口径统一）`。

- [ ] **Step 4: 更新 BASELINE.sha256（补四件套 hash）**

```bash
cd eval-and-samples
sha256sum ../backend/scripts/eval_faithfulness.py
# 把输出行追加到 BASELINE.sha256，并在文件头注释补充：
#   2026-08-28 起冻结四件套：评测集(下三行) + 判定脚本(本行) + RETRIEVAL_TOP_K=5(config.py) + LongCat-2.0(.env)
```

- [ ] **Step 5: Commit**

```bash
git add eval-and-samples/BASELINE.md eval-and-samples/BASELINE.sha256 eval-and-samples/results/baseline-longcat-20260828-attribution.md
git commit -m "docs(eval): 冻结 LongCat-2.0 新基线四件套 + 逐题归因清单；修正 report-only 过时说法"
```

---

### Task 5: 三个专项冲刺（协议任务——按归因结果走决策树，循环执行）

**Files:**
- Modify（按分支）: `backend/app/prompts/qa_prompt.py`、`backend/scripts/eval_faithfulness.py`（判定器/rejudge）、`backend/app/services/steps/refuse.py`、`backend/app/core/config.py`（MIN_SCORE）、`eval-and-samples/kb/`（锚点补充）
- Test: `backend/tests/test_eval_faithfulness.py`（每题红测）、`backend/tests/test_prompt_qa.py`（若存在 prompt 结构测试则同步，改动前先 `grep -rn "规则12\|引用贴近" backend/tests/` 确认）

**Interfaces:**
- Consumes: Task 4 归因清单；Task 1 的 JSON（chunks 自含，rejudge 免重检索）
- Produces: 每冲刺一个 `eval-and-samples/results/sprint<N>-<date>.json` 全量回归快照 + BASELINE.md 增量更新

**冲刺循环（每个 sprint 重复，顺序 S1→S2→S3）：**

- [ ] **S-Step 1: 提取本 sprint 失败题**（从最新基线 JSON，命令同 Task 4 Step 1）
- [ ] **S-Step 2: 红测先行**——每道确定性失败题先落 `test_eval_faithfulness.py` 可复现断言（判定器纯函数直接测；管线行为用 `_chunks_have_answer`/`judge_qa` 组合测）；跑红
- [ ] **S-Step 3: 按决策树选手段**（见下，单一变量纪律：一次只动一处）
- [ ] **S-Step 4: 快跑验证**——指定题快跑（不重全量）：

```bash
cd backend && python - <<'EOF'
import asyncio
from scripts.eval_faithfulness import parse_questions, parse_ground_truth, eval_one_retry, _resolve_kb
from app.core.database import SessionLocal

qs = {q["qid"]: q for q in parse_questions()}
gt = parse_ground_truth()
targets = ["Q064"]  # ← 换成本轮失败题号


async def main():
    db = SessionLocal()
    kb = _resolve_kb(db, "星河智家·官方政策库")
    for qid in targets:
        r = await eval_one_retry(db, kb.id, qs[qid], gt.get(qid))
        print(qid, r["kind"], r["ok"], r.get("why", ""), "| chunks:", len(r.get("chunks", [])))
    db.close()


asyncio.run(main())
EOF
```

- [ ] **S-Step 5: 全量回归**——`python -m scripts.eval_faithfulness --kb-name "星河智家·官方政策库" --out ../eval-and-samples/results/sprint<N>-<date>.json`；与上一基线对比 summary，**任一指标下降 → 回退本批次**（`git bisect` 定位）
- [ ] **S-Step 6: 收尾**——BASELINE.md 增记本 sprint 数字与手段；commit

**Sprint 1 决策树（引用合法率 → ≥95%）：**

```
新基线 citation ≥95%？
├─ 是 → sprint 1 直接闭合（记录进 BASELINE），跳 Task 6
└─ 否 → 按归因类别：
   ├─ 引用格式问题（无[来源N]/N越界）→ 分支 A
   ├─ 生成不贴原文（改写致 overlap<0.30，人工读确属改写）→ 分支 A'
   ├─ 判据过时（overlap 0.20-0.35 且人工读属实质支撑）→ 分支 B
   └─ 检索未召回（chunks 无支撑句）→ 转入 Sprint 3 处理，本 sprint 不动检索
```

分支 A（prompt 加固，qa_prompt.py 规则 3 区块内追加，few-shot 正反例）：

```
3a. 反例（禁止）：把两条资料的信息合并成一句后只标一个来源（"金卡95折且积分抵现50% [来源1]"——
    后半句来自资料2 → 必须拆两句各自标注）。
3b. 正例（必须）：资料1"金卡会员享95折"、资料2"100积分=1元，抵现上限50%" →
    回答"金卡会员全场95折 [来源1]；100积分=1元，最多抵订单金额50% [来源2]"。
3c. [来源N] 的 N 必须引用当句信息真正所在的资料序号；句末紧跟，不得放在句中或段尾统一标注。
```

（改动后同步检查 prompt 相关既有测试；每轮只动 prompt 一处。）

分支 B（判定器校准，含 rejudge 双跑）——eval_faithfulness.py 改造：

```python
# 模块级常量（替换 _sentence_supported 里的硬编码 0.30）：
CITE_OVERLAP_MIN = 0.30  # 引用点重叠率下限（判定器调参唯一入口；改动须双跑留档+重冻结）

def _sentence_supported(sentence: str, chunk_text: str) -> bool:
    return _sentence_overlap(sentence, chunk_text) >= CITE_OVERLAP_MIN
```

`--rejudge` 模式（main() argparse 加 `--rejudge <json>`，与评测互斥；优先级最高，有值则只做重判）：

```python
def _rejudge(path: str) -> int:
    """对已存 JSON 重跑引用判定（判定器调参双跑工具）：不重检索、不重生成。

    用法：改 CITE_OVERLAP_MIN 前后各跑一次本命令，对比 TOTAL 行——新旧尺子对同一批
    回答的判定差异即"尺子变化"的净效应（spec D5，防改尺子凑分质疑）。
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    class _C:  # 最小 chunk 桩：judge_citations 只读 .text
        def __init__(self, text: str) -> None:
            self.text = text
    good = total = 0
    for r in data["results"]:
        if not r.get("cit"):
            continue
        _, g, t = judge_citations(r["answer"], [_C(c["text"]) for c in r["chunks"]])
        good += g
        total += t
        old_g, old_t = r["cit"][0], r["cit"][1]
        mark = "" if g == old_g else "  <-- CHANGED"
        print(f"{r['qid']}: {g}/{t}（原 {old_g}/{old_t}）{mark}")
    rate = good / total if total else 0.0
    print(f"TOTAL {good}/{total} = {rate:.1%}（CITE_OVERLAP_MIN={CITE_OVERLAP_MIN}）")
    return 0
```

分支 B 执行序：① 归因清单挑出"争议样本"（overlap 0.20-0.35）逐条人工核读，列表留档；② `--rejudge` 跑旧值 → 改 `CITE_OVERLAP_MIN`（如 0.30→0.25）→ 再跑，CHANGED 行须全部能对应争议样本；③ 红测锁定新阈值边界用例；④ 判定脚本变更 → 重冻结 BASELINE 四件套（Task 4 Step 4 同款流程）；⑤ 全量回归。**不允许**同时动 prompt（单一变量）。

**Sprint 2 决策树（拒答 ≥90%，先攻 Q064 类误拒答）：**

```
误拒答题（refuse_qa ok=False）诊断（快跑脚本打印该题 chunks 的 dense_score）：
├─ top1 dense < 0.30（检索没把资料带上来）→ 检索侧：
│   ├─ query_rewrite 改写后仍不命中 → 评估 rewrite 规则（改前跑 run_eval.py 的 rewrite ON/OFF 对比）
│   └─ KB 文档缺锚点词（如"重复扣款"不在 支付与退款.txt）→ 补 KB 内容（沿 cbfb14f 先例），
│       补后必须重跑 smoke_import 导入 + 更新 BASELINE.sha256（评测集四件套之外的第五维，单独留痕）
└─ top1 dense ≥ 0.30 仍拒（refuse.py 误判）→ MIN_SCORE 边界：
    ├─ 该题 top1 落在 0.30-0.35 → 评估 MIN_SCORE 微调（如 0.30→0.28），
    │   依据 run_eval 的 T12 分布（可答/拒答 top1 分桶的分离度）——不许拍脑袋调
    └─ 分离度不足（可答与拒答 top1 分布重叠大）→ MIN_SCORE 单阈值不够，
        改 margin 辅助（top1-top2 差值）进 refuse.py——此为本计划唯一架构级改动，须单独批次+红测
```

Q064 红测（Sprint 2 开工第一测，已知失败可直接写）：

```python
def test_q064_chunks_have_answer_guard():
    """Q064 误拒答回归锁：资料含'重复扣款 1-3 工作日原路退'时 _chunks_have_answer 必须为 True。

    Q064='钱扣了但订单没生成怎么办？'，GT='已扣款订单未生成，1-3 个工作日内自动原路退回'。
    该函数为 True 是把 Q064 归类为'误拒答'（而非'合理拒答'）的前提——
    若本测红，说明误拒答归因本身错位，须先修判定再谈管线。
    """
    claims = ["已扣款订单未生成，1-3 个工作日内自动原路退回"]
    chunk = _Chunk("重复扣款：若支付后订单未生成，已扣款项将在 1-3 个工作日内自动原路退回原支付账户。")
    assert _chunks_have_answer([chunk], claims)
```

漏拒答（refuse 类 ok=False，该拒没拒）：`judge_refuse` 判据与 qa_prompt 规则 1/5 **成对检查**——每批次要么两端同改（判定器+prompt），要么只改一端并在归因清单注明，防互相掩蔽。

**Sprint 3（faithfulness ≥85%）：** 无预设决策树，完全由归因清单驱动：判据过时 → Sprint 1 分支 B 同款流程；生成不贴原文 → prompt 手段复用（规则 10 并列逐条转述 / 规则 13 禁止条款类推 / 规则 11 防自拒正例的既有模式，按失败题补对应反例）；检索未召回 → Sprint 2 检索侧三分支同款。每题红测 → 快跑 → 全量回归。

**冲刺收尾统一门（每个 sprint 必过）：**

```bash
cd backend && ruff check app tests alembic scripts && python -m pytest tests/ -x -q
git add -A && git commit -m "fix(eval|prompt|rag): sprint<N> <手段摘要>——<指标变化，如 citation 21%→95%>"
```

---

### Task 6: full_eval 复核 + 二次冻结 + 过时文档同步（执行任务）

**Files:**
- Modify: `eval-and-samples/BASELINE.md`（二次冻结）
- Modify: `docs/multi-agent-collaboration-design.md`（状态标注）
- Modify: `docs/optimization-top3-todo.md`（状态标注）

**Interfaces:**
- Consumes: Task 2 的全量门禁（citation 参与判定）；Task 5 达标后的本地基线
- Produces: CI 上的全量绿证据；冻结的达标下限

- [ ] **Step 1: CI 全量复核**

```bash
gh workflow run CI --ref master -f full_eval=true
gh run watch  # 或轮询 gh run list --branch master --limit 1
```
Expected: eval job（含 citation≥95% 判定）PASS。若 FAIL：按日志失败题回 Task 5 对应 sprint（本地已过但 CI 挂 → 查 KB 导入差异 / Key 配置，不许改阈值迁就）。

- [ ] **Step 2: BASELINE 二次冻结**

BASELINE.md 门禁说明区追加：

```markdown
- **2026-08-xx 二次冻结（达标）**：qa xx.x% / refuse xx.x% / citation xx.x% 均达标，
  已由 CI full_eval 全量复核（run <id>）。此三项数字为**新下限**，后续任何改动跌破即 CI 红。
```

- [ ] **Step 3: 过时文档状态标注**

`docs/multi-agent-collaboration-design.md` 头部状态行改为：
`> 日期：2026-08-24 | 状态：已实施（2026-08-28 核实：Router/TicketAgent/ImageAgent/SharedContext 已接入 chat.py） | 项目：灵犀 Customer Service`

`docs/optimization-top3-todo.md` 头部插入：
`> **状态（2026-08-28 核实）：T1/T2/T3 均已完成**——T1 观测（admin.py topic_dist/tool 聚合）、T2 迁移 CI（ci.yml migrations job 含升降级对称）、T3 工具徽标（MessageBubble 消费 done.tool）。本文保留为历史规划。`

- [ ] **Step 4: 最终 Commit**

```bash
git add eval-and-samples/BASELINE.md docs/multi-agent-collaboration-design.md docs/optimization-top3-todo.md
git commit -m "docs(eval): 三指标达标二次冻结（新下限）+ 过时规划文档状态标注（多智能体已实施/T1-T3 完成）"
```

---

## 自查记录（Self-Review）

1. **Spec 覆盖**：阶段一（Task 1 工具 + Task 3 基线 + Task 4 归因/冻结）✅；阶段二三专项（Task 5，含 A1 新基线前提、A2 样本量决策、B1-B4、D1-D6 全部落点）✅；阶段三门禁补全（Task 2 + Task 6 Step 1）✅；§6 文档同步（Task 6 Step 3）✅；§5 YAGNI 边界未越（rejudge 挂在 Sprint 1 分支 B 条件执行，基线达标即死代码不落地）✅。
2. **占位符扫描**：Task 3/4 的 `<实测数字>`、`<git短hash>`、`<密码>` 为测量任务的数据回填位（代码/命令/模板全部具体）；无 "TBD/TODO/类似 Task N"。
3. **类型一致性**：`judge_citations(answer, chunks, detail=None) -> (bool, int, int)` 与既有测试解包一致；`_pass_all(stats, cit_good, cit_total, full_run)` 在 Task 2 定义、Task 6 复用；`res["cit"]` 保持 `(good, total, all_ok)` 三元组（JSON 序列化为 list，rejudge 取 `r["cit"][0]/[1]` 一致）；`stats[kind] == [total, ok]` 在 `_pass_all`/`_write_report` 中解包方向一致。
