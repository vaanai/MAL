"""Tests for tools/exp016_screen.py. Fixtures only; nothing here opens a real data path (no /data/mal read)."""

from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
import warnings
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import tools.exp015_screen as e15
import tools.exp016_rug as rug
import tools.exp016_screen as x
from tools.exp012_fixtures import mint_tape

warnings.filterwarnings("ignore", category=FutureWarning)
SOL = 1_000_000_000
T0 = 1_790_000_000
NON_P1 = e15.non_p1_dates(True)
P1 = e15.block_dates("P1")
PLAN_TEXT = x.PLAN.read_text(encoding="utf-8")


# --- fixtures -------------------------------------------------------------------------------------------------------------------


def block_of(date):
    if date in P1:
        return "P1"
    if date in e15.p2_dates():
        return "P2"
    return "P3" if date <= "2026-09-08" else "P4"


def mk_row(i, date, *, rugged=False, net=1_000_000, filled=True, sel=True, held=0.0, dumps=0.0, exit_kind="cap", label191=False):
    feat = [0.0] * len(x.FEATURES18)
    feat[x.FEATURES18.index("launch_supply_held")] = held
    feat[x.FEATURES18.index("creator_prior_dumps")] = dumps
    mig = e15.date_start_ms(date) + 12 * 3_600_000 + i * 1000
    return {
        "mint": f"m-{date}-{i}", "date": date, "block": block_of(date), "source": "S", "mig_ms": mig, "pool": f"pool-{date}-{i}", "filled": filled, "sel": sel,
        "feat": feat, "flat": float(net), "press": float(net), "rug": bool(rugged) if filled else None, "rug70_1": False if filled else None, "event": "A" if rugged else None,
        "exit_kind": exit_kind, "label191": label191, "alt": {}, "alt_filled": {}, "deadline_ms": mig + 1_800_000,
    }


def good_book(dates=None, per_date=40, n_rug=2, rug_net=-30_000_000, ok_net=1_000_000):
    """A book where R1 isolates every RUG: rugs carry launch_supply_held 0.2 and lose 0.03 SOL; the rest earn 0.001."""
    out = []
    for d in dates or (NON_P1 + P1):
        for i in range(per_date):
            r = i < n_rug
            out.append(mk_row(i, d, rugged=r, net=rug_net if r else ok_net, held=0.2 if r else 0.0))
    return out


def vetoed_by(table, cid="r1"):
    return {r["mint"]: x.rule_veto(cid, r["feat"]) for r in table if r["sel"]}


# --- the doc-to-code pin ---------------------------------------------------------------------------------------------------------


class PlanPinTests(unittest.TestCase):
    def test_numbers_in_the_plan_match_the_module(self):
        t = PLAN_TEXT
        self.assertIn("**Cap:** 6 candidates", t)
        self.assertEqual(x.TRIES_CAP, 6)
        for k in ("exp016_r1..r4", "exp016_l5", "exp016_l10"):
            self.assertIn(k, t)
        self.assertEqual([c["key"] for c in x.CANDIDATES.values()], ["exp016_r1", "exp016_r2", "exp016_r3", "exp016_r4", "exp016_l5", "exp016_l10"])
        self.assertIn("`launch_supply_held ≥ 0.12`", t)
        self.assertIn("`serial_launch_held ≥ 0.12`", t)
        self.assertIn("`prior_dumper_held ≥ 0.12`", t)
        self.assertEqual(x.R_THRESHOLD, 0.12)
        self.assertIn("`creator_prior_dumps ≥ 1`", t)
        self.assertEqual(x.R4_MIN_DUMPS, 1)
        self.assertIn("veto the top 5%", t)
        self.assertIn("veto the top 10%", t)
        self.assertEqual(x.VETO_FRACTIONS, {"l5": 0.05, "l10": 0.10})
        self.assertIn("vetoes ≥ 30 filled trades and ≤ 20% of the frozen-selected filled trades", t)
        self.assertEqual((x.MIN_VETOED_FILLED, x.MAX_VETO_FRACTION), (30, 0.20))
        self.assertIn("above **0.15**", t)
        self.assertIn("fewer than **30** RUG trades", t)
        self.assertEqual((x.G1_MAX_RATE, x.G2_MIN_RUGS), (0.15, 30))
        self.assertIn("(lift ≥ 2)", t)
        self.assertEqual(x.LIFT_MIN, 2.0)
        self.assertIn("the **3 vetoed trades with the largest avoided losses**", t)
        self.assertEqual(x.TOP_AVOIDED, 3)
        self.assertIn("at most 20% of frozen-selected filled trades on every pinned date set", t)
        self.assertIn("max(1 bp of that print's quote reserve, 0.002 SOL)", t)
        self.assertEqual((x.V_TOL_BPS, x.V_TOL_LAMPORTS), (1.0, 2_000_000))
        self.assertIn("**more than 1%** of sampled pools disagree", t)
        self.assertEqual(x.V_DISAGREE_MAX, 0.01)
        self.assertIn("It must be over 99%", t)
        self.assertEqual(x.V_COVERAGE_MIN, 0.99)
        self.assertIn("35-minute purge", t)
        self.assertEqual(x.PURGE_MIN, 35)
        self.assertIn("1,000 draws, seed 1", t)
        self.assertEqual((x.BOOT_DRAWS, x.BOOT_SEED), (1000, 1))
        self.assertIn("505,000 lamports per side", t)
        self.assertEqual(x.FEE, 505_000)
        self.assertIn("0.0042038", t)
        self.assertAlmostEqual(x.HAIRCUT_FACTOR, 0.0042038, places=7)
        self.assertIn("k = 6;", t)
        self.assertIn("0.05 SOL;", t)
        self.assertEqual((x.K, x.SIZE_SOL, x.EXIT_LAG), (6, 0.05, 2))
        self.assertIn("model md5 `a1810d219ed61db64a396f40dc302ce5`", t)
        self.assertIn("threshold 0.8030766588450794", t)
        self.assertEqual(x.e15.FROZEN_THRESHOLD, 0.8030766588450794)
        self.assertIn("C = 0.5, L2", t)
        self.assertEqual(x.LOGREG["C"], 0.5)
        self.assertIn("−(size + both fees)", t)
        self.assertEqual(x.TL_NET, -(50_000_000 + 2 * 505_000))
        self.assertIn("index = round((1 − f)·(n − 1))", t)
        self.assertIn("`mean x > 0`" if False else "mean x > 0", t)

    def test_date_counts(self):
        self.assertEqual((len(e15.pool_dates(True)), len(e15.non_p1_dates(True))), (36, 27))
        self.assertEqual((len(e15.pool_dates(False)), len(e15.non_p1_dates(False))), (30, 21))

    def test_logit_inputs_are_the_18_features(self):
        self.assertEqual(len(x.FEATURES18), 18)
        self.assertTrue(set(x.LOG1P_FEATURES) <= set(x.FEATURES18))


# --- guards ----------------------------------------------------------------------------------------------------------------------


class GuardTests(unittest.TestCase):
    def test_placeholder_pin_refuses_at_startup(self):
        self.assertEqual(x.VMAP_EXP016_SHA256, "PENDING")
        with self.assertRaises(x.Refused):
            x.check_pin_ready()
        with mock.patch.object(x, "VMAP_EXP016_SHA256", "a" * 64):
            x.check_pin_ready()  # a real sha passes

    def test_run_guards_refuses_before_reading_anything_while_pending(self):
        args = SimpleNamespace(max_workers=4)
        with self.assertRaises(x.Refused):
            x.run_guards(args)

    def test_vmap_sha_mismatch_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "m.json"
            p.write_text(json.dumps({"v": {"a": 1}}))
            with mock.patch.object(x, "VMAP_EXP016_SHA256", "b" * 64):
                with self.assertRaises(x.Refused):
                    x.check_vmap(p)
            import hashlib

            with mock.patch.object(x, "VMAP_EXP016_SHA256", hashlib.sha256(p.read_bytes()).hexdigest()):
                self.assertEqual(x.check_vmap(p), hashlib.sha256(p.read_bytes()).hexdigest())

    def test_reserved_paths_are_refused_by_the_shared_guard(self):
        with self.assertRaises(e15.Refused):
            e15.refuse_reserved("/data/mal/blocks/fresh-0808/w1")

    def test_prior_exp016_line_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "tries.jsonl"
            log.write_text(json.dumps({"config": {"key": "exp015_c1"}}) + "\n")
            x.check_no_prior_tries(log)  # other families do not matter
            log.write_text(json.dumps({"config": {"key": "exp016_started"}}) + "\n")
            with self.assertRaises(x.Refused):
                x.check_no_prior_tries(log)

    def test_v_constancy_refuses_over_one_percent_and_small_samples(self):
        vmap = {f"p{i}": 17_584_000_000 for i in range(200)}
        ok = [{"pool": f"p{i}", "v_implied": 17_584_000_000 + 1_000_000, "quote_reserve": 10 * SOL} for i in range(200)]  # 0.001 SOL < 0.002 SOL
        self.assertEqual(x.check_v_constancy(ok, vmap)["n_disagree"], 0)
        two = [dict(s) for s in ok]
        for s in two[:2]:
            s["v_implied"] += 5_000_000  # 0.005 SOL: disagrees; 2 of 200 = 1.0% is not MORE than 1%
        self.assertEqual(x.check_v_constancy(two, vmap)["n_disagree"], 2)
        three = [dict(s) for s in ok]
        for s in three[:3]:
            s["v_implied"] += 5_000_000
        with self.assertRaises(x.Refused):
            x.check_v_constancy(three, vmap)
        with self.assertRaises(x.Refused):
            x.check_v_constancy(ok[:50], vmap)
        # the tolerance is the larger of 1 bp of the quote reserve and 0.002 SOL
        big = [dict(s, quote_reserve=500 * SOL, v_implied=17_584_000_000 + 40_000_000) for s in ok]  # 1 bp of 500 SOL = 0.05 SOL > 0.04 SOL diff
        self.assertEqual(x.check_v_constancy(big, vmap)["n_disagree"], 0)
        # a pool with no readable stored V is not a disagreement (priced by the section 4 rule) but it is not CHECKED either: the floor and the
        # 1% rate use n_checked, so an all-unreadable sample refuses
        none_map = {k: None for k in vmap}
        with self.assertRaises(x.Refused):
            x.check_v_constancy(three, none_map)

    def test_v_constancy_floor_and_rate_use_n_checked(self):
        vmap = {f"p{i}": 17_584_000_000 for i in range(300)}
        ok = [{"pool": f"p{i}", "v_implied": 17_584_000_000, "quote_reserve": 10 * SOL} for i in range(300)]
        part = {**vmap, **{f"p{i}": None for i in range(200, 300)}}  # 200 checked, 100 unreadable
        self.assertEqual(x.check_v_constancy(ok, part)["n_checked"], 200)
        part199 = {**vmap, **{f"p{i}": None for i in range(199, 300)}}  # 199 checked of 300: below the floor
        with self.assertRaises(x.Refused):
            x.check_v_constancy(ok, part199)
        bad3 = [dict(s, v_implied=s["v_implied"] + 5_000_000) if i < 3 else s for i, s in enumerate(ok)]
        self.assertEqual(x.check_v_constancy(bad3, vmap)["n_disagree"], 3)  # 3/300 = 1.0%: passes on the sample size ...
        with self.assertRaises(x.Refused):
            x.check_v_constancy(bad3, part)  # ... but 3/200 = 1.5% of the checked pools refuses

    def test_constancy_file_must_be_the_seeded_p2_sample(self):
        p2 = [f"pool{i}" for i in range(1000)]
        good = [{"pool": p, "v_implied": 1} for p in x.sample_pools(p2)]
        x.check_constancy_sample(good, p2)
        with self.assertRaises(x.Refused):
            x.check_constancy_sample(good[:-1] + [{"pool": "pool_not_in_the_draw", "v_implied": 1}], p2)
        with self.assertRaises(x.Refused):
            x.check_constancy_sample(good[:-1], p2)
        with self.assertRaises(x.Refused):
            x.check_constancy_sample(good + good[:1], p2)  # duplicates

    def test_sample_accepts_exactly_primary_plus_first_k_reserve(self):
        pop = [f"pool{i}" for i in range(1000)]
        prim, res = x.sample_pools(pop), x.reserve_pools(pop, x.sample_pools(pop))
        self.assertEqual((len(res), len(set(res) & set(prim))), (20, 0))
        self.assertEqual(res, x.reserve_pools(pop, prim))
        rows = [{"pool": p, "v_implied": 1} for p in prim]
        for k in (0, 1, 3):
            rs = [dict(r, v_implied=None) if i < k else r for i, r in enumerate(rows)] + [{"pool": p, "v_implied": 1} for p in res[:k]]
            x.check_constancy_sample(rs, pop)
            with self.assertRaises(x.Refused):  # one reserve too many
                x.check_constancy_sample(rs + [{"pool": res[k], "v_implied": 1}], pop)
            if k:
                with self.assertRaises(x.Refused):  # one reserve too few
                    x.check_constancy_sample(rs[:-1], pop)
                with self.assertRaises(x.Refused):  # a reserve out of order
                    x.check_constancy_sample(rs[:-k] + [{"pool": p, "v_implied": 1} for p in res[1:k + 1]], pop)

    def test_v_coverage_counts_only_readable_pools(self):
        vmap = {f"p{i}": (0 if i % 2 else -5) for i in range(200)}  # <= 0 is readable (vault-only)
        self.assertEqual(x.v_coverage(vmap, vmap)["coverage"], 1.0)
        vmap2 = dict(vmap)
        for i in range(3):
            vmap2[f"p{i}"] = None
        with self.assertRaises(x.Refused):
            x.v_coverage(list(vmap2), vmap2)  # 98.5% is not over 99%
        vmap2["p0"] = 5
        vmap2["p1"] = 5
        self.assertGreater(x.v_coverage(list(vmap2), vmap2)["coverage"], 0.99)

    def test_sample_pools_is_deterministic(self):
        pools = [f"p{i}" for i in range(1000)]
        self.assertEqual(x.sample_pools(pools), x.sample_pools(list(reversed(pools))))
        self.assertEqual(len(x.sample_pools(pools)), x.V_SAMPLE_SIZE)


