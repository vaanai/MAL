"""Tests for tools/a3_halt.py and scripts/ops/a3-daily.sh (halt chaining). No network: the script runs with a stub python that skips the monitor."""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tools import a3_halt as h
from tools import arena_h5_score as sc

ROOT = Path(__file__).resolve().parent.parent
SHA = "1" * 40
REG_MS = 1_792_000_000_000
RUN_UTC_MS = 1_792_480_287_000


def make_registry(tmp_path: Path) -> Path:
    arena = tmp_path / "arena"
    arena.mkdir()
    path = arena / "bots.json"
    for gen in ("a1", "a2"):
        sc.register_books(path, frozen_sha=SHA, sockets=2, endpoints=["default"], host="t", clock_start_ms=REG_MS + 1000, now_ms=REG_MS, generation=gen)
    bots = sc.load_registry(path)
    bots["h5v1-boostfloor-a1"]["window_end_ms"] = REG_MS + 500  # a1 already ended earlier
    bots["h5v1-boostfloor-a1"]["stopped_ms"] = REG_MS + 500
    q50 = copy.deepcopy(bots["h5v1-boostfloor-a2"])
    bots["h5v1-q50-a2"] = q50  # a prefix the scorer's halt does not reach
    sc._atomic_write_json(path, {"bots": bots})
    return path


BOOKS = {"legacy_bot_ids": ["h5v1-boostfloor-a1", "h5v2-boostclock-a1"], "new_rule_bot_ids": ["h5v1-boostfloor-a2", "h5v2-boostclock-a2", "h5v1-q50-a2"]}


def ends(path):
    return {b: e["window_end_ms"] for b, e in sc.load_registry(path).items()}


def test_plan_pass_ends_only_legacy_books():
    v = {"verdict": "PASS", "flag_halt_ms": 5000, "halt_at_ms": None}
    assert h.plan(v, BOOKS) == [(5000, BOOKS["legacy_bot_ids"], "legacy")]


def test_plan_none_does_nothing():
    assert h.plan({"verdict": "NONE", "flag_halt_ms": None, "halt_at_ms": None}, BOOKS) == []


@pytest.mark.parametrize("verdict", ["FAIL", "ERROR", "HALT"])
def test_plan_hard_ends_both_with_their_own_instants(verdict):
    v = {"verdict": verdict, "flag_halt_ms": 9000, "halt_at_ms": 4000}
    assert h.plan(v, BOOKS) == [(9000, BOOKS["legacy_bot_ids"], "legacy"), (4000, BOOKS["new_rule_bot_ids"], "new_rule")]


def test_apply_fail_uses_redeploy_instant_for_new_books_and_covers_q50(tmp_path):
    path = make_registry(tmp_path)
    out = h.apply({"verdict": "FAIL", "flag_halt_ms": RUN_UTC_MS, "halt_at_ms": RUN_UTC_MS - 50_000_000}, BOOKS, path)
    e = ends(path)
    assert e["h5v1-boostfloor-a2"] == e["h5v2-boostclock-a2"] == e["h5v1-q50-a2"] == RUN_UTC_MS - 50_000_000
    assert e["h5v1-boostfloor-a1"] == REG_MS + 500  # earlier end is never moved later
    assert e["h5v2-boostclock-a1"] == RUN_UTC_MS
    assert out["applied"][0]["unchanged"] == ["h5v1-boostfloor-a1"]
    assert sc.load_registry(path)["h5v1-q50-a2"]["stopped_ms"] == RUN_UTC_MS - 50_000_000


def test_apply_pass_leaves_new_rule_books_open(tmp_path):
    path = make_registry(tmp_path)
    h.apply({"verdict": "PASS", "flag_halt_ms": RUN_UTC_MS, "halt_at_ms": None}, BOOKS, path)
    e = ends(path)
    assert e["h5v1-boostfloor-a2"] is None and e["h5v2-boostclock-a2"] is None and e["h5v1-q50-a2"] is None
    assert e["h5v2-boostclock-a1"] == RUN_UTC_MS


