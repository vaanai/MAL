"""Tests for tools/r4b_live_host.py. SYNTHETIC data and fake sources only: no tape, no network, no key, no sealed path.

  PYTHONPATH=/data/mal/audit-1008/venv/lib/python3.12/site-packages /data/mal/venv/bin/python -m pytest tools/test_r4b_live_host.py
Run from a CLEAN tree: DryHost.start() calls repo_head_sha(), which refuses modified tracked files (a test pins that refusal).
QP-RULING-1030 Addendum 5: the certified trigger and its P-a2 tests (i) to (iv), the go/no-go read rules (b1, b2) and the late_public / abandoned fallback (b3).
"""

from __future__ import annotations

import inspect
import json
from datetime import datetime, timezone

import numpy as np
import pytest

pytest.importorskip("duckdb")

from tools import r4b_feed_adapter as fa  # noqa: E402
from tools import r4b_feed_merge as fm  # noqa: E402
from tools import r4b_live as lv  # noqa: E402
from tools import r4b_live_host as hh  # noqa: E402
from tools.arena_declog import DecisionLog  # noqa: E402
from tools.test_r4b_feed_adapter import _to_tape  # noqa: E402
from tools.test_r4b_live import BT0, MARKS, S, _score_ret, event_stream, pda_of, scenario  # noqa: E402

SHA = "ab" * 20
T_FIRST = BT0 + 3600  # the first mark after a start at BT0 + 1 s
START_MS = BT0 * 1000 + 1000
HOUR_MS = 3_600_000


def tip_at(ms: int) -> int:
    """The synthetic chain's slot at wall time ms (the scenario's 2.5 slots a second)."""
    return S + int((ms / 1000.0 - BT0) * 2.5)


class Clock:
    now = 0

    def __call__(self) -> int:
        return self.now


class FakeSource:
    """Tape rows at their receive time (event_ts + 7 ms), idle ticks every 250 ms from T - 6 s to T + 25 s of each mark and every 5 s to T + 1000 s. mark_complete
    is true complete_ms after the mark unless the mark is in `never` (or `verdict(T, sd, now)` answers); failed / hold are scripted. tip_slot follows the wall
    clock. public_hw_slot is the tip minus public_lag slots (None: the public stream never reports; public_after_ms: only that long after the mark).
    window_closed is true window_closed_ms after the mark. The iterator is persistent: a second run() continues where the first stopped."""

    def __init__(self, clock: Clock, marks, *, complete_ms: int = 300, never=(), fail=None, hold_windows=(), drop_window=None, tip=True, public_lag=7,
                 public_after_ms: int = 0, window_closed_ms: int = 10_000, late_ticks: bool = True, verdict=None):
        self.clock, self.complete_ms, self.never, self.fail, self.hold_windows, self.tip = clock, complete_ms, set(never), dict(fail or {}), list(hold_windows), tip
        self.public_lag, self.public_after_ms, self.window_closed_ms, self.verdict = public_lag, public_after_ms, window_closed_ms, verdict
        items = []
        for t, o, n, kind, r in event_stream(scenario()):
            if drop_window and drop_window[0] <= r["block_time"] < drop_window[1]:
                continue
            items.append((_to_tape(kind, r)["t_recv_ms"], 0, _to_tape(kind, r)))
        for T in marks:
            items += [(T * 1000 + dt, 1, None) for dt in range(-6000, 25001, 250)]
            if late_ticks:
                items += [(T * 1000 + dt, 1, None) for dt in range(30_000, 1_000_001, 5000)]
        self.items = sorted(items, key=lambda x: (x[0], x[1]))
        self._gen = self._iterate()

    def _iterate(self):
        for t, _, row in self.items:
            self.clock.now = t
            yield None if row is None else (row, t)

    def __iter__(self):
        return self._gen

    def mark_complete(self, T, sd):
        if self.verdict is not None:
            return self.verdict(T, sd, self.clock.now)
        return T not in self.never and self.clock.now >= T * 1000 + self.complete_ms

    def failed(self, T):
        f = self.fail.get(T)
        return f[0] if f and self.clock.now >= f[1] else None

    def buy_hold(self):
        return "credit_cap" if any(a <= self.clock.now < b for a, b in self.hold_windows) else None

    def tip_slot(self, commitment="processed"):
        if not self.tip:
            return None
        return tip_at(self.clock.now) - (0 if commitment == "processed" else 2)  # confirmed trails by 2 slots

    def public_hw_slot(self):
        if self.public_lag is None:
            return None
        rel = self.clock.now - round(self.clock.now / HOUR_MS) * HOUR_MS  # ms from the nearest mark
        return None if rel < self.public_after_ms else tip_at(self.clock.now) - self.public_lag

    def window_closed(self, T):
        return self.clock.now >= T * 1000 + self.window_closed_ms


def host_engine(**kw):
    kw.setdefault("universe_min_mbt", BT0)
    return lv.new_host_engine(warmup_s=7200, shorter_warmup_proof="test-ref", pda_fn=pda_of, hash_fn=lv.DuckHash(), score_fn=_score_ret, **kw)


def make_host(tmp_path, marks, *, trigger="guard", change_id="t1", **src_kw):
    clock = Clock()
    host = hh.DryHost(host_engine(), DecisionLog(tmp_path / "declog"), FakeSource(clock, marks, **src_kw), out_dir=tmp_path, now_ms=clock, frozen_sha=SHA,
                      shorter_warmup_proof="test-ref", hostname="mal-fast-0", ntp_offset_ms=3.0, trigger=trigger, change_id=change_id)
    return host, clock


def body(r):
    return {k: v for k, v in r.items() if k not in ("seq", "prev", "sha", "ms", "arm")}


def reference(marks, hold_marks=()):
    """A bare engine fed the same tape through the adapter (no host, no precompute), decided at each mark: its declog row bodies per mark."""
    eng, ad, i = host_engine(), fa.FeedAdapter(), 0
    items = [(r["t_recv_ms"], r) for _, _, _, k, r in [(0, 0, 0, k, _to_tape(k, r)) for _, _, _, k, r in event_stream(scenario())]]
    items.sort(key=lambda x: x[0])
    log, out = DecisionLog(__import__("tempfile").mkdtemp()), {}
    for T in marks:
        while i < len(items) and items[i][0] < (T + 30) * 1000:
            fa.apply(eng, ad.feed(items[i][1]))
            i += 1
        res = eng.decide(T, hold_entries=T in hold_marks)
        out[T] = [body(r) for r in lv.log_actions(log, res, {m.mint: m for m in eng.members()}, T * 1000)] if res.actions else []
    return out


def declog_by_mark(host):
    out = {}
    for r in host.log.rows(lv.BOT_ID):
        if r["kind"] in ("entry", "exit"):
            out.setdefault(r["t_mark"], []).append(body(r))
    return out


def _lines(host):
    return [json.loads(x) for x in host.latency_path.read_text().splitlines()]


def latency(host):
    """The per-mark records (not the mark_late follow-ups)."""
    return [r for r in _lines(host) if r["rec"] == "mark"]


def late_recs(host):
    return {r["mark"]: r for r in _lines(host) if r["rec"] == "mark_late"}


def events(host):
    return [json.loads(x) for x in host.events_path.read_text().splitlines()]


def first_second_at_or_after(T):
    """(s*, sd): the scenario's first trade second at or after T and its min slot (what the engine's clock gives)."""
    rows = [r for h in scenario().values() for r in h["trades"] if r["block_time"] >= T]
    s_star = min(r["block_time"] for r in rows)
    return s_star, min(r["slot"] for r in rows if r["block_time"] == s_star)


