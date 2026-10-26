"""Tests for tools/dbc_p18_run.py (P18 B1): start guard, pruning equivalence (synthetic tape, aggressive trimming), runner, restart, counters. No network, no key, no real tape hour."""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from tools import dbc_fwd as F
from tools import dbc_fwd_read as R
from tools import dbc_p18_equiv as EQ
from tools import dbc_p18_run as P
from tools import dbc_price as L
from tools import dbc_screen as S
from tools.arena_declog import DecisionLog
from tools.test_dbc_fwd import END, START, T0, _raises, tape_root

D = lambda *a: datetime(*a, tzinfo=timezone.utc)  # noqa: E731
FIX = Path(__file__).parent / "fixtures" / "dbc_txs_2026-10-14T12.json.gz"


# ------------------------------------------------------------------------------------------------------------------------------ guard
def test_marker_constant_equals_the_reads_marker():
    assert P.MARKER == R.MARKER


def test_live_refused_before_the_gate_time_and_without_the_marker():
    for now in (D(2026, 10, 26, 12), D(2026, 11, 1, 0, 9, 59)):
        with _raises(P.StartRefused):
            P.start_guard(live=True, test=False, now=now, marker_exists=lambda p: True)
    with _raises(P.StartRefused):  # after the time, but the read has not consumed its look
        P.start_guard(live=True, test=False, now=D(2026, 11, 1, 0, 10), marker_exists=lambda p: False)
    seen = []
    P.start_guard(live=True, test=False, now=D(2026, 11, 1, 0, 10), marker_exists=lambda p: seen.append(p) or True)
    assert seen == [P.MARKER]


def test_guard_never_opens_the_marker(monkeypatch):
    import builtins
    opened = []
    real = builtins.open
    monkeypatch.setattr(builtins, "open", lambda f, *a, **k: opened.append(str(f)) or real(f, *a, **k))
    try:
        P.start_guard(live=True, test=False, now=D(2026, 11, 2))
    except P.StartRefused:
        pass
    assert P.MARKER not in opened


def test_live_and_test_are_exclusive_and_test_needs_synthetic_or_exploration():
    for kw in (dict(live=True, test=True), dict(live=False, test=False), dict(live=False, test=True)):
        with _raises(P.StartRefused):
            P.start_guard(now=D(2026, 11, 2), **kw)
    P.start_guard(live=False, test=True, now=D(2026, 10, 26), fixture=True)
    P.start_guard(live=False, test=True, now=D(2026, 10, 26), hours=(D(2026, 10, 14, 0), D(2026, 10, 17, 0)))
    for h in ((D(2026, 10, 16, 23), D(2026, 10, 17, 1)), (D(2026, 10, 17, 0), D(2026, 10, 18, 0)), (D(2026, 10, 25, 0), D(2026, 10, 25, 3)), (D(2026, 10, 13, 23), D(2026, 10, 14, 2))):
        with _raises(P.StartRefused):
            P.start_guard(live=False, test=True, now=D(2026, 10, 26), hours=h)
    for bad in ("/data2/mal/venue-tape-backfill/fwdv1/meteora_dbc", "/x/meteora_dbc_holdout", "/data/mal/meta-capture/x"):
        with _raises(P.StartRefused):
            P.start_guard(live=False, test=True, now=D(2026, 10, 26), fixture=True, paths=(bad,))


def test_cli_refuses_and_writes_nothing():
    with tempfile.TemporaryDirectory() as td:
        out = str(Path(td) / "o")
        assert P.main(["--live", "--env-file", "/nonexistent", "--out", out]) == 2  # today is before the gate
        assert P.main(["--test", "--hours", "2026-10-17T00", "2026-10-17T03", "--out", out]) == 2
        assert P.main(["--test", "--out", out]) == 2
        assert not Path(out).exists()


# ------------------------------------------------------------------------------------------------------------------------------ pruning
class Tiny(P.PrunedEngine):
    TRIM_MIN_ROWS = 6  # trim on almost every close so pending positions, anchors and prefix bases are exercised
    TRIM_MARGIN_S = 0.0
    SWEEP_SLOTS = 1


