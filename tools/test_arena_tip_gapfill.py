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


def test_splice_replaces_a_filled_gap_with_a_filled_marker_then_the_repair_rows(tmp_path):
    run_fill(tmp_path, [gap_row(100, 104)])
    seg = gf.load_segment(tmp_path / "seg")
    stream = adapter_stream()
    assert [r["type"] for r in stream] == ["trade", "trade", "gap", "trade", "trade"] and stream[2]["data_loss"] is True
    c = Counter()
    out = list(gf.splice(stream, seg, c))
    assert [r["type"] for r in out] == ["trade", "trade", "gap"] + ["trade"] * 5 + ["trade", "trade"]  # QP C1: the marker BEFORE the repair rows
    marker = out[2]
    assert marker["filled"] is True and marker["data_loss"] is False and marker["from_slot"] == 100 and marker["to_slot"] == 104 and marker["kind"] == "tip_gap_backlog_jump_filled"
    assert marker["back_slots"] == gf.BACKLOG_SLOTS
    fills = [r for r in out if r.get("fill")]
    assert fills == out[3:8] and [r["slot"] for r in fills] == [100, 101, 102, 103, 104] and all(r["src"] == "tip_fill" for r in fills)
    assert c["filled_ranges"] == 1 and c["filled_slots"] == 5 and c["fill_rows"] == 5
    assert [r["slot"] for r in out if r["type"] == "trade"] == sorted(r["slot"] for r in out if r["type"] == "trade")  # slot order holds


def test_splice_passes_an_unfilled_gap_through_unchanged(tmp_path):
    run_fill(tmp_path, [gap_row(100, 104)], Rpc(1000, script={101: ["T"] * 20}))
    seg = gf.load_segment(tmp_path / "seg")
    stream = adapter_stream()
    c = Counter()
    out = list(gf.splice(stream, seg, c))
    assert out == stream and c["gaps_not_filled"] == 1 and c["filled_ranges"] == 0


def test_the_follower_row_stands_and_a_repair_twin_after_the_gap_row_is_dropped(tmp_path):
    run_fill(tmp_path, [gap_row(100, 104)])
    seg = gf.load_segment(tmp_path / "seg")
    stream = adapter_stream()
    dup = dict(gf.arena_rows(seg, 100, 104)[2])
    dup.pop("fill"), dup.pop("src")
    dup["tx_index"] = None  # the twin differs from the repair row in tx_index presence only: the key ignores it
    stream.insert(3, dup)  # the follower carried slot 102's print itself, after the gap row
    c = Counter()
    out = list(gf.splice(stream, seg, c))
    at102 = [r for r in out if r["type"] == "trade" and r["slot"] == 102]
    assert len(at102) == 1 and "fill" not in at102[0] and c["fill_rows_dup_dropped"] == 1 and c["fill_rows"] == 4


@pytest.mark.parametrize("reason", ["unresolved_dropped", "skip_contradicted"])
def test_follower_rows_on_both_sides_of_the_gap_row_leave_no_duplicate_and_no_mixed_group(tmp_path, reason):
    """QP C2: the slot has follower rows before AND after the gap row; the repair row is a twin of the one before. Through the engine: rows_dup 0, no gap, one fill."""
    run_fill(tmp_path, [gap_row(102, 102, reason)])
    seg = gf.load_segment(tmp_path / "seg")
    twin = dict(seg.walker_rows[0][1], t_recv_ms=T0 + 102 * 400)
    assert twin["slot"] == 102
    g = tr.TipGap(102, 102, reason, GAP_T, 1)
    stream = ata.ArenaTipAdapter().feed([("trade", wt(101)), ("trade", twin), ("gap", g), ("trade", wt(102, 7, t=T0 + 102 * 400 + 1)), ("trade", wt(103))])
    assert [r["type"] for r in stream] == ["trade", "trade", "gap", "trade", "trade"]
    c = Counter()
    out = list(gf.splice(stream, seg, c))
    assert [r["type"] for r in out] == ["trade", "trade", "gap", "trade", "trade"] and out[2]["filled"] is True  # marker in the gap row's place, no repair row kept
    assert c["fill_rows"] == 0 and c["fill_rows_dup_dropped"] == 1 and gf.mixed_groups(out) == 0
    assert len([r for r in out if r["type"] == "trade" and r["slot"] == 102]) == 2
    from tools.test_arena_tip_adapter import mk_engine

    (tmp_path / "eng").mkdir()
    eng = mk_engine(tmp_path / "eng", gap_slots=None, gap_silence_ms=None)
    for r in out:
        eng.on_row(r)
    assert eng.counters.get("rows_dup", 0) == 0 and eng.gaps == [] and len(eng.fills) == 1


