"""Tests for tools/a3_halt.py (strict decisions and books, dead-man) and scripts/ops/a3-daily.sh (the whole fast-0 daily, monitor stubbed). No network."""
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
RUN_MS = 1_792_480_287_000  # 2026-10-20T07:11:27Z
RUN_UTC = "2026-10-20T07:11:27Z"
NOW = RUN_MS + 3600_000
DEPLOY_MS = 1_792_432_022_000  # 2026-10-19T17:47:02Z


def make_registry(tmp_path: Path, q50: bool = True) -> Path:
    arena = tmp_path / "arena"
    arena.mkdir()
    path = arena / "bots.json"
    for gen in ("a1", "a2"):
        sc.register_books(path, frozen_sha=SHA, sockets=2, endpoints=["default"], host="t", clock_start_ms=REG_MS + 1000, now_ms=REG_MS, generation=gen)
    bots = sc.load_registry(path)
    bots["h5v1-boostfloor-a1"]["window_end_ms"] = REG_MS + 500  # a1 already ended earlier
    bots["h5v1-boostfloor-a1"]["stopped_ms"] = REG_MS + 500
    if q50:
        bots["h5v1-q50-a2"] = copy.deepcopy(bots["h5v1-boostfloor-a2"])  # a prefix the scorer's halt does not reach
    sc._atomic_write_json(path, {"bots": bots})
    return path


BOOKS = {"legacy_bot_ids": ["h5v1-boostfloor-a1", "h5v2-boostclock-a1"], "new_rule_bot_ids": ["h5v1-boostfloor-a2", "h5v2-boostclock-a2", "h5v1-q50-a2"]}


def ends(path):
    return {b: e["window_end_ms"] for b, e in sc.load_registry(path).items()}


def verdict(v="NONE", flag=None, halt=None, run_utc=RUN_UTC, **extra):
    return {"run_utc": run_utc, "verdict": v, "flag_halt_ms": flag, "halt_at_ms": halt, "reasons": ["r"], "redeploys": [], "schema": "x", **extra}


# ---------------------------------------------------------------- B3: strict decisions
GOOD = [verdict(), verdict("PASS", flag=RUN_MS), verdict("PASS", flag=RUN_MS, covered=True), verdict("FAIL", flag=RUN_MS, halt=DEPLOY_MS), verdict("ERROR", flag=RUN_MS, halt=RUN_MS),
        verdict("HALT", flag=RUN_MS, halt=DEPLOY_MS), verdict("HALT", flag=None, halt=RUN_MS)]


@pytest.mark.parametrize("v", GOOD)
def test_valid_decisions_pass(v):
    assert h.validate_decision(h.decision_from_verdict(v), NOW)["verdict"] == v["verdict"]


BAD = {
    "pass_with_halt_at": verdict("PASS", flag=RUN_MS, halt=RUN_MS),
    "none_with_halt_at": verdict("NONE", halt=RUN_MS),
    "none_with_flag": verdict("NONE", flag=RUN_MS),
    "hard_without_halt_at": verdict("HALT", flag=RUN_MS),
    "fail_without_halt_at": verdict("FAIL", flag=RUN_MS),
    "unknown_verdict": verdict("MAYBE"),
    "year_5138": verdict("HALT", flag=RUN_MS, halt=99999999999999999),
    "future_instant": verdict("HALT", flag=NOW + 10**9, halt=NOW + 10**9),
    "pre_2020_instant": verdict("HALT", flag=RUN_MS, halt=5),
    "string_instant": verdict("HALT", flag=RUN_MS, halt=str(DEPLOY_MS)),
    "bool_instant": verdict("HALT", flag=RUN_MS, halt=True),
    "halt_after_flag": verdict("HALT", flag=DEPLOY_MS, halt=RUN_MS),
    "bad_run_utc": verdict("NONE", run_utc="yesterday"),
    "future_run_utc": verdict("NONE", run_utc="2099-01-01T00:00:00Z"),
    "reasons_not_list": {**verdict(), "reasons": "x"},
}


@pytest.mark.parametrize("name", sorted(BAD))
def test_inconsistent_decisions_are_refused(name):
    with pytest.raises(h.Invalid):
        h.validate_decision(h.decision_from_verdict(BAD[name]), NOW)


