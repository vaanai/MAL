"""Engine side of F2 (repair before exclude): a filled range is not a DEC-031 1.4 gap for a position whose legs stay clear of it; an exit whose pricing slot falls inside it
executes at the first print at or after the range end + 1 (mark-through-gap); an entry leg in [lo - back, hi] or an exit leg in [lo - back, lo) keeps the exclusion; marked-through
trades count in the 5% numerator; an unfilled range stays a gap. Synthetic tape, tmp ledger, no network."""

from __future__ import annotations

import collections
from collections import Counter

import pytest

from tools import arena_stack as AS
from tools import arena_tip_gapfill as gf
from tools.test_arena_paper import SC, _row, mk_engine, run_trade, tms, trades
from tools.test_arena_tip_adapter import mk_engine as mk_tip_engine
from tools.test_arena_tip_gapfill import adapter_stream, gap_row, run_fill

LO, HI = 148, 160
SELL = 150
STAKE = 0.25


@pytest.fixture(autouse=True)
def _plenty_of_disk(monkeypatch):
    monkeypatch.setattr("tools.arena_paper.shutil.disk_usage", lambda path: collections.namedtuple("Usage", "total used free")(1000, 500, 500))


def marker(lo=LO, hi=HI, back=0, **kw) -> dict:
    return {"type": "gap", "prog": "all", "kind": "tip_gap_backlog_jump_filled", "slot": lo, "t_recv_ms": tms(lo), "data_loss": False, "filled": True, "from_slot": lo,
            "to_slot": hi, "missed_slots": hi - lo + 1, "src": "tip", "back_slots": back, **kw}


def lossgap(lo=LO, hi=HI) -> dict:
    return {"type": "gap", "prog": "all", "kind": "tip_gap_backlog_jump", "slot": lo, "t_recv_ms": tms(lo), "data_loss": True, "from_slot": lo, "to_slot": hi, "missed_slots": hi - lo + 1,
            "src": "tip"}


def tape(first=None, lo=90, hi=215, lo_fill=LO, hi_fill=HI) -> list[dict]:
    """P1 prints for slots lo..hi-1; the rows of [lo_fill, hi_fill] are repair rows (fill: true) when `first` (a marker or gap row) is given, placed before them."""
    rows, put = [], False
    for i, s in enumerate(range(lo, hi)):
        r = _row(i, s)
        if first is not None and lo_fill <= s <= hi_fill:
            if not put:
                rows.append(dict(first))
                put = True
            r["fill"], r["src"] = True, "tip_fill"
        rows.append(r)
    return rows


def run(tmp_path, name, rows, sell=SELL, cfg=None, buy=100):
    sub = tmp_path / name
    sub.mkdir()
    eng = mk_engine(sub, cfg or SC(strand_rule="total", gap_slots=None, gap_silence_ms=None))
    run_trade(eng, rows, buy_slot=buy, sell_slot=sell)
    return eng


def legs_of(eng) -> list:
    return [(r["stake_sol"], r["legs"], r["calc"]) for r in trades(eng)]


def excluded_reasons(eng) -> set:
    return {x["exclusion_reason"] for x in trades(eng, "excluded")}


# ------------------------------------------------------------------------------------------------------------------------------------ a filled range is not a gap
def test_a_filled_range_is_not_a_gap_and_the_trade_is_priced_not_excluded(tmp_path):
    eng = run(tmp_path, "filled", tape(marker()))
    assert eng.gaps == [] and eng.fills == [("all", LO, HI, 0)]
    assert eng.counters["gap_filled"] == 1 and eng.counters["gap_filled_slots"] == HI - LO + 1 and eng.counters.get("gap_data_loss", 0) == 0
    assert len(trades(eng)) == 3 and trades(eng, "excluded") == []


def test_an_unfilled_range_stays_a_gap_and_excludes_the_trade(tmp_path):
    eng = run(tmp_path, "unfilled", tape(lossgap()))
    assert len(eng.gaps) == 1 and eng.fills == []
    assert trades(eng) == [] and excluded_reasons(eng) == {"feed_gap"} and eng.counters["excluded_feed_gap"] == 1


