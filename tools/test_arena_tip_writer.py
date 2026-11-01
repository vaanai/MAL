"""Tests for tools/arena_tip_writer.py (HV7 tip plan step 5, QP-RULING-1101-hv4-reads 4.1 (B)): hour close only after the checkpoint has passed the hour plus the grace, byte identity with an
offline adaptation of the same rows (tied to the #2023 harness: scripts/research/arena_tip_equiv.py), fail-closed paths, resume (kill after every poll), the heartbeat and the consumer check.
SYNTHETIC rows in tmp dirs only: no real tip file, arena tape, mirror, ledger or network is touched.

  /data/mal/venv/bin/python -m pytest tools/test_arena_tip_writer.py -q
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

os.environ.setdefault("MAL_ARENA_TEST", "1")

from tools import arena_tip_adapter as ta  # noqa: E402
from tools import arena_tip_reader as tr  # noqa: E402
from tools import arena_tip_writer as aw  # noqa: E402
from tools import test_arena_tip_adapter as TA  # noqa: E402
from tools import test_arena_tip_equiv as TE  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("zstd") is None, reason="the zstd CLI is required")
HM = TA.B * 1000  # 2026-11-01T06:00:00Z in ms
STEP_MS = 12_000
GRACE = 60_000


class Clock:
    def __init__(self, ms: int) -> None:
        self.ms = ms

    def __call__(self) -> int:
        return self.ms


class TipSim:
    """A tip dir that grows like the follower's: rows first, then the checkpoint (the reader's protocol). Slot s is at t = T_BASE + s * 12 s, i.e. the run crosses the 06:00 and 07:00 marks."""

    T_BASE = HM - 1_800_000

    def __init__(self, root: Path) -> None:
        self.tip, self.state = root / "tip", root / "state"
        self.tip.mkdir(parents=True, exist_ok=True)
        self.state.mkdir(parents=True, exist_ok=True)
        self.gaps = self.skips = 0
        self.slot_done = 0
        self.t_done = 0

    @classmethod
    def t(cls, slot: int) -> int:
        return cls.T_BASE + slot * STEP_MS

    def rows(self, slot: int) -> dict[str, list[dict]]:
        t = self.t(slot)
        out: dict[str, list[dict]] = {"trades": [], "creates": [], "migrations": []}
        out["trades"].append(TA.wt(slot, 0, venue="pumpswap", t=t, tx=3))
        out["trades"].append(TA.wt(slot, 1, venue="pump_bonding", t=t, tx=7))
        if slot % 5 == 0:
            out["trades"].append(TA.wt(slot, 2, venue="pumpswap", t=t, tx=None, pool=f"PX{slot % 3}"))  # a print that came back through resolve_unresolved (its own pool: no mixed group)
            del out["trades"][-1]["tx_index"]
        if slot % 17 == 0:
            out["creates"].append({"type": "create", "mint": f"C{slot}", "creator": "c", "slot": slot, "signature": f"cr{slot}", "event_index": 0, "tx_index": 1, "block_time": t // 1000,
                                   "t_recv_ms": t, "is_mayhem_mode": False, "name": "n", "symbol": "s"})
        if slot % 31 == 0:
            out["migrations"].append({"type": "migration", "mint": f"M{slot % 3}", "pool": f"P{slot % 3}", "quote_mint": TA.WSOL, "slot": slot, "signature": f"mg{slot}", "event_index": 0,
                                      "tx_index": 0, "block_time": t // 1000, "t_recv_ms": t})
        return out

    def add(self, slot: int, rows: dict[str, list[dict]] | None = None, checkpoint: bool = True) -> None:
        t = self.t(slot)
        for kind, rs in (rows if rows is not None else self.rows(slot)).items():
            if rs:
                TA.put(self.tip, f"{kind}-{tr.hour_key(t // 1000)}.jsonl", rs, "a")
        self.slot_done, self.t_done = slot, t
        if checkpoint:
            self.checkpoint()

    def skip(self, slot: int) -> None:
        t = self.t(slot)
        TA.put(self.tip, f"skipped-slots-{tr.hour_key(t // 1000)}.jsonl", [{"slot": slot, "kind": "skipped", "t_recv_ms": t}], "a")
        self.skips += 1
        self.slot_done, self.t_done = slot, t

    def gap(self, lo: int, hi: int, t: int) -> None:
        TA.put(self.tip, "gaps.jsonl", [{"kind": "backlog_jump", "from_slot": lo, "to_slot": hi, "slots": hi - lo + 1, "t_recv_ms": t}], "a")
        self.gaps += 1

    def checkpoint(self) -> None:
        TA.write_state(self.state, last_done=self.slot_done, last_t_recv_ms=self.t_done, gaps=self.gaps, skipped=self.skips)

    def healthy(self, now: int) -> None:
        TA.healthy(self.state, now)


def make_source(sim: TipSim, clock: Clock, *, io_ok: bool = True) -> ta.ArenaTipSource:
    reader = tr.TipReader(sim.tip, sim.state, start_ms=TipSim.T_BASE, catch_up_hours=0, min_hour="2026-10-01T00", lookback_h=None, fadvise=False)
    return ta.ArenaTipSource(reader, sim.state, now_ms=clock, io_hook=lambda: io_ok)


def make_writer(sim: TipSim, out: Path, clock: Clock, **kw) -> aw.Writer:
    kw.setdefault("compress", False)
    kw.setdefault("snapshot_s", 0.0)
    kw.setdefault("status_s", 0.0)
    return aw.Writer(make_source(sim, clock, io_ok=kw.pop("io_ok", True)), out, start_ms=TipSim.T_BASE, catch_up_hours=0, grace_ms=kw.pop("grace_ms", GRACE), clock=clock,
                     out_roots=[str(out.parent)], **kw)


def scenario(sim: TipSim, clock: Clock, w_factory, n_slots: int = 600, every: int = 20, kill_after: int | None = None, w=None, start_slot: int = 1) -> aw.Writer | None:
    """Slots start_slot..n_slots in batches of `every`; one writer step per batch. Slot 211..219 is a backlog jump (the follower writes the gap, then goes on at slot 220); 300 is a skipped slot."""
    steps = 0
    if w is None:
        w = w_factory()
        w.start()
    for s in range(start_slot, n_slots + 1):
        if 211 <= s <= 219:
            if s == 211:
                sim.gap(211, 219, sim.t(211) - 500)
            continue
        if s == 300:
            sim.skip(300)
            continue
        sim.add(s, checkpoint=False)
        if s % every == 0 or s == n_slots:
            sim.checkpoint()
            clock.ms = sim.t_done + 1500
            sim.healthy(clock.ms)
            w.step()
            steps += 1
            if kill_after is not None and steps >= kill_after:
                w.close()
                return None
    return w


def hours_bytes(out: Path) -> dict[str, bytes]:
    """hour -> the tape bytes, whether the hour is open (.jsonl) or compacted (.jsonl.zst)."""
    res: dict[str, bytes] = {}
    for p in sorted(out.iterdir()):
        if p.name.startswith("tape-") and p.name.endswith(".jsonl"):
            res[p.name[5:18]] = p.read_bytes()
        elif p.name.startswith("tape-") and p.name.endswith(".jsonl.zst"):
            res[p.name[5:18]] = subprocess.run(["zstd", "-dc", "-q", str(p)], check=True, capture_output=True).stdout
    return res


def offline_bytes(sim: TipSim, clock_ms: int) -> dict[str, bytes]:
    """What adapting the same final tip dir in one pass gives, bucketed by the hour of t_recv_ms (no writer involved)."""
    reader = tr.TipReader(sim.tip, sim.state, start_ms=TipSim.T_BASE, catch_up_hours=0, min_hour="2026-10-01T00", lookback_h=None, fadvise=False)
    src = ta.ArenaTipSource(reader, sim.state, now_ms=lambda: clock_ms, io_hook=lambda: True)
    src.start()
    res: dict[str, list[bytes]] = {}
    while True:
        rows = src.poll(50_000)
        for r in rows:
            res.setdefault(aw.hour_key(r["t_recv_ms"]), []).append(aw.row_bytes(r))
        if not rows and src.caught_up:
            break
    return {h: b"".join(v) for h, v in res.items()}


def fresh(tmp_path: Path, name: str = "w") -> tuple[TipSim, Clock, Path]:
    root = tmp_path / name
    root.mkdir()
    sim = TipSim(root)
    (sim.tip / f"trades-{tr.hour_key(TipSim.T_BASE // 1000)}.jsonl").touch()  # the first history hour exists (ArenaTipSource.start checks it)
    clock = Clock(TipSim.T_BASE)
    sim.healthy(clock.ms)
    return sim, clock, root / "out"


# ------------------------------------------------------------------------------------------------------------------------------------ hour close
def test_an_hour_closes_only_after_the_checkpoint_passed_its_end_plus_the_grace(tmp_path):
    sim, clock, out = fresh(tmp_path)
    w = make_writer(sim, out, clock)
    w.start()
    end = HM  # hour 05 ends at 06:00:00
    for s in range(1, 400):
        if sim.t(s) > end + GRACE - STEP_MS:
            break
        sim.add(s, checkpoint=False)
        last = s
    sim.checkpoint()
    clock.ms = sim.t_done + 100
    sim.healthy(clock.ms)
    w.step()
    assert sim.t_done < end + GRACE and w.closed == {} and "2026-11-01T05" in w.size  # the checkpoint is in hour 06 but not a grace past the end of 05
    nxt = last + 1
    assert sim.t(nxt) >= end + GRACE - STEP_MS
    # one slot whose t is exactly end + GRACE - 1 ms: still open; then exactly end + GRACE: closed
    sim.add(nxt, {"trades": [TA.wt(nxt, 0, venue="pump_bonding", t=end + GRACE - 1, tx=1)], "creates": [], "migrations": []})
    sim.t_done = end + GRACE - 1
    sim.checkpoint()
    sim.healthy(end + GRACE)
    clock.ms = end + GRACE
    w.step()
    assert w.closed == {}
    sim.add(nxt + 1, {"trades": [TA.wt(nxt + 1, 0, venue="pump_bonding", t=end + GRACE, tx=1)], "creates": [], "migrations": []})
    sim.t_done = end + GRACE
    sim.checkpoint()
    w.step()
    assert list(w.closed) == ["2026-11-01T05"] and "2026-11-01T05" not in w.size
    row = json.loads((out / aw.META_DIR / "counts.jsonl").read_text().splitlines()[0])
    assert row["hour"] == "2026-11-01T05" and row["bytes"] == (out / "tape-2026-11-01T05.jsonl").stat().st_size and row["lines"] > 0
    assert row["sha256"] == hashlib.sha256((out / "tape-2026-11-01T05.jsonl").read_bytes()).hexdigest()
    w.close()


def test_an_hour_the_reader_has_not_finished_is_not_closed_even_when_the_checkpoint_is_far_past(tmp_path):
    sim, clock, out = fresh(tmp_path)
    for s in range(1, 600):
        sim.add(s, checkpoint=False)
    sim.checkpoint()
    clock.ms = sim.t_done + 1500
    sim.healthy(clock.ms)
    w = make_writer(sim, out, clock, poll_limit=40)
    w.start()
    w.step()
    assert not w.src.caught_up and w.closed == {}  # the checkpoint is two hours past hour 05, but the reader has returned only 40 items
    for _ in range(500):
        w.step()
        sim.healthy(clock.ms)
        if w.src.caught_up:
            break
    assert w.src.caught_up and set(w.closed) == {"2026-11-01T05", "2026-11-01T06"}
    w.close()


def test_a_closed_hour_is_compacted_and_the_zst_decompresses_to_the_bytes_the_writer_wrote(tmp_path):
    sim, clock, out = fresh(tmp_path)
    w = scenario(sim, clock, lambda: make_writer(sim, out, clock, compress=True))
    assert w.compactor.drain(60)
    names = sorted(p.name for p in out.iterdir() if p.name.startswith("tape-"))
    assert names == ["tape-2026-11-01T05.jsonl.zst", "tape-2026-11-01T06.jsonl.zst", "tape-2026-11-01T07.jsonl"]
    counts = {json.loads(ln)["hour"]: json.loads(ln) for ln in (out / aw.META_DIR / "counts.jsonl").read_text().splitlines()}
    got = hours_bytes(out)
    for h in ("2026-11-01T05", "2026-11-01T06"):
        assert hashlib.sha256(got[h]).hexdigest() == counts[h]["sha256"] and len(got[h]) == counts[h]["bytes"]
    w.close()


# ------------------------------------------------------------------------------------------------------------------------------------ identity with an offline adaptation
def test_the_written_hours_are_byte_identical_to_adapting_the_same_rows_offline(tmp_path):
    sim, clock, out = fresh(tmp_path)
    w = scenario(sim, clock, lambda: make_writer(sim, out, clock, compress=True))
    w.compactor.drain(60)
    ref = offline_bytes(sim, clock.ms)
    got = hours_bytes(out)
    assert set(got) == set(ref) == {"2026-11-01T05", "2026-11-01T06", "2026-11-01T07"}
    for h in ref:
        assert got[h] == ref[h], h
    types = {json.loads(ln)["type"] for b in got.values() for ln in b.splitlines()}
    assert {"trade", "create", "complete", "pool_create", "gap"} <= types or {"trade", "create", "pool_create", "gap"} <= types
    st = w.status()
    assert st["no_tx_index"]["rows_no_tx_index"] > 0 and st["gaps"]["written"] == 1 and st["skips"] == 1
    w.close()


def test_tied_to_the_2023_harness_the_written_files_give_the_engine_the_same_decisions_as_the_adapter_rows(tmp_path):
    """The harness (scripts/research/arena_tip_equiv.py) builds the tip dir from a synthetic walker stream (crossing an hour mark, no-tx_index prints, V, skipped slots), reads it through the
    adapter and feeds ArenaEngine. Here the writer sits between: the written files, parsed back, feed the same engine and must give the same md5s, and their bytes equal the offline adaptation's."""
    eq = TE.eq
    scratch = tmp_path / "h"
    built = eq.build_tip_dir(TE.stream(), scratch / "tip", scratch / "state", skip_every=3)
    first_hour = tr.hour_key(TE.H - TE.LEAD)
    assert built["skips_injected"] >= 1 and built["walker_rows_no_tx_index"] > 0
    ref_stats: dict = {}
    arm_a = eq.Arm("A", scratch / "A", TE.VMAP, TE.CFG)
    ref_rows = []
    for r in eq.tip_rows(scratch / "tip", scratch / "state", first_hour, built["status_ts_ms"], ref_stats):
        arm_a.feed(r)
        ref_rows.append(r)
    clock = Clock(built["status_ts_ms"])
    reader = tr.TipReader(scratch / "tip", scratch / "state", start_ms=tr.hour_epoch(first_hour) * 1000, catch_up_hours=0, min_hour="2026-08-01T00", lookback_h=None, fadvise=False)
    src = ta.ArenaTipSource(reader, scratch / "state", now_ms=clock, io_hook=lambda: True)
    out = tmp_path / "out"
    w = aw.Writer(src, out, start_ms=tr.hour_epoch(first_hour) * 1000, catch_up_hours=0, grace_ms=GRACE, clock=clock, out_roots=[str(tmp_path)], compress=True, snapshot_s=0.0)
    w.start()
    for _ in range(10):
        w.step()
        if src.caught_up:
            break
    assert w.compactor.drain(60)
    got = hours_bytes(out)
    assert b"".join(got[h] for h in sorted(got)) == b"".join(aw.row_bytes(r) for r in ref_rows)  # same rows, same order, same bytes
    arm_b = eq.Arm("B", scratch / "B", TE.VMAP, TE.CFG)
    for h in sorted(got):
        for ln in got[h].splitlines():
            arm_b.feed(json.loads(ln))
    da, db = arm_a.digest(), arm_b.digest()
    assert da["orders"]["buys_open"] >= 10
    assert da["decisions_md5"] == db["decisions_md5"] and da["trades_md5"] == db["trades_md5"] and da["counters"] == db["counters"] and da["gaps_held"] == db["gaps_held"]
    w.close()


