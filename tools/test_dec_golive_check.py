import unittest
from pathlib import Path

from tools import dec_golive_check as g


class T(unittest.TestCase):
    def test_counts_values_not_field_names(self):
        c = g.open_values("| `FILL:install_sha` | FILL:gate-read (x) FILL:gate-read |\nFILL:review and FILL:owner: y\n`FILL:owner_line` FILL:install_sha")
        self.assertEqual(c, {"gate-read": 2, "review": 1, "owner": 1})

    def test_dec033(self):
        p = Path(__file__).resolve().parents[1] / "DEC" / "DEC-033-h5v1q50-canary.md"
        t = p.read_text()
        c = g.open_values(t)
        self.assertGreater(c["gate-read"], 10); self.assertGreater(c["review"], 0)
        # c4c7a9a1 (#1054, 2026-10-27) turned the one open `FILL:owner` value (section 9 `FILL:owner_line` row) into the X-time token `POSTX:owner_answer_ids`
        # (three-stage fill order), so the owner count is 0 by design and the placeholder is the POSTX token. The pre-#1054 assertion `owner > 0` went stale.
        self.assertEqual(c["owner"], 0); self.assertIn("POSTX:owner_answer_ids", t)
        self.assertNotIn("<FILL", t)
        self.assertNotIn("h5v1q50-boostfloor-a1", g.ax.CANARY_OWNER_RE.findall(t))  # the owner line is not drafted


if __name__ == "__main__":
    unittest.main()
