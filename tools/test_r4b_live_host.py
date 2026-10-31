"""Tests for tools/r4b_live_host.py. SYNTHETIC data and fake sources only: no tape, no network, no key, no sealed path.

  PYTHONPATH=/data/mal/audit-1008/venv/lib/python3.12/site-packages /data/mal/venv/bin/python -m pytest tools/test_r4b_live_host.py
Run from a CLEAN tree: DryHost.start() calls repo_head_sha(), which refuses modified tracked files (a test pins that refusal).
"""

from __future__ import annotations

import inspect
import json

import numpy as np
import pytest

pytest.importorskip("duckdb")

from tools import r4b_feed_adapter as fa  # noqa: E402
from tools import r4b_live as lv  # noqa: E402
from tools import r4b_live_host as hh  # noqa: E402
from tools.arena_declog import DecisionLog  # noqa: E402
from tools.test_r4b_feed_adapter import _to_tape  # noqa: E402
from tools.test_r4b_live import BT0, MARKS, S, _score_ret, event_stream, pda_of, scenario  # noqa: E402

SHA = "ab" * 20
T_FIRST = BT0 + 3600  # the first mark after a start at BT0 + 1 s
START_MS = BT0 * 1000 + 1000


def tip_at(ms: int) -> int:
    """The synthetic chain's slot at wall time ms (the scenario's 2.5 slots a second)."""
    return S + int((ms / 1000.0 - BT0) * 2.5)


class Clock:
    now = 0

    def __call__(self) -> int:
        return self.now


class FakeSource:
    """Tape rows at their receive time (event_ts + 7 ms), idle ticks every 250 ms from T - 6 s to T + 25 s of each mark. mark_complete is true
    complete_ms after the mark unless the mark is in `never`; failed / hold are scripted. tip_slot follows the wall clock."""

    def __init__(self, clock: Clock, marks, *, complete_ms: int = 300, never=(), fail=None, hold_windows=(), drop_window=None, tip=True):
        self.clock, self.complete_ms, self.never, self.fail, self.hold_windows, self.tip = clock, complete_ms, set(never), dict(fail or {}), list(hold_windows), tip
        items = []
        for t, o, n, kind, r in event_stream(scenario()):
            if drop_window and drop_window[0] <= r["block_time"] < drop_window[1]:
                continue
            items.append((_to_tape(kind, r)["t_recv_ms"], 0, _to_tape(kind, r)))
        for T in marks:
            items += [(T * 1000 + dt, 1, None) for dt in range(-6000, 25001, 250)]
        self.items = sorted(items, key=lambda x: (x[0], x[1]))

    def __iter__(self):
        for t, _, row in self.items:
            self.clock.now = t
            yield None if row is None else (row, t)

    def mark_complete(self, T, sd):
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


def host_engine(**kw):
    kw.setdefault("universe_min_mbt", BT0)
    return lv.new_host_engine(warmup_s=7200, shorter_warmup_proof="test-ref", pda_fn=pda_of, hash_fn=lv.DuckHash(), score_fn=_score_ret, **kw)


def make_host(tmp_path, marks, **src_kw):
    clock = Clock()
    host = hh.DryHost(host_engine(), DecisionLog(tmp_path / "declog"), FakeSource(clock, marks, **src_kw), out_dir=tmp_path, now_ms=clock, frozen_sha=SHA,
                      shorter_warmup_proof="test-ref", hostname="mal-fast-0", ntp_offset_ms=3.0)
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


def latency(host):
    return [json.loads(x) for x in host.latency_path.read_text().splitlines()]


def events(host):
    return [json.loads(x) for x in host.events_path.read_text().splitlines()]


RUN = MARKS[:1] + [T_FIRST + 3600 * i for i in range(5)]  # not used for ordering; see MKS
MKS = [T_FIRST + 3600 * i for i in range(5)]  # 01:00 .. 05:00 (the first two are inside the 7200 s warm-up)


