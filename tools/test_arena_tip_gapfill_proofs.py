"""Tests for tools/arena_tip_gapfill_proofs.py (P1 to P3). Synthetic rows and blocks only: no explore-0814 data, no network, no sealed data."""

from __future__ import annotations

import collections
import json
from pathlib import Path

import pytest

from tools import arena_paper
from tools import arena_tip_gapfill_proofs as pr
from tools.pump_history_backfill import rows_from_block
from tools.test_arena_paper import _row
from tools.test_fast_tip_follower import Rpc, _block


@pytest.fixture(autouse=True)
def _plenty_of_disk(monkeypatch):
    monkeypatch.setattr("tools.arena_paper.shutil.disk_usage", lambda path: collections.namedtuple("Usage", "total used free")(1000, 500, 500))


def stream(pools: int = 10, lo: int = 90, hi: int = 420, stagger: int = 30) -> list[dict]:
    """`pools` PumpSwap pools, pool k printing every slot from lo + stagger * k to hi: the pinned book buys each at its first print and sells 40 slots later."""
    rows = []
    for k in range(pools):
        for i, s in enumerate(range(lo + stagger * k, hi)):
            rows.append(_row(i, s, pool=f"P{k}", sig=f"sigP{k}x{i}"))
    return sorted(rows, key=lambda r: (r["slot"], r["pool"]))


def current_source() -> str:
    return Path(arena_paper.__file__).read_text()


# ------------------------------------------------------------------------------------------------------------------------------------ P1
def test_p1_the_same_engine_gives_the_same_ledger_in_order_and_as_a_set(tmp_path):
    old = pr.load_engine_source(current_source(), "tools.arena_paper_old_t1")
    res = pr.p1(stream(), old, arena_paper, tmp_path, every=1)
    assert res["pass"] is True and res["old"] == res["new"] and res["old"]["rows"] > 0 and res["book_old"]["buys"] >= 5
    assert set(res["old"]) == {"rows", "md5_in_order", "md5_as_set"}  # md5s and counts only


def test_p1_detects_an_engine_that_prices_differently(tmp_path):
    src = current_source()
    patched = src.replace("return int(F.k_slots(lat, sps, self.cfg.k_rounding))", "return int(F.k_slots(lat, sps, self.cfg.k_rounding)) + 1")
    assert patched != src
    res = pr.p1(stream(), pr.load_engine_source(patched, "tools.arena_paper_old_t2"), arena_paper, tmp_path, every=1)
    assert res["pass"] is False and res["old"]["md5_in_order"] != res["new"]["md5_in_order"]


def test_p1_refuses_below_the_min_rows_floor(tmp_path):
    res = pr.p1(stream(pools=1), arena_paper, arena_paper, tmp_path, min_rows=10**9)
    assert res["pass"] is False and "min-rows" in res["refused"]


def test_the_cli_refuses_a_sealed_directory(tmp_path):
    with pytest.raises(SystemExit):
        pr.read_rows("/var/lib/mal/sealed/fast-trades-tip")


def test_read_rows_reads_jsonl_in_file_order(tmp_path):
    (tmp_path / "b.jsonl").write_text(json.dumps({"slot": 2}) + "\n")
    (tmp_path / "a.jsonl").write_text(json.dumps({"slot": 1}) + "\n\n")
    assert [r["slot"] for r in pr.read_rows(tmp_path)] == [1, 2]


# ------------------------------------------------------------------------------------------------------------------------------------ P2
def test_p2_compare_passes_on_equal_rows_and_names_a_differing_slot_by_count_only():
    rows = [{"slot": s, "signature": f"s{s}", "event_index": 0, "tx_index": 1, "sol_lamports": 5, "t_recv_ms": s * 7, "source": src} for s, src in ((10, "tip"), (11, "tip"))]
    walker = [dict(r, t_recv_ms=None, source="backfill") for r in rows]
    ok = pr.p2_compare(rows, rows, walker, 10, 11)
    assert ok["pass"] is True and ok["mismatched_slots"] == 0 and ok["slots_with_rows"] == 2
    bad = pr.p2_compare(rows, rows, [walker[0], dict(walker[1], sol_lamports=6)], 10, 11)
    assert bad["pass"] is False and bad["mismatched_slots"] == 1 and not any(isinstance(v, list) for v in bad.values())  # counts and digests, no slot list


def test_p2_compare_fails_when_tx_index_presence_differs():
    a = [{"slot": 10, "signature": "s", "event_index": 0, "tx_index": 3}]
    b = [{"slot": 10, "signature": "s", "event_index": 0}]
    res = pr.p2_compare(a, a, b, 10, 10)
    assert res["pass"] is False and res["rows_no_tx_index"] == {"fill": 0, "warm": 0, "walker": 1}


def test_p2_run_cold_fill_equals_the_warm_follower_and_the_walker_rows(tmp_path):
    rpc = Rpc(1000)
    walker = []
    for s in range(100, 104):
        walker.extend(rows_from_block(_block(s), {}, "helius_getblock_tip", event_v=True)["trades"])
    res = pr.p2_run(100, 103, 3, rpc.fetch, walker, tmp_path, min_slots=4)
    assert res["fill_complete"] is True and res["slots_with_rows"] == 4
    assert res["pass"] is True and res["mismatched_slots"] == 0, res


# ------------------------------------------------------------------------------------------------------------------------------------ P3
def test_p3_end_to_end_the_unaffected_positions_keep_their_rows_and_the_changed_ones_are_counted(tmp_path):
    res = pr.p3(stream(), arena_paper, tmp_path, ["backlog_jump", "unresolved_dropped"], every=1)
    assert res["pass"] is True, res
    assert res["splice"]["filled_ranges"] == 2 and res["rows_dup"] == 0 and res["mixed_groups"] == 0 and res["gaps_after_splice"] == 0 and res["fills_seen"] == 2
    assert res["unaffected_mismatched"] == 0 and res["positions_unbroken"] >= 5
    assert res["changed_by_design"], "the backlog_jump margin must change at least one position"
    assert isinstance(res["late_print_count"], int)
    assert res["splice"]["fill_rows_dup_dropped"] > 0  # the unresolved_dropped slot has follower rows on both sides: its repair rows are twins


def test_p3_every_fillable_reason_at_once_still_has_no_duplicate_and_no_mixed_group(tmp_path):
    res = pr.p3(stream(pools=14, hi=520), arena_paper, tmp_path, ["backlog_jump", "unfetchable", "skip_contradicted", "skip_contradicted_refetch", "unresolved_dropped"], every=1)
    assert res["pass"] is True, res
    assert res["splice"]["filled_ranges"] == 5 and res["rows_dup"] == 0 and res["mixed_groups"] == 0


def test_p3_refuses_below_the_min_rows_floor(tmp_path):
    res = pr.p3(stream(pools=1), arena_paper, tmp_path, ["backlog_jump"], min_rows=10**9)
    assert res["pass"] is False and "min-rows" in res["refused"]