def test_the_serialiser_is_the_arena_feeds():
    row = {"v": 1, "type": "trade", "prog": "pumpswap", "slot": 5, "x": float("nan"), "n": [1, {"a": None}], "s": "é"}
    assert aw.row_bytes(row) == b'{"v":1,"type":"trade","prog":"pumpswap","slot":5,"x":null,"n":[1,{"a":null}],"s":"\\u00e9"}\n'
    try:
        from tools.h5_shadow import clean as feed_clean
    except Exception:  # noqa: BLE001 - the feed's module may need packages this venv lacks
        pytest.skip("tools.h5_shadow not importable here")
    assert aw.clean(row) == feed_clean(row)


# ------------------------------------------------------------------------------------------------------------------------------------ late rows, counts, status
def test_a_row_for_a_closed_hour_goes_to_the_current_hour_and_is_counted(tmp_path):
    sim, clock, out = fresh(tmp_path)
    w = scenario(sim, clock, lambda: make_writer(sim, out, clock), n_slots=400)
    assert "2026-11-01T05" in w.closed
    before = (out / "tape-2026-11-01T05.jsonl").read_bytes()
    late = TA.wt(401, 0, venue="pump_bonding", t=HM - 5000, tx=1)  # t_recv_ms in the closed hour 05 (a refetched older slot)
    sim.add(401, {"trades": [late], "creates": [], "migrations": []})
    clock.ms = sim.t_done + 1500
    sim.healthy(clock.ms)
    w.step()
    assert (out / "tape-2026-11-01T05.jsonl").read_bytes() == before and w.totals["late_to_current"] == 1
    assert b"s401x0" in (out / "tape-2026-11-01T06.jsonl").read_bytes() or b"s401x0" in (out / "tape-2026-11-01T07.jsonl").read_bytes()
    w.close()


