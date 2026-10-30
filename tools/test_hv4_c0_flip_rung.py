"""QP standing condition C-a (#1090 comment 6528515779): the HV4 parity state carries `"rung": "C0"`, so DEC-031 Am.21 3.4 / #1106 reach the HV4 bridge.
The three owed tests: a gap-share breach gives NOT_DECIDABLE, no STOP, rc 0; gap plus a no_twin breach gives rc 10; a gap trip's loss counts in daily, total and
committed loss. Synthetic only: no HV4 row, ledger, read record or job log."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from tools import arena_live_parity as alp
from tools import hv4_c0_parity as c0p
from tools import hv4_live_c0 as c0
from tools.test_arena_live_bridge import TmpCase
from tools.test_arena_live_parity import NOW, attempts, book, cli, gap_book_share, gap_book_small
from tools import test_hv4_live_c0_pr3 as pr3
from tools import test_hv4_live_c0_pr5b as pr5b  # module import: a class imported by name would be collected and re-run here


def hv4_state() -> dict:
    """The state.json the HV4 gates write (hv4_c0_parity.parity_state, as LiveGates.run_parity calls it, plus the last landed tx it adds). The twin stamp is set
    here because a synthetic twins run record is a different test's concern (tools/test_hv4_c0_parity_stop.py); every other stamp is the function's own."""
    st = c0p.parity_state(NOW - 1_000, loss_ok=True, balance_ms=NOW - 1_000, twins_file="/nonexistent/twins.jsonl", trip_ids=[], chain_ok=True, control_ok=True,
                          hard_halts=[])
    st["at_ms"]["twin"] = NOW - 1_000
    st["last_landed_ms"] = NOW - 5_000
    return st


class RungTests(TmpCase):
    def test_the_hv4_state_names_rung_c0(self):
        st = hv4_state()
        self.assertEqual(st["rung"], "C0")
        self.assertEqual(c0p.RUNG, alp.P7_GAP_REPORT_ONLY_RUNG)
        self.assertFalse(alp.gap_share_halts(st))

    def test_gap_share_breach_is_not_decidable_with_no_stop_and_rc_0(self):
        for make in (gap_book_small, gap_book_share):
            with self.subTest(book=make.__name__):
                d = Path(self.tmp) / make.__name__
                d.mkdir()
                ts, ws = make()
                out = d / "o.json"
                args = cli(d, ts, ws, attempts(40), hv4_state())
                self.assertEqual(alp.main(args + ["--json", str(out)]), 0)
                self.assertFalse((d / "STOP").exists())
                res = json.loads(out.read_text())
                self.assertTrue(res["not_decidable"])
                self.assertIs(res["match"], False)
                self.assertEqual(res["verdict"], "parity not shown")
                self.assertNotIn("P7", res["halts"])

    def test_without_the_rung_the_same_breach_is_the_p7_soft_halt(self):
        st = hv4_state()
        del st["rung"]  # the state before C-a
        ts, ws = gap_book_small()
        self.assertEqual(alp.main(cli(Path(self.tmp), ts, ws, attempts(40), st)), 10)
        self.assertTrue((Path(self.tmp) / "STOP").exists())

    def test_gap_plus_a_no_twin_breach_is_rc_10_with_stop(self):
        ts, ws = gap_book_small()
        ws.pop(3)
        ws.pop(5)  # plus 2 no_twin before 20 trips
        out = Path(self.tmp) / "o.json"
        args = cli(Path(self.tmp), ts, ws, attempts(40), hv4_state())
        self.assertEqual(alp.main(args + ["--json", str(out)]), 10)
        self.assertTrue((Path(self.tmp) / "STOP").exists())
        res = json.loads(out.read_text())
        self.assertIn("P7", res["halts"])
        self.assertIn("no_twin", res["halts"]["P7"]["detail"])


class GapTripLossCountsTests(TmpCase):
    """A trip marked `gap` is excluded from parity statistics only; its realized loss still counts in the daily stop, the total stop and committed loss."""

    _env = pr3.GateWiringTests._env
    _prime_twins = pr3.GateWiringTests._prime_twins
    _bridge = pr3.GateWiringTests._bridge
    _setup = pr5b.TripEndToEndTests._setup

    @mock.patch.object(c0, "FEED_GAP_MANUAL_REVIEW", False)
    def test_a_gap_trips_loss_counts_in_daily_total_and_committed_loss(self):
        e, b, o, mint, rows = self._setup()
        buys = [r for r in rows if r["kind"] == "buy"]
        sells = [r for r in rows if r["kind"] == "sell"]
        e.port.ledger = [(r["kind"], mint, {k: v for k, v in r.items() if k not in ("kind", "mint", "ts_ms", "book")}) for r in buys]
        b.apply_events([{"event": "buy_landed", "mint": mint, "landed_slot": 110}])
        b.feed_ledger()
        self.assertFalse(b.gates.recorder.trips[mint]["gap"])
        d = Path(e.raw["root"]) / "transport"
        d.mkdir(exist_ok=True)
        (d / f"{e.bot}.tapegaps.jsonl").write_text(json.dumps({"kind": "data_loss", "ms": e.clock.t}) + "\n")  # a coverage-losing record while the trip is open
        b.gap_tick(e.clock.t)
        self.assertTrue(b.gates.recorder.trips[mint]["gap"])  # excluded from parity statistics
        loss = 1_000_000
        e.port.ledger = [(r["kind"], mint, {k: v for k, v in r.items() if k not in ("kind", "mint", "ts_ms", "book")}) for r in sells]
        b.apply_events([{"event": "sell_landed", "mint": mint, "landed_slot": 150, "pnl_lamports": -loss}])
        b.feed_ledger()
        self.assertTrue(b.gates.recorder.trips[mint]["exit_landed"])
        self.assertEqual(b.state.realized_lamports, -loss)  # total
        day = [v for v in b.state.days.values() if v.get("realized")]
        self.assertEqual([v["realized"] for v in day], [-loss])  # daily
        rent = b.cfg.rent_reserve_lamports if hasattr(b.cfg, "rent_reserve_lamports") else 2_100_000
        self.assertEqual(c0.committed_loss_lamports(-b.state.realized_lamports, [], rent), loss)  # committed loss
        near = c0.C0_LIMITS["total_loss_lamports"] - c0.position_worst_case_lamports(c0.C0_LIMITS["stake_lamports"], rent) - loss + 1
        self.assertIsNotNone(c0.total_stop_problem(near + loss, [], c0.C0_LIMITS["stake_lamports"], rent))  # the gap trip's loss can be what trips the total stop
        self.assertIsNone(c0.total_stop_problem(near - 1, [], c0.C0_LIMITS["stake_lamports"], rent))
