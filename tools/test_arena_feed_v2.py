"""tools/arena_feed_v2.py: standby, takeover, dedup, continuity, fail-closed, counter carry, readers. Synthetic notices only; no network. P35 PR 2.

A-T1 (fake sources: notices are handed to handle_notice directly), A-T2 (tape equivalence against one continuous v1 writer, checked with
tools/feed_handover_proof.py parity) and A-T3 (seam proof) of docs/research/p35-longbook-handoff/DESIGN.md section 2.3.
"""
from __future__ import annotations

import asyncio
import collections
import hashlib
import json
import os
from pathlib import Path

import pytest

from observe.fake_ws import fixture_line
from observe.trade_source import RawNotice
from tools import arena_feed as af
from tools import arena_feed_v2 as v2
from tools import feed_handover_proof as P

DU = collections.namedtuple("DU", "total used free")
T0 = 1_794_441_600_000  # 2026-11-12T00:00:00Z
SEC = 1000
STOP = T0 + 900 * SEC  # the old writer's last notice is before this
LOCK = STOP + 300
END = T0 + 1500 * SEC
SB_START = T0 + 600 * SEC
SHIFT = 3  # the standby receives every notice 3 ms after the old writer
LINES = {"pumpswap": fixture_line("pumpswap_buy_event.b64"), "pumpfun": fixture_line("pump_trade_event.b64")}
MOR = {"pumpswap": 200, "pumpfun": 200}
MDR = {"pumpswap": 0.01, "pumpfun": 0.01}


def roomy(_p) -> DU:
    return DU(10**12, 5 * 10**11, 5 * 10**11)


class Clock:
    def __init__(self, t: int) -> None:
        self.t = t

    def __call__(self) -> int:
        return self.t