def test_a_filled_marker_the_engine_cannot_read_is_a_data_loss_gap_fail_closed(tmp_path):
    bad = marker()
    bad.pop("from_slot")
    eng = run(tmp_path, "bad", tape(bad))
    assert eng.counters["gap_fill_marker_bad"] == 1 and eng.fills == [] and len(eng.gaps) == 1
    assert trades(eng) == [] and trades(eng, "excluded")[0]["exclusion_reason"] == "feed_gap"
    both = run(tmp_path, "both", tape(marker(data_loss=True)))  # filled AND data_loss: loss wins
    assert both.fills == [] and len(both.gaps) == 1


def test_a_data_loss_gap_inside_the_span_still_excludes_next_to_a_filled_range(tmp_path):
    rows = tape(marker())
    rows.insert(0, dict(lossgap(120, 125), t_recv_ms=tms(126)))  # an unfilled one elsewhere in the span
    eng = run(tmp_path, "mixed", rows)
    assert len(eng.fills) == 1 and len(eng.gaps) == 1 and trades(eng) == []


# ------------------------------------------------------------------------------------------------------------------------------------ mark-through-gap
def test_the_exit_inside_a_filled_range_executes_at_the_first_print_after_the_range(tmp_path):
    eng = run(tmp_path, "marked", tape(marker()))
    lag = AS.exit_lag_slots(0.4)
    trig = SELL + lag + 1
    assert LO <= trig <= HI  # the scenario: the exit's pricing slot is inside the range
    assert eng.counters["mark_through"] == 1
    got = trades(eng)
    assert len(got) == 3 and all(r["mark_through"] == {"xs_before": SELL, "filled_from_slot": LO, "filled_to_slot": HI, "xs": SELL + HI + 1 - trig} for r in got)
    ref = run(tmp_path, "ref", tape(), sell=HI - lag)  # the same book, an exit decided so that its pricing slot is HI + 1, no fill at all
    assert [(s, lg, cl) for s, lg, cl in legs_of(eng)] == legs_of(ref)
    assert all("mark_through" not in r for r in trades(ref))
    plain = run(tmp_path, "plain", tape())  # the unmarked exit prices at trig, a different print
    assert legs_of(plain) != legs_of(eng)


def test_an_exit_outside_the_filled_range_is_untouched_and_carries_no_mark(tmp_path):
    with_marker = run(tmp_path, "m", tape(marker()), sell=190)
    without = run(tmp_path, "n", tape(), sell=190)
    assert with_marker.counters.get("mark_through", 0) == 0
    assert legs_of(with_marker) == legs_of(without) and all("mark_through" not in r for r in trades(with_marker))
    assert not without.fills and without.counters.get("gap_filled", 0) == 0  # an engine that never sees a marker is the engine it was


def test_a_fill_of_another_program_does_not_move_the_exit(tmp_path):
    m = marker()
    m["prog"] = "pumpfun"  # P1 is a PumpSwap pool: a pump.fun range is not its gap and not its mark
    eng = run(tmp_path, "other", tape(m))
    assert eng.counters.get("mark_through", 0) == 0 and len(trades(eng)) == 3


def test_there_is_no_strand_rule_for_a_filled_range_the_ordinary_readiness_rule_applies(tmp_path):
    """QP C5: after a fill the pool history is complete. A quiet pool after the range is the stack's ordinary case: the position waits for its print (or derived_last at
    grace_slots); it is never booked as a total loss after a few hundred quiet slots."""
    p1 = tape(marker(), hi=HI + 1)  # P1 prints stop at the end of the filled range
    p2 = [_row(1000 + i, s, pool="P2") for i, s in enumerate(range(90, 90 + 600))]
    rows = sorted(p1 + p2, key=lambda r: (r["slot"], r["type"] != "gap", r.get("event_index", -1)))
    eng = run(tmp_path, "quiet", rows)
    assert trades(eng) == [] and trades(eng, "excluded") == [] and len(eng.positions) == 1 and "mark_strand" not in eng.counters
    assert not hasattr(gf, "MARK_THROUGH_BOUND_SLOTS")


# ------------------------------------------------------------------------------------------------------------------------------------ legs near the range keep the exclusion (QP C4)
def test_an_entry_leg_landing_inside_the_filled_range_stays_excluded(tmp_path):
    eng = run(tmp_path, "entry", tape(marker()), buy=150, sell=190)
    assert trades(eng) == [] and excluded_reasons(eng) == {"feed_gap"} and eng.gaps == []