def _run(cls, signals=0, **kw):
    L.enable_swap3_pricing()
    with tempfile.TemporaryDirectory() as td:
        log = DecisionLog(td)
        live_ss: dict = {}
        eng = cls(T0 + 3600.0, ss_live=lambda hk, m=live_ss: S.slot_s_for(m, hk), log=log, meta={"bt_src": "tape_block_time"}, **kw)
        log.append(F.BOOK, T0 * 1000, {"type": "start", "book": F.BOOK})
        closer = P.SlotCloser(eng, log) if signals else None
        feed = EQ.SignalFeed(closer, signals) if closer else eng
        F.stream_hours(feed, START, END, None, live_ss, log)
        eng.closer_stats = closer.stats if closer else None
        return eng, log.md5(), sorted(eng.trig_keys), log.rows(F.BOOK)


def test_pruned_engine_log_is_byte_identical_to_the_pinned_engine():
    with tape_root():
        e0, m0, k0, r0 = _run(F.Engine)
        e1, m1, k1, r1 = _run(Tiny)
        e2, m2, k2, r2 = _run(P.PrunedEngine)
    assert len([r for r in r0 if r["type"] == "decision"]) >= 3
    assert m0 == m1 == m2 and k0 == k1 == k2 and r0 == r1
    assert e1.stats["trim_calls"] > 0 and e1.stats["trim_rows"] > 0, e1.stats
    assert e1.stats["pools_dropped"] >= 1 and e1.stats["rows_after_drop"] == 0 and e1.stats["trim_window_clamped"] == 0, e1.stats
    assert sum(p.n() for p in e1.pools.values()) < sum(p.n() for p in e0.pools.values())
    for k in ("rows", "sol_rows", "trigger_prints", "late_rows", "live_exits"):
        assert e0.stats[k] == e1.stats[k]


def test_open_position_pool_is_never_dropped_or_trimmed_past_its_window():
    with tape_root():
        L.enable_swap3_pricing()
        live_ss: dict = {}
        eng = Tiny(T0 + 3600.0, ss_live=lambda hk, m=live_ss: S.slot_s_for(m, hk), meta={"bt_src": "tape_block_time"})
        bad = []
        orig = eng._trim

        def chk(p):
            orig(p)
            for t in (p.pend_live, p.pend_screen):
                if t is not None:
                    assert 0 <= t["row"] < p.n() and p.sig[t["row"]] == t["signature"], "pending trigger row no longer points at its print"
                    bad.append(1)
        eng._trim = chk
        F.stream_hours(eng, START, END, None, live_ss)
    assert bad, "no trim ran while a position was open"


def test_oracle_variants_are_refused():
    L.enable_swap3_pricing()
    with _raises(ValueError):
        P.PrunedEngine(0.0, oracle_mode={})


def test_dropped_pool_rows_are_counted_not_revived():
    L.enable_swap3_pricing()
    e = Tiny(0.0)
    base = {"v": 1, "venue": "meteora_dbc", "type": "curve_complete", "slot": 10, "t_recv_ms": 1000, "pool": "X", "config": "c"}
    e.on_row(base)
    e.advance(10 + e.DROP_MARGIN_SLOTS)
    assert "X" in e.pools and "X" not in e.dead, "dropped inside the margin"
    e.advance(10 + e.DROP_MARGIN_SLOTS + 1)
    assert "X" in e.dead and "X" not in e.pools
    e.on_row({**base, "slot": 11})
    assert e.stats["rows_after_drop"] == 1 and "X" not in e.pools


def _late_tape():
    """Synthetic tape where pool E's trigger print (the last print before its completion) arrives LATE: after the completion row and after a row of a slot three later (so a sweep has run)."""
    from tools.test_dbc_fwd import synth_rows
    rows = synth_rows()
    ci = next(i for i, r in enumerate(rows) if r["type"] == "curve_complete")
    comp = rows[ci]["slot"]
    ti = max(i for i, r in enumerate(rows[:ci]) if r["pool"] == "PoolEeee" and r["type"] == "trade")
    late = rows.pop(ti)
    ci -= 1
    j = next(i for i in range(ci, len(rows)) if rows[i]["slot"] >= comp + 3)
    rows.insert(j + 1, late)
    return rows, late


class NoMargin(Tiny):
    DROP_MARGIN_SLOTS = 0