def notices(t_from: int, t_to: int, shift: int = 0, skip=lambda prog, s: False) -> list[tuple[str, RawNotice]]:
    """One decodable notice per program per second in [t_from, t_to): pumpswap at :000, pumpfun at :500; one slot per 400 ms."""
    out = []
    for s in range((t_from - T0) // SEC, (t_to - T0) // SEC + 1):
        for prog, off in (("pumpswap", 0), ("pumpfun", 500)):
            t = T0 + s * SEC + off
            if t_from <= t < t_to and not skip(prog, s):
                out.append((prog, RawNotice(slot=300_000_000 + (t - T0) // 400, signature=f"sig-{prog}-{s}", failed=False, logs=(LINES[prog],),
                                            t_recv_ms=t + shift, commitment="confirmed", feed="public_rpc_logs")))
    return out


def v1_writer(d: Path, clock: Clock) -> tuple[af.Feed, int]:
    f = af.Feed(d, ["wss://a.test"], {"pumpswap": 2, "pumpfun": 2}, wall=clock, rpc_url=None, compress=False, disk_usage=roomy)
    fd = af.acquire_lock(d)
    f.emit_safe(f.meta_row("start", schema=af.SCHEMA, pid=os.getpid()))
    return f, fd


def feed_live(f: af.Feed, clock: Clock, notes, hb_from: int) -> int:
    """Hand notices to a live writer in time order, with an hb pair every 60 s of sim time (as the 60 s housekeeping would). Returns the next hb time."""
    nxt = hb_from
    for prog, n in notes:
        while nxt <= n.t_recv_ms:
            clock.t = nxt
            f.heartbeat(nxt)
            nxt += 60 * SEC
        clock.t = n.t_recv_ms
        f.handle_notice(prog, n)
    return nxt


def stop_writer(f: af.Feed, fd: int, clock: Clock, at: int, *, clean: bool = True) -> None:
    clock.t = at
    if clean:
        f.flush_pending(at)
        f.emit_safe(f.meta_row("stop", **{k: v for k, v in f.status(at).items() if k in ("gaps", "lines_written")}))
        af.write_status(f.out_dir / af.STATUS_NAME, f.status(at))
    f.writer.close()
    os.close(fd)


def standby(d: Path, clock: Clock, **kw) -> v2.FeedV2:
    kw.setdefault("min_overlap_rows", dict(MOR))
    kw.setdefault("max_disagree_rate", dict(MDR))
    return v2.FeedV2(d, ["wss://a.test"], {"pumpswap": 2, "pumpfun": 2}, standby=True, wall=clock, rpc_url=None, compress=False, disk_usage=roomy, **kw)


def run_handover(d: Path, *, sb_start=SB_START, sb_skip=lambda prog, s: False, clean=True, before_lock=None, **sb_kw) -> tuple[v2.FeedV2, dict]:
    """Old v1 writer over [T0, STOP); the standby receives [sb_start, END) 3 ms later; the old writer stops at STOP + 100; the standby takes the
    lock at LOCK and runs live to END. Returns (new feed, takeover info)."""
    clock = Clock(T0)
    old, ofd = v1_writer(d, clock)
    sb = standby(d, clock, **sb_kw)
    events = sorted([(n.t_recv_ms, 0, p, n) for p, n in notices(T0, STOP)] + [(n.t_recv_ms, 1, p, n) for p, n in notices(sb_start, LOCK - SHIFT, SHIFT, sb_skip)],
                    key=lambda e: (e[0], e[1]))
    nxt = T0
    for t, who, prog, n in events:
        if who == 0:
            nxt = feed_live(old, clock, [(prog, n)], nxt)
        else:
            clock.t = t
            sb.handle_notice(prog, n)
    stop_writer(old, ofd, clock, STOP + 100, clean=clean)
    if before_lock is not None:
        before_lock(sb)
    clock.t = LOCK
    fd = af.acquire_lock(d)
    info = sb.complete_takeover(fd, LOCK, standby_started_ms=sb_start, prev_pid=111)
    feed_live(sb, clock, notices(LOCK - SHIFT, END, SHIFT), LOCK + 60 * SEC)
    stop_writer(sb, fd, clock, END + 100)
    return sb, info


def reference(d: Path) -> None:
    clock = Clock(T0)
    f, fd = v1_writer(d, clock)
    feed_live(f, clock, notices(T0, END), T0)
    stop_writer(f, fd, clock, END + 100)


def rows_of(d: Path) -> list[dict]:
    return [json.loads(ln) for p in sorted(d.glob("tape-*.jsonl")) for ln in p.read_text().splitlines()]


W0, W1 = T0 + 120 * SEC, END - 60 * SEC


# ---- A-T2 / A-T3 -------------------------------------------------------------------------------------------------
def test_takeover_is_gapless_and_equals_one_continuous_writer(tmp_path):
    sb, info = run_handover(tmp_path / "ho")
    assert info["proven"] and info["failed"] == []
    assert info["continuity"] == {"pumpswap": "proven", "pumpfun": "proven"}
    rows = rows_of(tmp_path / "ho")
    kinds = collections.Counter(r["type"] for r in rows)
    assert kinds["takeover"] == 1 and kinds["start"] == 1 and kinds["stop"] == 2  # the old writer's start at T0; no start at the takeover
    assert not [r for r in rows if r["type"] == "gap"]
    reference(tmp_path / "ref")
    par = P.parity_report(tmp_path / "ho", tmp_path / "ref", W0, W1, seam_ms=LOCK)
    assert par["verdict"] == "PARITY_EXACT", par
    seam = P.seam_report(tmp_path / "ho", W0, W1, expect_takeover=True, max_disagree_rate=MDR, min_overlap_rows=MOR)
    assert seam["verdict"] == "PASS", (seam["failed"], seam["checks"]["takeover"])
    tk = info["dedup"]
    assert tk["buffered"] == tk["dropped_dup"] + tk["written_missed"] and tk["written_missed"] > 0 and tk["buffer_overflow"] is False
    assert info["agreement"]["pumpswap"]["only_old"] == 0 and info["agreement"]["pumpswap"]["n_old"] >= MOR["pumpswap"]


def test_standby_writes_nothing_before_takeover(tmp_path):
    d = tmp_path / "t"
    clock = Clock(T0)
    old, ofd = v1_writer(d, clock)
    feed_live(old, clock, notices(T0, SB_START), T0)
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in d.iterdir() if p.is_file()}
    sb = standby(d, clock)
    for p, n in notices(SB_START, STOP, SHIFT):
        clock.t = n.t_recv_ms
        sb.handle_notice(p, n)
        sb.tick(n.t_recv_ms)
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in d.iterdir() if p.is_file() and p.name in before}
    assert after == before and sb.buffered_rows > 0 and sb.mode == "standby"
    assert not any(p.name.startswith("tape-") and p.name not in before for p in d.iterdir())
    stop_writer(old, ofd, clock, STOP)


