"""Golden test: EXP-013's default end-to-end fixture screen output is unchanged by the EXP-014 work.

EXP-014 (tools/exp014_m15_*) imports tools/exp013_grad_screen.py and tools/exp013_grad_model.py and does not
edit them. This pins the screen document of EXP-013's own synthetic fixture (tools/test_exp013_grad_screen.py's
Fixture) by its canonical-JSON sha256, minus the two fields that depend on the temp dir.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest

import tools.exp013_grad_screen as sc
from tools.test_exp013_grad_screen import Fixture

VOLATILE = ("tries_log", "view_manifest_sha256")
GOLDEN_SHA256 = "f75cfa79eb6d1800ee0ab750724467493f8806b88b6e58781b46edc4cbef939f"
CENSORED = [{"mint": "m0-001", "day": "2026-08-10", "entry_land_k": 8, "reason": "tape_end"}]


def canonical_sha(doc: dict) -> str:
    d = {k: v for k, v in doc.items() if k not in VOLATILE}
    return hashlib.sha256(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()


class Exp013GoldenTests(unittest.TestCase):
    def test_default_fixture_screen_output_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fx = Fixture(td, censored=CENSORED)
            doc = fx.run()
        self.assertEqual([i["item"] for i in doc["items"]], list(sc.ITEM_ORDER))
        self.assertEqual(canonical_sha(doc), GOLDEN_SHA256)


if __name__ == "__main__":
    unittest.main()
