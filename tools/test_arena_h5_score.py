"""tools/arena_h5_score.py: the arena H5 scorer. Synthetic engine fixtures, plus one replay of exploration tape (skipped where pandas or the tape is absent).
Needs numpy (the audit or c1nf venv): python -m unittest tools.test_arena_h5_score"""
from __future__ import annotations

import collections
import contextlib
import io
import json
import math
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import arena_h5_score as sc
from tools import h5_shadow as h5
from tools.test_h5_shadow import make_engine
from tools.test_h5_shadow_fresh_forward import SHA, fresh_engine, pool_tape, run_pools

T0 = 1_800_000_000  # the synthetic pool's block time (2027-01-15T08:00:00Z)


def records(start_ms=T0 * 1000 - 5_000, pools=1, mutate=None):
    """The fresh-forward engine's records for `pools` synthetic pools, behind a start record."""
    eng, out = fresh_engine(start_ms)
    run_pools(eng, *[pool_tape(f"Pool{chr(65 + i)}{chr(65 + i)}{chr(65 + i)}{chr(65 + i)}", 1000 + 5000 * i, T0 + 1600 * i) for i in range(pools)])
    start = {"type": "start", "t_ms": start_ms, "start_ms": start_ms, "code_sha": SHA, "fresh_forward": "ARENA"}
    recs = [start] + out
    if mutate:
        mutate(recs)
    return recs