def test_unregistered_ids_are_reported_not_invented(tmp_path):
    path = make_registry(tmp_path)
    out = h.apply({"verdict": "HALT", "flag_halt_ms": 1, "halt_at_ms": 2}, {"new_rule_bot_ids": ["h5v1-boostfloor-a3"]}, path)
    assert out["applied"][0]["unregistered"] == ["h5v1-boostfloor-a3"] and "h5v1-boostfloor-a3" not in ends(path)


def test_halt_books_writes_exactly_what_set_window_end_writes(tmp_path):
    t1, t2 = tmp_path / "x", tmp_path / "y"
    t1.mkdir()
    t2.mkdir()
    p1, p2 = make_registry(t1), make_registry(t2)
    at = RUN_UTC_MS
    sc.set_window_end(p1, at)
    h.halt_books(p2, at, ["h5v1-boostfloor-a1", "h5v2-boostclock-a1", "h5v1-boostfloor-a2", "h5v2-boostclock-a2"])
    a, b = sc.load_registry(p1), sc.load_registry(p2)
    for bot in ("h5v1-boostfloor-a1", "h5v2-boostclock-a1", "h5v1-boostfloor-a2", "h5v2-boostclock-a2"):
        assert a[bot] == b[bot]
    assert a["h5v1-q50-a2"]["window_end_ms"] is None  # the scorer's own halt misses the other prefix: the reason for the explicit list


def test_cli_unreadable_inputs_exit_2(tmp_path):
    assert h.main(["--verdict", str(tmp_path / "no"), "--books", str(tmp_path / "no"), "--arena-root", str(tmp_path)]) == 2


# ---------------------------------------------------------------- the daily script, end to end (monitor stubbed, check and halt real)
def stub_python(tmp_path: Path) -> Path:
    stub = tmp_path / "py"
    stub.write_text(f"#!/bin/sh\nif [ \"$2\" = tools.pump_structure_monitor ]; then echo stub-monitor; exit 0; fi\nexec {sys.executable} \"$@\"\n")
    stub.chmod(0o755)
    return stub


def fresh_record(**flag_over):
    now = time.time()
    run_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    flags = {k: {"halt": False, "evaluated": True, "reason": "ok"} for k in ("pins_changed", "boost_disabled", "boost_share_low", "boost_last_slice_early",
                                                                            "boost_budget_or_slices_changed", "synthetic_share_high")}
    for k, v in flag_over.items():
        flags[k] = v
    return run_utc, {"run_utc": run_utc, "halt": {"flags": flags}, "accounts": {}, "programs": {}}


def run_script(tmp_path: Path, rec: dict):
    """research-0 half: monitor stubbed, check real. Returns (result, out dir)."""
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    daily = out / "daily.jsonl"
    daily.write_text(json.dumps(rec) + "\n")
    env = dict(os.environ, A3_REPO=str(ROOT), A3_PY=str(stub_python(tmp_path)), A3_DAILY=str(daily), A3_STATE=str(out / "state.jsonl"), A3_OUT=str(out))
    r = subprocess.run(["sh", str(ROOT / "scripts/ops/a3-daily.sh")], env=env, capture_output=True, text=True, timeout=120)
    return r, out


def decision_of(r) -> str:
    lines = [ln for ln in r.stdout.splitlines() if ln.startswith("A3_HALT_DECISION ")]
    assert len(lines) == 1, r.stdout + r.stderr
    return lines[0]


def run_apply(tmp_path: Path, line: str, books: dict, arena: Path):
    bf = tmp_path / "books.json"
    bf.write_text(json.dumps(books))
    env = dict(os.environ, A3_REPO=str(ROOT), A3_PY=sys.executable, A3_BOOKS=str(bf), A3_ARENA_ROOT=str(arena))
    return subprocess.run(["sh", str(ROOT / "scripts/ops/a3-apply-halt.sh"), line], env=env, capture_output=True, text=True, timeout=120)