# ---- A-T1: fail closed --------------------------------------------------------------------------------------------
def assert_unproven(d: Path, info: dict, reason: str) -> None:
    assert not info["proven"] and any(reason in f for f in info["failed"]), info["failed"]
    rows = rows_of(d)
    tk = [r for r in rows if r["type"] == "takeover"]
    gaps = [r for r in rows if r["type"] == "gap" and r["kind"] == "process_restart"]
    starts = [r for r in rows if r["type"] == "start"]
    assert not tk and len(gaps) == 1 and gaps[0]["data_loss"] is True and gaps[0]["reason"] == "handover_unproven" and len(starts) == 2
    seam = P.seam_report(d, W0, W1, expect_takeover=True, max_disagree_rate=MDR, min_overlap_rows=MOR)
    assert {"hard_gaps", "start_rows", "takeover"} <= set(seam["failed"])
    keys = collections.Counter((r["signature"], r["type"], r["event_index"]) for r in rows if r["type"] == "trade")
    assert max(keys.values()) == 1  # still no duplicate row


def test_standby_reconnect_in_overlap_is_unproven(tmp_path):
    _, info = run_handover(tmp_path / "t", before_lock=lambda sb: sb.note_reconnect("pumpfun", STOP - 60 * SEC))
    assert_unproven(tmp_path / "t", info, "pumpfun:standby_reconnect")


def test_reconnect_before_overlap_moves_nothing_but_is_not_counted(tmp_path):
    _, info = run_handover(tmp_path / "t", before_lock=lambda sb: sb.note_reconnect("pumpfun", SB_START - 10 * SEC))
    assert info["proven"]


def test_buffer_overflow_is_unproven(tmp_path):
    _, info = run_handover(tmp_path / "t", buffer_max=100)
    assert_unproven(tmp_path / "t", info, "buffer_overflow")


def test_killed_old_writer_is_unproven(tmp_path):
    _, info = run_handover(tmp_path / "t", clean=False)
    assert_unproven(tmp_path / "t", info, "no_clean_stop")


def test_disagreement_above_rate_is_unproven(tmp_path):
    # the standby misses every 20th pumpswap notice in the overlap: 5% > 1%
    _, info = run_handover(tmp_path / "t", sb_skip=lambda prog, s: prog == "pumpswap" and s % 20 == 0 and s < 899)
    assert_unproven(tmp_path / "t", info, "pumpswap:disagree")
    assert info["agreement"]["pumpswap"]["only_old"] > 0


def test_unpinned_thresholds_are_unproven(tmp_path):
    _, info = run_handover(tmp_path / "t", min_overlap_rows=None, max_disagree_rate=None)
    assert_unproven(tmp_path / "t", info, "thresholds_unpinned")


def test_too_few_overlap_rows_is_unproven(tmp_path):
    _, info = run_handover(tmp_path / "t", min_overlap_rows={"pumpswap": 1000, "pumpfun": 1000})
    assert_unproven(tmp_path / "t", info, "n_old_short")


def test_short_overlap_is_unproven_and_not_ready(tmp_path):
    seen = {}

    def peek(sb):
        seen["ready"] = sb.ready(STOP)

    _, info = run_handover(tmp_path / "t", sb_start=STOP - 60 * SEC, before_lock=peek)
    assert seen["ready"][0] is False and any(w.startswith("overlap_short") for w in seen["ready"][1])
    assert_unproven(tmp_path / "t", info, ":overlap")


