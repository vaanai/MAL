"""Tests for tools/arena_tip_tape_writer.py (HV7 tip plan step 5, QP-RULING-1101-hv4-reads 4.1 (B)): hour close only after the checkpoint has passed the hour plus the grace, byte identity with an
offline adaptation of the same rows (tied to the #2023 harness: scripts/research/arena_tip_equiv.py), fail-closed paths, resume (kill after every poll), the heartbeat and the consumer check.
SYNTHETIC rows in tmp dirs only: no real tip file, arena tape, mirror, ledger or network is touched.

  /data/mal/venv/bin/python -m pytest tools/test_arena_tip_tape_writer.py -q
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
from tools import arena_tip_tape_writer as aw  # noqa: E402
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


def scenario(sim: TipSim, clock: Clock, w_factory, n_slots: int = 600, every: int = 20, kill_after: int | None = None, w=None, start_slot: int = 1, rows_fn=None) -> aw.Writer | None:
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
        sim.add(s, rows_fn(s) if rows_fn else None, checkpoint=False)
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
    assert r06["schema"] == aw.SCHEMA_COUNTS and "pumpswap|trade" in r06["rows"] and r06["max_data_silence_s"]["pumpswap"] > 0
    # the QP ruling's W8 fields: the writer's sha256s, run id and start instant, the four no-tx counts, dropped rows, skips, status failures, every gap row with slots and instants
    for k in ("run_id", "runs", "started_ms", "pins", "no_tx_index", "dropped_rows", "skips", "status_failures", "status_bad_samples", "max_loss_span_s", "hb_gaps", "sha256", "bytes"):
        assert k in r06, k
    assert set(r06["pins"]) == {"writer_sha256", "adapter_sha256", "reader_sha256"} and r06["runs"] == [r06["run_id"]] and set(r06["no_tx_index"]) == {
        "rows_no_tx_index", "pool_slots_no_tx_index", "pool_slots_multi_sig", "pool_slots_mixed"}
    gap_hour = next(r for r in rows if r["gap_rows"])
    assert gap_hour["gap_rows"] == {"tip_gap_backlog_jump|all": 1} and gap_hour["gap_missed_slots"] == 9
    kind, prog, lo, hi, f_ms, t_ms = gap_hour["gap_spans"][0]
    assert (kind, prog, lo, hi) == ("tip_gap_backlog_jump", "all", 211, 219) and f_ms <= t_ms and gap_hour["max_loss_span_s"] == round((t_ms - f_ms) / 1000, 3)
    assert sum(r["no_tx_index_rows"] for r in rows) > 0 and sum(r["no_tx_index"]["rows_no_tx_index"] for r in rows) > 0 and sum(r["skips"] for r in rows) == 1
    blob = json.dumps(rows)
    for forbidden in ('"mint"', '"pool"', '"signature"', '"trader"', '"price"', '"quote_reserve"', '"sol_lamports"'):  # W8: the counts log holds no mint, pool, signature, trader, amount, reserve, price
        assert forbidden not in blob
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
    assert w.totals["gap_rows_written"] == 1  # W9: the unhealthy spell becomes ONE data_loss gap row (prog all), after the rows it held back
    g = [json.loads(ln) for b in hours_bytes(out).values() for ln in b.splitlines() if b'"type":"gap"' in ln][-1]
    assert g["kind"] == "tip_gap_status_unhealthy" and g["prog"] == "all" and g["data_loss"] is True and g["from_ms"] <= g["to_ms"] == g["t_recv_ms"] and g["src"] == "writer"
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
    assert aw.LOOKBACK_MAX_H == 70 and aw.CATCH_UP_H_DEFAULT == 70
    with pytest.raises(tr.TipRefused):
        tr.TipReader("/nonexistent-tip", "/nonexistent-state", start_ms=HM, catch_up_hours=71, lookback_h=aw.LOOKBACK_MAX_H)
    r = tr.TipReader("/var/lib/mal/sealed/fast-trades-tip", "/nonexistent-state", start_ms=HM, catch_up_hours=70, lookback_h=aw.LOOKBACK_MAX_H)  # constructed, nothing opened
    assert r.floor == "2026-10-29T08" and r.h0 == tr.hour_epoch("2026-10-29T08")


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


# ------------------------------------------------------------------------------------------------------------------------------------ QP-RULING-1101-arena-tip-reader refusals and conditions
@pytest.mark.parametrize("out", ["/var/lib/mal/sealed/x/tape", "/data/mal/arena-tip/tape", "/data/mal/arena/tape", "/home/claude/mal-data/tip-tape-archive/tape"])
def test_the_output_dir_cannot_be_the_sealed_tree_the_walker_filesystem_or_the_archive(out):
    with pytest.raises(aw.Refused):
        aw.check_out_dir(out, roots=["/var/lib/mal", "/data/mal", "/home/claude/mal-data"])


def test_the_tip_dir_is_the_production_pair_or_a_test_pair_and_never_overlaps_the_output(tmp_path, monkeypatch):
    monkeypatch.delenv("MAL_ARENA_TEST", raising=False)
    aw.check_tip_dir(tr.DEFAULT_TIP_DIR, "/home/claude/mal-data/arena-tipfeed/tape", tr.DEFAULT_STATE_DIR)  # production: both production paths, no test flag
    aw.check_tip_dir(tr.DEFAULT_TIP_DIR, "/home/claude/mal-data/arena-tipfeed/tape")  # the state dir defaults to the production one
    # C5: a production tip dir with another state dir would bypass the pin / freshness check; a synthetic tip dir needs the test flag
    with pytest.raises(aw.Refused, match="tip_dir_state_dir_mismatch"):
        aw.check_tip_dir(tr.DEFAULT_TIP_DIR, tmp_path / "out", tmp_path / "state")
    with pytest.raises(aw.Refused, match="tip_dir_state_dir_mismatch"):
        aw.check_tip_dir(tmp_path / "tip", tmp_path / "out", tr.DEFAULT_STATE_DIR)
    with pytest.raises(aw.Refused, match="tip_dir_not_allowed"):
        aw.check_tip_dir(tmp_path / "tip", tmp_path / "out", tmp_path / "state")  # no MAL_ARENA_TEST
    monkeypatch.setenv("MAL_ARENA_TEST", "1")
    aw.check_tip_dir(tmp_path / "tip", tmp_path / "out", tmp_path / "state")
    for bad in ("/data/mal/tip-tape-archive", "/var/lib/mal/sealed/other", "/home/claude/mal-data/x", "/data/mal/arena/tape"):
        with pytest.raises(aw.Refused, match="tip_dir_not_allowed"):
            aw.check_tip_dir(bad, tmp_path / "out", tmp_path / "state")
    with pytest.raises(aw.Refused, match="tip_dir_not_allowed"):
        aw.check_tip_dir(tmp_path / "tip", tmp_path / "out", "/home/claude/mal-data/state")  # the test state dir must be under the tmp / scratch roots too
    with pytest.raises(aw.Refused, match="overlaps"):
        aw.check_tip_dir(tmp_path / "tip", tmp_path / "tip" / "out", tmp_path / "state")
    monkeypatch.delenv("MAL_ARENA_TEST")
    rc = aw.main(["run", "--tip-dir", "/data/mal/tip-tape-archive", "--out-dir", str(Path(aw.OUT_ROOTS[0]) / "x" / "tape")])
    assert rc == aw.EXIT_REFUSED
    rc = aw.main(["run", "--tip-dir", str(tmp_path / "tip"), "--tip-state-dir", str(tmp_path / "state"), "--out-dir", str(Path(aw.OUT_ROOTS[0]) / "x" / "tape")])
    assert rc == aw.EXIT_REFUSED


def test_catch_up_must_reach_caught_up_within_the_deadline_and_fadvise_stops_once_live(tmp_path):
    sim, clock, out = fresh(tmp_path)
    for s in range(1, 200):
        sim.add(s, checkpoint=False)
    sim.checkpoint()
    clock.ms = sim.t_done + 1000
    sim.healthy(clock.ms)
    w = make_writer(sim, out, clock, poll_limit=30, catch_up_deadline_min=1.0)
    w.start()
    w.step()
    assert not w.src.caught_up and w.src.reader.fadvise is False  # make_source builds the reader with fadvise off; the production reader has it on (below)
    clock.ms += 61_000
    sim.healthy(clock.ms)
    with pytest.raises(aw.Fatal) as ei:
        w.step()
    assert ei.value.rc == aw.EXIT_CATCHUP and ei.value.why == "catch_up_deadline"
    w.close()
    sim2, clock2, out2 = fresh(tmp_path, "fa")
    sim2.add(1)
    clock2.ms = sim2.t_done + 1000
    sim2.healthy(clock2.ms)
    reader = tr.TipReader(sim2.tip, sim2.state, start_ms=TipSim.T_BASE, catch_up_hours=0, min_hour="2026-10-01T00", lookback_h=None, fadvise=True)
    w2 = aw.Writer(ta.ArenaTipSource(reader, sim2.state, now_ms=clock2, io_hook=lambda: True), out2, start_ms=TipSim.T_BASE, catch_up_hours=0, clock=clock2, out_roots=[str(out2.parent)],
                   compress=False)
    w2.start()
    assert reader.fadvise is True
    w2.step()
    assert w2.src.caught_up and reader.fadvise is False  # the first caught-up poll turns DONTNEED off
    w2.close()


def test_a_closed_hour_is_deleted_only_after_the_receiver_verified_it_plus_24h(tmp_path):
    sim, clock, out = fresh(tmp_path)
    w = scenario(sim, clock, lambda: make_writer(sim, out, clock, compress=True), n_slots=600)
    w.compactor.drain(60)
    z = sorted(out.glob("tape-*.jsonl.zst"))
    assert len(z) == 2
    assert w.prune(force=True) == 0 and len(list(out.glob("tape-*.jsonl.zst"))) == 2  # nothing verified: nothing deleted
    (out / ".push").mkdir()
    h0, h1 = z[0].name[5:18], z[1].name[5:18]
    aw.write_json_atomic(out / ".push" / "verified.json", {"hours": {h0: clock.ms - 25 * aw.H_MS, h1: clock.ms - 3 * aw.H_MS}})
    assert w.prune(force=True) == 1 and not z[0].exists() and z[1].exists()  # verified 25 h ago: gone; verified 3 h ago: kept
    (out / ".push" / "verified.json").write_text("not json")
    assert w.prune(force=True) == 0 and z[1].exists()
    w.close()


def test_a_follower_backlog_jump_during_our_catch_up_stops_the_writer_but_not_after_it_is_live(tmp_path):
    sim, clock, out = fresh(tmp_path)
    for s in range(1, 120):
        sim.add(s, checkpoint=False)
    sim.checkpoint()
    clock.ms = sim.t_done + 1000
    TA.healthy(sim.state, clock.ms, backlog_jumps=3)
    w = make_writer(sim, out, clock, poll_limit=25)
    w.start()
    assert w.step() > 0 and w.bj_base == 3 and not w.src.caught_up
    TA.healthy(sim.state, clock.ms, backlog_jumps=3)
    assert w.step() > 0  # unchanged: fine
    TA.healthy(sim.state, clock.ms, backlog_jumps=4)
    with pytest.raises(aw.Fatal) as ei:
        w.step()
    assert ei.value.rc == aw.EXIT_BACKLOG and "follower_backlog_jump_during_catch_up_3_to_4" in ei.value.why
    w.close()
    sim2, clock2, out2 = fresh(tmp_path, "live")
    sim2.add(1)
    clock2.ms = sim2.t_done + 1000
    TA.healthy(sim2.state, clock2.ms, backlog_jumps=0)
    w2 = make_writer(sim2, out2, clock2)
    w2.start()
    w2.step()
    assert w2.src.caught_up and w2.totals["caught_up_once"]
    TA.healthy(sim2.state, clock2.ms, backlog_jumps=9)  # a jump once live is the follower's own business: the adapter marks it as a gap row, the writer goes on
    sim2.add(2)
    assert w2.step() > 0
    w2.close()


# ------------------------------------------------------------------------------------------------------------------------------------ QP on #1167: C1-C7
def written_rows(out: Path) -> list[dict]:
    return [json.loads(ln) for h, b in sorted(hours_bytes(out).items()) for ln in b.splitlines()]


def test_c1_the_w9_row_makes_the_engine_exclude_from_the_last_healthy_instant(tmp_path):
    sim, clock, out = fresh(tmp_path)
    w = make_writer(sim, out, clock)
    w.start()
    sim.add(1)
    clock.ms = sim.t_done + 1000
    sim.healthy(clock.ms)
    w.step()
    last_healthy = clock.ms
    sim.add(2)
    TA.healthy(sim.state, clock.ms, decoder_blobs_pinned=False)
    assert w.step() == 0
    sim.add(3)
    clock.ms = sim.t_done + 5000
    sim.healthy(clock.ms)
    assert w.step() > 0
    w.close()
    rows = written_rows(out)
    gap = next(r for r in rows if r["type"] == "gap" and r["src"] == "writer")
    assert gap["from_ms"] == last_healthy and gap["silent_ms"] == gap["to_ms"] - gap["from_ms"] > 0
    newest_trade = max(r["t_recv_ms"] for r in rows if r["type"] == "trade")
    assert newest_trade > gap["from_ms"]  # the held-back rows come first: without silent_ms the engine would start the exclusion at them
    eng = TA.mk_engine(tmp_path / "e1", gap_slots=None, gap_silence_ms=None)
    for r in rows:
        eng.on_row(r)
    assert eng.counters.get("gap_data_loss") == 1 and eng.gaps[-1].t_lo == gap["from_ms"] and eng.gaps[-1].t_hi == gap["to_ms"] and eng.gaps[-1].kind == "tip_gap_status_unhealthy"
    mutant = TA.mk_engine(tmp_path / "e2", gap_slots=None, gap_silence_ms=None)  # the same rows with silent_ms stripped: the engine's exclusion starts too late (the bug QP found)
    for r in rows:
        mutant.on_row({k: v for k, v in r.items() if k != "silent_ms"} if r["type"] == "gap" else r)
    assert mutant.gaps[-1].t_lo == newest_trade and mutant.gaps[-1].t_lo > gap["from_ms"]


def test_c2_a_silence_that_crosses_an_hour_boundary_shows_in_both_hours(tmp_path):
    sim, clock, out = fresh(tmp_path)

    def rows_fn(s):
        r = sim.rows(s)
        if 125 <= s <= 190:  # no PumpSwap trade from 05:55:00 to 06:08:00 (the bonding program goes on; the migration at slot 186 is the first PumpSwap data row after)
            r["trades"] = [x for x in r["trades"] if x["venue"] != "pumpswap"]
            if s != 186:
                r["migrations"] = []  # the migrations at 155 would be PumpSwap data rows too
        return r

    w = scenario(sim, clock, lambda: make_writer(sim, out, clock), n_slots=600, rows_fn=rows_fn)
    w.close()
    rows = {json.loads(ln)["hour"]: json.loads(ln) for ln in (out / aw.META_DIR / "counts.jsonl").read_text().splitlines()}
    h5, h6 = rows["2026-11-01T05"], rows["2026-11-01T06"]
    last5, first6 = h5["data_t_last_ms"]["pumpswap"], h6["data_t_first_ms"]["pumpswap"]
    assert first6 - last5 == 62 * STEP_MS and last5 < HM <= first6  # the consumer sees the silence from the two rows (slot 124 -> the migration's pool_create at slot 186, a PumpSwap data row)
    assert h6["max_data_silence_ending_s"]["pumpswap"] >= 62 * STEP_MS / 1000  # it ENDS in hour 06 ...
    assert h5["max_data_silence_s"].get("pumpswap", 0) < 62 * STEP_MS / 1000  # ... hour 05 was already closed when it ended, so its row cannot hold it ...
    clo = [json.loads(ln) for ln in (out / aw.META_DIR / "silence.jsonl").read_text().splitlines()]  # ... and it is filed under the hour it STARTED in, as a closure line
    assert [(c["hour"], c["prog"], c["silence_s"], c["from_ms"], c["to_ms"]) for c in clo if c["silence_s"] > 60] == [("2026-11-01T05", "pumpswap", 744.0, last5, first6)]
    assert "pumpfun" in h5["data_t_first_ms"] and set(h5["newest_data_t_ms_at_close"]) >= {"pumpswap", "pumpfun"}


def test_a_silence_inside_one_hour_is_in_that_hours_row_and_a_silence_that_starts_in_an_open_hour_is_filed_there(tmp_path):
    sim, clock, out = fresh(tmp_path)

    def rows_fn(s):
        r = sim.rows(s)
        if 300 <= s <= 330:  # 06:30 -> 06:36 inside hour 06: no PumpSwap
            r["trades"] = [x for x in r["trades"] if x["venue"] != "pumpswap"]
        return r

    w = scenario(sim, clock, lambda: make_writer(sim, out, clock), n_slots=600, rows_fn=rows_fn)
    w.close()
    h6 = next(json.loads(ln) for ln in (out / aw.META_DIR / "counts.jsonl").read_text().splitlines() if '"2026-11-01T06"' in ln)
    assert h6["max_data_silence_s"]["pumpswap"] == h6["max_data_silence_ending_s"]["pumpswap"] == 21 * STEP_MS / 1000  # slot 299 -> the migration row at 310 -> slot 331: the longer leg


def test_c4_w7_is_armed_and_re_based_at_every_start_resume_included(tmp_path):
    sim, clock, out = fresh(tmp_path)
    sim.add(1)
    clock.ms = sim.t_done + 1000
    TA.healthy(sim.state, clock.ms, backlog_jumps=3)
    w = make_writer(sim, out, clock)
    w.start()
    w.step()
    assert w.live_run and w.bj_base == 3
    w.close()  # the writer exits (cancel, crash); meanwhile the follower jumped and a lot of rows piled up
    for s in range(2, 200):
        sim.add(s, checkpoint=False)
    sim.checkpoint()
    clock.ms = sim.t_done + 1000
    TA.healthy(sim.state, clock.ms, backlog_jumps=5)
    w2 = make_writer(sim, out, clock, poll_limit=25)
    w2.start()
    assert w2.resumed and w2.bj_base == 5 and not w2.live_run  # re-based: the jump that happened while we were down is not ours
    assert w2.step() > 0 and not w2.src.caught_up
    TA.healthy(sim.state, clock.ms, backlog_jumps=6)
    with pytest.raises(aw.Fatal) as ei:
        w2.step()
    assert ei.value.rc == aw.EXIT_BACKLOG
    w2.close()


def test_c5_the_state_dir_is_validated_in_the_production_pair(tmp_path, monkeypatch):
    monkeypatch.setenv("MAL_ARENA_TEST", "1")
    with pytest.raises(aw.Refused, match="tip_dir_state_dir_mismatch"):
        aw.check_tip_dir(tr.DEFAULT_TIP_DIR, "/home/claude/mal-data/arena-tipfeed/tape", "/tmp/fake-state")  # even with the test flag
    rc = aw.main(["run", "--tip-dir", tr.DEFAULT_TIP_DIR, "--tip-state-dir", str(tmp_path / "state"), "--out-dir", str(Path(aw.OUT_ROOTS[0]) / "x" / "tape")])
    assert rc == aw.EXIT_REFUSED


def test_c6_the_catch_up_deadline_runs_from_the_first_start_across_resumes(tmp_path):
    sim, clock, out = fresh(tmp_path)
    for s in range(1, 200):
        sim.add(s, checkpoint=False)
    sim.checkpoint()
    clock.ms = sim.t_done + 1000
    sim.healthy(clock.ms)
    first = clock.ms
    w = make_writer(sim, out, clock, poll_limit=25, catch_up_deadline_min=60.0)
    w.start()
    assert w.step() > 0 and not w.src.caught_up
    w.close()
    clock.ms = first + 61 * 60_000  # 61 minutes after the FIRST start, 0 after the restart
    sim.healthy(clock.ms)
    w2 = make_writer(sim, out, clock, poll_limit=25, catch_up_deadline_min=60.0)
    w2.start()
    assert w2.first_start_ms == first
    with pytest.raises(aw.Fatal) as ei:
        w2.step()
    assert ei.value.rc == aw.EXIT_CATCHUP
    w2.close()


def test_c7_a_recoverable_reader_problem_spell_writes_one_reader_problem_gap_row(tmp_path):
    sim, clock, out = fresh(tmp_path)
    w = make_writer(sim, out, clock)
    w.start()
    sim.add(1)
    clock.ms = sim.t_done + 1000
    sim.healthy(clock.ms)
    w.step()
    (sim.state / "checkpoint.json").write_text("{not json")  # present but unparsable: recoverable, not latched
    sim.add(2, checkpoint=False)
    assert w.step() == 0 and w.problem == "checkpoint_unreadable" and w.state == "unhealthy"
    assert w.step() == 0
    sim.checkpoint()
    clock.ms = sim.t_done + 2000
    sim.healthy(clock.ms)
    assert w.step() > 0
    w.close()
    gaps = [r for r in written_rows(out) if r["type"] == "gap"]
    assert [g["kind"] for g in gaps] == ["tip_gap_reader_problem"] and gaps[0]["prog"] == "all" and gaps[0]["data_loss"] is True and gaps[0]["silent_ms"] > 0
    assert w.totals["gap_rows_written"] == 1


def test_a_resume_from_a_state_older_than_60_hours_is_refused(tmp_path):
    sim, clock, out = fresh(tmp_path)
    scenario(sim, clock, lambda: make_writer(sim, out, clock), kill_after=3)
    clock.ms += 61 * aw.H_MS
    sim.healthy(clock.ms)
    w = make_writer(sim, out, clock)
    with pytest.raises(aw.Refused, match="state_older_than_60h"):
        w.start()
    w.close()


def test_the_status_carries_the_rss_trigger_and_the_job_pins_the_cap():
    assert aw.RSS_TRIGGER_MIB == 1152
    text = (Path(aw.__file__).resolve().parent.parent / "scripts" / "ops" / "arena-tip-writer-job.sh").read_text()
    assert "MEM_MAX=${MEM_MAX:-1536M}" in text and "MemorySwapMax=0" in text and "1152" in text and "mem_mb 1600" in text


# ------------------------------------------------------------------------------------------------------------------------------------ reviewer on #1167: S1, S2
def test_s1_the_state_keeps_only_the_hour_keys_of_confirmed_hours_and_a_kill_before_the_counts_row_is_repaired(tmp_path, monkeypatch):
    assert aw.SNAPSHOT_EVERY_S >= 5.0
    sim, clock, out = fresh(tmp_path)
    w = scenario(sim, clock, lambda: make_writer(sim, out, clock), n_slots=600)
    st = json.loads((out / aw.META_DIR / "state.json").read_text())
    assert set(st["closed"]) == {"2026-11-01T05", "2026-11-01T06"} and all(v is None for v in st["closed"].values())
    w.close()
    # the kill window: the close is durable in the state but the counts row was not appended yet
    sim2, clock2, out2 = fresh(tmp_path, "crash")
    w2 = make_writer(sim2, out2, clock2)
    w2.start()

    def boom(self_row):
        raise RuntimeError("killed here")

    monkeypatch.setattr(w2, "_append_counts", boom)
    with pytest.raises(RuntimeError):
        scenario(sim2, clock2, None, w=w2, n_slots=400)
    w2.close()
    st2 = json.loads((out2 / aw.META_DIR / "state.json").read_text())
    assert st2["closed"]["2026-11-01T05"]["hour"] == "2026-11-01T05" and not (out2 / aw.META_DIR / "counts.jsonl").exists()  # the row is in the state only
    w3 = make_writer(sim2, out2, clock2)
    w3.start()
    rows = [json.loads(ln) for ln in (out2 / aw.META_DIR / "counts.jsonl").read_text().splitlines()]
    assert [r["hour"] for r in rows] == ["2026-11-01T05"] and w3.closed["2026-11-01T05"] is None
    w3.close()


def test_s2_exits_6_to_9_latch_a_plain_rerun_refuses_until_the_marker_is_removed(tmp_path):
    sim, clock, out = fresh(tmp_path)
    for s in range(1, 200):
        sim.add(s, checkpoint=False)
    sim.checkpoint()
    clock.ms = sim.t_done + 1000
    sim.healthy(clock.ms)
    w = make_writer(sim, out, clock, poll_limit=25, catch_up_deadline_min=1.0)
    w.start()
    w.step()
    clock.ms += 61_000
    sim.healthy(clock.ms)
    rc = w.run(__import__("threading").Event(), max_s=5)
    assert rc == aw.EXIT_CATCHUP
    w.close()
    marker = json.loads((out / aw.META_DIR / aw.STOP_MARKER).read_text())
    assert marker["rc"] == 8 and marker["why"] == "catch_up_deadline"
    w2 = make_writer(sim, out, clock)
    with pytest.raises(aw.Refused, match="stopped_marker_rc8_catch_up_deadline"):
        w2.start()
    w2.close()
    (out / aw.META_DIR / aw.STOP_MARKER).unlink()  # a human's act
    w3 = make_writer(sim, out, clock, poll_limit=25, catch_up_deadline_min=1.0)
    with pytest.raises(aw.Fatal):  # the deadline still runs from the first start
        w3.start()
        w3.step()
    w3.close()
