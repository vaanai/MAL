"""Review item 8: key-less or malformed rows never abort poll(); pools_wsol() keeps non-SOL units out. No network."""

from __future__ import annotations

from pathlib import Path

from tools.arena_state import ArenaState
from tools.test_arena_state import M, POOL, T0, WSOL, curve, state, swap, write_tape


def row(typ: str, **kw) -> dict:
    return {"v": 1, "type": typ, "prog": "pumpswap", "slot": 1, "t_recv_ms": T0, "signature": None, "event_index": None, **kw}


def test_rows_without_a_key_are_counted_not_raised(tmp_path: Path) -> None:
    st = ArenaState(tmp_path, start_ms=0)
    for r in (row("pool_info", mint=M, quote_mint=WSOL), row("pool_create", mint=M), row("create", prog="pumpfun", creator="c"), row("complete", prog="pumpfun")):
        st.apply(r)  # the old code raised AttributeError on the first two
    assert st.counters["rows_without_key"] == 4 and len(st) == 0


def test_a_malformed_row_is_counted_and_the_poll_goes_on(tmp_path: Path) -> None:
    bad = curve(T0 + 1, "A", "buy", 1.0, 1e-7)
    bad["sol_lamports"] = "not a number"
    write_tape(tmp_path, [curve(T0, "A", "buy", 1.0, 1e-7), bad, curve(T0 + 2, "B", "buy", 1.0, 1e-7)])
    st = ArenaState(tmp_path, start_ms=0)
    assert st.poll() == 2 and st.counters["apply_errors"] == 1 and st.pool(M).n_trades == 2  # the bad row fails at its number conversion, before any state changes


def test_evict_idle_min_is_a_knob_and_the_default_is_six_hours(tmp_path: Path) -> None:
    other = "OTHER" + "1" * 39
    rows = [curve(T0, "A", "buy", 1.0, 1e-7), curve(T0 + 61 * 60_000, "B", "buy", 1.0, 1e-7, mint=other)]
    write_tape(tmp_path, rows)
    short = ArenaState(tmp_path, start_ms=0, evict_idle_min=60)
    short.poll()
    short.sweep()
    assert short.pool(M) is None and short.pool(other) is not None  # idle 61 min > 60
    default = ArenaState(tmp_path, start_ms=0)
    default.poll()
    default.sweep()
    assert default.evict_idle_ms == 6 * 3600 * 1000 and default.pool(M) is not None


def test_pools_wsol_only_returns_verified_wsol_keys(tmp_path: Path) -> None:
    pc = row("pool_create", pool=POOL, mint=M, quote_mint=WSOL)
    info = row("pool_info", pool="USDC" + "1" * 40, mint="X" * 44, quote_mint="USDC" + "2" * 40)
    rows = [curve(T0, "A", "buy", 1.0, 1e-7, mint="CURVE" + "1" * 39), pc, info, swap(T0 + 5, "B", "buy", 5, 5, 10**6, 10**6, pool="UNKNOWN" + "1" * 37)]
    st = state(tmp_path, rows)
    assert len(st.pools()) == 4
    assert sorted(p.id for p in st.pools_wsol()) == sorted(["CURVE" + "1" * 39, POOL])
    assert [p.id for p in st.pools_wsol(lambda p: p.prog == "pumpswap")] == [POOL]
    unknown = st.snapshot("UNKNOWN" + "1" * 37)
    assert unknown["quote_wsol"] is None and st.snapshot("USDC" + "1" * 40)["quote_wsol"] is False
