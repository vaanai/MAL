"""Tests for tools/arena_tip_gapfill.py. Synthetic fixtures only: a scripted fake RPC (no network), the REAL TipFollower and decoder, tmp dirs. No sealed data is opened."""

from __future__ import annotations

import json
import os
import stat
from collections import Counter
from pathlib import Path

import pytest

from tools import arena_tip_adapter as ata
from tools import arena_tip_gapfill as gf
from tools import arena_tip_reader as tr
from tools import fast_tip_follower as ftf
from tools.arena_paper import ArenaEngine
from tools.test_arena_tip_adapter import T0, wt
from tools.test_fast_tip_follower import Rpc

GAP_T = T0 + 105 * 400 - 50


def gap_row(lo: int, hi: int, reason: str = "backlog_jump", t: int = GAP_T, **kw) -> dict:
    return {"kind": "gap", "reason": reason, "from_slot": lo, "to_slot": hi, "slots": hi - lo + 1, "t_recv_ms": t, **kw}


@pytest.fixture(autouse=True)
def _unlock(tmp_path):
    yield
    for p in [tmp_path, *tmp_path.rglob("*")]:  # sealed segments are read-only: let pytest clean up
        try:
            os.chmod(p, 0o755 if p.is_dir() else 0o644)
        except OSError:
            pass


def run_fill(tmp_path, gaps, rpc=None, **kw):
    rpc = rpc or Rpc(1000)
    man = gf.fill(gaps, rpc.fetch, tmp_path / "seg", **kw)
    return man, rpc


def seg_trades(seg: Path) -> list[dict]:
    out = []
    for p in sorted((seg / "rows").glob("trades-*.jsonl")):
        out += [json.loads(x) for x in p.read_text().splitlines()]
    return out


# ------------------------------------------------------------------------------------------------------------------------------------ fill
def test_a_gap_range_is_fetched_slot_by_slot_and_decoded_with_tx_index_and_block_time(tmp_path):
    man, rpc = run_fill(tmp_path, [gap_row(100, 104)])
    assert rpc.calls == [100, 101, 102, 103, 104]
    rows = seg_trades(tmp_path / "seg")
    assert [r["slot"] for r in rows] == [100, 101, 102, 103, 104]
    assert all(isinstance(r["tx_index"], int) and isinstance(r["block_time"], int) for r in rows)
    assert {r["t_recv_ms"] for r in rows} == {GAP_T} and {r["source"] for r in rows} == {"tip"}  # the gap's own follower time: deterministic
    (rng,) = man["ranges"]
    assert rng["filled"] and rng["fetched"] == 5 and rng["failed_slots"] == [] and man["complete"] is True
    assert man["decoder_blobs"] == ftf.PINNED_DECODER_BLOBS


def test_the_segment_is_sealed_and_a_second_run_refuses_to_overwrite_it(tmp_path):
    run_fill(tmp_path, [gap_row(100, 101)])
    seg = tmp_path / "seg"
    assert all(not (p.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)) for p in [seg, seg / "manifest.json", *(seg / "rows").iterdir()])
    assert not (seg / "_work").exists()
    with pytest.raises(gf.SegmentRefused):
        gf.fill([gap_row(100, 101)], Rpc(1000).fetch, seg)


def test_the_repair_rows_equal_the_rows_the_follower_writes_for_the_same_slot(tmp_path):
    """The proof the F2 text asks for, on synthetic blocks: a real follower run over the slots and the fill agree row for row (t_recv_ms aside)."""
    run_fill(tmp_path, [gap_row(100, 103)])
    f = ftf.TipFollower(out_dir=tmp_path / "ref" / "out", state_dir=tmp_path / "ref" / "state", fetch=Rpc(1000).fetch, get_tip=lambda: 0, v_lookup=lambda p: {},
                        clock_ms=lambda: GAP_T, sleep=lambda s: None, log=lambda m: None, trade_event_v=True)
    for s in range(100, 104):
        f.process_slot(s)
    ref = [json.loads(x) for p in sorted((tmp_path / "ref" / "out").glob("trades-*.jsonl")) for x in p.read_text().splitlines()]
    assert ref == seg_trades(tmp_path / "seg") and len(ref) == 4