# ------------------------------------------------------------------------------------------------------------------------------- decisions
def test_host_decisions_equal_a_bare_engine_and_the_precompute_is_used(tmp_path):
    host, clock = make_host(tmp_path, MKS)
    out = host.start(START_MS)
    assert out["exits"] == [] and out["start"]["frozen_sha"] == SHA and out["start"]["warmup_s"] == 7200 and host.next_mark == T_FIRST
    counts = host.run(max_marks=5)
    want = reference(MKS)
    got = declog_by_mark(host)
    assert counts["marks_decided"] == 5 and counts.get("marks_incomplete", 0) == 0
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
        assert r["status"] == "decided" and r["hostname"] == "mal-fast-0" and r["ntp_offset_ms"] == 3.0
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
                                                    shorter_warmup_proof="test-ref")
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


# ------------------------------------------------------------------------------------------------------------------------------- fail closed
def test_incomplete_inputs_skip_the_entries_write_the_exits_and_a_gap_row(tmp_path):
    bad = MKS[3]  # 04:00, after the warm-up with positions held
    host, clock = make_host(tmp_path, MKS, never=[bad])
    host.start(START_MS)
    host.run(max_marks=5)
    want = reference(MKS, hold_marks=())  # the unheld reference up to the bad mark (same state before it)
    got = declog_by_mark(host)
    held_ref = reference(MKS, hold_marks=(bad,))
    assert got.get(MKS[2]) == want[MKS[2]] and any(r["kind"] == "entry" for r in want[MKS[2]])
    assert not any(r["kind"] == "entry" for r in got.get(bad, [])) and any(r["kind"] == "entry" for r in want[bad])  # the entries were skipped
    assert got.get(bad, []) == held_ref[bad]  # the exits are the engine's rank exits of that mark, entries held
    lat = {r["mark"]: r for r in latency(host)}
    assert lat[bad]["status"] == "incomplete" and lat[bad]["reason"] == "bound_incomplete" and lat[bad]["n_entries"] == 0 and lat[bad]["t_ready_ms"] is None
    assert lat[bad]["wall_decide_ms"] >= 20_000 and lat[bad]["d_ready"] is None
    gap = [r for r in host.log.rows(lv.BOT_ID) if r["kind"] == "gap"]
    assert len(gap) == 1 and gap[0]["gap_kind"] == "incomplete_inputs" and gap[0]["data_loss"] is True and gap[0]["from_ms"] == bad * 1000
    rows = host.log.rows(lv.BOT_ID)
    assert max(i for i, r in enumerate(rows) if r["kind"] == "gap") < min(i for i, r in enumerate(rows) if r.get("t_mark") == bad and r["kind"] in ("entry", "exit")) \
        if any(r.get("t_mark") == bad for r in rows) else True  # the gap row precedes the mark's other rows
    assert [e["event"] for e in events(host)].count("incomplete_inputs") == 1 and lat[MKS[4]]["status"] == "decided"
    assert not any(m in json.dumps(events(host)) for m in ("M00", "M01"))


def test_a_source_that_says_failed_ends_the_wait_at_once(tmp_path):
    bad = MKS[2]
    host, clock = make_host(tmp_path, MKS[:3], fail={bad: ("window_connect", bad * 1000 + 500)})
    host.start(START_MS)
    host.run(max_marks=3)
    r = {x["mark"]: x for x in latency(host)}[bad]
    assert r["status"] == "incomplete" and r["reason"] == "failed_window_connect" and r["wall_decide_ms"] < 2000 and r["n_entries"] == 0
    # an unsafe reason string never reaches a log
    host2, _ = make_host(tmp_path / "x", MKS[:3], fail={bad: ("https://x.invalid/?api-key=SECRET", bad * 1000 + 500)})
    host2.start(START_MS)
    host2.run(max_marks=3)
    assert "SECRET" not in host2.latency_path.read_text() + host2.events_path.read_text()
    assert {x["mark"]: x for x in latency(host2)}[bad]["reason"] == "failed_unspecified"


def test_a_clock_that_never_completes_decides_nothing_and_the_next_mark_closes_with_gap(tmp_path):
    bad = MKS[3]
    host, clock = make_host(tmp_path, MKS, drop_window=(bad - 5, bad + 25))
    host.start(START_MS)
    host.run(max_marks=5)
    lat = {r["mark"]: r for r in latency(host)}
    assert lat[bad]["status"] == "incomplete" and lat[bad]["reason"] == "bound_clock" and lat[bad]["n_rows"] is None and lat[bad]["sd"] is None
    rows = host.log.rows(lv.BOT_ID)
    assert not any(r["kind"] == "entry" and r["t_mark"] == bad for r in rows)  # nothing was decided at the bad mark
    at_bad = [r for r in rows if r["kind"] == "exit" and r["t_mark"] == bad]  # the engine's own gap rule closes what was held, at the skipped mark
    assert at_bad and all(r["reason"] == "gap" for r in at_bad) and lat[MKS[4]]["status"] == "decided" and host.engine.book.last == MKS[4]
    assert min(r["seq"] for r in at_bad) > max(r["seq"] for r in rows if r["kind"] == "gap")  # written after the gap row, with the next mark's decision


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
    assert hh.summarize(latency(host), min_marks=1, require_host=None)["n_without_precompute"] == 4  # every decided mark counts as "no precompute", warm-up marks included


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


