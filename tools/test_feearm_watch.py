"""Tests for tools/feearm_watch.py (synthetic files; no network, no key)."""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from tools import feearm_watch as fw
from tools import probe_executor as pe

TEST, CTRL = ("test", 155_000), ("control", 500_000)


def buy(i, arm, outcome="landed", ts=None):
    name, pr = arm
    r = dict(kind="buy", ts_ms=ts or 1000 + i, mint=f"M{i}", fee_arm=name, arm_priority_lamports=pr, arm_index=i, outcome=outcome,
             landed=outcome == "landed")
    if outcome == "dropped":
        r["fail_class"] = "expired"
    if outcome == "failed":
        r["fail_class"] = "slippage_exceeded"
    return r


def alert(what, arm=TEST):
    return dict(kind="alert", ts_ms=5, mint="Mx", alert=what, fee_arm=arm[0], arm_priority_lamports=arm[1], arm_index=1)


def state(open_=None, pending=None, realized=0):
    return dict(realized_lamports=realized, open=open_ or {}, pending=pending or {}, feearm=dict(seed=1, arms={}, sends=0))


class Run(unittest.TestCase):
    def run_tool(self, st, rows, *extra, raw_ledger=None, raw_state=None):
        with tempfile.TemporaryDirectory() as d:
            sp, lp = Path(d) / "s.json", Path(d) / "l.jsonl"
            sp.write_text(raw_state if raw_state is not None else json.dumps(st))
            lp.write_text(raw_ledger if raw_ledger is not None else "".join(json.dumps(r) + "\n" for r in rows))
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = fw.main(["--state", str(sp), "--ledger", str(lp), *extra])
            return rc, buf.getvalue()


