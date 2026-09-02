"""eval error 率门禁单测（防分母缩水假绿，BASELINE.md §四 2026-08-30 登记）。

纯函数 gate() 直测 + CLI exit code 集成（tmp_path 假 JSON，无 DB/无网络）。
fail-closed 语义：results 空 / 条目缺 kind / JSON 不可解析 → 一律 FAIL。
"""

from __future__ import annotations

import json

from scripts.eval_gate_error_rate import gate, main


def mk_results(n_ok: int, n_err: int, n_skip: int = 0) -> list[dict]:
    out = [{"qid": f"q{i}", "kind": "qa", "ok": True, "why": "", "answer": "a"} for i in range(n_ok)]
    out += [{"qid": f"e{i}", "kind": "error", "ok": False, "why": "HTTPStatusError: 402", "answer": ""} for i in range(n_err)]
    out += [{"qid": f"s{i}", "kind": "skip", "ok": False, "why": "无 ground-truth", "answer": ""} for i in range(n_skip)]
    return out


# ---------- 纯函数 gate ----------


def test_gate_pass_no_errors():
    ok, note = gate(mk_results(20, 0))
    assert ok
    assert "0/20" in note


def test_gate_pass_at_threshold():
    # 4/20 = 20% 恰好等于上限 → PASS（<= 语义）
    ok, note = gate(mk_results(16, 4))
    assert ok
    assert "20.0%" in note


def test_gate_fail_above_threshold():
    # 5/20 = 25% > 20% → FAIL（BASELINE.md §四 场景：15 题 402 缩分母假绿）
    ok, _note = gate(mk_results(15, 5))
    assert not ok


def test_gate_fail_all_errors():
    ok, _note = gate(mk_results(0, 20))
    assert not ok


def test_gate_fail_empty_results():
    ok, note = gate([])
    assert not ok
    assert "fail-closed" in note


def test_gate_counts_missing_kind_as_error():
    # 条目缺 kind 字段（形态异常）→ 按 error 计（防字段改名静默放行）：
    # 15 ok + 5 缺字段 = 5/20 = 25% > 20% → FAIL，note 体现 5/20
    results = mk_results(15, 0)
    results += [{"qid": f"w{i}", "ok": True} for i in range(5)]
    ok, note = gate(results)
    assert not ok
    assert "5/20" in note


def test_gate_skip_not_counted_as_error():
    # skip（无 ground-truth）是预期剔除，不算 error，但计入分母
    ok, note = gate(mk_results(19, 0, n_skip=1))
    assert ok
    assert "skip 1" in note


def test_gate_custom_threshold():
    # 阈值可调：10% 上限下 3/20 = 15% → FAIL（2/20 恰好 10% 仍 PASS，<= 语义）
    ok, _ = gate(mk_results(17, 3), max_error_rate=0.10)
    assert not ok
    ok2, _ = gate(mk_results(18, 2), max_error_rate=0.10)
    assert ok2


# ---------- CLI exit code ----------


def test_cli_pass_exit_zero(tmp_path, capsys):
    p = tmp_path / "eval.json"
    p.write_text(json.dumps({"summary": {}, "results": mk_results(20, 0)}), encoding="utf-8")
    assert main([str(p)]) == 0
    assert "PASS" in capsys.readouterr().out


def test_cli_fail_exit_one(tmp_path):
    p = tmp_path / "eval.json"
    p.write_text(json.dumps({"summary": {}, "results": mk_results(15, 5)}), encoding="utf-8")
    assert main([str(p)]) == 1


def test_cli_missing_file_fails(capsys):
    assert main(["/nonexistent/eval.json"]) == 1
    assert "fail-closed" in capsys.readouterr().out


def test_cli_unparseable_json_fails(capsys, tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    assert main([str(p)]) == 1
    assert "fail-closed" in capsys.readouterr().out


def test_cli_missing_results_key_fails(tmp_path, capsys):
    # JSON 可解析但缺 results 键 → fail-closed return 1（防上游字段改名静默跳过门禁）
    p = tmp_path / "noresults.json"
    p.write_text(json.dumps({"summary": {}}), encoding="utf-8")
    assert main([str(p)]) == 1
    assert "fail-closed" in capsys.readouterr().out


def test_cli_no_args_fails(capsys):
    assert main([]) == 1
    assert "fail-closed" in capsys.readouterr().out