def test_an_entry_leg_in_the_back_margin_of_a_backlog_range_stays_excluded_and_one_before_it_is_priced(tmp_path):
    inside = run(tmp_path, "inside", tape(marker(back=50)), buy=100, sell=190)  # landing 104/105 is in [lo - 50, hi] = [98, 160]
    assert trades(inside) == [] and excluded_reasons(inside) == {"feed_gap"}
    before = run(tmp_path, "before", tape(marker(back=50)), buy=92, sell=190)  # landing 96/97 is before the margin
    assert len(trades(before)) == 3 and trades(before, "excluded") == []
    zero = run(tmp_path, "zero", tape(marker(back=0)), buy=100, sell=190)  # any other reason: no margin
    assert len(trades(zero)) == 3 and trades(zero, "excluded") == []


def late_flags(eng) -> list:
    return [r for r in eng.ledger.read_bot("botA") if r["kind"] == "late_print" and r.get("gap_overlap") and r.get("fill_margin")]


def test_an_exit_leg_landing_before_the_range_inside_the_back_margin_is_flagged_for_the_scorer(tmp_path):
    """The exit's pricing slot (about 144) is in [138, 147]: the position is priced before the marker reaches the engine (the marker sits at the gap row, slot 148), so the
    priced row stays write-once and a late_print row with gap_overlap marks it; the scorer's late_print_gap rule excludes it and the cap counts it."""
    margin = run(tmp_path, "margin", tape(marker(back=10)), sell=140)
    flags = late_flags(margin)
    assert len(flags) == 1 and flags[0]["fill_margin"] == {"from_slot": LO, "to_slot": HI, "back_slots": 10} and margin.counters["fill_margin_flagged"] == 1
    assert gf.cap_counts(margin.ledger.read_bot("botA"), STAKE)["s14_reasons"] == {"late_print_gap": 1}
    none = run(tmp_path, "none", tape(marker(back=0)), sell=140)
    assert len(trades(none)) == 3 and trades(none, "excluded") == [] and none.counters.get("mark_through", 0) == 0 and late_flags(none) == []


def test_an_entry_priced_before_the_marker_inside_the_backlog_margin_is_flagged_the_same_way(tmp_path):
    early = run(tmp_path, "early", tape(marker(back=50)), buy=100, sell=110)  # landing 104/105 in [98, 160]; priced at about 115, before the marker at slot 148
    assert len(trades(early)) == 3 and len(late_flags(early)) == 1
    other = run(tmp_path, "other", tape(marker(back=0)), buy=100, sell=110)  # no margin for any other reason: nothing to flag
    assert len(trades(other)) == 3 and late_flags(other) == []


def test_a_position_that_spans_the_whole_range_is_priced_with_the_complete_history(tmp_path):
    eng = run(tmp_path, "span", tape(marker(back=50)), buy=92, sell=190)
    ref = run(tmp_path, "ref", tape(), buy=92, sell=190)  # no fill at all, the same prints
    assert len(trades(eng)) == 3 and legs_of(eng) == legs_of(ref)


# ------------------------------------------------------------------------------------------------------------------------------------ the 5% numerator
def test_marked_through_trades_stay_priced_and_count_in_the_cap_numerator(tmp_path):
    marked = run(tmp_path, "marked", tape(marker()))
    plain = run(tmp_path, "plain", tape(), sell=190)
    excl = run(tmp_path, "excl", tape(lossgap()))
    rows = lambda e: e.ledger.read_bot("botA")  # noqa: E731
    assert gf.mark_through_ids(rows(marked), STAKE) == {trades(marked)[0]["position_id"]}
    c = gf.cap_counts(rows(marked), STAKE)
    assert (c["n_positions"], c["n_s14"], c["n_mark_through"], c["numerator"], c["share"]) == (1, 0, 1, 1, 1.0) and len(trades(marked)) == 3
    assert gf.cap_counts(rows(plain), STAKE)["numerator"] == 0
    e = gf.cap_counts(rows(excl), STAKE)
    assert (e["n_s14"], e["n_mark_through"], e["numerator"], e["s14_reasons"]) == (1, 0, 1, {"feed_gap": 1})  # an exclusion is counted once, not twice


