"""Entry-latency parameter (exploration only): defaults are byte-identical to
the frozen entry (k=1, "start"); a larger k enters at a later slot's state.
Fixtures only (tools/exp012_fixtures.py)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import tools.exploration_exits as ex
from tools.exp012_fixtures import hour_start_s, mint_tape, write_zst_jsonl
from tools.exploration_entry_model import run_all_features
from tools.exploration_exits import ENTRY_BOUND, ENTRY_LAND_K

SPEC = "tpsl_tp50_sl30"


def write_root_with_midprint(root: Path, hour_idx: int = 10, mint: str = "mintL1") -> None:
    """Every whitelisted hour present; one mint in one hour. Its tape gets an
    extra pumpswap print at slot0+52 (quote reserve x1.02) between the migrate print
    (slot0+50) and the 'after' print (slot0+60), so k=1 and k=4 see different states."""
    for i, h in enumerate(ex.POOL_HOURS):
        creates: list[dict] = []
        trades: list[dict] = []
        if i == hour_idx:
            c, t = mint_tape(mint, hour_start_s(h) + 60, creator="creator-L", slot0=1000)
            mig = next(r for r in t if r["venue"] == "pumpswap" and r["slot"] == 1050)
            t.append({**mig, "slot": 1052, "t_recv_ms": mig["t_recv_ms"] + 400, "signature": "sig-mid", "quote_reserve": int(mig["quote_reserve"] * 1.02), "trader": "walletMid"})
            creates.extend(c)
            trades.extend(t)
        trades.sort(key=lambda r: (r["t_recv_ms"], r["slot"]))
        write_zst_jsonl(root / "trades" / f"trades-{h}.jsonl.zst", trades)
        write_zst_jsonl(root / "creates" / f"creates-{h}.jsonl.zst", creates)
        write_zst_jsonl(root / "migrations" / f"migrations-{h}.jsonl.zst", [])


class EntryLatencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._td = tempfile.TemporaryDirectory()
        cls.root = Path(cls._td.name) / "fast"
        write_root_with_midprint(cls.root)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._td.cleanup()

    def _rows(self, **kw):
        rows = run_all_features(max_workers=1, buffer_hours=2, backfill=self.root, **kw)
        return [r for r in rows if r["spec"] == SPEC]

    def test_frozen_constants_are_what_the_defaults_mean(self) -> None:
        self.assertEqual((ENTRY_LAND_K, ENTRY_BOUND), (1, "start"))

    def test_default_equals_explicit_k1_start(self) -> None:
        default = self._rows()
        explicit = self._rows(entry_land_k=1, entry_bound="start")
        self.assertTrue(default)
        self.assertEqual(json.dumps(default, sort_keys=True), json.dumps(explicit, sort_keys=True))

    def test_k4_enters_at_a_later_slots_price(self) -> None:
        k1 = self._rows(entry_land_k=1)[0]
        k4 = self._rows(entry_land_k=4)[0]
        self.assertTrue(k1["filled"] and k4["filled"])
        self.assertNotEqual(k1["gross"], k4["gross"])
        self.assertNotEqual(k1["flat"], k4["flat"])

    def test_numpy_integer_is_a_scalar_k(self) -> None:
        import numpy as np

        want = self._rows(entry_land_k=4)
        got = self._rows(entry_land_k=np.int64(4))
        self.assertEqual(json.dumps(got, sort_keys=True), json.dumps(want, sort_keys=True))
        self.assertNotIn("entry_land_k", got[0])

    def test_sequence_scores_each_k_from_one_pass_and_matches_scalar_runs(self) -> None:
        multi = self._rows(entry_land_k=[1, 4])
        self.assertEqual(sorted(r["entry_land_k"] for r in multi), [1, 4])
        for k in (1, 4):
            want = self._rows(entry_land_k=k)[0]
            got = {x: y for x, y in next(r for r in multi if r["entry_land_k"] == k).items() if x != "entry_land_k"}
            self.assertEqual(json.dumps(got, sort_keys=True), json.dumps(want, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
