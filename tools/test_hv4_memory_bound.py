"""Memory bounds of the HV4 runner (the #1236 stub OOM, 2026-10-19). Synthetic tapes only: no network, no API call, no key, no real tape.

The runner fed every tape row to ArenaEngine, which keeps every print of every pool (arena_paper.PoolHist) until ArenaEngine.prune() is called, and nothing
called it; QuoteHistory kept every row of a pool with fewer than 4,096 rows and never forgot a pool. These tests pin:
  * the bounds hold over a long synthetic run (pools, prints and quote rows stay under explicit caps while the unbounded setting grows without limit);
  * the bounds do not change a decision: the decision log, the engine ledger and the state digest are byte-identical with bound_memory on and off, on a tape where
    pools do go quiet for longer than the engine's keep window, one of them holds an open position, and one comes back after the window.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools import hv4_runner as R
from tools.arena_paper import HIST_KEEP_SLOTS
from tools.test_hv4_runner import (H, MIN, POOLS, SOL, T0, V, WSOL, X, MINTS, hour_name, make, price, trade_rows, write_tape)


@pytest.fixture(scope="module", autouse=True)
def _arena_test_env():
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("MAL_ARENA_TEST", "1")
        yield


SLOT0 = 400_000_000
MS_PER_SLOT = 400
KEEP_MS = HIST_KEEP_SLOTS * MS_PER_SLOT  # 8,000 s: the engine keeps a quiet pool this long (at 0.4 s a slot)


def slot_of(t: int) -> int:
    return SLOT0 + (t - (T0 - 3 * H)) // MS_PER_SLOT


def print_row(pool: str, mint: str, t: int, n: int, k: int = 0) -> dict:
    """One PumpSwap WSOL print of a flat 1.0e-6 price pool (the shape of tools/test_hv4_runner.trade_rows)."""
    buy_side = n % 4 != 3
    sol = int((0.15 if buy_side else 0.07) * SOL)
    p = 1e-6
    q, b = 60 * SOL, int((60 * SOL + V) / SOL / p * 1_000_000)
    return {"v": 1, "type": "trade", "prog": "pumpswap", "venue": "pumpswap", "slot": slot_of(t), "t_recv_ms": t, "signature": f"m{k}-{n}", "event_index": 0,
            "pool": pool, "mint": mint, "trader": f"B{n % 7}", "side": "buy" if buy_side else "sell", "sol_lamports": sol,
            "token_raw": int(sol / SOL / p * 1_000_000), "quote_reserve": q, "base_reserve": b, "virtual_quote_reserves": V, "quote_mint": WSOL, "event_ts": t // 1000}


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


# ------------------------------------------------------------------------------------------------------------------------------ the bounds hold
N_POOLS, EVICT_MIN = 720, 30  # 12 hours of tape time, a new pool a minute; the state evicts a key idle for 30 minutes


def long_run(tmp: Path, bound: bool) -> tuple[R.Runner, dict[str, int], list[dict]]:
    rows = churn_rows(N_POOLS, T0 - 3 * H + MIN)
    write_tape(tmp / "tape", [print_row(POOLS[0], MINTS[0], T0 - 3 * H + 1, 0)])  # a one-row tape: the state is fed by hand below
    run = make(tmp)
    run.cfg.bound_memory = bound
    run.state.evict_idle_ms = EVICT_MIN * MIN
    return run, feed(run, rows), rows


def test_long_synthetic_run_stays_under_the_caps(tmp_path):
    run, peak, rows = long_run(tmp_path, True)
    # a pool is kept by the engine for the keep window after its last print, plus up to one housekeeping interval, plus the 3 minutes it prints for
    engine_cap = (KEEP_MS + R.HOUSEKEEP_EVERY_MS + 3 * MIN) // MIN + 2
    assert peak["engine_pools"] <= engine_cap, peak
    assert peak["engine_rows"] <= engine_cap * 10, peak
    # a quote row list is kept for the state's eviction horizon plus one housekeeping interval, plus the 3 minutes it prints for
    quote_cap = (EVICT_MIN * MIN + R.HOUSEKEEP_EVERY_MS + 3 * MIN) // MIN + 2
    assert peak["quote_pools"] <= quote_cap, peak
    assert peak["quote_rows"] <= quote_cap * 10, peak
    assert N_POOLS > 3 * engine_cap  # the run is long enough for the bound to bite
    assert run.counters["engine_pools_pruned"] >= N_POOLS - engine_cap and run.counters["quote_pools_swept"] >= N_POOLS - quote_cap
    assert not run.hook_errors


def test_the_unbounded_setting_grows_without_limit(tmp_path):
    """The control: the same run with bound_memory off keeps every pool and every print (the 2026-10-19 behaviour)."""
    run, peak, rows = long_run(tmp_path, False)
    assert len(run.engine.pools) == N_POOLS and peak["engine_pools"] > 3 * ((KEEP_MS + R.HOUSEKEEP_EVERY_MS + 3 * MIN) // MIN)
    assert sum(len(h.slot) for h in run.engine.pools.values()) == len(rows)
    assert len(run.quotes.t) == N_POOLS and sum(len(v) for v in run.quotes.t.values()) == len(rows)


def test_housekeeping_runs_on_the_tape_clock_not_the_loop(tmp_path):
    """Rows applied in one burst (the warm-up of a start) are pruned as they are read; the cadence is the rows' t_recv_ms, 5 minutes apart."""
    write_tape(tmp_path / "tape", [print_row(POOLS[0], MINTS[0], T0 - 3 * H + 1, 0)])
    run = make(tmp_path)
    calls = []
    real = run.engine.prune
    run.engine.prune = lambda: calls.append(run.engine.hw_ms) or real()
    base = T0 - 3 * H
    for i in range(0, 31 * 60):  # 31 minutes of one row a second
        run.state.apply(print_row(POOLS[0], MINTS[0], base + i * 1000 + 5, i))
    assert len(calls) == 7  # the first row, then each 5 minute boundary of the tape clock (5, 10, 15, 20, 25, 30)
    run.cfg.bound_memory = False
    n = len(calls)
    run.state.apply(print_row(POOLS[0], MINTS[0], base + 40 * MIN, 99999))
    assert len(calls) == n