def late_trade(slot, ts, sig, mint="LATEMINT"):
    """A tape bonding trade row (a valid input of the adapter) for the late-arrival tests."""
    return {"v": 1, "type": "trade", "prog": "pumpfun", "venue": "pump_bonding", "mint": mint, "mint_source": "event", "trader": "TRX", "side": "buy",
            "sol_lamports": 10**8, "token_raw": 10**9, "event_ts": ts, "slot": slot, "t_recv_ms": ts * 1000 + 7, "signature": sig, "event_index": 0, "pool": None,
            "quote_reserve": 30 * 10**9, "base_reserve": 10**15, "quote_mint": None}


RUN = MARKS[:1] + [T_FIRST + 3600 * i for i in range(5)]  # not used for ordering; see MKS
MKS = [T_FIRST + 3600 * i for i in range(5)]  # 01:00 .. 05:00 (the first two are inside the 7200 s warm-up)


# ------------------------------------------------------------------------------------------------------------------------------- decisions
@pytest.mark.parametrize("trigger", ["guard", "certified"])
def test_host_decisions_equal_a_bare_engine_and_the_precompute_is_used(tmp_path, trigger):
    host, clock = make_host(tmp_path, MKS, trigger=trigger)
    out = host.start(START_MS)
    assert out["exits"] == [] and out["start"]["frozen_sha"] == SHA and out["start"]["warmup_s"] == 7200 and host.next_mark == T_FIRST
    counts = host.run(max_marks=5)
    want = reference(MKS)
    got = declog_by_mark(host)
    assert counts["marks_decided"] == 5 and counts.get("marks_late_public", 0) == 0 and counts.get("marks_abandoned", 0) == 0
    assert any(any(r["kind"] == "entry" for r in v) for v in got.values()), "the scenario must reach entries after the warm-up"
    assert {T: v for T, v in got.items()} == {T: v for T, v in want.items() if v}  # entries and exits, every field, in order
    lat = latency(host)
    assert [r["warmup"] for r in lat] == [True, True, False, False, False] and all(r["precompute"]["ran"] and r["precompute"]["rel_ms"] < 0 for r in lat)
    assert sum(r["precompute"]["hits"] for r in lat) > 10 and host.engine.counters["precompute_hits"] > 10
    assert log_chain_ok(host)


def log_chain_ok(host):
    return host.log.verify(lv.BOT_ID)[1] == len(host.log.rows(lv.BOT_ID)) - 1


def test_latency_record_fields_slot_s_and_the_go_quantity(tmp_path):
    host, clock = make_host(tmp_path, MKS)
    host.start(START_MS)
    host.run(max_marks=5)
    for r in latency(host):
        assert r["status"] == "decided" and r["hostname"] == "mal-fast-0" and r["ntp_offset_ms"] == 3.0 and r["trigger"] == "guard" and r["change_id"] == "t1"
        assert r["t_signal_ms"] is None and r["d_ready_signal"] is None and r["t_guard_ms"] == r["t_ready_ms"] and r["d_ready_guard"] == r["d_ready"]  # the guard run
        sd = r["sd"]
        assert r["d_ready"] == tip_at(r["t_ready_ms"]) - sd and r["d_decide"] == tip_at(r["t_decide_ms"]) - sd and r["d_would_send"] == tip_at(r["t_would_send_ms"]) - sd
        assert r["confirmed"]["d_would_send"] == r["d_would_send"] - 2 and r["processed"]["d_would_send"] == r["d_would_send"]
        assert r["go_qty"] == r["d_would_send"] + 4 and 0 <= r["d_ready"] <= r["d_decide"] <= r["d_would_send"]
        assert r["wall_ready_ms"] <= r["wall_decide_ms"] <= r["wall_would_send_ms"] and r["wall_ready_ms"] >= 300
    last = latency(host)[-1]
    assert last["slot_s_provisional"] is True and last["slot_s_hours"] == 4  # a date of 8 h data cannot be final
    assert last["slot_s"] == pytest.approx(0.4, rel=1e-3) and (last["k_1_9"], last["k_1_3"]) == (5, 3)  # round(4.75), round(3.25)
    assert last["go_within_k_1_9"] == (last["go_qty"] <= 5)
    first = latency(host)[0]
    assert first["slot_s"] is None and first["k_1_9"] is None and first["go_within_k_1_9"] is None  # the first hour of the first day is not measured yet


def test_a_source_without_a_tip_gives_none_d_fields_and_never_a_go(tmp_path):
    host, clock = make_host(tmp_path, MKS[:2], tip=False)
    host.start(START_MS)
    host.run(max_marks=2)
    assert all(r["d_ready"] is None and r["d_would_send"] is None and r["go_qty"] is None for r in latency(host))


# ------------------------------------------------------------------------------------------------------------------------------- start
def test_start_refuses_a_dirty_tree_a_missing_warmup_and_writes_the_restart_exits(tmp_path, monkeypatch):
    clock = Clock()
    src = FakeSource(clock, MKS[:1])
    mk = lambda root, eng=None, sha=None: hh.DryHost(eng or host_engine(), DecisionLog(root), src, out_dir=root, now_ms=clock, frozen_sha=sha,  # noqa: E731
                                                    shorter_warmup_proof="test-ref", change_id="t1")
    monkeypatch.setattr(lv, "repo_head_sha", lambda *a, **k: (_ for _ in ()).throw(lv.LiveRefused("modified tracked")))
    with pytest.raises(lv.LiveRefused, match="modified tracked"):
        mk(tmp_path / "a").start(START_MS)  # no explicit sha: the dirty-tree refusal stops the start, nothing is written
    assert DecisionLog(tmp_path / "a").rows(lv.BOT_ID) == []
    with pytest.raises(lv.LiveRefused, match="warm-up engine"):
        mk(tmp_path / "b", eng=lv.Engine(universe_min_mbt=BT0, pda_fn=pda_of, hash_fn=lv.DuckHash(), score_fn=_score_ret), sha=SHA).start(START_MS)
    # a previous process left two positions open: start writes the start row, then one restart exit each, before the engine decides anything
    log = DecisionLog(tmp_path / "c")
    log.append(lv.BOT_ID, START_MS - 5000, {"kind": "start", "book": lv.BOOK, "schema": lv.SCHEMA, "frozen_sha": SHA})
    for m in ("MX", "MY"):
        log.append(lv.BOT_ID, START_MS - 4000, {"kind": "entry", "order_id": f"{m}:{T_FIRST - 3600}", "meta": {"mint": m}, "t_mark": T_FIRST - 3600})
    h = mk(tmp_path / "c", sha=SHA)
    out = h.start(START_MS)
    assert [r["reason"] for r in out["exits"]] == ["restart", "restart"] and [r["kind"] for r in log.rows(lv.BOT_ID)[-3:]] == ["start", "exit", "exit"]
    with pytest.raises(lv.LiveRefused, match="already started"):
        h.start(START_MS)
    with pytest.raises(lv.LiveRefused, match="start"):
        mk(tmp_path / "d", sha=SHA).run()