def test_missing_key_and_extra_keys():
    v = verdict()
    del v["redeploys"]
    with pytest.raises(h.Invalid):
        h.decision_from_verdict(v)
    d = h.decision_from_verdict(verdict())
    with pytest.raises(h.Invalid):
        h.validate_decision({**d, "extra": 1}, NOW)
    assert tuple(h.decision_from_verdict(verdict())) == h.DECISION_KEYS


def test_verdict_file_path_is_validated_too(tmp_path):
    path = make_registry(tmp_path)
    before = path.read_text()
    bf = tmp_path / "books.json"
    bf.write_text(json.dumps(BOOKS))
    vf = tmp_path / "v.json"
    vf.write_text(json.dumps(verdict("PASS", flag=RUN_MS, halt=RUN_MS)))  # the NOW check uses the real clock: the instants are in the past
    rc = h.main(["apply", "--verdict", str(vf), "--applied", str(tmp_path / "ap.jsonl"), "--books", str(bf), "--arena-root", str(path.parent)])
    assert rc == 2 and path.read_text() == before and not (tmp_path / "ap.jsonl").exists()


# ---------------------------------------------------------------- B2: books file
def reg(path):
    return list(sc.load_registry(path))


def test_books_schema_errors_write_nothing(tmp_path):
    path = make_registry(tmp_path)
    before = path.read_text()
    bad_books = {
        "empty": {},
        "misspelt_key": {"legacy_bot_ids": BOOKS["legacy_bot_ids"], "new_rule_ids": BOOKS["new_rule_bot_ids"]},
        "extra_key": {**BOOKS, "other": []},
        "not_lists": {"legacy_bot_ids": "h5v1-boostfloor-a1", "new_rule_bot_ids": []},
        "duplicate": {"legacy_bot_ids": ["h5v1-boostfloor-a1", "h5v1-boostfloor-a1"], "new_rule_bot_ids": BOOKS["new_rule_bot_ids"]},
        "in_both": {"legacy_bot_ids": BOOKS["legacy_bot_ids"] + ["h5v1-q50-a2"], "new_rule_bot_ids": BOOKS["new_rule_bot_ids"]},
        "unregistered_id": {"legacy_bot_ids": BOOKS["legacy_bot_ids"], "new_rule_bot_ids": BOOKS["new_rule_bot_ids"] + ["h5v1-boostfloor-a3"]},
        "h5_book_in_neither": {"legacy_bot_ids": BOOKS["legacy_bot_ids"], "new_rule_bot_ids": ["h5v1-boostfloor-a2", "h5v2-boostclock-a2"]},
        "not_an_object": [],
    }
    for name, b in bad_books.items():
        with pytest.raises(h.Invalid):
            h.apply(verdict("FAIL", flag=RUN_MS, halt=DEPLOY_MS), b, path, NOW)
        assert path.read_text() == before, name


def test_applicable_rule_with_empty_list_is_an_error(tmp_path):
    path = make_registry(tmp_path, q50=False)
    only_legacy = {"legacy_bot_ids": ["h5v1-boostfloor-a1", "h5v2-boostclock-a1", "h5v1-boostfloor-a2", "h5v2-boostclock-a2"], "new_rule_bot_ids": []}
    before = path.read_text()
    with pytest.raises(h.Invalid, match="new_rule_bot_ids is empty"):
        h.apply(verdict("FAIL", flag=RUN_MS, halt=DEPLOY_MS), only_legacy, path, NOW)
    assert path.read_text() == before
    # a PASS applies no new-rule step, so an empty new-rule list is fine for it
    assert h.apply(verdict("PASS", flag=RUN_MS), only_legacy, path, NOW)["applied"][0]["rule"] == "legacy"
    only_new = {"legacy_bot_ids": [], "new_rule_bot_ids": ["h5v1-boostfloor-a1", "h5v2-boostclock-a1", "h5v1-boostfloor-a2", "h5v2-boostclock-a2"]}
    with pytest.raises(h.Invalid, match="legacy_bot_ids is empty"):
        h.apply(verdict("PASS", flag=RUN_MS), only_new, path, NOW)


