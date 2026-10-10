"""Tests for tools/exp025_look_driver.py (fixtures and exploration hours only; nothing October is opened).

    /data/mal/audit-1008/venv/bin/python -m unittest tools.test_exp025_look_driver -v
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

try:
    import duckdb  # noqa: F401
    import numpy  # noqa: F401
    import pyarrow  # noqa: F401
    HAVE_DEPS = True
except ImportError:
    HAVE_DEPS = False

if HAVE_DEPS:
    import exp025_look_driver as D
    from tools import test_exp025_adapter as TA

BLOB = "238942a6b3c5425389eddfde4d11268c300acbec"
AFTER_LOOK1 = 1_800_000_000          # 2027-01-15: after look 1's last allowlisted hour


@unittest.skipUnless(HAVE_DEPS, "needs duckdb, numpy, pyarrow (audit venv)")
class Refusals(unittest.TestCase):
    """Every refusal comes before any adapter call, and the SEAL before O is touched."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.O = self.tmp / "look1"
        self.marker = self.tmp / "FINAL_WRITTEN"
        self.ledger = self.tmp / "FINAL_READS.jsonl"
        self.marker.write_text("x\n")
        self.ledger.write_text(json.dumps(TA.FINAL_ROW) + "\n")
        self.blobs = self.tmp / "blobs.json"
        self.blobs.write_text(json.dumps({"forward-1002ev": BLOB, "walk2": BLOB}))
        self.patches = [mock.patch.dict(D.R.LOOKS[1], {"O": str(self.O)}),
                        mock.patch.dict(D.A.LOOK_TAPE, {"look1": str(self.O / "tape")}),
                        mock.patch.object(D.A, "convert", side_effect=AssertionError("convert called")),
                        mock.patch.object(D.A, "look_trade_files", side_effect=AssertionError("V0 files listed"))]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def drive(self, **kw):
        a = dict(now=AFTER_LOOK1, final_marker=str(self.marker), final_ledger=str(self.ledger))
        a.update(kw)
        with self.assertRaises(D.R.Refusal) as cm:
            D.drive_look(1, str(a.pop("blobs", self.blobs)), **a)
        return cm.exception.code

    def test_no_final_marker_refuses_before_o_exists(self):
        self.assertEqual(self.drive(final_marker=str(self.tmp / "absent")), "SEAL")
        self.assertFalse(self.O.exists())

    def test_empty_final_ledger_refuses(self):
        self.ledger.write_text("")
        self.assertEqual(self.drive(), "SEAL")
        self.assertFalse(self.O.exists())

    def test_before_the_looks_last_hour_refuses(self):
        self.assertEqual(self.drive(now=D.R.ep("2026-10-17T01")), "SEAL")

    def test_bad_decoder_blob_refuses_r13(self):
        for rec in ({"forward-1002ev": BLOB, "walk2": "0" * 40}, {"forward-1002ev": "nothex", "walk2": "nothex"}):
            self.blobs.write_text(json.dumps(rec))
            self.assertEqual(self.drive(), "R13")
        self.assertEqual(self.drive(blobs=self.tmp / "absent.json"), "R13")

    def test_non_allowlisted_hour_in_the_tape_refuses_r12(self):
        for kind, hour in (("trades", "2026-10-17T02"), ("v_ok", "2026-10-01T23"), ("creates", "notanhour")):
            with self.subTest(kind=kind, hour=hour):
                d = self.O / "tape" / kind
                d.mkdir(parents=True, exist_ok=True)
                f = d / f"{hour}.parquet"
                f.write_bytes(b"")
                self.assertEqual(self.drive(), "R12")
                f.unlink()

    def test_adapter_allowlist_differing_from_the_read_tools_refuses_r12(self):
        looks = dict(D.A.LOOKS)
        looks["look1"] = looks["look1"][:2] + (("walk2", "2026-10-16T01", "2026-10-17T03"),)
        with mock.patch.dict(D.A.LOOKS, looks):
            self.assertEqual(self.drive(), "R12")

    def test_locked_look_is_not_reassembled(self):
        self.O.mkdir(parents=True)
        (self.O / "READ.lock").write_text("")
        self.assertEqual(self.drive(), "LOCK")

    def test_cli_exits_2_on_seal(self):
        def seal(*a, **k):
            raise D.R.Refusal("SEAL", "FINAL marker absent")
        with mock.patch.object(D.R, "check_read_time", seal), mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(D.main(["look", "--look", "1", "--decoder-blobs", str(self.blobs)]), 2)

    def test_cli_has_no_path_override(self):
        for extra in (["--o", "/tmp/x"], ["--final-marker", "/tmp/x"], ["--src", "/tmp/x"]):
            with self.assertRaises(SystemExit), mock.patch("sys.stderr", io.StringIO()):
                D.main(["look", "--look", "1", "--decoder-blobs", str(self.blobs)] + extra)

    def test_plan_matches_the_allowlist(self):
        for look in (1, 2):
            plan = D.look_plan(look)
            self.assertEqual([(b, ev) for b, _, _, ev in plan], [("forward-1002", False), ("forward-1002ev", True), ("walk2", True)])
            self.assertEqual(sum(len(h) for _, _, h, _ in plan), len(D.R.allowlisted_hours(look)))
            self.assertEqual([s for _, s, _, _ in plan], [D.A.SOURCES[b] for b, _, _, _ in plan])


