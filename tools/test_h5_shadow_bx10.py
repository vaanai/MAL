"""bx10: the report-only paper exit variant of tools/h5_shadow.py (cell C10 of H5-BOOSTCLOCK-EXIT, FREEZE.md s2). Synthetic fixtures only."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import h5_shadow as h5
from tools.test_h5_shadow import POOL, SOL, SPS, Tape, announce, make_engine, ref_fill, types

FROZEN_RULE = "/data/mal/hunt-1008/iter-r2/h5-boostclock-exit/bc_rule.py"
REQUIRE_PARITY_ENV = "MAL_REQUIRE_BX10_PARITY"  # =1: the parity test FAILS (not skips) when the frozen rule or numpy is missing


class ProjectionTests(unittest.TestCase):
    def test_projection_e_hand_fixture(self):
        # R = 17.585 - 1.8 = 15.785 SOL; mean 0.6 SOL -> floor(26.31) = 26 slices left; median interval 12 s -> 34 + 26 * 12
        self.assertEqual(h5.project_e([10.0, 22.0, 34.0], [0.6e9] * 3), 346.0)

    def test_projection_e_median_of_an_even_number_of_intervals(self):
        # intervals 10, 20, 6, 4 -> median (6 + 10) / 2 = 8; R = 12.585 SOL, mean 1 SOL -> 12 left -> 40 + 96
        self.assertEqual(h5.project_e([0.0, 10.0, 30.0, 36.0, 40.0], [1e9] * 5), 136.0)
        # odd count: intervals 10, 20, 6 -> 10; R = 13.585 -> 13 left -> 36 + 130
        self.assertEqual(h5.project_e([0.0, 10.0, 30.0, 36.0], [1e9] * 4), 166.0)

    def test_budget_done_means_the_last_slice_was_seen(self):
        self.assertEqual(h5.project_e([0.0, 5.0, 9.0], [6e9, 6e9, 5.55e9]), 9.0)  # R = 0.035 SOL < 0.05
        self.assertEqual(h5.project_e([0.0, 5.0, 9.0], [6e9, 6e9, 6e9]), 9.0)  # overspent: R clipped at 0

    def test_exit_uses_only_slices_observed_1_35_s_earlier(self):
        t, a = [10.0 * i for i in range(18)], [1e9] * 18  # P_k = 160 for k <= 17 (17 slices left at k = 0); target 150
        tau, fired, k, proj = h5.bx10_exit(t, a, tmin=5.0, cap=330.0)
        self.assertEqual((tau, fired, k, proj), (150.0, True, 15, 160.0))  # the slice at 150 s is seen only at 151.35 s: not used
        tau0, _, k0, _ = h5.bx10_exit(t, a, tmin=5.0, cap=330.0, obs=0.0)
        self.assertEqual((tau0, k0), (150.0, 16))  # with no observation lag it would have been

    def test_exit_never_before_landing(self):
        t, a = [10.0 * i for i in range(18)], [1e9] * 18
        self.assertEqual(h5.bx10_exit(t, a, tmin=200.0, cap=330.0), (200.0, True, 18, 170.0))  # budget done at 170 s; decide at landing

    def test_exit_waits_for_the_target_inside_an_observation_gap(self):
        t, a = [0.0, 12.0, 24.0], [0.6e9] * 3  # P = 24 + 26 * 12 = 336 -> target 326, before the cap, no more slices
        self.assertEqual(h5.bx10_exit(t, a, tmin=30.0, cap=330.0), (326.0, True, 3, 336.0))
        self.assertEqual(h5.bx10_exit(t, a, tmin=30.0, cap=325.0), (325.0, False, 3, 336.0))  # target past the cap: v1's exit

    def test_slow_boost_hits_the_cap(self):
        t, a = [2.0 + 10.0 * i for i in range(30)], [0.5e9] * 30  # P = 342 at every k: target 332 > 330
        tau, fired, _, _ = h5.bx10_exit(t, a, tmin=5.0, cap=330.0)
        self.assertEqual((tau, fired), (330.0, False))

    def test_fewer_than_three_slices_is_v1(self):
        self.assertEqual(h5.bx10_exit([1.0, 13.0], [1e9, 1e9], tmin=5.0, cap=330.0), (330.0, False, 2, None))
        self.assertEqual(h5.bx10_exit([], [], tmin=5.0, cap=330.0), (330.0, False, 0, None))

    def test_parity_with_the_frozen_rule_on_random_fixtures(self):
        if not (os.path.exists(FROZEN_RULE) and importlib.util.find_spec("numpy")):
            if os.environ.get(REQUIRE_PARITY_ENV) == "1":
                self.fail(f"{REQUIRE_PARITY_ENV}=1 but the frozen rule {FROZEN_RULE} or numpy is missing: the parity check did NOT run")
            self.skipTest(f"frozen research rule or numpy not here (set {REQUIRE_PARITY_ENV}=1 to make this a failure)")
        spec = importlib.util.spec_from_file_location("bc_rule_frozen", FROZEN_RULE)
        bc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bc)
        rng = random.Random(1)
        fired = 0
        for _ in range(2000):
            sps = rng.choice((0.38, 0.4, 0.42))
            n = rng.randint(0, 32)
            slots, s = [], rng.randint(0, 20)
            for _ in range(n):
                slots.append(s)
                s += rng.choice((0, rng.randint(15, 45)))
            t = [x * sps for x in slots]
            a = [rng.randint(300_000_000, 1_200_000_000) for _ in range(n)]
            tmin = rng.uniform(0.0, 310.0)
            mine = h5.bx10_exit(t, a, tmin, 330.0)
            ref = bc.cad_exit(t, a, 10.0, 330.0, tmin)
            self.assertEqual((mine[0], mine[1]), (float(ref[0]), bool(ref[1])), (t, a, tmin))
            fired += mine[1]
        self.assertGreater(fired, 200)  # the fixtures exercise both branches

    def test_parity_fails_loudly_when_required_and_the_rule_is_missing(self):
        def run_parity(require: bool) -> unittest.TestResult:
            res = unittest.TestResult()
            with mock.patch.dict(os.environ), mock.patch.object(sys.modules[__name__], "FROZEN_RULE", "/nonexistent/bc_rule.py"):
                os.environ.pop(REQUIRE_PARITY_ENV, None)
                if require:
                    os.environ[REQUIRE_PARITY_ENV] = "1"
                ProjectionTests("test_parity_with_the_frozen_rule_on_random_fixtures").run(res)
            return res
        req = run_parity(True)
        self.assertEqual((len(req.failures), len(req.errors), len(req.skipped)), (1, 0, 0))
        self.assertIn(REQUIRE_PARITY_ENV, req.failures[0][1])
        opt = run_parity(False)
        self.assertEqual((len(opt.failures), len(opt.errors), len(opt.skipped)), (0, 0, 1))


def bx_tape(amt: int, n: int, extra: tuple = ()):
    """s0 = slot 1000 (sps 0.4). BOOST (the per-pool PDA) buys `amt` every 25 slots (10 s) from slot 1005; a dump at slot 1200 (80 s) triggers."""
    ev = [(1000, "buy", "A", SOL // 10)] + [(1005 + 25 * i, "buy", "PDA_" + POOL, amt) for i in range(n)]
    ev += [(1200, "sell", "DUMP", None), (1500, "sell", "Z", SOL // 2), (1900, "buy", "W", SOL)] + list(extra)
    t = Tape()
    for slot, side, trader, sol in sorted(ev, key=lambda e: e[0]):
        if sol is None:  # leave Q = real quote + V at 35 SOL
            sol = int(t.q + t.v - 35 * SOL)
        t.row(slot, side, trader, sol)
    return t


def run_tape(t: Tape, **kw):
    kw.setdefault("bx10_enabled", True)  # bx10 is off by default (EXP-026 flag); the tests of the variant itself switch it on
    eng, out = make_engine(**kw)
    announce(eng)
    for r in t.rows:
        eng.on_trade(r)
    eng.close_all("shutdown")
    return eng, out


def pre_after(t: Tape, slot: int):
    r = next(r for r in t.rows if r["slot"] > slot)
    return r["quote_reserve"] + r["virtual_quote_reserves"], r["base_reserve"]


class EngineTests(unittest.TestCase):
    def test_bx10_exit_from_live_slices_and_its_fill(self):
        t = bx_tape(SOL, 18)
        _, out = run_tape(t)
        bx = {r["variant"]: r for r in types(out, "outcome_bx10")}
        self.assertEqual(set(bx), {"pv", "fv"})
        r = bx["pv"]
        self.assertEqual((r["boost_id"], r["boost_src"], r["boost_src_at_trigger"], r["n_slices"]), ("PDA_" + POOL, "pda", "pda", 18))
        self.assertEqual(r["v1_exit_trigger_slot"], 1825)
        self.assertTrue(r["complete"])
        b = r["legs"]["binding"]  # landing 1200 + ceil(1.9 / 0.4) = 1205 (82 s); P = 162 s, target 152 s, seen with k = 15
        self.assertEqual((b["landing_slot"], b["fired"], b["k_slices"]), (1205, True, 15))
        self.assertAlmostEqual(b["tau_s"], 152.0, places=9)
        self.assertAlmostEqual(b["proj_last_slice_s"], 162.0, places=9)
        self.assertEqual((b["exit_trigger_slot"], b["exit_landing_slot"], b["same_as_v1"]), (1380, 1382, False))
        qe, be = pre_after(t, 1205)
        qx, bx_ = pre_after(t, 1382)
        self.assertAlmostEqual(b["q_sol"] * 1e9, qx, delta=1)
        for label, stake in h5.STAKES:
            pnl, gross = ref_fill(qe, be, qx, bx_, float(stake))
            self.assertAlmostEqual(b["net"][label]["pnl_nofail_lamports"], pnl, delta=1)
            self.assertAlmostEqual(b["net"][label]["gross"], gross, places=9)
        self.assertEqual(r["legs"]["primary"]["landing_slot"], 1204)

    def test_capped_bx10_is_exactly_v1(self):
        _, out = run_tape(bx_tape(SOL // 2, 30))
        v1 = {r["variant"]: r for r in types(out, "outcome")}
        for r in types(out, "outcome_bx10"):
            for leg, b in r["legs"].items():
                self.assertEqual((b["fired"], b["same_as_v1"], b["exit_trigger_slot"]), (False, True, 1825))
                self.assertEqual(b["net"], v1[r["variant"]]["legs"][leg]["net"])

    def test_no_boost_signer_is_v1(self):
        _, out = run_tape(bx_tape(SOL, 0))
        rs = types(out, "outcome_bx10")
        self.assertEqual(len(rs), 2)
        for r in rs:
            self.assertEqual((r["boost_id"], r["boost_src"], r["boost_src_at_trigger"], r["n_slices"]), (None, "none", "none", 0))
            self.assertTrue(all(b["same_as_v1"] and not b["fired"] for b in r["legs"].values()))

    def test_v1_records_are_byte_identical_with_and_without_bx10(self):
        for amt, n in ((SOL, 18), (SOL // 2, 30), (SOL, 0)):
            _, with_bx = run_tape(bx_tape(amt, n))
            with mock.patch.object(h5.Engine, "_resolve_bx10", lambda *a, **k: None):
                _, without = run_tape(bx_tape(amt, n))
            self.assertEqual([json.dumps(r, sort_keys=True) for r in with_bx if r["type"] != "outcome_bx10"],
                             [json.dumps(r, sort_keys=True) for r in without])
            self.assertEqual(len(types(with_bx, "outcome_bx10")), len(types(with_bx, "outcome")))

    def test_bx10_fault_does_not_touch_v1(self):
        def boom(*a, **k):
            raise RuntimeError("x")
        with mock.patch.object(h5.Engine, "_resolve_bx10", boom):
            eng, out = run_tape(bx_tape(SOL, 18))
        with mock.patch.object(h5.Engine, "_resolve_bx10", lambda *a, **k: None):
            eng2, ref = run_tape(bx_tape(SOL, 18))
        self.assertEqual([r for r in out if r["type"] != "outcome_bx10"], ref)
        self.assertEqual(dict(eng.counters), dict(eng2.counters))
        self.assertTrue(all("error" in r and r["boost_src_at_trigger"] == "pda" for r in types(out, "outcome_bx10")))

    def test_behavioural_pick_at_trigger_is_kept_when_the_pda_signs_later(self):
        # B: 7 buys of 1 SOL before the trigger (the behavioural pick); the PDA first buys 10 slots AFTER the trigger (slot 1200)
        b = tuple((1005 + 25 * i, "buy", "B", SOL) for i in range(7))
        pda = tuple((1210 + 25 * i, "buy", "PDA_" + POOL, SOL) for i in range(10))
        _, out = run_tape(bx_tape(SOL, 0, b + pda))
        trig = {r["variant"]: r for r in types(out, "trigger")}
        rs = types(out, "outcome_bx10")
        self.assertEqual(len(rs), 2)
        for r in rs:
            self.assertEqual((trig[r["variant"]]["boost_id"], trig[r["variant"]]["boost_src"]), ("B", "behavioural"))
            self.assertEqual((r["boost_id"], r["boost_src"], r["boost_src_at_trigger"], r["n_slices"]), ("B", "behavioural", "behavioural", 7))
            self.assertEqual([s for s, _, _ in r["slices"]], [5 + 25 * i for i in range(7)])  # B's slices only, none of the PDA's
        # the same tape with the old resolve-time switch would have used the PDA's 10 slices: the case is live
        eng, _ = make_engine()
        announce(eng)
        for row in bx_tape(SOL, 0, b + pda).rows:
            eng.on_trade(row)
        p = eng.pools[POOL]
        self.assertEqual(eng.boost_identity(p), ("PDA_" + POOL, "pda"))

    def test_pda_signing_after_a_none_trigger_is_used(self):
        # nobody qualifies at the trigger (src none); the PDA, a protocol address, starts buying after it: its slices are used
        pda = tuple((1210 + 25 * i, "buy", "PDA_" + POOL, SOL) for i in range(10))
        _, out = run_tape(bx_tape(SOL, 0, pda))
        rs = types(out, "outcome_bx10")
        self.assertEqual(len(rs), 2)
        for r in rs:
            self.assertEqual((r["boost_id"], r["boost_src"], r["boost_src_at_trigger"], r["n_slices"]), ("PDA_" + POOL, "pda", "none", 10))

    def test_cap_pick_sealed_pool_writes_no_bx10(self):
        _, out = run_tape(bx_tape(SOL, 18), seal_start_ms=0, suppress_outcome=lambda m: True)
        self.assertEqual((types(out, "outcome"), types(out, "outcome_bx10"), types(out, "trigger")), ([], [], []))

    def test_h5_look2_sealed_pool_writes_no_bx10_unless_observed(self):
        _, out = run_tape(bx_tape(SOL, 18), h5_look2_start_ms=0)
        self.assertEqual(len(types(out, "trigger")), 2)
        self.assertEqual((types(out, "outcome"), types(out, "outcome_bx10")), ([], []))
        with mock.patch.object(h5, "H5_LOOK2_END_MS", 10**15):  # the synthetic tape is in 2027, past the declared window's end
            _, out = run_tape(bx_tape(SOL, 18), h5_look2_start_ms=0, h5_look2_observed=True)
        self.assertEqual((len(types(out, "outcome")), len(types(out, "outcome_bx10"))), (2, 2))


TAPES = ((SOL, 18), (SOL // 2, 30), (SOL, 0))


class DefaultOffTests(unittest.TestCase):
    """bx10 is a declared flag (EXP-026), off by default: with it off the shadow does no bx10 work and every other record is unchanged."""

    def test_flag_off_writes_no_bx10_record_of_any_kind(self):
        eng, out = make_engine()  # no kwarg at all: the default
        self.assertIs(eng.bx10_enabled, False)
        announce(eng)
        for r in bx_tape(SOL, 18).rows:
            eng.on_trade(r)
        eng.close_all("shutdown")
        self.assertGreater(len(types(out, "outcome")), 0)  # v1 does produce outcomes on this tape: the absence below is the flag's doing
        self.assertFalse([r for r in out if "bx10" in r["type"] or r.get("rule") == "bx10"])
        self.assertFalse([k for k in eng.counters if "bx10" in k])

    def test_flag_off_computes_nothing(self):
        def boom(*a, **k):
            raise AssertionError("bx10 work with the flag off")
        with mock.patch.object(h5, "bx10_exit", boom), mock.patch.object(h5, "project_e", boom), \
                mock.patch.object(h5.Engine, "_bx10_slices", boom), mock.patch.object(h5.Engine, "_resolve_bx10", boom):
            for amt, n in TAPES:
                _, out = run_tape(bx_tape(amt, n), bx10_enabled=False)
                self.assertGreater(len(types(out, "outcome")), 0)
                self.assertEqual(types(out, "outcome_bx10"), [])  # a raise inside _resolve_bx10 would have left an error record

    def test_v1_records_and_counters_are_byte_identical_flag_off_vs_on(self):
        for amt, n in TAPES:
            eng_on, on = run_tape(bx_tape(amt, n), bx10_enabled=True)
            eng_off, off = run_tape(bx_tape(amt, n), bx10_enabled=False)
            self.assertEqual(len(types(on, "outcome_bx10")), len(types(on, "outcome")))
            self.assertEqual([json.dumps(r, sort_keys=True) for r in on if r["type"] != "outcome_bx10"], [json.dumps(r, sort_keys=True) for r in off])
            self.assertEqual(dict(eng_on.counters), dict(eng_off.counters))

    def test_only_the_literal_flag_turns_it_on(self):
        self.assertTrue(h5.bx10_flag("EXP-026"))
        self.assertEqual(h5.BX10_ENABLE_REF, "EXP-026")
        for bad in (None, "", "1", "0", "yes", "true", "True", "on", "exp-026", "EXP-026 ", " EXP-026", "EXP-026x", "EXP-026,x", "EXP-024-Am2", "EXP-027"):
            self.assertFalse(h5.bx10_flag(bad), repr(bad))  # a wrong value is off, not an error

    def test_the_engine_takes_the_boolean_only(self):
        for wrong in ("EXP-026", "1", 1, "true", None):
            self.assertIs(make_engine(bx10_enabled=wrong)[0].bx10_enabled, False, repr(wrong))
        self.assertIs(make_engine(bx10_enabled=True)[0].bx10_enabled, True)

    def test_the_command_line_parses_any_value_and_only_the_literal_is_on(self):
        ap = h5.build_parser()
        self.assertIsNone(ap.parse_args([]).bx10_enable)
        for v in ("EXP-026", "1", ""):
            self.assertEqual(ap.parse_args(["--bx10-enable", v]).bx10_enable, v)  # parsed, never refused (the engine flag is h5.bx10_flag(v))

    # ---- run_live: the start record names the state ----------------------------------------------------------------------------------
    def run_live(self, extra):
        d = tempfile.mkdtemp(dir="/tmp", prefix="h5-bx10-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        args = h5.build_parser().parse_args(["--out-dir", d, "--sockets", "3", *extra])
        built = []
        real = h5.Engine

        def spy(*a, **k):
            built.append(k.get("bx10_enabled"))
            return real(*a, **k)

        async def no_feed(*a, **k):
            return None

        with mock.patch.object(h5, "run_feed", no_feed), mock.patch.object(h5, "build_source", lambda *a, **k: None), mock.patch.object(h5, "Engine", spy):
            self.assertEqual(asyncio.run(h5.run_live(args)), 0)
        rows = [json.loads(line) for f in sorted(Path(d).glob("h5-shadow-*.jsonl")) for line in f.read_text().splitlines()]
        return [r for r in rows if r["type"] == "start"][0], built

    def test_the_start_record_names_the_flag_state_and_the_engine_follows_it(self):
        start, built = self.run_live([])
        self.assertIs(start["bx10"], False)
        self.assertEqual(built, [False])
        start, built = self.run_live(["--bx10-enable", "EXP-026"])
        self.assertIs(start["bx10"], True)
        self.assertEqual(built, [True])
        for wrong in ("1", "exp-026", "true"):
            with self.assertLogs("mal.h5_shadow", level="WARNING"):
                start, built = self.run_live(["--bx10-enable", wrong])
            self.assertIs(start["bx10"], False, wrong)
            self.assertEqual(built, [False], wrong)

    # ---- the wrapper -----------------------------------------------------------------------------------------------------------------
    def test_the_wrapper_passes_the_flag_only_when_set_and_says_what_is_on(self):
        d = tempfile.mkdtemp(dir="/tmp", prefix="h5-bx10-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        fake = Path(d) / "fakepy"
        fake.write_text('#!/bin/sh\nif [ "$1" = "-c" ]; then exit 0; fi\nprintf "ARGV:%s\\n" "$*"\n')
        fake.chmod(0o755)
        script = Path(__file__).resolve().parent.parent / "scripts" / "research" / "h5-shadow.sh"
        for shell in ("sh", "bash"):
            base = {"PATH": os.environ["PATH"], "HOME": d, "H5_OUT_DIR": d + "/out", "H5_PYTHON": str(fake)}
            r = subprocess.run([shell, str(script)], env=base, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, (shell, r.stderr))
            self.assertIn("bx10=off", r.stdout)
            self.assertNotIn("--bx10-enable", r.stdout.split("ARGV:")[1])
            for value, state in (("EXP-026", "on"), ("1", "off"), ("exp-026", "off")):
                r = subprocess.run([shell, str(script)], env={**base, "H5_BX10_ENABLE": value}, capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, (shell, value, r.stderr))  # a wrong value is not refused
                self.assertIn(f"bx10={state}", r.stdout, (shell, value))
                self.assertIn(f"--bx10-enable {value}", r.stdout.split("ARGV:")[1], (shell, value))
            r = subprocess.run([shell, str(script)], env={**base, "H5_BX10_ENABLE": ""}, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, (shell, r.stderr))
            self.assertIn("bx10=off", r.stdout)
            self.assertNotIn("--bx10-enable", r.stdout.split("ARGV:")[1])  # set but empty = not declared


if __name__ == "__main__":
    unittest.main()
