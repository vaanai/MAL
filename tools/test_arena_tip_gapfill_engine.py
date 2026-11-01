"""Engine side of F2 (repair before exclude): a filled range is not a DEC-031 1.4 gap; an exit whose pricing slot falls inside it executes at the first print at or after the
range end + 1 (mark-through-gap); no print within the bound after that takes the strand class; an unfilled range stays a gap. Synthetic tape, tmp ledger, no network."""

from __future__ import annotations

import collections
from collections import Counter

import pytest

from tools import arena_stack as AS
from tools import arena_tip_gapfill as gf
from tools.arena_paper import MARK_THROUGH_BOUND_SLOTS, STAKES_SOL
from tools.test_arena_paper import SC, _row, mk_engine, run_trade, tms, trades
from tools.test_arena_tip_adapter import mk_engine as mk_tip_engine
from tools.test_arena_tip_gapfill import adapter_stream, gap_row, run_fill

LO, HI = 148, 160
SELL = 150


@pytest.fixture(autouse=True)
def _plenty_of_disk(monkeypatch):
    monkeypatch.setattr("tools.arena_paper.shutil.disk_usage", lambda path: collections.namedtuple("Usage", "total used free")(1000, 500, 500))


def marker(lo=LO, hi=HI, **kw) -> dict:
    return {"type": "gap", "prog": "all", "kind": "tip_gap_backlog_jump_filled", "slot": lo, "t_recv_ms": tms(lo), "data_loss": False, "filled": True, "from_slot": lo,
            "to_slot": hi, "missed_slots": hi - lo + 1, "src": "tip", **kw}


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


def run(tmp_path, name, rows, sell=SELL, cfg=None):
    sub = tmp_path / name
    sub.mkdir()
    eng = mk_engine(sub, cfg or SC(strand_rule="total", gap_slots=None, gap_silence_ms=None))
    run_trade(eng, rows, sell_slot=sell)
    return eng


def legs_of(eng) -> list:
    return [(r["stake_sol"], r["legs"], r["calc"]) for r in trades(eng)]


# ------------------------------------------------------------------------------------------------------------------------------------ a filled range is not a gap
def test_a_filled_range_is_not_a_gap_and_the_trade_is_priced_not_excluded(tmp_path):
    eng = run(tmp_path, "filled", tape(marker()))
    assert eng.gaps == [] and eng.fills == [("all", LO, HI)]
    assert eng.counters["gap_filled"] == 1 and eng.counters["gap_filled_slots"] == HI - LO + 1 and eng.counters.get("gap_data_loss", 0) == 0
    assert len(trades(eng)) == 3 and trades(eng, "excluded") == []


def test_an_unfilled_range_stays_a_gap_and_excludes_the_trade(tmp_path):
    eng = run(tmp_path, "unfilled", tape(lossgap()))
    assert len(eng.gaps) == 1 and eng.fills == []
    assert trades(eng) == [] and {x["exclusion_reason"] for x in trades(eng, "excluded")} == {"feed_gap"} and eng.counters["excluded_feed_gap"] == 1


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


# ------------------------------------------------------------------------------------------------------------------------------------ the strand class
def strand_tape() -> list[dict]:
    p1 = tape(marker(), hi=HI + 1)  # P1 prints stop at the end of the filled range
    p2 = [_row(1000 + i, s, pool="P2") for i, s in enumerate(range(90, 90 + 480))]
    return sorted(p1 + p2, key=lambda r: (r["slot"], r["type"] != "gap", r.get("event_index", -1)))  # the marker goes before the repair rows of its slot


def test_no_print_within_the_bound_after_the_filled_range_takes_the_strand_class(tmp_path):
    eng = run(tmp_path, "strand", strand_tape())
    got = trades(eng)
    assert len(got) == 3 and trades(eng, "excluded") == [] and eng.counters["mark_strand"] == 1
    for r in got:
        stake = round(r["stake_sol"] * 1e9)
        want = -stake - eng.cfg.send - AS.RENT_STRANDED
        assert r["state_src"] == "mark_strand" and r["mark_through"]["filled_to_slot"] == HI
        assert all(v["net_lamports"] == want and v["state"] == "stranded" for rule in r["legs"].values() for v in rule.values())
        assert all(d["strand"] is True for t in r["calc"].values() for d in t["by_rule"].values())


def test_the_strand_waits_for_the_bound_it_is_not_taken_early(tmp_path):
    rows = [r for r in strand_tape() if r["slot"] <= HI + 1 + MARK_THROUGH_BOUND_SLOTS - 20]  # the clock has not run the bound out yet
    eng = run(tmp_path, "early", rows)
    assert trades(eng) == [] and trades(eng, "excluded") == [] and eng.counters.get("mark_strand", 0) == 0


def test_the_strand_class_covers_both_rules_when_the_stack_prices_both(tmp_path):
    eng = run(tmp_path, "both", strand_tape(), cfg=SC(gap_slots=None, gap_silence_ms=None))  # strand_rule "both"
    r = trades(eng)[0]
    assert set(r["legs"]) == {"total", "sc"} and len({v["net_lamports"] for rule in r["legs"].values() for v in rule.values()}) == 1


# ------------------------------------------------------------------------------------------------------------------------------------ the spliced stream into the engine
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