def test_the_counts_row_and_the_heartbeat_carry_the_numbers_the_hour_health_check_needs(tmp_path):
    sim, clock, out = fresh(tmp_path)
    w = scenario(sim, clock, lambda: make_writer(sim, out, clock), n_slots=600)
    rows = [json.loads(ln) for ln in (out / aw.META_DIR / "counts.jsonl").read_text().splitlines()]
    r06 = next(r for r in rows if r["hour"] == "2026-11-01T06") if any(r["hour"] == "2026-11-01T06" for r in rows) else rows[-1]
    assert r06["schema"] == aw.SCHEMA_COUNTS and "pumpswap|trade" in r06["rows"] and r06["longest_data_silence_ms"]["pumpswap"] > 0
    gap_hour = next(r for r in rows if r["gap_rows"])
    assert gap_hour["gap_rows"] == {"tip_gap_backlog_jump|all": 1} and gap_hour["gap_missed_slots"] == 9 and gap_hour["gap_spans"] == [[211, 219, "tip_gap_backlog_jump", "all"]]
    assert sum(r["no_tx_index_rows"] for r in rows) > 0
    w.heartbeat(force=True)
    st = json.loads((out / aw.META_DIR / "status.json").read_text())
    for k in ("lag_ms", "closed_hours", "last_closed_hour", "gaps", "decoder_pin", "no_tx_index", "released_t_ms", "caught_up", "rss_mb", "pins", "dropped_rows", "compaction"):
        assert k in st
    assert st["decoder_pin"]["ok"] is True and st["no_tx_index"]["rows_no_tx_index"] > 0 and st["gaps"]["by_reason"] == {"backlog_jump": 1}
    assert st["state"] == "live" and aw.writer_problem(out, clock.ms) is None
    w.close()


