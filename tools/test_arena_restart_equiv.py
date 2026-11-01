"""Tests for tools/arena_restart_equiv.py (F3 (d)): the md5 restart-equivalence harness, run on a SYNTHETIC fixture only. No real tape, ledger, decision log or network; the harness is NOT run on
real data by this file.

  /data/mal/venv/bin/python -m pytest tools/test_arena_restart_equiv.py -q
"""
from __future__ import annotations

import collections
import json
import os
import re
from pathlib import Path

import pytest

os.environ.setdefault("MAL_ARENA_TEST", "1")

from tools import arena_restart_equiv as E  # noqa: E402
from tools import arena_tip_continuity as C  # noqa: E402

N_SLOTS = 1500
SPAN = N_SLOTS * E.SLOT_MS


@pytest.fixture(autouse=True)
def _plenty_of_disk(monkeypatch):
    monkeypatch.setattr("tools.arena_paper.shutil.disk_usage", lambda path: collections.namedtuple("Usage", "total used free")(1000, 500, 500))


@pytest.fixture(scope="module")
def spec(tmp_path_factory):
    work = tmp_path_factory.mktemp("equiv")
    base = 1_790_316_000_000 - (1_790_316_000_000 % E.H_MS) + E.H_MS - int(SPAN * 0.4)
    tape = work / "tape"
    E.write_tape(tape, E.synthetic_rows(base, N_SLOTS), compress_closed=True)
    return E.Spec(tape, E.synthetic_entries(base, N_SLOTS), work / "runs", rule=C.StopTargetTimeRule(max_hold_ms=SPAN // 6))


def test_d_replay_with_restart_has_the_same_md5_as_the_uninterrupted_run_at_every_kill_point(spec):
    n = sum(c for _, c in E.tape_plan(spec.tape_dir))
    rep = E.run_all(spec, [n // 12, n // 5, n // 3, n // 2, (2 * n) // 3, (7 * n) // 10], [0, 80, 400], ckpt_lag_rows=17)
    assert rep["schema"] == E.SCHEMA and len(rep["cases"]) == 18 and rep["all_equal"], [c["name"] for c in rep["cases"] if not c["equal"]]
    for c in rep["cases"]:
        b = c["per_bot"][E.BOT]
        assert b["equal_ordered"] and b["equal_set"] and c["fingerprint_equal"]
        assert b["reference"]["md5_ordered"] == b["restarted"]["md5_ordered"] and b["engine_restart_rows"] == 0
        assert b["start_records_restarted"] == 2 or c["open_at_restart"] == 0  # the restarted engine wrote its own start record: the restart is visible in the ledger
    assert any(c["open_at_restart"] >= 2 for c in rep["cases"]) and any(c["restore_counters"].get("exits") for c in rep["cases"])
    assert any(c["per_bot"][E.BOT]["kinds_reference"].get("excluded") for c in rep["cases"])  # the gap exclusion is replayed too


def test_d_a_zero_downtime_restart_equals_the_plain_reference_not_only_the_blind_one(spec):
    plan = E.tape_plan(spec.tape_dir)
    case = E.run_case(spec, plan, 500, 0, name="zero")
    plain = E.run_reference(spec, "plain")
    rows = E.book_rows(plain["engine"], E.BOT)
    assert case["per_bot"][E.BOT]["restarted"] == E.digests(rows)


def test_d_a_downtime_removes_only_the_entries_decided_in_it(spec):
    plan = E.tape_plan(spec.tape_dir)
    short = E.run_case(spec, plan, 100, 0, name="s")
    long = E.run_case(spec, plan, 100, 600, name="l")
    opens = lambda c: c["per_bot"][E.BOT]["kinds_reference"]["open"]  # noqa: E731
    assert opens(short) == len(spec.entries) and opens(long) < opens(short) and long["equal"]
    assert long["per_bot"][E.BOT]["kinds_restarted"] == long["per_bot"][E.BOT]["kinds_reference"]


def test_d_the_harness_sees_a_restart_that_changes_the_books(spec):
    """Mutant: the restarted engine decides its exits with a different rule than the uninterrupted one. The md5 must differ (the harness is not vacuous)."""
    plan = E.tape_plan(spec.tape_dir)
    n = sum(c for _, c in plan)
    kill, restart = E.cursor_at(plan, n // 4), E.cursor_at(plan, n // 4 + 50)
    mutant = E.Spec(spec.tape_dir, spec.entries, spec.work, rule=C.StopTargetTimeRule(max_hold_ms=SPAN // 6, target_pct=0.0004, stop_pct=-0.0004))
    bad = E.run_restart(mutant, kill, restart, "mut")
    good = E.run_reference(spec, "mutref", blind=bad["blind"])
    a, b = E.digests(E.book_rows(good["engine"], E.BOT)), E.digests(E.book_rows(bad["engine"], E.BOT))
    assert a["md5_ordered"] != b["md5_ordered"]


def test_d_a_restart_with_a_truncated_tape_is_refused_not_approximated(spec, tmp_path):
    plan = E.tape_plan(spec.tape_dir)
    kill = E.cursor_at(plan, 500)
    run = E.build_engine(spec, "trunc")
    drv = C.BookDriver(run, spec.rule, spec.entries)
    for c, row in C.iter_tape(spec.tape_dir, plan[0][0], kill):
        drv.feed(c, row)
    ck = drv.checkpoint()
    short = tmp_path / "short"
    short.mkdir()
    lines = (spec.tape_dir / f"tape-{plan[0][0]}.jsonl").read_bytes().splitlines(keepends=True) if (spec.tape_dir / f"tape-{plan[0][0]}.jsonl").exists() else None
    if lines is None:  # the first hour is compacted in this fixture: write the same rows back, minus the last 100
        rows = [r for _, r in C.iter_tape(spec.tape_dir, plan[0][0], E.cursor_at(plan, plan[0][1]))]
        E.write_tape(short, rows[: 500 - 100])
    else:
        (short / f"tape-{plan[0][0]}.jsonl").write_bytes(b"".join(lines[:400]))
    with pytest.raises(C.ContinuityRefused) as ei:
        C.restore_engine(E.build_engine(spec, "trunc", fresh=False), short, spec.entries, spec.rule, ck)
    assert ei.value.reason == "tape_shorter_than_checkpoint"


def test_d_the_report_holds_names_counts_and_md5s_only(spec):
    rep = E.run_all(spec, [300], [0], ckpt_lag_rows=3)
    text = json.dumps(rep, sort_keys=True)
    assert not re.search(r'"(PA|PB|PC|PD|MPA|sigPA\d+|stop|target|time)"', text)  # no pool, mint, signature or exit reason
    assert not re.search(r"\b[1-9]\d{12}\b", text)  # no epoch-ms instants except as counted durations (downtime_ms is < 10^12)
    assert set(rep) == {"schema", "tape_hours", "tape_rows", "entries", "bots", "rule", "margin_ms", "cases", "all_equal"}


def test_d_cursor_at_and_the_path_guard(spec, tmp_path):
    plan = E.tape_plan(spec.tape_dir)
    assert E.cursor_at(plan, 0) == C.Cursor(plan[0][0], 0) and E.cursor_at(plan, plan[0][1]) == C.Cursor(plan[0][0], plan[0][1])
    assert E.cursor_at(plan, plan[0][1] + 5) == C.Cursor(plan[1][0], 5)
    with pytest.raises(E.Refused):
        E.cursor_at(plan, sum(n for _, n in plan) + 1)
    for bad in ("/data/mal/arena/x", str(tmp_path / "arena-tipfeed" / "tape"), str(tmp_path / "tip-tape"), str(tmp_path / "forward-books"), "/var/lib/mal/sealed/h", str(tmp_path / "hv4-out")):
        with pytest.raises(E.Refused):
            E.check_path(bad, "x")
    assert E.check_path(tmp_path / "exploration-hours", "x")


def test_d_the_cli_selftest_writes_a_report_and_refuses_sealed_names(tmp_path, capsys):
    out = tmp_path / "r" / "report.json"
    rc = E.main(["--selftest", "--selftest-slots", "900", "--kill-rows", "200,500", "--downtime-rows", "0,100", "--work-dir", str(tmp_path / "w"), "--out", str(out)])
    assert rc == 0 and json.loads(out.read_text())["all_equal"] is True and "all_equal=True" in capsys.readouterr().out
    assert E.main(["--selftest", "--work-dir", str(tmp_path / "arena-tipfeed" / "w"), "--out", str(tmp_path / "o.json")]) == 2
    assert E.main(["--work-dir", str(tmp_path / "w2"), "--out", str(tmp_path / "o2.json")]) == 2  # no tape, no entries, no kill rows


def test_d_load_entries_accepts_decision_log_rows_and_plain_dicts(tmp_path):
    log = tmp_path / "log.jsonl"
    log.write_text("\n".join(json.dumps(r) for r in [{"seq": 1, "arm": "b1", "ms": 5, "kind": "entry", "pool": "P", "decision_slot": 9, "stake_sol": 0.25, "meta": {"a": 1}, "order_id": "o"},
                                                  {"seq": 2, "arm": "b1", "ms": 6, "kind": "exit", "pool": "P"}]) + "\n")
    got = E.load_entries(log)
    assert len(got) == 1 and got[0].bot_id == "b1" and got[0].slot == 9
    plain = tmp_path / "plain.jsonl"
    plain.write_text(json.dumps({"bot_id": "b2", "pool": "Q", "ms": 7, "slot": 3, "sol_in": 0.5, "meta": {}}) + "\n")
    assert E.load_entries(plain)[0].bot_id == "b2"