def test_late_row_after_a_sweep_still_gives_the_pinned_engines_decision():
    rows, late = _late_tape()
    with tape_root(rows):
        e0, m0, k0, r0 = _run(F.Engine)
        e1, m1, k1, r1 = _run(Tiny)  # sweep every slot, default 600-slot margin
        e2, m2, k2, r2 = _run(NoMargin)  # control: no margin loses the late row
    assert e0.stats["late_rows"] >= 1
    d0 = [r["key"] for r in r0 if r["type"] == "decision" and r["pool"] == "PoolEeee"]
    assert d0, "the pinned engine must decide pool E from the late print"
    assert m0 == m1 and r0 == r1 and e1.stats["rows_after_drop"] == 0
    assert e2.stats["rows_after_drop"] >= 1 and [r["key"] for r in r2 if r["type"] == "decision" and r["pool"] == "PoolEeee"] != d0, "control does not bite"


def test_fixture_must_live_under_tools_fixtures():
    P.start_guard(live=False, test=True, now=D(2026, 10, 26), fixture=str(FIX))
    with _raises(P.StartRefused):
        P.start_guard(live=False, test=True, now=D(2026, 10, 26), fixture="/tmp/x.json.gz")


# ------------------------------------------------------------------------------------------------------------------------------ runner
def test_fixture_run_chain_start_row_and_restart():
    with tempfile.TemporaryDirectory() as td:
        res = P.run_fixture(str(FIX), td)
        log = DecisionLog(td + "/declog")
        head, seq = log.verify(F.BOOK)
        rows = log.rows(F.BOOK)
        assert rows[0]["type"] == "start" and rows[0]["mode"] == "test" and "restart" not in rows[0] and sum(r["type"] == "start" for r in rows) == 1
        assert rows[0]["code_sha256"]["dbc_p18_run.py"] and res["stats"]["sol_rows"] > 0 and res["rss_mib"] > 0
        # restart: a second runner on the same out dir continues the chain with a start row that quotes the head
        run2 = P.Runner(td, live=False, start_ts=T0 + 5.0)
        r2 = DecisionLog(td + "/declog").rows(F.BOOK)[-1]
        assert r2["type"] == "start" and r2["restart"] is True and r2["prev_head"] == head and r2["prev_seq"] == seq and r2["seq"] == seq + 1
        assert run2.eng is not None
        DecisionLog(td + "/declog").verify(F.BOOK)


def test_restart_lists_decisions_without_exit_rows():
    with tempfile.TemporaryDirectory() as td:
        log = DecisionLog(td + "/declog")
        log.append(F.BOOK, T0 * 1000, {"type": "start", "book": F.BOOK})
        d = log.append(F.BOOK, T0 * 1000 + 1, {"type": "decision", "book": F.BOOK, "key": "k1"})
        d2 = log.append(F.BOOK, T0 * 1000 + 2, {"type": "decision", "book": F.BOOK, "key": "k2"})
        log.append(F.BOOK, T0 * 1000 + 3, {"type": "exit", "book": F.BOOK, "decision_seq": d["seq"]})
        P.Runner(td, start_ts=T0 + 9.0)
        last = DecisionLog(td + "/declog").rows(F.BOOK)[-1]
        assert last["orphan_decision_seqs"] == [d2["seq"]]


def test_memory_bar_flags_and_status_prints_credits():
    with tempfile.TemporaryDirectory() as td:
        from tools import dbc_fwd_feed as FF
        m = FF.Meter()
        m.add(250_000)
        run = P.Runner(td, start_ts=T0, rss=lambda: 1600.0, meter=m)
        st = run.tick()
        assert run.mem_exceeded and st["mem_exceeded"] and st["feed"]["credits_est"] == 5.0 and "estimate" in st["feed"]["credits_basis"] and "live_exits" not in st["stats"] and st["peak_rss_mib"] > 0 and P.MEM_BAR_MIB == 1536.0
        assert json.loads((Path(td) / "status.json").read_text())["feed"]["bytes"] == 250_000
        ok = P.Runner(td + "/b", start_ts=T0, rss=lambda: 900.0)
        ok.tick()
        assert not ok.mem_exceeded


