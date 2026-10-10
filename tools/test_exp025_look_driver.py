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
                        mock.patch.object(D.A, "look_trade_files", side_effect=AssertionError("V0 files listed")),
                        mock.patch.object(D.R, "LOOK_LEDGER", str(self.tmp / "LOOK_READS.jsonl"))]   # never the host's ledger
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

    def test_lock_in_the_look_ledger_refuses_without_read_lock(self):
        """The runner's spent look (exp025_look._run_look): READ.lock OR a `lock` event in LOOK_READS. A deleted READ.lock
        must not let the driver rewrite O/tape after the read."""
        ledger = self.tmp / "LOOK_READS_locked.jsonl"
        ledger.write_text(json.dumps({"look": 1, "event": "lock"}) + "\n")
        conv = mock.Mock(side_effect=AssertionError("convert called"))
        with mock.patch.object(D.R, "LOOK_LEDGER", str(ledger)), mock.patch.object(D.A, "convert", conv):
            self.assertEqual(self.drive(), "LOCK")
        self.assertFalse((self.O / "READ.lock").exists())
        conv.assert_not_called()

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
        self.pre_merge = {}

    @staticmethod
    def rows(p, drop=()):
        """(columns, rows) of a parquet file in file_row_number order, without `drop`."""
        import duckdb
        con = duckdb.connect()
        cur = con.execute(f"SELECT * EXCLUDE (file_row_number{''.join(', ' + c for c in drop)}) FROM "
                          f"read_parquet('{p}', file_row_number=true) ORDER BY file_row_number")
        out = ([d[0] for d in cur.description], cur.fetchall())
        con.close()
        return out

    def spy_merge(self):
        real = D.merge_v_ok

        def spy(tape, hour):   # convert's trades rows, before the merge
            self.pre_merge[hour] = self.rows(Path(tape) / "trades" / f"{hour}.parquet")
            return real(tape, hour)
        return mock.patch.object(D, "merge_v_ok", side_effect=spy)

    def make_history(self):
        """A fixture P2 working directory (self.tmp/p2: out/mout and wl, 36 days each) and its two manifests; returns the
        P2_HISTORY to patch in."""
        hist = {}
        self.p2 = self.tmp / "p2"
        for kind, d in (("mout", self.p2 / "out" / "mout"), ("wl", self.p2 / "wl")):
            d.mkdir(parents=True, exist_ok=True)
            lines = []
            for i in range(D.P2_DAYS):
                n = f"{D.R.date_str(D.R.ep('2026-08-14T00') + 86400 * i)}.parquet"
                (d / n).write_text(f"{kind}{i}\n")
                lines.append(f"{D.A.sha256_file(d / n)}  {n}")
            m = self.tmp / f"{kind}_sha256.txt"
            m.write_text("\n".join(lines) + "\n")
            hist[kind] = (str(m), D.A.sha256_file(m))
        return hist

    def run_look_assemble(self, hist, days=None):
        """Look-mode assemble on the fixture hour: convert runs the real adapter convert in exploration mode, look_trade_files
        gives the fixture trades file, build_shared is the stub, the October wl days are `days` (default: the fixture hour's
        day, which the fixture P2 days 08-14..09-18 do not hold) and the ledger is the real pinned script."""
        real = D.A.convert
        trades = D.A.src_file(str(self.src), "trades", self.HOUR)

        def conv(src, blk, out, hours, *, look, event_v, final_ledger, now):
            return real(src, blk, out, hours, event_v=event_v)
        plan = [("fixture-blk", str(self.src), [self.HOUR], True)]
        days = [self.HOUR[:10]] if days is None else list(days)
        with mock.patch.object(D.A, "convert", side_effect=conv), mock.patch.dict(D.P2_HISTORY, hist), \
                mock.patch.object(D.A, "look_trade_files", return_value=([trades], [])), \
                mock.patch.object(D.A, "build_shared", self.fake_build()), \
                mock.patch.object(D, "look_october_wl_days", return_value=days):
            return D.assemble(1, str(self.O), str(self.O / "tape"), plan, lookname="look1", final_ledger=None, now=None,
                              exploration_sh=str(self.sh), exploration_sha256=self.sh_sha, p2_src=str(self.p2))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fake_build(self, extra=()):
        import pyarrow as pa
        import pyarrow.parquet as pq

        def build(tape, out, hours, *, vmap=None, **kw):
            self.builds.append(dict(vmap))
            out = Path(out)
            self.assertFalse((out / "tokens.parquet").exists(), "build_shared ran over an earlier tokens.parquet")
            (out / "bars_1m" / "fixture-blk").mkdir(parents=True, exist_ok=True)
            mints = [TA.MINT] + (list(extra) if len(self.builds) == 1 else list(extra))
            pq.write_table(pa.table({"mint": mints, "grad_src": ["complete"] * len(mints), "is_mayhem_mode": [False] * len(mints),
                                     "complete_ms": [1789880399000] * len(mints)}, schema=self.schema), out / "tokens.parquet")
            return out / "tokens.parquet"
        return build

    def run_assemble(self, extra=()):
        plan = [("fixture-blk", str(self.src), [self.HOUR], True)]
        with mock.patch.object(D.A, "build_shared", self.fake_build(extra)), self.spy_merge():
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
        # E1: the trades file carries v_ok, so the runner's own V checks see it
        class _G:
            def check_hour(self, s, h):
                pass
        trades = str(self.O / "tape" / "trades")
        with mock.patch.object(D.L, "v_hours", lambda look: [self.HOUR]):
            self.assertEqual(D.L.v_flag_missing(1, trades, _G()), [])
            cov = D.L.r3_coverage(1, trades, [TA.POOL], _G())
        self.assertEqual((cov["n"], cov["n_v"]), (2, 2))
        f = self.O / "tape" / "trades" / f"{self.HOUR}.parquet"
        cols, got = self.rows(f, drop=("v_ok",))
        self.assertEqual((cols, got), self.pre_merge[self.HOUR])          # convert's rows, same order, v_ok aside
        self.assertEqual(self.rows(f)[0], cols + ["v_ok"])
        tf = [x for x in mans[0]["files"] if x["kind"] == "trades"]
        self.assertEqual([x["sha256_with_v_ok"] for x in tf], [D.A.sha256_file(f)])
        self.assertNotEqual(tf[0]["sha256"], tf[0]["sha256_with_v_ok"])
        # the runner's hour check refuses exploration hours (R12): an E0 assembly is never a look's
        with self.assertRaises(D.R.Refusal) as cm:
            D.L.hour_status(1, mans, {})
        self.assertEqual(cm.exception.code, "R12")

    def test_v_ok_sidecar_that_does_not_align_refuses(self):
        import duckdb
        tape = self.tmp / "t"
        (tape / "trades").mkdir(parents=True)
        (tape / "v_ok").mkdir()
        t, s = tape / "trades" / f"{self.HOUR}.parquet", tape / "v_ok" / f"{self.HOUR}.parquet"
        con = duckdb.connect()
        con.execute(f"COPY (SELECT range AS slot, 0 AS tx_index, 0 AS event_index, 'x' AS venue FROM range(3)) TO '{t}' (FORMAT parquet)")
        before = D.A.sha256_file(t)
        for name, q in (("one row short", "SELECT range AS slot, 0 AS tx_index, 0 AS event_index, true AS v_ok FROM range(2)"),
                        ("slot shifted", "SELECT range + 1 AS slot, 0 AS tx_index, 0 AS event_index, true AS v_ok FROM range(3)")):
            with self.subTest(name):
                con.execute(f"COPY ({q}) TO '{s}' (FORMAT parquet)")
                with self.assertRaises(D.R.Refusal) as cm:
                    D.merge_v_ok(str(tape), self.HOUR)
                self.assertEqual(cm.exception.code, "NOT_READY")
                self.assertEqual(D.A.sha256_file(t), before)
        con.execute(f"COPY (SELECT range AS slot, 0 AS tx_index, 0 AS event_index, range = 1 AS v_ok FROM range(3)) TO '{s}' (FORMAT parquet)")
        con.close()
        D.merge_v_ok(str(tape), self.HOUR)
        self.assertEqual(self.rows(t)[1], [(0, 0, 0, "x", False), (1, 0, 0, "x", True), (2, 0, 0, "x", False)])
        with self.assertRaises(D.R.Refusal):   # a second merge on a file that already has v_ok
            D.merge_v_ok(str(tape), self.HOUR)

    def test_look_mode_copies_p2_history_and_builds_the_october_wl_days(self):
        hist = self.make_history()
        rec = self.run_look_assemble(hist)
        self.assertTrue((self.O / "look_assembly.json").exists())
        self.assertEqual(rec["mode"], "look")
        h = rec["history"]
        self.assertEqual((h["mout"]["files"], h["mout"]["october_files"]), (36, 0))
        self.assertEqual((h["wl"]["files"], h["wl"]["p2_files"], h["wl"]["october_files"]), (37, 36, 1))
        self.assertEqual({k: (v["copied"], v["kept"]) for k, v in h["copy"].items()}, {"mout": (36, 0), "wl": (36, 0)})
        for kind, d in (("mout", self.O / "out" / "mout"), ("wl", self.O / "wl")):   # byte copies of the P2 files
            for n in os.listdir(self.p2 / ("out/mout" if kind == "mout" else "wl")):
                self.assertFalse((d / n).is_symlink())
                self.assertEqual(D.A.sha256_file(d / n), D.A.sha256_file(self.p2 / ("out/mout" if kind == "mout" else "wl") / n))
        # provenance: the pinned ledger script, the merged trades file it read (as the manifest records it), the day it wrote
        prov = h["wl_provenance"]
        self.assertEqual(prov["ledger_script_sha256"], D.A.pinned_sha(D.LEDGER_REL))
        day = self.HOUR[:10]
        f = self.O / "tape" / "trades" / f"{self.HOUR}.parquet"
        man = json.loads((self.O / "assembly" / "manifest-fixture-blk.json").read_text())
        tf = [x for x in man["files"] if x["kind"] == "trades"][0]
        self.assertEqual(prov["days"][day]["inputs"], {f"trades/{self.HOUR}.parquet": tf["sha256_with_v_ok"]})
        self.assertEqual(tf["sha256_with_v_ok"], D.A.sha256_file(f))
        wl = self.O / "wl" / f"{day}.parquet"
        self.assertEqual(prov["days"][day]["sha256"], D.A.sha256_file(wl))
        self.assertEqual(h["wl"]["october_sha256"], {f"{day}.parquet": D.A.sha256_file(wl)})
        cols, got = self.rows(wl)
        self.assertEqual(cols, ["th", "n", "nm", "nbond", "buy", "sell", "nwin", "nrt", "cash"])
        self.assertEqual(prov["days"][day]["wallet_rows"], len(got))
        self.assertGreater(len(got), 0)
        self.assertFalse((self.O / "assembly" / "tmp_wl").exists())
        A_, _ = D.L.load_assembly(1, str(self.O))
        self.assertEqual(A_["history"], h)
        self.assertEqual(D.look_october_wl_days(1)[0], "2026-10-02")
        self.assertEqual(D.look_october_wl_days(1)[-1], "2026-10-16")

    def test_reassembly_rebuilds_october_wl_days_and_repairs_p2_copies(self):
        hist = self.make_history()
        first = self.run_look_assemble(hist)["history"]
        day = f"{self.HOUR[:10]}.parquet"
        (self.O / "wl" / day).write_text("stale October day\n")             # the pinned ledger would skip it
        (self.O / "wl" / "2026-10-30.parquet").write_text("not a day of this look\n")
        (self.O / "wl" / "2026-08-20.parquet.tmp").write_text("left over\n")
        (self.O / "out" / "mout" / "2026-08-14.parquet").write_text("changed\n")
        second = self.run_look_assemble(hist)["history"]
        self.assertEqual((second["copy"]["mout"]["copied"], second["copy"]["mout"]["kept"]), (1, 35))
        self.assertEqual((second["copy"]["wl"]["copied"], second["copy"]["wl"]["kept"], second["copy"]["wl"]["removed"]), (0, 36, 3))
        self.assertEqual(second["wl"]["october_sha256"], first["wl"]["october_sha256"])   # idempotent
        self.assertEqual(second["wl_provenance"]["days"], first["wl_provenance"]["days"])
        self.assertFalse((self.O / "wl" / "2026-10-30.parquet").exists())

    def test_history_mismatches_refuse_before_any_record(self):
        cases = (("a P2 source file differs from its manifest", lambda h: (self.p2 / "wl" / "2026-08-20.parquet").write_text("x"), None),
                 ("a manifest that is not the pinned one", lambda h: h.update(wl=(h["wl"][0], "0" * 64)), None),
                 ("a manifest with 35 files", lambda h: (Path(h["mout"][0]).write_text("".join(Path(h["mout"][0]).read_text().splitlines(True)[:35])),
                                                         h.update(mout=(h["mout"][0], D.A.sha256_file(h["mout"][0])))), None),
                 ("an extra mout file", lambda h: ((self.O / "out" / "mout").mkdir(parents=True), (self.O / "out" / "mout" / "2026-10-10.parquet").write_text("x")), None),
                 ("an October day with no tape hour", lambda h: None, [self.HOUR[:10], "2026-09-21"]))
        for name, break_it, days in cases:
            with self.subTest(name):
                shutil.rmtree(self.O, ignore_errors=True)
                self.O.mkdir()
                (self.O / "look_assembly.json").write_text("{}\n")
                hist = self.make_history()
                break_it(hist)
                with self.assertRaises(D.R.Refusal) as cm:
                    self.run_look_assemble(hist, days)
                self.assertEqual(cm.exception.code, "NOT_READY")
                self.assertFalse((self.O / "look_assembly.json").exists())   # E3: the earlier record is gone too

    def test_build_wl_days_refusals(self):
        import pyarrow as pa
        import pyarrow.parquet as pq
        tape = self.tmp / "t"
        (tape / "trades").mkdir(parents=True)
        f = tape / "trades" / f"{self.HOUR}.parquet"
        pq.write_table(pa.table({"trader": ["W"], "mint": ["M"], "venue": ["pumpswap"], "side": ["buy"], "sol_lamports": [5]}), f)
        good = [{"files": [{"kind": "trades", "hour": self.HOUR, "sha256": D.A.sha256_file(f)}]}]
        out = self.tmp / "wl"
        day = self.HOUR[:10]
        prov = D.build_wl_days(str(self.tmp), str(tape), good, [day], out_dir=str(out))
        self.assertEqual(prov["days"][day]["wallet_rows"], 1)
        self.assertEqual(D.build_wl_days(str(self.tmp), str(tape), good, [], out_dir=str(out))["days"], {})
        fail = mock.Mock(return_value=mock.Mock(returncode=1, stdout="", stderr="boom"))
        for name, kw, patches in (
                ("the ledger script is not the pinned one", {}, [mock.patch.object(D.A, "pinned_sha", return_value="0" * 64)]),
                ("a trades file differs from the manifest", {"mans": [{"files": [{"kind": "trades", "hour": self.HOUR, "sha256": "0" * 64}]}]}, []),
                ("a trades hour in no manifest", {"mans": []}, []),
                ("the ledger exits non-zero", {}, [mock.patch.object(D.subprocess, "run", fail)]),
                ("the ledger writes no day", {}, [mock.patch.object(D.subprocess, "run", mock.Mock(return_value=mock.Mock(returncode=0, stdout="done\n", stderr="")))])):
            with self.subTest(name):
                for p in patches:
                    p.start()
                try:
                    with self.assertRaises(D.R.Refusal) as cm:
                        D.build_wl_days(str(self.tmp), str(tape), kw.get("mans", good), [day], out_dir=str(out))
                finally:
                    for p in patches:
                        p.stop()
                self.assertEqual(cm.exception.code, "NOT_READY")

    def test_tape_file_in_no_manifest_refuses_before_any_record(self):
        """A file left by an earlier assembly for an allowlisted hour (e.g. a walk-2 hour re-walked between two assemblies,
        whose source file is now missing) is in no manifest of this assembly: NOT_READY, no look_assembly.json."""
        import pyarrow as pa
        import pyarrow.parquet as pq
        stray_hour = D.look_plan(1)[-1][2][0]
        d = self.O / "tape" / "trades"
        d.mkdir(parents=True)
        pq.write_table(pa.table({"slot": [1]}), d / f"{stray_hour}.parquet")
        with self.assertRaises(D.R.Refusal) as cm:
            self.run_assemble()
        self.assertEqual(cm.exception.code, "NOT_READY")
        self.assertIn("1 files in no manifest", str(cm.exception))
        self.assertFalse((self.O / "look_assembly.json").exists())

    def test_stale_october_shared_is_not_reused(self):
        import pyarrow as pa
        import pyarrow.parquet as pq
        stale = self.O / "october-shared"
        stale_bars = stale / "bars_1m" / "fixture-blk" / "2026-09-20.parquet"
        stale_bars.parent.mkdir(parents=True)
        junk = pa.table({"junk": [1]})
        pq.write_table(junk, stale / "tokens.parquet")
        pq.write_table(junk, stale_bars)
        self.run_assemble()
        self.assertFalse(stale_bars.exists())

    def test_failed_reassembly_leaves_no_stale_record(self):
        self.O.mkdir(parents=True)
        (self.O / "look_assembly.json").write_text("{}\n")
        plan = [("forward-1002ev", "a", ["2026-10-09T00"], True), ("walk2", "b", ["2026-10-16T01"], True)]
        conv = mock.Mock(side_effect=[{"files": [], "event_v": True}, D.A.Refused("R12: the second block")])
        with mock.patch.object(D.A, "convert", conv), self.assertRaises(D.A.Refused):
            D.assemble(1, str(self.O), str(self.O / "tape"), plan, lookname="look1", final_ledger=None, now=None,
                       exploration_sh=str(self.sh), exploration_sha256=self.sh_sha)
        self.assertEqual(conv.call_count, 2)
        self.assertFalse((self.O / "look_assembly.json").exists())

    V0_PLAN = [("forward-1002", "sA", ["2026-10-08T00"], False), ("forward-1002ev", "sB", ["2026-10-09T00"], True),
               ("walk2", "sC", ["2026-10-16T01"], True)]
    V0_FILES = [Path("/x/forward-1002/trades-2026-10-08T00.jsonl.zst"), Path("/x/forward-1002ev/trades-2026-10-09T00.jsonl.zst"),
                Path("/x/forward-1002ev/trades-2026-10-09T01.jsonl.zst"), Path("/x/walk2/trades-2026-10-16T01.jsonl.zst")]

    def v0_assemble(self, bads, ns, collect):
        """Look-mode assemble with convert / look_trade_files mocked; bads, ns: the event-V manifests' v0_bad and v0_files."""
        mans = [{"block": "forward-1002", "event_v": False, "files": [], "bad_hours": [], "missing": [], "v0_bad": [], "v0_files": 0}]
        for (blk, *_), bad, n in zip(self.V0_PLAN[1:], bads, ns):
            mans.append({"block": blk, "event_v": True, "files": [], "bad_hours": [], "missing": [], "v0_files": n,
                         "v0_bad": [{"file": str(f), "reason": "strict"} for f in bad]})
        ltf = mock.Mock(return_value=(list(self.V0_FILES), []))
        with mock.patch.object(D.A, "convert", side_effect=mans), mock.patch.object(D.A, "look_trade_files", ltf), \
                mock.patch.object(D.A, "collect_v0", collect), mock.patch.object(D, "copy_p2_history", return_value={}), \
                mock.patch.object(D, "build_wl_days", return_value={"days": {}}):   # step 4b has its own tests
            D.assemble(1, str(self.O), str(self.O / "tape"), self.V0_PLAN, lookname="look1", final_ledger="L", now=None,
                       exploration_sh=str(self.sh), exploration_sha256=self.sh_sha)
        return ltf

    def test_v0_files_are_the_looks_trade_files_minus_v0_bad(self):
        class _Stop(Exception):
            pass
        collect = mock.Mock(side_effect=_Stop)
        bad = self.V0_FILES[2]
        with self.assertRaises(_Stop):
            self.v0_assemble([[bad], [bad]], [3, 3], collect)
        want = [f for f in self.V0_FILES if f != bad]
        self.assertEqual(collect.call_args[0][1], want)   # every allowlisted trade hour of the look, not the event-V blocks' own
        self.assertIn(self.V0_FILES[0], collect.call_args[0][1])

    def test_event_v_manifests_with_other_v0_files_refuse(self):
        collect = mock.Mock(side_effect=AssertionError("collect_v0 called"))
        bad = self.V0_FILES[2]
        for name, bads, ns in (("v0_bad differs", [[bad], []], [3, 3]), ("v0_files differs", [[bad], [bad]], [3, 2]),
                               ("v0_files is the block's own hours", [[], []], [1, 1])):
            with self.subTest(name):
                with self.assertRaises(D.R.Refusal) as cm:
                    self.v0_assemble(bads, ns, collect)
                self.assertEqual(cm.exception.code, "NOT_READY")
        collect.assert_not_called()

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