def test_the_start_row_and_event_record_the_trigger_and_change_id_and_a_trigger_cannot_change_in_a_run(tmp_path):
    host, clock = make_host(tmp_path, MKS[:1], trigger="certified", change_id="c.2026-11-02_a")
    host.start(START_MS)
    srow = [r for r in host.log.rows(lv.BOT_ID) if r["kind"] == "start"][0]
    assert srow["trigger"] == "certified" and srow["change_id"] == "c.2026-11-02_a" and srow["frozen_sha"] == SHA
    ev = [e for e in events(host) if e["event"] == "start"][0]
    assert ev["trigger"] == "certified" and ev["change_id"] == "c.2026-11-02_a" and ev["late_bound_s"] == 900.0 and ev["watch_bound_s"] == 900.0
    assert host.trigger == "certified"
    with pytest.raises(AttributeError):
        host.trigger = "guard"  # fixed at construction: one trigger per run
    for bad in ("both", "Guard", "", None):
        with pytest.raises(lv.LiveRefused, match="trigger must be"):
            make_host(tmp_path / "bad", MKS[:1], trigger=bad)
    with pytest.raises(lv.LiveRefused, match="change_id must match"):
        make_host(tmp_path / "bad2", MKS[:1], change_id="has space")
    h2, _ = make_host(tmp_path / "nochange", MKS[:1], change_id=None)
    with pytest.raises(lv.LiveRefused, match="needs a change_id"):
        h2.start(START_MS)
    assert DecisionLog(tmp_path / "nochange" / "declog").rows(lv.BOT_ID) == []  # nothing was written
    # a restart of the same out_dir under the same change_id but another trigger is refused; a new change_id is a declared change
    host.run(max_marks=1)
    h3, _ = make_host(tmp_path, MKS[:1], trigger="guard", change_id="c.2026-11-02_a")
    with pytest.raises(lv.LiveRefused, match="one trigger per run"):
        h3.start(START_MS + 5000)
    h4, _ = make_host(tmp_path, MKS[:1], trigger="guard", change_id="c.2026-11-02_b")
    h4.start(START_MS + 5000)
    assert [r["trigger"] for r in [r for r in h4.log.rows(lv.BOT_ID) if r["kind"] == "start"]] == ["certified", "guard"]


# ------------------------------------------------------------------------------------------------------------------------------- gaps
def test_a_feed_gap_that_loses_data_writes_a_gap_row_and_a_reconnect_writes_nothing(tmp_path):
    host, clock = make_host(tmp_path, MKS[:1])
    host.start(START_MS)
    lossy = {"v": 1, "type": "gap", "kind": "data_loss", "prog": "pumpswap", "from_ms": START_MS + 100, "to_ms": START_MS + 900, "data_loss": True, "closed": True,
             "t_recv_ms": START_MS + 900, "signature": None}
    clock.now = START_MS + 900
    host._ingest(lossy, clock.now)
    host._ingest({**lossy, "kind": "socket_reconnect", "data_loss": False}, clock.now)
    gaps = [r for r in host.log.rows(lv.BOT_ID) if r["kind"] == "gap"]
    assert len(gaps) == 1 and gaps[0]["gap_kind"] == "data_loss" and gaps[0]["data_loss"] is True and host.counts["gap_rows"] == 1
    host._ingest({"v": 1, "type": "nonsense", "t_recv_ms": clock.now}, clock.now)  # a reject halts buys: also a gap row
    assert [r["gap_kind"] for r in host.log.rows(lv.BOT_ID) if r["kind"] == "gap"] == ["data_loss", "reject_unknown_type"]


# ------------------------------------------------------------------------------------------------------------------------------- fail closed (ruling b3)
@pytest.mark.parametrize("trigger", ["guard", "certified"])
def test_incomplete_inputs_write_the_gap_row_wait_for_the_public_stream_then_write_exits_only(tmp_path, trigger):
    """Ruling (b3): a mark the source cannot certify writes NO rank exit from what had arrived at T + 20 s. The host writes the gap row, waits for the public
    stream (here 60 s after the mark), then decides on the complete inputs with the entries held: exits only, status late_public."""
    bad = MKS[3]  # 04:00, after the warm-up with positions held
    host, clock = make_host(tmp_path, MKS, trigger=trigger, never=[bad], public_after_ms=60_000)
    host.start(START_MS)
    host.run(max_marks=5)
    want = reference(MKS)
    got = declog_by_mark(host)
    held_ref = reference(MKS, hold_marks=(bad,))
    assert got.get(MKS[2]) == want[MKS[2]] and any(r["kind"] == "entry" for r in want[MKS[2]])
    assert not any(r["kind"] == "entry" for r in got.get(bad, [])) and any(r["kind"] == "entry" for r in want[bad])  # the entries were skipped
    assert got.get(bad, []) == held_ref[bad] and any(r["kind"] == "exit" for r in held_ref[bad])  # the exits of the complete inputs, entries held
    lat = {r["mark"]: r for r in latency(host)}
    r = lat[bad]
    assert r["status"] == "late_public" and r["reason"] == "bound_incomplete" and r["n_entries"] == 0 and r["n_exits"] > 0 and r["n_suppressed"] > 0
    assert r["t_incomplete_ms"] == bad * 1000 + 20_000 and r["wall_decide_ms"] >= 60_000 and r["wall_decide_ms"] < 61_000 and r["public_hw_slot"] >= r["sd"] + 1
    assert r["t_signal_ms"] is None and r["t_guard_ms"] is None and r["d_ready_signal"] is None and r["d_ready_guard"] is None  # the fallback has neither
    gap = [x for x in host.log.rows(lv.BOT_ID) if x["kind"] == "gap"]
    assert len(gap) == 1 and gap[0]["gap_kind"] == "incomplete_inputs" and gap[0]["data_loss"] is True and gap[0]["from_ms"] == bad * 1000
    assert gap[0]["ms"] == bad * 1000 + 20_000  # written when the mark is declared incomplete, not when the fallback decides
    rows = host.log.rows(lv.BOT_ID)
    assert max(i for i, x in enumerate(rows) if x["kind"] == "gap") < min(i for i, x in enumerate(rows) if x.get("t_mark") == bad and x["kind"] == "exit")
    evs = [e["event"] for e in events(host)]
    assert evs.count("incomplete_inputs") == 1 and evs.count("late_public") == 1 and "abandoned" not in evs and lat[MKS[4]]["status"] == "decided"
    assert not any(m in json.dumps(events(host)) for m in ("M00", "M01"))


@pytest.mark.parametrize("trigger", ["guard", "certified"])
def test_no_public_stream_abandons_the_mark_without_any_decision(tmp_path, trigger):
    """Ruling (b3): if the public stream never passes sd + 1 by the bound, nothing is decided at the mark: no exit row, the book is not stepped, and the next
    decided mark's own gap rule closes what was held (T1 = last mark + 1 h)."""
    bad = MKS[3]
    host, clock = make_host(tmp_path, MKS, trigger=trigger, never=[bad], public_lag=None)
    host.start(START_MS)
    host.run(max_marks=5)
    lat = {r["mark"]: r for r in latency(host)}
    r = lat[bad]
    assert r["status"] == "abandoned" and r["reason"] == "bound_incomplete" and r["abandon"] == "late_bound" and r["n_rows"] is None and r["n_entries"] == 0
    assert r["wall_decide_ms"] >= 900_000 and r["public_hw_slot"] is None and r["sd"] is not None  # the engine's clock was complete; the public stream was not
    rows = host.log.rows(lv.BOT_ID)
    assert not any(x["kind"] in ("entry", "exit") and x["t_mark"] == bad and x["reason"] != "gap" for x in rows if x["kind"] == "exit") and \
        not any(x["kind"] == "entry" and x["t_mark"] == bad for x in rows)  # no rank exit, no entry at the abandoned mark
    at_bad = [x for x in rows if x["kind"] == "exit" and x["t_mark"] == bad]  # the engine's own gap rule closes what was held, at the skipped mark
    assert at_bad and all(x["reason"] == "gap" for x in at_bad) and lat[MKS[4]]["status"] == "decided" and host.engine.book.last == MKS[4]
    assert min(x["seq"] for x in at_bad) > max(x["seq"] for x in rows if x["kind"] == "gap")  # written after the gap row, with the next mark's decision
    assert [e["event"] for e in events(host)].count("abandoned") == 1 and host.counts["marks_abandoned"] == 1