@unittest.skipUnless(HAVE_DEPS and TA.HAVE_ZSTD, "needs duckdb, pyarrow, zstd")
class Assembly(unittest.TestCase):
    """assemble() on a fixture exploration hour, real adapter convert / october_vmap / append_tokens, a stub build_shared:
    the record passes exp025_look.load_assembly."""
    HOUR = "2026-09-20T05"

    def setUp(self):
        import pyarrow as pa
        import pyarrow.parquet as pq
        self.tmp = Path(tempfile.mkdtemp())
        self.src = self.tmp / "clean-view" / "fixture"
        rows = [TA.trade(slot=100, tx_index=1, virtual_quote_reserves=17_600_000_000),
                TA.trade(slot=101, tx_index=2, side="sell", virtual_quote_reserves=17_601_000_000),
                TA.trade(venue="pump_bonding", pool=None, slot=99, quote_reserve=31_000_000_000)]
        TA.write_zst(self.src / "trades" / f"trades-{self.HOUR}.jsonl.zst", TA.jl(rows))
        TA.write_zst(self.src / "creates" / f"creates-{self.HOUR}.jsonl.zst",
                     TA.jl([{"type": "create", "mint": TA.MINT, "creator": "C", "trader": "C", "name": "N", "symbol": "S",
                             "is_mayhem_mode": False, "quote_reserve": 30_000_000_000, "base_reserve": 1, "real_token_reserves": 1,
                             "token_raw": 1, "slot": 90, "tx_index": 0, "event_index": 0, "block_time": 1789880000, "signature": "s"}]))
        TA.write_zst(self.src / "migrations" / f"migrations-{self.HOUR}.jsonl.zst",
                     TA.jl([{"type": "complete", "mint": TA.MINT, "trader": "X", "bonding_curve": "B", "slot": 99, "tx_index": 0,
                             "event_index": 0, "block_time": 1789880399, "signature": "s2"}]))
        self.sh = self.tmp / "hunt-shared"
        (self.sh / "bars_1m" / "explore-0814").mkdir(parents=True)
        self.schema = pa.schema([("mint", pa.string()), ("grad_src", pa.string()), ("is_mayhem_mode", pa.bool_()),
                                 ("complete_ms", pa.int64())])
        pq.write_table(pa.table({"mint": ["OLD"], "grad_src": ["complete"], "is_mayhem_mode": [False], "complete_ms": [1]},
                                schema=self.schema), self.sh / "tokens.parquet")
        self.sh_sha = D.A.sha256_file(self.sh / "tokens.parquet")
        self.O = self.tmp / "O"
        self.builds = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fake_build(self, extra=()):
        import pyarrow as pa
        import pyarrow.parquet as pq

        def build(tape, out, hours, *, vmap=None, **kw):
            self.builds.append(dict(vmap))
            out = Path(out)
            (out / "bars_1m" / "fixture-blk").mkdir(parents=True, exist_ok=True)
            mints = [TA.MINT] + (list(extra) if len(self.builds) == 1 else list(extra))
            pq.write_table(pa.table({"mint": mints, "grad_src": ["complete"] * len(mints), "is_mayhem_mode": [False] * len(mints),
                                     "complete_ms": [1789880399000] * len(mints)}, schema=self.schema), out / "tokens.parquet")
            return out / "tokens.parquet"
        return build

    def run_assemble(self, extra=()):
        plan = [("fixture-blk", str(self.src), [self.HOUR], True)]
        with mock.patch.object(D.A, "build_shared", self.fake_build(extra)):
            return D.assemble(1, str(self.O), str(self.O / "tape"), plan, lookname=None, final_ledger=None, now=None,
                              exploration=True, exploration_sh=str(self.sh), exploration_sha256=self.sh_sha)

    def test_record_passes_the_runners_load_assembly(self):
        rec = self.run_assemble()
        A_, mans = D.L.load_assembly(1, str(self.O))
        self.assertEqual(A_["schema"], "exp025_look_assembly_v1")
        self.assertEqual(A_["look"], "look1")
        self.assertEqual(A_["manifests"], [str(self.O / "assembly" / "manifest-fixture-blk.json")])
        self.assertEqual([m["block"] for m in mans], ["fixture-blk"])
        self.assertTrue(mans[0]["event_v"])
        self.assertEqual(set(A_["r2"]), {"non_mayhem_completes", "pda_pool_in_tape", "share"})
        self.assertEqual(A_["r2"]["non_mayhem_completes"], 1)
        self.assertEqual(set(A_["append_tokens"]), {"exploration_rows", "october_rows", "october_dup_mints_dropped",
                                                     "october_dup_graduated", "sha256"})
        self.assertEqual((A_["append_tokens"]["exploration_rows"], A_["append_tokens"]["october_rows"]), (1, 1))
        self.assertFalse(rec["completes"]["rebuilt"])
        self.assertEqual(len(self.builds), 1)
        self.assertTrue((self.O / "tape" / "v_ok" / f"{self.HOUR}.parquet").exists())
        self.assertFalse((self.O / "tape" / "manifest.json").exists())
        self.assertEqual(sorted(os.listdir(self.O / "hunt-shared" / "bars_1m")), ["explore-0814", "fixture-blk"])
        self.assertEqual((rec["pumpswap_rows"], rec["pumpswap_rows_with_v"]), (2, 2))
        # the runner's hour check refuses exploration hours (R12): an E0 assembly is never a look's
        with self.assertRaises(D.R.Refusal) as cm:
            D.L.hour_status(1, mans, {})
        self.assertEqual(cm.exception.code, "R12")

    def test_completes_from_the_built_tokens_decide_r2(self):
        rec = self.run_assemble(extra=("ExtraMint1111111111111111111111111111111pump",))
        self.assertTrue(rec["completes"]["rebuilt"])
        self.assertEqual(len(self.builds), 2)
        self.assertEqual(rec["r2"]["non_mayhem_completes"], 2)

    def test_bars_block_in_both_refuses_at_a_look(self):
        (self.sh / "bars_1m" / "fixture-blk").mkdir()
        with self.assertRaises(D.R.Refusal):
            D.link_bars(str(self.sh / "bars_1m"), str(self.sh / "bars_1m"), str(self.tmp / "b"))
        got = D.link_bars(str(self.sh / "bars_1m"), str(self.sh / "bars_1m"), str(self.tmp / "b2"), allow_dup=True)
        self.assertEqual(got["dup_blocks_dropped"], 2)

    def test_october_hour_in_exploration_mode_is_refused(self):
        plan = [("fixture-blk", str(self.src), ["2026-10-09T00"], True)]
        with self.assertRaises(D.A.Refused):
            D.assemble(1, str(self.O), str(self.O / "tape"), plan, lookname=None, final_ledger=None, now=None, exploration=True,
                       exploration_sh=str(self.sh), exploration_sha256=self.sh_sha)

    def test_e0_never_writes_into_a_looks_o(self):
        with self.assertRaises(D.R.Refusal):
            D.drive_e0(D.R.LOOKS[1]["O"] + "/e0")


if __name__ == "__main__":
    unittest.main()
