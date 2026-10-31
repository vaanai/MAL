"""Tests for tools/r4b_feed_merge.py. SYNTHETIC rows and FAKE sockets only: no network, no key, no tape file, no sealed path.

  PYTHONPATH=/data/mal/audit-1008/venv/lib/python3.12/site-packages /data/mal/venv/bin/python -s -m pytest tools/test_r4b_feed_merge.py

The tests that need tools/arena_feed.py (the window's row conversion) import it through the `arena_feed_mod` fixture. observe.trade_source imports certifi and
websockets at import time; where a test venv lacks them (this host's does, which also keeps tools/test_arena_feed.py from being collected there) the fixture
puts two EMPTY stand-in modules in sys.modules for the duration of the module's tests and removes everything it imported afterwards. The row conversion itself is
arena_feed's own code, unchanged.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
import types
from collections import Counter
from pathlib import Path

import pytest

from tools import r4b_feed_adapter as fa
from tools import r4b_feed_lag_probe as lp
from tools import r4b_feed_merge as fm

T = 1_786_665_600  # an hour mark (seconds)
FIX = Path(__file__).resolve().parents[1] / "observe" / "fixtures"
SIG = "S" * 88
FAKE_KEY = "FAKE-KEY-not-a-secret-0001"
FAKE_URL = f"wss://helius.example.test/?api-key={FAKE_KEY}"


# ----------------------------------------------------------------------------------------------------------------------------------- helpers
def fx(name: str) -> str:
    return "Program data: " + (FIX / name).read_text(encoding="utf-8").strip()


def ack(i: int) -> str:
    return json.dumps({"jsonrpc": "2.0", "result": 100 + i, "id": i})


def slot_msg(s: int) -> str:
    return json.dumps({"jsonrpc": "2.0", "method": "slotNotification", "params": {"result": {"slot": s, "parent": s - 1, "root": s - 40}, "subscription": 100}})


def logs_msg(sig: str, slot: int, logs: list[str], err=None) -> str:
    return json.dumps({"jsonrpc": "2.0", "method": "logsNotification", "params": {"subscription": 101, "result": {"context": {"slot": slot},
                                                                                                                   "value": {"signature": sig, "err": err, "logs": logs}}}})


class Clock:
    def __init__(self, ms: int) -> None:
        self.ms, self.slept = ms, []

    def now(self) -> int:
        return self.ms

    async def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.ms += int(round(s * 1000))


class FakeWS:
    def __init__(self, script) -> None:
        self.script, self.sent = list(script), []

    async def send(self, s: str) -> None:
        self.sent.append(json.loads(s))


class FakeConnect:
    def __init__(self, ws: FakeWS | None = None, exc: BaseException | None = None) -> None:
        self.ws, self.exc, self.urls, self.closed = ws, exc, [], False

    def __call__(self, url: str) -> "FakeConnect":
        self.urls.append(url)
        return self

    async def __aenter__(self):
        if self.exc is not None:
            raise self.exc
        return self.ws

    async def __aexit__(self, *a) -> None:
        self.closed = True


def make_recv(clock: Clock):
    """Scripted receive on the fake clock: a frame due within the timeout is delivered at its time; otherwise the clock moves by the timeout and TimeoutError."""
    async def recv(ws: FakeWS, timeout: float):
        ms = int(round(timeout * 1000))
        if ws.script and ws.script[0][0] <= clock.ms + ms:
            t, item = ws.script.pop(0)
            clock.ms = max(clock.ms, t)
            if isinstance(item, BaseException):
                raise item
            return item
        clock.ms += ms
        raise TimeoutError
    return recv


def script(mark: int = T, *, tip0: int = 1000, until_ms: int | None = None, step_ms: int = 400, ack_at_ms: int = 80, extra=()) -> list:
    """Acks 50..150 ms after the window opens, then a processed tip every step_ms until until_ms (default the window's end), plus extra (t_ms, raw) frames."""
    open_ms = (mark - lp.PRE_S) * 1000
    end = (mark + lp.POST_S) * 1000 if until_ms is None else until_ms
    fr = [(open_ms + ack_at_ms - 30, ack(1)), (open_ms + ack_at_ms, ack(2)), (open_ms + ack_at_ms + 20, ack(3))]
    t, s = open_ms + ack_at_ms + 100, tip0
    while t <= end:
        fr.append((t, slot_msg(s)))
        t, s = t + step_ms, s + 1
    return sorted(fr + list(extra), key=lambda x: x[0])


def window(clock: Clock, frames, mark: int = T, **kw) -> tuple[fm.HeliusWindow, FakeConnect]:
    ws = FakeWS(frames)
    conn = kw.pop("connect", None) or FakeConnect(ws)
    kw.setdefault("to_rows", lambda *a: [])
    w = fm.HeliusWindow(mark, FAKE_URL, connect=conn, recv=make_recv(clock), now_ms=clock.now, sleep=clock.sleep, **kw)
    return w, conn


def run(w: fm.HeliusWindow) -> fm.WindowState:
    return asyncio.run(w.run())


@pytest.fixture(scope="module")
def arena_feed_mod():
    before = set(sys.modules)
    stand_ins = []
    for name in ("certifi", "websockets", "websockets.exceptions"):
        try:
            importlib.import_module(name)
        except ImportError:
            stand_ins.append(name)
    if stand_ins:
        c, w, e = types.ModuleType("certifi"), types.ModuleType("websockets"), types.ModuleType("websockets.exceptions")
        c.where = lambda: ""
        e.ConnectionClosed = type("ConnectionClosed", (Exception,), {})
        e.InvalidStatusCode = type("InvalidStatusCode", (Exception,), {})
        w.exceptions = e
        sys.modules.update({k: v for k, v in (("certifi", c), ("websockets", w), ("websockets.exceptions", e)) if k in stand_ins})
    try:
        yield importlib.import_module("tools.arena_feed")
    finally:
        for k in set(sys.modules) - before:
            del sys.modules[k]


def trow(i: int, slot: int | None = None, typ: str = "trade", ei: int = 0, t: int | None = None, **kw) -> dict:
    return {"v": 1, "type": typ, "prog": "pumpswap", "slot": 100 + i if slot is None else slot, "t_recv_ms": 1000 * i if t is None else t, "signature": f"sig{i}",
            "event_index": ei, "venue": "pumpswap", **kw}


# ----------------------------------------------------------------------------------------------------------------------------------- Proof 1: the merge
def test_overlapping_copies_give_one_row_each_and_record_the_first_source():
    mg = Merger = fm.Merger(clock_ms=lambda: 0)
    pub = [trow(i, t=1000 * i + 500) for i in range(0, 6)]  # the public copy of rows 0..5 arrives half a second after the tick
    hel = [trow(i, t=1000 * i) for i in range(3, 9)]  # the window delivers rows 3..8 first
    out = []
    for src, r in fm.merge_ordered({"public": pub, "helius": hel}):
        e = mg.offer(src, r, now_ms=r["t_recv_ms"])
        if e is not None:
            out.append((src, e["signature"]))
    assert sorted(s for _, s in out) == sorted(f"sig{i}" for i in range(9)) and len(out) == 9  # one row each
    assert {s: src for src, s in out} == {**{f"sig{i}": "public" for i in range(3)}, **{f"sig{i}": "helius" for i in range(3, 9)}}
    c = mg.counts()
    assert c["offered"] == {"public": 6, "helius": 6} and c["emitted"] == {"public": 3, "helius": 6}
    assert c["dup"] == 3 and c["dup_pairs"]["helius_then_public"] == 3 and c["dup_pairs"]["public_then_helius"] == 0
    assert mg.first_seen(("sig4", "trade", 0)) == ("helius", 4000) and mg.first_seen(("sig1", "trade", 0)) == ("public", 1500)
    assert len(mg) == 9 and mg.first_seen(("nope", "trade", 0)) is None


def test_first_arrival_wins_in_either_direction_and_the_first_copy_is_what_is_emitted():
    mg = fm.Merger()
    a = mg.offer("public", trow(1, t=10, sol_lamports=111), now_ms=10)
    b = mg.offer("helius", trow(1, t=11, sol_lamports=222), now_ms=11)  # same key, other payload: still a duplicate, the first stands
    c = mg.offer("helius", trow(2, t=12), now_ms=12)
    d = mg.offer("public", trow(2, t=13), now_ms=13)
    assert a["sol_lamports"] == 111 and b is None and c is not None and d is None
    assert mg.stats["dup_public_then_helius"] == 1 and mg.stats["dup_helius_then_public"] == 1


def test_rows_seen_by_only_one_source_are_emitted_once_each():
    mg = fm.Merger()
    rows = [trow(i) for i in range(20)]
    pub_out = mg.offer_many("public", rows[:10])
    hel_out = mg.offer_many("helius", rows[10:])
    assert len(pub_out) == 10 and len(hel_out) == 10 and mg.stats["dup"] == 0
    assert mg.counts()["emitted"] == {"public": 10, "helius": 10}


def test_the_key_is_signature_type_and_event_index():
    mg = fm.Merger()
    base = trow(1)
    assert mg.offer("public", base) is not None
    assert mg.offer("helius", {**base, "type": "pool_create"}) is not None  # same signature, other type
    assert mg.offer("helius", {**base, "event_index": 1}) is not None  # other event index
    assert mg.offer("helius", {**base, "t_recv_ms": 5}) is None  # same key
    assert fm.row_key({"type": "gap", "signature": None}) is None and fm.row_key({"type": "hb"}) is None


def test_rows_without_a_transaction_pass_through_and_are_not_deduplicated():
    mg = fm.Merger()
    gap = {"v": 1, "type": "gap", "prog": "pumpswap", "slot": 7, "t_recv_ms": 5, "signature": None, "event_index": None, "kind": "slot_jump", "data_loss": True}
    assert mg.offer("public", gap) is not None and mg.offer("public", gap) is not None
    assert mg.stats["passthrough"] == 2 and mg.stats["dup"] == 0 and len(mg) == 0


def test_unknown_source_is_refused():
    with pytest.raises(ValueError):
        fm.Merger().offer("rpc", trow(1))


def test_the_key_map_is_bounded_by_horizon_and_by_count():
    mg = fm.Merger(horizon_slots=50, max_keys=1000)
    for i in range(300):
        mg.offer("public", trow(i, slot=1000 + i))
    assert len(mg) <= 51 and mg.stats["evicted_horizon"] >= 249
    assert mg.offer("helius", trow(299, slot=1299)) is None  # recent: still deduplicated
    assert mg.offer("helius", trow(5, slot=1005)) is not None and mg.stats["below_horizon"] == 1  # too old to check: emitted, counted
    cap = fm.Merger(horizon_slots=10**9, max_keys=10)
    for i in range(25):
        cap.offer("public", trow(i))
    assert len(cap) == 10 and cap.stats["evicted_cap"] == 15


def test_pool_mints_are_learned_from_emitted_pool_rows():
    mg = fm.Merger()
    mg.offer("public", {"v": 1, "type": "pool_create", "prog": "pumpswap", "slot": 5, "t_recv_ms": 1, "signature": "sp", "event_index": 0, "pool": "P1",
                        "mint": "M1", "quote_mint": "Q"})
    mg.offer("public", {"v": 1, "type": "pool_info", "prog": "pumpswap", "slot": 0, "t_recv_ms": 2, "signature": None, "event_index": None, "pool": "P2",
                        "mint": "M2", "quote_mint": "Q"})
    assert mg.pool_mints == {"P1": ("M1", "Q"), "P2": ("M2", "Q")}


def test_seam_report_counts_keys_seen_by_both_and_by_one_source():
    mg = fm.Merger()
    for i in range(10):
        mg.offer("public", trow(i, slot=100 + i))
    for i in range(5, 15):
        mg.offer("helius", trow(i, slot=100 + i))
    r = mg.seam_report(105, 109)
    assert r == {"lo_slot": 105, "hi_slot": 109, "both": 5, "public_only": 0, "helius_only": 0}
    assert mg.seam_report(100, 104)["public_only"] == 5 and mg.seam_report(110, 114)["helius_only"] == 5
    mg.offer("public", trow(20, slot=120))  # a row the window never delivered inside its range
    assert mg.seam_report(115, 125)["public_only"] == 1


def test_merge_ordered_interleaves_by_receive_time_and_needs_integer_times():
    a = [{"t_recv_ms": 1, "n": "a1"}, {"t_recv_ms": 5, "n": "a2"}]
    b = [{"t_recv_ms": 1, "n": "b1"}, {"t_recv_ms": 3, "n": "b2"}]
    assert [r["n"] for _, r in fm.merge_ordered({"public": a, "helius": b})] == ["a1", "b1", "b2", "a2"]
    with pytest.raises(ValueError):
        list(fm.merge_ordered({"public": [{"t_recv_ms": None}]}))


def _engine_state(eng, drop=()):
    """(clock, market minutes, {mint: member prints as tuples}); `drop` names print columns to leave out (frn is the arrival counter)."""
    prints = {}
    for m, st in eng.mints.items():
        cols = [[None if x != x else x for x in v] for k, v in st.prints.items() if k not in drop]
        prints[m] = list(zip(*cols))
    return dict(eng.clock), {k: list(v) for k, v in eng.mkt.items()}, prints


def _scenario_tape():
    pytest.importorskip("duckdb")
    from tools.test_r4b_feed_adapter import _to_tape
    from tools.test_r4b_live import event_stream, scenario

    ev = event_stream(scenario())
    tape = [(t, o, n, k, _to_tape(k, r)) for t, o, n, k, r in ev]
    assert len({fm.row_key(x[4]) for x in tape}) == len(tape)  # the scenario's keys are unique
    return tape


def _two_feeds(tape, lag: int):
    """Public copies of rows [0, P), window copies of rows [H, N): rows H..P-1 are in both. Row i is due at 10 i. A row before H is public-only and arrives on
    time; the window copy of a row arrives on time and its public copy `lag` later (45 is about 4 rows, 15 s of block time, 37 slots in this scenario; the
    default dedup horizon is 1,500 slots). Returns the two arrival-ordered streams and H, P."""
    n = len(tape)
    h, p = n // 3, 2 * n // 3
    pub = [{**tape[i][4], "t_recv_ms": 10 * i + (lag if i >= h else 0)} for i in range(p)]
    hel = [{**tape[i][4], "t_recv_ms": 10 * i} for i in range(h, n)]
    return pub, hel, h, p


def test_merged_stream_through_the_adapter_equals_a_single_feed_of_the_same_rows():
    """Proof 1 (Addendum 3 item 1) on the synthetic scenario: the merged stream, through r4b_feed_adapter.apply into an Engine, gives the same clock, market
    minutes and member prints as the single feed of the same rows, and the same decisions at every mark."""
    from tools.test_r4b_live import MARKS, drive, mk_engine, sig

    tape = _scenario_tape()
    pub, hel, h, p = _two_feeds(tape, lag=45)
    mg = fm.Merger()
    merged = []
    for src, r in fm.merge_ordered({"public": pub, "helius": hel}):
        e = mg.offer(src, r, now_ms=r["t_recv_ms"])
        if e is not None:
            merged.append(e)
    assert len(merged) == len(tape)  # every row once
    assert mg.stats["emitted_public"] == h and mg.stats["emitted_helius"] == len(tape) - h and mg.stats["dup"] == p - h
    assert [m["signature"] for m in merged] == [x[4]["signature"] for x in tape]  # arrival order of first copies = the single feed's order
    single, merged_eng = mk_engine(), mk_engine()
    a1, a2 = fa.FeedAdapter(), fa.FeedAdapter()
    ev_single, ev_merged = [], []
    for (t, o, n, k, row), mrow in zip(tape, merged):
        ms, mm = a1.feed(row), a2.feed(mrow)
        assert [x.kind for x in ms] == [x.kind for x in mm] and not any(x.halts_buys for x in mm)
        fa.apply(single, ms)
        fa.apply(merged_eng, mm)
        ev_single += [(t, o, n, x.kind, x.row) for x in ms]
        ev_merged += [(t, o, n, x.kind, x.row) for x in mm]
    assert _engine_state(single) == _engine_state(merged_eng)
    assert sum(len(v) for v in _engine_state(merged_eng)[2].values()) > 100
    _, want = drive(ev_single, MARKS)
    _, got = drive(ev_merged, MARKS)
    assert sum(len(r.mints) for r in want) > 40 and any(r.actions for r in want)
    assert [sig(r) for r in got] == [sig(r) for r in want]


def test_a_seam_where_the_public_copy_trails_by_a_few_rows_keeps_clock_market_and_prints_as_sets():
    """The public tape lags the window, so around the seam a few public-only rows arrive after window rows of later slots. Order within the whole stream
    changes a little; the clock, the market minutes and the multiset of member prints do not (within-slot ORDER can differ: disclosed in the module docstring)."""
    from tools.test_r4b_live import mk_engine

    tape = _scenario_tape()
    n = len(tape)
    h, p = n // 3, 2 * n // 3
    pub = [{**tape[i][4], "t_recv_ms": 10 * i + 45} for i in range(p)]  # every public copy is about 4 rows late
    hel = [{**tape[i][4], "t_recv_ms": 10 * i} for i in range(h, n)]
    mg = fm.Merger()
    merged = [e for src, r in fm.merge_ordered({"public": pub, "helius": hel}) if (e := mg.offer(src, r, now_ms=r["t_recv_ms"])) is not None]
    assert len(merged) == n and [m["signature"] for m in merged] != [x[4]["signature"] for x in tape]  # the seam did reorder some rows
    single, merged_eng = mk_engine(), mk_engine()
    a1, a2 = fa.FeedAdapter(), fa.FeedAdapter()
    for (_, _, _, _, row) in tape:
        fa.apply(single, a1.feed(row))
    for row in merged:
        fa.apply(merged_eng, a2.feed(row))
    c1, m1, p1 = _engine_state(single, drop=("frn",))  # the arrival counter differs by construction; every other column is compared
    c2, m2, p2 = _engine_state(merged_eng, drop=("frn",))
    assert c1 == c2 and m1 == m2
    assert {k: sorted(v, key=repr) for k, v in p1.items()} == {k: sorted(v, key=repr) for k, v in p2.items()}
    assert sum(len(v) for v in p2.values()) > 100


def test_without_the_dedup_the_overlap_would_be_double_counted():
    """The failure Proof 1 guards against: both copies into the engine count each overlapping row twice in the market minutes."""
    from tools.test_r4b_live import mk_engine

    tape = _scenario_tape()
    pub, hel, h, p = _two_feeds(tape, lag=45)
    once, twice = mk_engine(), mk_engine()
    a1, a2 = fa.FeedAdapter(), fa.FeedAdapter()
    for _, row in fm.merge_ordered({"public": pub, "helius": hel}):
        fa.apply(twice, a2.feed(row))
    for _, _, _, _, row in tape:
        fa.apply(once, a1.feed(row))
    assert sum(v[0] + v[2] for v in twice.mkt.values()) > sum(v[0] + v[2] for v in once.mkt.values())


# ----------------------------------------------------------------------------------------------------------------------------------- completeness (pure)
def state(*, armed=1_000, tips=None, failure=None, failure_ms=None, mark=T, connected=900) -> fm.WindowState:
    st = fm.WindowState(mark, "processed", connect_ms=800, connected_ms=connected, armed_ms=armed, first_note_ms=1_100)
    st.tips = list(tips if tips is not None else [(1_200 + 400 * i, 500 + i) for i in range(20)])  # slots 500.. every 400 ms
    st.failure, st.failure_ms = failure, failure_ms
    return st


def test_complete_when_the_tip_passed_sd_the_window_covered_it_and_the_seam_is_closed():
    c = fm.mark_complete(T, 510, state(), public_hw_slot=520)
    assert c.complete and c.reason == "ok"
    assert c.ready_tip_slot == 511 and c.ready_ms == 1_200 + 400 * 11 and c.covered_from_slot == 501
    assert c.ready_after_mark_ms == c.ready_ms - T * 1000


def test_not_complete_while_the_tip_has_not_reached_sd_plus_one():
    st = state(tips=[(1_200 + 400 * i, 500 + i) for i in range(11)])  # tip 510 = sd, not sd + 1
    c = fm.mark_complete(T, 510, st, public_hw_slot=520)
    assert not c.complete and c.reason == "tip_below_sd_plus_1"
    st.tips.append((6_000, 511))
    assert fm.mark_complete(T, 510, st, public_hw_slot=520).complete


def test_not_complete_for_the_wrong_mark():
    assert fm.mark_complete(T, 510, state(mark=T + 3600), public_hw_slot=520).reason == "wrong_mark"


def test_not_complete_for_a_window_that_was_never_armed():
    assert fm.mark_complete(T, 510, state(armed=None), public_hw_slot=520).reason == "not_armed"


def test_sd_unknown_is_not_complete():
    assert fm.mark_complete(T, None, state(), public_hw_slot=520).reason == "sd_unknown"


def test_a_window_armed_after_sd_cannot_cover_it():
    """Armed at 1,000 ms but the first tip after it is slot 600: the window covers slots from 601, so it cannot vouch for sd 510 or 599."""
    st = state(armed=1_000, tips=[(1_200 + 400 * i, 600 + i) for i in range(5)])
    for sd in (510, 599):
        c = fm.mark_complete(T, sd, st, public_hw_slot=700)
        assert not c.complete and c.reason == "armed_late" and c.covered_from_slot == 601
    assert fm.mark_complete(T, 601, st, public_hw_slot=700).complete  # sd 601 is covered and the tip reached 602


def test_tips_before_the_arming_instant_do_not_count_as_coverage():
    st = state(armed=3_000, tips=[(1_000, 480), (1_400, 481), (3_100, 490), (3_500, 491), (3_900, 492)])
    assert fm.covered_from_slot(st) == 491
    assert fm.covered_from_slot(state(armed=None)) is None and fm.covered_from_slot(state(armed=9_999_999)) is None


def test_a_silent_stretch_over_two_seconds_between_arming_and_ready_is_not_complete():
    tips = [(1_200, 500), (1_600, 501), (4_000, 502), (4_400, 503)]  # 2.4 s without a tip
    c = fm.mark_complete(T, 501, state(tips=tips), public_hw_slot=520)
    assert not c.complete and c.reason == "silence"
    ok = [(1_200, 500), (1_600, 501), (3_500, 502), (3_900, 503)]  # 1.9 s is allowed
    assert fm.mark_complete(T, 501, state(tips=ok), public_hw_slot=520).complete
    assert fm.mark_complete(T, 501, state(tips=tips), public_hw_slot=520, silence_s=3.0).complete


def test_the_seam_must_be_closed_by_the_public_stream():
    assert fm.mark_complete(T, 510, state(), public_hw_slot=None).reason == "seam_unknown"
    assert fm.mark_complete(T, 510, state(), public_hw_slot=500).reason == "seam_open"  # the public stream has not reached the window's coverage (501)
    assert fm.mark_complete(T, 510, state(), public_hw_slot=501).complete
    assert fm.mark_complete(T, 510, state(), public_hw_slot=501, seam_margin_slots=5).reason == "seam_open"
    assert fm.mark_complete(T, 510, state(), public_hw_slot=None, require_seam=False).complete


def test_a_failure_at_or_before_ready_blocks_and_one_after_it_does_not():
    ready_ms = 1_200 + 400 * 11
    before = state(failure="decode_error", failure_ms=ready_ms - 1)
    at = state(failure="silence", failure_ms=ready_ms)
    after = state(failure="closed_early:ConnectionClosed", failure_ms=ready_ms + 1)
    assert fm.mark_complete(T, 510, before, public_hw_slot=520).reason == "window_failed:decode_error"
    assert fm.mark_complete(T, 510, at, public_hw_slot=520).reason == "window_failed:silence"
    assert fm.mark_complete(T, 510, after, public_hw_slot=520).complete
    cut = state(tips=[(1_200, 500)], failure="connect_failed", failure_ms=1_300)  # failed before the tip got there
    assert fm.mark_complete(T, 510, cut, public_hw_slot=520).reason == "window_failed:connect_failed"


def test_window_failed_reports_the_recorded_failure_and_a_live_silence():
    assert fm.window_failed(state()) is None
    assert fm.window_failed(state(failure="rpc_error", failure_ms=5)) == "rpc_error"
    st = state()
    last = st.tips[-1][0]
    assert fm.window_failed(st, now_ms=last + 1_999) is None and fm.window_failed(st, now_ms=last + 2_001) == "silence"
    st.closed_ms = last + 100
    assert fm.window_failed(st, now_ms=last + 9_000) is None  # a closed window is not "silent"
    never = fm.WindowState(T, connected_ms=1_000)  # connected, never a tip
    assert fm.tip_silence(never, 3_500) and not fm.tip_silence(never, 2_900) and not fm.tip_silence(fm.WindowState(T), 10**9)


def test_a_failed_window_becomes_a_gap_row_the_adapter_holds_buys_on():
    st = state(failure="silence", failure_ms=7_000)
    row = fm.window_gap_row(st)
    assert row["type"] == "gap" and row["data_loss"] is True and row["kind"] == "helius_window_failed" and row["to_ms"] == 7_000 and row["from_ms"] == 800
    out = fa.FeedAdapter().feed(row)
    assert [m.kind for m in out] == ["gap"] and out[0].halts_buys and out[0].row.instant_ms == 7_000
    assert fm.Merger().offer("helius", row) is not None  # a keyless row reaches the stream


# ----------------------------------------------------------------------------------------------------------------------------------- the socket (fake)
def test_a_window_opens_at_t_minus_30_subscribes_and_closes_at_t_plus_10_recording_the_mark():
    clock = Clock((T - 40) * 1000)
    frames = script()
    w, conn = window(clock, frames)
    all_bytes = sum(len(r) for _, r in frames)
    st = run(w)
    assert clock.slept == [10.0] and st.connect_ms == (T - 30) * 1000 and st.opened_late_ms == 0
    assert conn.urls == [FAKE_URL] and conn.closed
    assert [r["id"] for r in conn.ws.sent] == [1, 2, 3] and [r["method"] for r in conn.ws.sent] == ["slotSubscribe", "logsSubscribe", "logsSubscribe"]
    assert [r["params"][1]["commitment"] for r in conn.ws.sent[1:]] == ["processed", "processed"]
    assert [r["params"][0]["mentions"][0] for r in conn.ws.sent[1:]] == list(lp.PROGRAMS.values())
    assert st.failure is None and st.close_reason == "deadline" and (T + 10) * 1000 <= st.closed_ms < (T + 10) * 1000 + 300
    assert st.armed_ms == (T - 30) * 1000 + 100 and st.first_note_ms > st.armed_ms and st.connected_ms == (T - 30) * 1000
    assert st.bytes == all_bytes and len(st.tips) == sum(1 for _, r in frames if "slotNotification" in r)
    assert st.tips[0][1] == 1000 and [s for _, s in st.tips] == sorted(s for _, s in st.tips)
    assert st.credits() == pytest.approx(all_bytes / 1e5 * 3)
    rec = st.as_record()
    assert rec["bytes"] == all_bytes and rec["tips"][0] == [st.tips[0][0], 1000] and rec["connect_ms"] == (T - 30) * 1000
    json.dumps(rec)
    assert FAKE_KEY not in json.dumps(rec) and FAKE_KEY not in repr(w)


def test_the_commitment_is_a_parameter_and_must_be_known():
    clock = Clock((T - 40) * 1000)
    w, conn = window(clock, script(), commitment="confirmed")
    run(w)
    assert [r["params"][1]["commitment"] for r in conn.ws.sent[1:]] == ["confirmed", "confirmed"]
    with pytest.raises(ValueError):
        fm.HeliusWindow(T, FAKE_URL, commitment="finalized")


def test_the_window_closes_as_soon_as_the_caller_says_the_mark_is_complete():
    clock = Clock((T - 40) * 1000)
    sd = 1020
    w, _ = window(clock, script(), stop_when=lambda st: fm.mark_complete(T, sd, st, public_hw_slot=10**9).complete)
    st = run(w)
    assert st.close_reason == "complete" and st.failure is None
    assert max(s for _, s in st.tips) == sd + 1 and st.closed_ms < (T - 30) * 1000 + 9_000  # long before T + 10 s, and no byte read after the tip passed sd
    w2, _ = window(Clock((T - 40) * 1000), script())
    w2.complete()
    assert run(w2).close_reason == "complete"


def test_a_connect_failure_is_recorded_by_class_only():
    clock = Clock((T - 40) * 1000)
    w, conn = window(clock, [], connect=FakeConnect(exc=OSError(FAKE_URL)))
    st = run(w)
    assert st.failure == "connect_failed" and st.close_reason == "failed" and st.bytes == 0 and fm.window_failed(st) == "connect_failed"
    assert FAKE_KEY not in json.dumps(st.as_record()) and not fm.mark_complete(T, 1010, st, require_seam=False).complete
    assert fm.window_gap_row(st)["data_loss"] is True


def test_silence_over_two_seconds_fails_the_window_and_closes_it():
    clock = Clock((T - 40) * 1000)
    w, conn = window(clock, script(until_ms=(T - 30) * 1000 + 3_000))  # tips stop 3 s after the window opened
    st = run(w)
    assert st.failure == "silence" and st.close_reason == "failed" and conn.closed
    last = st.tips[-1][0]
    assert last + 2_000 < st.failure_ms <= last + 2_000 + 200 + 1  # detected at the first wake after the 2 s
    assert st.closed_ms < (T + 10) * 1000
    assert not fm.mark_complete(T, 1030, st, require_seam=False).complete


def test_a_socket_that_closes_early_is_data_loss():
    clock = Clock((T - 40) * 1000)
    frames = script(until_ms=(T - 30) * 1000 + 2_000) + [((T - 30) * 1000 + 2_100, ConnectionResetError("x"))]
    st = run(window(clock, frames)[0])
    assert st.failure == "closed_early:ConnectionResetError" and st.close_reason == "failed"


@pytest.mark.parametrize("bad,kind", [(json.dumps({"jsonrpc": "2.0", "error": {"code": -32602, "message": "x"}, "id": 2}), "rpc_error"), ("{not json", "bad_frame"),
                                      ("[1,2]", "bad_frame")])
def test_a_subscription_error_or_an_unreadable_frame_fails_the_window(bad, kind):
    clock = Clock((T - 40) * 1000)
    frames = script(until_ms=(T - 30) * 1000 + 1_000) + [((T - 30) * 1000 + 900, bad)]
    st = run(window(clock, frames)[0])
    assert st.failure == kind and st.close_reason == "failed"


def test_a_decode_failure_and_a_broken_callback_fail_the_window():
    def boom(*a):
        raise ValueError("cannot decode")

    for kw, kind in (({"to_rows": boom}, "decode_error"), ({"to_rows": lambda *a: [{"signature": "x"}], "on_rows": boom}, "callback_error")):
        clock = Clock((T - 40) * 1000)
        frames = script(until_ms=(T - 30) * 1000 + 1_000, extra=[((T - 30) * 1000 + 500, logs_msg(SIG, 1001, ["Program log: x"]))])
        w, _ = window(clock, frames, **kw)
        st = run(w)
        assert st.failure == kind and st.close_reason == "failed", kind


def test_a_late_start_is_recorded_and_a_start_after_the_end_is_refused():
    clock = Clock((T - 20) * 1000)  # the window should have opened 10 s ago
    st = run(window(clock, script())[0])
    assert clock.slept == [] and st.opened_late_ms == 10_000 and st.connect_ms == (T - 20) * 1000
    st2 = run(window(Clock((T + 11) * 1000), script())[0])
    assert st2.failure == "too_late" and st2.connect_ms is None


def test_logs_are_converted_and_delivered_and_a_transaction_on_both_subscriptions_is_decoded_once():
    clock = Clock((T - 40) * 1000)
    got, seen = [], []

    def to_rows(msg, recv_ms, commitment, pool_mints, counters):
        sig = msg["params"]["result"]["value"]["signature"]
        seen.append((commitment, sig))
        return [{"signature": sig, "type": "trade", "event_index": 0, "slot": 1001, "t_recv_ms": recv_ms}]

    open_ms = (T - 30) * 1000
    extra = [(open_ms + 500, logs_msg("A" * 88, 1001, ["x"])), (open_ms + 501, logs_msg("A" * 88, 1001, ["x"])), (open_ms + 700, logs_msg("B" * 88, 1001, ["y"]))]
    w, _ = window(clock, script(extra=extra), to_rows=to_rows, on_rows=lambda rows, ms: got.extend(rows))
    st = run(w)
    assert seen == [("processed", "A" * 88), ("processed", "B" * 88)]  # the second delivery of A is not decoded again
    assert [r["signature"] for r in got] == ["A" * 88, "B" * 88] and st.rows == 2 and st.cross_dedup == 1 and st.notes == 3
    assert [r["t_recv_ms"] for r in got] == [open_ms + 500, open_ms + 700]


def test_the_default_connect_opens_the_socket_as_the_probe_does_with_compression_off(monkeypatch):
    seen = {}
    fake_ws = types.ModuleType("websockets")
    fake_ws.connect = lambda url, **kw: seen.update(url=url, **kw) or "CM"
    fake_certifi = types.ModuleType("certifi")
    fake_certifi.where = lambda: "/nonexistent"
    monkeypatch.setitem(sys.modules, "websockets", fake_ws)
    monkeypatch.setitem(sys.modules, "certifi", fake_certifi)
    import ssl

    monkeypatch.setattr(ssl, "create_default_context", lambda cafile=None: ("ctx", cafile))
    assert fm.default_connect("wss://x.test/") == "CM"
    assert seen["compression"] is None and seen["url"] == "wss://x.test/" and seen["ssl"] == ("ctx", "/nonexistent") and seen["open_timeout"] == 15


def test_read_url_reads_the_key_file_inside_the_process(tmp_path):
    kf = tmp_path / "k.env"
    kf.write_text(f"HELIUS_API_KEY={FAKE_KEY}\n")
    assert fm.read_url(str(kf)) == f"{lp.BASE}?api-key={FAKE_KEY}"
    kf.write_text("OTHER=1\n")
    with pytest.raises(ValueError) as ei:
        fm.read_url(str(kf))
    assert FAKE_KEY not in str(ei.value)
    with pytest.raises(ValueError):
        fm.read_url(str(tmp_path / "missing.env"))


# ----------------------------------------------------------------------------------------------------------------------------------- conversion = arena_feed's
def test_a_window_notification_converts_to_exactly_the_rows_arena_feed_makes(arena_feed_mod):
    """The window reuses tools/arena_feed.rows_from_notice (imported, not copied): the rows equal the feed's own for the same notice, on both programs."""
    from observe.trade_source import RawNotice

    af = arena_feed_mod
    for fixture, prog in (("pumpswap_buy_event.b64", "pumpswap"), ("pump_trade_event.b64", "pumpfun")):
        logs = [fx(fixture)]
        got = fm.notice_rows(json.loads(logs_msg(SIG, 4242, logs)), 1_790_318_932_000, "processed", {}, Counter())
        want = af.rows_from_notice(RawNotice(slot=4242, signature=SIG, failed=False, logs=tuple(logs), t_recv_ms=1_790_318_932_000, commitment="processed",
                                             feed="public_rpc_logs"), {}, Counter())
        assert got == want and len(got) == 1 and got[0]["prog"] == prog and got[0]["type"] == "trade"
        assert got[0]["slot"] == 4242 and got[0]["signature"] == SIG and got[0]["t_recv_ms"] == 1_790_318_932_000 and isinstance(got[0]["event_index"], int)
        assert "feed" not in got[0] and "commitment" not in got[0]  # the feed's own field drops
    assert fm.notice_rows(json.loads(logs_msg(SIG, 1, ["Program log: nothing"])), 1, "processed", {}, Counter()) == []
    assert fm.notice_rows(json.loads(logs_msg(SIG, 1, [fx("pumpswap_buy_event.b64")], err={"InstructionError": [0, "x"]})), 1, "processed", {}, Counter()) == []
    with pytest.raises(ValueError):
        fm.notice_rows({"method": "logsNotification", "params": {"result": {"context": {}, "value": {}}}}, 1, "processed", {}, Counter())


def test_a_real_conversion_window_feeds_the_merger_and_the_adapter_without_rejects(arena_feed_mod):
    """Default converter end to end on the recorded fixtures: window frames -> tape rows -> Merger (the public copy of the same tx is the duplicate) -> adapter."""
    from observe.trade_source import RawNotice

    af = arena_feed_mod
    clock = Clock((T - 40) * 1000)
    mg = fm.Merger(clock_ms=clock.now)
    extra = [((T - 30) * 1000 + 500, logs_msg(SIG, 1001, [fx("pumpswap_buy_event.b64")])),
             ((T - 30) * 1000 + 600, logs_msg("B" * 88, 1002, [fx("pump_trade_event.b64")]))]
    w, _ = window(clock, script(extra=extra), to_rows=None, pool_mints=mg.pool_mints, on_rows=lambda rows, ms: mg.offer_many("helius", rows, ms))
    st = run(w)
    assert st.failure is None and st.rows == 2 and mg.stats["emitted_helius"] == 2
    pub_copy = af.rows_from_notice(RawNotice(slot=1001, signature=SIG, failed=False, logs=(fx("pumpswap_buy_event.b64"),), t_recv_ms=(T - 30) * 1000 + 1_400,
                                             commitment="confirmed", feed="public_rpc_logs"), {}, Counter())
    assert mg.offer_many("public", pub_copy) == [] and mg.stats["dup_helius_then_public"] == 1
    ad = fa.FeedAdapter()
    kinds = [m.kind for r in mg.offer_many("public", []) + [pub_copy[0]] for m in ad.feed(r)]
    assert "reject" not in kinds and ad.stats["rows_trade"] == 1


# ----------------------------------------------------------------------------------------------------------------------------------- credits (Proof 5)
class Wall:
    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


DAY1 = 1_786_665_600 + 3600  # 2026-08-14 hour 1 UTC? (a fixed instant; only the date changes are asserted)


def test_the_cap_is_required_and_must_be_a_positive_number(tmp_path):
    with pytest.raises(TypeError):
        fm.DailyCreditCap(state_path=tmp_path / "s.json")  # type: ignore[call-arg]
    for bad in (0, -1, float("nan"), float("inf"), True, "100"):
        with pytest.raises(ValueError):
            fm.DailyCreditCap(bad, tmp_path / "s.json")  # type: ignore[arg-type]


def test_credits_are_three_per_tenth_of_a_megabyte_and_the_cap_stops_the_socket(tmp_path):
    cap = fm.DailyCreditCap(30, tmp_path / "s.json", clock=Wall(DAY1))
    assert fm.CREDITS_PER_TENTH_MB == 3 and cap.credits() == 0 and cap.can_open() and cap.buy_hold() is None
    assert cap.add(100_000) is True and cap.credits() == pytest.approx(3.0)
    assert cap.add(800_000) is True and cap.credits() == pytest.approx(27.0)
    assert cap.add(99_999) is True  # 29.99997 credits: below the cap
    assert cap.add(1) is False and cap.stopped and cap.credits() == pytest.approx(30.0)  # reached
    assert cap.add(50_000) is False  # trailing frames keep being counted
    assert not cap.can_open() and cap.buy_hold().startswith("helius_credit_cap_reached")
    assert "30" in cap.buy_hold() and cap.day in cap.buy_hold()


def test_no_window_opens_when_the_largest_window_would_not_fit(tmp_path):
    cap = fm.DailyCreditCap(31, tmp_path / "s.json", clock=Wall(DAY1))
    cap.add(500_000)  # 15 credits
    cap.window_done(500_000)  # the largest window so far costs 15
    assert cap.can_open() and cap.buy_hold() is None  # 15 + 15 = 30 < 31
    cap.add(40_000)  # 16.2 credits spent: 16.2 + 15 = 31.2 does not fit
    assert not cap.can_open() and cap.buy_hold().startswith("helius_credit_cap_no_room_for_a_window")
    assert cap.can_open(est_bytes=0) and not cap.stopped  # not reached, only no room for the next window


def test_the_cap_state_survives_a_restart_and_the_stop_is_written_at_once(tmp_path):
    p = tmp_path / "sub" / "s.json"
    cap = fm.DailyCreditCap(30, p, clock=Wall(DAY1))
    cap.add(300_000)
    cap.window_done(300_000)  # flushes
    again = fm.DailyCreditCap(30, p, clock=Wall(DAY1 + 60))
    assert again.bytes == 300_000 and again.windows == 1 and again.max_window_bytes == 300_000 and again.credits() == pytest.approx(9.0)
    again.add(700_000)  # reaches the cap: persisted without a flush call
    third = fm.DailyCreditCap(30, p, clock=Wall(DAY1 + 120))
    assert third.stopped and not third.can_open() and third.buy_hold() is not None
    assert [f.name for f in p.parent.iterdir()] == ["s.json"]  # no tmp file is left behind
    doc = json.loads(p.read_text())
    assert doc["v"] == 1 and doc["day"] == third.day and doc["stopped"] is True


def test_a_new_utc_day_resets_the_count_and_keeps_the_record_lines(tmp_path):
    clk = Wall(DAY1)
    cap = fm.DailyCreditCap(30, tmp_path / "s.json", clock=clk)
    cap.add(1_000_000)
    assert cap.stopped
    day1 = cap.day
    clk.t += 86_400
    assert cap.can_open() and not cap.stopped and cap.bytes == 0 and cap.day != day1 and cap.buy_hold() is None
    cap.add(200_000)
    cap.window_done(200_000)
    lines = cap.day_lines()
    assert [x["date"] for x in lines] == [day1, cap.day]
    assert lines[0] == {"date": day1, "bytes": 1_000_000, "credits": 30.0, "windows": 0, "stopped": True}
    assert lines[1] == {"date": cap.day, "bytes": 200_000, "credits": 6.0, "windows": 1, "stopped": False}
    assert [json.loads(x) for x in cap.jsonl().splitlines()] == lines
    back = fm.DailyCreditCap(30, tmp_path / "s.json", clock=clk)  # the lines are in the state file
    assert back.day_lines() == lines


def test_a_clock_that_moves_back_does_not_reset_the_count(tmp_path):
    clk = Wall(DAY1)
    cap = fm.DailyCreditCap(30, tmp_path / "s.json", clock=clk)
    cap.add(1_000_000)
    clk.t -= 3 * 86_400
    assert not cap.can_open() and cap.clock_back >= 1 and cap.stopped


def test_an_unreadable_state_file_stops_the_day_and_is_kept_aside(tmp_path):
    p = tmp_path / "s.json"
    p.write_text("{not json")
    clk = Wall(DAY1)
    cap = fm.DailyCreditCap(30, p, clock=clk)
    assert cap.state_error == "unreadable_state_file" and cap.stopped and not cap.can_open() and cap.buy_hold() is not None
    assert any(f.name.startswith("s.json.corrupt-") for f in tmp_path.iterdir())
    clk.t += 86_400
    assert cap.can_open()  # the next UTC day starts clean
    p.write_text(json.dumps({"v": 2}))
    assert fm.DailyCreditCap(30, p, clock=Wall(DAY1)).stopped  # a state of another shape is also not trusted


def test_the_window_counts_every_frame_against_the_cap_and_closes_at_the_cap(tmp_path):
    clock = Clock((T - 40) * 1000)
    cap = fm.DailyCreditCap(0.05, tmp_path / "s.json", clock=Wall(DAY1))  # 0.05 credits = 1,667 bytes
    w, conn = window(clock, script(), cap=cap)
    st = run(w)
    assert st.failure == "cap_reached" and st.close_reason == "failed" and cap.stopped and conn.closed
    assert st.bytes == cap.bytes and cap.credits() >= 0.05 and cap.windows == 1
    assert st.closed_ms < (T + 10) * 1000
    c2 = Clock((T - 40) * 1000)
    w2, conn2 = window(c2, script(), cap=cap)
    st2 = run(w2)
    assert st2.failure == "cap_closed" and st2.close_reason == "cap_closed" and conn2.urls == [] and st2.bytes == 0 and c2.slept == [10.0]
    assert cap.buy_hold() is not None


def test_a_window_under_the_cap_records_its_bytes_on_the_day(tmp_path):
    clock = Clock((T - 40) * 1000)
    cap = fm.DailyCreditCap(10**6, tmp_path / "s.json", clock=Wall(DAY1))
    st = run(window(clock, script(), cap=cap)[0])
    assert st.failure is None and cap.bytes == st.bytes and cap.windows == 1 and cap.max_window_bytes == st.bytes
    assert fm.DailyCreditCap(10**6, tmp_path / "s.json", clock=Wall(DAY1)).bytes == st.bytes


# ----------------------------------------------------------------------------------------------------------------------------------- clock (Proof 6)
CHRONY = """Reference ID    : A9FEA97B (169.254.169.123)
Stratum         : 4
Ref time (UTC)  : Sat Oct 31 00:45:00 2026
System time     : {off} seconds {side} of NTP time
Last offset     : +0.000000123 seconds
RMS offset      : 0.000010000 seconds
Leap status     : {leap}
"""


def runner(td=(0, "yes\n"), chrony=(0, CHRONY.format(off="0.000012345", side="fast", leap="Normal"))):
    calls = []

    def run_(argv, timeout):
        calls.append((list(argv), timeout))
        return td if argv[0] == "timedatectl" else chrony
    run_.calls = calls
    return run_


def test_ntp_status_reads_timedatectl_and_the_chrony_offset():
    r = runner()
    s = fm.ntp_status(r)
    assert s.synced is True and s.offset_ms == pytest.approx(0.012345)
    assert r.calls[0][0] == ["timedatectl", "show", "-p", "NTPSynchronized", "--value"] and r.calls[1][0] == ["chronyc", "tracking"]
    assert all(t == fm.NTP_TIMEOUT_S for _, t in r.calls)
    assert s.as_record()["ntp_synchronized"] is True and "timedatectl" in s.as_record()["detail"]
    slow = fm.ntp_status(runner(chrony=(0, CHRONY.format(off="0.020000000", side="slow", leap="Normal"))))
    assert slow.offset_ms == pytest.approx(-20.0)


def test_require_synced_passes_inside_the_bound_and_returns_the_status_to_log():
    s = fm.require_synced(runner=runner(chrony=(0, CHRONY.format(off="0.049000000", side="fast", leap="Normal"))))
    assert s.synced and s.offset_ms == pytest.approx(49.0)
    assert fm.NTP_MAX_OFFSET_MS == 50.0


@pytest.mark.parametrize("r,why", [(runner(td=(0, "no\n")), "not confirmed"), (runner(td=(127, "")), "not confirmed"), (runner(td=(0, "weird")), "not confirmed"),
                                   (runner(chrony=(0, CHRONY.format(off="0.080000000", side="fast", leap="Normal"))), "exceeds 50"),
                                   (runner(chrony=(0, CHRONY.format(off="0.080000000", side="slow", leap="Normal"))), "exceeds 50"),
                                   (runner(chrony=(0, CHRONY.format(off="0.000001000", side="fast", leap="Not synchronised"))), "not confirmed")])
def test_require_synced_raises_when_unsynced_unreadable_or_over_the_bound(r, why):
    with pytest.raises(fm.ClockNotSynced, match=why):
        fm.require_synced(runner=r)


def test_chrony_is_optional_and_the_bound_is_a_parameter():
    s = fm.require_synced(runner=runner(chrony=(127, "")))
    assert s.synced is True and s.offset_ms is None and "chronyc rc=127" in s.detail
    with pytest.raises(fm.ClockNotSynced):
        fm.require_synced(max_offset_ms=0.001, runner=runner())
    assert fm.parse_chrony("nothing useful here") == (None, None)


def test_the_default_runner_runs_a_fixed_argv_and_maps_failures_to_codes():
    assert fm.default_runner([sys.executable, "-c", "print('x')"], 10) == (0, "x\n")
    assert fm.default_runner(["/nonexistent/definitely-not-here"], 1) == (127, "")
    assert fm.default_runner([sys.executable, "-c", "import time; time.sleep(5)"], 0.3) == (124, "")


# ----------------------------------------------------------------------------------------------------------------------------------- hygiene
def test_no_live_path_no_send_and_no_key_in_the_module():
    import inspect

    from tools import r4b_live as lv

    assert fm.LIVE_BUILT is False and lv.LIVE_BUILT is False
    src = inspect.getsource(fm).split('"""', 2)[2]
    for word in ("probe_live", "LiveExecutor", "Keypair", "sendTransaction", "send_transaction", "private_key", "getBlock", "print("):
        assert word not in src, word
    assert src.count("api-key=") == 1  # only the URL builder (read_url) names the query parameter, and it reads the key from the file inside the call