def test_the_writer_never_writes_to_the_tip_or_its_state_dir(tmp_path):
    sim, clock, out = fresh(tmp_path)

    def snap(d: Path) -> dict:
        return {p.name: (p.stat().st_size, p.stat().st_mtime_ns, hashlib.md5(p.read_bytes()).hexdigest()) for p in sorted(d.iterdir())}

    w = scenario(sim, clock, lambda: make_writer(sim, out, clock), n_slots=100)
    tip0, st0 = snap(sim.tip), snap(sim.state)
    w.step()
    w.step()
    assert snap(sim.tip) == tip0 and snap(sim.state) == st0
    w.close()


# ------------------------------------------------------------------------------------------------------------------------------------ fail closed
@pytest.mark.parametrize("patch,code", [({"decoder_blobs_pinned": False}, "decoder_blobs_not_pinned"), ({"trade_event_v": False}, "event_v_off"),
                                        ({"decoder_blobs": {"observe/trade_decode.py": "0" * 40}}, "decoder_blobs_mismatch")])
def test_an_unpinned_decoder_writes_nothing_and_the_heartbeat_says_so(tmp_path, patch, code):
    sim, clock, out = fresh(tmp_path)
    w = make_writer(sim, out, clock)
    w.start()
    sim.add(1)
    clock.ms = sim.t_done + 1000
    sim.healthy(clock.ms)
    assert w.step() > 0
    n = w.totals["rows_written"]
    sim.add(2)
    TA.healthy(sim.state, clock.ms, **patch)
    assert w.step() == 0 and w.totals["rows_written"] == n and w.state == "unhealthy"
    w.heartbeat(force=True)
    st = json.loads((out / aw.META_DIR / "status.json").read_text())
    assert st["decoder_pin"] == {"ok": False, "tip_status_problem": code, "blobs": dict(tr.PINNED_BLOBS)}
    assert aw.writer_problem(out, clock.ms) == "writer_problem"
    sim.healthy(clock.ms)  # the pin is right again: the rows that waited are written, nothing is lost
    assert w.step() > 0 and w.state in ("live", "catching_up") and aw.writer_problem(out, clock.ms, need_caught_up=False) is None
    w.close()