class Base(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(dir="/tmp", prefix="arena-score-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        p = mock.patch.object(h5, "ARENA_ROOT", str(self.root / "arena"))
        p.start()
        self.addCleanup(p.stop)
        self.ledger = self.root / "arena" / "ledger"

    def shadow_dir(self, recs, name="h5-shadow-test"):
        d = self.root / "arena" / name
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "h5-shadow-2027-01-15T08.jsonl", "w") as fh:
            for r in recs:
                fh.write(json.dumps(r) + "\n")
        return d

    def score(self, recs):
        counts = collections.Counter()
        return list(sc.trade_rows(self.shadow_dir(recs), counts)), counts


class PricingTests(Base):
    def test_rows_for_both_bots_all_stakes_and_the_ledger_fields(self):
        recs = records()
        rows, counts = self.score(recs)
        self.assertEqual(len(rows), 2 * 3)  # 2 bots x 3 stakes x 1 pool
        self.assertEqual({r["bot_id"] for r in rows}, {"h5v1-boostfloor", "h5v2-boostclock"})
        self.assertEqual(sorted({r["stake_sol"] for r in rows}), [0.1, 0.25, 0.3])
        trig = next(r for r in recs if r["type"] == "trigger" and r["variant"] == "pv")
        for r in rows:
            self.assertEqual(r["trade_id"], f"{r['bot_id']}:PoolAAAA:{'%.2f' % r['stake_sol']}")
            self.assertEqual((r["frozen_sha"], r["prog"], r["pool"], r["mint"]), (SHA, "pumpswap", "PoolAAAA", "MintPoolAAAA"))
            self.assertEqual(r["decision_ms"], trig["t_detect_ms"])
            self.assertGreater(r["exit_ms"], r["decision_ms"])
            self.assertEqual(set(r["legs"]), {"flat_1p3", "flat_1p9", "press_1p3", "press_1p9"})
            for leg in r["legs"].values():
                self.assertTrue(math.isfinite(leg["net_lamports"]))
            json.dumps(r, allow_nan=False)
        self.assertEqual(sum(v for k, v in counts.items() if k.startswith("skipped")), 0)

    def test_v1_nofail_equals_the_shadows_own_fill_at_its_two_stakes_and_the_fail_models_follow(self):
        recs = records()
        rows, _ = self.score(recs)
        o = next(r for r in recs if r["type"] == "outcome" and r["variant"] == "pv")
        for r in rows:
            if r["bot_id"] != "h5v1-boostfloor":
                continue
            label = "%.1f" % r["stake_sol"] if r["stake_sol"] in (0.1,) else "%.2f" % r["stake_sol"]
            for leg, sfx in sc.LEGS:
                flat, press = r["legs"][f"flat_{sfx}"], r["legs"][f"press_{sfx}"]
                self.assertAlmostEqual(flat["nofail_lamports"], press["nofail_lamports"], places=6)
                nf = flat["nofail_lamports"]
                self.assertAlmostEqual(flat["net_lamports"], 0.85 * nf + 0.15 * -55_000, places=3)
                self.assertAlmostEqual(press["net_lamports"], (1 - press["p_fail"]) * nf + press["p_fail"] * -55_000, places=3)
                self.assertTrue(0 < press["p_fail"] < 1)
                if label in ("0.1", "0.25"):
                    self.assertAlmostEqual(nf, o["legs"][leg]["net"][label]["pnl_nofail_lamports"], delta=1e-3)

    def test_v2_uses_the_bx10_exit_state_and_the_same_entry(self):
        recs = records()
        rows, _ = self.score(recs)
        bx = next(r for r in recs if r["type"] == "outcome_bx10" and r["variant"] == "pv")
        for r in rows:
            if r["bot_id"] != "h5v2-boostclock" or r["stake_sol"] != 0.1:
                continue
            for leg, sfx in sc.LEGS:
                self.assertAlmostEqual(r["legs"][f"flat_{sfx}"]["nofail_lamports"], bx["legs"][leg]["net"]["0.1"]["pnl_nofail_lamports"], delta=1e-3)
            self.assertEqual(r["meta"]["exit_rule"], "bx10")

    def test_a_stake_above_the_impact_limit_is_refused(self):
        with self.assertRaises(ValueError):
            sc.price_leg(40.0, 2e14, 38.0, 2.1e14, 300_000_001, 1, 10**9, "primary")
        sc.price_leg(40.0, 2e14, 38.0, 2.1e14, 300_000_000, 1, 10**9, "primary")

    def test_frozen_intercepts_reproduce_the_target_mean_on_a_book_that_has_the_exploration_pressure_mix(self):
        # refit on any one book gives mean p = 0.289 by construction; the frozen values are the exploration book's and are plain numbers
        self.assertEqual(sc.PRESS_INTERCEPT.keys(), {"primary", "binding"})
        rows, _ = self.score(records(pools=3))
        fit = sc.refit_press_intercepts([r for r in rows if r["bot_id"] == "h5v1-boostfloor"])
        self.assertEqual(set(fit), {"primary", "binding"})
        from tools.latency_curve import FailCurve, Pressure

        ps = [Pressure(r["legs"]["press_1p3"]["ssb"], r["legs"]["press_1p3"]["nb_lamports"]) for r in rows if r["bot_id"] == "h5v1-boostfloor" and r["stake_sol"] == 0.1]
        mean_p = sum(FailCurve(fit["primary"], 0.8, 0.35, 1.0).p(p) for p in ps) / len(ps)
        self.assertAlmostEqual(mean_p, 0.289, places=6)


class SelectionTests(Base):
    def test_a_trade_decided_before_the_start_record_is_not_scored(self):
        late = T0 * 1000 + 10 ** 7
        rows, counts = self.score(records(start_ms=late))  # the engine itself would ignore the pool; force it through to test the scorer's own check
        self.assertEqual(rows, [])
        recs = records()
        recs[0]["start_ms"] = late
        rows, counts = self.score(recs)
        self.assertEqual(rows, [])
        self.assertEqual(counts["skipped_pre_start"], 2)  # one per bot

    def test_incomplete_outcomes_are_counted_and_not_scored(self):
        def incomplete(recs):
            for r in recs:
                if r["type"] in ("outcome", "outcome_bx10"):
                    r["complete"] = False
        rows, counts = self.score(records(mutate=incomplete))
        self.assertEqual((rows, counts["skipped_incomplete"]), ([], 2))

    def test_only_the_pv_variant_is_a_trade(self):
        recs = records()
        rows, _ = self.score([r for r in recs if r.get("variant") != "pv"])
        self.assertEqual(rows, [])
        rows, _ = self.score(recs)
        self.assertEqual({r["pool"] for r in rows}, {"PoolAAAA"})

    def test_a_record_before_any_start_has_no_clock(self):
        rows, counts = self.score(records()[1:])
        self.assertEqual((rows, counts["skipped_no_start"]), ([], 2))

    def test_a_restart_in_the_same_dir_scores_each_trade_against_its_own_start(self):
        a, b = records(), records()
        for r in b:
            if r["type"] in ("start",):
                r["code_sha"] = "f" * 40
        # the second run's records follow the second start: pool A belongs to the first start, and a second copy of it is a duplicate in the ledger
        rows, _ = self.score(a + b)
        self.assertEqual({r["frozen_sha"] for r in rows}, {SHA})

    def test_bx10_error_record_is_counted(self):
        def boom(recs):
            for r in recs:
                if r["type"] == "outcome_bx10":
                    r.pop("legs")
                    r["error"] = "RuntimeError('x')"
        rows, counts = self.score(records(mutate=boom))
        self.assertEqual({r["bot_id"] for r in rows}, {"h5v1-boostfloor"})
        self.assertEqual(counts["skipped_bx10_error"], 1)


class LedgerTests(Base):
    def test_write_once_by_trade_id_and_day_files(self):
        rows, _ = self.score(records(pools=2))
        counts = collections.Counter()
        sc.write_ledger(rows, self.ledger, counts)
        self.assertEqual((counts["rows_h5v1-boostfloor"], counts["rows_h5v2-boostclock"], counts["duplicate"]), (6, 6, 0))
        day = sc._utc_day(rows[0]["decision_ms"])
        f = self.ledger / "h5v1-boostfloor" / f"trades-{day}.jsonl"
        before = f.read_bytes()
        lines = before.decode().splitlines()
        self.assertEqual(len(lines), 6)
        self.assertEqual(len({json.loads(x)["trade_id"] for x in lines}), 6)
        counts2 = collections.Counter()
        sc.write_ledger(rows, self.ledger, counts2)  # again: nothing is rewritten
        self.assertEqual((counts2["duplicate"], counts2["rows_h5v1-boostfloor"]), (12, 0))
        self.assertEqual(f.read_bytes(), before)
        for line in lines:
            json.loads(line, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))  # strict JSON

    def test_a_changed_row_with_an_existing_id_does_not_replace_it(self):
        rows, _ = self.score(records())
        sc.write_ledger(rows, self.ledger, collections.Counter())
        changed = json.loads(json.dumps(rows))
        for r in changed:
            r["legs"]["flat_1p3"]["net_lamports"] = 123.0
        sc.write_ledger(changed, self.ledger, collections.Counter())
        written = [json.loads(x) for f in (self.ledger / "h5v1-boostfloor").glob("trades-*.jsonl") for x in f.read_text().splitlines()]
        self.assertTrue(all(w["legs"]["flat_1p3"]["net_lamports"] != 123.0 for w in written))

    def test_a_non_finite_number_is_refused_and_nothing_is_written_for_it(self):
        rows, _ = self.score(records())
        rows[0]["legs"]["flat_1p3"]["net_lamports"] = float("nan")
        with self.assertRaises(ValueError):
            sc.write_ledger(rows[:1], self.ledger, collections.Counter())
        self.assertEqual(list((self.ledger / rows[0]["bot_id"]).glob("trades-*.jsonl")), [])

    def test_dry_run_writes_nothing(self):
        rows, _ = self.score(records())
        counts = collections.Counter()
        sc.write_ledger(rows, self.ledger, counts, dry_run=True)
        self.assertEqual(counts["rows_h5v1-boostfloor"], 3)
        self.assertFalse(self.ledger.exists())


