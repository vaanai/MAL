"""Tests for tools/r4b_tip_reader.py. SYNTHETIC files in tmp dirs and the REAL tools.fast_tip_follower.TipFollower (_commit / _emit) with a fake RPC. No real tip file,
no sealed path, no network is touched (QP-RULING-1031-r4b-history P-C1 (a), (b) and the reader's mutation checks).

  PYTHONPATH=/data/mal/audit-1008/venv/lib/python3.12/site-packages /data/mal/venv/bin/python -m pytest tools/test_r4b_tip_reader.py -q
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools import fast_tip_follower as ftf
from tools import r4b_tip_reader as tr
from tools.test_fast_tip_follower import Rpc, _block, _follower, _seed  # noqa: F401

B = int(datetime(2026, 11, 1, 6, 0, tzinfo=timezone.utc).timestamp())  # an hour mark
H05, H06 = "2026-11-01T05", "2026-11-01T06"


class SetClock:
    def __init__(self, ms: int) -> None:
        self.ms = ms

    def __call__(self) -> int:
        return self.ms


def write_state(state: Path, **kw) -> None:
    state.mkdir(parents=True, exist_ok=True)
    (state / "checkpoint.json").write_text(json.dumps({"last_done": None, "last_t_recv_ms": 0, "gaps": 0, "skipped": 0, "writing": None, **kw}))


def trade(slot: int, i: int = 0) -> dict:
    return {"venue": "pump_bonding", "mint": "M", "trader": "T", "side": "buy", "sol_lamports": 1, "token_raw": 1, "quote_reserve": 1, "base_reserve": 1, "pool": None,
            "slot": slot, "signature": f"s{slot}x{i}", "event_index": i, "tx_index": i, "block_time": 1_000 + slot, "t_recv_ms": 1, "source": "tip"}


def put(d: Path, name: str, rows: list, mode: str = "w") -> None:
    d.mkdir(parents=True, exist_ok=True)
    with open(d / name, mode) as fh:
        for r in rows:
            fh.write((r if isinstance(r, str) else json.dumps(r, separators=(",", ":"))) + "\n")


def reader(tmp_path: Path, **kw) -> tr.TipReader:
    kw.setdefault("start_ms", (B - 3600 + 600) * 1000)  # 05:10Z: the first hour read is 05
    kw.setdefault("catch_up_hours", 0)
    return tr.TipReader(tmp_path / "out", tmp_path / "state", **kw)


def slots_of(items, kind="trade") -> list:
    return [r["slot"] for k, r in items if k == kind]


# ------------------------------------------------------------------------------------------------------------------------------------ P-C1 (a): refusals
def test_pins_equal_the_followers_pins():
    assert tr.PINNED_BLOBS == ftf.PINNED_DECODER_BLOBS


def test_hour_before_the_floor_is_refused(tmp_path):
    f = tr.TipFiles(tmp_path)
    f.check_name("trades-2026-10-16T01.jsonl")
    for bad in ("trades-2026-10-16T00.jsonl", "creates-2026-10-15T23.jsonl", "migrations-2026-10-02T10.jsonl", "skipped-slots-2026-10-01T00.jsonl"):
        with pytest.raises(tr.TipRefused):
            f.check_name(bad)
        with pytest.raises(tr.TipRefused):
            f.open(bad)
    assert f.opened == 0


def test_reader_floor_is_start_minus_53_hours_and_never_below_the_seal_floor(tmp_path):
    start = int(datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc).timestamp()) * 1000
    assert tr.floor_hour(start) == "2026-10-30T00"  # 05:30 - 53 h = 10-30 00:30 -> hour 10-30T00
    assert tr.floor_hour(int(datetime(2026, 10, 17, 3, tzinfo=timezone.utc).timestamp()) * 1000) == tr.MIN_HOUR
    with pytest.raises(tr.TipRefused):
        reader(tmp_path, start_ms=start, catch_up_hours=54)
    r = reader(tmp_path, start_ms=start, catch_up_hours=53)
    assert tr.hour_key(r.h0) == "2026-10-30T00"  # exactly the floor hour
    with pytest.raises(tr.TipRefused):
        r.files.check_name("trades-2026-10-29T23.jsonl")
    early = int(datetime(2026, 10, 16, 3, tzinfo=timezone.utc).timestamp()) * 1000
    with pytest.raises(tr.TipRefused):
        reader(tmp_path, start_ms=early, catch_up_hours=5)  # first hour 10-15T22 is before the seal floor


def test_names_outside_the_five_file_kinds_are_refused(tmp_path):
    f = tr.TipFiles(tmp_path)
    for bad in ("../trades-2026-11-01T05.jsonl", "sub/trades-2026-11-01T05.jsonl", "trades-2026-11-01T05.jsonl.zst", "observe-2026-11-01.jsonl", "checkpoint.json",
                "status.json", "gaps.jsonl.bak", "trades-2026-11-01T05.json", "/etc/passwd"):
        with pytest.raises(tr.TipRefused):
            f.path(bad)
    assert f.path("gaps.jsonl").endswith("/gaps.jsonl")
    with pytest.raises(tr.TipRefused):
        tr.read_state_json(tmp_path, "gaps.jsonl")
    with pytest.raises(tr.TipRefused):
        tr.read_state_json(tmp_path, "../x.json")


def test_a_symlink_out_of_the_tip_dir_is_refused(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "trades-2026-11-01T05.jsonl").write_text("{}\n")
    tip = tmp_path / "tip"
    tip.mkdir()
    os.symlink(outside / "trades-2026-11-01T05.jsonl", tip / "trades-2026-11-01T05.jsonl")
    f = tr.TipFiles(tip)
    with pytest.raises(tr.TipRefused):
        f.path("trades-2026-11-01T05.jsonl")
    with pytest.raises(tr.TipRefused):
        f.open("trades-2026-11-01T05.jsonl")
    sub = tmp_path / "tip2"
    (sub / "x").mkdir(parents=True)
    os.symlink(sub / "x", sub / "link")
    with pytest.raises(tr.TipRefused):
        tr.TipFiles(sub).path("link/gaps.jsonl")


def test_the_archive_and_any_copy_of_it_are_refused(tmp_path):
    for d in (tr.ARCHIVE_DIR, tr.ARCHIVE_DIR + "/2026-10-20", str(tmp_path / "tip-tape-archive"), str(tmp_path / "x" / "tip-tape-archive" / "y")):
        with pytest.raises(tr.TipRefused):
            tr.TipFiles(d)
        with pytest.raises(tr.TipRefused):
            tr.TipReader(d, tmp_path, start_ms=B * 1000)
    link = tmp_path / "innocent"
    arch = tmp_path / "tip-tape-archive"
    arch.mkdir()
    os.symlink(arch, link)
    with pytest.raises(tr.TipRefused):
        tr.TipFiles(link)  # a symlink to a copy of the archive resolves to a path with the archive mark


def test_the_production_floor_cannot_be_lowered_on_the_sealed_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(os.path, "realpath", lambda p: os.fspath(p))  # no stat of the real sealed path: the check is on the string
    with pytest.raises(tr.TipRefused):
        tr.TipFiles(tr.DEFAULT_TIP_DIR, floor="2026-08-01T00")
    with pytest.raises(tr.TipRefused):
        tr.TipReader(tr.DEFAULT_TIP_DIR, tr.DEFAULT_STATE_DIR, start_ms=B * 1000, min_hour="2026-08-01T00", lookback_h=None)
    # a lower min_hour with the 53 h look-back still leaves the floor at start - 53 h, which is above the seal floor: nothing is lowered
    assert tr.TipReader(tr.DEFAULT_TIP_DIR + "/", tr.DEFAULT_STATE_DIR, start_ms=B * 1000, min_hour="2026-08-01T00").floor == "2026-10-30T01"
    tr.TipFiles(tr.DEFAULT_TIP_DIR, floor=tr.MIN_HOUR)  # the production floor is fine


def test_a_replay_harness_may_lower_the_floor_outside_the_sealed_dir(tmp_path):
    r = tr.TipReader(tmp_path / "t", tmp_path / "s", start_ms=int(datetime(2026, 8, 19, 12, tzinfo=timezone.utc).timestamp()) * 1000, catch_up_hours=130,
                     min_hour="2026-08-01T00", lookback_h=None)
    assert tr.hour_key(r.h0) == "2026-08-14T02"


def test_start_problem_only_checks_that_the_first_hour_exists(tmp_path):
    r = reader(tmp_path)
    assert r.start_problem() == "history_start_missing"
    put(tmp_path / "out", f"trades-{H05}.jsonl", [])
    assert r.start_problem() is None
    assert r.files.opened == 1


# ------------------------------------------------------------------------------------------------------------------------------------ status (2.1 item 5)
def good_status(now: int, **kw) -> dict:
    return {"ts_ms": now - 1000, "decoder_blobs": dict(tr.PINNED_BLOBS), "decoder_blobs_pinned": True, "trade_event_v": True, **kw}


def test_status_problem_fails_closed():
    now = 1_800_000_000_000
    assert tr.status_problem(good_status(now), now) is None
    assert tr.status_problem(good_status(now, ts_ms=now - 30_000), now) is None  # the bound is inclusive
    assert tr.status_problem(None, now) == "status_missing"
    assert tr.status_problem({}, now) == "status_ts_missing"
    assert tr.status_problem(good_status(now, ts_ms=now - 30_001), now) == "status_stale"
    assert tr.status_problem(good_status(now, ts_ms=now + 31_000), now) == "status_stale"
    assert tr.status_problem(good_status(now, ts_ms="x"), now) == "status_ts_missing"
    assert tr.status_problem(good_status(now, ts_ms=True), now) == "status_ts_missing"
    assert tr.status_problem(good_status(now, decoder_blobs={**tr.PINNED_BLOBS, "tools/pump_history_backfill.py": "0" * 40}), now) == "decoder_blobs_mismatch"
    assert tr.status_problem(good_status(now, decoder_blobs={"observe/trade_decode.py": tr.PINNED_BLOBS["observe/trade_decode.py"]}), now) == "decoder_blobs_mismatch"
    assert tr.status_problem(good_status(now, decoder_blobs=None), now) == "decoder_blobs_mismatch"
    assert tr.status_problem(good_status(now, decoder_blobs_pinned=False), now) == "decoder_blobs_not_pinned"
    assert tr.status_problem(good_status(now, decoder_blobs_pinned=None), now) == "decoder_blobs_not_pinned"
    assert tr.status_problem(good_status(now, trade_event_v=False), now) == "event_v_off"
    assert tr.status_problem(good_status(now, trade_event_v="true"), now) == "event_v_off"


def test_a_real_follower_status_is_healthy_only_with_event_v_and_the_pins(tmp_path):
    f = _follower(tmp_path, Rpc(tip=0), clock=SetClock(B * 1000), trade_event_v=True)
    st = f.status()
    assert tr.status_problem(st, st["ts_ms"]) in (None, "decoder_blobs_mismatch", "decoder_blobs_not_pinned")  # the checkout's blobs may differ from the pins
    assert tr.status_problem({**st, "decoder_blobs": dict(tr.PINNED_BLOBS), "decoder_blobs_pinned": True}, st["ts_ms"]) is None
    f2 = _follower(tmp_path / "b", Rpc(tip=0), clock=SetClock(B * 1000), trade_event_v=False)
    assert tr.status_problem({**f2.status(), "decoder_blobs": dict(tr.PINNED_BLOBS), "decoder_blobs_pinned": True}, B * 1000) == "event_v_off"


# ------------------------------------------------------------------------------------------------------------------------------------ P-C1 (b): the protocol
def drive(tmp_path: Path, *, mid_polls: bool = True, n: int = 14, status_d: bool = False) -> list[str]:
    """The REAL TipFollower across the hour mark B (slots 100..: the first 6 land in hour 05, the rest in hour 06). A reader polls after every commit and, with
    mid_polls, again after the trades file of each slot was appended but BEFORE the checkpoint moved. Returns the violations of the protocol."""
    clk = SetClock(B * 1000 - 3000)
    rpc = Rpc(tip=0, skipped={103})
    f = _follower(tmp_path, rpc, clock=clk)
    _seed(f, 99)
    rd = reader(tmp_path)
    bad: list[str] = []
    emitted: list[int] = []
    if status_d:
        f.heartbeat(force=True)  # status.json says last_done_slot = 99 and is never rewritten again (HEARTBEAT_S)
    state = {"D": 99}

    def poll(tag: str) -> None:
        items = rd.poll()
        emitted.extend(slots_of(items))
        gaps = [g for k, g in items if k == "gap"]
        if gaps:
            bad.append(f"{tag}: unexpected gap {gaps}")
        if emitted and max(emitted) > state["D"]:
            bad.append(f"{tag}: slot {max(emitted)} emitted beyond the checkpoint {state['D']}")

    orig_append = f.writer.append

    def append(kind, hour, rows):
        orig_append(kind, hour, rows)
        if mid_polls and kind == "trades" and rows:
            poll(f"mid-write of {rows[0]['slot']}")

    f.writer.append = append
    for i, slot in enumerate(range(100, 100 + n)):
        clk.ms = B * 1000 - 3000 + i * 500
        f.process_slot(slot)
        state["D"] = f.last_done
        poll(f"after commit {slot}")
        truth = sorted(r["slot"] for p in (tmp_path / "out").glob("trades-*.jsonl") for r in map(json.loads, p.read_text().splitlines()) if r["slot"] <= f.last_done)
        if sorted(emitted) != truth:
            bad.append(f"after commit {slot} (D={f.last_done}): emitted {sorted(set(truth) - set(emitted))} missing, extra {sorted(set(emitted) - set(truth))}")
        if rd.released_D != f.last_done or not rd.caught_up:
            bad.append(f"after commit {slot}: released_D {rd.released_D} caught_up {rd.caught_up}, checkpoint {f.last_done}")
    if emitted != sorted(emitted) or len(emitted) != len(set(emitted)):
        bad.append("rows emitted out of slot order or twice")
    hours = {p.name for p in (tmp_path / "out").glob("trades-*.jsonl")}
    if hours != {f"trades-{H05}.jsonl", f"trades-{H06}.jsonl"}:
        bad.append(f"the run did not cross the hour: {hours}")
    return bad


def test_every_row_of_slots_up_to_last_done_is_read_across_an_hour_rollover_at_the_mark(tmp_path):
    assert drive(tmp_path) == []


def test_the_protocol_holds_without_the_mid_write_polls_too(tmp_path):
    assert drive(tmp_path, mid_polls=False) == []


def test_mutation_hour_rollover_tail_off_is_caught(tmp_path, monkeypatch):
    monkeypatch.setattr(tr.TipReader, "_last_hour", lambda self, last_t_ms: self.h)  # never reads the next hour's file
    bad = drive(tmp_path)
    assert bad and any("missing" in b for b in bad)


def test_mutation_d_from_status_json_instead_of_checkpoint_is_caught(tmp_path, monkeypatch):
    def from_status(self):
        st = tr.read_state_json(self.state_dir, "status.json")
        return (int(st["last_done_slot"]), None) if st and isinstance(st.get("last_done_slot"), int) else None

    monkeypatch.setattr(tr.TipReader, "_read_checkpoint", from_status)
    bad = drive(tmp_path, status_d=True)
    assert bad and any("released_D" in b or "missing" in b for b in bad)


def test_mutation_releasing_rows_beyond_D_is_caught(tmp_path, monkeypatch):
    orig = tr.TipReader._merge
    monkeypatch.setattr(tr.TipReader, "_merge", lambda self, cur, D, budget, out: orig(self, cur, 10**18, budget, out))
    bad = drive(tmp_path)
    assert any("beyond the checkpoint" in b for b in bad)


def test_catch_up_over_many_hours_then_live_tail(tmp_path):
    out = tmp_path / "out"
    put(out, "trades-2026-11-01T03.jsonl", [trade(s) for s in (1, 2, 3)])
    put(out, "migrations-2026-11-01T03.jsonl", [{"type": "complete", "mint": "M", "slot": 2, "block_time": 5, "tx_index": 0, "event_index": 0, "pool": None, "quote_mint": None}])
    put(out, "creates-2026-11-01T04.jsonl", [{"type": "create", "mint": "M", "creator": "c", "slot": 4, "block_time": 6, "is_mayhem_mode": False}])
    put(out, "trades-2026-11-01T04.jsonl", [trade(4), trade(5)])
    put(out, "skipped-slots-2026-11-01T04.jsonl", [{"slot": 6, "kind": "skipped", "t_recv_ms": 1}])
    put(out, "trades-2026-11-01T05.jsonl", [trade(7)])
    write_state(tmp_path / "state", last_done=7, last_t_recv_ms=(B - 600) * 1000)
    r = reader(tmp_path, start_ms=(B - 3600 * 2 - 3600 + 600) * 1000, catch_up_hours=0)
    items = r.poll()
    assert [(k, x["slot"] if isinstance(x, dict) else x) for k, x in items] == [("trade", 1), ("migration", 2), ("trade", 2), ("trade", 3), ("create", 4), ("trade", 4),
                                                                           ("trade", 5), ("skip", 6), ("trade", 7)]
    assert r.caught_up and r.released_D == 7 and tr.hour_key(r.h) == H05
    assert r.poll() == []


def test_limit_stops_the_poll_and_released_D_stays_safe(tmp_path):
    out = tmp_path / "out"
    put(out, f"trades-{H05}.jsonl", [trade(s) for s in range(1, 21)])
    write_state(tmp_path / "state", last_done=20, last_t_recv_ms=(B - 100) * 1000)
    r = reader(tmp_path)
    got = r.poll(limit=7)
    assert slots_of(got) == [1, 2, 3, 4, 5, 6, 7] and not r.caught_up and r.released_D == 6
    got += r.poll(limit=100)
    assert slots_of(got) == list(range(1, 21)) and r.caught_up and r.released_D == 20


def test_rows_above_the_checkpoint_wait_for_a_later_one(tmp_path):
    out = tmp_path / "out"
    put(out, f"trades-{H05}.jsonl", [trade(s) for s in (1, 2, 3, 4)])
    write_state(tmp_path / "state", last_done=2, last_t_recv_ms=(B - 100) * 1000)
    r = reader(tmp_path)
    assert slots_of(r.poll()) == [1, 2] and r.caught_up and r.released_D == 2
    assert r.poll() == []
    write_state(tmp_path / "state", last_done=4, last_t_recv_ms=(B - 50) * 1000)
    assert slots_of(r.poll()) == [3, 4] and r.released_D == 4


def test_a_torn_last_line_is_not_consumed_until_its_newline(tmp_path):
    out = tmp_path / "out"
    full = json.dumps(trade(2), separators=(",", ":"))
    with open(out.parent / "x", "w"):
        pass
    put(out, f"trades-{H05}.jsonl", [trade(1)])
    with open(out / f"trades-{H05}.jsonl", "a") as fh:
        fh.write(full[:40])
    write_state(tmp_path / "state", last_done=2, last_t_recv_ms=(B - 100) * 1000)
    r = reader(tmp_path)
    assert slots_of(r.poll()) == [1]
    with open(out / f"trades-{H05}.jsonl", "a") as fh:
        fh.write(full[40:] + "\n")
    assert slots_of(r.poll()) == [2] and r.stats["bad_lines"] == 0


def test_a_file_cut_back_by_the_followers_recovery_never_leaves_a_stale_row(tmp_path):  # rows above D are re-read, not kept
    out = tmp_path / "out"
    put(out, f"trades-{H05}.jsonl", [trade(1), trade(2), trade(3, 0)])  # slot 3 is an in-flight write
    write_state(tmp_path / "state", last_done=2, last_t_recv_ms=(B - 100) * 1000)
    r = reader(tmp_path)
    assert slots_of(r.poll()) == [1, 2]
    put(out, f"trades-{H05}.jsonl", [trade(1), trade(2)])  # _recover truncated the file back to the end of slot 2
    put(out, f"trades-{H05}.jsonl", [trade(3, 7)], mode="a")  # the slot is fetched again
    write_state(tmp_path / "state", last_done=3, last_t_recv_ms=(B - 50) * 1000)
    got = r.poll()
    assert [x["signature"] for k, x in got if k == "trade"] == ["s3x7"]


def test_a_complete_line_that_is_not_json_is_a_tip_gap_not_a_silent_skip(tmp_path):
    out = tmp_path / "out"
    put(out, f"trades-{H05}.jsonl", [trade(1), "{not json", trade(2)])
    write_state(tmp_path / "state", last_done=2, last_t_recv_ms=(B - 100) * 1000)
    got = reader(tmp_path).poll()
    assert slots_of(got) == [1, 2]
    gaps = [g for k, g in got if k == "gap"]
    assert len(gaps) == 1 and gaps[0].reason == "tip_bad_line"


def test_checkpoint_problems_stop_the_poll_closed(tmp_path):
    put(tmp_path / "out", f"trades-{H05}.jsonl", [trade(1)])
    r = reader(tmp_path)
    assert r.poll() == [] and r.problem == "checkpoint_unreadable" and not r.caught_up
    write_state(tmp_path / "state", last_done=5, last_t_recv_ms=(B - 100) * 1000)
    assert slots_of(r.poll()) == [1] and r.problem is None
    (tmp_path / "state" / "checkpoint.json").write_text("{not json")
    assert r.poll() == [] and r.problem == "checkpoint_unreadable"  # recoverable: retried on the next poll
    write_state(tmp_path / "state", last_done=5, last_t_recv_ms=(B - 100) * 1000)
    assert r.poll() == [] and r.problem is None and r.caught_up
    write_state(tmp_path / "state", last_done=3, last_t_recv_ms=(B - 100) * 1000)
    assert r.poll() == [] and r.problem == "checkpoint_regressed" and not r.caught_up
    write_state(tmp_path / "state", last_done=9, last_t_recv_ms=(B - 50) * 1000)
    assert r.poll() == [] and r.problem == "checkpoint_regressed" and r.latched  # unsafe: stays latched for the run


def test_a_missing_middle_hour_is_a_gap_item(tmp_path):
    out = tmp_path / "out"
    put(out, "trades-2026-11-01T03.jsonl", [trade(1)])
    put(out, "trades-2026-11-01T05.jsonl", [trade(9)])  # hour 04 has no trades file
    write_state(tmp_path / "state", last_done=9, last_t_recv_ms=(B - 100) * 1000)
    r = reader(tmp_path, start_ms=(B - 3600 * 3 + 600) * 1000, catch_up_hours=0)
    got = r.poll()
    assert slots_of(got) == [1, 9]
    assert [g.reason for k, g in got if k == "gap"] == ["hour_file_missing"]


# ------------------------------------------------------------------------------------------------------------------------------------ gaps (each reason)
def test_gap_rows_of_the_real_follower_each_reason(tmp_path):
    rpc = Rpc(tip=1000, script={105: ["T"] * 30})
    f = _follower(tmp_path, rpc, clock=SetClock(B * 1000 - 5000), max_retries=2, backlog_slots=50)
    _seed(f, 99)
    f.step()  # tip 1000 is 901 slots ahead: backlog_jump 100..999, then slot 1000
    rpc2 = Rpc(tip=1010, script={1005: ["T"] * 30})
    f.fetch, f.get_tip = rpc2.fetch, rpc2.get_tip
    f.step()  # slot 1005 unfetchable
    write_gaps = (tmp_path / "out" / "gaps.jsonl").read_text().splitlines()
    assert len(write_gaps) == 2
    r = reader(tmp_path)
    got = r.poll()
    gaps = [g for k, g in got if k == "gap"]
    assert [(g.reason, g.lo, g.hi, g.slots) for g in gaps] == [("backlog_jump", 100, 999, 900), ("unfetchable", 1005, 1005, 1)]
    assert [g.code for g in gaps] == ["tip_gap_backlog_jump", "tip_gap_unfetchable"]
    assert r.caught_up and r.released_D == 1010


def test_contradicted_skip_and_unresolved_dropped_gap_rows(tmp_path):
    rpc = Rpc(tip=106, script={103: [-32007, -32007]})  # 103 looks skipped, but block 104's parentSlot is 103
    f = _follower(tmp_path, rpc, clock=SetClock(B * 1000 - 5000))
    _seed(f, 99)
    f.step()
    rows = [json.loads(x) for x in (tmp_path / "out" / "gaps.jsonl").read_text().splitlines()]
    assert [(x["slot"], x["reason"]) for x in rows] == [(103, "skip_contradicted")]
    put(tmp_path / "out", "gaps.jsonl", [{"slot": 105, "kind": "unresolved_dropped", "n": 2, "lookup_failed": False, "t_recv_ms": (B - 4) * 1000},
                                         {"kind": "gap", "reason": "weird", "t_recv_ms": (B - 3) * 1000}], mode="a")
    got = reader(tmp_path).poll()
    gaps = [g for k, g in got if k == "gap"]
    assert [(g.reason, g.lo, g.hi) for g in gaps[:2]] == [("skip_contradicted", 103, 103), ("unresolved_dropped", 105, 105)]
    assert gaps[2].reason == "unparsable"  # no slot at all: a gap AT the reader's position (fail closed)
    assert 103 in [x for k, x in got if k == "skip"]
    assert slots_of(got).count(103) == 1  # the refetched rows of the slot arrive once


def test_gap_rows_older_than_the_first_hour_read_are_counted_and_dropped(tmp_path):
    put(tmp_path / "out", "gaps.jsonl", [{"kind": "gap", "reason": "backlog_jump", "from_slot": 1, "to_slot": 9, "slots": 9, "t_recv_ms": (B - 7200) * 1000},
                                         {"kind": "gap", "reason": "backlog_jump", "from_slot": 20, "to_slot": 29, "slots": 10, "t_recv_ms": (B - 100) * 1000}])
    write_state(tmp_path / "state", last_done=30, last_t_recv_ms=(B - 50) * 1000)
    r = reader(tmp_path)
    got = r.poll()
    assert [(g.lo, g.hi) for k, g in got if k == "gap"] == [(20, 29)] and r.stats["gaps_before_window"] == 1


# ------------------------------------------------------------------------------------------------------------------------------------ reviewer fixes on #1155
def test_a_checkpoint_without_last_t_recv_ms_fails_closed(tmp_path):
    put(tmp_path / "out", f"trades-{H05}.jsonl", [trade(1)])
    put(tmp_path / "out", f"trades-{H06}.jsonl", [trade(2)])  # D's row is in the NEXT hour file
    write_state(tmp_path / "state", last_done=2, last_t_recv_ms=0)
    r = reader(tmp_path)
    assert r.poll() == [] and r.problem == "checkpoint_no_t" and not r.caught_up and r.released_D is None
    (tmp_path / "state" / "checkpoint.json").write_text(json.dumps({"last_done": 2}))
    assert r.poll() == [] and r.problem == "checkpoint_no_t"
    write_state(tmp_path / "state", last_done=2, last_t_recv_ms=(B + 1) * 1000)
    assert slots_of(r.poll()) == [1, 2] and r.caught_up and r.released_D == 2


def test_a_torn_tail_is_not_kept_across_polls(tmp_path):
    out = tmp_path / "out"
    full = json.dumps(trade(2), separators=(",", ":"))
    put(out, f"trades-{H05}.jsonl", [trade(1)])
    with open(out / f"trades-{H05}.jsonl", "a") as fh:
        fh.write(full[:40])  # torn
    write_state(tmp_path / "state", last_done=2, last_t_recv_ms=(B - 100) * 1000)
    r = reader(tmp_path)
    assert slots_of(r.poll()) == [1]
    # the follower cuts the file back to the end of slot 1 and writes slot 2 again, longer than the torn bytes we saw
    put(out, f"trades-{H05}.jsonl", [trade(1), {**trade(2), "signature": "s2-rewritten-" + "x" * 80}])
    got = r.poll()
    assert [x["signature"] for k, x in got if k == "trade"] == ["s2-rewritten-" + "x" * 80] and r.stats["bad_lines"] == 0


def test_fadvise_stops_when_catch_up_is_over(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(os, "posix_fadvise", lambda *a: calls.append(a))
    put(tmp_path / "out", f"trades-{H05}.jsonl", [trade(s) for s in (1, 2)])
    write_state(tmp_path / "state", last_done=2, last_t_recv_ms=(B - 100) * 1000)
    r = reader(tmp_path)
    r.poll()
    assert calls
    n = len(calls)
    r.stop_fadvise()
    put(tmp_path / "out", f"trades-{H05}.jsonl", [trade(3)], mode="a")
    write_state(tmp_path / "state", last_done=3, last_t_recv_ms=(B - 50) * 1000)
    assert slots_of(r.poll()) == [3] and len(calls) == n


def test_qp_r3_three_hours_of_files_and_no_last_t_never_report_caught_up(tmp_path):
    out = tmp_path / "out"
    for h, sl in (("2026-11-01T03", (1, 2, 3)), ("2026-11-01T04", (4, 5)), ("2026-11-01T05", (6, 7))):
        put(out, f"trades-{h}.jsonl", [trade(x) for x in sl])
    for t in (0, None):
        write_state(tmp_path / "state", last_done=7, last_t_recv_ms=t)
        r = reader(tmp_path, start_ms=(B - 3600 * 3 + 600) * 1000, catch_up_hours=0)
        assert r.poll() == [] and r.problem == "checkpoint_no_t" and not r.caught_up and r.released_D is None and r.hw is None
