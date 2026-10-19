"""Memory bounds of the HV4 runner (the #1236 stub OOM, 2026-10-19). Synthetic tapes only: no network, no API call, no key, no real tape.

What is bounded here: QuoteHistory (it kept every row of a pool with fewer than 4,096 rows and never forgot a pool). What is deliberately NOT bounded here: the
engine's print history (arena_paper.PoolHist). The runner does not call ArenaEngine.prune(): QP 6274522375 showed a pruned pool is re-created with last_v_row None,
so a revival print with V outside [0, 30 SOL] is priced `none` (a missing_v exclusion) where the unbounded engine prices it from pool_prev. The engine bound waits for
a state-preserving trim in tools/arena_paper.py (its own branch and review). These tests pin:
  * QuoteHistory stays under explicit caps over a long synthetic run while the unbounded setting grows without limit;
  * the quote trim and sweep change no answer, and the sweep never drops a pool the state still holds (the state's own 60 s sweep gap);
  * the runner leaves the engine alone: every print of every pool, with its (slot, v, vsrc) and last_v_row, is the same with bound_memory on and off, on a tape where
    a pool goes quiet for longer than the engine's own keep window and comes back with out-of-band V prints; and the decision log, the engine ledger (mint included)
    and the state digest are byte-identical.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tools import hv4_runner as R
from tools.arena_paper import HIST_KEEP_SLOTS
from tools.hv4_memreplay import ledger_digest
from tools.test_hv4_runner import H, MIN, MINTS, POOLS, SOL, T0, V, WSOL, X, make, trade_rows, write_tape


@pytest.fixture(scope="module", autouse=True)
def _arena_test_env():
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("MAL_ARENA_TEST", "1")
        yield


SLOT0 = 400_000_000
MS_PER_SLOT = 400
KEEP_MS = HIST_KEEP_SLOTS * MS_PER_SLOT  # 8,000 s: the engine's own prune() keeps a quiet pool this long (at 0.4 s a slot)


def slot_of(t: int) -> int:
    return SLOT0 + (t - (T0 - 3 * H)) // MS_PER_SLOT


def print_row(pool: str, mint: str, t: int, n: int, k: int = 0, vq: int | None = None) -> dict:
    """One PumpSwap WSOL print of a flat 1.0e-6 price pool (the shape of tools/test_hv4_runner.trade_rows). `vq` overrides virtual_quote_reserves."""
    buy_side = n % 4 != 3
    sol = int((0.15 if buy_side else 0.07) * SOL)
    p = 1e-6
    q, b = 60 * SOL, int((60 * SOL + V) / SOL / p * 1_000_000)
    return {"v": 1, "type": "trade", "prog": "pumpswap", "venue": "pumpswap", "slot": slot_of(t), "t_recv_ms": t, "signature": f"m{k}-{n}", "event_index": 0,
            "pool": pool, "mint": mint, "trader": f"B{n % 7}", "side": "buy" if buy_side else "sell", "sol_lamports": sol,
            "token_raw": int(sol / SOL / p * 1_000_000), "quote_reserve": q, "base_reserve": b, "virtual_quote_reserves": V if vq is None else vq, "quote_mint": WSOL,
            "event_ts": t // 1000}


def noise_pool(i: int) -> tuple[str, str]:
    return f"NzPool{i:05d}" + "1" * 30, f"NzMint{i:05d}" + "1" * 30


def churn_rows(n_pools: int, start: int, every_ms: int = MIN, prints: int = 10, span_ms: int = 3 * MIN) -> list[dict]:
    """A new pool every `every_ms`, each printing `prints` times over `span_ms`, then never again."""
    rows = []
    for i in range(n_pools):
        pool, mint = noise_pool(i)
        t0 = start + i * every_ms
        rows += [print_row(pool, mint, t0 + j * span_ms // prints + 7, j, i) for j in range(prints)]
    return sorted(rows, key=lambda r: (r["t_recv_ms"], r["signature"]))


def feed(run: R.Runner, rows: list[dict], sample_every: int = 500) -> dict[str, int]:
    """Apply the rows through the state (so the engine and the quote history see them, as in the loop) and record the peaks."""
    peak = {"engine_pools": 0, "engine_rows": 0, "quote_pools": 0, "quote_rows": 0}
    for i, r in enumerate(rows):
        run.state.apply(r)
        if i % sample_every == 0:
            peak["engine_pools"] = max(peak["engine_pools"], len(run.engine.pools))
            peak["engine_rows"] = max(peak["engine_rows"], sum(len(h.slot) for h in run.engine.pools.values()))
            peak["quote_pools"] = max(peak["quote_pools"], len(run.quotes.t))
            peak["quote_rows"] = max(peak["quote_rows"], sum(len(v) for v in run.quotes.t.values()))
    return peak


def hist_prints(run: R.Runner) -> dict[str, tuple]:
    """Every print the engine holds, per pool: the order keys, slot, V and V source of each, and the pool's last in-band V."""
    return {k: (tuple(h.keys), tuple(h.slot), tuple(h.v), tuple(h.vsrc), h.last_v_row, h.mint) for k, h in sorted(run.engine.pools.items())}