def test_cross_host_hard_flag_halts_both_rule_groups_at_the_flag_instant(tmp_path):
    path = make_registry(tmp_path)
    run_utc, rec = fresh_record(boost_disabled={"halt": True, "evaluated": True, "reason": "x"})
    r, out = run_script(tmp_path, rec)
    assert r.returncode == 20 and "A3_HALT_NEEDED" in r.stdout, r.stdout + r.stderr
    assert not (out / "bots.json").exists()  # the research-0 half touches no arena file
    line = decision_of(r)
    a = run_apply(tmp_path, line, BOOKS, path.parent)
    assert a.returncode == 0, a.stdout + a.stderr
    from tools import a3_redeploy_check as c
    want = c.utc_ms(run_utc)
    at = sc.load_registry(path)
    assert at["h5v1-boostfloor-a2"]["window_end_ms"] == want and at["h5v1-q50-a2"]["window_end_ms"] == want and at["h5v2-boostclock-a1"]["window_end_ms"] == want
    assert json.loads((out / "a3-verdicts.jsonl").read_text().splitlines()[-1])["verdict"] == "HALT"
    # applying the same line again, or the bare JSON, changes nothing
    before = path.read_text()
    assert run_apply(tmp_path, line.split(" ", 1)[1], BOOKS, path.parent).returncode == 0 and path.read_text() == before


def test_cross_host_clean_record_prints_a_noop_decision(tmp_path):
    path = make_registry(tmp_path)
    before = path.read_text()
    _, rec = fresh_record()
    r, _ = run_script(tmp_path, rec)
    assert r.returncode == 0 and "A3_HALT_NEEDED" not in r.stdout
    line = decision_of(r)
    assert json.loads(line.split(" ", 1)[1])["verdict"] == "NONE"
    assert run_apply(tmp_path, line, BOOKS, path.parent).returncode == 0 and path.read_text() == before


def test_cross_host_stale_record_makes_no_decision(tmp_path):
    _, rec = fresh_record(boost_disabled={"halt": True, "evaluated": True, "reason": "x"})
    rec["run_utc"] = "2026-01-01T00:00:00Z"
    r, _ = run_script(tmp_path, rec)
    assert r.returncode == 11 and "A3_NO_VERDICT" in r.stdout and "A3_HALT_DECISION" not in r.stdout


@pytest.mark.parametrize("bad", ["", "not json", '{"verdict": "HALT", "halt_at_ms": 5, "flag_halt_ms": 5}', '{"verdict": "HALT", "flag_halt_ms": 1792480287000}',
                                 '{"verdict": "PASS", "flag_halt_ms": "1792480287000"}', '{"verdict": "MAYBE"}', '[1]'])
def test_malformed_decision_is_refused_and_writes_nothing(tmp_path, bad):
    path = make_registry(tmp_path)
    before = path.read_text()
    a = run_apply(tmp_path, "A3_HALT_DECISION " + bad if bad else "x", BOOKS, path.parent)
    assert a.returncode == 2 and path.read_text() == before


def test_older_decision_after_a_newer_one_cannot_move_a_window_later(tmp_path):
    path = make_registry(tmp_path)
    h.apply({"verdict": "FAIL", "flag_halt_ms": RUN_UTC_MS, "halt_at_ms": RUN_UTC_MS - 1000}, BOOKS, path)
    h.apply({"verdict": "FAIL", "flag_halt_ms": RUN_UTC_MS + 99_000, "halt_at_ms": RUN_UTC_MS + 99_000}, BOOKS, path)
    assert ends(path)["h5v1-boostfloor-a2"] == RUN_UTC_MS - 1000


def test_apply_script_needs_an_argument():
    r = subprocess.run(["sh", str(ROOT / "scripts/ops/a3-apply-halt.sh")], capture_output=True, text=True)
    assert r.returncode == 2


@pytest.mark.parametrize("name", ["a3-daily.sh", "a3-apply-halt.sh"])
def test_script_is_posix_sh(name):
    text = (ROOT / "scripts/ops" / name).read_text()
    assert text.startswith("#!/bin/sh")
    for bad in ("PIPESTATUS", "[[", "<(", "source ", "local ", "function ", "$'"):
        assert bad not in text
    r = subprocess.run(["sh", "-n", str(ROOT / "scripts/ops" / name)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_apply_half_imports_without_third_party_packages():
    """fast-0 has no /data/mal/venv: tools.a3_halt (via the scorer) must import on a bare interpreter. -S drops site-packages; stdlib only."""
    code = "import tools.a3_halt"
    r = subprocess.run([sys.executable, "-S", "-c", code], cwd=ROOT, capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=str(ROOT)))
    assert r.returncode == 0, r.stderr[-400:]