def test_a_none_decision_still_validates_the_books(tmp_path):
    path = make_registry(tmp_path)
    with pytest.raises(h.Invalid):
        h.apply(verdict(), {"legacy_bot_ids": [], "new_rule_bot_ids": []}, path, NOW)  # H5 books uncovered: loud even on a quiet day
    assert h.apply(verdict(), BOOKS, path, NOW)["applied"] == []


# ---------------------------------------------------------------- applying
def test_plan_pass_ends_only_legacy_books():
    d = h.decision_from_verdict(verdict("PASS", flag=5000 + h.MIN_MS))
    assert h.plan(d, BOOKS["legacy_bot_ids"], BOOKS["new_rule_bot_ids"]) == [(5000 + h.MIN_MS, BOOKS["legacy_bot_ids"], "legacy")]


@pytest.mark.parametrize("v", ["FAIL", "ERROR", "HALT"])
def test_hard_ends_both_with_their_own_instants(v):
    d = h.decision_from_verdict(verdict(v, flag=RUN_MS, halt=DEPLOY_MS))
    assert h.plan(d, BOOKS["legacy_bot_ids"], BOOKS["new_rule_bot_ids"]) == [(RUN_MS, BOOKS["legacy_bot_ids"], "legacy"), (DEPLOY_MS, BOOKS["new_rule_bot_ids"], "new_rule")]


def test_apply_fail_uses_redeploy_instant_for_new_books_and_covers_q50(tmp_path):
    path = make_registry(tmp_path)
    out = h.apply(verdict("FAIL", flag=RUN_MS, halt=DEPLOY_MS), BOOKS, path, NOW)
    e = ends(path)
    assert e["h5v1-boostfloor-a2"] == e["h5v2-boostclock-a2"] == e["h5v1-q50-a2"] == DEPLOY_MS
    assert e["h5v1-boostfloor-a1"] == REG_MS + 500  # an earlier end is never moved later
    assert e["h5v2-boostclock-a1"] == RUN_MS
    assert out["applied"][0]["unchanged"] == ["h5v1-boostfloor-a1"]
    assert sc.load_registry(path)["h5v1-q50-a2"]["stopped_ms"] == DEPLOY_MS


def test_apply_pass_leaves_new_rule_books_open(tmp_path):
    path = make_registry(tmp_path)
    h.apply(verdict("PASS", flag=RUN_MS), BOOKS, path, NOW)
    e = ends(path)
    assert e["h5v1-boostfloor-a2"] is None and e["h5v2-boostclock-a2"] is None and e["h5v1-q50-a2"] is None and e["h5v2-boostclock-a1"] == RUN_MS


def test_older_decision_after_a_newer_one_cannot_move_a_window_later(tmp_path):
    path = make_registry(tmp_path)
    h.apply(verdict("FAIL", flag=RUN_MS, halt=DEPLOY_MS), BOOKS, path, NOW)
    h.apply(verdict("FAIL", flag=RUN_MS + 9000, halt=RUN_MS + 9000), BOOKS, path, NOW)
    assert ends(path)["h5v1-boostfloor-a2"] == DEPLOY_MS


def test_halt_books_writes_exactly_what_set_window_end_writes(tmp_path):
    t1, t2 = tmp_path / "x", tmp_path / "y"
    t1.mkdir()
    t2.mkdir()
    p1, p2 = make_registry(t1), make_registry(t2)
    sc.set_window_end(p1, RUN_MS)
    h.halt_books(p2, RUN_MS, ["h5v1-boostfloor-a1", "h5v2-boostclock-a1", "h5v1-boostfloor-a2", "h5v2-boostclock-a2"])
    a, b = sc.load_registry(p1), sc.load_registry(p2)
    for bot in ("h5v1-boostfloor-a1", "h5v2-boostclock-a1", "h5v1-boostfloor-a2", "h5v2-boostclock-a2"):
        assert a[bot] == b[bot]
    assert a["h5v1-q50-a2"]["window_end_ms"] is None  # the scorer's own halt misses the other prefix: the reason for the explicit list


# ---------------------------------------------------------------- C-4 dead-man
def write_applied(path: Path, *run_utcs: str):
    path.write_text("".join(json.dumps({"run_utc": r, "verdict": "NONE"}) + "\n" for r in run_utcs))