def test_quote_history_trims_small_pools_and_keeps_every_answer(tmp_path):
    """Trimming a pool of fewer than 4,096 rows (the old gate) changes no ref() or peak() answer for a query at or after the newest row."""
    keep = 65 * MIN
    a, b = R.QuoteHistory(keep), R.QuoteHistory(keep)
    old_gate = R.QUOTE_PRUNE_MIN_ROWS
    base = 1_790_000_000_000 - 1_790_000_000_000 % MIN
    try:
        R.QUOTE_PRUNE_MIN_ROWS = 10 ** 9  # never trims: the reference
        for i in range(0, 400):
            a.add("P", base + i * 45_000, 1.0 + 0.001 * (i % 37))
    finally:
        R.QUOTE_PRUNE_MIN_ROWS = old_gate
    for i in range(0, 400):
        b.add("P", base + i * 45_000, 1.0 + 0.001 * (i % 37))
    assert len(b.t["P"]) < len(a.t["P"]) == 400
    for k in range(399 * 45_000 // MIN + 1, 399 * 45_000 // MIN + 5):  # queries after the newest row (299.25 minutes in)
        T = base + k * MIN + 2_000
        assert a.ref("P", T) == b.ref("P", T) and a.peak("P", T) == b.peak("P", T)


def test_quote_sweep_drops_only_pools_idle_past_the_horizon():
    q = R.QuoteHistory()
    q.add("old", 1_000, 1.0)
    q.add("new", 9_000_000, 1.0)
    assert q.sweep(10_000_000, idle_ms=5_000_000) == 1 and list(q.t) == ["new"] and list(q.q) == ["new"]
    assert q.sweep(10_000_000, idle_ms=5_000_000) == 0


# ------------------------------------------------------------------------------------------------------------------------------ decisions are unchanged
def churn_and_return_rows() -> list[dict]:
    """The recorded two-pool tape (pullbacks, entries, target and stop exits) plus pools that print early and go quiet for longer than the engine's keep window,
    and one of them (RET) comes back 3 hours later with a price path of its own."""
    rows = trade_rows()
    rows += churn_rows(12, X - 100 * MIN, every_ms=2 * MIN)  # quiet for > 8,000 s well before the run ends
    ret_pool, ret_mint = "RetPool" + "1" * 37, "RetMint" + "1" * 37
    rows += [print_row(ret_pool, ret_mint, X - 100 * MIN + j * 30_000, j, 777) for j in range(20)]
    rows += [print_row(ret_pool, ret_mint, X + 60 * MIN + j * 30_000, j, 778) for j in range(20, 60)]  # back after > the keep window
    return sorted(rows, key=lambda r: (r["t_recv_ms"], r["signature"]))


def run_digest(tmp: Path, bound: bool) -> tuple[str, str, str, R.Runner]:
    run = make(tmp, rows=churn_and_return_rows(), lane_h=False)
    run.cfg.bound_memory = bound
    run.run(T0 - 20 * MIN + R.SCAN_SECOND_MS, T0 + 100 * MIN)
    ledger = hashlib.md5()
    for f in sorted((tmp / "ledger").rglob("*")):
        if f.is_file():
            ledger.update(f.relative_to(tmp / "ledger").as_posix().encode() + b"\0")
            for line in f.read_bytes().splitlines():
                row = json.loads(line)
                if row.get("kind") == "start":
                    continue  # the engine's process-start record: a fresh uuid and the wall clock, not a function of the tape
                row.pop("engine_id", None)
                ledger.update(R.canon(row).encode() + b"\n")
    digest = hashlib.md5(R.canon(run.state_digest(T0 + 100 * MIN)).encode()).hexdigest()
    return run.log.md5(), ledger.hexdigest(), digest, run


def test_decision_log_ledger_and_state_digest_are_identical_with_the_bounds_on_and_off(tmp_path):
    a = run_digest(tmp_path / "bounded", True)
    b = run_digest(tmp_path / "unbounded", False)
    ra, rb = a[3], b[3]
    assert a[:3] == b[:3]
    # the bound did act on this tape, and the run did trade: otherwise the equality above would be vacuous
    assert ra.counters.get("engine_pools_pruned", 0) >= 12 and "engine_pools_pruned" not in rb.counters
    assert len(ra.engine.pools) < len(rb.engine.pools)
    opens = [r for arm in ("CON-X", "BIG-X") for r in ra.log.rows(arm) if r["kind"] == "entry"]
    assert len({r["pool"] for r in opens}) >= 2
    assert not ra.hook_errors and not rb.hook_errors


def test_a_pool_with_an_open_position_is_never_pruned(tmp_path):
    """ArenaEngine.prune skips pools in positions; the runner's housekeeping must not lose a pool a book holds while the position is unpriced."""
    run = make(tmp_path, lane_h=False)
    run.run(T0 - 20 * MIN + R.SCAN_SECOND_MS, X + 12 * MIN)  # ends in the middle of the pullback: entries are open
    busy = {p.pool for p in run.engine.positions.values()}
    assert busy, "the tape should leave an engine position open here"
    run._housekeep_at = 0
    run.state.apply(print_row(POOLS[0], MINTS[0], X + 12 * MIN + 3 * H, 424242))  # three hours on: far past the keep window for every other pool
    assert busy <= set(run.engine.pools)
