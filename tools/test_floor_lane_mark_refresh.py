"""FLOOR lane: open-position marks stay fresh over a 24 h hold (FloorLanePort re-primes the pool cache of open mints). Synthetic only: fake executor, no RPC, no key, no tape.

The fake executor mirrors the two real rules that made the mark go stale (probe_executor.STATIC_TTL_MS = 300 s cache gate in _batched_exit_snapshots; arena_live_bridge
position_return = None when the warm snapshot is older than 5 s). The pricing is the V-correct spot ratio (quote_vault + V) / base_vault over the entry mark.

  /data/mal/venv/bin/python -m pytest tools/test_floor_lane_mark_refresh.py
"""

from __future__ import annotations

import types

from tools import floor_lane_executor as X
from tools import h5_lane_executor as H
from tools import probe_executor as pe
from tools.test_floor_arena_executor import Clock
from tools.test_floor_lane_executor import FakeInner, STAKE, T0, entry, feed, mk  # noqa: F401  (autouse fixture comes with the import)

V = 17_584_505_493
ENTRY_MARK = (2_000_000_000 + V) / 1_000_000_000  # (quote + V) / base at the buy


class PricedEx:
    def __init__(self, clk):
        self.now_ms = clk
        self.limits = types.SimpleNamespace(priority_lamports=500_000, size_lamports=STAKE)
        self.state = types.SimpleNamespace(open={}, pending={})
        self.fast_exit = True
        self._pool_cache = {}
        self.warm_snaps = {}
        self.quote = 3_000_000_000  # moves the spot; base vault fixed
        self.fail = False
        self.snapshot_calls = []
        self.rows = []

    def _log(self, kind, mint, **kw):
        self.rows.append((kind, mint, kw))

    def _snapshot(self, mint, commitment=None):
        self.snapshot_calls.append((mint, self.now_ms()))
        if self.fail:
            return None, "pool", "rpc_error"
        self._pool_cache[mint] = ("ps", V, self.now_ms())
        return object(), "pool", None

    def refresh_warm(self, mints):  # the real _batched_exit_snapshots gate
        now = self.now_ms()
        for m in mints:
            if m in self.state.open and m in self._pool_cache and 0 <= now - self._pool_cache[m][2] < pe.STATIC_TTL_MS:
                self.warm_snaps[m] = ((self.quote + V) / 1_000_000_000, now)


class PricedInner(FakeInner):
    def __init__(self, clk):
        super().__init__()
        self.ex = PricedEx(clk)

    def warm(self, mints):
        self.ex.refresh_warm(mints)

    def position_return(self, mint):  # LiveExecutorPort.position_return: None when the warm snapshot is older than 5 s
        w = self.ex.warm_snaps.get(mint)
        if not w or mint not in self.ex.state.open or self.ex.now_ms() - w[1] > 5_000:
            return None
        return w[0] / ENTRY_MARK - 1


def open_position(tmp_path, clk):
    inner = PricedInner(clk)
    br, _ = mk(tmp_path, clk, inner=inner)
    entry(tmp_path)
    feed(br, clk)
    inner.land_buy("MP")
    br._poll_ports()
    inner.ex.state.open["MP"] = {"mark": ENTRY_MARK}
    inner.ex._pool_cache["MP"] = ("ps", V, clk.t)  # primed at entry, as _snapshot does
    return br, inner


def run(br, clk, seconds, step_ms=1_000):
    for _ in range(seconds * 1000 // step_ms):
        clk.t += step_ms
        br._last_warm = 0  # warm on every call; the port's own bound is what limits the RPC
        br.warm()
        br._lane_update_marks()


def fold(br, clk, marks=None):
    with br.lane.locked():
        return H.lane_fold(br.lane.read(), br._lane_marks if marks is None else marks, clk.t, 60.0, "floor-lane-1")


def test_24h_hold_keeps_a_fresh_v_correct_mark(tmp_path):
    clk = Clock(T0)
    br, inner = open_position(tmp_path, clk)
    want = (inner.ex.quote + V) / 1_000_000_000 / ENTRY_MARK - 1
    assert 0 < want < 1  # V is on both sides of the ratio
    for _ in range(24):  # hourly checks across 24 h
        run(br, clk, 3_600, step_ms=2_000)
        mark, at = br._lane_marks["MP"]
        assert clk.t - at <= 2_000
        assert mark == max(0, int(STAKE * (1 + want)))


def test_control_without_reprime_the_mark_goes_stale(tmp_path, monkeypatch):
    clk = Clock(T0)
    br, inner = open_position(tmp_path, clk)
    monkeypatch.setattr(X, "LANE_REPRIME_MS", 10**12)  # disable the re-prime: the old behaviour
    run(br, clk, 400)
    assert clk.t - br._lane_marks["MP"][1] > 60_000
    assert fold(br, clk).pnl == fold(br, clk, {}).pnl  # stale mark counts 0


def test_fetch_failure_leaves_the_mark_stale_and_counts_zero(tmp_path, capsys):
    clk = Clock(T0)
    br, inner = open_position(tmp_path, clk)
    inner.ex.fail = True
    run(br, clk, 700)
    assert clk.t - br._lane_marks["MP"][1] > 60_000
    assert "mark_reprime_failed mint=MP label=rpc_error" in capsys.readouterr().out
    assert fold(br, clk).pnl == fold(br, clk, {}).pnl
    inner.ex.fail = False
    run(br, clk, 300)  # recovers on the next allowed fetch
    assert clk.t - br._lane_marks["MP"][1] <= 1_000


def test_reprime_rate_is_bounded(tmp_path):
    clk = Clock(T0)
    br, inner = open_position(tmp_path, clk)
    run(br, clk, 3_600)  # 1 h of per-second warm calls
    times = [t for _m, t in inner.ex.snapshot_calls]
    assert 14 <= len(times) <= 16  # one per 240 s
    assert all(b - a >= X.LANE_REPRIME_MS for a, b in zip(times, times[1:]))
    inner.ex.fail = True
    inner.ex.snapshot_calls.clear()
    inner.ex._pool_cache.clear()
    run(br, clk, 3_600)
    assert len(inner.ex.snapshot_calls) <= 16  # failures are bounded too


def test_closed_pending_or_unknown_mints_are_not_reprimed(tmp_path):
    clk = Clock(T0)
    br, inner = open_position(tmp_path, clk)
    inner.ex.state.pending["MP"] = {}
    clk.t += 10 * 60_000
    br.port.warm(["MP", "NOT_OPEN"])
    assert inner.ex.snapshot_calls == []


def test_stop_arithmetic_sees_the_live_mark_for_a_healthy_position(tmp_path):
    clk = Clock(T0)
    br, inner = open_position(tmp_path, clk)
    run(br, clk, 3_600)
    mark, at = br._lane_marks["MP"]
    assert clk.t - at <= 1_000 and mark > 0
    assert fold(br, clk).pnl - fold(br, clk, {}).pnl == mark  # the mark is added back to pnl (the daily stop and budget STOP read view.pnl); stale it would add 0


def test_non_fast_exit_executor_is_a_noop(tmp_path):
    clk = Clock(T0)
    br, inner = open_position(tmp_path, clk)
    inner.ex.fast_exit = False
    clk.t += 10 * 60_000
    br.port.warm(["MP"])
    assert inner.ex.snapshot_calls == []
