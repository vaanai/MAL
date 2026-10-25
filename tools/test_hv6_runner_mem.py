"""Memory bounds of the HV6 runner are decision-neutral (byte-identical decision logs with and without them). Synthetic tapes only; no real tape, no API, no RPC."""

from __future__ import annotations

import pytest

from tools import hv6_cards as H
from tools import hv6_memscale as MS
from tools import hv6_runner as R
from tools.test_hv4_runner import T0
from tools.test_hv6_runner import _env, make, rows_of  # noqa: F401  (_env is autouse)

MIN = 60_000


def _digest(run):
    return {"log_md5": run.log.md5(), "entries": {a: list(b.entries_ms) for a, b in run.books.items()}, "realized": {a: dict(b.realized) for a, b in run.books.items()},
            "open": {a: sorted(b.open) for a, b in run.books.items()}}


def test_bounds_change_no_decision_on_the_hv4_style_tape(tmp_path_factory):
    out = {}
    for bound in (True, False):
        run = make(tmp_path_factory.mktemp("r6mem"), cfg_kw={"bound_memory": bound})
        run.run(T0 + 9 * MIN + 2000, T0 + 110 * MIN)
        out[bound] = (_digest(run), run)
    d_on, on = out[True]
    d_off, off = out[False]
    assert d_on == d_off and any(d_on["entries"][a] for a in ("L", "F", "X"))  # decisions exist, and they are byte-identical
    assert on.counters.get("engine_prints_trimmed", 0) > 0 and on.counters.get("engine_trims", 0) > 0                  # the trim really ran
    assert off.counters.get("engine_trims", 0) == 0
    held = lambda r: sum(len(h.slot) for h in r.engine.pools.values())  # noqa: E731
    assert held(on) < held(off)                                                                                        # and it holds fewer prints
    assert not on.hook_errors


def test_bounds_change_no_decision_on_a_scaled_synthetic_tape(tmp_path_factory, monkeypatch):
    tape_dir = tmp_path_factory.mktemp("r6memsc")
    MS.write_tape(tape_dir / "tape", MS.T0, 3, 1.0, 12_000, seed=3, pre_hours=3)
    runs = {}
    for bound in (True, False):
        base = tmp_path_factory.mktemp("r6memrun")
        run = MS.build_runner(base, tape_dir / "tape", bound=bound)
        run.run(MS.T0 + 2000, MS.T0 + 3 * MS.HOUR)
        runs[bound] = run
    on, off = runs[True], runs[False]
    assert _digest(on) == _digest(off)
    assert on.counters["engine_prints_trimmed"] > 0 and on.counters["quote_pools_swept"] >= 0
    assert sum(len(h.slot) for h in on.engine.pools.values()) < sum(len(h.slot) for h in off.engine.pools.values())
    assert len(rows_of(on, "SCAN", "scan")) > 100 and not on.hook_errors


def test_quote_history_sweep_keeps_held_and_recent_pools():
    q = R.QuoteHistory()
    q.add("old", 1_000, 60.0)
    q.add("recent", 9_000_000, 60.0)
    q.add("held", 1_000, 60.0)
    assert q.sweep(10_000_000, 3_600_000, held=lambda k: k == "held") == 1
    assert set(q.t) == {"recent", "held"} and set(q.q) == {"recent", "held"}


def test_watch_set_is_bounded_and_keeps_every_hot_pool(tmp_path_factory, monkeypatch):
    seen = []

    class WatchChain:
        def chain_part(self, pool, mint, now_ms, slot):
            return None

        def watch(self, items, hot, now_ms, vols=None):
            seen.append((list(items), set(hot)))

    monkeypatch.setattr(R, "WATCH_MAX_POOLS", 5)
    tape_dir = tmp_path_factory.mktemp("r6memw")
    MS.write_tape(tape_dir / "tape", MS.T0, 2, 2.0, 15_000, seed=5, pre_hours=3)
    run = MS.build_runner(tmp_path_factory.mktemp("r6memwr"), tape_dir / "tape", bound=True)
    run.chain = WatchChain()
    run.run(MS.T0 + 2000, MS.T0 + 2 * MS.HOUR)
    assert seen
    for items, hot in seen:
        ids = [p for p, _ in items]
        assert len(ids) == len(set(ids)) and hot <= set(ids) and len(ids) <= max(5, len(hot))
    assert max(len(i) for i, _ in seen) >= 1