def test_a_skipped_slot_is_listed_not_a_hole(tmp_path):
    man, _ = run_fill(tmp_path, [gap_row(100, 104)], Rpc(1000, skipped={102}))
    (rng,) = man["ranges"]
    assert rng["skipped_slots"] == [102] and rng["fetched"] == 4 and rng["filled"] is True
    assert 102 not in {r["slot"] for r in seg_trades(tmp_path / "seg")}


def test_overlapping_ranges_fetch_each_slot_once(tmp_path):
    man, rpc = run_fill(tmp_path, [gap_row(100, 104), gap_row(103, 106, "unfetchable")])
    assert sorted(rpc.calls) == list(range(100, 107)) and len(rpc.calls) == 7
    assert [r["filled"] for r in man["ranges"]] == [True, True]


# ------------------------------------------------------------------------------------------------------------------------------------ the rule and the bound
def test_a_range_over_the_bound_a_foreign_reason_or_no_time_stays_a_gap(tmp_path):
    gaps = [gap_row(100, 104), gap_row(200, 300, "backlog_jump"), {"kind": "gap", "reason": "tip_bad_line", "slot": 5, "t_recv_ms": GAP_T},
            gap_row(400, 401, t=None), {"kind": "gap", "reason": "backlog_jump", "t_recv_ms": GAP_T}]
    gaps[3].pop("t_recv_ms")
    man, rpc = run_fill(tmp_path, gaps, max_missed=50)
    by = {r["gid"]: r for r in man["ranges"]}
    assert by["backlog_jump:100-104"]["filled"] is True
    assert by["backlog_jump:200-300"]["refused"] == "over_bound" and not by["backlog_jump:200-300"]["filled"]
    assert by["tip_bad_line:5-5"]["refused"] == "reason_not_fillable"
    assert by["backlog_jump:400-401"]["refused"] == "no_follower_time"
    assert any(r.get("refused") == "no_slot_range" for r in man["ranges"])
    assert rpc.calls == [100, 101, 102, 103, 104]  # nothing outside the fillable range was fetched
    assert gf.completeness(man, gaps, max_missed=50) == []


def test_the_rule_ignores_everything_but_the_gap_row_outcome_blind(tmp_path):
    a = gf.make_plan([gap_row(100, 104)])
    b = gf.make_plan([{**gap_row(100, 104), "price": 9, "pnl": -1, "mint": "x"}])
    assert [g.gid for g in a.fill] == [g.gid for g in b.fill] and a.slots == b.slots


# ------------------------------------------------------------------------------------------------------------------------------------ completeness
def test_a_slot_that_cannot_be_fetched_leaves_the_range_a_gap(tmp_path):
    man, _ = run_fill(tmp_path, [gap_row(100, 104)], Rpc(1000, script={102: ["T"] * 20}))
    (rng,) = man["ranges"]
    assert rng["filled"] is False and rng["failed_slots"] == [102] and man["complete"] is False
    assert gf.completeness(man, [gap_row(100, 104)]) == []  # a failed range is consistent: it is reported unfilled, not filled
    seg = gf.load_segment(tmp_path / "seg")
    assert seg.filled() == {}  # nothing is spliced in for it


def test_completeness_check_catches_a_manifest_that_claims_more_than_it_fetched(tmp_path):
    man, _ = run_fill(tmp_path, [gap_row(100, 104)])
    bad = json.loads(json.dumps(man))
    bad["ranges"][0]["fetched"] = 4  # one slot unaccounted for
    assert any("slots accounted" in p for p in gf.completeness(bad, [gap_row(100, 104)]))
    assert any("not in the manifest" in p for p in gf.completeness(man, [gap_row(100, 104), gap_row(500, 502)]))
    worse = json.loads(json.dumps(man))
    worse["ranges"][0]["failed_slots"] = [101]
    worse["ranges"][0]["fetched"] = 4
    assert any("filled with failed slots" in p for p in gf.completeness(worse, [gap_row(100, 104)]))


