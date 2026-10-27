"""HV7 C3a tests: resume, torn line, in-flight shutdown, owner-ref check, counter lock, ledger reconcile (tools/hv7_runner.py). SYNTHETIC tape and StubModel only:
no API call, no key, no network, no /data/mal path, no HV4/HV6/Q50 output. Nothing here says HV7 makes money."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from tools import hv7_runner as R
from tools.test_hv7_runner import (HOUR, MIN, T0, T_D1, T_E1, POOLS, _env, leader_first, make, rows_of)  # noqa: F401 - _env is an autouse fixture

START = T0 + HOUR
END = T_E1 + 15 * MIN

@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch):
    """The drill is 0 API: any socket connect in these tests fails the test (own guard, independent of the runner test file's fixture)."""
    import socket

    def no_net(*a, **k):
        raise AssertionError("network used in a drill test")

    monkeypatch.setattr(socket.socket, "connect", no_net)
    monkeypatch.setattr(socket, "create_connection", no_net)


def test_sockets_are_blocked_in_this_file():
    import socket

    with pytest.raises(AssertionError):
        socket.socket().connect(("127.0.0.1", 9))



class Kill(BaseException):
    """A kill -9 stand-in: not an Exception, so no handler in the runner can swallow it."""


@pytest.fixture(scope="module")
def mod_env(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    b = str(tmp_path_factory.getbasetemp())
    mp.setattr(R, "READ_ROOTS", (b,))
    mp.setattr(R, "WRITE_ROOTS", (b,))
    mp.setenv("MAL_ARENA_TEST", "1")
    yield
    mp.undo()


@pytest.fixture(scope="module")
def base(tmp_path_factory, mod_env):
    """The uninterrupted run, to END. md5 and rows are what every resumed run is compared with."""
    run = make(tmp_path_factory.mktemp("h7base"))
    run.run(START, END)
    assert len(rows_of(run, "U", "entry")) >= 3, "the synthetic tape must give a real period"
    return run


def rows_all(run):
    return {n: run.log.rows(n) for n in ("SCAN",) + R.ARM_ORDER}


def interrupted(tmp, kill_at, *, resume_at=None, mutate=None):
    """Run to kill_at (exclusive), drop the runner, optionally damage the log, build a NEW runner on the same dirs, resume, run to END."""
    first = make(tmp)
    first.run(START, kill_at)
    if mutate:
        mutate(first)
    second = make(tmp)
    nxt = second.resume(resume_at or kill_at)
    second.run(nxt, END)
    return first, second


# ------------------------------------------------------------------------------------------------------------------------ kill at each step
@pytest.mark.parametrize("name,kill_at", [("before_t_d", T_D1 - 5 * MIN), ("between_t_d_and_t_e", T_E1), ("positions_open", T_E1 + 3 * MIN)])
def test_kill_then_resume_gives_the_uninterrupted_md5(tmp_path, base, name, kill_at):
    first, second = interrupted(tmp_path, kill_at)
    assert second.log.md5() == base.log.md5(), name
    assert rows_all(second) == rows_all(base)
    ev = [json.loads(x) for x in (tmp_path / "v7" / "resume.jsonl").read_text().splitlines()]
    assert len(ev) == 1 and ev[0]["torn_lines"] == 0 and ev[0]["gap_rows"] == 0          # the resume event is outside the decision log (rule 6)
    if name == "between_t_d_and_t_e":
        assert ev[0]["pending_rebuilt"] is True
    if name == "positions_open":
        assert ev[0]["open_rebuilt"] >= 1


def test_between_t_d_and_t_e_reask_uses_the_same_request_so_the_cache_serves_it(tmp_path):
    seen = []
    first = make(tmp_path)
    orig = first.model.complete
    first.model.complete = lambda req, now: (seen.append(req.request_sha256()), orig(req, now))[1]
    first.run(START, T_E1)
    second = make(tmp_path)
    orig2 = second.model.complete
    second.model.complete = lambda req, now: (seen.append(req.request_sha256()), orig2(req, now))[1]
    second.run(second.resume(T_E1), T_E1 + MIN)
    assert len(seen) == 2 and seen[0] == seen[1]            # identical cache key: CallerModel(replay=True) answers from the saved reply, no second reservation
    assert len(rows_of(second, "L", "rank")) == 1


def test_positions_open_are_rebuilt_from_log_and_engine(tmp_path, base):
    first, second = interrupted(tmp_path, T_E1 + 3 * MIN)
    pids = {r["order_id"] for a in R.ARM_ORDER for r in rows_of(second, a, "entry")}
    sold = {r["pid"] for a in R.ARM_ORDER for r in rows_of(second, a, "sell")}
    assert pids and len(pids) == len({r["order_id"] for a in R.ARM_ORDER for r in rows_of(base, a, "entry")})
    assert set(second.open) == pids - sold                  # same open set as the uninterrupted run at END
    assert set(second.open) == set(base.open)


# ------------------------------------------------------------------------------------------------------------------------ torn line
def test_torn_last_line_is_truncated_and_recorded_as_a_gap(tmp_path, base):
    def tear(first):
        f = first.log.files("U")[-1]
        with open(f, "ab") as fh:
            fh.write(b'{"seq":99,"arm":"U","kind":"sell","pi')       # kill -9 mid-write
    first, second = interrupted(tmp_path, T_E1 + 3 * MIN, mutate=tear)
    gaps = [r for r in rows_of(second, "SCAN", "gap") if r["reason"] == "torn_last_line"]
    assert len(gaps) == 1 and gaps[0]["stream"] == "U" and gaps[0]["dropped_bytes"] == len(b'{"seq":99,"arm":"U","kind":"sell","pi')
    for n in ("SCAN",) + R.ARM_ORDER:
        second.log._head.pop(n, None)
        second.log.verify(n)                                # the chain is intact after the repair
    assert rows_all(second)["U"] == rows_all(base)["U"]    # nothing but the torn bytes was lost
    non_gap = [r for r in rows_all(second)["SCAN"] if r.get("kind") != "gap"]
    assert [r["kind"] for r in non_gap] == [r["kind"] for r in rows_all(base)["SCAN"]]
    assert second.log.md5() != base.log.md5()               # by rule: a gap row is in the log


def test_a_broken_chain_is_not_repaired(tmp_path):
    first = make(tmp_path)
    first.run(START, T_E1 + MIN)
    f = first.log.files("S")[-1]
    f.write_bytes(f.read_bytes().replace(b'"kind":"entry"', b'"kind":"entrz"', 1))
    second = make(tmp_path)
    with pytest.raises(R.LogChainError):
        second.resume(T_E1 + MIN)


# ------------------------------------------------------------------------------------------------------------------------ in flight, late restart
def test_restart_after_t_e_excludes_the_period_and_logs_a_rank_row(tmp_path):
    first = make(tmp_path)
    first.run(START, T_E1)                                  # killed between t_d and t_e
    second = make(tmp_path)
    nxt = second.resume(T_E1 + 30_000)
    second.run(nxt, END)
    rank = rows_of(second, "L", "rank")
    assert len(rank) == 1 and rank[0]["excluded"] is True and rank[0]["fallback"] is True and rank[0]["fallback_reason"] == "restart_after_t_e"
    assert [r["reason"] for r in rows_of(second, "SCAN", "period_excluded")] == ["restart_after_t_e"]
    assert all(not rows_of(second, a, "entry") for a in R.ARM_ORDER)        # no late entry in any arm
    assert second.counters.get("restart_after_t_e") == 1


def test_in_flight_call_at_shutdown_is_a_counted_l_fallback_and_resume_never_re_asks(tmp_path):
    first = make(tmp_path)
    first.run(START, T_E1)
    assert first.pending is not None
    first.pending["call"].done.clear()                      # the thread has not returned
    first.shutdown()
    assert first.pending is None and first.counters["shutdown_in_flight"] == 1
    r = rows_of(first, "L", "rank")
    assert len(r) == 1 and r[0]["fallback"] is True and not r[0].get("excluded") and r[0]["model_error"] == "shutdown_in_flight" and r[0]["cut_off"] is True
    assert not rows_of(first, "SCAN", "period_excluded")        # a fallback, not an exclusion
    second = make(tmp_path)
    calls = []
    second.model.complete = lambda *a, **k: calls.append(1)
    second.run(second.resume(T_E1), END)
    assert calls == [] and len(rows_of(second, "L", "rank")) == 1 and len(rows_of(second, "SCAN", "gap")) == 0
    ent = {a: rows_of(second, a, "entry") for a in R.ARM_ORDER}
    assert all(len(ent[a]) >= 1 and ent[a][0]["ms"] == T_E1 for a in R.ARM_ORDER)
    assert ent["L"][0]["pool"] == ent["R"][0]["pool"] == r[0]["pick"]          # L entered on its fallback (R's pick) at t_e


def test_reserve_row_without_cache_file_is_a_fallback_and_is_never_re_asked(tmp_path):
    """(b): the call was reserved and cut off. Count reserve rows before and after resume: no second paid call."""
    seen = []
    first = make(tmp_path)
    orig = first.model.complete
    first.model.complete = lambda req, now: (seen.append(req.request_sha256()), orig(req, now))[1]
    first.run(START, T_E1)
    led = tmp_path / "v7" / "ledgers" / R.RANK_LEDGER / (R.utc_day(T_D1) + ".jsonl")
    led.parent.mkdir(parents=True, exist_ok=True)
    led.write_text(json.dumps({"kind": "reserve", "reservation_id": "r1", "request_sha256": seen[0], "cost_usd": 0.01}) + "\n")
    assert not (tmp_path / "v7" / "cache" / R.RANK_LEDGER / (seen[0] + ".json")).exists()

    def reserves():
        return sum(1 for x in led.read_text().splitlines() if json.loads(x)["kind"] == "reserve")

    n0 = reserves()
    second = make(tmp_path)
    calls = []
    second.model.complete = lambda *a, **k: calls.append(1)
    second.run(second.resume(T_E1), END)
    assert calls == [] and reserves() == n0 == 1
    rank = rows_of(second, "L", "rank")
    assert len(rank) == 1 and rank[0]["fallback"] is True and rank[0]["model_error"] == "call_cut_off" and not rank[0].get("excluded")
    assert second.counters["call_cut_off"] == 1
    assert {a: rows_of(second, a, "entry")[0]["pool"] for a in "LR"}["L"] == rank[0]["pick"]
    assert all(rows_of(second, a, "entry") for a in R.ARM_ORDER)


def test_a_reserve_with_its_cache_file_is_re_asked_through_the_cache(tmp_path):
    seen = []
    first = make(tmp_path)
    orig = first.model.complete
    first.model.complete = lambda req, now: (seen.append(req.request_sha256()), orig(req, now))[1]
    first.run(START, T_E1)
    led = tmp_path / "v7" / "ledgers" / R.RANK_LEDGER / (R.utc_day(T_D1) + ".jsonl")
    led.parent.mkdir(parents=True, exist_ok=True)
    led.write_text(json.dumps({"kind": "reserve", "reservation_id": "r1", "request_sha256": seen[0], "cost_usd": 0.01}) + "\n")
    cache = tmp_path / "v7" / "cache" / R.RANK_LEDGER
    cache.mkdir(parents=True, exist_ok=True)
    (cache / (seen[0] + ".json")).write_text("{}")
    second = make(tmp_path)
    second.run(second.resume(T_E1), T_E1 + MIN)
    assert not rows_of(second, "L", "rank")[0].get("cut_off") and second.counters.get("call_cut_off", 0) == 0


def test_restart_with_entries_halt_enters_nothing_after_h_and_ends_flat(tmp_path):
    """DESIGN section 4: a restart carrying an entries halt. Positions are open at the restart; the halt falls before the next t_e."""
    halt = T_E1 + 30 * MIN
    first = make(tmp_path)
    first.run(START, T_E1 + 3 * MIN)
    second = make(tmp_path, cfg_kw={"entries_halt_ms": halt})
    nxt = second.resume(T_E1 + 3 * MIN)
    second.run(nxt, T_E1 + 3 * HOUR)
    assert second._stop and not second.open                               # ran until flat, then stopped itself
    assert [r["reason"] for r in rows_of(second, "SCAN", "run_complete")] == ["entries_halt_and_no_open_position"]
    assert all(r["ms"] < halt for a in R.ARM_ORDER for r in rows_of(second, a, "entry"))
    assert all(r["ms"] < halt for r in rows_of(second, "SCAN", "period"))     # no new screen after H
    assert rows_of(second, "U", "sell")


def test_shutdown_with_a_returned_reply_leaves_the_period_to_resume(tmp_path, base):
    first = make(tmp_path)
    first.run(START, T_E1)
    first.shutdown()
    assert first.pending is not None and not rows_of(first, "L", "rank")
    second = make(tmp_path)
    second.run(second.resume(T_E1), END)
    assert second.log.md5() == base.log.md5()


def test_kill_inside_enter_after_the_log_row_before_the_engine_buy(tmp_path):
    first = make(tmp_path)
    orig = first.engine.buy

    def buy(bot, *a, **k):
        if bot == R.bot_id("S"):
            raise Kill()                                    # S's entry row is in the log, its engine position is not
        return orig(bot, *a, **k)

    first.engine.buy = buy
    with pytest.raises(Kill):
        first.run(START, END)
    second = make(tmp_path)
    nxt = second.resume(T_E1 + 1000)
    gaps = rows_of(second, "SCAN", "gap")
    reasons = sorted(g["reason"] for g in gaps)
    assert "entry_not_in_engine" in reasons and reasons.count("entry_missing_after_restart") >= 1          # S and U never entered
    assert set(second.open) and all(second.open[p].arm in ("L", "R") for p in second.open)
    second.run(nxt, END)                                    # the run goes on; nothing enters late
    assert all(r["ms"] == T_E1 for a in R.ARM_ORDER for r in rows_of(second, a, "entry"))
    n = len(rows_of(second, "SCAN", "gap"))
    third = make(tmp_path)
    third.resume(END)
    assert len(rows_of(third, "SCAN", "gap")) == n          # a second resume does not repeat a gap row


# ------------------------------------------------------------------------------------------------------------------------ owner ref
@pytest.mark.parametrize("ref", ["q_6_kzYJva9XXuYQ", "n_-pn6x44cliJfQg", "n__xWQhqHQh0pbvw", "n_KF2tdyqfuCSYDg", "n_" + "a" * 12, "n_" + "a" * 20])
def test_owner_ref_accepts_notebook_and_question_ids(ref):
    assert R.validate_owner_ref(ref) == ref


@pytest.mark.parametrize("ref", ["", "yes", "n_", "q_", "q_short", "n_" + "a" * 11, "x_abcdefgh1234", "q_6_kzYJva9XXuYQ ", "q_6_kzYJva9 XXuYQ", "n_" + "a" * 21, "N_abcdefgh1234", None, 5, "n_abcdefgh1234\n"])
def test_owner_ref_refuses_everything_else(ref):
    with pytest.raises(R.Hv7Error):
        R.validate_owner_ref(ref)


def test_main_never_builds_caller_model_on_a_bad_ref(tmp_path, monkeypatch, capsys):
    import tools.hv6_agents as A

    def boom(*a, **k):
        raise AssertionError("CallerModel built")

    monkeypatch.setattr(A, "CallerModel", boom)
    monkeypatch.setattr(R, "Hv7Guard", boom)
    argv = ["--tape", str(tmp_path), "--root", str(tmp_path / "v7"), "--frozen-sha", "abcdef1", "--clock-start-ms", "1", "--entries-halt-ms", "2", "--feed-delay-slots", "2"]
    for ref in ("free text", "n_x"):
        assert R.main(argv + ["--owner-cost-line-ref", ref]) == 2
    assert "refused" in capsys.readouterr().err


# ------------------------------------------------------------------------------------------------------------------------ counters, ledger
def test_counters_are_locked(tmp_path):
    run = make(tmp_path)
    ts = [threading.Thread(target=lambda: [run._count("k") for _ in range(20_000)]) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert run.counters["k"] == 160_000


def test_request_cost_reads_reserve_and_settle_rows(tmp_path):
    g = R.Hv7Guard(tmp_path / "v7")
    d = tmp_path / "v7" / "ledgers" / R.RANK_LEDGER
    d.mkdir(parents=True)
    rows = [{"kind": "reserve", "reservation_id": "a", "request_sha256": "X", "cost_usd": 0.05}, {"kind": "settle", "reservation_id": "a", "cost_usd": -0.04},
            {"kind": "reserve", "reservation_id": "b", "request_sha256": "Y", "cost_usd": 0.02}]
    (d / "2026-10-27.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert g.request_cost("2026-10-27", "X") == pytest.approx(0.01)
    assert g.request_cost("2026-10-27", "Z") == 0.0
    assert g.request_cost("2026-10-28", "X") == 0.0


def test_rank_row_carries_the_ledger_figure_and_a_mismatch_is_counted(tmp_path, base):
    r = rows_of(base, "L", "rank")[0]
    assert r["ledger_cost_usd"] == 0.0 and r["cost_reconciled"] is True and r["replayed"] is False
    run = make(tmp_path, cost_usd=0.01)                     # the stub reports a cost but writes no ledger row: the reconcile must say so
    run.run(START, T_E1 + 10_000)
    r = rows_of(run, "L", "rank")[0]
    assert r["cost_usd"] == 0.01 and r["ledger_cost_usd"] == 0.0 and r["cost_reconciled"] is False
    assert run.counters["cost_mismatch"] == 1
