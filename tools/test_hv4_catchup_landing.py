"""HV4 start-up catch-up: where a buy or a sell called during the drain LANDS (QP ruling 2026-10-19, condition C1).

C1 (QP-RULING-1019-startup.md, ruling (b)): "a buy or sell called during catch-up lands at or after the slot of the wall-clock call instant, NOT at the slot of the
last feed row the engine has seen through the lagging apply hook." This file shows it on synthetic tapes at the code under test (tools/arena_paper.py and
tools/hv4_runner.py as of d1dbcd6; neither is edited here). TESTS ONLY: no runner, model or pinned file is touched, nothing reads a real tape, ledger or bots.json,
and nothing here says any book makes money.

What a catch-up is: the process starts, ArenaState.poll() drains the warm-up tape row by row through the apply hook (Runner.apply -> Runner._on_row ->
ArenaEngine.on_row), and the engine's wall clock (now_fn) is ~130 s ahead of the newest row it has seen (the 2026-10-19 stub: 129,903 ms at the first minute).

The tests pin REAL behaviour. If any of them fails, C1 fails and the runner needs a change before registration.
"""
from __future__ import annotations

import math
from pathlib import Path

import pytest

from tools import hv4_runner as R
from tools.arena_paper import ArenaEngine, register_bot
from tools.arena_state import ArenaState
from tools.hv4_budget import BudgetGuard
from tools.hv4_model import StubModel
from tools.test_hv4_runner import BIG_PLAN, FEED_DELAY_SLOTS, POOLS, X, FakeChain, script_for, trade_rows, write_tape
from tools.test_hv4_runner import T0 as RUN_T0

SOL = 1_000_000_000
MIN = 60_000
LAG_MS = 130_000  # the stub's first-minute exit-check delay p99 (129,903 ms), the catch-up this file models
SPS = 0.4
SLOT_MS = 400
K13, K19 = math.ceil(1.3 / SPS - 1e-9), math.ceil(1.9 / SPS - 1e-9)  # entry legs, k_rounding "ceil" (R.STACK_FIELDS): 4 and 5 slots
EXIT_LAG = math.ceil(0.55 / SPS - 1e-9)  # arena_stack.exit_lag_slots: 2 slots at 0.4 s/slot
D = FEED_DELAY_SLOTS
V = 17_584_505_288
WSOL = "So11111111111111111111111111111111111111112"
SHA = "abcdef1234567"
SHA40 = SHA + "0" * 27
BOT = "hv4-cu-test"


