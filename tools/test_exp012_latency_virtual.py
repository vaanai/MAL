"""Wrapper wiring only: args split, env set, workers patched during the run and restored after."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import exp012_latency_virtual as lv


class LatencyVirtualTests(unittest.TestCase):
    def test_split_args_strips_vmap(self) -> None:
        vmap, rest, out = lv.split_args(["--out-dir", "/x", "--vmap", "/m.json", "--ks", "1,4"])
        self.assertEqual((vmap, rest, out), ("/m.json", ["--out-dir", "/x", "--ks", "1,4"], Path("/x")))

    def test_requires_out_dir(self) -> None:
        with self.assertRaises(SystemExit):
            lv.split_args(["--ks", "1"])

    def test_runs_latency_main_under_patch(self) -> None:
        import tools.exploration_entry_model as eem
        from tools import exp012_latency_sensitivity as ls
        from tools import pumpswap_virtual_adapter as ad

        orig = eem.run_worker_a
        seen = {}

        def fake_main(argv):
            seen["argv"] = list(argv)
            seen["patched"] = eem.run_worker_a is ad.worker_a
            seen["mcap"] = os.environ[ad.ENV_MCAP]
            seen["frozen"] = os.environ[ad.ENV_FROZEN]
            return 0

        with tempfile.TemporaryDirectory() as d, mock.patch.object(ls, "main", fake_main):
            self.assertEqual(lv.main(["--out-dir", d, "--vmap", "/m.json"]), 0)
            self.assertTrue((Path(d) / "counts_virtual").is_dir())
        self.assertEqual(seen, {"argv": ["--out-dir", d], "patched": True, "mcap": "v", "frozen": "0"})
        self.assertIs(eem.run_worker_a, orig)


if __name__ == "__main__":
    unittest.main()
