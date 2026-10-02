"""Tests for tools/exp013_grad_model.py. Synthetic fixtures only; no real data is read."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import random
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import tools.exp011_freeze as fz
import tools.exp013_grad_model as gm
import tools.exp013_grad_trigger as gt

DAYS = tuple(f"2026-08-{d:02d}" for d in range(10, 16))
UTC = timezone.utc


def synth_rows(days=DAYS, per_day=40, seed=5, ks=(1, 4, 8)) -> list[dict]:
    rng = random.Random(seed)
    rows = []
    for di, day in enumerate(days):
        for i in range(per_day):
            feats = {n: rng.random() for n in gm.FEATURE_NAMES}
            signal = feats["buy_sol"] + 0.5 * rng.random()
            for k in ks:
                miss = (i % 9 == 0)
                press = -500_000 if miss else int(round((signal - 0.9) * 40_000_000 + rng.gauss(0, 5_000_000)))
                rows.append(
                    {"mint": f"m{di}-{i:03d}", "day": day, "pool": "A", "entry_land_k": k, "filled": not miss, "flat": press - 1000,
                     "press": press, "features": dict(feats)}
                )
    return rows


class FeatureTests(unittest.TestCase):
    def test_order_and_count(self) -> None:
        self.assertEqual(len(gm.FEATURE_NAMES), 22)
        self.assertEqual(len(set(gm.FEATURE_NAMES)), 22)
        self.assertEqual(gm.FEATURE_NAMES, gt.GRAD_FEATURE_NAMES)
        self.assertEqual(gm.FEATURE_NAMES[:18], fz.FROZEN_FEATURE_NAMES)
        self.assertEqual(gm.FEATURE_NAMES[18:], gt.NEW_FEATURE_NAMES)

    def test_missing_feature_is_error_and_nan_passes(self) -> None:
        f = {n: 0.5 for n in gm.FEATURE_NAMES}
        bad = dict(f)
        del bad["buy_sol"]
        with self.assertRaises(ValueError):
            gm.vector(bad)
        f["buy_sol"] = float("nan")
        self.assertTrue(math.isnan(gm.vector(f)[gm.FEATURE_NAMES.index("buy_sol")]))
        rows = synth_rows(days=DAYS[:2], ks=(4,))
        for r in rows[::3]:
            r["features"]["buy_sol"] = float("nan")
        model = gm.fit(rows)
        scores = gm.predict(model, rows)
        self.assertTrue(all(0.0 <= s <= 1.0 and not math.isnan(s) for s in scores))


class LabelTests(unittest.TestCase):
    def test_label_and_training_rows(self) -> None:
        rows = synth_rows(days=DAYS[:2])
        tr = gm.training_rows(rows)
        self.assertEqual({r["entry_land_k"] for r in tr}, {4})
        self.assertEqual(len(tr), 80)
        self.assertEqual(gm.label({"press": 1}), 1)
        self.assertEqual(gm.label({"press": 0}), 0)
        self.assertEqual(gm.label({"press": -5}), 0)
        misses = [r for r in tr if not r["filled"]]
        self.assertTrue(misses)  # MISS rows are in the training rows, label 0
        self.assertTrue(all(gm.label(r) == 0 for r in misses))

    def test_miss_rows_enter_fit(self) -> None:
        rows = gm.training_rows(synth_rows(days=DAYS[:2]))
        with mock.patch("lightgbm.Dataset", wraps=__import__("lightgbm").Dataset) as ds:
            gm.fit(rows)
        self.assertEqual(ds.call_args.args[0].shape, (len(rows), 22))

    def test_params_match_exp011(self) -> None:
        import lightgbm as lgb

        rows = gm.training_rows(synth_rows(days=DAYS[:2]))
        with mock.patch("lightgbm.train", wraps=lgb.train) as tr:
            gm.fit(rows)
        p = tr.call_args.args[0]
        pos = sum(gm.label(r) for r in rows)
        self.assertEqual(p["seed"], fz.SEED)
        self.assertEqual(p["num_threads"], 1)
        self.assertTrue(p["deterministic"])
        self.assertEqual(p["num_leaves"], fz.LGB_PARAMS["num_leaves"])
        self.assertEqual(p["min_data_in_leaf"], fz.LGB_PARAMS["min_data_in_leaf"])
        self.assertEqual(p["learning_rate"], fz.LGB_PARAMS["learning_rate"])
        self.assertEqual(tr.call_args.kwargs["num_boost_round"], fz.LGB_PARAMS["rounds"])
        self.assertAlmostEqual(p["scale_pos_weight"], (len(rows) - pos) / pos)

    def test_single_class_refused(self) -> None:
        rows = gm.training_rows(synth_rows(days=DAYS[:2]))
        for r in rows:
            r["press"] = -1
        with self.assertRaises(ValueError):
            gm.fit(rows)


class ThresholdTests(unittest.TestCase):
    def test_percentile_parity(self) -> None:
        rng = random.Random(3)
        for n in (1, 2, 7, 100, 101):
            oof = [{"score": rng.random()} for _ in range(n)]
            want = fz._percentile(sorted(r["score"] for r in oof), 0.90)
            self.assertEqual(gm.pooled_threshold(oof)["threshold"], want)

    def test_outer_oof_covers_every_row_once(self) -> None:
        rows = synth_rows()
        oof, oinfo = gm.outer_lodo_oof(rows, DAYS)
        self.assertEqual(oinfo["n_skipped_folds"], 0)
        self.assertEqual(len(oof), len(DAYS) * 40)
        self.assertEqual(len({(r["day"], r["mint"]) for r in oof}), len(oof))
        thr = gm.pooled_threshold(oof)
        self.assertEqual(thr["percentile"], 0.90)


class NestedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rows = synth_rows()
        cls.sel, cls.info, cls.counts = gm.nested_lodo_select(cls.rows, DAYS)

    def test_shape(self) -> None:
        self.assertEqual([i["outer_day"] for i in self.info], list(DAYS))
        self.assertTrue(all(i["trained"] for i in self.info))
        self.assertEqual(sum(i["n_selected"] for i in self.info), len(self.sel))
        self.assertTrue(self.sel)
        self.assertTrue(all(s["score"] >= s["threshold"] for s in self.sel))
        self.assertEqual(len({(s["day"], s["mint"]) for s in self.sel}), len(self.sel))

    def test_day_d_never_in_its_own_threshold_or_model(self) -> None:
        d = DAYS[2]
        rows2 = copy.deepcopy(self.rows)
        rng = random.Random(9)
        for r in rows2:
            if r["day"] == d:
                r["press"] = -r["press"] - 1 if r["press"] > 0 else 7_000_000
                r["features"] = {n: rng.random() for n in gm.FEATURE_NAMES}
        # Day d's rows only change its test set: its threshold is identical, and so is the training
        # pool of its outer model; the other days' thresholds do change (d is in their inner pool).
        sel2, info2, _ = gm.nested_lodo_select(rows2, DAYS)
        i1 = next(i for i in self.info if i["outer_day"] == d)
        i2 = next(i for i in info2 if i["outer_day"] == d)
        self.assertEqual(i1["threshold"], i2["threshold"])
        self.assertEqual(i1["n_inner_oof"], i2["n_inner_oof"])
        # Labels alone (features unchanged): d's scores must be bit-identical.
        rows3 = copy.deepcopy(self.rows)
        for r in rows3:
            if r["day"] == d:
                r["press"] = 99_000_000 if r["press"] <= 0 else -1
        sel3, info3, _ = gm.nested_lodo_select(rows3, DAYS)
        self.assertEqual([s for s in self.sel if s["day"] == d], [s for s in sel3 if s["day"] == d])
        self.assertEqual(next(i for i in info3 if i["outer_day"] == d)["threshold"], i1["threshold"])
        other = DAYS[3]
        self.assertNotEqual(
            next(i for i in info3 if i["outer_day"] == other)["threshold"], next(i for i in self.info if i["outer_day"] == other)["threshold"]
        )

    def test_n_jobs_equivalence(self) -> None:
        sel2, info2, _ = gm.nested_lodo_select(self.rows, DAYS, n_jobs=2)
        self.assertEqual(json.dumps(self.sel), json.dumps(sel2))
        self.assertEqual(json.dumps(self.info), json.dumps(info2))

    def test_skipped_folds(self) -> None:
        rows = synth_rows(days=DAYS[:3], per_day=5)  # 10 rows in the other days: below 20
        sel, info, _ = gm.nested_lodo_select(rows, DAYS[:3])
        self.assertEqual(sel, [])
        self.assertTrue(all(not i["trained"] and i["skip_reason"] for i in info))
        one_class = synth_rows(days=DAYS[:3])
        for r in one_class:
            r["press"] = -1
        sel, info, _ = gm.nested_lodo_select(one_class, DAYS[:3])
        self.assertEqual(sel, [])
        self.assertTrue(all(not i["trained"] for i in info))
        # A day with no rows is skipped and recorded, not an error.
        sel, info, _ = gm.nested_lodo_select(synth_rows(days=DAYS[:3]), DAYS[:3] + ("2026-09-01",))
        self.assertEqual(next(i for i in info if i["outer_day"] == "2026-09-01")["skip_reason"], "no test rows")


class PnlAtKTests(unittest.TestCase):
    def test_join_and_missing(self) -> None:
        rows = synth_rows(days=DAYS[:2])
        rows = [r for r in rows if not (r["mint"] == "m0-001" and r["entry_land_k"] == 8)]  # censored at k=8
        sel = [{"day": DAYS[0], "mint": m, "score": 0.9, "threshold": 0.5} for m in ("m0-000", "m0-001", "m0-002")]
        out8 = gm.pnl_at_k(sel, rows, 8)
        self.assertEqual((out8["n_selected"], out8["n_joined"], out8["n_missing_k"]), (3, 2, 1))
        self.assertEqual(out8["missing"], [{"day": DAYS[0], "mint": "m0-001"}])
        out4 = gm.pnl_at_k(sel, rows, 4)
        self.assertEqual(out4["n_missing_k"], 0)
        want = next(r for r in rows if r["mint"] == "m0-002" and r["entry_land_k"] == 4)
        got = next(r for r in out4["records"] if r["mint"] == "m0-002")
        self.assertEqual((got["flat"], got["press"]), (want["flat"], want["press"]))


class GuardTests(unittest.TestCase):
    def _write(self, d: Path, rows: list[dict], md5: str | None = None) -> None:
        d.mkdir(parents=True, exist_ok=True)
        text = "".join(json.dumps(r) + "\n" for r in rows)
        (d / "table.jsonl").write_text(text, encoding="utf-8")
        (d / "table.md5").write_text((md5 or hashlib.md5(text.encode()).hexdigest()) + "\n", encoding="utf-8")
        (d / "manifest.json").write_text(json.dumps({"schema": "x"}), encoding="utf-8")

    def test_load_table_roundtrip_and_md5(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            rows = synth_rows(days=DAYS[:1], per_day=3)
            self._write(Path(td) / "ok", rows)
            got, man = gm.load_table(Path(td) / "ok")
            self.assertEqual(len(got), len(rows))
            self.assertEqual(man["schema"], "x")
            self._write(Path(td) / "bad", rows, md5="0" * 32)
            with self.assertRaises(SystemExit):
                gm.load_table(Path(td) / "bad")
            (Path(td) / "ok" / "table.md5").unlink()
            with self.assertRaises(SystemExit):
                gm.load_table(Path(td) / "ok")

    def test_cutoff(self) -> None:
        before = datetime(2026, 10, 4, 11, 59, 59, tzinfo=UTC)
        after = datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
        with self.assertRaises(SystemExit):
            gm.assert_run_dir_allowed("/data/mal/exp013-grad/run1", now=before)
        gm.assert_run_dir_allowed("/data/mal/exp013-grad/run1", now=after)
        with self.assertRaises(SystemExit):  # allow_fixture never opens a real path early
            gm.assert_run_dir_allowed("/data/mal/exp013-grad/run1", now=before, allow_fixture=True)
        gm.assert_run_dir_allowed("/data/mal/exp013-grad/run1", now=after, allow_fixture=True)
        gm.assert_run_dir_allowed("/tmp/not-real-data", now=before)  # outside /data/mal: fixtures

    def test_cutoff_applies_in_load_table(self) -> None:
        with self.assertRaises(SystemExit) as cm:
            gm.load_table("/data/mal/exp013-grad/run1", now=datetime(2026, 10, 2, tzinfo=UTC))
        self.assertIn("real data", str(cm.exception))

    def test_cli_refuses_real_path_with_allow_fixture(self) -> None:
        with mock.patch.object(gm, "_now", return_value=datetime(2026, 10, 3, tzinfo=UTC)), mock.patch.object(gm, "nested_lodo_select") as nl:
            with self.assertRaises(SystemExit):
                gm.main(["--run-dir", "/data/mal/exp013-grad/run1", "--allow-fixture"])
        nl.assert_not_called()

    def test_cli_fixture_outside_data_mal(self) -> None:
        import contextlib
        import io

        with tempfile.TemporaryDirectory() as td:
            rows = synth_rows(days=DAYS[:3], per_day=30)
            self._write(Path(td) / "r", rows)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(gm.main(["--run-dir", str(Path(td) / "r"), "--allow-fixture"]), 0)
        out = json.loads(buf.getvalue())
        for key in ("n_rows_total", "n_k4_rows", "n_dropped_out_of_days", "n_mints_without_k4"):
            self.assertIn(key, out)

    def test_manifest_and_row_checks(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            rows = synth_rows(days=DAYS[:1], per_day=3)
            self._write(Path(td) / "a", rows)
            (Path(td) / "a" / "manifest.json").write_text(json.dumps({"n_rows": len(rows) + 1}), encoding="utf-8")
            with self.assertRaises(SystemExit):
                gm.load_table(Path(td) / "a")
            (Path(td) / "a" / "manifest.json").write_text(json.dumps({"table_md5": "0" * 32}), encoding="utf-8")
            with self.assertRaises(SystemExit):
                gm.load_table(Path(td) / "a")
            (Path(td) / "a" / "manifest.json").write_text(json.dumps({"n_rows": len(rows), "table_md5": hashlib.md5("".join(json.dumps(r) + "\n" for r in rows).encode()).hexdigest()}), encoding="utf-8")
            self.assertEqual(len(gm.load_table(Path(td) / "a")[0]), len(rows))
            bad = [dict(r) for r in rows]
            del bad[1]["press"]
            self._write(Path(td) / "b", bad)
            with self.assertRaises(SystemExit) as cm:
                gm.load_table(Path(td) / "b")
            self.assertIn("press", str(cm.exception))

    def test_forbidden_always(self) -> None:
        after = datetime(2027, 1, 1, tzinfo=UTC)
        for p in ("/data/mal/blocks/fresh-0903", "/data/mal/exp012/x", "/var/lib/mal/backfill-fast-b"):
            with self.assertRaises(SystemExit):
                gm.assert_run_dir_allowed(p, now=after, allow_fixture=True)
        repo = Path(gm.__file__).resolve().parents[1]
        with self.assertRaises(SystemExit):
            gm.assert_run_dir_allowed(repo / "ARTIFACTS" / "exp012" / "read", now=after, allow_fixture=True)


class CountTests(unittest.TestCase):
    def test_counts_and_reports(self) -> None:
        rows = synth_rows(days=DAYS[:3])
        rows = [r for r in rows if not (r["mint"] == "m0-001" and r["entry_land_k"] == 4)]  # no k=4 row
        _, _, c = gm.nested_lodo_select(rows, DAYS[:2])
        self.assertEqual(c["n_rows_total"], len(rows))
        self.assertEqual(c["n_k4_rows"], 119)
        self.assertEqual(c["n_dropped_out_of_days"], 40)  # DAYS[2]
        self.assertEqual(c["n_mints_without_k4"], 1)
        _, oinfo = gm.outer_lodo_oof(synth_rows(days=DAYS[:3], per_day=5), DAYS[:3])
        self.assertEqual(oinfo["n_skipped_folds"], 3)
        self.assertEqual(oinfo["skipped_folds"], list(DAYS[:3]))

    def test_empty_oof_raises(self) -> None:
        with self.assertRaises(ValueError):
            gm.pooled_threshold([])

    def test_duplicate_mint_k_refused(self) -> None:
        rows = synth_rows(days=DAYS[:1], per_day=3)
        sel = [{"day": DAYS[0], "mint": "m0-000", "score": 1.0, "threshold": 0.5}]
        with self.assertRaises(ValueError):
            gm.pnl_at_k(sel, rows + [dict(rows[0])], rows[0]["entry_land_k"])


class RealParityTests(unittest.TestCase):
    def test_booster_identical_to_fz_fit_on_18_features(self) -> None:
        rows = gm.training_rows(synth_rows(days=DAYS[:3]))
        names18 = list(fz.FROZEN_FEATURE_NAMES)
        x = [[r["features"][n] for n in names18] for r in rows]
        y = [1 if r["press"] > 0 else 0 for r in rows]
        want = fz._fit(x, y)
        with mock.patch.object(gm, "FEATURE_NAMES", names18):
            got = gm.fit(rows)
            got_scores = gm.predict(got, rows)
        self.assertEqual(got.model_to_string(), want.model_to_string())
        self.assertEqual(got_scores, fz._predict(want, x))


class FinalTests(unittest.TestCase):
    def test_fit_final_errors(self) -> None:
        with self.assertRaises(ValueError):
            gm.fit_final([])
        rows = synth_rows(days=DAYS[:2])
        for r in rows:
            r["press"] = -1
        with self.assertRaises(ValueError):
            gm.fit_final(rows)

    def test_fit_final_uses_k4_only(self) -> None:
        rows = synth_rows(days=DAYS[:3])
        model = gm.fit_final(rows)
        self.assertEqual(model.num_feature(), 22)
        self.assertEqual(model.feature_name(), gm.FEATURE_NAMES)


if __name__ == "__main__":
    unittest.main()