def test_a_source_that_says_failed_falls_back_to_the_public_stream_when_it_is_there(tmp_path):
    bad = MKS[2]
    host, clock = make_host(tmp_path, MKS[:3], fail={bad: ("window_connect", bad * 1000 + 500)})
    host.start(START_MS)
    host.run(max_marks=3)
    r = {x["mark"]: x for x in latency(host)}[bad]
    assert r["status"] == "late_public" and r["reason"] == "failed_window_connect" and 3000 <= r["wall_decide_ms"] < 8000 and r["n_entries"] == 0  # at once = as soon as the public stream (7 slots late) has passed sd + 1
    assert r["t_incomplete_ms"] == bad * 1000 + 500
    # an unsafe reason string never reaches a log
    host2, _ = make_host(tmp_path / "x", MKS[:3], fail={bad: ("https://x.invalid/?api-key=SECRET", bad * 1000 + 500)})
    host2.start(START_MS)
    host2.run(max_marks=3)
    assert "SECRET" not in host2.latency_path.read_text() + host2.events_path.read_text()
    assert {x["mark"]: x for x in latency(host2)}[bad]["reason"] == "failed_unspecified"
    # and with no public stream the failed mark is abandoned
    host3, _ = make_host(tmp_path / "y", MKS[:3], fail={bad: ("window_connect", bad * 1000 + 500)}, public_lag=None)
    host3.start(START_MS)
    host3.run(max_marks=3)
    assert {x["mark"]: x for x in latency(host3)}[bad]["status"] == "abandoned"


def test_a_clock_that_never_completes_decides_nothing_and_the_next_mark_closes_with_gap(tmp_path):
    bad = MKS[3]
    host, clock = make_host(tmp_path, MKS, drop_window=(bad - 5, bad + 25), public_lag=None)
    host.start(START_MS)
    host.run(max_marks=5)
    lat = {r["mark"]: r for r in latency(host)}
    assert lat[bad]["status"] == "abandoned" and lat[bad]["reason"] == "bound_clock" and lat[bad]["n_rows"] is None
    rows = host.log.rows(lv.BOT_ID)
    assert not any(r["kind"] == "entry" and r["t_mark"] == bad for r in rows)  # nothing was decided at the bad mark
    at_bad = [r for r in rows if r["kind"] == "exit" and r["t_mark"] == bad]  # the engine's own gap rule closes what was held, at the skipped mark
    assert at_bad and all(r["reason"] == "gap" for r in at_bad) and lat[MKS[4]]["status"] == "decided" and host.engine.book.last == MKS[4]
    assert min(r["seq"] for r in at_bad) > max(r["seq"] for r in rows if r["kind"] == "gap")  # written after the gap row, with the next mark's decision


def test_the_public_fallback_needs_the_engine_clock_guard_too(tmp_path):
    """The fallback is the engine's clock guard AND the public stream past sd + 1: a public high-water slot alone is not enough."""
    bad = MKS[3]
    host, clock = make_host(tmp_path, MKS, never=[bad], drop_window=(bad + 3, bad + 400))  # no chain second after the first one for a while
    host.start(START_MS)
    host.run(max_marks=5)
    r = {x["mark"]: x for x in latency(host)}[bad]
    assert r["status"] == "late_public" and r["wall_decide_ms"] >= 400_000  # decided only once a later chain second reached the engine (the rows resume at T + 400 s)


# ------------------------------------------------------------------------------------------------------------------------------- certified trigger (ruling a): P-a2 and P-a3
def test_certified_host_rows_md5_equals_the_guard_host_rows_md5_and_decides_earlier(tmp_path):
    """P-a2 (i) at the host: complete inputs in arrival order give byte-equal decision rows; the certified run decides before the guard run where the next chain
    second came late, and the late_inputs watch records the guard instant of the same mark, equal to the guard run's own ready instant."""
    import hashlib

    hg, _ = make_host(tmp_path / "g", MKS, trigger="guard")
    hg.start(START_MS)
    hg.run()  # to the end of the source (the rows run to 08:00, so the marks after MKS are decided on row time alone)
    hc, _ = make_host(tmp_path / "c", MKS, trigger="certified")
    hc.start(START_MS)
    hc.run()  # to the end of the source: the watches close
    md5 = lambda h: hashlib.md5(json.dumps(declog_by_mark(h), sort_keys=True).encode()).hexdigest()  # noqa: E731
    assert md5(hg) == md5(hc) and any(r["kind"] == "entry" for v in declog_by_mark(hc).values() for r in v)
    assert declog_by_mark(hg) == declog_by_mark(hc) and log_chain_ok(hc)
    lg, lc, late = latency(hg), latency(hc), late_recs(hc)
    assert [r["status"] for r in lc] == ["decided"] * len(lc) and len(lc) == len(lg) >= 5 and sorted(late) == [r["mark"] for r in lc]
    assert all(c["t_ready_ms"] <= g["t_ready_ms"] and c["d_ready"] <= g["d_ready"] for c, g in zip(lc, lg))
    assert any(c["t_ready_ms"] < g["t_ready_ms"] and c["d_ready"] < g["d_ready"] for c, g in zip(lc, lg))  # the guard floor is gone where the next second is late
    for c, g in zip(lc, lg):
        m = late[c["mark"]]
        assert c["trigger"] == "certified" and c["t_signal_ms"] == c["t_ready_ms"] and c["d_ready_signal"] == c["d_ready"] and m["d_ready_signal"] == c["d_ready"]
        assert m["t_guard_ms"] == g["t_ready_ms"] and m["d_ready_guard"] == g["d_ready"]  # the guard path would have decided at this instant, at this tip
        assert m["t_guard_ms"] >= m["t_signal_ms"] and m["d_ready_guard"] >= m["d_ready_signal"] and m["guard_after_signal_ms"] == m["t_guard_ms"] - m["t_signal_ms"]
        assert m["late_inputs"] == 0 and m["late"] is False and m["watch_complete"] is True and m["missing"] == [] and m["sd_changed"] is False
        assert m["sd_guard"] == m["sd_end"] == m["sd"] == c["sd"] == g["sd"] and m["fault_sd_changed_without_late"] is False
        assert (c["t_guard_ms"] is None) == (m["t_guard_ms"] > c["t_ready_ms"])  # known at the decision only if the guard already held then
    assert hc.counts["watches_closed"] == len(lc) and hc.counts.get("late_marks", 0) == 0 and hc.counts.get("watch_incomplete", 0) == 0
    pos = {(r["rec"], r["mark"]): i for i, r in enumerate(_lines(hc))}
    assert all(pos[("mark", c["mark"])] < pos[("mark_late", c["mark"])] for c in lc)  # the follow-up comes after its mark