def test_a_stale_tip_status_writes_nothing_and_past_the_limit_the_writer_exits_5(tmp_path):
    sim, clock, out = fresh(tmp_path)
    w = make_writer(sim, out, clock, max_unhealthy_s=0.2)
    w.start()
    sim.add(1)
    clock.ms = sim.t_done + 1000
    sim.healthy(clock.ms - 60_000)  # the follower's status is a minute old
    assert w.step() == 0 and w.problem == "tip:status_stale"
    with pytest.raises(aw.Fatal) as ei:
        time.sleep(0.3)
        w.step()
    assert ei.value.rc == aw.EXIT_UNHEALTHY
    w.close()


def test_a_latched_reader_problem_exits_6_at_once(tmp_path):
    sim, clock, out = fresh(tmp_path)
    w = make_writer(sim, out, clock)
    w.start()
    for s in range(1, 41):
        sim.add(s, checkpoint=False)
    sim.checkpoint()
    clock.ms = sim.t_done + 1000
    sim.healthy(clock.ms)
    w.step()
    TA.write_state(sim.state, last_done=5, last_t_recv_ms=sim.t(5), gaps=0, skipped=0)  # the follower restarted from an older point
    with pytest.raises(aw.Fatal) as ei:
        w.step()
    assert ei.value.rc == aw.EXIT_LATCHED and "checkpoint_regressed" in ei.value.why
    w.close()