# --- tries protocol ---------------------------------------------------------------------------------------------------------------


class TriesTests(unittest.TestCase):
    def _log(self, d, n_p1=3, n_p2=2):
        log = Path(d) / "tries.jsonl"
        lines = []
        for _ in range(n_p1):
            lines.append({"config": {"key": "old"}, "data_blocks": [{"start_hour": "2026-09-19T01", "end_hour_exclusive": "2026-09-22T00"}]})
        for _ in range(n_p2):
            lines.append({"config": {"key": "old2"}, "data_blocks": [{"start_hour": "2026-08-14T12", "end_hour_exclusive": "2026-08-28T12"}]})
        lines.append({"config": {"key": "exp016_r1"}, "data_blocks": [{"start_hour": "2026-09-19T01", "end_hour_exclusive": "2026-09-22T00"}]})
        log.write_text("".join(json.dumps(r) + "\n" for r in lines))
        return log

    def test_prior_counts_are_read_per_pool_and_exclude_exp016(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(x.prior_tries_per_pool(self._log(d), True), {"P1": 3, "P2": 2, "P3": 0, "P4": 0})
            self.assertEqual(set(x.prior_tries_per_pool(self._log(d), False)), {"P1", "P2", "P3"})

    def test_prior_counts_of_the_canonical_log_are_at_least_the_plan_figures(self):
        c = x.prior_tries_per_pool(x.CANONICAL_TRIES, True)
        self.assertGreaterEqual(c["P1"], 74)
        self.assertGreaterEqual(c["P2"], 6)

    def test_started_is_one_line_with_six_keys_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            out, log = Path(d) / "out", Path(d) / "t.jsonl"
            extra = {"universe_sha256": "u", "feature_table_sha256": "f", "prior_tries_per_pool": {"P1": 74}}
            r1 = x.log_all(out, log, log, "started", True, extra)
            r2 = x.log_all(out, log, log, "started", True, extra)
            lines = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual((r1["logged"], r2["logged"], len(lines)), (1, 0, 1))
            c = lines[0]["config"]
            self.assertEqual((c["key"], c["status"], c["universe_sha256"], c["feature_table_sha256"], c["prior_tries_per_pool"]), (x.STARTED_KEY, "started", "u", "f", {"P1": 74}))
            self.assertEqual(c["keys"], [v["key"] for v in x.CANDIDATES.values()])
            self.assertEqual(lines[0]["role"], "exploration")
            with self.assertRaises(x.Refused):  # a second run on this log refuses
                x.check_no_prior_tries(log)

    def test_results_count_each_candidate_on_every_pool_it_touches(self):
        with tempfile.TemporaryDirectory() as d:
            out, log = Path(d) / "out", Path(d) / "t.jsonl"
            x.log_all(out, log, log, "completed", True, {})
            lines = [json.loads(line)["config"] for line in log.read_text().splitlines()]
            self.assertEqual(len(lines), 6 * 4)  # +6 per pool
            self.assertEqual({(c["key"], c["pool_group"]) for c in lines}, {(v["key"], g) for v in x.CANDIDATES.values() for g in ("P1", "P2", "P3", "P4")})
            self.assertEqual(x.log_all(out, log, log, "completed", True, {})["logged"], 0)
            # an aborted status never follows a completed line for the same candidate
            self.assertEqual(x.log_all(out, log, log, "aborted_after_read", True, {})["logged"], 0)

    def test_without_p4_three_pools(self):
        with tempfile.TemporaryDirectory() as d:
            out, log = Path(d) / "out", Path(d) / "t.jsonl"
            x.log_all(out, log, log, "completed", False, {})
            self.assertEqual(len(log.read_text().splitlines()), 6 * 3)

    def test_run_lock_without_record_refuses_and_stale_lock_clears(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            e15.take_lock(out, "head", "h")
            with self.assertRaises(e15.Refused):
                e15.check_run_lock(out)
            with self.assertRaises(FileExistsError):
                e15.take_lock(out, "head", "h")
            e15.write_record(out, "aborted_after_read", False, {})
            e15.check_run_lock(out)  # not started: stale, cleared
            self.assertFalse((out / e15.OUT_LOCK).exists())


# --- tape: admission, the single-mint simulation, P2, one merged map -------------------------------------------------------------


def fixture_source(pool_of_prints="POOL1", foreign_first=False, block="P1"):
    creates, trades = mint_tape("MINTA", T0)
    for t in trades:
        if t["venue"] == "pumpswap":
            t["pool"] = pool_of_prints
    if foreign_first:
        mig = next(t for t in trades if t["venue"] == "pumpswap")
        foreign = dict(mig, pool="FOREIGN", signature="sig-foreign", t_recv_ms=mig["t_recv_ms"] - 500, slot=mig["slot"] - 1)
        trades.insert(trades.index(mig), foreign)
    mig_row = {"type": "migration", "mint": "MINTA", "pool": "POOL1", "slot": 1050}
    return x.SourceData("P1A", block, {"MINTA": creates[0]}, {"MINTA": mig_row}, {"MINTA": trades}, (T0 + 4000) * 1000)


VMAP = {"POOL1": 17_584_000_000, "FOREIGN": 17_584_000_000}


class TapeTests(unittest.TestCase):
    def test_admission_is_run_holdouts(self):
        rows = [{"block_time": 5}, {"block_time": None}, {"block_time": "7"}, {"block_time": 9, "t_recv_ms": 9500}, {}]
        got = x.admit_rows(rows)
        self.assertEqual([r["t_recv_ms"] for r in got], [5000, 9500])
        self.assertNotIn("t_recv_ms", rows[0])  # the tape rows are not edited

    def test_single_mint_cell_is_filled_labelled_and_featured(self):
        src = fixture_source()
        r = x.process_source(src, VMAP)
        self.assertEqual(r["no_pool_mints"], [])
        (c,) = r["cells"]
        self.assertEqual(c["status"], "FILLED")
        self.assertEqual(c["label"]["status"], "LABELLED")
        self.assertFalse(c["label"]["rug"])
        self.assertEqual(c["exit_kind"], "cap")
        self.assertEqual(set(c["features"]), set(x.FEATURES18))
        self.assertEqual(len(c["exp012_features"]), len(x.fz.FROZEN_FEATURE_NAMES))
        self.assertEqual(set(c["cells"]), set(x.CELL_KEYS))
        self.assertEqual(c["date"], e15.utc_date(c["mig_ms"]))

    def test_censored_when_the_tape_ends_before_the_deadline(self):
        src = fixture_source()
        src.through_ms = (T0 + 100) * 1000
        # drop the late print so the 30-minute cap runs past the tape end
        src.rows_by_mint["MINTA"] = [t for t in src.rows_by_mint["MINTA"] if t["signature"] != "sig-later-MINTA"]
        (c,) = x.process_source(src, VMAP)["cells"]
        self.assertEqual(c["status"], "CENSORED")
        self.assertFalse(x.in_book(c))
        self.assertNotIn("label", c)

    def test_p2_simulator_is_fed_migration_pool_rows_only(self):
        src = fixture_source(foreign_first=True)
        seen = []
        real = x.make_wrapper

        def spy(orig, vmap, mcap_mode="v"):
            w = real(orig, vmap, mcap_mode)

            def wrapped(row):
                if row.get("venue") == "pumpswap":
                    seen.append(row.get("pool"))
                return w(row)

            return wrapped

        with mock.patch.object(x, "make_wrapper", spy):
            r = x.process_source(src, VMAP)
        self.assertTrue(seen)
        self.assertEqual(set(seen), {"POOL1"})  # the FOREIGN print never reaches the simulator
        self.assertEqual(r["gate"]["foreign_first_mints"], ["MINTA"])
        self.assertEqual(r["gate"]["mints_with_foreign_pool_prints"], 1)

    def test_p2_gate_refuses_when_the_fed_rows_leak(self):
        src = fixture_source(foreign_first=True)
        with mock.patch.object(rug, "restrict_rows_to_migration_pool", lambda rows, pool: iter(rows)):
            with self.assertRaises(rug.PoolAttributionRefusal):
                x.process_source(src, VMAP)

    def test_no_pool_mint_is_excluded_and_counted(self):
        src = fixture_source()
        src.migrations["MINTA"] = {"type": "migration", "mint": "MINTA", "slot": 1050}  # no pool
        r = x.process_source(src, VMAP)
        self.assertEqual((r["cells"], r["no_pool_mints"]), ([], ["MINTA"]))

    def test_one_merged_map_goes_to_the_label_and_the_adapter(self):
        src = fixture_source()
        vmap_raw = {"POOL1": None}
        merged = rug.merge_v_map(vmap_raw, {"POOL1": 17_584_000_000})
        got = {}
        real_wrap, real_label = x.make_wrapper, rug.label_trade

        def spy_wrap(orig, vmap, mcap_mode="v"):
            got["wrapper"] = vmap
            return real_wrap(orig, vmap, mcap_mode)

        def spy_label(*a, **kw):
            got["label"] = kw["vmap"]
            return real_label(*a, **kw)

        with mock.patch.object(x, "make_wrapper", spy_wrap), mock.patch.object(rug, "label_trade", spy_label):
            x.process_source(src, merged)
        self.assertIs(got["wrapper"], got["label"])
        self.assertEqual(got["label"]["POOL1"], 17_584_000_000)

    def test_drift_between_the_mirrored_exit_and_the_simulator_refuses(self):
        src = fixture_source()
        real = x.tpsl_exit

        def bad(*a, **kw):
            e = real(*a, **kw)
            return {**e, "state_idx": 0}  # a different print than the simulator filled against

        with mock.patch.object(x, "tpsl_exit", bad):
            with self.assertRaises(x.SimulatorDrift):
                x.process_source(src, VMAP)

    def test_pre_started_counts_are_outcome_blind(self):
        src = fixture_source(foreign_first=True)
        r = x.process_source(src, {**VMAP, "POOL1": None})
        sel = {"MINTA": True}
        pc = x.pre_started_counts([r], sel, {"POOL1": None}, closed=["POOL1"])
        self.assertEqual(pc["closed_pools_frozen_selected"], ["POOL1"])
        self.assertEqual(pc["parse_fail_pools_frozen_selected"], [])
        pc2 = x.pre_started_counts([r], sel, {"POOL1": None}, closed=[])
        self.assertEqual(pc2["parse_fail_pools_frozen_selected"], ["POOL1"])
        self.assertEqual(pc["foreign_first_mints"], {"P1A": 1})
        self.assertEqual(pc["slot_inversions"], {"P1A": 0})
        self.assertEqual(pc["censored_cells"], 0)
        flat = json.dumps(pc, default=str)
        for banned in ("net0", "press", "rug", "flat"):
            self.assertNotIn(banned, flat.replace("foreign_pool_prints", ""))

    def test_loader_reads_plain_hour_files(self):
        creates, trades = mint_tape("MINTB", T0)
        with tempfile.TemporaryDirectory() as d:
            tp, cp = Path(d) / "trades.jsonl", Path(d) / "creates.jsonl"
            tp.write_text("".join(json.dumps(t) + "\n" for t in trades))
            cp.write_text("".join(json.dumps(c) + "\n" for c in creates))
            src = x.load_source_data("P3", "P3", lambda h: {"trade": tp, "create": cp}, ["h0"], [])
        self.assertEqual(set(src.creates), {"MINTB"})
        self.assertEqual(len(src.rows_by_mint["MINTB"]), len([t for t in trades if t["venue"] == "pump_bonding"]))  # never migrated here: only its curve rows are kept
        self.assertEqual(src.through_ms, max(t["t_recv_ms"] for t in trades))


# --- G1, G2 and silent-pool counting AFTER started ---------------------------------------------------------------------------------


class GuardsAfterStartedTests(unittest.TestCase):
    def _table(self, rate_rugs, n_dates_non=27, per_date=10):
        t = []
        k = 0
        for d in (NON_P1[:n_dates_non] + P1):
            for i in range(per_date):
                t.append(mk_row(i, d, rugged=(k < rate_rugs)))
                k += 1
        return t

    def test_g1_too_broad_above_015_on_all_dates(self):
        tb = self._table(rate_rugs=60)  # 60 of 360 = 16.7% > 15%
        g = x.label_guards(tb, True)
        self.assertTrue(g["g1_too_broad"])
        self.assertEqual(g["outcome"], x.OUTCOME_G1)
        tb = self._table(rate_rugs=54)  # exactly 15%: not above
        self.assertFalse(x.label_guards(tb, True)["g1_too_broad"])

    def test_g2_too_rare_below_30_on_the_non_p1_dates(self):
        tb = self._table(rate_rugs=29)  # all in P2.. first dates (non-P1 listed first)
        g = x.label_guards(tb, True)
        self.assertTrue(g["g2_too_rare"])
        self.assertEqual(g["outcome"], x.OUTCOME_G2)
        self.assertFalse(x.label_guards(self._table(rate_rugs=30), True)["g2_too_rare"])

    def test_rug_counts_use_filled_selected_only(self):
        tb = self._table(rate_rugs=40)
        tb.append(mk_row(99, NON_P1[0], rugged=True, sel=False))
        tb.append(mk_row(98, NON_P1[0], filled=False))
        g = x.label_guards(tb, True)
        self.assertEqual(g["n_rug_selected_filled_non_p1"], 40)

    def test_silent_pool_cells_are_counted_with_the_deadline_rule(self):
        d = NON_P1[0]
        cells = [
            {"mint": "a", "status": "FILLED", "block": block_of(d), "mig_ms": e15.date_start_ms(d) + 13 * 3_600_000, "pool": "pa", "deadline_ms": 10_000_000},
            {"mint": "b", "status": "FILLED", "block": block_of(d), "mig_ms": e15.date_start_ms(d) + 13 * 3_600_000, "pool": "pb", "deadline_ms": 10_000_000},
            {"mint": "c", "status": "MISS", "block": block_of(d), "mig_ms": e15.date_start_ms(d) + 13 * 3_600_000, "pool": "pc"},
        ]
        prints = {"pa": [9_950_000], "pb": [9_900_000, 5_000_000]}  # pa prints inside the last 60 s; pb's last print is 100 s before
        self.assertEqual(x.silent_pool_ids(cells, prints), ["b"])
        self.assertEqual(x.silent_pool_ids(cells, {"pa": [9_940_000], "pb": [10_000_000]}), [])  # exactly 60 s before and the deadline itself count

    def test_guards_are_computed_before_any_candidate_is_scored(self):
        order = []
        tb = good_book(per_date=20, n_rug=2)
        with mock.patch.object(x, "nested_lodo", lambda *a, **k: order.append("nested") or (_ for _ in ()).throw(RuntimeError("stop"))):
            with self.assertRaises(RuntimeError):
                x.run_screen(tb, [], {}, set(), True, on_guards=lambda g: order.append("guards"))
        self.assertEqual(order, ["guards", "nested"])

    def test_a_guard_stop_scores_nothing(self):
        tb = good_book(per_date=10, n_rug=5)  # 50% rate
        with mock.patch.object(x, "nested_lodo", side_effect=AssertionError("must not run")):
            res = x.run_screen(tb, [], {}, set(), True)
        self.assertFalse(res["decision"]["passes"])
        self.assertEqual(res["decision"]["outcome"], x.OUTCOME_G1)
        self.assertTrue(res["decision"]["family_closed"])


# --- bars on constructed books -----------------------------------------------------------------------------------------------------


class BarTests(unittest.TestCase):
    def setUp(self):
        self.table = good_book()
        self.veto = vetoed_by(self.table)

    def test_all_bars_pass_on_a_book_where_the_veto_isolates_the_rugs(self):
        b = x.bars(self.table, self.veto, True)
        self.assertTrue(b["passes"], json.dumps(b, default=str)[:600])
        for leg in x.LEGS:
            self.assertTrue(b[leg]["all"])
        self.assertAlmostEqual(b["S3"]["lift"], 20.0)
        self.assertEqual(b["S3"]["rug_recall"], 1.0)

    def test_s1_fails_when_the_mean_is_not_positive(self):
        veto = {r["mint"]: not r["rug"] for r in self.table if r["sel"]}  # veto only the winners
        b = x.bars(self.table, veto, True)
        self.assertFalse(b["press"]["S1"]["pass"])

    def test_s1_needs_both_resamplers(self):
        # mean x > 0 but noisy: one huge avoided loss on one date among many zero dates -> date-cluster CI lower bound is 0
        tb = [mk_row(i, d, net=0) for d in NON_P1 for i in range(10)]
        tb[0] = mk_row(0, NON_P1[0], rugged=True, net=-500_000_000)
        veto = {tb[0]["mint"]: True}
        b = x.bars(tb, veto, True)
        self.assertGreater(b["press"]["S1"]["mean_x_sol_non_p1"], 0)
        self.assertFalse(b["press"]["S1"]["pass"])

    def test_s2_a_date_with_no_vetoed_filled_trade_is_not_positive(self):
        veto = dict(self.veto)
        for r in self.table:  # remove the veto on the dates after the first 13: only 13 of 27 positive
            if r["date"] in NON_P1[13:]:
                veto[r["mint"]] = False
        b = x.bars(self.table, veto, True)
        self.assertEqual(b["press"]["S2"]["dates_positive"], 13)
        self.assertFalse(b["press"]["S2"]["pass"])  # 13 of 27 is not more than half
        for r in self.table:
            if r["date"] in NON_P1[13:14]:
                veto[r["mint"]] = self.veto[r["mint"]]
        self.assertTrue(x.bars(self.table, veto, True)["press"]["S2"]["pass"])  # 14 of 27
        # a vetoed MISS scores 0, so it never makes a date positive
        miss = mk_row(500, NON_P1[0], filled=False, sel=True)
        b2 = x.bars(self.table + [miss], {**veto, miss["mint"]: True}, True)
        self.assertEqual(b2["press"]["S2"]["dates_positive"], 14)

    def test_s3_lift_must_be_at_least_two(self):
        tb = [mk_row(i, d, rugged=(i < 4), net=-30_000_000 if i < 4 else 1_000_000) for d in NON_P1 for i in range(40)]  # base rate 10%
        veto = {r["mint"]: (r["rug"] or (r["date"] == NON_P1[0] and r["flat"] > 0)) for r in tb}
        veto = {k: v for k, v in veto.items()}
        # veto all 4 rugs per date plus 36 winners on one date: precision (108+0)/(108+36) -> lift below 2? compute
        b = x.bars(tb, veto, True)
        self.assertEqual(b["S3"]["pass"], b["S3"]["lift"] is not None and b["S3"]["lift"] >= 2.0)
        low = {r["mint"]: (i % 5 == 0) for i, r in enumerate(tb)}  # random 20%: lift ~ 1
        self.assertFalse(x.bars(tb, low, True)["S3"]["pass"])
        none = {r["mint"]: False for r in tb}
        self.assertFalse(x.bars(tb, none, True)["S3"]["pass"])  # nothing vetoed: no lift

    def test_s3_reports_tp_exits_precision_and_recall(self):
        tb = good_book(dates=NON_P1, per_date=40, n_rug=2)
        tb[0] = dict(tb[0], exit_kind="tp", flat=40_000_000.0, press=40_000_000.0, rug=False)  # a vetoed winner that exited at the tp
        veto = vetoed_by(tb)
        s3 = x.bars(tb, veto, True)["S3"]
        self.assertEqual(s3["n_vetoed_tp_exits"], 1)
        self.assertAlmostEqual(s3["vetoed_tp_total_sol"]["flat"], 0.04)
        self.assertAlmostEqual(s3["rug_precision"], 53 / 54)
        self.assertEqual(s3["rug_recall"], 53 / 53)

    def test_s4_three_rugs_are_not_enough(self):
        tb = [mk_row(i, d, net=0) for d in NON_P1 for i in range(10)]
        for j, d in enumerate(NON_P1[:3]):  # three vetoed rugs, everything else zero
            tb[j * 10] = mk_row(0, d, rugged=True, net=-30_000_000, held=0.2)
        veto = {r["mint"]: r["rug"] for r in tb}
        b = x.bars(tb, veto, True)
        self.assertLessEqual(b["press"]["S4"]["ex_top3_avoided_sol"], 0)
        self.assertFalse(b["press"]["S4"]["pass"])
        # four vetoed trades, but all of the gain is on one date: it survives removing the top 3 trades, not removing the best date
        tb = [mk_row(i, d, net=0) for d in NON_P1 for i in range(10)]
        for j in range(4):
            tb[j] = mk_row(j, NON_P1[0], rugged=True, net=-100_000_000)
            tb[10 + j] = mk_row(j, NON_P1[1], net=1_000_000)
        veto = {r["mint"]: (r["date"] in NON_P1[:2] and (r["rug"] or r["flat"] > 0)) for r in tb}
        s4 = x.bars(tb, veto, True)["press"]["S4"]
        self.assertGreater(s4["ex_top3_avoided_sol"], 0)
        self.assertLess(s4["ex_best_date_sol"], 0)
        self.assertFalse(s4["pass"])

    def test_s5_veto_size_cap_on_every_pinned_set(self):
        veto = dict(self.veto)
        b = x.bars(self.table, veto, True)
        self.assertTrue(b["S5"]["pass"])
        self.assertEqual(set(b["S5"]["by_set"]), {"all", "non_p1", "P1", "P2", "P3", "P4"})
        # 21% of the filled trades on ONE pool (P1) fails S5 even if every other set is fine
        for r in [r for r in self.table if r["block"] == "P1"][:200]:
            veto[r["mint"]] = True
        b = x.bars(self.table, veto, True)
        self.assertFalse(b["S5"]["by_set"]["P1"]["pass"])
        self.assertFalse(b["S5"]["pass"])
        self.assertFalse(b["passes"])

    def test_s6_kept_book_must_not_be_a_loser(self):
        tb = [mk_row(i, d, net=-1_000_000) for d in NON_P1 for i in range(20)]
        b = x.bars(tb, {}, True)
        self.assertFalse(b["flat"]["S6"]["pass"])
        self.assertLess(b["flat"]["S6"]["kept_mean_sol"], 0)

    def test_kept_book_keeps_a_vetoed_miss_with_its_fee(self):
        miss = mk_row(1, NON_P1[0], filled=False, net=-505_000)
        ok = mk_row(2, NON_P1[0], net=1_000_000)
        kept = x.kept_trades([miss, ok], {miss["mint"]: True, ok["mint"]: False})
        self.assertEqual(len(kept), 2)
        self.assertEqual(x.x_trades([miss], {miss["mint"]: True})[0]["press"], 0.0)

    def test_total_loss_sensitivity_on_filled_trades_only(self):
        tb = good_book(dates=NON_P1, per_date=40, n_rug=2)
        veto = vetoed_by(tb)
        self.assertTrue(x.bars(tb, veto, True)["press"]["S6"]["pass"])
        # score every kept FILLED trade on a flagged pool at -(size + fees): the kept book turns negative
        flags = {r["mint"]: True for r in tb if r["filled"]}
        b = x.bars(tb, veto, True, flags)
        self.assertTrue(b["press"]["S6"]["kept_mean_sol"] > 0)  # the plain figure is untouched
        self.assertLess(b["press"]["S6"]["kept_mean_total_loss_sol"], 0)
        self.assertFalse(b["press"]["S6"]["pass"])
        self.assertFalse(b["passes"])
        # a MISS is never scored at total loss: it keeps its miss cost
        miss = mk_row(77, NON_P1[0], filled=False, net=-505_000)
        kept = x.kept_trades([miss], {}, {miss["mint"]: True})
        self.assertEqual(kept[0]["press"], -505_000.0)
        # a vetoed flagged trade is an avoided total loss in the report-only S1 recomputation
        rep = x.report_only(tb, veto, {"per_candidate": {c: veto for c in x.CANDIDATES}, "chosen_by_date": {}}, True, flags, {})
        self.assertGreater(rep["s1_mean_with_total_loss"]["press"], x.bars(tb, veto, True)["press"]["S1"]["mean_x_sol_non_p1"])

    def test_both_fail_models_are_required(self):
        tb = good_book(dates=NON_P1, per_date=40, n_rug=2)
        for r in tb:
            r["flat"] = 1_000_000.0 if not r["rug"] else 30_000_000.0  # under the flat leg the vetoed rugs WIN: the paired mean is negative
        b = x.bars(tb, vetoed_by(tb), True)
        self.assertTrue(b["press"]["all"])
        self.assertFalse(b["flat"]["S1"]["pass"])
        self.assertFalse(b["passes"])


# --- nested leave-one-date-out ----------------------------------------------------------------------------------------------------


class NestedTests(unittest.TestCase):
    def test_selects_the_rule_that_isolates_rugs_and_applies_it_to_the_held_out_date(self):
        tb = good_book()
        nested = x.nested_lodo(tb, dates=NON_P1[:3])
        self.assertEqual(set(nested["chosen_by_date"].values()), {"r1"})
        self.assertEqual(len(nested["folds"]), 3)
        veto = nested["veto"]
        self.assertTrue(all(veto[r["mint"]] == bool(r["rug"]) for r in tb if r["date"] in NON_P1[:3]))
        self.assertEqual(set(nested["folds"][0]["veto"]), {r["mint"] for r in tb if r["date"] == NON_P1[0]})  # only the held-out date is scored

    def test_no_signal_keeps_the_frozen_book(self):
        tb = []
        for d in NON_P1 + P1:
            for i in range(40):
                tb.append(mk_row(i, d, rugged=(i < 2), net=-30_000_000 if i < 2 else 1_000_000))  # rugs exist but no feature separates them
        nested = x.nested_lodo(tb, dates=NON_P1[:2])
        self.assertEqual(set(nested["chosen_by_date"].values()), {x.NONE_ID})
        self.assertFalse(any(nested["veto"].values()))
        for f in nested["folds"]:
            self.assertFalse(f["inner"]["r1"]["eligible"])

    def test_pick_requires_eligibility_and_a_strictly_positive_inner_mean(self):
        base = {"n_selected": 100, "n_selected_filled": 100, "n_vetoed_filled": 40, "frac": 0.40, "press_sum": 5e8, "flat_sum": 0.0}
        stats = {cid: dict(base, press_sum=0.0, n_vetoed_filled=0, frac=0.0) for cid in x.CANDIDATES}
        self.assertEqual(x.pick_candidate(stats)[0], x.NONE_ID)
        stats["r2"] = dict(base, frac=0.10)  # eligible: 40 vetoed, 10%
        self.assertEqual(x.pick_candidate(stats)[0], "r2")
        stats["r2"] = dict(base, frac=0.25)  # over the 20% cap
        self.assertEqual(x.pick_candidate(stats)[0], x.NONE_ID)
        stats["r2"] = dict(base, frac=0.10, n_vetoed_filled=29)  # under the 30 floor
        self.assertEqual(x.pick_candidate(stats)[0], x.NONE_ID)
        stats["r2"] = dict(base, frac=0.10, press_sum=0.0)  # a zero mean ties with no veto: no veto wins
        self.assertEqual(x.pick_candidate(stats)[0], x.NONE_ID)
        stats["r2"] = dict(base, frac=0.10, press_sum=-1.0)
        self.assertEqual(x.pick_candidate(stats)[0], x.NONE_ID)
        stats["r3"] = dict(base, frac=0.10, press_sum=9e8)
        stats["r2"] = dict(base, frac=0.10, press_sum=9e8)
        self.assertEqual(x.pick_candidate(stats)[0], "r2")  # a tie among candidates goes to the lowest index
        stats["r4"] = dict(base, frac=0.10, press_sum=9.5e8)
        self.assertEqual(x.pick_candidate(stats)[0], "r4")

    def test_inner_pick_uses_pressure_only(self):
        tb = good_book(per_date=40)
        for r in tb:
            r["flat"] = 1_000_000.0 if r["rug"] else r["flat"]  # flat leg says the rugs win; the pick must ignore it
        nested = x.nested_lodo(tb, dates=NON_P1[:2])
        self.assertEqual(set(nested["chosen_by_date"].values()), {"r1"})

    def test_held_out_date_and_purge_never_enter_training(self):
        tb = good_book(dates=NON_P1[:4], per_date=30)
        d = NON_P1[1]
        near = mk_row(500, NON_P1[0], rugged=True, net=-1)
        near["mig_ms"] = e15.date_start_ms(d) - 10 * 60_000  # 10 minutes before d starts
        self.assertTrue(x.purged(near, d))
        far = dict(near, mig_ms=e15.date_start_ms(d) - 40 * 60_000)
        self.assertFalse(x.purged(far, d))
        after = dict(near, mig_ms=e15.date_start_ms(d) + e15.DAY_MS + 34 * 60_000)
        self.assertTrue(x.purged(after, d))
        seen = []
        real = x.fit_logit

        def spy(xm, y):
            seen.append(len(y))
            return real(xm, y)

        with mock.patch.object(x, "fit_logit", spy):
            x.outer_fold(tb + [near], d)
        self.assertTrue(seen)
        self.assertLessEqual(max(seen), 3 * 30)  # at most the three other dates' rows, never d's

    def test_logistic_threshold_is_the_1_minus_f_quantile_of_filled_rows(self):
        vals = list(range(101))
        self.assertEqual(x.quantile_threshold(vals, 0.05), 95)  # index round(0.95 * 100)
        self.assertEqual(x.quantile_threshold(vals, 0.10), 90)
        self.assertIsNone(x.quantile_threshold([], 0.05))

    def test_the_logistic_candidate_can_be_selected(self):
        tb = good_book(per_date=50)  # 4% rugs: below the 5% veto fraction, so the quantile cut falls among the non-rugs
        for j, r in enumerate(tb):  # hide the rule feature; a continuous count separates the rugs (no tied probabilities)
            r["feat"][x.FEATURES18.index("launch_supply_held")] = 0.0
            r["feat"][x.FEATURES18.index("n_launch_buyers")] = 5.0 if r["rug"] else (j % 50) * 0.01
        nested = x.nested_lodo(tb, dates=NON_P1[:3])
        self.assertTrue(set(nested["chosen_by_date"].values()) <= {"l5", "l10"})
        self.assertTrue(all(nested["veto"][r["mint"]] for r in tb if r["date"] in NON_P1[:3] and r["rug"]))  # every rug is vetoed (and nothing else beyond the 5% cut)
        self.assertLessEqual(sum(nested["veto"].values()), 3 * 50 * 0.06)

    def test_logistic_uses_the_pinned_setting(self):
        self.assertEqual((x.LOGREG["kind"], x.LOGREG["C"]), ("logreg", 0.5))
        fit = x.fit_logit(x.design([[0.0] * 18, [1.0] * 18] * 15), [0, 1] * 15)
        self.assertEqual(fit["model"].class_weight, "balanced")
        self.assertIsNone(x.fit_logit(x.design([[0.0] * 18] * 30), [0] * 30))  # one class: no fit
        self.assertIsNone(x.fit_logit(x.design([[0.0] * 18] * 5), [0, 1, 0, 1, 0]))  # too few rows


class ReportTests(unittest.TestCase):
    def test_full_run_screen_on_a_good_book_and_render(self):
        tb = good_book(per_date=20)
        pass_cells = []
        res = x.run_screen(tb, pass_cells, {}, set(), True)
        self.assertTrue(res["decision"]["passes"], res["decision"])
        self.assertEqual(res["decision"]["outcome"], x.OUTCOME_PASS)
        rep = {"first_line": x.first_line(True), "banner": x.BANNER, "decision": res["decision"], "pre_started": {}, "guards_after_started": res["guards"], "bars": res["bars"],
               "report_only": res["report_only"], "prior_tries": {}}
        md = x.render_md(rep)
        self.assertIn("SCREEN PASS", md)
        with tempfile.TemporaryDirectory() as d:
            x.write_report(Path(d), rep)
            self.assertTrue((Path(d) / x.OUT_REPORT).is_file() and (Path(d) / x.OUT_MD).is_file())

    def test_without_p4_the_first_line_says_so(self):
        self.assertIn("SMALLER 30-UTC-date pool", x.first_line(False))

    def test_frozen_selection_uses_oof_on_p1_and_the_model_elsewhere(self):
        z = [0.0] * 18
        cells = [{"mint": "a", "block": "P1", "exp012_features": z}, {"mint": "b", "block": "P1", "exp012_features": z}, {"mint": "c", "block": "P3", "exp012_features": z},
                 {"mint": "d", "block": "P1", "exp012_features": z}]
        sel = x.frozen_flags(cells, {"a": 0.9, "b": 0.5}, scorer=lambda xs: [0.9 for _ in xs])
        self.assertEqual(sel, [True, False, True, False])  # d has no stored OOF score: not selected

    def test_universe_and_feature_hashes_are_outcome_blind(self):
        tb = good_book(dates=NON_P1[:2], per_date=5)
        u1, f1 = x.universe_sha256(tb), x.feature_table_sha256(tb)
        tb2 = [dict(r, flat=r["flat"] * 7, press=-r["press"], rug=not r["rug"]) for r in tb]
        self.assertEqual((u1, f1), (x.universe_sha256(tb2), x.feature_table_sha256(tb2)))
        tb3 = [dict(r, filled=False) if i == 0 else r for i, r in enumerate(tb)]
        self.assertNotEqual(u1, x.universe_sha256(tb3))


# --- P1B canonical pool (plan 13 item 6) and the single `started` line (plan 5.4) ---------------------------------------------------------------


def fake_canon(m: str) -> str:
    return "CANON_" + m


PRE_PERIOD_MINTS = 300  # realistic scale: many migrated-before-the-tape tokens trade on PumpSwap with canonical pools and have no create row


def p1b_source(*, with_creates=True):
    """A P1B source through the REAL resolver shape: `_hour_info_b` returns {"hour", "day", "end", "trade"} (no `create` key), Oracle trade rows
    have no `block_time` and PumpSwap rows no `quote_is_wsol`, and creates come from the adapter's observe day files (slot 0 placeholders)."""
    from functools import partial

    from tools.oracle_live_adapter import POOL_B_CREATE_DAYS, _hour_info_b

    from datetime import datetime, timezone

    from tools.oracle_live_adapter import POOL_B_START

    start_s = int(datetime.strptime(POOL_B_START, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp())
    b0 = start_s + 3600
    cr_b, tr_b = mint_tape("MINTB", b0)
    cr_c, tr_c = mint_tape("MINTC", b0 + 100)
    cr_d, tr_d = mint_tape("MINTD", b0 + 200)
    cr_e, tr_e = mint_tape("MINTE", b0 + 300)  # created before the tape starts (see the t_ws override below)
    cr_f, tr_f = mint_tape("MINTF", b0 + 400)  # its first bonding print is 700 s after its create
    for t in tr_e + tr_f:
        if t["venue"] == "pumpswap":
            t["pool"] = "CANON_" + t["mint"]
    for t in tr_b:
        if t["venue"] == "pumpswap":
            t["pool"] = "CANON_MINTB"
    for t in tr_c:
        if t["venue"] == "pumpswap":
            t["pool"] = "SOME_OTHER_POOL"  # only foreign-pool prints
    for t in tr_d:
        if t["venue"] == "pumpswap":
            t["pool"] = "CANON_MINTD"
    tr_d = [t for t in tr_d if t["venue"] == "pumpswap"]  # MINTD: a migration but no bonding print on the tape
    pre = [{"type": "trade", "venue": "pumpswap", "mint": f"PRE{i}", "pool": f"CANON_PRE{i}", "slot": 5000 + i, "t_recv_ms": (b0 + i) * 1000, "side": "buy",
            "event_index": 1, "trader": "w", "sol_lamports": 1, "token_raw": 1, "quote_reserve": 1, "base_reserve": 1, "signature": f"s{i}"} for i in range(PRE_PERIOD_MINTS)]
    tz = [dict(t, mint="MINTZ", pool="CANON_MINTZ") for t in tr_b if t["venue"] == "pumpswap"]  # PumpSwap token that was never a pump.fun create
    trades = []
    ty = [dict(t, mint="MINTY", pool="SOME_FOREIGN") for t in tr_b if t["venue"] == "pumpswap"]  # an unrelated PumpSwap token: foreign pool, no create
    for t in tr_b + tr_c + tr_d + tr_e + tr_f + tz + ty + pre:
        t = dict(t)
        t.pop("block_time", None)  # the Oracle tape has t_recv_ms only
        if t["venue"] == "pumpswap":
            t.pop("quote_is_wsol", None)
        trades.append(t)
    d = Path(tempfile.mkdtemp())
    (d / "trades").mkdir()
    (d / "creates").mkdir()
    (d / "trades" / "trades-2026-09-25T07.jsonl").write_text("".join(json.dumps(t) + "\n" for t in trades))
    t_ws = {"MINTE": (start_s - 100) * 1000, "MINTF": (b0 + 400) * 1000 - 700_000}
    obs = [{"stream": "subscribeNewToken", "txType": "create", "mint": c["mint"], "t_ws": t_ws.get(c["mint"], c["block_time"] * 1000 - 5000), "traderPublicKey": "creatorZ",
            "signature": c["signature"]} for c in (cr_b + cr_c + cr_d + cr_e + cr_f)]
    for day in POOL_B_CREATE_DAYS:
        (d / "creates" / f"observe-{day}.jsonl").write_text("".join(json.dumps(o) + "\n" for o in obs) if (with_creates and day == POOL_B_CREATE_DAYS[0]) else "")
    hours_fn = partial(_hour_info_b, root=d)
    assert "create" not in hours_fn("2026-09-25T07")  # the real shape
    return x.load_source_data("P1B", "P1", hours_fn, ["2026-09-25T07"], [d], canonical_fn=fake_canon)


class P1BCanonicalPoolTests(unittest.TestCase):
    def test_real_canonical_pool_is_the_executors_pool(self):
        from solders.pubkey import Pubkey

        from tools.pumpswap_tx import canonical_pool

        mint = "So11111111111111111111111111111111111111112"
        self.assertEqual(x.canonical_pool_str(mint), str(canonical_pool(Pubkey.from_string(mint))))

    def test_derive_uses_canonical_pool_not_the_row_pool_and_first_canonical_print_slot(self):
        prints = [("A", "FOREIGN", 90), ("A", "CANON_A", 120), ("A", "CANON_A", 110), ("B", "FOREIGN", 50), ("C", None, 5)]
        mg = x.derive_p1b_migrations(prints, fake_canon)
        self.assertEqual((mg["A"]["pool"], mg["A"]["slot"]), ("CANON_A", 110))  # the foreign print at 90 does not date the migration
        self.assertEqual(rug.count_no_pool_mints(mg.values()), ["B", "C"])  # no print on the canonical pool: no-pool
        self.assertEqual(rug.migration_pool_map(mg.values()), {"A": "CANON_A"})

    def test_underivable_mint_is_no_pool(self):
        def boom(m):
            raise ValueError("bad pubkey")

        mg = x.derive_p1b_migrations([("A", "P", 1)], boom)
        self.assertEqual(rug.count_no_pool_mints(mg.values()), ["A"])

    def test_loader_p1b_derives_pools_and_counts_zero_print_mints(self):
        src = p1b_source()
        self.assertTrue(src.derived_pools)
        pmap = rug.migration_pool_map(src.migrations.values())
        self.assertEqual(set(pmap), {"MINTB", "MINTD", "MINTE", "MINTF"})  # only mints WITH a create row
        self.assertEqual(pmap["MINTB"], "CANON_MINTB")
        self.assertEqual(rug.count_no_pool_mints(src.migrations.values()), ["MINTC"])  # pump.fun mint, prints only on a foreign pool
        self.assertEqual(src.canonical_no_create, 1 + PRE_PERIOD_MINTS)  # canonical prints but no create: a COUNT only (pre-period tokens)
        self.assertFalse([m for m in src.rows_by_mint if m == "MINTZ" or m.startswith("PRE")])  # no rows retained for them
        self.assertTrue(x.pool_print_times(src, pmap)["CANON_MINTB"])

    def test_other_sources_keep_the_migration_row_pool(self):
        creates, trades = mint_tape("MINTB", T0)
        with tempfile.TemporaryDirectory() as d:
            tp, cp = Path(d) / "trades.jsonl", Path(d) / "creates.jsonl"
            tp.write_text("".join(json.dumps(t) + "\n" for t in trades))
            cp.write_text("".join(json.dumps(c) + "\n" for c in creates))
            src = x.load_source_data("P3", "P3", lambda h: {"trade": tp, "create": cp}, ["h0"], [], canonical_fn=fake_canon)
        self.assertFalse(src.derived_pools)
        self.assertEqual(src.migrations, {})

    def test_p1b_zero_print_canonical_mint_is_excluded_from_both_books_and_counted(self):
        src = fixture_source(pool_of_prints="OTHER_POOL")  # every print is on a pool that is not the mint's canonical pool
        src.tag, src.derived_pools = "P1B", True
        src.migrations = x.derive_p1b_migrations(
            ((t["mint"], t.get("pool"), t["slot"]) for t in src.rows_by_mint["MINTA"] if t["venue"] == "pumpswap"), fake_canon
        )
        r = x.process_source(src, VMAP, fake_canon)
        self.assertEqual((r["cells"], r["no_pool_mints"]), ([], ["MINTA"]))
        self.assertIsNone(r["pool_vs_canonical"])  # derived sources have no migration-row pools to compare
        pc = x.pre_started_counts([r], {}, {}, closed=[])
        self.assertEqual(pc["no_pool_mints"], {"P1B": 1})

    def test_pool_vs_canonical_counts_are_report_only_and_outcome_blind(self):
        src = fixture_source()
        src.migrations["MINTX"] = {"type": "migration", "mint": "MINTX", "pool": "CANON_MINTX", "slot": 5}
        src.migrations["MINTY"] = {"type": "migration", "mint": "MINTY", "pool": "ELSEWHERE", "slot": 5}
        r = x.process_source(src, VMAP, fake_canon)
        self.assertEqual(r["pool_vs_canonical"], {"equal": 1, "not_equal": 2, "underivable": 0})  # MINTA's POOL1 != CANON_MINTA
        pc = x.pre_started_counts([r], {}, {}, closed=[])
        self.assertEqual(pc["migration_pool_vs_canonical"], {"P1A": {"equal": 1, "not_equal": 2, "underivable": 0}})
        self.assertEqual([c["mint"] for c in r["cells"]], ["MINTA"])  # the migration-row pool still drives the cells

    def test_plan_item_6_is_recorded(self):
        text = (Path(__file__).resolve().parent.parent / "EXP" / "EXP-016-rug-veto-plan.md").read_text()
        self.assertRegex(text, r"\n6\. 2026-10-06[^\n]*canonical_pool")


class SingleStartedLineTests(unittest.TestCase):
    def test_started_precedes_every_candidate_line_and_is_written_once(self):
        with tempfile.TemporaryDirectory() as d:
            out, log = Path(d) / "out", Path(d) / "t.jsonl"
            x.log_all(out, log, log, "started", True, {"universe_sha256": "u", "feature_table_sha256": "f", "prior_tries_per_pool": {}})
            x.log_all(out, log, log, "completed", True, {})
            x.log_all(out, log, log, "started", True, {})  # same out dir: dedupe keeps it at one
            cfg = [json.loads(line)["config"] for line in log.read_text().splitlines()]
            self.assertEqual(cfg[0]["key"], x.STARTED_KEY)
            self.assertEqual(sum(c["key"] == x.STARTED_KEY for c in cfg), 1)
            cand = {v["key"] for v in x.CANDIDATES.values()}
            self.assertTrue(all(c["key"] in cand for c in cfg[1:]))
            self.assertEqual(len(cfg), 1 + 6 * 4)
            self.assertEqual(set(cfg[0]["keys"]), cand)

    def test_a_second_run_refuses_before_any_started_line(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "t.jsonl"
            x.log_all(Path(d) / "out", log, log, "started", True, {})
            before = log.read_text()
            with self.assertRaises(x.Refused):
                x.check_no_prior_tries(log)  # main() calls this first, before reading any row
            self.assertEqual(log.read_text(), before)

    def test_main_orders_the_spend_point_before_scoring(self):
        import inspect

        src = inspect.getsource(x.main)
        i_check, i_started, i_screen = src.index("check_no_prior_tries"), src.index('"started"'), src.index("run_screen(")
        self.assertLess(i_check, i_started)
        self.assertLess(i_started, i_screen)
        self.assertEqual(src.count('"started"'), 1)


# --- quant-proof round: crash safety, censoring rule, refusals, inputs ---------------------------------------------------------------------


def _cells_with_no_sim_and_censored():
    nf = len(x.fz.FROZEN_FEATURE_NAMES)
    filled = {"mint": "OK", "pool": "PO", "block": "P2", "status": "FILLED", "mig_ms": 1, "exp012_features": [0.0] * nf}
    no_sim_early = {"mint": "NS1", "pool": "PN", "block": "P2", "status": "NO_SIM", "why": "no frozen row"}  # no mig_ms, no features key
    no_sim_bare = {"mint": "NS2", "block": "P2", "status": "NO_SIM", "why": "bad create row"}  # an old-style record: no pool at all
    censored = {"mint": "CE", "pool": "PC", "block": "P2", "status": "CENSORED", "mig_ms": 2, "exp012_features": None}
    return [filled, no_sim_early, no_sim_bare, censored]


class NoSimAndCensoredConsumerTests(unittest.TestCase):
    def test_simulate_mint_no_sim_record_carries_pool_and_reason(self):
        creates, trades = mint_tape("MINTA", T0)
        bad = dict(creates[0], slot=None)
        rec = x.simulate_mint("MINTA", bad, trades, pool="POOL1", migration_slot=1050, vmap=VMAP, tape_through_ms=(T0 + 4000) * 1000, creator_hist={})
        self.assertEqual((rec["status"], rec["pool"], rec["why"]), ("NO_SIM", "POOL1", "bad create row"))

    def test_frozen_flags_scores_only_cells_with_features(self):
        cells = _cells_with_no_sim_and_censored()
        seen = []

        def scorer(rows):
            seen.append(len(rows))
            return [1.0] * len(rows)

        flags = x.frozen_flags(cells, {}, scorer=scorer)
        self.assertEqual(flags, [True, False, False, False])
        self.assertEqual(seen, [1])

    def test_pre_started_counts_and_v_coverage_survive_them(self):
        cells = _cells_with_no_sim_and_censored()
        res = {"tag": "P2", "cells": cells, "no_pool_mints": [], "gate": {"foreign_first_mints": [], "mints_with_foreign_pool_prints": 0}, "slot_inversions": 0,
               "no_create_row": ["X"], "no_migration_slot": ["Y"]}
        pc = x.pre_started_counts([res], {"OK": True, "NS1": True}, {"PO": 5, "PC": 5}, closed=[])
        self.assertEqual(pc["no_sim"], 2)
        self.assertEqual(pc["no_sim_by_reason"], {"bad create row": 1, "no frozen row": 1})
        self.assertEqual(pc["n_canonical_pools"], 3)
        self.assertEqual(pc["migrated_mints_without_create_row"], {"P2": 1})
        self.assertEqual(pc["no_migration_slot_mints"], {"P2": ["Y"]})
        self.assertEqual(x.v_coverage([c["pool"] for c in cells if c.get("pool")], {"PO": 5, "PN": 5, "PC": 5})["n_pools"], 3)
        self.assertEqual(x.total_loss_flags(cells, {"PO"}, set())["NS2"], False)

    def test_unmapped_pools_join_the_total_loss_set(self):
        import inspect

        src = inspect.getsource(x.main)
        self.assertIn('pre["unmapped_pools_frozen_selected"]', src)
        cells = [{"mint": "A", "pool": "PU", "block": "P2", "status": "FILLED", "mig_ms": 1}]
        self.assertTrue(x.total_loss_flags(cells, {"PU"}, set())["A"])


class PlanCensoringTests(unittest.TestCase):
    def test_deadline_past_the_edge_censors_even_if_an_early_exit_would_hit(self):
        base = x.simulate_mint("MINTA", fixture_source().creates["MINTA"], fixture_source().rows_by_mint["MINTA"], pool="POOL1", migration_slot=1050, vmap=VMAP,
                               tape_through_ms=(T0 + 4000) * 1000, creator_hist={})
        self.assertEqual(base["status"], "FILLED")
        cap = int(x._target_spec()["cap_ms"])
        edge = base["landing_ms"] + cap
        src = fixture_source()

        def run(through):
            with mock.patch.object(x, "tpsl_exit", side_effect=AssertionError("censoring must not depend on the exit")):
                return x.simulate_mint("MINTA", src.creates["MINTA"], src.rows_by_mint["MINTA"], pool="POOL1", migration_slot=1050, vmap=VMAP,
                                       tape_through_ms=through, creator_hist={})

        r = run(edge - 1)
        self.assertEqual(r["status"], "CENSORED")  # deadline one ms past the block edge; no exit logic ran
        self.assertNotIn("features", r)

    def test_censored_cell_is_in_no_book(self):
        c = {"mint": "C", "pool": "P", "block": "P1", "status": "CENSORED", "mig_ms": 5}
        self.assertFalse(x.in_book(c))


class RefusalTests(unittest.TestCase):
    def test_simulator_drift_is_a_refusal_naming_only_the_mint(self):
        src = fixture_source()
        real = x.tpsl_exit

        def bad(*a, **kw):
            return {**real(*a, **kw), "state_idx": 0}

        with mock.patch.object(x, "tpsl_exit", bad):
            with self.assertRaises(x.Refused) as cm:
                x.process_source(src, VMAP)
        self.assertIsInstance(cm.exception, x.SimulatorDrift)
        msg = str(cm.exception)
        self.assertTrue(msg.startswith("MINTA:"))
        self.assertIsNone(re.search(r"-?\d", msg))  # no number at all: no net, no outcome value
        self.assertNotIn("net", msg.lower().replace("no net", ""))

    def test_reserved_0802_path_is_refused_by_the_guard(self):
        ns = SimpleNamespace(p1_fast_dir="/x/fresh-0802/a", p1_oracle_insample_dir="/y", p1_oracle_live_dir="/z", p3_root="/w", p2_view_dir=[], p4_view_dir=None)
        with self.assertRaises(x.Refused):
            x.refuse_extra_reserved(ns)
        ok = SimpleNamespace(p1_fast_dir="/x/a", p1_oracle_insample_dir="/y", p1_oracle_live_dir="/z", p3_root="/w", p2_view_dir=["/v/fresh-0802"], p4_view_dir=None)
        with self.assertRaises(x.Refused):
            x.refuse_extra_reserved(ok)
        ok.p2_view_dir = ["/v/explore-0814"]
        x.refuse_extra_reserved(ok)


class SourceCountersTests(unittest.TestCase):
    def test_missing_migration_slot_and_missing_create_are_counted_and_excluded(self):
        src = fixture_source()
        src.migrations["MINTA"] = {"type": "migration", "mint": "MINTA", "pool": "POOL1"}  # no slot
        src.migrations["GHOST"] = {"type": "migration", "mint": "GHOST", "pool": "PG", "slot": 7}  # no create row
        r = x.process_source(src, VMAP)
        self.assertEqual((r["cells"], r["no_migration_slot"], r["no_create_row"]), ([], ["MINTA"], ["GHOST"]))

    def test_slimmed_rows_keep_quote_is_wsol(self):
        creates, trades = mint_tape("MINTB", T0)
        for t in trades:
            t["quote_is_wsol"] = True
        with tempfile.TemporaryDirectory() as d:
            tp, cp = Path(d) / "trades.jsonl", Path(d) / "creates.jsonl"
            tp.write_text("".join(json.dumps(t) + "\n" for t in trades))
            cp.write_text("".join(json.dumps(c) + "\n" for c in creates))
            src = x.load_source_data("P3", "P3", lambda h: {"trade": tp, "create": cp}, ["h0"], [])  # not migrated: slimmed rows
        self.assertTrue(all(r.get("quote_is_wsol") is True for r in src.rows_by_mint["MINTB"]))

    def test_p1b_gap_slots_are_slots_only(self):
        rows = [{"venue": "pump_bonding", "slot": 100}, {"venue": "pump_bonding", "slot": 108}, {"venue": "pumpswap", "slot": 120, "pool": "CP"}]
        src = x.SourceData("P1B", "P1", {}, {"M": {"pool": "CP", "slot": 120, "mint": "M"}, "N": {"pool": "CN", "slot": 50, "mint": "N"}}, {"M": rows, "N": []}, 0, derived_pools=True)
        self.assertEqual(x.p1b_gap_slots(src), {"n": 1, "n_no_bonding": 1, "p50": 12, "p90": 12, "max": 12})
        self.assertEqual(x._pctile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.9), 9)
        self.assertEqual(x._pctile([1, 2, 3, 4], 0.5), 2)

    def test_started_blocks_are_listed_separately(self):
        bl = x.started_blocks(True)
        self.assertEqual([(b["start_hour"], b["end_hour_exclusive"]) for b in bl], [e15.BLOCKS[k] for k in ("P2", "P3", "P4")])
        self.assertEqual(len(x.started_blocks(False)), 2)

    def test_started_line_carries_the_input_shas_and_main_orders_the_checks(self):
        import inspect

        src = inspect.getsource(x.main)
        self.assertIn("**input_shas", src)
        self.assertIn("prior_tries_per_pool(canonical", src)
        self.assertLess(src.index("e15.take_lock"), src.index("check_no_prior_tries(tries_path, canonical)  # re-checked"))
        self.assertLess(src.index("check_no_prior_tries(tries_path, canonical)  # re-checked"), src.index('log_all(out_dir, tries_path, canonical, "started"'))
        self.assertLess(src.index("input files:"), src.index("load_source_data("))
        self.assertLess(src.index("check_constancy_sample("), src.index('"started"'))
        self.assertIn("setdefault(p_, []).extend", src)


# --- quant-proof round 2 ---------------------------------------------------------------------------------------------------------------


def _digits_outside(msg: str, mint: str) -> bool:
    return re.search(r"\d", msg.replace(mint, "")) is not None


class ConstancyPopulationTests(unittest.TestCase):
    def test_sample_is_drawn_from_readable_pools_only(self):
        pools = [f"pool{i}" for i in range(600)]
        vmap = {p: 17_584_000_000 for p in pools}
        vmap["pool0"] = None  # an unreadable pool in the population
        vmap["pool1"] = None
        pop = x.readable_pools(pools, vmap)
        self.assertEqual(len(pop), 598)
        sample = x.sample_pools(pop)
        self.assertEqual(len(sample), x.V_SAMPLE_SIZE)
        self.assertTrue(all(vmap[p] is not None for p in sample))
        x.check_constancy_sample([{"pool": p, "v_implied": 1} for p in sample], pop)
        # the old rule (draw from ALL pools) is what the file must not be
        old = x.sample_pools(pools)
        if any(vmap[p] is None for p in old):
            with self.assertRaises(x.Refused):
                x.check_constancy_sample([{"pool": p, "v_implied": 1} for p in old], pop)
        samples = [{"pool": p, "v_implied": 17_584_000_000, "quote_reserve": 10 * SOL} for p in sample]
        self.assertEqual(x.check_v_constancy(samples, vmap)["n_checked"], x.V_SAMPLE_SIZE)  # both checks agree

    def _args(self, out):
        return SimpleNamespace(vmap="unused", emit_constancy_sample=out)

    def test_emit_constancy_sample_writes_pool_ids_only_and_matches_main_population(self):
        n = 500
        migs = {f"M{i}": {"type": "migration", "mint": f"M{i}", "pool": f"pool{i}", "slot": 100 + i} for i in range(n)}
        migs["M0"]["slot"] = None  # no migration slot: excluded
        creates = {f"M{i}": {"mint": f"M{i}"} for i in range(n)}
        del creates["M1"]  # no create row: excluded
        migs["NOPOOL"] = {"type": "migration", "mint": "NOPOOL", "slot": 5}
        creates["NOPOOL"] = {"mint": "NOPOOL"}
        src = x.SourceData("P2", "P2", creates, migs, {}, 0)
        vmap = {f"pool{i}": 17_584_000_000 for i in range(n)}
        vmap["pool2"] = None
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "sample.json"
            with mock.patch.object(x, "run_guards", return_value={}), mock.patch.object(x, "load_pinned_vmap", return_value=vmap), \
                    mock.patch.object(x, "build_sources", return_value=[("P1A", "P1", None, [], []), ("P2", "P2", None, [], [])]), \
                    mock.patch.object(x, "load_source_data", return_value=src) as lsd, mock.patch.object(x, "log_all") as lg:
                self.assertEqual(x.emit_constancy_sample(self._args(out)), 0)
                self.assertEqual(lsd.call_count, 1)  # only the P2 source is read
                lg.assert_not_called()  # no tries line
            obj = json.loads(out.read_text())
            got = obj["primary"]
            self.assertFalse(Path(d, "tries.jsonl").exists())
        elig = [f"pool{i}" for i in range(n) if i not in (0, 1, 2)]
        self.assertEqual(got, x.sample_pools(elig))
        self.assertTrue(all(isinstance(p, str) and p.startswith("pool") for p in got))
        # the same set process_source would make cells for, and main's population rule accepts it
        self.assertEqual(x.p2_eligible_pools(src), sorted(f"pool{i}" for i in range(n) if i not in (0, 1)))
        pop = x.readable_pools(x.p2_eligible_pools(src), vmap)
        self.assertEqual(obj["reserve"], x.reserve_pools(pop, got))
        self.assertEqual((len(obj["reserve"]), set(obj["reserve"]) & set(got)), (20, set()))
        x.check_constancy_sample([{"pool": p, "v_implied": 1} for p in got], pop)

    def test_emit_refuses_when_guards_refuse(self):
        with mock.patch.object(x, "run_guards", side_effect=x.Refused("no")):
            self.assertEqual(x.emit_constancy_sample(self._args(Path("/nonexistent/x.json"))), 2)


class OutcomeFreeMessageTests(unittest.TestCase):
    def test_resolve_key_message(self):
        fill = SimpleNamespace(t_recv_ms=1_790_000_123_456, slot=987_654, tx_index=3, event_index=4)
        with self.assertRaises(rug.PoolAttributionRefusal) as cm:
            x.resolve_key([], fill, mint="MINTA", endpoint="exit")
        self.assertIn("MINTA", str(cm.exception))
        self.assertIn("exit", str(cm.exception))
        self.assertFalse(_digits_outside(str(cm.exception), "MINTA"))

    def test_check_migration_pool_only_message(self):
        fill = {"venue": "pumpswap", "t_recv_ms": 1_790_000_123_456, "slot": 987_654, "tx_index": 3, "event_index": 4}
        with self.assertRaises(rug.PoolAttributionRefusal) as cm:
            rug.check_migration_pool_only([], "POOL_X", [fill], mint="MINTA", names=("entry",))
        self.assertIn("entry", str(cm.exception))
        self.assertFalse(_digits_outside(str(cm.exception), "MINTA"))
        self.assertNotIn("POOL_X", str(cm.exception))

    def test_label_trade_endpoint_message(self):
        with self.assertRaises(rug.PoolAttributionRefusal) as cm:
            rug.label_trade([], migration_pool="POOL_X", entry_key=(1_790_000_123_456, 987_654, 3, 4), exit_key=(1_790_000_123_457, 987_655, 3, 4), vmap={}, mint="MINTA")
        self.assertIn("MINTA", str(cm.exception))
        self.assertIn("entry", str(cm.exception))
        self.assertFalse(_digits_outside(str(cm.exception), "MINTA"))

    def test_mirrored_exit_censored_is_a_drift_not_a_censor(self):
        src = fixture_source()
        with mock.patch.object(x, "tpsl_exit", return_value=None):
            with self.assertRaises(x.SimulatorDrift) as cm:
                x.process_source(src, VMAP)
        self.assertFalse(_digits_outside(str(cm.exception), "MINTA"))


class DeadlineBandTests(unittest.TestCase):
    def test_exit_lag_band_is_censored(self):
        src = fixture_source()
        base = x.simulate_mint("MINTA", src.creates["MINTA"], src.rows_by_mint["MINTA"], pool="POOL1", migration_slot=1050, vmap=VMAP,
                               tape_through_ms=(T0 + 4000) * 1000, creator_hist={})
        edge = base["landing_ms"] + int(x._target_spec()["cap_ms"]) + x.EXIT_LAG * x.xx.SLOT_MS

        def run(through):
            with mock.patch.object(x, "tpsl_exit", side_effect=AssertionError("no exit logic")):
                return x.simulate_mint("MINTA", src.creates["MINTA"], src.rows_by_mint["MINTA"], pool="POOL1", migration_slot=1050, vmap=VMAP,
                                       tape_through_ms=through, creator_hist={})

        self.assertEqual(run(edge - 1)["status"], "CENSORED")  # inside the two-slot band after the cap deadline
        self.assertEqual(run(edge - x.xx.SLOT_MS)["status"], "CENSORED")
        self.assertEqual(run(edge - 1)["landing_ms"], base["landing_ms"])


# --- quant-proof round 3: no traceback or message text before `started` --------------------------------------------------------------------


class MainExceptionHygieneTests(unittest.TestCase):
    def _run(self, d, patches, limits=()):
        import contextlib
        import io

        d = Path(d)
        (d / "c.json").write_text("[]")
        argv = ["--p1-fast-dir", "/x1", "--p1-oracle-insample-dir", "/x2", "--p1-oracle-live-dir", "/x3", "--out-dir", str(d / "out"),
                "--tries-log", str(d / "t.jsonl"), "--canonical-tries", str(d / "canon.jsonl"), "--v-constancy-json", str(d / "c.json"), "--p2-view-dir", "/x4"]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as st:
            st.enter_context(mock.patch.object(x, "run_guards", return_value={"with_p4": False, "vmap_sha256": "v"}))
            st.enter_context(mock.patch.object(x, "git_state", return_value={"dirty_tools": False, "head": "h"}))
            st.enter_context(mock.patch.object(x, "load_pinned_vmap", return_value=dict(VMAP)))
            st.enter_context(mock.patch.object(x, "check_v_constancy", return_value={}))
            st.enter_context(mock.patch.object(x, "check_constancy_sample"))
            st.enter_context(mock.patch.object(x, "check_limits", return_value=list(limits)))  # the fixture source has 1 in-book cell: the floor is tested on its own
            st.enter_context(mock.patch.object(x, "build_sources", return_value=[("P1A", "P1", None, [], [])]))
            st.enter_context(mock.patch.object(x, "load_source_data", side_effect=lambda *a, **k: fixture_source()))
            st.enter_context(mock.patch.object(x, "load_oof", return_value=({}, None)))
            st.enter_context(mock.patch.object(x, "frozen_flags", side_effect=lambda cells, *a, **k: [False] * len(cells)))
            st.enter_context(mock.patch.object(e15, "check_run_lock"))
            for p in patches:
                st.enter_context(p)
            st.enter_context(contextlib.redirect_stderr(err))
            st.enter_context(contextlib.redirect_stdout(out))
            try:
                rc = x.main(argv)
                exc = None
            except Exception as e:  # noqa: BLE001
                rc, exc = None, e
        return rc, exc, out.getvalue() + err.getvalue()

    def test_unexpected_error_in_the_simulation_before_started_prints_type_only(self):
        boom = mock.patch.object(x.eem, "score_one", side_effect=RuntimeError("x 12345 net=-0.0042"))
        with tempfile.TemporaryDirectory() as d, mock.patch.object(x, "log_all") as lg:
            rc, exc, text = self._run(d, [boom])
            lg.assert_not_called()  # `started` was never written
        self.assertIsNone(exc)  # no traceback escapes
        self.assertEqual(rc, 2)
        for banned in ("12345", "0042", "net="):
            self.assertNotIn(banned, text)
        self.assertIn("RuntimeError", text)
        self.assertIn("MINTA", text)

    def test_unexpected_error_outside_the_simulation_before_started_prints_type_only(self):
        boom = mock.patch.object(x, "pre_started_counts", side_effect=ValueError("net=-0.0042 12345"))
        with tempfile.TemporaryDirectory() as d, mock.patch.object(x, "log_all") as lg:
            rc, exc, text = self._run(d, [boom])
            lg.assert_not_called()
        self.assertEqual((exc, rc), (None, 2))
        self.assertNotIn("12345", text)
        self.assertIn("ValueError", text)

    def test_after_started_the_exception_still_propagates_and_the_status_is_logged(self):
        boom = mock.patch.object(x, "run_screen", side_effect=RuntimeError("after started 777"))
        with tempfile.TemporaryDirectory() as d, mock.patch.object(x, "log_all") as lg:
            rc, exc, text = self._run(d, [boom])
        self.assertIsInstance(exc, RuntimeError)
        self.assertIsNone(rc)
        statuses = [c.args[3] for c in lg.call_args_list]
        self.assertEqual(statuses[0], "started")
        self.assertEqual(statuses[-1], "aborted_after_read")


# --- PR preread-fixes: P1B creates, create-slot rule, refusal limits, --precount --------------------------------------------------------------


class P1BCreatesTests(unittest.TestCase):
    def test_creates_are_loaded_from_the_adapter_and_slots_come_from_the_first_bonding_print(self):
        src = p1b_source()
        self.assertEqual(set(src.creates), {"MINTB", "MINTC"})
        self.assertEqual(src.creates["MINTB"]["slot"], 1001)  # slot0 + 1: the first pump_bonding print, never the adapter's slot 0
        self.assertEqual(src.excluded_no_bonding, [])
        self.assertEqual(src.excluded_other, {"pre_tape_create": ["MINTE"], "pre_tape_migration": ["MINTD"], "gap_over": ["MINTF"]})
        self.assertEqual(src.create_stats["n_creates_loaded"], 5)
        self.assertEqual(src.create_stats["gap_s"], {"n": 2, "p50": 6, "p90": 6, "max": 6})  # the REMAINING first bonding block_time - create block_time, seconds

    def test_pre_tape_create_pre_tape_migration_and_gap_rules(self):
        creates = {k: {"mint": k, "slot": 0, "block_time": bt} for k, bt in (("A", 1000), ("B", 50), ("C", 1000), ("D", 1000), ("E", 1000))}
        rows = {"A": [{"venue": "pump_bonding", "slot": 10, "block_time": 1005}], "B": [{"venue": "pump_bonding", "slot": 10, "block_time": 60}],
                "C": [{"venue": "pump_bonding", "slot": 90, "block_time": 1005}],  # bonding only AFTER the first canonical print (slot 80)
                "D": [{"venue": "pump_bonding", "slot": 10, "block_time": 1700}], "E": []}
        mig = {"A": {"pool": "p", "slot": 20}, "B": {"pool": "p", "slot": 20}, "C": {"pool": "p", "slot": 80}, "D": {"pool": "p", "slot": 20}}
        out, nb, st = x.apply_create_slot_rule(creates, rows, migrations=mig, tape_start_s=100, max_gap_s=600)
        self.assertEqual(set(out), {"A"})
        self.assertEqual(st["excluded"], {"no_bonding": ["E"], "pre_tape_create": ["B"], "pre_tape_migration": ["C"], "gap_over": ["D"]})
        self.assertEqual(nb, ["E"])  # a pre-tape migration is NOT in the no-bonding list
        self.assertEqual(st["gap_s"]["max"], 5)

    def test_rows_are_adapted_so_admission_keeps_them(self):
        src = p1b_source()
        rows = src.rows_by_mint["MINTB"]
        self.assertTrue(rows)
        self.assertTrue(all(isinstance(r.get("block_time"), int) for r in rows))  # `adapt_trade_row` stamped it
        self.assertEqual(len(x.admit_rows(rows)), len(rows))
        self.assertTrue(all(r.get("quote_is_wsol") is True for r in rows if r["venue"] == "pumpswap"))

    def test_process_source_counts_exclusions_separately_and_builds_the_cell(self):
        src = p1b_source()
        r = x.process_source(src, {"CANON_MINTB": 17_584_000_000}, fake_canon)
        self.assertEqual(r["no_bonding_excluded"], [])  # pre-tape migrations are not in the no-bonding numerator
        self.assertEqual((r["pre_tape_migration_excluded"], r["pre_tape_create_excluded"], r["gap_excluded"]), (["MINTD"], ["MINTE"], ["MINTF"]))
        self.assertEqual(r["no_create_row"], [])  # canonical prints but no create: a count outside every limit
        self.assertEqual(r["p1b_canonical_no_create"], 1 + PRE_PERIOD_MINTS)
        self.assertEqual(r["no_pool_mints"], ["MINTC"])
        self.assertEqual([c["mint"] for c in r["cells"]], ["MINTB"])
        self.assertEqual(r["n_creates"], 5)
        self.assertEqual(r["n_creates_with_migration"], 5)

    def test_creator_history_uses_all_creates_before_exclusion(self):
        src = p1b_source()
        self.assertEqual(len(src.creator_hist["creatorZ"]), 5)  # B, C, D, E, F: the excluded ones still count as the creator's earlier creates
        self.assertEqual(len(x.creator_history(src.creates)["creatorZ"]), 2)

    def test_p1b_rows_of_unrelated_pumpswap_mints_are_not_kept(self):
        src = p1b_source()
        self.assertNotIn("MINTY", src.rows_by_mint)
        self.assertNotIn("MINTZ", src.rows_by_mint)  # canonical prints but no create: counted, no rows kept
        self.assertNotIn("MINTZ", src.migrations)

    def test_pre_period_mints_fire_no_limit_and_are_outside_the_no_create_share(self):
        src = p1b_source()
        r = x.process_source(src, {"CANON_MINTB": 17_584_000_000}, fake_canon)
        self.assertEqual(r["no_create_row"], [])
        self.assertEqual(r["p1b_canonical_no_create"], 1 + PRE_PERIOD_MINTS)
        self.assertEqual(r["no_create_window"], 0)
        self.assertFalse([w for w in x.check_limits([r]) if "no-create" in w])
        self.assertEqual(r["p1b_cap_denominator"], 3)  # B, D, F: post-start creates with a migration (E is a pre-tape create, C has no pool)

    def test_no_creates_means_no_migrations(self):
        src = p1b_source(with_creates=False)
        self.assertEqual(src.creates, {})
        self.assertEqual(src.migrations, {})  # no create row: no migration kept, and the canonical-print mints are only counted

    def test_create_slot_rule_keeps_real_slots_and_derives_placeholders(self):
        creates = {"R": {"mint": "R", "slot": 500, "block_time": 10}, "P": {"mint": "P", "slot": 0, "block_time": 10}, "N": {"mint": "N", "slot": None, "block_time": 10}}
        rows = {"P": [{"venue": "pump_bonding", "slot": 90, "block_time": 20}, {"venue": "pump_bonding", "slot": 80, "block_time": 18}, {"venue": "pumpswap", "slot": 1}],
                "R": [{"venue": "pump_bonding", "slot": 7, "block_time": 12}]}
        out, excl, st = x.apply_create_slot_rule(creates, rows)
        self.assertEqual((out["R"]["slot"], out["P"]["slot"], excl), (500, 80, ["N"]))  # a real slot is kept; the first (lowest-slot) bonding print wins
        self.assertEqual(st["gap_s"]["max"], 8)
        self.assertEqual((st["n_placeholder_slot"], st["n_with_bonding"]), (2, 1))

    def test_precount_style_counts_for_p1c_real_slots_report_zero_placeholders(self):
        creates, trades = mint_tape("MINTB", T0)
        with tempfile.TemporaryDirectory() as d:
            tp, cp = Path(d) / "trades.jsonl", Path(d) / "creates.jsonl"
            tp.write_text("".join(json.dumps(t) + "\n" for t in trades))
            cp.write_text("".join(json.dumps(c) + "\n" for c in creates))
            src = x.load_source_data("P1C", "P1", lambda h: {"trade": tp, "create": cp}, ["h0"], [])
        self.assertEqual(src.create_stats["n_placeholder_slot"], 0)
        self.assertEqual(src.creates["MINTB"]["slot"], 1000)


def _res(tag, *, cells=30, n_mig=100, no_create=0, no_pool=0, no_bonding=0, with_mig=100, foreign=0, pre_tape_mig=0, gap=0, den=100, n_win=None):
    return {"tag": tag, "cells": [{"status": "FILLED", "pool": f"p{i}", "mint": f"m{i}", "block": "P1", "mig_ms": e15.date_start_ms(P1[0]) + 12 * 3_600_000} for i in range(cells)], "n_migrations": n_mig,
            "no_create_row": ["a"] * no_create, "no_pool_mints": ["b"] * no_pool, "no_migration_slot": [], "no_bonding_excluded": ["c"] * no_bonding,
            "n_creates_with_migration": with_mig, "n_creates": with_mig, "create_stats": None, "p1b_gap_slots": None,
            "gate": {"foreign_first_mints": ["f"] * foreign, "mints_with_foreign_pool_prints": 0}, "slot_inversions": 0, "pool_vs_canonical": None,
            "pre_tape_migration_excluded": ["d"] * pre_tape_mig, "gap_excluded": ["g"] * gap, "p1b_cap_denominator": den, "p1b_canonical_no_create": 0,
            **({"n_migrations_window": n_win, "no_create_window": no_create, "no_pool_window": no_pool} if n_win is not None else {})}


class RefusalLimitTests(unittest.TestCase):
    def test_limits_pass_at_the_boundary(self):
        self.assertEqual(x.check_limits([_res("P1A", no_create=2, no_pool=2), _res("P1B", pre_tape_mig=5, gap=5, den=100)]), [])

    def test_zero_cells_refuses(self):
        self.assertIn("P2: 0 cells", x.check_limits([_res("P2", cells=0)]))

    def test_no_create_row_share_over_two_percent_refuses(self):
        (why,) = x.check_limits([_res("P3", no_create=3)])
        self.assertIn("P3", why)
        self.assertIn("no-create-row", why)

    def test_no_pool_share_over_two_percent_refuses(self):
        (why,) = x.check_limits([_res("P4", no_pool=3)])
        self.assertIn("no-pool", why)

    def test_p1b_pre_tape_migration_cap(self):
        (why,) = x.check_limits([_res("P1B", pre_tape_mig=6, den=100)])
        self.assertIn("pre-tape migrations", why)
        self.assertEqual(x.check_limits([_res("P1A", pre_tape_mig=6, den=100)]), [])  # P1B only

    def test_p1b_gap_over_cap(self):
        (why,) = x.check_limits([_res("P1B", gap=6, den=100)])
        self.assertIn("gap over 600 s", why)

    def test_pre_tape_creates_and_no_bonding_have_no_cap(self):
        r = _res("P1B", no_bonding=90, den=100)
        r["pre_tape_create_excluded"] = ["e"] * 90
        self.assertEqual(x.check_limits([r]), [])  # reported only

    def test_denominators_are_the_windowed_migrations(self):
        self.assertEqual(x.check_limits([_res("P2", no_create=2, n_win=100, n_mig=10_000)]), [])  # 2 of the 100 in window, not 2 of 10,000
        self.assertEqual(len(x.check_limits([_res("P2", no_create=3, n_win=100, n_mig=10_000)])), 1)

    def test_raise_variant_is_a_refusal(self):
        with self.assertRaises(x.Refused):
            x.enforce_limits([_res("P3", no_create=3)])
        x.enforce_limits([_res("P3")])


class PrecountTests(unittest.TestCase):
    def _args(self, d):
        return SimpleNamespace(vmap=str(Path(d) / "map.json"), out_dir=Path(d) / "out", v_fallback_json=None, closed_pools_json=None, artifact_dir=Path(d), v_constancy_json=getattr(self, "cfile", None),
                               p1_fast_dir="/x1", p1_oracle_insample_dir="/x2", p1_oracle_live_dir="/x3", p3_root="/x4", p2_view_dir=["/x5"], p4_view_dir=None)

    def _run(self, d, sources, extra=()):
        import contextlib
        import io

        out = io.StringIO()
        with contextlib.ExitStack() as st:
            st.enter_context(mock.patch.object(x, "run_guards", return_value={"with_p4": False}))
            st.enter_context(mock.patch("tools.pumpswap_virtual.load_map", return_value=dict(VMAP)))
            st.enter_context(mock.patch("tools.exp012_forward_vmap.load_detail", return_value={"POOL1": {"v_base": 17_584_505_300}}))
            st.enter_context(mock.patch.object(x, "build_sources", return_value=[(t, "P1", None, [], []) for t in sources]))
            st.enter_context(mock.patch.object(x, "load_source_data", side_effect=lambda tag, *a, **k: fixture_source()))
            st.enter_context(mock.patch.object(x, "load_oof", return_value=({}, None)))
            st.enter_context(mock.patch.object(x, "frozen_flags", side_effect=lambda cells, *a, **k: [True] * len(cells)))
            st.enter_context(mock.patch.object(x, "log_all"))
            st.enter_context(mock.patch.object(e15, "take_lock"))
            for e in extra:
                st.enter_context(e)
            st.enter_context(contextlib.redirect_stdout(out))
            rc = x.precount(self._args(d))
        return rc, out.getvalue()

    def test_precount_prints_only_counts_and_writes_no_tries_or_lock(self):
        with tempfile.TemporaryDirectory() as d:
            rc, text = self._run(d, ["P1A"])
            files = sorted(p.name for p in (Path(d) / "out").iterdir())
            self.assertEqual(files, ["precount.json"])  # no lock, no report, no tries
            self.assertFalse(Path(d, "tries.jsonl").exists())
            rec = json.loads(text)
        self.assertEqual(rc, 0)
        src = rec["sources"]["P1A"]
        for k in ("creates", "migrations", "cells", "no_create_row", "no_pool", "no_migration_slot", "foreign_first", "censored", "no_sim_by_reason", "v_coverage", "pool_vs_canonical", "p1b_gap_slots"):
            self.assertIn(k, src)
        self.assertEqual((src["creates"], src["migrations"], src["cells"]), (1, 1, 1))
        for banned in ("net", "press", "flat", "rug", "label", "pnl", "exit_kind", "deadline_ms", "mig_ms"):
            self.assertNotIn(banned, text.lower().replace("foreign_pool_prints", ""))
        self.assertNotIn("MINTA", text)  # counts, never ids
        self.assertTrue(any("1 in-book cells" in w for w in rec["would_refuse"]))  # the in-book floor (30) is reported, not raised
        self.assertTrue(any("OOF" in w for w in rec["would_refuse"]))  # the mocked OOF map is empty: no stored score on the one P1 cell
        self.assertIn("p1_oof_score_counts", rec["pre_started"])
        self.assertIn("v_coverage", rec["pre_started"])
        self.assertEqual(rec["pre_started"]["lp_active_proxy"]["by_source"]["P1A"], {"selected": 1, "outside": 0, "no_v_base": 0})

    def test_precount_reports_would_refuse_instead_of_refusing(self):
        with tempfile.TemporaryDirectory() as d:
            rc, text = self._run(d, ["P1A"], extra=[mock.patch.object(x, "process_source", side_effect=lambda src, v, c=None: _res("P1A", cells=0))])
            rec = json.loads(text)
        self.assertEqual(rc, 0)
        self.assertIn("P1A: 0 cells", rec["would_refuse"])
        self.assertTrue(any("0 in-book cells" in w for w in rec["would_refuse"]))

    def test_precount_predicts_the_real_run_v_coverage_and_constancy(self):
        with tempfile.TemporaryDirectory() as d:
            rc, text = self._run(d, ["P1A"], extra=[mock.patch("tools.pumpswap_virtual.load_map", return_value={})])  # no pool has a readable V
            rec = json.loads(text)
        self.assertTrue(any(w.startswith("V coverage") for w in rec["would_refuse"]))
        self.assertTrue(rec["pre_started"]["v_coverage"]["would_refuse"])
        with tempfile.TemporaryDirectory() as d:
            c = Path(d) / "c.json"
            c.write_text(json.dumps([{"pool": "POOL1", "v_implied": 17_584_000_000, "quote_reserve": 10 * SOL}]))
            self.cfile = c  # one sampled pool: far below the pinned sample size, and not the seeded P2 draw
            try:
                rc, text = self._run(d, ["P1A"])
            finally:
                del self.cfile
            rec = json.loads(text)
        self.assertEqual(rc, 0)
        self.assertTrue(any(w.startswith("constancy:") for w in rec["would_refuse"]))

    def test_precount_runs_with_the_map_unpinned(self):
        self.assertEqual(x.VMAP_EXP016_SHA256, "PENDING")  # the pin is the manager's; the precount must not need it
        with tempfile.TemporaryDirectory() as d:
            rc, text = self._run(d, ["P1A"])
        self.assertIn("UNPINNED", text)

    def test_real_run_enforces_the_limits_before_started(self):
        import inspect

        src = inspect.getsource(x.main)
        self.assertLess(src.index("enforce_limits(results, oof)"), src.index("e15.take_lock"))
        with tempfile.TemporaryDirectory() as d, mock.patch.object(x, "log_all") as lg:
            rc, exc, text = MainExceptionHygieneTests()._run(d, [], limits=["P1A: 0 cells"])
            lg.assert_not_called()
        self.assertEqual((rc, exc), (2, None))
        self.assertIn("plan 13 item 9", text)


class TiesAndCountsTests(unittest.TestCase):
    def test_all_tied_probabilities_veto_nothing(self):
        tb = good_book()
        with mock.patch.object(x.eem, "predict_setting", side_effect=lambda fit, X: [0.5] * len(X)):
            f = x.outer_fold(tb, NON_P1[0])
        for cid in ("l5", "l10"):
            self.assertEqual(f["thresholds"][cid], 0.5)
            self.assertFalse(any(f["per_candidate"][cid].values()))  # rows tied AT the threshold are not vetoed
            self.assertEqual(f["inner"][cid]["n_vetoed_filled"], 0)
            self.assertFalse(f["inner"][cid]["eligible"])  # under the 30 vetoed-fills floor

    def test_only_strictly_greater_probabilities_are_vetoed(self):
        tb = good_book()
        with mock.patch.object(x.eem, "predict_setting", side_effect=lambda fit, X: [0.9 if i < 3 else 0.5 for i in range(len(X))]):
            f = x.outer_fold(tb, NON_P1[0])
        veto = [m for m, v in f["per_candidate"]["l10"].items() if v]
        self.assertTrue(len(veto) <= 3)
        self.assertTrue(all(f["per_candidate"]["l10"][r["mint"]] is False for r in tb if r["date"] == NON_P1[0] and r["mint"] not in veto))

    def test_p1_frozen_selected_foreign_first_count(self):
        r = _res("P1A", cells=3, foreign=2)
        r["gate"]["foreign_first_mints"] = ["m0", "m1"]
        for c in r["cells"]:
            c.update(source="P1A", status="NO_SIM")
        pc = x.pre_started_counts([r], {"m0": True, "m1": False, "m2": True}, {}, closed=[])
        self.assertEqual(pc["p1_frozen_selected_foreign_first"], {"P1A": 1})  # m0 only: selected and foreign-first

    def test_lp_active_proxy_counts_pools_outside_the_cluster_by_more_than_1000(self):
        lo, hi = x.LP_V_CLUSTER
        detail = {"a": {"v_base": lo}, "b": {"v_base": hi + 1000}, "c": {"v_base": hi + 1001}, "d": {"v_base": lo - 1001}, "e": {"v_base": None}}
        cells = [{"mint": k, "pool": k, "source": "P2"} for k in "abcde"] + [{"mint": "z", "pool": "a", "source": "P2"}]
        out = x.lp_active_proxy(cells, {k: True for k in "abcde"}, detail)
        self.assertEqual(out["by_source"]["P2"], {"selected": 5, "outside": 2, "no_v_base": 1})
        self.assertEqual(x.lp_active_proxy(cells, {}, None), {"available": False})


class PriorTriesPerExperimentTests(unittest.TestCase):
    def test_counts_by_experiment_from_the_given_log_and_excludes_exp016(self):
        blk = [{"start_hour": e15.BLOCKS["P2"][0], "end_hour_exclusive": e15.BLOCKS["P2"][1]}]
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "t.jsonl"
            lines = [{"config": {"experiment": "EXP-015 screen"}, "data_blocks": blk, "tool": "a"}, {"config": {"experiment": "EXP-015 screen"}, "data_blocks": [], "tool": "a"},
                     {"config": {"event": "started"}, "data_blocks": blk, "tool": "exp013_grad"}, {"config": {"key": "exp016_started"}, "data_blocks": blk, "tool": "x"}]
            log.write_text("".join(json.dumps(r) + "\n" for r in lines))
            got = x.prior_tries_per_experiment(log, True)
        self.assertEqual(set(got), {"EXP-015 screen", "exp013_grad"})
        self.assertEqual((got["EXP-015 screen"]["total"], got["EXP-015 screen"]["P2"]), (2, 1))
        self.assertEqual(got["exp013_grad"]["P2"], 1)

    def test_started_extra_records_it_from_the_canonical_path(self):
        import inspect

        self.assertIn("prior_tries_per_experiment(canonical, with_p4)", inspect.getsource(x.main))


class DataQualityGuardTests(unittest.TestCase):
    def _cells(self, n_in_book, n_no_sim=0):
        base = e15.date_start_ms(P1[0]) + 12 * 3_600_000
        cs = [{"status": "FILLED", "pool": f"p{i}", "mint": f"m{i}", "block": "P1", "mig_ms": base} for i in range(n_in_book)]
        cs += [{"status": "NO_SIM", "why": "x", "pool": f"q{i}", "mint": f"n{i}", "block": "P1"} for i in range(n_no_sim)]
        return cs

    def test_a_source_with_all_no_sim_cells_refuses(self):
        r = _res("P1B", cells=0)
        r["cells"] = self._cells(0, n_no_sim=100)
        why = x.check_limits([r])
        self.assertTrue(any("0 in-book cells" in w for w in why))
        self.assertTrue(any("NO_SIM 100 of 100" in w for w in why))
        with self.assertRaises(x.Refused):
            x.enforce_limits([r])

    def test_named_constants_and_boundaries(self):
        self.assertEqual((x.LIMIT_MIN_IN_BOOK, x.LIMIT_NO_SIM_SHARE, x.LIMIT_P1_NO_OOF), (30, 0.25, 0.02))
        r = _res("P2", cells=0)
        r["cells"] = self._cells(30, n_no_sim=10)  # 30 in book; 10 of 40 = 25% is not over
        self.assertEqual(x.check_limits([r]), [])
        r["cells"] = self._cells(29)
        self.assertEqual(len(x.check_limits([r])), 1)
        r["cells"] = self._cells(30, n_no_sim=11)  # 11 of 41 > 25%
        (why,) = x.check_limits([r])
        self.assertIn("NO_SIM", why)

    def test_censored_cells_do_not_count_in_book(self):
        r = _res("P3", cells=0)
        r["cells"] = self._cells(29) + [{"status": "CENSORED", "pool": "pc", "mint": "mc", "block": "P1", "mig_ms": e15.date_start_ms(P1[0]) + 12 * 3_600_000}]
        self.assertEqual(len(x.in_book_cells(r["cells"])), 29)
        self.assertEqual(len(x.check_limits([r])), 1)

    def test_p1_oof_score_match(self):
        r = _res("P1A", cells=100)
        oof = {f"m{i}": 0.9 for i in range(100)}
        self.assertEqual(x.check_limits([r], oof), [])
        self.assertEqual(x.oof_counts([r], oof), {"P1A": {"in_book": 100, "with_score": 100, "without_score": 0}})
        for i in range(2):
            del oof[f"m{i}"]
        self.assertEqual(x.check_limits([r], oof), [])  # 2% is not over
        del oof["m2"]
        (why,) = x.check_limits([r], oof)
        self.assertIn("OOF", why)
        with self.assertRaises(x.Refused):
            x.enforce_limits([r], oof)
        self.assertEqual(x.check_limits([r]), [])  # no OOF map given: that limit is not evaluated here (main always passes it)

    def test_oof_counts_only_cover_p1_sources(self):
        r = _res("P2", cells=40)
        for c in r["cells"]:
            c["block"] = "P2"
        self.assertEqual(x.oof_counts([r], {}), {})

    def test_main_passes_oof_to_the_limits_and_prior_experiment_is_before_the_lock(self):
        import inspect

        src = inspect.getsource(x.main)
        self.assertIn("enforce_limits(results, oof)", src)
        self.assertLess(src.index("prior_exp = prior_tries_per_experiment"), src.index("e15.take_lock"))

    def test_every_migration_row_is_kept_and_the_window_is_only_for_the_denominators(self):
        creates, trades = mint_tape("MINTB", T0)
        from datetime import datetime, timezone

        hour = datetime.fromtimestamp(T0, timezone.utc).strftime("%Y-%m-%dT%H")
        with tempfile.TemporaryDirectory() as d:
            tp, cp = Path(d) / "trades.jsonl", Path(d) / "creates.jsonl"
            (Path(d) / "migrations").mkdir()
            tp.write_text("".join(json.dumps(t) + "\n" for t in trades))
            cp.write_text("".join(json.dumps(c) + "\n" for c in creates))
            rows = [{"type": "migration", "mint": "MINTB", "slot": 1050, "pool": "P", "block_time": T0 + 10}, {"type": "migration", "mint": "OLD", "slot": 5, "pool": "Q", "block_time": T0 - 90 * 86400}]
            with mock.patch("tools.exp012_virtual_rescore._zcat_lines", return_value=[json.dumps(r) for r in rows]):
                (Path(d) / "migrations" / "migrations-x.jsonl.zst").write_bytes(b"")
                src = x.load_source_data("P3", "P3", lambda h: {"trade": tp, "create": cp}, [hour], [Path(d)])
        self.assertEqual(sorted(src.migrations), ["MINTB", "OLD"])  # nothing dropped at read: d-group features and dump steps are unchanged
        a, z = e15.BLOCKS["P2"]
        inside = {"block_time": e15.hour_ms(a) // 1000 + 3600}
        self.assertTrue(x.in_counted_window("P2", inside))  # exactly e15.in_block_window
        self.assertFalse(x.in_counted_window("P2", {"block_time": e15.hour_ms(z) // 1000}))  # the end is exclusive
        self.assertFalse(x.in_counted_window("P2", {"block_time": e15.hour_ms(a) // 1000 - 1}))
        self.assertTrue(x.in_counted_window("P2", {}))  # no time (P1B derived rows): counted inside

    def test_hours_are_read_in_time_order_so_a_create_precedes_its_trades(self):
        creates, trades = mint_tape("MINTB", T0)
        with tempfile.TemporaryDirectory() as d:
            ct, cc = Path(d) / "ct.jsonl", Path(d) / "cc.jsonl"
            e, ec = Path(d) / "empty_t.jsonl", Path(d) / "empty_c.jsonl"
            cc.write_text("".join(json.dumps(c) + "\n" for c in creates))
            ct.write_text("")
            e.write_text("".join(json.dumps(t) + "\n" for t in trades))
            ec.write_text("")
            files = {"2026-09-01T01": {"trade": ct, "create": cc}, "2026-09-01T02": {"trade": e, "create": ec}}
            src = x.load_source_data("P3", "P3", lambda h: files[h], ["2026-09-01T02", "2026-09-01T01"], [])  # given out of order
        self.assertEqual(len(src.rows_by_mint["MINTB"]), len([t for t in trades if t["venue"] == "pump_bonding"]))

    def test_a_create_after_its_trades_refuses_with_counts_only(self):
        creates, trades = mint_tape("MINTB", T0)
        with tempfile.TemporaryDirectory() as d:
            tp, cp = Path(d) / "t.jsonl", Path(d) / "c.jsonl"
            tp.write_text("".join(json.dumps(t) + "\n" for t in trades))
            cp.write_text("".join(json.dumps(c) + "\n" for c in creates))
            files = {"2026-09-01T01": {"trade": tp, "create": None}, "2026-09-01T02": {"trade": Path(d) / "none.jsonl", "create": cp}}
            (Path(d) / "none.jsonl").write_text("")
            with self.assertRaises(x.Refused) as cm:
                x.load_source_data("P3", "P3", lambda h: files[h], ["2026-09-01T01", "2026-09-01T02"], [])
        self.assertIn("1 create row(s) appear after their mint's trades", str(cm.exception))
        self.assertNotIn("MINTB", str(cm.exception))


class MemoryRegressionTests(unittest.TestCase):
    def _tape(self, d):
        creates, trades = mint_tape("MINTB", T0)  # create + bonding + pumpswap
        _c2, other = mint_tape("MINTU", T0 + 5)  # no create row and no migration: irrelevant to this source
        tp, cp, mp = Path(d) / "trades.jsonl", Path(d) / "creates.jsonl", Path(d)
        tp.write_text("".join(json.dumps(t) + "\n" for t in trades + other))
        cp.write_text("".join(json.dumps(c) + "\n" for c in creates))
        return tp, cp, trades, other

    def test_rows_of_mints_without_a_create_or_migration_are_not_retained(self):
        with tempfile.TemporaryDirectory() as d:
            tp, cp, trades, other = self._tape(d)
            src = x.load_source_data("P3", "P3", lambda h: {"trade": tp, "create": cp}, ["h0"], [])
        self.assertEqual(set(src.rows_by_mint), {"MINTB"})  # MINTU's rows were dropped at read time
        self.assertNotIn("MINTU", src.rows_by_mint)

    def test_migrated_mints_keep_all_rows_and_others_keep_curve_rows_only(self):
        creates, trades = mint_tape("MINTB", T0)
        with tempfile.TemporaryDirectory() as d:
            tp, cp = Path(d) / "trades.jsonl", Path(d) / "creates.jsonl"
            (Path(d) / "migrations").mkdir()
            (Path(d) / "migrations" / "migrations-x.jsonl.zst").write_bytes(b"")
            tp.write_text("".join(json.dumps(t) + "\n" for t in trades))
            cp.write_text("".join(json.dumps(c) + "\n" for c in creates))
            row = {"type": "migration", "mint": "MINTB", "slot": 1050, "pool": "P", "block_time": T0 + 10}
            from datetime import datetime, timezone

            hour = datetime.fromtimestamp(T0, timezone.utc).strftime("%Y-%m-%dT%H")
            with mock.patch("tools.exp012_virtual_rescore._zcat_lines", return_value=[json.dumps(row)]):
                src = x.load_source_data("P3", "P3", lambda h: {"trade": tp, "create": cp}, [hour], [Path(d)])
        self.assertEqual(len(src.rows_by_mint["MINTB"]), len(trades))  # migrated: every row

    def test_process_source_frees_the_rows_of_never_migrated_mints(self):
        src = fixture_source()
        creates, trades = mint_tape("MINTQ", T0 + 3)
        src.creates["MINTQ"] = creates[0]
        src.rows_by_mint["MINTQ"] = [t for t in trades if t["venue"] == "pump_bonding"]
        x.process_source(src, VMAP)
        self.assertEqual(set(src.rows_by_mint), {"MINTA"})

    def test_progress_lines_are_counts_only_and_carry_rss(self):
        import contextlib
        import io

        err = io.StringIO()
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stderr(err):
            tp, cp, trades, other = self._tape(d)
            x.load_source_data("P3", "P3", lambda h: {"trade": tp, "create": cp}, ["h0"], [], progress_every_hour=True)
        text = err.getvalue()
        for needle in ("P3: load start", "hour h0 rows_read=", "tape pass done rows_read=", "mints_kept=1", "rss_mb="):
            self.assertIn(needle, text)
        for banned in ("net", "press", "flat", "label", "pnl"):
            self.assertNotIn(banned, text.lower())


if __name__ == "__main__":
    unittest.main()
