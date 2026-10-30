"""PR #1092 finding 1: the parity trips file is cumulative while the tie-out cuts the fill rows at the day's end, so a trip opened after the cut raised a false
hard tieout_mismatch, and so did a trip whose sell landed after the cut. hv4_c0_tieout.cut_parity_trips audits each trip as it stood at the cut, decided by
the trip's own fill rows; a trip with no landed buy anywhere still raises. Synthetic only."""

from __future__ import annotations

from tools import hv4_c0_tieout as tie
from tools.test_arena_live_bridge import T0, TmpCase
from tools.test_hv4_live_c0_pr5b import BUY_FEE, DAY_MS, S, SELL_FEE_TOTAL, trip_rows

END = T0 - T0 % DAY_MS + DAY_MS  # the end of T0's UTC day


def ptrip(mint, exit_landed=True):
    return {"trip_id": mint, "stake_lamports": S, "buy_fee_lamports": BUY_FEE, "sell_fee_lamports": SELL_FEE_TOTAL, "exit_landed": exit_landed}


def state(*mints, status="closed"):
    return {"orders": {f"o{i}": {"order_id": f"o{i}", "mint": m, "status": status, "stake_lamports": S} for i, m in enumerate(mints)}, "latches": {}}


class TieoutCutTests(TmpCase):
    def test_a_trip_opened_after_the_cut_is_not_a_mismatch(self):
        rows = trip_rows("M1", T0, T0) + trip_rows("M2", END + 1_000, END + 900)  # M2 opens and closes on the next UTC day
        trips = {"M1": ptrip("M1"), "M2": ptrip("M2")}  # the recorder's cumulative view, as at the export instant
        doc = tie.build(rows, state("M1", "M2"), END, trips)
        self.assertIsInstance(doc, dict)
        self.assertEqual(tie.cut_parity_trips(rows, trips, END), {"M1": trips["M1"]})
        tie.build(rows, state("M1", "M2"), END + DAY_MS, trips)  # the next day's export checks M2 in full

    def test_a_trip_whose_sell_landed_after_the_cut_is_checked_as_open_at_the_cut(self):
        ts = END - 10_000  # buy 10 s before midnight; the landed sell (+20 s) falls on the next day
        rows = trip_rows("M1", ts, ts - 100)
        trips = {"M1": ptrip("M1", exit_landed=True)}
        tie.build(rows, state("M1"), END, trips)
        self.assertIs(tie.cut_parity_trips(rows, trips, END)["M1"]["exit_landed"], False)
        self.assertIs(trips["M1"]["exit_landed"], True)  # the caller's mapping is not changed
        tie.build(rows, state("M1"), END + DAY_MS, trips)

    def test_fail_closed_cases_still_raise(self):
        rows = trip_rows("M1", T0, T0)
        with self.assertRaises(tie.TieoutMismatch):  # a parity trip with no landed buy anywhere
            tie.build(rows, state("M1"), END, {"M1": ptrip("M1"), "GHOST": ptrip("GHOST")})
        with self.assertRaises(tie.TieoutMismatch):  # an in-cut trip with a wrong fee
            tie.build(rows, state("M1"), END, {"M1": {**ptrip("M1"), "buy_fee_lamports": BUY_FEE + 1}})
        with self.assertRaises(tie.TieoutMismatch):  # exit_landed true with no landed sell anywhere (not an after-cut sell)
            tie.build(rows[:3], state("M1", status="open"), END, {"M1": ptrip("M1")})
        ts = END - 10_000
        late = trip_rows("M2", ts, ts - 100)
        with self.assertRaises(tie.TieoutMismatch):  # the after-cut rule never hides a wrong buy fee of a trip bought inside the cut
            tie.build(late, state("M2"), END, {"M2": {**ptrip("M2"), "buy_fee_lamports": BUY_FEE + 1}})

    def test_export_day_end_to_end_with_a_next_day_trip(self):
        import json
        from pathlib import Path
        d = Path(self.tmp)
        rows = trip_rows("M1", T0, T0) + trip_rows("M2", END + 1_000, END + 900)
        (d / "fills.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        (d / "state.json").write_text(json.dumps(state("M1", "M2")))
        (d / "trips.jsonl").write_text("".join(json.dumps(t) + "\n" for t in (ptrip("M1"), ptrip("M2"))))
        day = tie.utc_day(T0) if hasattr(tie, "utc_day") else None
        if day is None:
            from datetime import datetime, timezone
            day = datetime.fromtimestamp(T0 / 1000, timezone.utc).strftime("%Y-%m-%d")
        out = d / "tie"
        self.assertEqual(tie.export_day(d / "fills.jsonl", d / "state.json", out, day, END + 2 * DAY_MS, d / "trips.jsonl"), "created")