def test_a_disk_below_the_reserve_pauses_everything_and_loses_nothing(tmp_path):
    sim, clock, out = fresh(tmp_path)
    free = {"pct": 50.0}

    class DU:
        total = 1000

        @property
        def free(self):
            return int(free["pct"] * 10)

    w = make_writer(sim, out, clock, disk_usage=lambda _p: DU(), min_free_pct=20.0)
    w.start()
    sim.add(1)
    clock.ms = sim.t_done + 1000
    sim.healthy(clock.ms)
    free["pct"] = 19.0
    assert w.step() == 0 and w.state == "disk_reserve" and w.src.adapter.stats["rows_trade"] == 0 and not any(p.name.startswith("tape-") for p in out.iterdir())
    free["pct"] = 21.0  # hysteresis: 2 points above the reserve before it resumes
    assert w.step() == 0
    free["pct"] = 23.0
    assert w.step() > 0 and any(p.name.startswith("tape-") for p in out.iterdir())
    w.close()


def test_start_refuses_without_idle_io_and_a_second_writer_on_the_dir_exits_3(tmp_path):
    sim, clock, out = fresh(tmp_path)
    sim.add(1)
    sim.healthy(clock.ms)
    w = make_writer(sim, out, clock, io_ok=False)
    with pytest.raises(ta.TipUnhealthy, match="ionice"):
        w.start()
    with pytest.raises(aw.Fatal) as ei:
        make_writer(sim, out, clock)
    assert ei.value.rc == aw.EXIT_LOCKED
    w.close()


