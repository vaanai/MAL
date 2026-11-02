"""splice() before live wiring (QP): a gap noticed late stays unfilled, a range wider than the look-behind stays unfilled, and the dedupe looks back 2,000 slots. Synthetic rows and segments only."""

from __future__ import annotations

from collections import Counter

from tools import arena_tip_adapter as ata
from tools import arena_tip_gapfill as gf
from tools import arena_tip_reader as tr
from tools.test_arena_tip_adapter import wt
from tools.test_arena_tip_gapfill import GAP_T


def rng(lo: int, hi: int, reason: str = "backlog_jump", back: int = 50) -> dict:
    return {"gid": f"{reason}:{lo}-{hi}", "reason": reason, "from_slot": lo, "to_slot": hi, "t_recv_ms": GAP_T, "slots": hi - lo + 1, "fetched": hi - lo + 1, "skipped_slots": [],
            "failed_slots": [], "filled": True, "back_slots": back}


def seg_of(ranges: list[dict], walker: list[dict]) -> gf.Segment:
    return gf.Segment({"ranges": ranges}, [("trade", w) for w in walker])


def feed(items: list) -> list[dict]:
    return ata.ArenaTipAdapter().feed(items)


def test_the_look_behind_is_the_widest_range_the_fill_bound_allows():
    assert gf.LOOKBEHIND_SLOTS == gf.MAX_MISSED_SLOTS == 2_000


def test_a_gap_noticed_on_time_is_filled_and_the_same_gap_noticed_late_is_not():
    walker = [wt(s) for s in range(100, 105)]
    seg = seg_of([rng(100, 104)], walker)
    g = tr.TipGap(100, 104, "backlog_jump", GAP_T, 5)
    on_time = feed([("trade", wt(99)), ("gap", g), ("trade", wt(105)), ("trade", wt(106))])
    c = Counter()
    out = list(gf.splice(on_time, seg, c))
    assert c["filled_ranges"] == 1 and any(r.get("filled") for r in out)
    late = feed([("trade", wt(99)), ("trade", wt(105)), ("trade", wt(106)), ("gap", g), ("trade", wt(107))])
    assert [r["type"] for r in late] == ["trade", "trade", "trade", "gap", "trade"]  # the adapter noticed the gap after rows 105 and 106 had gone out
    c = Counter()
    out = list(gf.splice(late, seg, c))
    assert out == late and c["fill_refused_late"] == 1 and c["filled_ranges"] == 0 and c["gaps_not_filled"] == 1
    assert not any(r.get("filled") or r.get("fill") for r in out)  # the data_loss gap row passes unchanged: the engine excludes by it


def test_a_late_unresolved_dropped_slot_is_still_filled_while_the_stream_has_not_left_its_slot():
    """The follower writes unresolved_dropped after the slot's rows: rows of the SAME slot may precede the gap row. Only a LATER slot makes it late."""
    walker = [wt(102, 0), wt(102, 1)]
    seg = seg_of([rng(102, 102, "unresolved_dropped", 0)], walker)
    g = tr.TipGap(102, 102, "unresolved_dropped", GAP_T, 1)
    same_slot = feed([("trade", wt(101)), ("trade", wt(102, 0)), ("gap", g), ("trade", wt(102, 5, t=GAP_T + 1))])
    c = Counter()
    out = list(gf.splice(same_slot, seg, c))
    assert c["filled_ranges"] == 1 and c["fill_rows_dup_dropped"] == 1 and c["fill_rows"] == 1
    later_slot = feed([("trade", wt(101)), ("trade", wt(102, 0)), ("trade", wt(103)), ("gap", g), ("trade", wt(104))])
    c = Counter()
    assert list(gf.splice(later_slot, seg, c)) == later_slot and c["fill_refused_late"] == 1


def test_the_dedupe_sees_a_follower_twin_more_than_256_slots_behind_the_gap_row():
    """Follower rows at 105 and 1,800 come before the gap row for [100, 1900]: the twin of the repair row at 105 is 1,695 slots behind the newest row, inside the 2,000-slot look-behind
    (the old 256 would have forgotten it and inserted a duplicate)."""
    twin = wt(105, 0)
    seg = seg_of([rng(100, 1_900)], [twin, wt(1_500, 0)])
    g = tr.TipGap(100, 1_900, "backlog_jump", GAP_T, 1_801)
    stream = feed([("trade", wt(99)), ("trade", dict(twin)), ("trade", wt(1_800, 3)), ("gap", g), ("trade", wt(1_901))])
    assert [r["type"] for r in stream] == ["trade", "trade", "trade", "gap", "trade"]
    c = Counter()
    out = list(gf.splice(stream, seg, c))
    assert c["filled_ranges"] == 1 and c["fill_rows_dup_dropped"] == 1 and c["fill_rows"] == 1
    assert len([r for r in out if r["type"] == "trade" and r["slot"] == 105]) == 1 and len([r for r in out if r["type"] == "trade" and r["slot"] == 1_500]) == 1


def test_a_range_wider_than_the_look_behind_stays_unfilled():
    seg = seg_of([rng(100, 2_200)], [wt(1_000)])
    g = tr.TipGap(100, 2_200, "backlog_jump", GAP_T, 2_101)
    stream = feed([("trade", wt(99)), ("gap", g), ("trade", wt(2_201))])
    c = Counter()
    out = list(gf.splice(stream, seg, c))
    assert out == stream and c["fill_refused_wide"] == 1 and c["filled_ranges"] == 0


def test_the_look_behind_memory_is_bounded_and_a_long_stream_passes_through_unchanged():
    rows = feed([("trade", wt(s, 0, tx=0, t=GAP_T + s)) for s in range(1_000, 41_000)])
    out = list(gf.splice(rows, seg_of([], []), Counter()))
    assert out == rows