# ------------------------------------------------------------------------------------------------------------------------------------ pins, digests
def test_a_pin_mismatch_refuses_before_any_fetch(tmp_path):
    rpc = Rpc(1000)
    wrong = dict(ftf.PINNED_DECODER_BLOBS, **{"observe/trade_decode.py": "0" * 40})
    with pytest.raises(gf.PinMismatch):
        gf.fill([gap_row(100, 104)], rpc.fetch, tmp_path / "seg", blobs_fn=lambda: wrong)
    assert rpc.calls == [] and not (tmp_path / "seg").exists()


def test_a_missing_decoder_blob_is_a_mismatch_too(tmp_path):
    with pytest.raises(gf.PinMismatch):
        gf.check_pins(lambda: {"observe/trade_decode.py": None})


def test_a_segment_made_by_another_decoder_is_refused_on_load(tmp_path):
    run_fill(tmp_path, [gap_row(100, 101)])
    other = dict(ftf.PINNED_DECODER_BLOBS, **{"tools/pump_history_backfill.py": "f" * 40})
    with pytest.raises(gf.PinMismatch):  # this process runs another decoder
        gf.load_segment(tmp_path / "seg", blobs_fn=lambda: other)
    mp = tmp_path / "seg" / "manifest.json"
    os.chmod(tmp_path / "seg", 0o755), os.chmod(mp, 0o644)
    man = json.loads(mp.read_text())
    man["decoder_blobs"] = other
    mp.write_text(json.dumps(man))
    with pytest.raises(gf.PinMismatch):  # the segment was made by another decoder
        gf.load_segment(tmp_path / "seg")


def test_a_tampered_row_file_is_refused_on_load(tmp_path):
    run_fill(tmp_path, [gap_row(100, 101)])
    seg = tmp_path / "seg"
    p = next((seg / "rows").glob("trades-*.jsonl"))
    os.chmod(p.parent, 0o755), os.chmod(p, 0o644)
    p.write_text(p.read_text() + "\n")
    with pytest.raises(gf.SegmentRefused):
        gf.load_segment(seg)


def test_the_cli_pin_check_exits_zero_without_any_rpc(capsys):
    assert gf.main(["--gaps", "x", "--segment-dir", "y", "--public-rpc", "--check-pins"]) == 0
    assert json.loads(capsys.readouterr().out)["pinned"] is True


# ------------------------------------------------------------------------------------------------------------------------------------ dedupe, splice
def adapter_stream(gap=(100, 104), *, before=(10, 11), after=(105, 106)):
    g = tr.TipGap(gap[0], gap[1], "backlog_jump", GAP_T, gap[1] - gap[0] + 1)
    items = [("trade", wt(s)) for s in before] + [("gap", g)] + [("trade", wt(s, tx=1)) for s in after]
    return ata.ArenaTipAdapter().feed(items)


def test_dedupe_drops_a_repair_row_the_follower_already_carries():
    fill_rows = [{"type": "trade", "slot": 100, "signature": "a", "event_index": 0}, {"type": "trade", "slot": 101, "signature": "b", "event_index": 0}]
    later = [{"type": "trade", "slot": 101, "signature": "b", "event_index": 0, "t_recv_ms": 5}]
    kept, dropped = gf.dedupe(fill_rows, later)
    assert [r["slot"] for r in kept] == [100] and dropped == 1