@pytest.mark.parametrize("path", ["/home/x/mal-data/probe-feed/tape", "/home/x/mal-data/a/sealed/tape", "/home/x/mal-data/tip-tape-copy", "/home/x/other/tape", "relative/tape", "/home/x/mal-data"])
def test_the_output_dir_must_be_a_clean_child_of_the_data_root(path):
    with pytest.raises(aw.Refused):
        aw.check_out_dir(path, roots=["/home/x/mal-data"])
    assert aw.check_out_dir("/home/x/mal-data/arena-tipfeed/tape", roots=["/home/x/mal-data"]) == Path("/home/x/mal-data/arena-tipfeed/tape")
    assert aw.DEFAULT_OUT.startswith(os.path.join(aw.HOME, "mal-data")) and not any(t in aw.DEFAULT_OUT for t in aw.FORBIDDEN_PARTS)


def test_the_catch_up_window_is_capped_at_the_followers_retention():
    assert aw.LOOKBACK_MAX_H == 72
    with pytest.raises(tr.TipRefused):
        tr.TipReader("/nonexistent-tip", "/nonexistent-state", start_ms=HM, catch_up_hours=73, lookback_h=aw.LOOKBACK_MAX_H)
    r = tr.TipReader("/var/lib/mal/sealed/fast-trades-tip", "/nonexistent-state", start_ms=HM, catch_up_hours=72, lookback_h=aw.LOOKBACK_MAX_H)  # constructed, nothing opened
    assert r.floor == "2026-10-29T06" and r.h0 == tr.hour_epoch("2026-10-29T06")


def test_the_consumer_check_fails_closed_on_every_anomaly(tmp_path):
    sim, clock, out = fresh(tmp_path)
    w = scenario(sim, clock, lambda: make_writer(sim, out, clock), n_slots=100)
    w.heartbeat(force=True)
    sp = out / aw.META_DIR / "status.json"
    good = json.loads(sp.read_text())
    now = good["ms"]
    assert aw.writer_problem(out, now) is None and aw.writer_problem(out, now + 4000) is None
    assert aw.writer_problem(out, now + 6000) == "status_stale" and aw.writer_problem(out, now - 5000) == "status_future"

    def bad(**kw):
        sp.write_text(json.dumps({**good, **kw}))
        return aw.writer_problem(out, now)

    assert bad(problem="tip:status_stale") == "writer_problem" and bad(state="failed") == "state_failed" and bad(decoder_pin={"ok": False}) == "decoder_pin"
    assert bad(caught_up=False) == "not_caught_up" and bad(schema="x") == "status_schema" and bad(ms="1") == "status_schema"
    sp.write_text("{not json")
    assert aw.writer_problem(out, now) == "status_unreadable"
    sp.unlink()
    assert aw.writer_problem(out, now) == "status_missing"
    w.close()


# ------------------------------------------------------------------------------------------------------------------------------------ resume
def uninterrupted(tmp_path: Path) -> tuple[dict[str, bytes], dict, int]:
    sim, clock, out = fresh(tmp_path, "ref")
    w = scenario(sim, clock, lambda: make_writer(sim, out, clock))
    res = hours_bytes(out)
    st = w.status()
    n_steps = 30
    w.close()
    return res, {k: st[k] for k in ("rows_written", "bytes_written", "gaps", "no_tx_index", "skips")}, n_steps


def run_killed(tmp_path: Path, k: int, **kw) -> tuple[aw.Writer, TipSim, Clock, Path]:
    """Kill the writer after step k (its files are closed, nothing else is saved), then start a NEW writer on the same dir and let it finish the same scenario."""
    sim, clock, out = fresh(tmp_path, f"k{k}")
    scenario(sim, clock, lambda: make_writer(sim, out, clock, **kw), kill_after=k)
    w2 = make_writer(sim, out, clock, **kw)
    w2.start()
    scenario(sim, clock, None, w=w2, start_slot=sim.slot_done + 1 if sim.slot_done < 600 else 601)
    return w2, sim, clock, out


def test_a_writer_killed_after_every_poll_resumes_to_exactly_the_uninterrupted_bytes(tmp_path):
    ref, ref_st, n_steps = uninterrupted(tmp_path)
    assert len(ref) == 3 and ref_st["gaps"]["written"] == 1
    for k in range(1, n_steps):
        w2, _sim, _clock, out = run_killed(tmp_path, k)
        assert hours_bytes(out) == ref, f"killed after step {k}"
        st = w2.status()
        assert st["resumed"] is True and {x: st[x] for x in ref_st} == ref_st, k
        w2.close()


