"""The executor's own exit-gap writer (tools/h5_arena_gates.ExitGapWriter, wired in tools/h5_arena_executor.py). Fixtures only: no RPC, no key, nothing sent."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from unittest import mock

import pytest

from tools import h5_arena_executor as ax
from tools import h5_arena_gates as g
from tools.test_h5_arena_exec_wiring import LiveCase


NOW = 10_000_000


def trip(tid, sell=0.9, **kw):
    t = {"trip_id": tid, "exit_landed": True, "gap": False, "live_sell_price": sell, "exit_landed_ms": NOW - 1_000}
    t.update(kw)
    return t


def twins(path: Path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return str(path)


def twin(tid, exit_price=1.0, **kw):
    r = {"trip_id": tid, "pnl_lamports": 5, "exit_price": exit_price}
    r.update(kw)
    return r


def test_gap_sign_and_refusals():
    assert g.exit_gap_bps_worse(0.98, 1.0) == pytest.approx(200.0)  # live received 2% less: +200 bps worse
    assert g.exit_gap_bps_worse(1.01, 1.0) == pytest.approx(-100.0)
    for a, b in ((0, 1.0), (1.0, 0), (-1, 1.0), (None, 1.0), (1.0, "1"), (True, 1.0), (math.nan, 1.0), (1.0, math.inf)):
        assert g.exit_gap_bps_worse(a, b) is None


def test_load_twin_rows_keeps_priced_rows_only(tmp_path):
    f = twins(tmp_path / "t.jsonl", [twin("a"), twin("b", price_failed=True), {"trip_id": "c", "pnl_lamports": None}, twin("d", pnl_lamports=1.5)])
    assert set(g.load_twin_rows(f)) == {"a", "d"}
    (tmp_path / "bad.jsonl").write_text("{x\n")
    assert g.load_twin_rows(str(tmp_path / "bad.jsonl")) is None
    assert g.load_twin_rows(str(tmp_path / "missing.jsonl")) is None


def test_writer_writes_eligible_trips_once(tmp_path):
    out = tmp_path / "p" / "exit-gaps.jsonl"
    w = g.ExitGapWriter(out)
    assert out.exists() and out.read_text() == ""
    tw = twins(tmp_path / "t.jsonl", [twin("a"), twin("g"), twin("u"), twin("o")])
    trips = {"a": trip("a", 0.97), "g": trip("g", gap=True), "u": trip("u", unreadable=True), "o": trip("o", exit_landed=False)}
    assert w.write(trips, tw, NOW) == (1, [])
    rows = [json.loads(x) for x in out.read_text().splitlines()]
    assert rows == [{"trip_id": "a", "mint": "a", "exit_gap_bps": 300.0}]
    assert w.write(trips, tw, NOW) == (0, [])  # once per trip
    w2 = g.ExitGapWriter(out)  # a restart reads the ids back
    assert w2.write(trips, tw, NOW) == (0, [])
    out.write_text(out.read_text() + "not json\n" + '{"mint": "x", "exit_gap_bps": 1}\n')
    assert "a" in g.ExitGapWriter(out).written  # foreign rows are the reader's to latch on, not the writer's


@pytest.mark.parametrize("sell", [0, -1.0, math.nan, math.inf, None, "0.9", True])
def test_a_bad_live_sell_price_against_a_priced_twin_is_a_problem_not_a_skip(tmp_path, sell):
    w = g.ExitGapWriter(tmp_path / "e.jsonl")
    tw = twins(tmp_path / "t.jsonl", [twin("a")])
    assert w.write({"a": trip("a", sell)}, tw, NOW) == (0, [("exit_gap_live_price_bad", "a")])


@pytest.mark.parametrize("rows", [[], [twin("a", price_failed=True)], [twin("a", exit_price=0)], [twin("a", exit_price=None)], [{"trip_id": "a", "pnl_lamports": None, "exit_price": 1.0}]])
def test_a_missing_or_unpriced_twin_is_signalled_after_the_grace(tmp_path, rows):
    w = g.ExitGapWriter(tmp_path / "e.jsonl")
    tw = twins(tmp_path / "t.jsonl", rows)
    assert w.write({"a": trip("a")}, tw, NOW) == (0, [])  # inside the grace: the twin writer runs every 20 s
    late = NOW - 1_000 + g.EXIT_GAP_TWIN_GRACE_MS
    assert w.write({"a": trip("a")}, tw, late) == (0, [("exit_gap_no_priced_twin", "a")])
    assert w.write({"a": trip("a", exit_landed_ms=None)}, tw, NOW) == (0, [("exit_gap_no_priced_twin", "a")])  # no landing stamp: no grace


def test_an_unreadable_twins_file_is_signalled_after_the_grace(tmp_path):
    w = g.ExitGapWriter(tmp_path / "e.jsonl")
    missing = str(tmp_path / "missing.jsonl")
    assert w.write({"a": trip("a")}, missing, NOW) == (0, [])
    assert w.write({"a": trip("a")}, missing, NOW + g.EXIT_GAP_TWIN_GRACE_MS) == (0, [("exit_gap_no_priced_twin", "a")])


def test_writer_failures_raise(tmp_path):
    d = tmp_path / "ro"
    d.mkdir()
    out = d / "exit-gaps.jsonl"
    w = g.ExitGapWriter(out)
    os.chmod(out, 0o444)
    tw = twins(tmp_path / "t.jsonl", [twin("a")])
    if os.geteuid() != 0:
        with pytest.raises(OSError):
            w.write({"a": trip("a")}, tw, NOW)


class ExecutorWiringTests(LiveCase):
    def test_live_start_requires_the_own_writer_path(self):
        self.refused("exit_gap_file_not_own_writer", exit_gap_file=str(self.tmp / "elsewhere.jsonl"))
        e = self.live()
        self.assertEqual(e.ex.exit_gap_writer.path, Path(e.conf["exit_gap_file"]))
        self.assertTrue(e.conf["exit_gap_file"].endswith("arena-parity/exit-gaps.jsonl"))

    def test_ten_closed_trips_worse_than_200_bps_latch_the_executor_halt(self):
        e = self.live()
        rec = e.ex.gates.recorder
        rows = []
        for i in range(ax.EXIT_GAP_MIN_N):
            rec.trips[f"m{i}"] = trip(f"m{i}", 0.975)  # 250 bps worse than the twin
            rows.append(twin(f"m{i}"))
        twins(Path(e.conf["parity_twins_file"]), rows)
        e.ex.write_exit_gaps()
        e.ex.exit_gap_tick()
        self.assertEqual(len(e.ex.monitor.exit_gaps), ax.EXIT_GAP_MIN_N)
        self.assertIn(ax.H_EXIT_GAP, e.ex.counters.halts)

    def test_better_exits_do_not_latch(self):
        e = self.live()
        rec = e.ex.gates.recorder
        rows = []
        for i in range(ax.EXIT_GAP_MIN_N):
            rec.trips[f"m{i}"] = trip(f"m{i}", 1.0)
            rows.append(twin(f"m{i}"))
        twins(Path(e.conf["parity_twins_file"]), rows)
        e.ex.write_exit_gaps()
        e.ex.exit_gap_tick()
        self.assertEqual(len(e.ex.monitor.exit_gaps), ax.EXIT_GAP_MIN_N)
        self.assertNotIn(ax.H_EXIT_GAP, e.ex.counters.halts)

    def test_a_writer_failure_latches(self):
        e = self.live()
        with mock.patch.object(g.ExitGapWriter, "write", side_effect=OSError("disk")):
            e.ex.write_exit_gaps()
        self.assertEqual(e.ex.counters.halts[ax.H_EXIT_GAP]["why"], "exit_gap_writer_failed")

    def test_a_bad_live_price_latches_in_the_executor(self):
        e = self.live()
        e.ex.gates.recorder.trips["m0"] = trip("m0", 0, exit_landed_ms=e.ex.now_ms())
        twins(Path(e.conf["parity_twins_file"]), [twin("m0")])
        e.ex.write_exit_gaps()
        h = e.ex.counters.halts[ax.H_EXIT_GAP]
        self.assertEqual((h["why"], h["trip_id"]), ("exit_gap_live_price_bad", "m0"))

    def test_a_missing_twin_latches_after_the_grace(self):
        e = self.live()
        e.ex.gates.recorder.trips["m0"] = trip("m0", exit_landed_ms=e.ex.now_ms())
        e.ex.write_exit_gaps()
        self.assertNotIn(ax.H_EXIT_GAP, e.ex.counters.halts)
        e.ex.gates.recorder.trips["m0"]["exit_landed_ms"] = e.ex.now_ms() - g.EXIT_GAP_TWIN_GRACE_MS
        e.ex.write_exit_gaps()
        self.assertEqual(e.ex.counters.halts[ax.H_EXIT_GAP]["why"], "exit_gap_no_priced_twin")

    def test_the_writer_is_in_the_c0_tripwire(self):
        self.assertIn("write_exit_gaps", ax.GUARD_METHODS)
        self.assertIsNone(ax.c0_guard_problem())

    def test_dry_run_has_no_writer(self):
        e = self.env()
        self.assertIsNone(e.ex.exit_gap_writer)
        e.ex.write_exit_gaps()