@pytest.fixture(scope="module", autouse=True)
def _arena_test_env():
    """register_bot's now_ms / verify_sha=False overrides are refused outside tests unless MAL_ARENA_TEST=1 (arena_paper)."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("MAL_ARENA_TEST", "1")
        yield


# ---------------------------------------------------------------------------------------------------------------- engine level: one print per slot
T0 = 1_790_316_000_000  # an hour boundary
SLOT0 = 400_000_000
CLOCK = T0 - MIN


def slot_of(t: int) -> int:
    """The true slot of wall-clock instant t on this tape: exactly 0.4 s per slot."""
    return SLOT0 + (t - T0) // SLOT_MS


def q_of(slot: int) -> int:
    return 60 * SOL + (slot - SLOT0) * 1000  # the quote reserve is a function of the slot: the state a fill read names the print it came from


def slot_of_q(q: float) -> int:
    return SLOT0 + int(round((q - V - 60 * SOL) / 1000))


def row(i: int) -> dict:
    """The print at slot SLOT0 + i, received exactly on its slot boundary."""
    t = T0 + i * SLOT_MS
    sol = 10_000_000
    return {"v": 1, "type": "trade", "prog": "pumpswap", "venue": "pumpswap", "slot": SLOT0 + i, "t_recv_ms": t, "signature": f"s{i}", "event_index": 0, "pool": "PoolCU" + "1" * 38,
            "mint": "MintCU" + "1" * 38, "trader": "W1", "side": "buy" if i % 2 else "sell", "sol_lamports": sol, "token_raw": sol * 1000, "quote_reserve": q_of(SLOT0 + i),
            "base_reserve": (60 * SOL + V) * 1000, "virtual_quote_reserves": V, "quote_mint": WSOL, "event_ts": t // 1000}


POOL_CU = row(0)["pool"]
N_ROWS = 2400  # 16 minutes of tape


def wall_at(slot_index: int, extra_ms: int = 123) -> int:
    """A wall-clock instant inside the slot SLOT0 + slot_index."""
    return T0 + slot_index * SLOT_MS + extra_ms


def make_engine(tmp: Path, wall: dict, sps_fn=lambda: SPS) -> ArenaEngine:
    reg = tmp / "bots.json"
    register_bot(BOT, SHA, CLOCK, reg, deciding_stake=0.25, model_ids=[], now_ms=CLOCK - 1, verify_sha=False)
    return ArenaEngine(tmp / "ledger", reg, cfg=R.stack_config(feed_delay_slots=D), sps_fn=sps_fn, now_fn=lambda: wall["ms"], code_state_fn=lambda: (SHA40, False))


def feed(eng: ArenaEngine, lo: int, hi: int) -> int:
    """Apply rows lo..hi (inclusive) through the engine's own on_row, in receive order; returns the highest slot seen (the runner's `self.slot`)."""
    for i in range(lo, hi + 1):
        eng.on_row(row(i))
    return SLOT0 + hi


def ledger_rows(eng: ArenaEngine, kind: str) -> list[dict]:
    return [r for r in eng.ledger.read_bot(BOT) if r.get("kind") == kind]


@pytest.mark.parametrize("stale_stamp", [False, True], ids=["stamp=wall", "stamp=stale-tick"])
def test_buy_and_sell_in_the_lag_land_at_the_wall_clock_slot_not_the_last_row_seen(tmp_path, stale_stamp):
    """The core of C1, exact: sps is fixed, so the mapped wall-clock slot is exact. The engine has seen rows up to 130 s behind its clock.

    HV4 passes decision_ms = the wall clock (LLM entries) or the logical tick T (X-arm entries and every code exit: T runs behind the wall clock during a catch-up),
    and decision_slot = the highest slot the runner has applied (Runner.slot). Both stamps are run."""
    wall = {"ms": 0}
    eng = make_engine(tmp_path, wall)
    i_buy = 1000
    w_buy = wall_at(i_buy)
    lag_slots = LAG_MS // SLOT_MS
    last_seen = feed(eng, 0, i_buy - lag_slots)  # the drain has reached ~130 s before the wall clock
    wall["ms"] = w_buy
    assert w_buy - eng.hw_ms >= LAG_MS and last_seen == SLOT0 + i_buy - lag_slots
    stamp = (w_buy - LAG_MS) if stale_stamp else w_buy

    pid = eng.buy(BOT, POOL_CU, 0.25, stamp, last_seen)
    pos = eng.positions[pid]
    assert pos.decision_ms == w_buy and pos.call_ms == w_buy  # the write instant is the wall clock; a stale stamp is not the instant
    assert pos.stamp_ms == stamp
    assert pos.sd == slot_of(w_buy) == SLOT0 + i_buy  # the effective decision slot IS the wall-clock slot
    assert pos.sd >= slot_of(w_buy) and pos.sd - last_seen == lag_slots  # ... 325 slots after the last feed row seen, not at it
    assert pos.sd != last_seen and pos.call_feed_slot == last_seen
    op = ledger_rows(eng, "open")[0]
    assert op["effective_slot"]["entry"] == pos.sd and op["corrected_slot"]["entry"] == pos.sd + D
    assert op["write_lag_ms"]["entry"] == w_buy - stamp and op["stamp_ms"]["entry"] == stamp  # the stale stamp is logged, never used as the instant

    i_sell = 1060
    w_sell = wall_at(i_sell, 77)
    last_seen = feed(eng, i_buy - lag_slots + 1, i_sell - lag_slots)  # still ~130 s behind
    wall["ms"] = w_sell
    sstamp = (w_sell - LAG_MS) if stale_stamp else w_sell
    eng.sell(BOT, pid, 1.0, sstamp, last_seen, reason="test")
    assert pos.exit_ms == w_sell and pos.exit_call_ms == w_sell and pos.exit_stamp_ms == sstamp
    assert pos.xs == slot_of(w_sell) == SLOT0 + i_sell and pos.xs - last_seen == lag_slots
    assert pos.xs >= slot_of(w_sell) and pos.xs != last_seen

    # The sell is NOT priced from the lagging feed: nothing past the need slot has been seen, so it waits for the tape to get there.
    need = max(pos.sd + D + K19, pos.xs + D + EXIT_LAG) + 1
    assert need == SLOT0 + i_sell + D + EXIT_LAG + 1
    assert eng.pending == {POOL_CU: [pid]} and not ledger_rows(eng, "trade") and eng.hw_slot < need - 300

    feed(eng, i_sell - lag_slots + 1, N_ROWS - 1)  # the drain goes on to the end of the tape
    assert not eng.pending and pid not in eng.positions
    trades = ledger_rows(eng, "trade")
    assert len(trades) == 3 and not ledger_rows(eng, "excluded")
    for t in trades:
        assert t["state_src"] == "tape" and t["effective_slot"] == {"entry": SLOT0 + i_buy, "exit": SLOT0 + i_sell}
        for tag, k in (("1p3", K13), ("1p9", K19)):
            c = t["calc"][tag]
            land = c["landing_slot"]
            assert land == SLOT0 + i_buy + D + k and land >= slot_of(w_buy) + D  # the landing slot is wall-clock slot + d + k
            assert slot_of_q(c["Qe"]) == land + 1  # PumpSwap END bound: the entry state is the first print AFTER the landing slot ...
            assert slot_of_q(c["Qe"]) > last_seen  # ... never a print the lagging feed had already shown at the call (here: slot SLOT0 + 675)
            assert slot_of_q(c["Qx"]) == SLOT0 + i_sell + D + EXIT_LAG + 1  # the exit state is read after the wall-clock slot of the sell
            assert slot_of_q(c["Qx"]) >= slot_of(w_sell) + D + EXIT_LAG


def test_a_lagging_feed_and_a_current_feed_give_the_same_decision_slots(tmp_path):
    """Two engines on the same tape and the same wall clock: one drained to the call instant (no lag), one 130 s behind. Same sd, same xs, same landing, same
    Qe and Qx. The lag moves nothing: the engine reads the clock, not the feed."""
    out = {}
    for name, lag_rows in (("current", 0), ("lagging", LAG_MS // SLOT_MS)):
        wall = {"ms": 0}
        eng = make_engine(tmp_path / name, wall)
        w_buy, w_sell = wall_at(1000), wall_at(1060, 77)
        seen = feed(eng, 0, 1000 - lag_rows)
        wall["ms"] = w_buy
        pid = eng.buy(BOT, POOL_CU, 0.25, w_buy, seen)
        seen = feed(eng, 1000 - lag_rows + 1, 1060 - lag_rows)
        wall["ms"] = w_sell
        eng.sell(BOT, pid, 1.0, w_sell, seen, reason="test")
        feed(eng, 1060 - lag_rows + 1, N_ROWS - 1)
        t = ledger_rows(eng, "trade")
        pos_rows = [(r["effective_slot"], r["calc"]["1p3"]["landing_slot"], r["calc"]["1p9"]["landing_slot"], r["calc"]["1p3"]["Qe"], r["calc"]["1p9"]["Qe"],
                     r["calc"]["1p3"]["Qx"], r["calc"]["1p3"]["Be"], r["calc"]["1p3"]["Bx"]) for r in t]
        out[name] = pos_rows
        assert len(t) == 3
    assert out["current"] == out["lagging"]


def test_the_engines_own_clock_is_the_only_input_a_stale_stamp_and_a_stale_decision_slot_cannot_pull_the_landing_back(tmp_path):
    """decision_ms and decision_slot can only push the landing LATER (arena_paper docstring; sd = max(decision_slot, mapped write instant))."""
    wall = {"ms": 0}
    eng = make_engine(tmp_path, wall)
    feed(eng, 0, 675)
    wall["ms"] = wall_at(1000)
    pos_old = eng.positions[eng.buy(BOT, POOL_CU, 0.25, T0, SLOT0)]  # a stamp from the start of the tape and slot 0 of it: priced at the write instant anyway
    assert pos_old.sd == SLOT0 + 1000 and pos_old.decision_ms == wall["ms"]
    wall["ms"] = wall_at(1001)
    pos_new = eng.positions[eng.buy(BOT, POOL_CU, 0.25, wall["ms"], SLOT0 + 1500)]  # a decision_slot ahead of the wall-clock slot moves the landing later, never earlier
    assert pos_new.sd == SLOT0 + 1500


def test_a_stamp_in_the_future_is_still_refused_during_the_lag(tmp_path):
    from tools.arena_paper import ArenaRefused

    wall = {"ms": 0}
    eng = make_engine(tmp_path, wall)
    feed(eng, 0, 675)
    wall["ms"] = wall_at(1000)
    with pytest.raises(ArenaRefused, match="future_decision"):
        eng.buy(BOT, POOL_CU, 0.25, wall["ms"] + 60_000, SLOT0 + 675)


def test_with_the_tape_derived_slot_clock_main_uses_the_landing_is_the_wall_clock_slot_to_within_the_sps_error(tmp_path):
    """main() builds ArenaEngine(...) with NO sps_fn: sps comes from SlotClock fitted on the drained rows' block times (whole seconds), i.e. from the tape, not from
    the wall clock. The mapped wall-clock slot then carries an error of (lag / sps) x (relative sps error); the landing is never BEFORE the last row seen and is within
    a few slots (about 1 s) of the true wall-clock slot on this tape. The exact lower bound at the engine's own sps is `slot_at`."""
    wall = {"ms": 0}
    eng = make_engine(tmp_path, wall, sps_fn=None)
    i_buy = 1000
    seen = feed(eng, 0, i_buy - LAG_MS // SLOT_MS)
    wall["ms"] = wall_at(i_buy)
    sps = eng.sps()
    assert sps is not None and abs(sps / SPS - 1) < 0.02  # the fit from whole-second block times is within 2% on this tape
    pid = eng.buy(BOT, POOL_CU, 0.25, wall["ms"], seen)
    pos = eng.positions[pid]
    assert pos.sd == max(seen, eng.slot_at(wall["ms"], sps))
    assert pos.sd >= eng.slot_at(wall["ms"], sps) > seen + 300  # by construction, at or after the engine's own mapping of the call instant
    err = pos.sd - slot_of(wall["ms"])
    bound = math.ceil((LAG_MS / (SPS * 1000)) * abs(sps / SPS - 1)) + 1
    assert abs(err) <= bound  # how far the mapped slot can sit from the true wall-clock slot: lag x relative sps error, plus the floor of slot_at


# ---------------------------------------------------------------------------------------------------------------- runner level: the real hook and the real _open / _sell
def live_runner(tmp: Path, wall: dict) -> R.Runner:
    """The hv4_runner Runner in LIVE mode (as main() builds it) on the module's synthetic two-pool tape, with a fake wall clock shared by the runner and the engine."""
    tape = tmp / "tape"
    write_tape(tape, trade_rows())
    root = tmp / "hv4"
    guard = BudgetGuard(root)
    model = StubModel(script_for(), guard=guard, cost_usd=0.0, latency_ms=50)
    state = ArenaState(tape, start_ms=0)
    engine = ArenaEngine(tmp / "ledger", tmp / "bots.json", cfg=R.stack_config(feed_delay_slots=D), sps_fn=lambda: SPS, now_fn=lambda: wall["ms"], code_state_fn=lambda: (SHA40, False))
    cfg = R.RunnerConfig(root=str(root), frozen_sha=SHA, clock_start_ms=RUN_T0 - 60 * MIN, live=True, lane_h=False)
    run = R.Runner(cfg, state, engine, model, FakeChain(), guard, now_fn=lambda: wall["ms"], binding={"git_sha": SHA, "dirty": False})
    run.register_all(str(tmp / "bots.json"), now_ms=RUN_T0 - 61 * MIN, verify_sha=False)
    return run


BASE = RUN_T0 - 3 * 3_600_000  # the run tape's slot origin: slot = 400_000_000 + (t - BASE) // 400


def run_slot_of(t: int) -> int:
    return 400_000_000 + (t - BASE) // SLOT_MS


def boundary_plus_399(t: int) -> int:
    """The last millisecond of the slot containing t. With it floor() in ArenaEngine.slot_at adds no loss: the mapped slot equals the true one."""
    return BASE + ((t - BASE) // SLOT_MS) * SLOT_MS + SLOT_MS - 1


def first_print_at_or_after(rows: list[dict], pool: str, slot: int) -> dict:
    cand = [r for r in rows if r["type"] == "trade" and r["pool"] == pool and r["slot"] >= slot]
    return min(cand, key=lambda r: (r["slot"], r["t_recv_ms"], r["signature"], r["event_index"]))


@pytest.mark.parametrize("dec", ["wall", "tick"])
def test_runner_open_and_sell_during_a_drain_land_at_the_wall_clock_slot(tmp_path, dec):
    """Runner._open and Runner._sell exactly as the catch-up ticks call them: the state is drained through ArenaState.poll() and the wrapped apply hook (so
    Runner.slot and ArenaEngine.hw_ms lag), the wall clock is 130 s ahead, an LLM entry stamps decision_ms = the wall clock and an X-arm entry / any code exit
    stamps the logical tick T (which runs behind the wall clock). The price window (pool 0, minutes -60..-15) rises every print, so a fill priced from a stale
    print could not match."""
    wall = {"ms": 0}
    run = live_runner(tmp_path, wall)
    rows = trade_rows()
    w_buy = boundary_plus_399(X - 40 * MIN + 7_000)
    t_buy = w_buy - LAG_MS  # the logical tick T of the catch-up
    run.poll_to(t_buy)  # the drain: every row up to T - 1 goes through the apply hook into the engine
    wall["ms"] = w_buy
    eng = run.engine
    assert eng.hw_ms is not None and w_buy - eng.hw_ms >= LAG_MS and run.slot == eng.hw_slot
    last_seen = run.slot
    card = {"id": POOLS[0], "f": {"age_tier": "established"}}
    pid = run._open("BIG-L", card, 0.25, BIG_PLAN, w_buy if dec == "wall" else t_buy, {}, {})
    assert pid is not None
    pos = eng.positions[pid]
    assert pos.decision_ms == w_buy and pos.sd == run_slot_of(w_buy)  # the wall-clock slot, exactly
    assert pos.sd - last_seen >= (LAG_MS // SLOT_MS) - 2 and pos.sd > last_seen + 300

    w_sell = boundary_plus_399(w_buy + 30_000)
    t_sell = w_sell - LAG_MS
    run.poll_to(t_sell)
    wall["ms"] = w_sell
    seen2 = run.slot
    assert run._sell(run.books["BIG-L"].open[pid], "test", t_sell, None) is True  # the tick instant T is the stamp; the clock is the wall clock
    assert pos.xs == run_slot_of(w_sell) and pos.xs > seen2 + 300 and pos.exit_ms == w_sell and pos.exit_stamp_ms == t_sell
    assert eng.pending  # not priced from the lagging feed

    run.poll_to(w_sell + 120_000)  # the drain reaches the prints after the landing slots
    assert not eng.pending and pid not in eng.positions
    t = [r for r in eng.ledger.read_bot(R.bot_id("BIG-L")) if r.get("kind") == "trade" and r["stake_sol"] == 0.25]
    assert len(t) == 1
    r = t[0]
    assert r["state_src"] == "tape" and r["effective_slot"] == {"entry": pos.sd, "exit": pos.xs}
    for tag, k in (("1p3", K13), ("1p9", K19)):
        c = r["calc"][tag]
        assert c["landing_slot"] == pos.sd + D + k and c["landing_slot"] >= run_slot_of(w_buy)
        p = first_print_at_or_after(rows, POOLS[0], c["landing_slot"] + 1)
        assert (c["Qe"], c["Be"]) == (p["quote_reserve"] + V, p["base_reserve"])  # the entry state is the first print after the landing slot, from the tape
        stale = first_print_at_or_after(rows, POOLS[0], last_seen)
        assert stale["slot"] <= last_seen + 13 and c["Be"] != stale["base_reserve"]  # not the print the lagging feed showed at the call
    px = first_print_at_or_after(rows, POOLS[0], pos.xs + D + EXIT_LAG + 1)
    assert (r["calc"]["1p3"]["Qx"], r["calc"]["1p3"]["Bx"]) == (px["quote_reserve"] + V, px["base_reserve"])


def test_observation_not_c1_the_guard_reference_is_the_lagging_feed_slot(tmp_path):
    """NOT part of C1 (C1 is the landing). Recorded because it is the other place the lag shows: the 15% guard compares the landing price with the price at the
    engine's own latest feed slot at the call (call_feed_slot, arena_paper._ref_slot), which during a catch-up is the lagging slot, ~325 slots before the landing.
    The test pins this behaviour so a reader of the registration does not have to find it."""
    wall = {"ms": 0}
    eng = make_engine(tmp_path, wall)
    seen = feed(eng, 0, 675)
    wall["ms"] = wall_at(1000)
    pid = eng.buy(BOT, POOL_CU, 0.25, wall["ms"], seen)
    pos = eng.positions[pid]
    assert pos.call_feed_slot == seen and pos.sd - pos.call_feed_slot == 325
    seen = feed(eng, 676, 725)
    wall["ms"] = wall_at(1060, 77)
    eng.sell(BOT, pid, 1.0, wall["ms"], seen, reason="test")
    feed(eng, 726, N_ROWS - 1)
    for t in ledger_rows(eng, "trade"):
        assert t["guard_ref_slot"] == SLOT0 + 675 and t["calc"]["1p3"]["landing_slot"] - t["guard_ref_slot"] == 325 + D + K13