def test_a_late_row_after_a_certified_decide_is_counted_and_the_mark_flagged(tmp_path):
    """P-a2 (ii): a row of slot < sd (or a lower slot with block_time >= T) ingested after the certified decide is counted in late_inputs and the mark flagged.
    Rows that decide(T) would not have read (slot >= sd, block_time >= T) are not counted."""
    host, clock = make_host(tmp_path, MKS, trigger="certified")
    host.start(START_MS)
    host.run(max_marks=3)
    T = MKS[2]
    s_star, sd = first_second_at_or_after(T)
    assert s_star == T + 2 and {r["mark"]: r for r in latency(host)}[T]["sd"] == sd and T in {w["T"] for w in host._watches}  # decided, watch still open
    now = clock.now
    host._ingest(late_trade(sd - 3, T - 1, "L1"), now)  # slot < sd (and block_time < T)
    host._ingest(late_trade(sd - 1, T + 1, "L2"), now)  # a lower slot with block_time >= T: it lowers sd(T) itself
    host._ingest(late_trade(sd + 40, T + 30, "C1"), now)  # decide(T) would not have read these two
    host._ingest(late_trade(sd + 2, T + 3, "C2"), now)
    host.run()
    m = late_recs(host)[T]
    assert m["late_inputs"] == 2 and m["late_by_kind"] == {"trade": 2} and m["late"] is True and m["watch_complete"] is True
    assert m["sd_changed"] is True and m["sd_end"] == sd - 1 and m["fault_sd_changed_without_late"] is False  # the counter explains the moved sd
    assert all(late_recs(host)[t]["late_inputs"] == 0 and late_recs(host)[t]["late"] is False for t in MKS if t != T)
    assert host.counts["late_marks"] == 1 and host.counts["late_inputs"] == 2
    ev = [e for e in events(host) if e["event"] == "late_inputs"]
    assert len(ev) == 1 and ev[0]["mark"] == T and ev[0]["n"] == 2
    assert [r for r in latency(host) if r["mark"] == T][0]["status"] == "decided"  # the mark itself was decided on the certified signal, as the ruling states
    s = hh.summarize(_lines(host), min_marks=1, require_host=None)
    assert s["late_inputs"]["n_late_marks"] == 1 and s["late_inputs"]["late_inputs_total"] == 2 and s["late_inputs"]["n_watched"] == len(latency(host))


def test_an_sd_that_moves_without_a_counted_late_row_is_flagged_as_a_code_fault(tmp_path):
    """P-a3: 'a differing result with late_inputs = 0 is a code fault, and it blocks'. A row of block_time T and a HIGHER slot than sd moves the first second
    at or after T without being a row decide(T) would have read; the watch notices the moved sd."""
    host, clock = make_host(tmp_path, MKS, trigger="certified")
    host.start(START_MS)
    host.run(max_marks=3)
    T = MKS[2]
    s_star, sd = first_second_at_or_after(T)
    host._ingest(late_trade(sd + 30, T, "F1"), clock.now)
    host.run()
    m = late_recs(host)[T]
    assert m["late_inputs"] == 0 and m["sd_changed"] is True and m["sd_end"] == sd + 30 and m["fault_sd_changed_without_late"] is True
    assert host.counts["fault_sd_changed_without_late"] == 1 and any(e["event"] == "fault_sd_changed_without_late" for e in events(host))
    s = hh.summarize(_lines(host), min_marks=1, require_host=None)
    assert s["late_inputs"]["n_fault_sd_changed_without_late"] == 1 and any("code fault" in n for n in s["notes"])


def test_the_watch_is_bounded_and_a_run_that_ends_closes_it_incomplete(tmp_path):
    host, clock = make_host(tmp_path, MKS, trigger="certified", window_closed_ms=10 ** 9)  # a window that never closes
    host.start(START_MS)
    host.run()
    m = late_recs(host)
    for T in MKS:
        x = m[T]
        assert x["watch_complete"] is False and x["missing"] == ["window"] and x["watch_end_ms"] == T * 1000 + 900_000  # the bound after the mark
        assert x["late_inputs"] == 0 and x["t_guard_ms"] is not None and x["t_public_passed_ms"] is not None
    host2, clock2 = make_host(tmp_path / "e", MKS[:2], trigger="certified", window_closed_ms=10 ** 9)
    host2.start(START_MS)
    host2.run(max_marks=1)
    assert len(host2._watches) == 1 and "mark_late" not in {r["rec"] for r in _lines(host2)}  # a stop at max_marks leaves the watch open
    host2.flush_watches()
    assert late_recs(host2)[MKS[0]]["watch_complete"] is False and host2.counts["watch_incomplete"] == 1 and not host2._watches


def test_a_refused_certified_decide_is_retried_then_falls_back_to_the_public_stream(tmp_path, monkeypatch):
    """P-a2 (iii) at the host: the engine refuses a certified sd that differs; nothing is decided; the host retries on the next step."""
    host, clock = make_host(tmp_path, MKS[:3], trigger="certified")
    real, calls = host.engine.decide, []

    def flaky(T, **kw):
        calls.append((T, kw.get("certified_sd")))
        if len(calls) == 1:
            raise lv.LiveRefused("certified sd moved")
        return real(T, **kw)

    monkeypatch.setattr(host.engine, "decide", flaky)
    host.start(START_MS)
    host.run(max_marks=3)
    assert host.counts["decide_refused"] == 1 and [e["event"] for e in events(host)].count("decide_refused") == 1
    assert calls[0][0] == MKS[0] and all(isinstance(c[1], int) for c in calls) and len(calls) == 4  # one refusal, three decisions, every call certified
    lat = latency(host)
    assert [r["status"] for r in lat] == ["decided"] * 3 and lat[0]["wall_ready_ms"] > 300
    # a certified call that is refused until the bound: bound_refused, then the guard path decides exits only through the public fallback
    host2, _ = make_host(tmp_path / "r", MKS[:3], trigger="certified")
    real2 = host2.engine.decide

    def stubborn(T, **kw):
        if kw.get("certified_sd") is not None and T == MKS[2]:
            raise lv.LiveRefused("certified sd moved")
        return real2(T, **kw)

    monkeypatch.setattr(host2.engine, "decide", stubborn)
    host2.start(START_MS)
    host2.run(max_marks=3)
    r = {x["mark"]: x for x in latency(host2)}[MKS[2]]
    assert r["status"] == "late_public" and r["reason"] == "bound_refused" and r["n_entries"] == 0 and 2 not in late_recs(host2)


class WindowSource(FakeSource):
    """mark_complete answers with the REAL tools.r4b_feed_merge.mark_complete over a WindowState built from the synthetic chain; kinds[T] makes mark T's window
    failed / unarmed / silent (a 4 s hole in the tip notices) / seam_open (the public stream has not reached the window's coverage)."""

    def __init__(self, clock, marks, kinds=None, **kw):
        super().__init__(clock, marks, **kw)
        self.kinds = dict(kinds or {})

    def state(self, T, kind, now):
        st = fm.WindowState(mark=T)
        st.connect_ms = (T - 30) * 1000
        st.connected_ms = st.connect_ms + 100
        if kind != "unarmed":
            st.armed_ms = st.connect_ms + 600
        for t in range(st.connect_ms + 700, now + 1, 400):
            if kind == "silent" and T * 1000 - 6000 <= t < T * 1000 - 2000:
                continue
            st.tips.append((t, tip_at(t)))
        if kind == "failed":
            st.fail("connect_failed", T * 1000 - 1000)
        return st

    def complete(self, T, sd, now):
        kind = self.kinds.get(T, "ok")
        st = self.state(T, kind, now)
        cov = fm.covered_from_slot(st)
        hw = (cov - 5 if cov is not None else 0) if kind == "seam_open" else tip_at(now) - 7
        return fm.mark_complete(T, sd, st, public_hw_slot=hw)

    def mark_complete(self, T, sd):
        return self.complete(T, sd, self.clock.now)