def test_standby_data_loss_gap_moves_cover_from(tmp_path):
    # a slot jump on the standby's own stream near the end of the overlap: coverage restarts after it, overlap too short
    def jump(sb):
        sb._standby_gap({"type": "gap", "prog": "pumpswap", "kind": "slot_jump", "data_loss": True, "t_recv_ms": STOP - 30 * SEC,
                         "from_ms": STOP - 40 * SEC, "to_ms": STOP - 30 * SEC})

    _, info = run_handover(tmp_path / "t", before_lock=jump)
    assert info["cover_from_ms"]["pumpswap"] == STOP - 30 * SEC
    assert_unproven(tmp_path / "t", info, "pumpswap:overlap")


def test_overlap_floor_enforced():
    with pytest.raises(ValueError):
        standby(Path("/nonexistent-p35"), Clock(T0), overlap_min_s=60)


# ---- async takeover: deadline, lock race ------------------------------------------------------------------------------
def prepared(d: Path) -> tuple[v2.FeedV2, af.Feed, int, Clock]:
    clock = Clock(T0)
    old, ofd = v1_writer(d, clock)
    sb = standby(d, clock)
    events = sorted([(n.t_recv_ms, 0, p, n) for p, n in notices(T0, STOP)] + [(n.t_recv_ms, 1, p, n) for p, n in notices(SB_START, STOP, SHIFT)],
                    key=lambda e: (e[0], e[1]))
    nxt = T0
    for t, who, prog, n in events:
        if who == 0:
            nxt = feed_live(old, clock, [(prog, n)], nxt)
        else:
            clock.t = t
            sb.handle_notice(prog, n)
    clock.t = STOP  # the takeover instant: takeover() reads the (fake) wall clock
    return sb, old, ofd, clock


def test_async_takeover_completes(tmp_path):
    d = tmp_path / "t"
    sb, old, ofd, clock = prepared(d)

    def stop_old():
        stop_writer(old, ofd, clock, STOP + 100)
        clock.t = LOCK
        return {"pid": 111}

    fd, info = asyncio.run(sb.takeover(STOP, STOP + 60 * SEC, stop_old, standby_started_ms=SB_START))
    assert info["proven"] and sb.mode == "live"
    os.close(fd)


def test_async_takeover_not_ready_by_deadline_writes_nothing(tmp_path):
    d = tmp_path / "t"
    sb, old, ofd, clock = prepared(d)
    sb.overflow = True
    called = []
    with pytest.raises(v2.TakeoverAbort) as ei:
        asyncio.run(sb.takeover(STOP, STOP, lambda: called.append(1), standby_started_ms=SB_START))
    assert ei.value.code == v2.TAKEOVER_EXIT and not called and sb.mode == "standby"
    stop_writer(old, ofd, clock, STOP + 100)


def test_async_takeover_lock_race_writes_nothing(tmp_path):
    d = tmp_path / "t"
    sb, old, ofd, clock = prepared(d)
    third: list[int] = []
    before: list = []

    def stop_old():
        stop_writer(old, ofd, clock, STOP + 100)
        before.extend(sorted((p.name, p.stat().st_size) for p in d.glob("tape-*.jsonl")))  # after the old writer's stop row
        fd3 = af.acquire_lock(d)  # a resumed old job gets the lock first
        os.ftruncate(fd3, 0)
        os.pwrite(fd3, b"424242\n", 0)
        third.append(fd3)
        return {"pid": 111}

    with pytest.raises(v2.TakeoverAbort) as ei:
        asyncio.run(sb.takeover(STOP, STOP + 60 * SEC, stop_old, standby_started_ms=SB_START))
    assert before and ei.value.code == af.LOCK_HELD_EXIT and sb.mode == "standby"
    assert sorted((p.name, p.stat().st_size) for p in d.glob("tape-*.jsonl")) == before
    os.close(third[0])