def test_gap_and_stop_rows_are_logged_and_chained():
    with tempfile.TemporaryDirectory() as td:
        run = P.Runner(td, start_ts=T0)
        run.on_row({"type": "gap", "reason": "silence", "start_ms": T0 * 1000, "t_ms": T0 * 1000 + 100})
        run.on_row({"type": "gap_end", "reason": "silence", "start_ms": T0 * 1000, "end_ms": T0 * 1000 + 200})
        kinds = [r["type"] for r in DecisionLog(td + "/declog").rows(F.BOOK)]
        assert kinds == ["start", "gap", "gap_end"]
        DecisionLog(td + "/declog").verify(F.BOOK)


def test_live_decision_rows_carry_latency_stamps():
    with tape_root():
        with tempfile.TemporaryDirectory() as td:
            clock = [0]
            eng_kw = dict(live=True, bt_src="tape_block_time", start_ts=float(T0), clock_ms=lambda: clock.__setitem__(0, clock[0] + 7) or clock[0] + 1_700_000_000_000)
            run = P.Runner(td, **eng_kw)
            for h in range(3):
                for row in F.read_hour_ordered(str(Path(L.BACKFILL_ROOT) / f"tape-2026-10-14T{h:02d}.jsonl.zst"))["rows"]:
                    run.on_row(row)
            run.finish()
            dec = run.decision_rows()
    assert dec and all("t_decide_ms" in r and "max_slot_seen" in r and r["max_slot_seen"] >= r["slot"] for r in dec)


# ------------------------------------------------------------------------------------------------------------------------------ counters
def test_l90_reads_decision_rows_only_and_applies_the_formula():
    dec = [{"type": "decision", "t_recv_ms": 1000, "t_decide_ms": 1000 + 10 * i, "slot_s": 0.2166, "slot": 5, "max_slot_seen": 5 + (i % 4)} for i in range(1, 11)]
    exits = [{"type": "exit", "t_recv_ms": 0, "t_decide_ms": 999999, "slot_s": 9.0, "slot": 1, "max_slot_seen": 99}] * 5
    a, b = P.l90_report(dec, 0.5), P.l90_report(dec + exits, 0.5)
    assert a == b and a["n_decisions"] == 10
    p90 = a["decide_minus_recv_s"]["p90"]
    assert abs(a["L90_s"] - (p90 + 0.76 + 0.453 + 3 * 0.2166)) < 1e-9 and a["bar_met"] is True
    assert P.l90_report(dec, 2.0)["bar_met"] is False
    assert P.l90_report(dec)["L90_s"] is None and P.l90_report([])["bar_met"] is None


def test_late_rows_and_xcheck_bars():
    assert P.late_rows_ok({"late_rows": 1, "sol_rows": 100})["ok"] is True
    assert P.late_rows_ok({"late_rows": 2, "sol_rows": 100})["ok"] is False
    live, back = {f"k{i}" for i in range(98)} | {"a"}, {f"k{i}" for i in range(98)} | {"b"}
    x = P.xcheck(live, back)
    assert x["union"] == 100 and x["unexplained"] == 2 and x["ok"] is True
    assert P.xcheck(live, back | {"c"})["ok"] is False
    assert P.xcheck(live, back | {"c"}, explained={"c"})["ok"] is True


def test_slot_signal_hook_closes_a_slot_and_counts_later_rows_as_late():
    with tape_root():
        with tempfile.TemporaryDirectory() as td:
            run = P.Runner(td, live=False, bt_src="tape_block_time", start_ts=float(T0))
            rows = F.read_hour_ordered(str(Path(L.BACKFILL_ROOT) / "tape-2026-10-14T00.jsonl.zst"))["rows"]
            tr = [r for r in rows if r["type"] == "trade"]
            run.on_row(tr[0])
            run.on_slot_signal(tr[0]["slot"])
            late = {**tr[0], "signature": "sig-late"}
            run.on_row(late)
            assert run.eng.stats["late_rows"] == 1


