"""HV7's REAL runner (tools/hv7_runner.py) across a restart, with the F3 continuity wired in from the outside (tools/hv7_restart_equiv.py). SYNTHETIC tape and StubModel only (the fixtures of
tools/test_hv7_runner.py): no API call, no key, no network, no /data/mal path, no real tape, ledger or decision log. Nothing here says HV7 makes money.

  /data/mal/venv/bin/python -m pytest tools/test_hv7_restart_equiv.py -q
"""
from __future__ import annotations

import collections
import json
from pathlib import Path

import pytest

from tools import arena_tip_continuity as C
from tools import hv7_restart_equiv as H
from tools import hv7_runner as R
from tools.test_hv7_runner import (HOUR, MIN, T0, T_E1, _env, make)  # noqa: F401 - _env is an autouse fixture

START = T0 + HOUR
END = T_E1 + 65 * MIN


@pytest.fixture(autouse=True)
def _plenty_of_disk(monkeypatch):
    monkeypatch.setattr("tools.arena_paper.shutil.disk_usage", lambda path: collections.namedtuple("Usage", "total used free")(1000, 500, 500))


def factory(d: Path, cls: type):
    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(R, "Hv7Runner", cls)  # tools.test_hv7_runner.make builds R.Hv7Runner
        return make(d)
    finally:
        mp.undo()


def case(tmp_path, kill, restart, **kw):
    return H.run_hv7_case(factory, tmp_path, START, kill, restart, END, **kw)


def test_b4_the_real_runner_restarted_has_the_ledger_md5_of_the_uninterrupted_one_with_the_same_blind_interval(tmp_path):
    kill, restart = T_E1 + 6 * MIN, T_E1 + 9 * MIN
    res = case(tmp_path, kill, restart)
    assert res["open_at_kill"] >= 3, res
    for arm in R.ARM_ORDER:
        assert res["restarted"][arm]["engine_restart_rows"] == 0
    assert res["equal"], {a: (res["reference"][a], res["restarted"][a]) for a in R.ARM_ORDER}
    assert res["sells_in_blind_interval"] == 0
    assert sum(res["restarted"][a]["trade_rows"] for a in R.ARM_ORDER) > 0


def test_b4_zero_downtime_and_a_restart_before_the_entries_are_also_equal(tmp_path):
    for kill, restart in ((T_E1 + 4 * MIN, T_E1 + 4 * MIN), (T_E1 - 2 * MIN, T_E1 - MIN)):
        res = case(tmp_path / f"k{kill}", kill, restart)
        assert res["equal"], (kill, restart)


def test_b4_the_legacy_resume_marks_the_same_positions_engine_restart(tmp_path):
    """The contrast: HV7's own resume() (engine.recover()) books the positions that are open at the kill `engine_restart` once the runner sells them."""
    kill, restart = T_E1 + 6 * MIN, T_E1 + 9 * MIN
    first = factory(tmp_path, R.Hv7Runner)
    first.run(START, kill)
    second = factory(tmp_path, R.Hv7Runner)
    nxt = second.resume(restart)
    second.run(nxt, END)
    n = sum(1 for arm in R.ARM_ORDER for r in second.engine.ledger.read_bot(R.bot_id(arm)) if r.get("exclusion_reason") == "engine_restart")
    assert n > 0


def test_b2_a_sell_logged_but_not_priced_at_the_kill_is_used_as_logged(tmp_path):
    # find a kill instant at which a sell is decided and pending (the engine waits for the print that prices it)
    probe = factory(tmp_path / "probe", H.Hv7ContinuityRunner).configure()
    kill = None
    for T in range(START, END, 1000):
        probe.tick(T)
        if sum(len(v) for v in probe.engine.pending.values()):
            kill = T + 1000
            break
    assert kill is not None, "the synthetic tape never produced a pending sell"
    res = case(tmp_path / "pend", kill, kill + 2 * MIN)
    assert res["pending_sells_at_kill"] >= 1 and res["equal"], res
    # the journal is lost with the kill: the same positions are booked engine_restart (fail closed), never re-derived
    lost = case(tmp_path / "lost", kill, kill + 2 * MIN, journal_mode="lost")
    assert not lost["equal"] and any(lost["restarted"][a]["engine_restart_rows"] for a in R.ARM_ORDER), lost
    assert sum(lost["restarted"][a]["engine_restart_rows"] for a in R.ARM_ORDER) % 3 == 0  # whole positions (3 stakes each)


def test_b3_a_stale_checkpoint_with_a_pricing_after_it_is_refused_and_a_tampered_one_too(tmp_path):
    kill, restart = T_E1 + 20 * MIN, T_E1 + 22 * MIN
    fresh = case(tmp_path / "fresh", kill, restart)
    assert fresh["equal"]
    with pytest.raises(C.ContinuityRefused) as ei:  # an older checkpoint: something was priced after it
        case(tmp_path / "stale", kill, restart, ckpt_lag_events=3)
    assert ei.value.reason in ("priced_after_checkpoint", "no_checkpoint_that_old")
    # a tampered fingerprint is a state mismatch, whatever the runner does next
    first = factory(tmp_path / "t", H.Hv7ContinuityRunner).configure(C.SellJournal(tmp_path / "t" / "j.jsonl"))
    first.run(START, T_E1 + 6 * MIN)
    ck = dict(first.recorder.history[-1], fp="0" * 32)
    second = factory(tmp_path / "t", H.Hv7ContinuityRunner).configure(C.SellJournal(tmp_path / "t" / "j.jsonl"), ck)
    nxt = second.resume(T_E1 + 7 * MIN)
    with pytest.raises(C.ContinuityRefused) as ei2:
        second.run(nxt, END)
    assert ei2.value.reason == "state_mismatch"
    assert not any(r.get("kind") == "sell" and r["ms"] >= T_E1 + 7 * MIN for a in R.ARM_ORDER for r in second.log.rows(a))  # nothing was decided on the unverified state


def test_b1_no_sell_is_decided_in_the_downtime_and_the_report_is_names_counts_and_md5s(tmp_path):
    kill, restart = T_E1 + 6 * MIN, T_E1 + 14 * MIN
    res = case(tmp_path, kill, restart)
    assert res["equal"] and res["sells_in_blind_interval"] == 0
    rep = {k: v for k, v in res.items() if k != "runner"}
    assert set(json.dumps(rep)) and "Pxx" not in json.dumps(rep)