# ---- status counters, readers, guards ---------------------------------------------------------------------------------
def test_status_counters_carry_over(tmp_path):
    d = tmp_path / "t"

    def bump(sb):
        st = json.loads((d / af.STATUS_NAME).read_text())
        st["disk_reserve"]["pauses"], st["disk_reserve"]["rows_dropped_total"] = 2, 5
        (d / af.STATUS_NAME).write_text(json.dumps(st))

    sb, info = run_handover(d, before_lock=bump)
    st = json.loads((d / af.STATUS_NAME).read_text())
    assert info["counter_base"]["pauses"] == 2 and info["counter_base"]["rows_dropped_total"] == 5 and info["counter_base"]["lines_written"] > 0
    assert st["disk_reserve"]["pauses"] >= 2 and st["disk_reserve"]["rows_dropped_total"] >= 5
    assert st["lines_written"] > info["counter_base"]["lines_written"] and st["handover_from_pid"] == 111


def test_hb_cadence_across_the_seam(tmp_path):
    run_handover(tmp_path / "t")
    hb = sorted(r["t_recv_ms"] for r in rows_of(tmp_path / "t") if r["type"] == "hb" and r["prog"] == "pumpswap")
    assert max(b - a for a, b in zip(hb, hb[1:])) <= 61 * SEC


def test_readers_ignore_the_takeover_row(tmp_path):
    from tools.arena_paper import ArenaEngine, StackConfig
    from tools.arena_state import ArenaState
    from tools import arena_hour_health as hh

    row = {"v": 1, "type": "takeover", "prog": "all", "slot": 0, "t_recv_ms": LOCK, "signature": None, "event_index": None,
           "continuity": {"pumpswap": "proven", "pumpfun": "proven"}}
    st = ArenaState(tmp_path / "tape", start_ms=T0)
    st.apply(dict(row))
    assert st.counters.get("apply_errors", 0) == 0 and not st.gap_spans()
    eng = ArenaEngine(ledger_root=tmp_path / "ledger", registry_path=tmp_path / "bots.json", cfg=StackConfig(feed_delay_slots=7))
    eng.on_row(dict(row))
    assert eng.gaps == [] and eng.hw_ms is None
    assert b"takeover" not in hh.DATA_TYPES


def test_production_tape_refused_before_hv6_end_and_unpinned():
    with pytest.raises(ValueError, match="HV6"):
        v2.check_production(Path(v2.PRODUCTION_TAPE), v2.EARLIEST_PRODUCTION_MS - 1, True)
    with pytest.raises(ValueError, match="pinned"):
        v2.check_production(Path(v2.PRODUCTION_TAPE), v2.EARLIEST_PRODUCTION_MS + 1, False)
    v2.check_production(Path(v2.PRODUCTION_TAPE), v2.EARLIEST_PRODUCTION_MS + 1, True)
    v2.check_production(Path("/data2/mal/p35/rehearsal/x"), 0, False)  # any other dir is not guarded


def test_lock_holder_pids_reads_proc_locks(tmp_path):
    lock = tmp_path / af.LOCK_NAME
    lock.write_text("1\n")
    ino = lock.stat().st_ino
    locks = tmp_path / "locks"
    locks.write_text(f"1: FLOCK  ADVISORY  WRITE 4242 08:01:{ino} 0 EOF\n2: POSIX  ADVISORY  WRITE 99 08:01:{ino + 1} 0 EOF\n")
    assert v2.lock_holder_pids(lock, str(locks)) == [4242]


def test_standby_lock_refuses_a_second_standby(tmp_path):
    fd = v2.acquire_standby_lock(tmp_path)
    with pytest.raises(af.LockHeld):
        v2.acquire_standby_lock(tmp_path)
    os.close(fd)


def test_cli_standby_needs_instants():
    assert v2.main(["--standby", "--out-dir", "/tmp/p35-never-created"]) == 2


def test_parse_per_prog():
    assert v2.parse_per_prog("0.02", float) == {"pumpswap": 0.02, "pumpfun": 0.02}
    assert v2.parse_per_prog("pumpswap=10,pumpfun=20", int) == {"pumpswap": 10, "pumpfun": 20}
    with pytest.raises(ValueError):
        v2.parse_per_prog("bogus=1", int)


def test_arena_feed_v1_untouched():
    p = Path(__file__).resolve().parent / "arena_feed.py"
    assert hashlib.sha256(p.read_bytes()).hexdigest().startswith("7a9d424b")