class TestWatch(Run):
    def test_ok_case_counts(self):
        rows = [buy(0, CTRL), buy(1, TEST), buy(2, TEST, "dropped"), buy(3, TEST, "failed"),
                dict(kind="sell", landed=True, fee_arm="test", arm_priority_lamports=155_000), alert("buy_landed_zero_tokens")]
        rc, out = self.run_tool(state(), rows)
        self.assertEqual(rc, 0, out)
        self.assertIn("FEEARM_WATCH OK", out)
        self.assertIn("arm=test buys_attempted=3 landed=1 expired_unlanded=1 failed_onchain=1 sells_landed=1", out)
        self.assertIn("arm=control buys_attempted=1", out)
        self.assertIn("buy_landed_zero_tokens=1", out)
        self.assertIn("committed_cost_lamports=0 cap_lamports=150000000", out)

    def test_expired_threshold_is_more_than_3_of_first_20(self):
        rows = [buy(i, TEST, "dropped" if i < 3 else "landed") for i in range(20)]
        self.assertEqual(self.run_tool(state(), rows)[0], 0)
        rows = [buy(i, TEST, "dropped" if i < 4 else "landed") for i in range(20)]
        rc, out = self.run_tool(state(), rows)
        self.assertEqual(rc, 3)
        self.assertIn("FEEARM_WATCH STOP_RECOMMENDED reason=test_arm_expired first20=4>3", out)

    def test_only_first_20_by_arm_index_count_and_control_ignored(self):
        rows = [buy(i, TEST) for i in range(20)] + [buy(i, TEST, "dropped") for i in range(20, 30)]
        rows += [buy(100 + i, CTRL, "dropped") for i in range(10)]
        self.assertEqual(self.run_tool(state(), rows)[0], 0)
        # order in the file is irrelevant: arm_index decides
        rows = [buy(i, TEST, "dropped") for i in range(4)][::-1] + [buy(i, TEST) for i in range(4, 20)]
        self.assertEqual(self.run_tool(state(), rows)[0], 3)

    def test_early_stop_with_fewer_than_20(self):
        rc, out = self.run_tool(state(), [buy(i, TEST, "dropped") for i in range(4)])
        self.assertEqual(rc, 3)
        self.assertIn("first4=4>3", out)

    def test_each_alert_stops(self):
        for what in ("unsafe_tx_refused", "sell_stuck", "sell_abandoned"):
            rc, out = self.run_tool(state(), [buy(0, TEST), alert(what)])
            self.assertEqual(rc, 3, what)
            self.assertIn(f"FEEARM_WATCH STOP_RECOMMENDED reason=alert_{what} count=1", out)

    def test_separate_alerts_file(self):
        with tempfile.TemporaryDirectory() as d:
            sp, lp, ap = (Path(d) / n for n in ("s.json", "l.jsonl", "a.jsonl"))
            sp.write_text(json.dumps(state()))
            lp.write_text(json.dumps(buy(0, TEST)) + "\n")
            ap.write_text(json.dumps(alert("sell_stuck")) + "\n")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(fw.main(["--state", str(sp), "--ledger", str(lp), "--alerts", str(ap)]), 3)

    def test_balance_mismatch(self):
        st = state(open_={"M": dict(buy_cost_lamports=7_000_000, arm_priority=155_000, sell_attempts=0)}, realized=-1_000_000)
        start = 280_000_000
        expected = start - 1_000_000 - 7_000_000
        ok = ("--start-balance-lamports", str(start), "--balance-lamports", str(expected - 4_999_999))
        rc, out = self.run_tool(st, [], *ok)
        self.assertEqual(rc, 0, out)
        bad = ("--start-balance-lamports", str(start), "--balance-lamports", str(expected - 5_000_001))
        rc, out = self.run_tool(st, [], *bad)
        self.assertEqual(rc, 3)
        self.assertIn("balance_mismatch abs_lamports=5000001", out)
        high = ("--start-balance-lamports", str(start), "--balance-lamports", str(expected + 5_000_001))
        self.assertEqual(self.run_tool(st, [], *high)[0], 3)

    def test_pending_buy_widens_lower_side_only(self):
        pend = {"M": dict(kind="buy", spend=5_000_000, arm_priority=155_000)}
        start = 280_000_000
        landed_meanwhile = ("--start-balance-lamports", str(start), "--balance-lamports", str(start - 7_600_000))
        self.assertEqual(self.run_tool(state(pending=pend), [], *landed_meanwhile)[0], 0)
        self.assertEqual(self.run_tool(state(), [], *landed_meanwhile)[0], 3)

    def test_balance_args_must_pair(self):
        rc, out = self.run_tool(state(), [], "--balance-lamports", "1")
        self.assertEqual(rc, 2)

    def test_balance_skipped_without_args(self):
        self.assertIn("balance_check=skipped", self.run_tool(state(), [])[1])

    def test_multiple_stops_each_reported(self):
        rows = [buy(i, TEST, "dropped") for i in range(4)] + [alert("sell_abandoned")]
        rc, out = self.run_tool(state(), rows)
        self.assertEqual(out.count("STOP_RECOMMENDED"), 2)
        self.assertNotIn("FEEARM_WATCH OK", out)

    def test_malformed_inputs_exit_2(self):
        for kw in (dict(raw_ledger="{not json\n"), dict(raw_ledger='{"no_kind":1}\n'), dict(raw_state="nope"),
                   dict(raw_state=json.dumps({"open": {}})), dict(raw_state=json.dumps({**state(), "realized_lamports": "x"}))):
            rc, out = self.run_tool(state(), [], **kw)
            self.assertEqual(rc, 2, kw)
            self.assertIn("BAD_INPUT", out)

    def test_missing_files_exit_2(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(fw.main(["--state", "/nonexistent/s", "--ledger", "/nonexistent/l"]), 2)

    def test_no_pnl_printed_and_out_json(self):
        st = state(realized=-123_456_789, open_={"M": dict(buy_cost_lamports=7_777_777, arm_priority=155_000, sell_attempts=0)})
        rows = [dict(buy(0, TEST), sol_spent_lamports=9_999_991),
                dict(kind="sell", landed=True, fee_arm="test", sol_received_lamports=8_888_881, pnl_lamports=-4_242_424, ret=-0.2)]
        with tempfile.TemporaryDirectory() as d:
            o = Path(d) / "o.json"
            rc, out = self.run_tool(st, rows, "--out", str(o))
            blob = out + o.read_text()
        for needle in ("123456789", "9999991", "8888881", "4242424", "pnl", "realized", "ret="):
            self.assertNotIn(needle, blob)
        self.assertIn("committed_cost_lamports=", out)

    def test_committed_cost_matches_executor(self):
        pos = {"buy_cost_lamports": 6_000_000, "arm_priority": 155_000, "sell_attempts": 1}
        pend = {"kind": "buy", "spend": 5_000_000, "arm_priority": 500_000}
        for realized in (-3_000_000, 2_000_000):
            st = pe.State(realized_lamports=realized, open={"A": pos}, pending={"B": pend})
            d = state(open_={"A": pos}, pending={"B": pend}, realized=realized)
            self.assertEqual(fw.committed_cost(d, 3), pe.feearm_committed_cost(st, 3))

    def test_over_cap_reported(self):
        rc, out = self.run_tool(state(realized=-151_000_000), [])
        self.assertIn("over_cap=true", out)


if __name__ == "__main__":
    unittest.main()