# ------------------------------------------------------------------------------------------------------------------------------------ the spliced stream into the engine
def walker(r: dict) -> dict:
    w = dict(r)
    for k in ("sol_lamports", "token_raw", "quote_reserve", "base_reserve"):
        w[k] = int(w[k])
    return w


def test_the_spliced_stream_marks_the_exit_through_the_range_marker_first(tmp_path):
    """QP C1: the stream splice() produces (not a hand-built tape) must give the engine the range before the repair rows, so an exit decided while they stream in is
    marked through. Probe of the QP: marker after the repair rows gave mark_through 0."""
    flat = tape()
    before = [r for r in flat if r["slot"] < LO]
    after = [r for r in flat if r["slot"] > HI]
    gap = {"type": "gap", "prog": "all", "kind": "tip_gap_unfetchable", "reason": "unfetchable", "slot": LO, "t_recv_ms": tms(LO), "from_ms": tms(LO - 1), "to_ms": tms(LO),
           "data_loss": True, "from_slot": LO, "to_slot": HI, "missed_slots": HI - LO + 1, "src": "tip", "signature": None, "event_index": None, "v": 1}
    rng = {"gid": f"unfetchable:{LO}-{HI}", "reason": "unfetchable", "from_slot": LO, "to_slot": HI, "t_recv_ms": tms(LO), "slots": HI - LO + 1, "fetched": HI - LO + 1,
           "skipped_slots": [], "failed_slots": [], "filled": True, "back_slots": 0}
    seg = gf.Segment({"ranges": [rng]}, [("trade", walker(r)) for r in flat if LO <= r["slot"] <= HI])
    c = Counter()
    out = list(gf.splice([*before, gap, *after], seg, c))
    assert [r["filled"] for r in out if r.get("type") == "gap"] == [True] and c["filled_ranges"] == 1 and c["fill_rows"] == HI - LO + 1
    i_marker = next(i for i, r in enumerate(out) if r.get("type") == "gap")
    assert [r.get("fill") for r in out[i_marker + 1 : i_marker + 1 + HI - LO + 1]] == [True] * (HI - LO + 1)  # the repair rows follow the marker
    eng = run(tmp_path, "spliced", out)
    assert eng.counters["mark_through"] == 1 and eng.counters.get("rows_dup", 0) == 0 and eng.gaps == [] and len(eng.fills) == 1
    ref = run(tmp_path, "ref", tape(), sell=HI - AS.exit_lag_slots(0.4))
    assert legs_of(eng) == legs_of(ref) and len(trades(eng)) == 3


def test_the_adapter_stream_spliced_with_a_fill_gives_the_engine_a_fill_and_no_gap(tmp_path):
    run_fill(tmp_path, [gap_row(100, 104)])
    seg = gf.load_segment(tmp_path / "seg")
    for name, rows, gaps, fills in (("raw", adapter_stream(), 1, 0), ("spliced", list(gf.splice(adapter_stream(), seg, Counter())), 0, 1)):
        sub = tmp_path / name
        sub.mkdir()
        eng = mk_tip_engine(sub, gap_slots=None, gap_silence_ms=None)
        for r in rows:
            eng.on_row(r)
        assert (len(eng.gaps), len(eng.fills)) == (gaps, fills), name
    assert eng.counters["gap_filled_slots"] == 5 and eng.counters.get("rows_dup", 0) == 0


def test_a_fill_row_does_not_trip_the_engines_own_silence_or_slot_jump_detection(tmp_path):
    """Default detection ON: the first repair row is far from the last row before the gap (a long follower outage), and the first live row after the range is far from
    the last repair print. Neither is an engine gap once the range is filled; without the marker both are."""
    first = _row(0, 100)
    fills = [dict(_row(1, s), fill=True, t_recv_ms=tms(100) + 300_000) for s in (101, 102)]
    after = _row(2, 400, t=tms(100) + 301_000)
    for name, rows, want in (("filled", [first, marker(103, 399, t_recv_ms=tms(100) + 300_000), *fills, after], 0), ("plain", [first, *fills, after], 2)):
        sub = tmp_path / name
        sub.mkdir()
        eng = mk_engine(sub, SC(strand_rule="total"))
        for r in rows:
            eng.on_row({k: v for k, v in r.items() if not (name == "plain" and k == "fill")} if name == "plain" else r)
        assert len(eng.gaps) == want, (name, eng.gaps)