def synth_records(n_days=2, d_ws=5, sph=18000, host="mal-fast-0", pre=True, extra_marks=1):
    """n_days of hourly marks (+ extra_marks of the next day so the last day is final), 200 ms slots, every mark decided with d_would_send = d_ws."""
    recs, sd, base = [], 5000, 1_786_665_600  # 2026-08-14T00Z
    for h in range(24 * n_days + extra_marks):
        T = base + 3600 * h
        recs.append({"rec": "mark", "mark": T, "date": hh.utc_date(T), "status": "decided", "sd": sd, "bt_star": T, "hostname": host,
                     "precompute": {"ran": pre, "rel_ms": -5000}, "processed": {"d_ready": 2, "d_decide": 4, "d_would_send": d_ws},
                     "confirmed": {"d_ready": 3, "d_decide": 5, "d_would_send": d_ws + 1}})
        sd += sph
    return recs


def test_summary_go_no_go_over_at_least_48_marks():
    s = hh.summarize(synth_records(d_ws=5))
    assert (s["verdict"], s["n_counted"], s["n_provisional"], s["k_1_9_used"], s["go_qty"]) == ("GO", 48, 1, 9, 9.0)  # 5 + 4 <= round(1.9 / 0.2) = 9
    assert s["processed"]["d_would_send"] == {"n": 48, "p50": 5.0, "p90": 5.0} and s["confirmed"]["d_would_send"]["p50"] == 6.0
    assert hh.summarize(synth_records(d_ws=6))["verdict"] == "NO_GO"  # 6 + 4 = 10 > 9
    assert hh.summarize(synth_records(d_ws=6), decide_commitment="confirmed")["verdict"] == "NO_GO" and hh.summarize(synth_records(d_ws=4), decide_commitment="confirmed")["verdict"] == "GO"
    assert hh.summarize(synth_records(n_days=2)[:-2])["verdict"] == "INSUFFICIENT"  # 47 marks counted: the last date is then not final, so none of its marks count
    assert hh.summarize(synth_records(host="mal-research-0"))["verdict"] == "INSUFFICIENT"
    assert hh.summarize(synth_records(host="mal-research-0"), require_host=None)["verdict"] == "GO"
    s = hh.summarize(synth_records(pre=False))
    assert s["verdict"] == "INSUFFICIENT" and s["n_counted"] == 0 and s["n_without_precompute"] == 49  # all 49 synthetic marks
    recs = synth_records(d_ws=5)
    for r in recs[10:14]:
        r["status"], r["processed"]["d_would_send"] = "incomplete", None  # incomplete marks never count and never help
    s = hh.summarize(recs)
    assert s["n_incomplete"] == 4 and s["n_counted"] == 44 and s["verdict"] == "INSUFFICIENT"
    sl = synth_records(n_days=3, d_ws=5)  # a third day with slower slots: its k is smaller, and the verdict uses the smallest k of the counted dates
    sd = sl[48]["sd"]
    for i, r in enumerate(sl[48:]):
        r["sd"] = sd + i * 15000  # 240 ms slots on day 3: k_1_9 = round(7.9) = 8
    s = hh.summarize(sl)
    assert s["per_date"][hh.utc_date(sl[60]["mark"])]["k_1_9"] == 8 and s["k_1_9_used"] == 8 and s["verdict"] == "NO_GO"  # 5 + 4 = 9 > 8


def test_summary_cli_prints_counts_only(tmp_path, capsys):
    p = tmp_path / "lat.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in synth_records()))
    assert hh.main(["summarize", "--latency", str(p)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["verdict"] == "GO" and out["schema"] == hh.SCHEMA


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