@pytest.mark.parametrize("kind,reason", [("failed", "window_failed"), ("unarmed", "not_armed"), ("silent", "silence"), ("seam_open", "seam_open")])
def test_no_certification_on_a_failed_unarmed_silent_or_seam_open_window(tmp_path, kind, reason):
    """P-a2 (iv): with the real mark_complete, a window that cannot certify never triggers the certified decide; the mark takes the late_public fallback."""
    bad = MKS[3]
    clock = Clock()
    src = WindowSource(clock, MKS, {bad: kind})
    host = hh.DryHost(host_engine(), DecisionLog(tmp_path / "declog"), src, out_dir=tmp_path, now_ms=clock, frozen_sha=SHA, shorter_warmup_proof="test-ref",
                      hostname="mal-fast-0", trigger="certified", change_id="t1")
    host.start(START_MS)
    host.run(max_marks=5)
    lat = {r["mark"]: r for r in latency(host)}
    r = lat[bad]
    assert r["status"] == "late_public" and r["reason"] == "bound_incomplete" and r["source_reason"] == reason and r["n_entries"] == 0 and r["t_signal_ms"] is None
    assert all(lat[t]["status"] == "decided" and lat[t]["t_signal_ms"] is not None for t in MKS if t != bad)  # the healthy windows do certify (the control)
    # and the pure function says the same at any instant of that mark
    s_star, sd = first_second_at_or_after(bad)
    for dt in (3000, 8000, 19_000):
        c = src.complete(bad, sd, bad * 1000 + dt)
        assert c.complete is False and c.reason.split(":")[0] == reason, (dt, c)
    assert src.complete(MKS[0], first_second_at_or_after(MKS[0])[1], MKS[0] * 1000 + 8000).complete is True


def test_a_buy_hold_suppresses_entries_keeps_exits_and_writes_no_gap_row(tmp_path):
    held = MKS[3]
    host, clock = make_host(tmp_path, MKS, hold_windows=[(held * 1000 - 1000, held * 1000 + 60_000)])
    host.start(START_MS)
    host.run(max_marks=5)
    got, want = declog_by_mark(host), reference(MKS, hold_marks=(held,))
    lat = {r["mark"]: r for r in latency(host)}
    assert lat[held]["status"] == "decided_held" and lat[held]["hold"] == "credit_cap" and lat[held]["n_entries"] == 0 and lat[held]["n_suppressed"] > 0
    assert got.get(held, []) == want[held] and not any(r["kind"] == "gap" for r in host.log.rows(lv.BOT_ID))
    assert got.get(MKS[4], []) == want[MKS[4]]  # the held mints are not in the book: the next mark enters them as the bare engine does
    assert any(e["event"] == "entries_held" and e["reason"] == "credit_cap" for e in events(host))


def test_a_precompute_failure_costs_speed_not_a_decision(tmp_path, monkeypatch):
    host, clock = make_host(tmp_path, MKS[:4])
    monkeypatch.setattr(host.engine, "precompute", lambda T: (_ for _ in ()).throw(RuntimeError("boom")))
    host.start(START_MS)
    host.run(max_marks=4)
    assert declog_by_mark(host) == {T: v for T, v in reference(MKS[:4]).items() if v}
    assert host.counts["precompute_error"] == 4 and all(r["precompute"]["ran"] is False for r in latency(host))
    assert [e["error"] for e in events(host) if e["event"] == "precompute_error"] == ["RuntimeError"] * 4
    sm = hh.summarize(latency(host), min_marks=1, require_host=None)
    assert sm["n_warmup"] == 2 and sm["n_counted"] + sm["n_provisional"] == 2  # warm-up marks left out first; the other two are never dropped for latency
    assert sm["n_counted_failed"] == sm["n_counted"] == sm["n_without_precompute"]  # when their date is final they count, as fails


# ------------------------------------------------------------------------------------------------------------------------------- slot time, k and the summary
def test_slot_s_and_k_follow_the_pinned_read_and_the_constants_match():
    from tools import cap_pick_score as cps
    from tools import r4b_read as rr

    assert (hh.HOUR_MIN_SPAN_S, hh.HOUR_SPH_RANGE) == (cps.HOUR_MIN_SPAN_S, cps.HOUR_SPH_RANGE)
    for pts in ((100, 0, 9100, 3600), (100, 0, 100, 3600), (100, 0, 9000, 1000), (0, 0, 50_000, 3600), (0, 0, 9000, 7200)):
        assert hh.sph_from_points(*pts) == cps.hour_sph_from_points(*pts)
    rnd = np.random.default_rng(3)
    base = 1_786_665_600
    points, sph = {}, {}
    sd = 1000
    for h in range(60):  # 2.5 days of hourly marks, slots per hour varying
        T = base + 3600 * h
        points[T] = (sd, T + int(rnd.integers(0, 2)))
        sd += int(rnd.integers(15000, 19000))
    tab = hh.slot_s_table(points)
    for T in sorted(points):
        if T + 3600 in points:
            sph[datetime_hour(T)] = (points[T + 3600][0] - points[T][0]) / (points[T + 3600][1] - points[T][1]) * 3600.0
    want = rr.slot_s_by_date(sph)
    assert {d: v["slot_s"] for d, v in tab.items()} == want and tab[hh.utc_date(base)]["final"] is True and tab[hh.utc_date(base + 2 * 86400)]["final"] is False
    for lat in (1.3, 1.9, 3.0):
        for s_s in (0.2, 0.2118, 0.4, 0.3999):
            assert hh.k_for(lat, s_s) == int(rr.k_for(lat, np.array([s_s]))[0])
    assert (hh.k_for(1.3, 0.2), hh.k_for(1.9, 0.2)) == (6, 9)  # EXP-027 section 3.3's disclosed tie behaviour at exactly 200 ms


def datetime_hour(T):
    from datetime import datetime, timezone

    return datetime.fromtimestamp(T, tz=timezone.utc).strftime("%Y-%m-%dT%H")


BASE = 1_786_665_600  # 2026-08-14T00Z


def synth_records(n_days=2, d_ws=5, sph=18000, host="mal-fast-0", pre=True, extra_marks=1, start_h=0, warm=0, change_id="c1", trigger="certified"):
    """n_days of hourly marks (+ extra_marks of the next day so the last day is final), 200 ms slots, every mark decided with d_would_send = d_ws. start_h: the host
    started that many hours into the first date; warm: the first `warm` marks (counting from the start) are warm-up marks."""
    recs, sd = [], 5000
    for h in range(24 * n_days + extra_marks):
        T = BASE + 3600 * h
        sd += sph if h else 0
        if h < start_h:
            continue
        recs.append({"rec": "mark", "mark": T, "date": hh.utc_date(T), "status": "decided", "sd": sd, "bt_star": T, "hostname": host, "warmup": (h - start_h) < warm,
                     "trigger": trigger, "change_id": change_id, "hold": None, "reason": None, "precompute": {"ran": pre, "rel_ms": -5000},
                     "processed": {"d_ready": 2, "d_decide": 4, "d_would_send": d_ws}, "confirmed": {"d_ready": 3, "d_decide": 5, "d_would_send": d_ws + 1}})
    return recs


def day(n):
    return hh.utc_date(BASE + 86400 * n)