# ------------------------------------------------------------------------------------------------------------------------------ the quote bounds hold
N_POOLS, EVICT_MIN = 720, 30  # 12 hours of tape time, a new pool a minute; the state evicts a key idle for 30 minutes


def long_run(tmp: Path, bound: bool) -> tuple[R.Runner, dict[str, int], list[dict]]:
    rows = churn_rows(N_POOLS, T0 - 3 * H + MIN)
    write_tape(tmp / "tape", [print_row(POOLS[0], MINTS[0], T0 - 3 * H + 1, 0)])  # a one-row tape: the state is fed by hand below
    run = make(tmp)
    run.cfg.bound_memory = bound
    run.state.evict_idle_ms = EVICT_MIN * MIN
    return run, feed(run, rows), rows


def test_long_synthetic_run_keeps_quote_history_under_its_cap(tmp_path):
    run, peak, rows = long_run(tmp_path, True)
    # a quote row list is kept for the state's eviction horizon plus one housekeeping interval, plus the 3 minutes it prints for
    quote_cap = (EVICT_MIN * MIN + R.HOUSEKEEP_EVERY_MS + 3 * MIN) // MIN + 2
    assert peak["quote_pools"] <= quote_cap, peak
    assert peak["quote_rows"] <= quote_cap * 10, peak
    assert N_POOLS > 10 * quote_cap // 2  # the run is long enough for the bound to bite
    assert run.counters["quote_pools_swept"] >= N_POOLS - quote_cap
    assert not run.hook_errors
    # the engine is untouched: every pool and every print is still there (its bound is not this module's)
    assert len(run.engine.pools) == N_POOLS and sum(len(h.slot) for h in run.engine.pools.values()) == len(rows)
    assert "engine_pools_pruned" not in run.counters


