"""Tests for tools/hv4_c0_orders_proof.py: the md5 orders-equal-rows replay of the hv4_c0 bridge. Synthetic fixtures only: a temp root with SYNTHETIC_FIXTURE_ROOT, no HV4
row, no arena path, no network, no key. /data/mal/arena appears only as a string in the refusal test."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import arena_live_bridge as alb
from tools import hv4_c0_orders_proof as P
from tools import hv4_live_c0 as c0

REPO = Path(__file__).resolve().parent.parent
HEX32 = re.compile(r"^[0-9a-f]{32}$")


class ProofRuns(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = P.run_proof()

    def test_equal_and_exit_ok(self):
        self.assertTrue(self.res.ok, "\n".join(self.res.lines))
        self.assertEqual(self.res.record["result"], "EQUAL")
        self.assertEqual(self.res.record["problems"], [])

    def test_md5s_printed_and_equal(self):
        for key in ("bridge_orders_md5", "expected_orders_md5", "bridge_outcomes_md5", "expected_outcomes_md5"):
            self.assertRegex(self.res.record[key], HEX32)
        self.assertEqual(self.res.record["bridge_orders_md5"], self.res.record["expected_orders_md5"])
        self.assertEqual(self.res.record["bridge_outcomes_md5"], self.res.record["expected_outcomes_md5"])

    def test_counts_and_stakes_are_what_the_scenario_says(self):
        r = self.res.record
        self.assertEqual((r["buys"], r["sells"], r["orders"]), (32, 31, 63))  # 30 on day 1 (the 31st is capped) + 2 on day 2; d2 is left open
        text = "\n".join(self.res.lines)
        self.assertIn("stakes=[30000000, 50000000]", text)  # the 0.25 / 0.5 / 1.0 cards are clamped to 0.05 SOL; the 0.03 card is not
        self.assertIn("'max_trades_per_day': 1", text)
        for label in ("max_open", "stale_decision", "already_bought", "size_invalid", "card_stake_invalid", "duplicate_entry", "no_start_row",
                      "universe_skip:universe_v_out_of_range", "universe_skip:quote_not_wsol"):
            self.assertIn(f"'{label}': 1", text, label)

    def test_deterministic(self):
        again = P.run_proof()
        for key in ("bridge_orders_md5", "bridge_outcomes_md5", "log_md5"):
            self.assertEqual(again.record[key], self.res.record[key], key)

    def test_start_row_repeats_the_pins(self):
        steps, _pools = P.build_scenario()
        start = [s.body for s in steps if s.body["kind"] == "start"]
        self.assertEqual(len(start), 1)
        self.assertEqual((start[0]["frozen_sha"], start[0]["config_sha256"]), ("9ed4c5ff63935696b2a89150e13565e123047754",
                                                                              "1e611a5a636dfe6bd563ab5258f6b498cdb8906500ee8bf2f0264a2621621986"))
        self.assertEqual((c0.FROZEN_SHA, c0.CONFIG_PIN_SHA256), (start[0]["frozen_sha"], start[0]["config_sha256"]))

    def test_model_constants_are_typed_here_and_equal_the_compiled_ones_today(self):
        """The model must not import the bridge's caps (that would make the proof a tautology); this pins that the typed copies still agree with DEC-032 s3 as compiled."""
        self.assertEqual(P.M_STAKE_CAP_LAMPORTS, c0.C0_LIMITS["stake_lamports"])
        self.assertEqual((P.M_MAX_OPEN, P.M_MAX_TRADES_PER_DAY), (c0.C0_LIMITS["max_open"], c0.C0_LIMITS["max_trades_per_day"]))
        self.assertEqual((P.M_MAX_DECISION_AGE_MS, P.M_V_BAND, P.M_WSOL, P.M_SIZES), (alb.MAX_DECISION_AGE_MS, c0.UNIVERSE_V_RANGE, c0.WSOL_MINT, c0.HV4_SIZES))
        src = Path(P.__file__).read_text()
        body = src[src.index("def expected_lists"):src.index("def canonical")]
        self.assertNotIn("c0.", body)
        self.assertNotIn("alb.", body)

    def test_bridge_log_carries_the_decision_seq_of_every_send(self):
        run = P.run_bridge(Path(tempfile.mkdtemp(prefix="t-proof-")) / "w", *P.build_scenario())
        sends = [r for r in run.log if r["kind"] in ("buy_sent", "sell_sent")]
        self.assertEqual(len(sends), 63)
        by_seq = {r["seq"]: r for r in run.rows}
        for r in sends:
            row = by_seq[r["decision_seq"]]
            self.assertEqual(row["kind"], "entry" if r["kind"] == "buy_sent" else "exit")
            self.assertEqual(row["order_id"], r["order_id"])
            if r["kind"] == "sell_sent":
                self.assertIsInstance(r["runner_silent_ms"], int)
                self.assertGreaterEqual(r["runner_silent_ms"], 0)