def test_a_range_whose_merged_rows_would_mix_the_two_order_forms_stays_unfilled():
    """QP C2: a numbered follower print and an unnumbered repair print in one (pool, slot): the range is NOT filled and the data_loss gap passes unchanged."""
    rng = {"gid": "backlog_jump:100-100", "reason": "backlog_jump", "from_slot": 100, "to_slot": 100, "t_recv_ms": GAP_T, "slots": 1, "fetched": 1, "skipped_slots": [],
           "failed_slots": [], "filled": True, "back_slots": 50}
    repair = wt(100, 0, venue="pumpswap", pool="P0")
    repair.pop("tx_index")
    seg = gf.Segment({"ranges": [rng]}, [("trade", repair)])
    g = tr.TipGap(100, 100, "backlog_jump", GAP_T, 1)
    stream = ata.ArenaTipAdapter().feed([("trade", wt(99)), ("gap", g), ("trade", wt(100, 1, venue="pumpswap", pool="P0", t=GAP_T + 1)), ("trade", wt(101))])
    c = Counter()
    out = list(gf.splice(stream, seg, c))
    assert out == stream and c["fill_refused_mixed_form"] == 1 and c["filled_ranges"] == 0
    assert not any(r.get("fill") or r.get("filled") for r in out)


def test_the_follower_rows_already_passed_are_remembered_for_the_dedupe(tmp_path):
    """A follower row of a slot inside the range that arrived BEFORE the gap row is a twin too (the late gap of unresolved_dropped)."""
    run_fill(tmp_path, [gap_row(100, 104)])
    seg = gf.load_segment(tmp_path / "seg")
    twin = dict(seg.walker_rows[2][1], t_recv_ms=T0 + 102 * 400)
    g = tr.TipGap(100, 104, "backlog_jump", GAP_T, 5)
    stream = ata.ArenaTipAdapter().feed([("trade", wt(99)), ("trade", twin), ("gap", g), ("trade", wt(105))])
    c = Counter()
    out = list(gf.splice(stream, seg, c))
    assert c["fill_rows_dup_dropped"] == 1 and c["fill_rows"] == 4 and len([r for r in out if r["type"] == "trade" and r["slot"] == 102]) == 1


# ------------------------------------------------------------------------------------------------------------------------------------ warm decode (QP C3)
def pumpswap_block(slot: int) -> dict:
    from tools.test_fast_tip_follower import _block, _line

    blk = _block(slot)
    blk["transactions"][0]["meta"]["logMessages"] = [_line("pumpswap_sell_event.b64")]
    return blk


def test_a_pumpswap_repair_row_carries_tx_index_because_the_fill_decodes_with_a_warm_pool_cache(tmp_path):
    from observe.trade_decode import WSOL_MINT

    def lookup(pools):
        return {p: ("Mint111", WSOL_MINT) for p in pools}

    cold = ftf.TipFollower(out_dir=tmp_path / "cold" / "out", state_dir=tmp_path / "cold" / "st", fetch=Rpc(5, script={5: [pumpswap_block(5)]}).fetch, get_tip=lambda: 0,
                           lookup=lookup, v_lookup=lambda p: {}, clock_ms=lambda: GAP_T, sleep=lambda s: None, log=lambda m: None, trade_event_v=True)
    cold.process_slot(5)  # the cold follower: the print comes back through resolve_unresolved and has no tx_index
    (cold_row,) = [json.loads(x) for p in (tmp_path / "cold" / "out").glob("trades-*.jsonl") for x in p.read_text().splitlines()]
    assert "tx_index" not in cold_row and cold_row["mint"] == "Mint111"
    man, _ = run_fill(tmp_path, [gap_row(5, 5)], Rpc(5, script={5: [pumpswap_block(5)]}), lookup=lookup)
    (row,) = seg_trades(tmp_path / "seg")
    assert isinstance(row["tx_index"], int) and man["rows_no_tx_index"] == 0 and man["ranges"][0]["filled"] is True
    assert {k: v for k, v in row.items() if k not in ("tx_index", "t_recv_ms")} == {k: v for k, v in cold_row.items() if k not in ("tx_index", "t_recv_ms")}