# ------------------------------------------------------------------------------------------------------------------------------ slot close (decision neutrality, late rows)
def test_slot_signals_synthesised_from_the_tape_leave_the_decision_log_unchanged():
    with tape_root():
        e0, m0, k0, r0 = _run(F.Engine)
        e1, m1, k1, r1 = _run(Tiny, signals=1)
        e2, m2, k2, r2 = _run(Tiny, signals=2)
    assert len([r for r in r0 if r["type"] == "decision"]) >= 3
    assert m0 == m1 == m2 and r0 == r1 == r2 and k0 == k1 == k2
    for e in (e1, e2):
        assert e.closer_stats["late_after_close"] == 0 and e.closer_stats["signals_advanced"] > 0 and e.stats["late_rows"] == 0
    assert e2.closer_stats["signals_clamped"] > 0  # empty-slot signals are clamped, not forwarded as a jump


def test_signal_decides_a_slot_without_a_later_row():
    with tape_root():
        eng = P.PrunedEngine(0.0)
        eng.decide = True
        rows = [r for r in F.read_hour_ordered(str(Path(L.BACKFILL_ROOT) / "tape-2026-10-14T00.jsonl.zst"))["rows"] if r["type"] == "trade"]
        c = P.SlotCloser(eng)
        c.on_row(rows[0])
        s0 = rows[0]["slot"]
        assert eng.open and eng.stats["groups_closed"] == 0  # the pinned engine waits for a later row
        assert c.signal(s0) is True
        assert not eng.open and eng.stats["groups_closed"] == 1 and eng.cur_slot == s0 + 1


def test_signal_guards_stale_clamped_and_before_any_row():
    L.enable_swap3_pricing()
    eng = P.PrunedEngine(0.0)
    c = P.SlotCloser(eng)
    assert c.signal(50) is False and c.stats["signals_ignored"] == 1 and eng.cur_slot == -1  # nothing seen yet: nothing to close, engine untouched
    base = {"v": 1, "venue": "meteora_dbc", "type": "curve_complete", "slot": 100, "t_recv_ms": 1000, "pool": "X", "config": "c"}
    c.on_row(base)
    assert c.signal(10_000) is True and c.stats["signals_clamped"] == 1 and eng.cur_slot == 101  # a skewed far-future signal never moves the engine past its data
    assert c.signal(100) is False and c.signal(10_000) is False and c.stats["signals_ignored"] == 3
    assert eng.cur_slot == 101


def test_late_row_after_an_early_close_is_counted_logged_and_forwarded():
    with tape_root():
        with tempfile.TemporaryDirectory() as td:
            run = P.Runner(td, live=False, bt_src="tape_block_time", start_ts=float(T0))
            rows = F.read_hour_ordered(str(Path(L.BACKFILL_ROOT) / "tape-2026-10-14T00.jsonl.zst"))["rows"]
            tr = [r for r in rows if r["type"] == "trade"]
            run.on_row(tr[0])
            n0 = run.eng.stats["sol_rows"]
            run.on_slot_signal(tr[0]["slot"])
            late = {**tr[0], "signature": "sig-late"}
            run.on_row(late)
            assert run.closer.stats["late_after_close"] == 1 and run.eng.stats["late_rows"] == 1
            assert run.eng.stats["sol_rows"] == n0 + 1  # forwarded: the pinned engine's late-row behaviour, not a silent drop
            lr = [r for r in DecisionLog(td + "/declog").rows(F.BOOK) if r["type"] == "late_after_close"]
            assert len(lr) == 1 and lr[0]["signature"] == "sig-late" and lr[0]["slot"] == tr[0]["slot"] and lr[0]["closed_through"] == tr[0]["slot"]
            DecisionLog(td + "/declog").verify(F.BOOK)
            assert run.tick()["slot_close"]["late_after_close"] == 1


def test_late_rows_after_early_close_never_change_a_decision_silently():
    rows, late = _late_tape()
    with tape_root(rows):
        e0, m0, k0, r0 = _run(F.Engine)
        e1, m1, k1, r1 = _run(Tiny, signals=1)
    assert e1.closer_stats["late_after_close"] >= 1 and e1.stats["late_rows"] >= 1
    marks = [r for r in r1 if r["type"] == "late_after_close"]
    assert marks and any(r["signature"] == late["signature"] for r in marks)
    dec = lambda rs: [(r["type"], r.get("key")) for r in rs if r["type"] in ("decision", "exit")]  # noqa: E731
    if dec(r0) != dec(r1) or m0 != m1:
        assert marks  # any divergence from the pinned engine is accompanied by a logged late_after_close row
    assert m0 != m1, "a late row after an early close must leave a trace in the chained log"
