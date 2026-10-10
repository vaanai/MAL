"""DEC-026 section 11 item 12: the pinned C1-NF canary model (ARTIFACTS/c1nf_model, built by tools/c1nf_model_pin.py).

Checks, without /data/mal: the file's sha256 equals the manifest's and the executor's pin; the manifest is the shadow's (#503) ModelSet format;
the column order is the live engine's; the training days are the 36 exploration days (no October day); the recorded VERIFY reproduction is exact.
With lightgbm installed it also loads the file and scores the smoke rows identically (a host check: run it on the host that runs the shadow)."""
import hashlib
import json
import math
import os
import unittest

from tools import c1nf_executor as ex
from tools import c1nf_features as cf
from tools import c1nf_model_pin as mp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(ROOT, "ARTIFACTS", "c1nf_model")
PIN = "faf8a01f5fb5019a3c26affc7de49f47270e7b6717eea34767bfcdc759399478"


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        h.update(fh.read())
    return h.hexdigest()


class ModelPinTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(DIR, mp.MANIFEST), encoding="utf-8") as fh:
            cls.man = json.load(fh)
        with open(os.path.join(DIR, mp.SMOKE), encoding="utf-8") as fh:
            cls.smoke = json.load(fh)

    def test_file_sha_equals_manifest_and_executor_pin(self):
        (e,) = self.man["models"]
        self.assertEqual(set(e), {"from_day", "file", "sha256"})
        self.assertEqual(e["file"], mp.MODEL_FILE)
        self.assertEqual(_sha(os.path.join(DIR, e["file"])), e["sha256"])
        self.assertEqual(e["sha256"], PIN)
        self.assertEqual(ex.C1NF_MODEL_SHA256, frozenset({PIN}))
        self.assertEqual(self.smoke["model_sha256"], PIN)

    def test_from_day_after_training_data(self):
        (e,) = self.man["models"]
        self.assertEqual(e["from_day"], mp.FROM_DAY)
        days = self.man["training"]["days"]
        self.assertEqual(tuple(days), mp.EXPLORATION_DAYS)
        self.assertEqual(len(days), 36)
        self.assertTrue(all(d < e["from_day"] for d in days))
        self.assertFalse(any(d.startswith("2026-10") for d in days))
        self.assertLess(self.man["training"]["last_row_t"], 1790380800)  # 2026-09-26T00:00Z

    def test_columns_are_live_engine_order(self):
        self.assertEqual(self.man["feature_names"], list(cf.FEATURE_NAMES))
        self.assertEqual(len(self.smoke["rows"]), mp.SMOKE_ROWS)
        self.assertTrue(all(len(r) == cf.N_FEATURES for r in self.smoke["rows"]))

    def test_recipe_and_inputs_pinned(self):
        self.assertEqual(self.man["inputs"]["disc.npz"], mp.DISC_SHA256)
        self.assertEqual(self.man["inputs"]["conf.npz"], mp.CONF_SHA256)
        sums = mp.pinned_sums()
        for k in ("mlcommon.py", "16_confirm.py"):
            self.assertEqual(self.man["inputs"][k], sums["scripts/" + k])
        self.assertEqual(self.man["inputs"]["rule.json"], sums["rule.json"])
        r = self.man["recipe"]
        self.assertEqual((r["objective"], r["rounds"], r["threshold"], r["purge_s"]), ("huber", 400, 0.02, 3600))
        self.assertEqual(r["params"]["seed"], 1)
        self.assertTrue(r["params"]["deterministic"])
        self.assertTrue(self.man["training"]["two_runs_equal_sha256"])

    def test_verify_reproduction_was_exact(self):
        rv = self.man["repro_verify"]
        self.assertEqual(rv["max_abs_diff_file_vs_verify"], 0.0)
        self.assertEqual(rv["max_abs_diff_mem_vs_file"], 0.0)
        self.assertEqual(rv["max_abs_diff_vs_16_confirm_book"], 0.0)
        self.assertTrue(rv["selection_equal"])
        self.assertEqual(rv["selected_ours"], rv["selected_verify"])
        self.assertGreater(rv["confirm_book_rows"], 0)

    def test_loaded_file_scores_smoke_rows(self):
        try:
            import lightgbm as lgb
            import numpy as np
        except ImportError:
            self.skipTest("lightgbm not installed")
        b = lgb.Booster(model_file=os.path.join(DIR, mp.MODEL_FILE))
        self.assertEqual((b.num_feature(), b.num_trees()), (107, 400))
        X = np.array([[math.nan if v is None else v for v in r] for r in self.smoke["rows"]], dtype=np.float32)
        p = b.predict(X)
        self.assertEqual(p.tolist(), self.smoke["pred"])


if __name__ == "__main__":
    unittest.main()