def test_summary_go_no_go_over_at_least_48_marks():
    s = hh.summarize(synth_records(d_ws=5))
    assert (s["verdict"], s["n_counted"], s["n_provisional"], s["k_1_9_used"], s["go_qty"]) == ("GO", 48, 1, 9, 9.0)  # 5 + 4 <= round(1.9 / 0.2) = 9
    assert s["read_date"] == day(1) and s["read_boundary_utc"] == "2026-08-16T00:00:00Z" and s["monitor_only"] is False and s["change_id"] == "c1"
    assert s["processed"]["d_would_send"] == {"n": 48, "p50": 5.0, "p90": 5.0} and s["confirmed"]["d_would_send"]["p50"] == 6.0
    assert hh.summarize(synth_records(d_ws=6))["verdict"] == "NO_GO"  # 6 + 4 = 10 > 9
    assert hh.summarize(synth_records(d_ws=6), decide_commitment="confirmed")["verdict"] == "NO_GO" and hh.summarize(synth_records(d_ws=4), decide_commitment="confirmed")["verdict"] == "GO"
    s47 = hh.summarize(synth_records(n_days=2)[:-2])  # 47 marks: the last date is then not final, so none of its marks count
    assert s47["verdict"] == "INSUFFICIENT" and s47["n_counted"] == 24 and s47["monitor_only"] is True and s47["read_date"] is None
    assert hh.summarize(synth_records(host="mal-research-0"))["verdict"] == "INSUFFICIENT"
    assert hh.summarize(synth_records(host="mal-research-0"), require_host=None)["verdict"] == "GO"
    s = hh.summarize(synth_records(pre=False))
    assert s["verdict"] == "NO_GO" and s["n_counted"] == 48 and s["n_counted_failed"] == 48 and s["n_without_precompute"] == 48  # counted as fails, never dropped
    recs = synth_records(d_ws=5)
    for r in recs[10:14]:
        r["status"], r["reason"], r["processed"]["d_would_send"] = "abandoned", "bound_clock", None  # abandoned marks never count and never help
    s = hh.summarize(recs)
    assert s["n_incomplete"] == 4 and s["n_abandoned"] == 4 and s["n_counted"] == 44 and s["verdict"] == "INSUFFICIENT"


def test_summary_warmup_marks_never_count():
    """Ruling (b2.1): during the warm-up the member universe is partial; its marks are left out (and a record that does not say it is past the warm-up too)."""
    recs = synth_records(n_days=4, d_ws=6, warm=30)
    for r in recs[:30]:
        r["processed"]["d_would_send"] = 0  # a warm-up mark's latency looks better than it is
    s = hh.summarize(recs)
    assert s["n_warmup"] == 30 and s["n_counted"] == 66 and s["read_date"] == day(3)  # 18 (day 2) + 24 (day 3) = 42 < 48: the boundary after day 4
    assert s["processed"]["d_would_send"] == {"n": 66, "p50": 6.0, "p90": 6.0} and s["verdict"] == "NO_GO"
    s = hh.summarize(synth_records(n_days=3, d_ws=5, warm=30))
    assert s["n_counted"] == 42 and s["verdict"] == "INSUFFICIENT" and s["monitor_only"] is True  # 42 counted marks by the end of day 3
    unsure = synth_records(d_ws=5)
    for r in unsure:
        del r["warmup"]
    s = hh.summarize(unsure)
    assert s["n_warmup"] == 49 and s["n_counted"] == 0 and s["verdict"] == "INSUFFICIENT"  # no flag: not vouched for


def test_summary_is_read_once_at_the_first_date_boundary_with_48_counted_marks():
    """Ruling (b2.3): the verdict is taken over all counted marks up to the first boundary with >= 48; later marks never change it."""
    base = hh.summarize(synth_records(n_days=2, d_ws=5))
    assert base["verdict"] == "GO"
    later = synth_records(n_days=4, d_ws=5)
    for r in later[48:]:
        r["processed"]["d_would_send"] = 40  # days 3 and 4 would be a NO_GO if they counted
    s = hh.summarize(later)
    assert s["verdict"] == "GO" and s["read_date"] == day(1) and s["n_counted"] == 48 and s["n_after_read"] == 48
    assert {k: s[k] for k in ("verdict", "k_1_9_used", "go_qty", "processed", "per_date")} == {k: base[k] for k in ("verdict", "k_1_9_used", "go_qty", "processed", "per_date")}
    # the smallest k_1_9 of the counted dates: day 2 with 250 ms slots (k = round(7.6) = 8), day 1 with 200 ms (k = 9)
    sl = synth_records(n_days=3, d_ws=5)
    sd0 = sl[24]["sd"]
    for i, r in enumerate(sl[24:]):
        r["sd"] = sd0 + i * 14400
    s = hh.summarize(sl)
    assert s["per_date"][day(0)]["k_1_9"] == 9 and s["per_date"][day(1)]["k_1_9"] == 8 and s["k_1_9_used"] == 8 and s["verdict"] == "NO_GO"  # 5 + 4 = 9 > 8
    assert s["read_date"] == day(1) and day(2) not in s["per_date"]
    # a boundary reached only on day 3 (day 1 has 24, day 2 loses 6 marks to held ones): read there
    h = synth_records(n_days=3, d_ws=5)
    for r in h[24:30]:
        r["status"], r["hold"] = "decided_held", "credit_cap"
    s = hh.summarize(h)
    assert s["n_counted"] == 66 and s["read_date"] == day(2) and s["verdict"] == "GO"


def test_summary_leaves_a_date_out_only_for_a_non_final_slot_s_and_reports_every_date():
    """Ruling (b2.2): a date is left out because its slot_s is not final, never because of its marks' latency."""
    s = hh.summarize(synth_records(n_days=3, start_h=5))  # started 05:00 on date 1: 19 hours measured there
    d = s["dates"]
    assert sorted(d) == [day(0), day(1), day(2), day(3)]
    assert d[day(0)] == {"n_marks": 19, "n_counted_candidates": 0, "slot_s": d[day(0)]["slot_s"], "slot_s_hours": 19, "final": False, "left_out": "slot_s_not_final"}
    assert d[day(3)]["left_out"] == "slot_s_not_final" and d[day(1)]["left_out"] is None and d[day(2)]["final"] is True
    assert s["n_counted"] == 48 and s["read_date"] == day(2) and s["n_provisional"] == 20 and s["verdict"] == "GO"
    slow = synth_records(n_days=3, d_ws=5)
    for r in slow[24:48]:
        r["processed"]["d_would_send"] = 50  # day 2 is terrible: it is still counted (final), and it decides the verdict
    s = hh.summarize(slow)
    assert s["dates"][day(1)]["left_out"] is None and s["per_date"][day(1)]["n"] == 24 and s["verdict"] == "NO_GO" and s["processed"]["d_would_send"]["p50"] == 27.5


def test_summary_excludes_held_late_public_and_abandoned_marks_and_reports_them_by_reason():
    """Ruling (b2.4): held and abandoned marks leave the p50 (C0 sends nothing on them); late_public ones too (exits only)."""
    recs = synth_records(n_days=3, d_ws=5)
    for r in recs[24:27]:
        r.update(status="decided_held", hold="credit_cap")
        r["processed"]["d_would_send"] = 99
    for r in recs[27:29]:
        r.update(status="late_public", reason="bound_incomplete")
        r["processed"]["d_would_send"] = 99
    recs[29].update(status="abandoned", reason="failed_unspecified")
    recs[29]["processed"]["d_would_send"] = None
    s = hh.summarize(recs)
    assert s["excluded"] == {"abandoned:failed_unspecified": 1, "held:credit_cap": 3, "late_public:bound_incomplete": 2}
    assert (s["n_held"], s["n_late_public"], s["n_abandoned"], s["n_incomplete"], s["n_decided"]) == (3, 2, 1, 3, 70)
    assert s["n_counted"] == 66 and s["read_date"] == day(2) and s["verdict"] == "GO" and s["processed"]["d_would_send"]["p90"] == 5.0