def test_deadman_alive_no_anchor_and_fired(tmp_path):
    reg_path = make_registry(tmp_path)
    ap = tmp_path / "ap.jsonl"
    assert h.deadman(ap, BOOKS, reg_path, NOW)["deadman"].startswith("no anchor")
    write_applied(ap, "2026-10-19T07:11:27Z", RUN_UTC)
    assert h.deadman(ap, BOOKS, reg_path, NOW)["deadman"] == "alive"
    assert h.deadman(ap, BOOKS, reg_path, RUN_MS + h.DEADMAN_S * 1000)["deadman"] == "alive"  # exactly 48 h is not past 48 h
    late = RUN_MS + h.DEADMAN_S * 1000 + 1
    out = h.deadman(ap, BOOKS, reg_path, late)
    assert out["deadman"] == "FIRED" and out["end_ms"] == RUN_MS + h.DEADMAN_S * 1000
    e = ends(reg_path)
    assert e["h5v1-boostfloor-a2"] == e["h5v2-boostclock-a2"] == e["h5v1-q50-a2"] == RUN_MS + h.DEADMAN_S * 1000
    assert e["h5v2-boostclock-a1"] is None  # legacy books are not the dead-man's


def test_deadman_uses_the_newest_applied_run_and_never_moves_an_end_later(tmp_path):
    reg_path = make_registry(tmp_path)
    ap = tmp_path / "ap.jsonl"
    write_applied(ap, RUN_UTC)
    h.halt_books(reg_path, DEPLOY_MS, BOOKS["new_rule_bot_ids"])  # already ended earlier
    h.deadman(ap, BOOKS, reg_path, RUN_MS + h.DEADMAN_S * 1000 + 5)
    assert ends(reg_path)["h5v1-boostfloor-a2"] == DEPLOY_MS


def test_deadman_with_no_new_rule_books_is_an_error_when_due(tmp_path):
    reg_path = make_registry(tmp_path, q50=False)
    ap = tmp_path / "ap.jsonl"
    write_applied(ap, RUN_UTC)
    b = {"legacy_bot_ids": BOOKS["legacy_bot_ids"] + ["h5v1-boostfloor-a2", "h5v2-boostclock-a2"], "new_rule_bot_ids": []}
    assert h.deadman(ap, b, reg_path, NOW)["deadman"] == "alive"
    with pytest.raises(h.Invalid):
        h.deadman(ap, b, reg_path, RUN_MS + h.DEADMAN_S * 1000 + 5)


# ---------------------------------------------------------------- the daily script, end to end on a fixture (monitor stubbed; check, apply and dead-man real)
def stub_python(tmp_path: Path) -> Path:
    stub = tmp_path / "py"
    stub.write_text(f"#!/bin/sh\nif [ \"$2\" = tools.pump_structure_monitor ]; then echo stub-monitor; exit 0; fi\nexec {sys.executable} \"$@\"\n")
    stub.chmod(0o755)
    return stub


def fresh_record(**flag_over):
    run_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time()))
    flags = {k: {"halt": False, "evaluated": True, "reason": "ok"} for k in ("pins_changed", "boost_disabled", "boost_share_low", "boost_last_slice_early",
                                                                            "boost_budget_or_slices_changed", "synthetic_share_high")}
    flags.update(flag_over)
    return run_utc, {"run_utc": run_utc, "halt": {"flags": flags}, "accounts": {}, "programs": {}}


def run_script(tmp_path: Path, rec: dict | None, books: dict, arena: Path, *args: str):
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    if rec is not None:
        (out / "daily.jsonl").write_text(json.dumps(rec) + "\n")
    bf = tmp_path / "books.json"
    bf.write_text(json.dumps(books))
    env = dict(os.environ, A3_REPO=str(ROOT), A3_PY=str(stub_python(tmp_path)), A3_OUT=str(out), A3_BOOKS=str(bf), A3_ARENA_ROOT=str(arena), HOME=str(tmp_path / "home"))
    r = subprocess.run(["sh", str(ROOT / "scripts/ops/a3-daily.sh"), *args], env=env, capture_output=True, text=True, timeout=120)
    return r, out