class MainTests(Base):
    def main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = sc.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_refusals(self):
        d = self.shadow_dir(records())
        for argv in (["--shadow-dir", "/srv/mal-h5-shadow"], ["--shadow-dir", "/tmp/x"], ["--shadow-dir", str(d), "--ledger-root", "/tmp/ledger"],
                     ["--shadow-dir", str(d), "--ledger-root", str(self.ledger / ".." / "x")]):
            rc, _, err = self.main(*argv)
            self.assertEqual(rc, 2, argv)
            self.assertIn("REFUSED", err)

    def test_a_run_prints_counts_only_and_writes_the_ledger(self):
        d = self.shadow_dir(records())
        rc, out, _ = self.main("--shadow-dir", str(d), "--ledger-root", str(self.ledger))
        self.assertEqual(rc, 0)
        res = json.loads(out)
        self.assertEqual(res["counts"]["rows_scored"], 6)
        self.assertNotIn("lamports", out)
        self.assertEqual(len(list(self.ledger.glob("*/trades-*.jsonl"))), 2)

    def test_the_default_dirs_are_the_arena_shadow_dirs(self):
        self.shadow_dir(records(), "h5-shadow-2027-01-15")
        self.shadow_dir(records(), "h5-smoke-1")
        self.assertEqual([Path(p).name for p in sc.default_shadow_dirs()], ["h5-shadow-2027-01-15"])