class ProofHasTeeth(unittest.TestCase):
    """A change the model does not know about must turn EQUAL into DIFF (each mutation touches the real bridge or the caps in this process only)."""

    def assertDiff(self, res):
        self.assertFalse(res.ok)
        self.assertEqual(res.record["result"], "DIFF")
        self.assertTrue(res.lines[-1].startswith("RESULT DIFF"))

    def test_loosened_max_open_is_a_diff(self):
        old = dict(c0.C0_LIMITS)
        try:
            c0.C0_LIMITS["max_open"] = 3
            self.assertDiff(P.run_proof())
        finally:
            c0.C0_LIMITS.update(old)

    def test_loosened_stake_cap_is_a_diff(self):
        old = dict(c0.C0_LIMITS)
        try:
            c0.C0_LIMITS["stake_lamports"] = 60_000_000
            res = P.run_proof()
            self.assertDiff(res)
            self.assertIn("60000000", "\n".join(res.lines))
        finally:
            c0.C0_LIMITS.update(old)

    def test_a_bridge_that_drops_exits_is_a_diff(self):
        with mock.patch.object(alb.Bridge, "handle_exit", lambda self, row: None):
            self.assertDiff(P.run_proof())

    def test_a_bridge_that_ignores_the_cap_on_the_stake_is_a_diff(self):
        def uncapped(row, cap=0):
            lam = int(round(float(row.get("stake_sol", row.get("size"))) * 1e9))
            return (lam, {}) if lam > 0 else (None, {"refused": "card_stake_invalid"})

        with mock.patch.object(c0, "c0_stake", uncapped):
            self.assertDiff(P.run_proof())

    def test_a_wrong_frozen_sha_pin_is_a_diff(self):
        with mock.patch.object(c0, "FROZEN_SHA", "0" * 40):
            res = P.run_proof()
        self.assertDiff(res)
        self.assertIn("FROZEN_SHA", "\n".join(res.lines))

    def test_a_dropped_decision_seq_is_a_diff(self):
        class NoSeq(alb.Bridge):
            def _log(self, kind, **kw):
                kw.pop("decision_seq", None)
                super()._log(kind, **kw)

        with mock.patch.object(alb, "Bridge", NoSeq):
            self.assertDiff(P.run_proof())