def test_summary_reads_one_change_id_and_one_trigger():
    a, b = synth_records(change_id="c1"), synth_records(change_id="c2", d_ws=9)
    s = hh.summarize(a + b)
    assert s["verdict"] == "INSUFFICIENT" and any("several change_ids" in n for n in s["notes"]) and s["change_id"] is None
    assert hh.summarize(a + b, change_id="c1")["verdict"] == "GO" and hh.summarize(a + b, change_id="c2")["verdict"] == "NO_GO"  # 9 + 4 > 9
    assert hh.summarize(a + b, change_id="c1")["n_records_other_change_id"] == 49
    none = synth_records()
    for r in none:
        del r["change_id"]
    s = hh.summarize(none)
    assert s["verdict"] == "INSUFFICIENT" and any("no change_id" in n for n in s["notes"])
    mixed = synth_records()
    for r in mixed[:24]:
        r["trigger"] = "guard"
    s = hh.summarize(mixed)
    assert s["verdict"] == "INSUFFICIENT" and s["triggers"] == ["certified", "guard"] and any("one trigger per run" in n for n in s["notes"])


def test_summary_reports_the_late_inputs_of_the_watched_marks():
    recs = synth_records()
    late = [{"rec": "mark_late", "mark": r["mark"], "late_inputs": 0, "late": False, "watch_complete": True, "fault_sd_changed_without_late": False} for r in recs[:40]]
    late[3].update(late_inputs=3, late=True)
    late[7].update(late_inputs=1, late=True, watch_complete=False)
    s = hh.summarize(recs + late)
    assert s["late_inputs"] == {"n_watched": 40, "n_late_marks": 2, "late_inputs_total": 4, "n_watch_incomplete": 1, "n_unwatched_decided": 9, "n_fault_sd_changed_without_late": 0}
    assert s["verdict"] == "GO"  # late marks are decided marks: they count; Bar 1 attributes their differences
    late[9]["fault_sd_changed_without_late"] = True
    s = hh.summarize(recs + late)
    assert s["late_inputs"]["n_fault_sd_changed_without_late"] == 1 and any("code fault" in n for n in s["notes"])


def write_lat(path, recs):
    path.write_text("".join(json.dumps(r) + "\n" for r in recs))
    return path


def test_the_go_no_go_is_read_once_per_change_id(tmp_path):
    lat = write_lat(tmp_path / "latency.jsonl", synth_records(change_id="c1") + synth_records(change_id="c2", d_ws=9))
    ins = write_lat(tmp_path / "short.jsonl", synth_records(n_days=1, change_id="c1"))
    r = hh.read_go_no_go(ins, change_id="c1")
    assert r["verdict"] == "INSUFFICIENT" and not (tmp_path / "reads.jsonl").exists()  # not a read: nothing recorded, may be asked again
    assert hh.read_go_no_go(ins, change_id="c1")["verdict"] == "INSUFFICIENT"
    r = hh.read_go_no_go(lat, change_id="c1")
    assert r["verdict"] == "GO" and r["read_recorded"] is True
    reads = [json.loads(x) for x in (tmp_path / "reads.jsonl").read_text().splitlines()]
    assert len(reads) == 1 and reads[0]["change_id"] == "c1" and reads[0]["verdict"] == "GO" and reads[0]["n_counted"] == 48 and reads[0]["read_boundary_utc"] == "2026-08-16T00:00:00Z"
    with pytest.raises(lv.LiveRefused, match="already read"):
        hh.read_go_no_go(lat, change_id="c1")  # a second read with the same change_id
    assert hh.read_go_no_go(lat, change_id="c2")["verdict"] == "NO_GO"  # a declared change (another change_id on its own records) has its own read
    assert [json.loads(x)["change_id"] for x in (tmp_path / "reads.jsonl").read_text().splitlines()] == ["c1", "c2"]
    with pytest.raises(lv.LiveRefused, match="change_id must match"):
        hh.read_go_no_go(lat, change_id="a b")
    # a NO_GO is not re-read until a change is declared: the same change_id again is refused, and so is a growing file
    grown = write_lat(tmp_path / "grown.jsonl", synth_records(n_days=3, change_id="c2", d_ws=9))
    with pytest.raises(lv.LiveRefused, match="already read"):
        hh.read_go_no_go(grown, change_id="c2", reads_path=tmp_path / "reads.jsonl")


def test_summary_cli_reads_once_and_a_rehearsal_records_nothing(tmp_path, capsys):
    p = write_lat(tmp_path / "lat.jsonl", synth_records())
    assert hh.main(["summarize", "--latency", str(p), "--change-id", "c1"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["verdict"] == "GO" and out["schema"] == hh.SCHEMA and out["read_recorded"] is True
    assert hh.main(["summarize", "--latency", str(p), "--change-id", "c1"]) == 2  # the second read
    assert "already read" in json.loads(capsys.readouterr().out)["refused"]
    assert hh.main(["summarize", "--latency", str(p), "--change-id", "c1", "--min-marks", "5"]) == 2  # no lowered bar for a read
    assert "--min-marks is 48" in json.loads(capsys.readouterr().out)["refused"]
    (tmp_path / "rehearsal").mkdir()
    p2 = write_lat(tmp_path / "rehearsal" / "lat.jsonl", synth_records(host="mal-research-0"))
    assert hh.main(["summarize", "--latency", str(p2), "--change-id", "c1", "--any-host", "--min-marks", "5"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["rehearsal"] is True and out["verdict"] == "GO" and not (tmp_path / "rehearsal" / "reads.jsonl").exists()
    assert hh.main(["summarize", "--latency", str(p2), "--change-id", "c1", "--any-host"]) == 0  # and may be repeated


# ------------------------------------------------------------------------------------------------------------------------------- seals and no send path
def test_logs_hold_counts_only_and_the_module_has_no_send_path_or_key(tmp_path):
    host, clock = make_host(tmp_path, MKS)
    host.start(START_MS)
    host.run(max_marks=5)
    text = host.latency_path.read_text() + host.events_path.read_text()
    mints = {m for h in scenario().values() for r in h["migrations"] for m in [r["mint"]]}
    assert not any(m in text for m in mints) and '"score"' not in text and '"rank"' not in text and "order_id" not in text
    src = inspect.getsource(hh).split('"""', 2)[2]
    for word in ("probe_live", "LiveExecutor", "Keypair", "sendTransaction", "send_transaction", "private_key", "helius", "requests", "urllib", "websocket"):
        assert word not in src, word
    assert lv.LIVE_BUILT is False



def test_every_decided_mark_counts_slow_or_unmeasured_marks_are_fails_not_dropped():
    """Reviewer on #1143 at 9cdee9ca: dropping decided marks whose precompute ran after T (or that have no would-send slot) shrank the denominator and
    still returned GO. They count, as fails in the median (FAIL_SLOTS)."""
    base = hh.summarize(synth_records(d_ws=5))
    assert base["verdict"] == "GO" and base["n_counted"] == 48
    recs = synth_records(d_ws=5)
    counted = [r for r in recs if r.get("warmup") is False and r["status"] == "decided"][:48]
    for r in counted[:20]:
        r["precompute"] = {**(r.get("precompute") or {}), "ran": True, "rel_ms": 250}  # ran after the mark
    for r in counted[20:25]:
        r["processed"]["d_would_send"] = None  # no would-send slot
    s = hh.summarize(recs)
    assert s["n_counted"] == 48 and s["n_counted_failed"] == 25 and s["counted_failed_by_reason"] == {"no_d_would_send": 5, "no_precompute_ahead": 20}
    assert s["verdict"] == "NO_GO" and s["go_qty"] >= hh.FAIL_SLOTS  # 25 of 48 fail: the median is a fail
    for r in counted[:20]:
        r["precompute"]["rel_ms"] = -5000
    s = hh.summarize(recs)
    assert s["n_counted_failed"] == 5 and s["verdict"] == "GO"  # 5 fails of 48: the median is still 5 slots