def test_the_unbounded_setting_grows_without_limit(tmp_path):
    """The control: the same run with bound_memory off keeps every quote row (the 2026-10-19 behaviour)."""
    run, peak, rows = long_run(tmp_path, False)
    assert len(run.quotes.t) == N_POOLS and sum(len(v) for v in run.quotes.t.values()) == len(rows)
    assert peak["quote_pools"] > 3 * ((EVICT_MIN * MIN + R.HOUSEKEEP_EVERY_MS + 3 * MIN) // MIN)


def test_housekeeping_runs_on_the_tape_clock_not_the_loop(tmp_path):
    """Rows applied in one burst (the warm-up of a start) are swept as they are read; the cadence is the rows' t_recv_ms, 5 minutes apart."""
    write_tape(tmp_path / "tape", [print_row(POOLS[0], MINTS[0], T0 - 3 * H + 1, 0)])
    run = make(tmp_path)
    calls = []
    real = run.quotes.sweep
    run.quotes.sweep = lambda now_ms, idle_ms, **kw: calls.append(now_ms) or real(now_ms, idle_ms, **kw)
    base = T0 - 3 * H
    for i in range(0, 31 * 60):  # 31 minutes of one row a second
        run.state.apply(print_row(POOLS[0], MINTS[0], base + i * 1000 + 5, i))
    assert len(calls) == 7  # the first row, then each 5 minute boundary of the tape clock (5, 10, 15, 20, 25, 30)
    run.cfg.bound_memory = False
    n = len(calls)
    run.state.apply(print_row(POOLS[0], MINTS[0], base + 40 * MIN, 99999))
    assert len(calls) == n


def test_the_runner_never_calls_the_engines_prune(tmp_path):
    """QP 6274522375: prune() is not pricing-equivalent (a re-created pool loses last_v_row). It stays out of the runner until the state-preserving trim lands."""
    write_tape(tmp_path / "tape", [print_row(POOLS[0], MINTS[0], T0 - 3 * H + 1, 0)])
    run = make(tmp_path)

    def boom(*a, **k):
        raise AssertionError("the runner must not call ArenaEngine.prune")

    run.engine.prune = boom
    feed(run, churn_rows(40, T0 - 3 * H + MIN))
    run.state.apply(print_row(POOLS[0], MINTS[0], T0 + 5 * H, 1))  # five hours on: far past the engine's keep window
    assert not run.hook_errors and len(run.engine.pools) == 41


def test_a_housekeeping_failure_never_skips_the_rows_engine_or_h5_feed(tmp_path):
    """Reviewer finding on #744: _housekeep has its own try, so an exception in it cannot skip the H5 feed of the same row."""

    class FakeH5:
        def __init__(self) -> None:
            self.rows = 0

        def feed(self, row):
            self.rows += 1
            return []

    write_tape(tmp_path / "tape", [print_row(POOLS[0], MINTS[0], T0 - 3 * H + 1, 0)])
    h5 = FakeH5()
    run = make(tmp_path, h5=h5, lane_h=True)

    def boom(*a, **k):
        raise RuntimeError("housekeeping failed")

    run.quotes.sweep = boom  # fails after _housekeep has moved its next-run instant, as a real failure would
    base = T0 - 3 * H
    for i in range(0, 12 * 60):  # 12 minutes of one row a second: housekeeping fires at the first row and at each 5 minute boundary
        run.state.apply(print_row(POOLS[0], MINTS[0], base + i * 1000 + 5, i))
    assert h5.rows == 12 * 60
    assert run.engine.hw_ms is not None and sum(len(h.slot) for h in run.engine.pools.values()) == 12 * 60
    assert run.hook_errors and set(run.hook_errors) == {"housekeep:RuntimeError"} and len(run.hook_errors) == 3


def test_quote_history_trims_small_pools_and_keeps_every_answer(tmp_path):
    """Trimming a pool of fewer than 4,096 rows (the old gate) changes no ref() or peak() answer for a query at or after the newest row."""
    keep = 65 * MIN
    a, b = R.QuoteHistory(keep), R.QuoteHistory(keep)
    base = 1_790_000_000_000 - 1_790_000_000_000 % MIN
    for i in range(0, 400):
        a.add("P", base + i * 45_000, 1.0 + 0.001 * (i % 37))  # the old gate (4,096 rows): never trims here
        b.add("P", base + i * 45_000, 1.0 + 0.001 * (i % 37), R.QUOTE_PRUNE_MIN_ROWS)
    assert len(b.t["P"]) < len(a.t["P"]) == 400
    for k in range(399 * 45_000 // MIN + 1, 399 * 45_000 // MIN + 5):  # queries after the newest row (299.25 minutes in)
        T = base + k * MIN + 2_000
        assert a.ref("P", T) == b.ref("P", T) and a.peak("P", T) == b.peak("P", T)


def test_quote_sweep_drops_only_pools_idle_past_the_horizon_and_not_held_by_the_state():
    q = R.QuoteHistory()
    q.add("old", 1_000, 1.0)
    q.add("old_held", 1_000, 1.0)
    q.add("new", 9_000_000, 1.0)
    held = {"old_held"}
    assert q.sweep(10_000_000, idle_ms=5_000_000, held=lambda k: k in held) == 1
    assert sorted(q.t) == ["new", "old_held"] and sorted(q.q) == ["new", "old_held"]
    held.clear()
    assert q.sweep(10_000_000, idle_ms=5_000_000, held=lambda k: k in held) == 1 and list(q.t) == ["new"]
    assert q.sweep(10_000_000, idle_ms=5_000_000, held=lambda k: False) == 0


def test_a_pool_the_state_still_holds_keeps_its_quotes_when_it_trades_inside_the_state_sweep_gap(tmp_path):
    """QP 6274522375 (a): ArenaState evicts on its own 60 s sweep, the runner sweeps quotes at a 5 minute tape boundary, so for up to 60 s a pool can be past the
    quote horizon while the state still holds it. If it trades then, it keeps its first-seen age and can be carded, and its 15-min-ago quote must be what the unbounded
    history says. The state sweep is switched off here to hold that gap open for the whole run."""
    pool, mint = noise_pool(0)
    other, other_mint = noise_pool(1)
    base = T0 - 3 * H
    rows = [print_row(pool, mint, base + j * 30_000 + 7, j, 1) for j in range(20)]  # prints for 10 minutes, then silence
    rows += [print_row(other, other_mint, base + 10 * MIN + j * 30_000 + 9, j, 2) for j in range(100)]  # keeps the tape clock moving for 50 minutes
    rows += [print_row(pool, mint, base + 62 * MIN + j * 30_000 + 7, 100 + j, 3) for j in range(10)]  # trades again, past the 30 minute horizon
    rows.sort(key=lambda r: (r["t_recv_ms"], r["signature"]))
    T = base + 67 * MIN + R.SCAN_SECOND_MS
    answers = {}
    for bound in (True, False):
        write_tape(tmp_path / str(bound) / "tape", [print_row(POOLS[0], MINTS[0], base + 1, 0)])
        run = make(tmp_path / str(bound))
        run.cfg.bound_memory = bound
        run.state.evict_idle_ms = 30 * MIN
        run.state.sweep = lambda: 0  # the state keeps every key: the gap between "past the horizon" and "evicted" never closes
        feed(run, rows)
        assert run.state.pool(pool) is not None
        answers[bound] = (run.quotes.ref(pool, T), run.quotes.peak(pool, T), pool in run.quotes.t)
    assert answers[True] == answers[False] and answers[True][0] is not None and answers[True][2]
    # and the other half: once the state does drop the key, the sweep forgets it
    del run.state.sweep  # back to the class method
    run.state.now_ms += 10 * H
    run.state.sweep()
    assert run.state.pool(pool) is None
    run.cfg.bound_memory = True
    run._housekeep(run.state.now_ms)
    assert pool not in run.quotes.t


# ------------------------------------------------------------------------------------------------------------------------------ decisions and pricing are unchanged
def churn_and_return_rows() -> list[dict]:
    """The recorded two-pool tape (pullbacks, entries, target and stop exits) plus pools that print early and go quiet for longer than the engine's own keep window,
    and one of them (RET) comes back 2.5 hours later, first with prints whose V is outside [0, 30 SOL] (they are priced from the pool's earlier V, `pool_prev`)."""
    rows = trade_rows()
    rows += churn_rows(12, X - 100 * MIN, every_ms=2 * MIN)  # quiet for > 8,000 s well before the run ends
    ret_pool, ret_mint = "RetPool" + "1" * 37, "RetMint" + "1" * 37
    rows += [print_row(ret_pool, ret_mint, X - 100 * MIN + j * 30_000, j, 777) for j in range(20)]
    rows += [print_row(ret_pool, ret_mint, X + 60 * MIN + j * 30_000, j, 778, vq=-1_000_000_000 if j < 25 else None) for j in range(20, 60)]  # back, V out of band at first
    return sorted(rows, key=lambda r: (r["t_recv_ms"], r["signature"]))


def run_digest(tmp: Path, bound: bool) -> tuple[str, str, dict, R.Runner]:
    run = make(tmp, rows=churn_and_return_rows(), lane_h=False)
    run.cfg.bound_memory = bound
    run.state.evict_idle_ms = 120 * MIN  # shorter than the default, so the state evicts the quiet pools (idle 150 to 166 min) and the quote sweep has work; the main pools' 110 min start-up silence is under it
    run.run(T0 - 20 * MIN + R.SCAN_SECOND_MS, T0 + 100 * MIN)
    ledger = ledger_digest(tmp / "ledger")  # strict: the mint field is included
    return run.log.md5(), ledger, run.state_digest(T0 + 100 * MIN), run


def test_decision_log_ledger_prints_and_state_digest_are_identical_with_the_bounds_on_and_off(tmp_path):
    a = run_digest(tmp_path / "bounded", True)
    b = run_digest(tmp_path / "unbounded", False)
    ra, rb = a[3], b[3]
    assert a[:2] == b[:2]
    da, db = a[2], b[2]
    assert {k: v for k, v in da.items() if k != "quotes"} == {k: v for k, v in db.items() if k != "quotes"}
    assert da["quotes"] and {p: v for p, v in db["quotes"].items() if p in da["quotes"]} == da["quotes"]  # every quote answer the bounded history still gives is the unbounded one
    assert hist_prints(ra) == hist_prints(rb)  # every print the engine holds: order key, slot, V, V source, last in-band V, mint
    # the revival really exercised the pool_prev fallback, and the engine kept the old history across the gap
    ret = rb.engine.pools["RetPool" + "1" * 37]
    assert ret.vsrc.count("pool_prev") == 5 and ret.vsrc.count("row") == len(ret.vsrc) - 5 and len(ret.slot) == 60
    # the run did trade, and the quote trim did act: otherwise the equalities above would be vacuous
    assert len({r["pool"] for arm in ("CON-X", "BIG-X") for r in ra.log.rows(arm) if r["kind"] == "entry"}) >= 2
    assert sum(len(v) for v in ra.quotes.t.values()) < sum(len(v) for v in rb.quotes.t.values())  # the 64-row trim acted on the two long pools
    assert ra.counters["quote_pools_swept"] >= 13 and "quote_pools_swept" not in rb.counters  # the sweep acted: 12 noise pools and RET (idle > 120 min, evicted by the state)
    assert len(ra.quotes.t) < len(rb.quotes.t)
    assert not ra.hook_errors and not rb.hook_errors