class ProofHousekeeping(unittest.TestCase):
    def test_window_dir_is_restored_and_no_window_file_is_left_on_the_host(self):
        before = c0.WINDOW_DIR
        P.run_proof()
        self.assertEqual(c0.WINDOW_DIR, before)
        self.assertFalse((Path(before) / f"{P.BOT_ID}.json").exists())

    def test_window_dir_is_restored_after_a_failure(self):
        before = c0.WINDOW_DIR
        with mock.patch.object(alb.Bridge, "tick", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                P.run_proof()
        self.assertEqual(c0.WINDOW_DIR, before)

    def test_temp_dir_is_removed(self):
        with tempfile.TemporaryDirectory() as base:
            with mock.patch.dict("os.environ", {"TMPDIR": base}):
                tempfile.tempdir = None
                try:
                    P.run_proof()
                finally:
                    tempfile.tempdir = None
            self.assertEqual(list(Path(base).iterdir()), [])

    def test_the_sealed_prefix_is_refused_as_a_temp_base_without_touching_it(self):
        self.assertEqual(P.tmp_base_refusal("/data/mal/arena"), "tmp_base_under_sealed_arena")
        self.assertEqual(P.tmp_base_refusal("/data/mal/arena/x/../y"), "tmp_base_under_sealed_arena")
        self.assertEqual(P.tmp_base_refusal("/data/mal/arena/../arena2"), None)
        self.assertIsNone(P.tmp_base_refusal("/tmp"))
        env = {"PATH": "/usr/bin:/bin", "TMPDIR": "/data/mal/arena/x"}
        p = subprocess.run([sys.executable, "-c", "import tempfile, sys; tempfile.tempdir = '/data/mal/arena/x'; from tools import hv4_c0_orders_proof as P; sys.exit(P.main([]))"],
                           capture_output=True, text=True, cwd=REPO, env=env)
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("REFUSED: tmp_base_under_sealed_arena", p.stdout)

    def test_cli_exit_code_lines_and_json(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "rec.json"
            p = subprocess.run([sys.executable, "-m", "tools.hv4_c0_orders_proof", "--json", str(out)], capture_output=True, text=True, cwd=REPO,
                               env={"PATH": "/usr/bin:/bin", "PROOF_GIT_SHA": "a" * 40})
            self.assertEqual(p.returncode, 0, p.stdout[-600:] + p.stderr[-600:])
            self.assertRegex(p.stdout, r"(?m)^bridge_orders_md5=[0-9a-f]{32}$")
            self.assertRegex(p.stdout, r"(?m)^expected_orders_md5=[0-9a-f]{32}$")
            self.assertRegex(p.stdout, r"(?m)^RESULT EQUAL ")
            self.assertIn("git_sha=" + "a" * 40, p.stdout)
            rec = json.loads(out.read_text())
            self.assertEqual((rec["result"], rec["git_sha"]), ("EQUAL", "a" * 40))

    def test_dry_proof_never_imports_the_solana_stack(self):
        code = ("import sys; from tools import hv4_c0_orders_proof as P; r = P.run_proof(); "
                "bad = [m for m in ('solders', 'solana', 'tools.probe_live', 'tools.h5_executor') if m in sys.modules]; print('BAD', bad) if bad else print('CLEAN', r.ok)")
        p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=REPO)
        self.assertIn("CLEAN True", p.stdout, p.stdout[-300:] + p.stderr[-300:])


class BridgeLogFieldScope(unittest.TestCase):
    def test_other_profiles_do_not_get_the_hv4_c0_log_keys(self):
        from tools.test_arena_live_bridge import Env, TmpCase  # noqa: F401  (the T0 profile fixtures)
        with tempfile.TemporaryDirectory() as d:
            e = Env(d)
            e.start()
            e.entry("o1")
            b = e.bridge()
            b.tick()
            e.port.events.append({"order_id": "o1", "event": "buy_landed", "landed_slot": 1002})
            b.tick()
            e.exit("o1")
            b.tick()
            for kind in ("buy_sent", "sell_sent"):
                row = e.log_rows(kind)[0]
                self.assertNotIn("decision_seq", row)
                self.assertNotIn("runner_silent_ms", row)

    def test_runner_silent_ms_is_the_same_base_as_the_runner_silent_rule(self):
        """runner_silent_ms > RUNNER_SILENT_MS exactly when c0.runner_silent_problem says runner_silent."""
        from tools.test_hv4_live_c0 import Hv4Env
        with tempfile.TemporaryDirectory() as d:
            e = Hv4Env(d)
            b = e.bridge()
            e.start()
            e.entry_sz("o1")
            b.tick()
            for gap, silent in ((c0.RUNNER_SILENT_MS, False), (c0.RUNNER_SILENT_MS + 1, True)):
                b._last_row_ms = b._started_ms + 5_000
                now = b._last_row_ms + gap
                self.assertEqual(b._runner_silent_ms(now), gap)
                self.assertEqual(c0.runner_silent_problem(b._last_row_ms, b._started_ms, now) is not None, silent)


if __name__ == "__main__":
    unittest.main()


class GapCaseTests(unittest.TestCase):
    """QP standing condition C-b (#1104 N5): the gap-row case runs under both FEED_GAP_MANUAL_REVIEW values and its refusal labels are inside the outcomes md5."""

    def test_both_modes_are_equal_with_their_own_labels_and_the_value_is_restored(self):
        from tools import hv4_c0_orders_proof as P
        from tools import hv4_live_c0 as c0x
        before = c0x.FEED_GAP_MANUAL_REVIEW
        res = P.run_proof()
        self.assertTrue(res.ok, "\n".join(res.lines))
        self.assertEqual(c0x.FEED_GAP_MANUAL_REVIEW, before)
        cases = {g["mode"]: g for g in res.record["gap_cases"]}
        self.assertEqual(set(cases), {"manual", "am21"})
        for g in cases.values():
            self.assertEqual((g["bo"], g["bq"]), (g["eo"], g["eq"]))
        self.assertEqual(cases["manual"]["labels"], {"latched:feed_break": 4, "order": 1})
        self.assertEqual(cases["am21"]["labels"], {"feed_gap": 1, "feed_gap_hold": 2, "order": 2})
        self.assertNotEqual(cases["manual"]["bq"], cases["am21"]["bq"])  # the labels are inside the md5
        self.assertEqual((res.record["bridge_orders_md5"], res.record["bridge_outcomes_md5"]),
                         ("e2ee70275ec0aae1791f3c410f76cc0f", "73c9fc148575796b5fdf11f6f18c8c8d"))  # the main scenario is unchanged

    def test_a_wrong_hold_in_the_model_is_a_diff(self):
        from unittest import mock
        from tools import hv4_c0_orders_proof as P
        with mock.patch.object(P, "M_GAP_HOLD_MS", 1_000):
            res = P.run_proof()
        self.assertFalse(res.ok)
        self.assertTrue(any(line.startswith("gap/am21") for line in res.lines))

    def test_a_mode_run_against_the_other_models_expectation_is_a_diff(self):
        from unittest import mock
        from tools import hv4_c0_orders_proof as P
        with mock.patch.object(P, "GAP_MODES", (("manual", False),)):  # the bridge holds (Am.21) but the model expects the latch
            res = P.run_proof()
        self.assertFalse(res.ok)