def test_splice_replaces_a_filled_gap_with_repair_rows_and_a_filled_marker(tmp_path):
    run_fill(tmp_path, [gap_row(100, 104)])
    seg = gf.load_segment(tmp_path / "seg")
    stream = adapter_stream()
    assert [r["type"] for r in stream] == ["trade", "trade", "gap", "trade", "trade"] and stream[2]["data_loss"] is True
    c = Counter()
    out = list(gf.splice(stream, seg, c))
    assert [r["type"] for r in out] == ["trade", "trade"] + ["trade"] * 5 + ["gap", "trade", "trade"]
    fills = [r for r in out if r.get("fill")]
    assert [r["slot"] for r in fills] == [100, 101, 102, 103, 104] and all(r["src"] == "tip_fill" for r in fills)
    marker = out[7]
    assert marker["filled"] is True and marker["data_loss"] is False and marker["from_slot"] == 100 and marker["to_slot"] == 104 and marker["kind"] == "tip_gap_backlog_jump_filled"
    assert c["filled_ranges"] == 1 and c["filled_slots"] == 5 and c["fill_rows"] == 5
    assert [r["slot"] for r in out if r["type"] == "trade"] == sorted(r["slot"] for r in out if r["type"] == "trade")  # slot order holds


def test_splice_passes_an_unfilled_gap_through_unchanged(tmp_path):
    run_fill(tmp_path, [gap_row(100, 104)], Rpc(1000, script={101: ["T"] * 20}))
    seg = gf.load_segment(tmp_path / "seg")
    stream = adapter_stream()
    c = Counter()
    out = list(gf.splice(stream, seg, c))
    assert out == stream and c["gaps_not_filled"] == 1 and c["filled_ranges"] == 0


def test_splice_drops_a_later_follower_row_that_repeats_a_repair_row(tmp_path):
    run_fill(tmp_path, [gap_row(100, 104)])
    seg = gf.load_segment(tmp_path / "seg")
    stream = adapter_stream()
    fr = gf.arena_rows(seg, 100, 104)
    dup = dict(fr[2])
    dup.pop("fill"), dup.pop("src")
    stream.insert(3, dup)  # the follower later carried slot 102's print itself
    c = Counter()
    out = list(gf.splice(stream, seg, c))
    assert c["follower_dup_dropped"] == 1 and sum(1 for r in out if r["type"] == "trade" and r["slot"] == 102) == 1


# ------------------------------------------------------------------------------------------------------------------------------------ order key
def test_repair_rows_carry_tx_index_so_the_engine_uses_the_tx_index_order_key_form(tmp_path):
    run_fill(tmp_path, [gap_row(100, 104)])
    seg = gf.load_segment(tmp_path / "seg")
    rows = [r for r in gf.arena_rows(seg, 100, 104) if r["type"] == "trade"]
    assert rows and all(r["tx_index"] is not None for r in rows)
    assert all(ArenaEngine.order_key(r, r["slot"], r["t_recv_ms"]) == (r["slot"], 0, r["tx_index"], r["event_index"], r["signature"]) for r in rows)


def test_a_repair_row_without_tx_index_is_marked_by_the_adapter_not_mixed_silently():
    """A PumpSwap print that came back through resolve_unresolved has no tx_index. Mixed with numbered rows of the same (pool, slot) the adapter writes a data_loss gap
    (fail closed); a repair row never gets a made-up tx_index."""
    a, b = wt(100, 0, venue="pumpswap", tx=0, pool="P0"), wt(100, 1, venue="pumpswap", pool="P0")
    b.pop("tx_index")
    seg = gf.Segment({"ranges": []}, [("trade", a), ("trade", b)])
    rows = gf.arena_rows(seg, 100, 100)
    kinds = [(r["type"], r.get("kind")) for r in rows]
    assert ("gap", "tip_gap_mixed_tx_index") in kinds
    assert [r.get("tx_index") for r in rows if r["type"] == "trade"] == [0, None]
    assert ArenaEngine.order_key([r for r in rows if r["type"] == "trade"][1], 100, T0)[1] == 1  # the receive-order form, as for any live row without the key