TAPE = "/data/mal/audit-1008/tape"
HOUR = "2026-09-20T12"


def _have_replay() -> bool:
    try:
        import pandas  # noqa: F401
        import pyarrow  # noqa: F401
    except ImportError:
        return False
    return Path(f"{TAPE}/trades/{HOUR}.parquet").exists() and Path(h5.WORK_DIR).exists()


@unittest.skipUnless(_have_replay(), "needs pandas and the exploration tape on this host")
class ExplorationReplayTests(Base):
    """One hour of the exploration tape through the same engine, scored: the scorer's v1 no-fail numbers equal the shadow's own (0.1 and 0.25 SOL)."""

    def test_scorer_matches_the_shadow_on_replayed_exploration_tape(self):
        rows_in, meta, sps_pool = h5.replay_rows(TAPE, h5.WORK_DIR, [HOUR])
        recs: list[dict] = []
        eng = h5.Engine(recs.append, boost_mode="auto", sps_fn=lambda p: sps_pool.get(p.pool), classifier=h5.PreEventClassifier(), bx10_enabled=True)
        h5.replay(rows_in, meta, eng)
        recs.insert(0, {"type": "start", "t_ms": 0, "start_ms": 0, "code_sha": SHA})
        for r in recs:
            r.setdefault("t_ms", 0)
        # detect instants of replay records are tape time: the start record at 0 is before all of them
        rows, counts = self.score(recs)
        outcomes = [r for r in recs if r["type"] == "outcome" and r["variant"] == "pv" and r["complete"]]
        self.assertGreater(len(outcomes), 0)
        self.assertEqual(len([r for r in rows if r["bot_id"] == "h5v1-boostfloor"]), 3 * len([o for o in outcomes if any(l["net"] for l in o["legs"].values())]))
        by_pool = {o["pool"]: o for o in outcomes}
        for r in rows:
            if r["bot_id"] != "h5v1-boostfloor" or r["stake_sol"] not in (0.1, 0.25):
                continue
            o = by_pool[r["pool"]]
            for leg, sfx in sc.LEGS:
                if o["legs"][leg]["net"]:
                    self.assertAlmostEqual(r["legs"][f"flat_{sfx}"]["nofail_lamports"], o["legs"][leg]["net"][str(r["stake_sol"])]["pnl_nofail_lamports"], delta=0.01)


if __name__ == "__main__":
    unittest.main()