def test_a_pool_that_cannot_be_looked_up_leaves_the_range_a_gap(tmp_path):
    man, _ = run_fill(tmp_path, [gap_row(5, 5)], Rpc(5, script={5: [pumpswap_block(5)]}), lookup=lambda pools: {})
    assert man["ranges"][0]["filled"] is False and man["ranges"][0]["failed_slots"] == [5] and man["complete"] is False


def test_the_manifest_records_the_back_margin_and_the_rows_without_tx_index(tmp_path):
    man, _ = run_fill(tmp_path, [gap_row(100, 101), gap_row(200, 200, "unfetchable")])
    by = {r["reason"]: r for r in man["ranges"]}
    assert by["backlog_jump"]["back_slots"] == 50 and by["unfetchable"]["back_slots"] == 0 and man["backlog_slots"] == 50 and man["rows_no_tx_index"] == 0


# ------------------------------------------------------------------------------------------------------------------------------------ post-fill record, sealed paths
def test_the_post_fill_gap_record_and_the_hour_counts_report_both_filled_and_unfilled(tmp_path):
    gaps = [gap_row(100, 104), gap_row(200, 300), gap_row(100, 104)]
    man, _ = run_fill(tmp_path, gaps, max_missed=50)
    rec = gf.post_fill_gap_record(gaps, man)
    assert [(e["gid"], e["filled"], e["refused"]) for e in rec] == [("backlog_jump:100-104", True, None), ("backlog_jump:200-300", False, "over_bound")]  # the repeat is one range
    hour = ftf.hour_of_ms(GAP_T)
    assert gf.hour_fill_counts(rec) == {hour: {"filled_ranges": 1, "unfilled_ranges": 1, "filled_slots": 5, "unfilled_slots": 101}}
    assert all(set(e) <= {"gid", "reason", "from_slot", "to_slot", "slots", "missed_slots", "t_recv_ms", "hour", "filled", "refused", "failed_slots"} for e in rec)  # numbers only


def test_the_cli_refuses_the_sealed_tip_tree_the_archive_and_the_derived_copy(tmp_path):
    from tools import arena_tip_tape_writer as w

    from tools import arena_tip_push as push

    for bad in ("/var/lib/mal/sealed/fast-trades-tip/gaps.jsonl", "/var/lib/mal/tip-tape-archive/x", os.path.join(w.DEFAULT_OUT, "seg"), w.DEFAULT_OUT,
                push.DST_ROOTS[0], os.path.join(push.DST_ROOTS[0], "seg"), os.path.join(push.DST_ROOTS[0], "tape", "gaps.jsonl")):
        assert gf.refused_path(bad), bad
    assert gf.refused_path(tmp_path / "gaps.jsonl") is None
    assert gf.main(["--gaps", "/var/lib/mal/sealed/fast-trades-tip/gaps.jsonl", "--segment-dir", str(tmp_path / "s"), "--public-rpc"]) == 4
    assert gf.main(["--gaps", str(tmp_path / "g"), "--segment-dir", "/var/lib/mal/sealed/x", "--public-rpc"]) == 4
    assert gf.main(["--gaps", os.path.join(push.DST_ROOTS[0], "gaps.jsonl"), "--segment-dir", str(tmp_path / "s"), "--public-rpc"]) == 4  # reading the research-0 derived copy
    assert gf.main(["--gaps", str(tmp_path / "g"), "--segment-dir", os.path.join(push.DST_ROOTS[0], "fill"), "--public-rpc"]) == 4  # writing into it


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