def test_script_hard_flag_halts_both_rule_groups_without_a_manager(tmp_path):
    path = make_registry(tmp_path)
    run_utc, rec = fresh_record(boost_disabled={"halt": True, "evaluated": True, "reason": "x"})
    r, out = run_script(tmp_path, rec, BOOKS, path.parent)
    assert r.returncode == 0, r.stdout + r.stderr  # a decision was made AND applied: exit 0
    assert "A3_HALT_APPLIED" in r.stdout
    from tools import a3_redeploy_check as c
    want = c.utc_ms(run_utc)
    at = ends(path)
    assert at["h5v1-boostfloor-a2"] == want and at["h5v1-q50-a2"] == want and at["h5v2-boostclock-a1"] == want
    assert json.loads((out / "a3-verdicts.jsonl").read_text().splitlines()[-1])["verdict"] == "HALT"
    assert json.loads((out / "a3-applied.jsonl").read_text().splitlines()[-1])["run_utc"] == run_utc  # the dead-man anchor


def test_script_clean_record_halts_nothing_and_leaves_an_anchor(tmp_path):
    path = make_registry(tmp_path)
    before = path.read_text()
    run_utc, rec = fresh_record()
    r, out = run_script(tmp_path, rec, BOOKS, path.parent)
    assert r.returncode == 0 and "A3_HALT_APPLIED" not in r.stdout and path.read_text() == before
    assert json.loads((out / "a3-applied.jsonl").read_text())["run_utc"] == run_utc


def test_script_stale_record_is_loud_halts_nothing_and_leaves_no_anchor(tmp_path):
    path = make_registry(tmp_path)
    before = path.read_text()
    _, rec = fresh_record(boost_disabled={"halt": True, "evaluated": True, "reason": "x"})
    rec["run_utc"] = "2026-01-01T00:00:00Z"
    r, out = run_script(tmp_path, rec, BOOKS, path.parent)
    assert r.returncode == 11 and "A3_NO_VERDICT" in r.stdout and path.read_text() == before and not (out / "a3-applied.jsonl").exists()


def test_script_bad_books_file_is_a_nonzero_exit_and_writes_nothing(tmp_path):
    path = make_registry(tmp_path)
    before = path.read_text()
    _, rec = fresh_record(boost_disabled={"halt": True, "evaluated": True, "reason": "x"})
    r, _ = run_script(tmp_path, rec, {"legacy_bot_ids": [], "new_rule_ids": []}, path.parent)
    assert r.returncode == 2 and path.read_text() == before


def test_script_deadman_fires_first_and_the_deadman_mode_needs_no_record(tmp_path):
    path = make_registry(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3 * 86400))
    write_applied(out / "a3-applied.jsonl", old)
    r, _ = run_script(tmp_path, None, BOOKS, path.parent, "deadman")
    assert r.returncode == 0 and "FIRED" in r.stdout, r.stdout + r.stderr
    from tools import a3_redeploy_check as c
    assert ends(path)["h5v1-boostfloor-a2"] == c.utc_ms(old) + h.DEADMAN_S * 1000 and ends(path)["h5v2-boostclock-a1"] is None


@pytest.mark.parametrize("name", ["a3-daily.sh"])
def test_script_is_posix_sh(name):
    text = (ROOT / "scripts/ops" / name).read_text()
    assert text.startswith("#!/bin/sh")
    for bad in ("PIPESTATUS", "[[", "<(", "source ", "local ", "function ", "$'"):
        assert bad not in text
    assert subprocess.run(["sh", "-n", str(ROOT / "scripts/ops" / name)], capture_output=True, text=True).returncode == 0
    assert not (ROOT / "scripts/ops/a3-apply-halt.sh").exists()


def test_apply_half_imports_without_third_party_packages():
    """fast-0 has no /data/mal/venv: tools.a3_halt (via the scorer) must import on a bare interpreter. -S drops site-packages; stdlib only."""
    r = subprocess.run([sys.executable, "-S", "-c", "import tools.a3_halt, tools.a3_redeploy_check"], cwd=ROOT, capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=str(ROOT)))
    assert r.returncode == 0, r.stderr[-400:]
