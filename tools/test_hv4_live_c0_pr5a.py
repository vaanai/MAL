"""PR 5a offline tests: the exit snapshot builder inside the sell pin (QP removal precondition 2). Synthetic only: no HV4 row, ledger, read record or job log."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import hv4_live_c0 as c0


def edited(*a, **k):  # source differs from every pinned function
    return "edited"


class ExitSnapshotPinTests(unittest.TestCase):
    def setUp(self):
        try:
            from tools import probe_executor  # noqa: F401
        except ModuleNotFoundError as e:
            self.skipTest(str(e))

    def test_pin_has_every_listed_piece(self):
        keys = set(c0.SELL_BUILDER_PIN)
        for n in c0.PE_FUNCS:
            self.assertIn(f"pe.{n}_sha256", keys)
        for cn, mn in c0.PE_METHODS:
            self.assertIn(f"pe.{cn}.{mn}_sha256", keys)
        self.assertIn("pe.constants_sha256", keys)
        for n in c0.BRIDGE_METHODS:
            self.assertIn(f"bridge.{n}_sha256", keys)
        for must in ("pe.fetch_snapshot_sha256", "pe.Executor._snapshot_sha256", "pe.Executor._batched_exit_snapshots_sha256", "pe.Executor._exit_snapshot_sha256",
                     "pe.Snapshot.quote_priced_sha256", "bridge.poll_positions_sha256", "bridge.refresh_warm_sha256"):
            self.assertIn(must, keys)
        self.assertIsNone(c0.sell_pin_problem())

    def test_every_new_key_mismatch_is_named(self):
        for k in [k for k in c0.SELL_BUILDER_PIN if k.startswith(("pe.", "bridge."))]:
            with self.subTest(k=k):
                self.assertEqual(c0.sell_pin_problem({**c0.SELL_BUILDER_PIN, k: "0" * 64}), f"sell_builder_pin_mismatch:{k}")
                self.assertEqual(c0.sell_pin_problem({kk: v for kk, v in c0.SELL_BUILDER_PIN.items() if kk != k}), f"sell_builder_pin_mismatch:{k}")

    def test_editing_a_probe_executor_function_breaks_the_pin(self):
        from tools import probe_executor as pe
        for n in c0.PE_FUNCS:
            with self.subTest(fn=n), mock.patch.object(pe, n, edited):
                self.assertEqual(c0.sell_pin_problem(), f"sell_builder_pin_mismatch:pe.{n}_sha256")
        for cn, mn in c0.PE_METHODS:
            with self.subTest(method=f"{cn}.{mn}"), mock.patch.object(getattr(pe, cn), mn, property(edited) if cn == "Snapshot" else edited):
                self.assertEqual(c0.sell_pin_problem(), f"sell_builder_pin_mismatch:pe.{cn}.{mn}_sha256")
        self.assertIsNone(c0.sell_pin_problem())

    def test_changing_a_constant_breaks_the_pin(self):
        from tools import probe_executor as pe
        for n in c0.PE_CONSTANTS:
            with self.subTest(const=n), mock.patch.object(pe, n, getattr(pe, n) + 1):
                self.assertEqual(c0.sell_pin_problem(), "sell_builder_pin_mismatch:pe.constants_sha256")
        for n in ("BATCH_MAX_AGE_MS", "STATIC_TTL_MS"):
            self.assertIn(n, c0.PE_CONSTANTS)

    def test_editing_a_bridge_method_breaks_the_pin(self):
        real = Path(c0.__file__).with_name("arena_live_bridge.py")
        for n in c0.BRIDGE_METHODS:
            src = real.read_text()
            seg = c0.bridge_method_source(n)
            self.assertIn(seg, src)
            with tempfile.TemporaryDirectory() as d:
                alt = Path(d) / "arena_live_bridge.py"
                alt.write_text(src.replace(seg, seg + "\n            pass  # edit", 1))
                real_fn = c0.bridge_method_source
                with mock.patch.object(c0, "bridge_method_source", lambda name, path=None, f=real_fn, a=str(alt): f(name, a)):
                    self.assertEqual(c0.sell_pin_problem(), f"sell_builder_pin_mismatch:bridge.{n}_sha256", n)
        self.assertIsNone(c0.sell_pin_problem())

    def test_missing_bridge_method_is_unreadable(self):
        with mock.patch.object(c0, "bridge_method_source", side_effect=KeyError("poll_positions")):
            self.assertEqual(c0.sell_pin_problem(), "sell_builder_pin_unreadable:bridge.poll_positions_sha256")


if __name__ == "__main__":
    unittest.main()