def test_bytes_written_after_the_last_snapshot_are_verified_not_trusted_and_the_file_never_shrinks(tmp_path):
    ref, _st, _n = uninterrupted(tmp_path)
    # snapshots only at start, close and stop: the kill leaves a long unsnapshotted tail on disk
    sim, clock, out = fresh(tmp_path, "tail")
    scenario(sim, clock, lambda: make_writer(sim, out, clock, snapshot_s=1e9), kill_after=6)
    sizes = {p.name: p.stat().st_size for p in out.iterdir() if p.name.startswith("tape-")}
    w2 = make_writer(sim, out, clock, snapshot_s=1e9)
    w2.start()
    assert w2.verify and sum(w2.verify.values()) > 0
    scenario(sim, clock, None, w=w2, start_slot=sim.slot_done + 1)
    assert hours_bytes(out) == ref and w2.totals["verified_tail_bytes"] > 0 and not w2.verify
    assert all((out / n).stat().st_size >= s for n, s in sizes.items())
    w2.close()


def test_a_tail_that_the_regenerated_rows_do_not_reproduce_refuses_the_resume(tmp_path):
    sim, clock, out = fresh(tmp_path, "div")
    scenario(sim, clock, lambda: make_writer(sim, out, clock, snapshot_s=1e9), kill_after=6)
    p = sorted(out.glob("tape-*.jsonl"))[-1]
    data = p.read_bytes()
    i = data.rfind(b"\n", 0, len(data) - 1)
    p.write_bytes(data[:i + 1] + data[i + 1:].replace(b"s", b"S", 1))  # one byte of the last line differs
    w2 = make_writer(sim, out, clock, snapshot_s=1e9)
    w2.start()
    with pytest.raises(aw.Fatal) as ei:
        scenario(sim, clock, None, w=w2, start_slot=sim.slot_done + 1)
    assert ei.value.rc == aw.EXIT_DIVERGED
    w2.close()


def test_a_torn_last_line_is_trimmed_and_a_short_file_or_a_changed_pin_or_config_refuses(tmp_path):
    ref, _s, _n = uninterrupted(tmp_path)
    sim, clock, out = fresh(tmp_path, "torn")
    scenario(sim, clock, lambda: make_writer(sim, out, clock), kill_after=8)
    p = sorted(out.glob("tape-*.jsonl"))[-1]
    with open(p, "ab") as fh:
        fh.write(b'{"v":1,"type":"tra')  # the kill happened mid-write
    w2 = make_writer(sim, out, clock)
    w2.start()
    scenario(sim, clock, None, w=w2, start_slot=sim.slot_done + 1)
    assert hours_bytes(out) == ref
    w2.close()
    # a short file
    sim3, clock3, out3 = fresh(tmp_path, "short")
    scenario(sim3, clock3, lambda: make_writer(sim3, out3, clock3), kill_after=8)
    q = sorted(out3.glob("tape-*.jsonl"))[-1]
    q.write_bytes(q.read_bytes()[:-50])
    with pytest.raises(aw.Refused, match="out_shorter_than_state|open_hour_missing"):
        make_writer(sim3, out3, clock3).start()
    # a changed pin / config
    for name, kw in (("pin", {"pins": {**aw.code_pins(), "writer_sha256": "0" * 64}}), ("grace", {"grace_ms": GRACE + 1})):
        s4, c4, o4 = fresh(tmp_path, "c" + name)
        scenario(s4, c4, lambda: make_writer(s4, o4, c4), kill_after=3)
        w4 = None
        try:
            w4 = make_writer(s4, o4, c4, **kw)
            with pytest.raises(aw.Refused, match="pins_changed|config_changed"):
                w4.start()
        finally:
            if w4 is not None:
                w4.close()


def test_snapshot_and_restore_round_trip_the_reader_and_the_adapter(tmp_path):
    sim, clock, out = fresh(tmp_path)
    w = scenario(sim, clock, lambda: make_writer(sim, out, clock), n_slots=250)  # inside the backlog-jump window: the gap may be pending
    snap = aw.snapshot_source(w.src)
    clone = make_source(sim, clock)
    aw.restore_source(clone, copy.deepcopy(json.loads(json.dumps(snap))))
    assert aw.snapshot_source(clone) == snap
    w.close()
