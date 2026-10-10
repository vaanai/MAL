"""Tests for tools/exp025_adapter.py (EXP-025 P3, the October adapter). Fixtures only: nothing here opens /data/mal, a
forward block, walk 2, an October label or an outcome. DuckDB tests run under /data/mal/audit-1008/venv/bin/python
(duckdb 1.5.6, the interpreter of the pinned convert.py / build_shared.py) and are skipped where duckdb is missing."""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from ARTIFACTS.exp025 import event_v_map as EV  # noqa: E402
from tools import exp025_adapter as A  # noqa: E402

try:
    import duckdb  # noqa: F401
    import pyarrow  # noqa: F401
    HAVE_DUCKDB = True
except ImportError:
    HAVE_DUCKDB = False

HAVE_ZSTD = shutil.which("zstd") is not None
MINT = "EeLNsgFGbT2PvGoYmZUE81GAGa2SgJ2xBH7k56Z1pump"   # 2026-09-20 tape: this mint's PumpSwap prints are on this pool
POOL = "GMYEehZa5SMgevqpysm78TvNR4rMJbRVXE2cHcK4QkN7"
FINAL_ROW = {"schema": "exp012_forward_final_marker_v1", "experiment": "EXP-012", "final": True, "test_window": False,
             "clean_clock": "2026-10-06T00:00:00Z", "read_end": "2026-10-16T00:00:00Z"}


def write_zst(path: Path, lines: list[bytes]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = path.with_suffix("")
    raw.write_bytes(b"".join(x + b"\n" for x in lines))
    subprocess.run(["zstd", "-q", "-f", "--rm", str(raw), "-o", str(path)], check=True)
    return path


def trade(**kw) -> dict:
    row = {"v": 2, "venue": "pumpswap", "mint": MINT, "trader": "T1", "side": "buy", "sol_lamports": 1000, "token_raw": 5000,
           "quote_reserve": 100_000_000_000, "base_reserve": 150_000_000_000_000, "price_sol": 1e-7, "pool": POOL, "slot": 100,
           "signature": "sig", "event_index": 0, "event_ts": 1789880400, "block_time": 1789880400, "tx_index": 1,
           "lp_fee": 1, "protocol_fee": 2, "creator_fee": 3}
    row.update(kw)
    return row


def jl(rows) -> list[bytes]:
    return [json.dumps(r).encode() for r in rows]


class Pins(unittest.TestCase):
    def test_columns_are_the_pinned_convert_py_literals(self):
        cols = A.pinned_cols()
        tree = ast.parse(A.CONVERT_PY.read_text())
        lit = {n.targets[0].id: ast.literal_eval(n.value) for n in tree.body
               if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id.endswith("_COLS")}
        self.assertEqual(cols, {"trades": lit["TR_COLS"], "creates": lit["CR_COLS"], "migrations": lit["MG_COLS"]})
        for c in ("pool", "slot", "tx_index", "event_index", "block_time", "quote_reserve", "base_reserve", "lp_fee",
                  "protocol_fee", "creator_fee"):
            self.assertIn(c + ":", cols["trades"])

    def test_pinned_files_are_checked_by_sha256(self):
        A.check_pinned(A.CONVERT_PY, "ref/convert.py")
        A.check_pinned(A.BUILD_SHARED_PY, "ref/build_shared.py")
        with self.assertRaises(A.Refused):
            A.check_pinned(A.CONVERT_PY, "ref/build_shared.py")

    def test_section0_lines_and_allowlist_agree_with_the_exp_file(self):
        text = A.EXP_FILE.read_text()
        for key, val in (("START", A.COUNT_START), ("LOOK1_END", A.LOOK1_END), ("END", A.COUNT_END)):
            self.assertEqual(len(re.findall(rf"^EXP025_COUNT_{key}: {val}$" if key != "LOOK1_END" else rf"^EXP025_LOOK1_END: {val}$",
                                            text, re.M)), 1, key)
        self.assertIn("forward-1002 `[2026-10-02T15, 2026-10-09T00)`, forward-1002ev `[2026-10-09T00, 2026-10-16T01)`, "
                      "walk 2 `[2026-10-16T01, 2026-10-17T02)`", text)
        self.assertIn("walk 2 `[2026-10-17T02, 2026-10-24T02)`", text)
        l1, l2 = A.look_allowlist("look1"), A.look_allowlist("look2")
        self.assertEqual(min(l1), "2026-10-02T15")
        self.assertEqual(max(l1), "2026-10-17T01")
        self.assertEqual(max(l2), "2026-10-24T01")
        self.assertTrue(set(l1) < set(l2))
        self.assertEqual(len(l1), 24 * 14 + 11)

    def test_pda_canonical_pool_matches_a_real_tape_pool(self):
        self.assertEqual(A.canonical_pool(MINT), POOL)


class Seal(unittest.TestCase):
    """R12 and the seal: every refusal fires before a file is opened."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ledger = self.tmp / "FINAL_READS.jsonl"
        self.ledger.write_text(json.dumps(FINAL_ROW) + "\n")
        self.after = datetime(2026, 10, 25, tzinfo=timezone.utc)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_exploration_mode_refuses_october_exp009_and_sealed_paths(self):
        for h in ("2026-10-02T15", "2026-10-12T00", "2026-09-25T07", "2026-09-16T00", "2026-08-13T23"):
            with self.assertRaises(A.Refused, msg=h):
                A.check_exploration_hour(h, "/data/mal/clean-view/x")
        for p in ("/data/mal/blocks/forward-1002", "/data/mal/blocks/forward-1016", "/x/fresh-0828/w1", "/x/OUT/rows.jsonl"):
            with self.assertRaises(A.Refused, msg=p):
                A.check_exploration_hour("2026-09-20T05", p)
        A.check_exploration_hour("2026-09-20T05", A.E0_SRC)

    def test_october_hour_without_look_is_refused(self):
        with self.assertRaises(A.Refused):
            A.convert(A.SOURCES["walk2"], "walk2", self.tmp / "o", ["2026-10-16T05"])

    def test_r12_outside_allowlist_wrong_block_wrong_dir(self):
        kw = dict(final_ledger=self.ledger, now=self.after)
        with self.assertRaisesRegex(A.Refused, "R12"):  # past Look 1's end: EXP-022's counted walk-2 hours
            A.check_october_hour("2026-10-17T02", "look1", "walk2", A.SOURCES["walk2"], **kw)
        with self.assertRaisesRegex(A.Refused, "R12"):
            A.check_october_hour("2026-10-24T02", "look2", "walk2", A.SOURCES["walk2"], **kw)
        with self.assertRaisesRegex(A.Refused, "R12"):
            A.check_october_hour("2026-10-12T00", "look1", "forward-1002", A.SOURCES["forward-1002"], **kw)
        with self.assertRaisesRegex(A.Refused, "R12"):
            A.check_october_hour("2026-10-12T00", "look1", "forward-1002ev", "/data/mal/somewhere-else", **kw)
        with self.assertRaisesRegex(A.Refused, "R12"):  # a prefix of another block's directory is not the block's directory
            A.check_october_hour("2026-10-05T00", "look1", "forward-1002", A.SOURCES["forward-1002ev"], **kw)
        with self.assertRaisesRegex(A.Refused, "R12"):
            A.check_october_hour("2026-10-05T00", "look1", "forward-1002", A.SOURCES["forward-1002"] + "/../forward-1016", **kw)
        with self.assertRaisesRegex(A.Refused, "R12"):
            A.check_october_hour("2026-10-12T00", "look3", "forward-1002ev", A.SOURCES["forward-1002ev"], **kw)
        with self.assertRaisesRegex(A.Refused, "R12"):
            A.convert(A.E0_SRC, A.E0_BLOCK, self.tmp / "o", ["2026-09-20T05"], look="look1")

    def test_seal_needs_the_final_marker_and_a_closed_hour(self):
        h, blk = "2026-10-12T00", "forward-1002ev"
        with self.assertRaisesRegex(A.Refused, "seal"):
            A.check_october_hour(h, "look1", blk, A.SOURCES[blk], final_ledger=self.tmp / "missing.jsonl", now=self.after)
        bad = self.tmp / "test_window.jsonl"
        bad.write_text(json.dumps({**FINAL_ROW, "test_window": True}) + "\n")
        with self.assertRaisesRegex(A.Refused, "seal"):
            A.check_october_hour(h, "look1", blk, A.SOURCES[blk], final_ledger=bad, now=self.after)
        with self.assertRaisesRegex(A.Refused, "not closed"):
            A.check_october_hour("2026-10-16T05", "look1", "walk2", A.SOURCES["walk2"], final_ledger=self.ledger,
                                 now=datetime(2026, 10, 16, 5, 59, tzinfo=timezone.utc))
        A.check_october_hour(h, "look1", blk, A.SOURCES[blk], final_ledger=self.ledger, now=self.after)

    def test_r1_r2_r3_refusals(self):
        A.check_r1(1000, 20)
        with self.assertRaisesRegex(A.Refused, "R1"):
            A.check_r1(1000, 21)
        A.check_r2({"non_mayhem_completes": 100, "pda_pool_in_tape": 95, "share": 0.95})
        with self.assertRaisesRegex(A.Refused, "R2"):
            A.check_r2({"non_mayhem_completes": 100, "pda_pool_in_tape": 94, "share": 0.94})
        with self.assertRaisesRegex(A.Refused, "R2"):
            A.check_r2({"non_mayhem_completes": 0, "pda_pool_in_tape": 0, "share": 0.0})
        A.check_r3(100, 95)
        with self.assertRaisesRegex(A.Refused, "R3"):
            A.check_r3(100, 94)

    def test_october_vmap_holds_only_pda_canonical_pools_with_v0(self):
        v0 = [(POOL, 17_580_000_000), ("OtherPool111", 17_600_000_000)]
        vmap, r2 = A.october_vmap(v0, [(MINT, False)])
        self.assertEqual(vmap, {POOL: 17_580_000_000})
        self.assertEqual(r2["share"], 1.0)
        vmap, r2 = A.october_vmap([(POOL, None)], [(MINT, False)])
        self.assertEqual(vmap, {})  # s0 without V (a forward-1002 hour): no V0, so the universe V band drops it
        self.assertEqual(r2["pda_pool_in_tape"], 1)


@unittest.skipUnless(HAVE_ZSTD, "zstd CLI missing")
class StrictLines(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_good_file_counts_lines(self):
        f = write_zst(self.tmp / "a.jsonl.zst", jl([trade(), trade(slot=101)]))
        self.assertEqual(A.strict_scan(f), 2)

    def test_nul_array_and_blank_lines_are_bad(self):
        for name, lines in (("nul", [b'{"a":1}', b'{"a":\x002}']), ("arr", [b'{"a":1}', b"[1,2]"]),
                            ("blank", [b'{"a":1}', b"", b'{"a":2}']), ("scalar", [b"7"])):
            f = write_zst(self.tmp / f"{name}.jsonl.zst", lines)
            with self.assertRaises(A.BadHour, msg=name):
                A.strict_scan(f)


@unittest.skipUnless(HAVE_DUCKDB and HAVE_ZSTD, "duckdb or zstd missing")
class Convert(unittest.TestCase):
    HOUR = "2026-09-20T05"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.src = self.tmp / "clean-view" / "fixture"
        rows = [trade(slot=100, tx_index=1), trade(slot=100, tx_index=2, side="sell"),
                trade(venue="pump_bonding", pool=None, slot=99, quote_reserve=31_000_000_000)]
        write_zst(self.src / "trades" / f"trades-{self.HOUR}.jsonl.zst", jl(rows))
        write_zst(self.src / "creates" / f"creates-{self.HOUR}.jsonl.zst",
                  jl([{"type": "create", "mint": MINT, "creator": "C", "trader": "C", "name": "N", "symbol": "S",
                       "is_mayhem_mode": False, "quote_reserve": 30_000_000_000, "base_reserve": 1, "real_token_reserves": 1,
                       "token_raw": 1, "slot": 90, "tx_index": 0, "event_index": 0, "block_time": 1789880000, "signature": "s"}]))
        write_zst(self.src / "migrations" / f"migrations-{self.HOUR}.jsonl.zst",
                  jl([{"type": "complete", "mint": MINT, "trader": "X", "bonding_curve": "B", "slot": 99, "tx_index": 0,
                       "event_index": 0, "block_time": 1789880399, "signature": "s2"}]))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_rows_equal_convert_py_select_and_output_is_idempotent(self):
        import duckdb
        man1 = A.convert(str(self.src), "fixture-blk", self.tmp / "t1", [self.HOUR], threads=2)
        man2 = A.convert(str(self.src), "fixture-blk", self.tmp / "t2", [self.HOUR], threads=1)
        self.assertEqual(man1["bad"], [])
        self.assertEqual([f["sha256"] for f in man1["files"]], [f["sha256"] for f in man2["files"]])
        cols = A.pinned_cols()
        con = duckdb.connect()
        for kind in A.KINDS:
            f = A.src_file(str(self.src), kind, self.HOUR)
            ref = self.tmp / f"ref-{kind}.parquet"  # convert.py's own statement
            con.execute(f"COPY (SELECT *, 'fixture-blk' AS block, '{self.HOUR}' AS hour FROM read_json('{f}', "
                        f"format='newline_delimited', compression='zstd', columns={cols[kind]})) TO '{ref}' (FORMAT parquet)")
            self.assertEqual(A.rows_md5(con, [ref]), A.rows_md5(con, [self.tmp / "t1" / kind / f"{self.HOUR}.parquet"]), kind)
            a = con.execute(f"DESCRIBE SELECT * FROM '{ref}'").fetchall()
            b = con.execute(f"DESCRIBE SELECT * FROM '{self.tmp / 't1' / kind / (self.HOUR + '.parquet')}'").fetchall()
            self.assertEqual([x[:2] for x in a], [x[:2] for x in b], kind)

    def test_malformed_json_object_makes_the_hour_bad_not_lenient(self):
        write_zst(self.src / "trades" / f"trades-{self.HOUR}.jsonl.zst", jl([trade()]) + [b'{"venue": "pumpswap", "slot": }'])
        man = A.convert(str(self.src), "fixture-blk", self.tmp / "t", [self.HOUR])
        self.assertEqual(man["bad_hours"], [self.HOUR])
        self.assertFalse((self.tmp / "t" / "trades" / f"{self.HOUR}.parquet").exists())

    def test_event_v_mapping_uses_each_prints_own_vault_and_v(self):
        import pyarrow.parquet as pq
        v0 = 17_580_000_000
        prints = [  # (slot, vault_pre, V(t)); the third is an LP deposit (vault jumps, V unchanged)
            (100, 85_000_000_000, v0), (101, 86_000_000_000, v0 + 3_000_000), (102, 120_000_000_000, v0 + 3_000_000),
            (103, 119_000_000_000, v0 + 4_500_000)]
        rows = [trade(slot=s, quote_reserve=q, virtual_quote_reserves=v) for s, q, v in prints]
        rows.append(trade(venue="pump_bonding", pool=None, slot=99, quote_reserve=31_000_000_000))
        rows.append(trade(pool="NoVPool", slot=100, quote_reserve=50))  # s0 without V: not mapped
        write_zst(self.src / "trades" / f"trades-{self.HOUR}.jsonl.zst", jl(rows))
        man = A.convert(str(self.src), "fixture-blk", self.tmp / "t", [self.HOUR], event_v=True)
        t = pq.read_table(self.tmp / "t" / "trades" / f"{self.HOUR}.parquet").to_pylist()
        want = EV.mapped_series([(q, v) for _, q, v in prints], v0)
        self.assertEqual([int(r["quote_reserve"]) for r in t[:4]], want)
        self.assertEqual(want[0], prints[0][1])  # identity at s0
        self.assertEqual(int(t[4]["quote_reserve"]), 31_000_000_000)
        self.assertEqual(int(t[5]["quote_reserve"]), 50)
        self.assertNotIn("virtual_quote_reserves", t[0])
        ft = [f for f in man["files"] if f["kind"] == "trades"][0]
        self.assertEqual((ft["pumpswap_rows"], ft["pumpswap_rows_with_v"], ft["pumpswap_rows_mapped"]), (5, 4, 4))

    def test_v0_is_the_first_print_even_when_s0_is_in_another_block(self):
        other = self.tmp / "other" / "trades" / "trades-2026-09-20T04.jsonl.zst"
        write_zst(other, jl([trade(slot=50, quote_reserve=1)]))  # s0 with no V (a forward-1002-like hour)
        write_zst(self.src / "trades" / f"trades-{self.HOUR}.jsonl.zst",
                  jl([trade(slot=100, quote_reserve=9, virtual_quote_reserves=17_000_000_000),
                      trade(slot=101, quote_reserve=10, virtual_quote_reserves=17_003_000_000)]))
        import duckdb
        import pyarrow.parquet as pq
        files = [other, A.src_file(str(self.src), "trades", self.HOUR)]
        con = duckdb.connect()
        A.collect_v0(con, files, A.pinned_cols())
        self.assertEqual(con.execute("SELECT pool, v0, first_slot FROM v0map").fetchall(), [(POOL, None, 50)])
        con.close()
        A.convert(str(self.src), "fixture-blk", self.tmp / "t", [self.HOUR], event_v=True, v0_files=files)
        t = pq.read_table(self.tmp / "t" / "trades" / f"{self.HOUR}.parquet").to_pylist()
        self.assertEqual(int(t[0]["quote_reserve"]), 9)  # V0 unknown -> vault kept, never chained from a later print
        self.assertEqual(int(t[1]["quote_reserve"]), 10)  # not 10 + 17_003_000_000 - 17_000_000_000

    def test_look_v0_spans_every_allowlisted_trade_hour(self):
        """A look's event-V convert takes V0 over every allowlisted trade hour of every block, not only its own hours."""
        from unittest import mock
        import pyarrow.parquet as pq
        a_dir, b_dir = self.tmp / "blocks" / "blk-a", self.tmp / "blocks" / "blk-b"
        write_zst(a_dir / "trades" / "trades-2026-10-03T00.jsonl.zst", jl([trade(slot=50, quote_reserve=1)]))  # s0, no V
        write_zst(b_dir / "trades" / "trades-2026-10-03T01.jsonl.zst",
                  jl([trade(slot=100, quote_reserve=9, virtual_quote_reserves=17_000_000_000),
                      trade(slot=101, quote_reserve=10, virtual_quote_reserves=17_003_000_000)]))
        ledger = self.tmp / "FINAL_READS.jsonl"
        ledger.write_text(json.dumps(FINAL_ROW) + "\n")
        looks = {"look1": (("blk-a", "2026-10-03T00", "2026-10-03T01"), ("blk-b", "2026-10-03T01", "2026-10-03T03"))}
        hb = ["2026-10-03T01"]
        with mock.patch.dict(A.SOURCES, {"blk-a": str(a_dir), "blk-b": str(b_dir)}), mock.patch.dict(A.LOOKS, looks, clear=True):
            kw = dict(look="look1", event_v=True, final_ledger=ledger)
            with self.assertRaisesRegex(A.Refused, "not closed"):  # 10-03T02 is allowlisted and still open
                A.convert(str(b_dir), "blk-b", self.tmp / "o0", hb, now=datetime(2026, 10, 3, 2, 30, tzinfo=timezone.utc), **kw)
            after = datetime(2026, 10, 25, tzinfo=timezone.utc)
            with self.assertRaisesRegex(A.Refused, "v0_files"):
                A.convert(str(b_dir), "blk-b", self.tmp / "o1", hb, now=after, v0_files=[], **kw)
            man = A.convert(str(b_dir), "blk-b", self.tmp / "o", hb, now=after, **kw)
        self.assertEqual((man["v0_files"], man["v0_missing_hours"]), (2, ["2026-10-03T02"]))
        t = pq.read_table(self.tmp / "o" / "trades" / "2026-10-03T01.parquet").to_pylist()
        self.assertEqual([int(r["quote_reserve"]) for r in t], [9, 10])  # s0 in blk-a has no V: no V0, nothing mapped


@unittest.skipUnless(HAVE_DUCKDB, "duckdb missing")
class LookAssembly(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _tokens(self, path, mints, cms):
        import pyarrow as pa
        import pyarrow.parquet as pq
        pq.write_table(pa.table({"mint": mints, "complete_ms": pa.array(cms, pa.int64()), "v0_lamports": pa.array([1] * len(mints), pa.int64())}), path)
        return path

    def test_exploration_rows_unchanged_then_october_rows(self):
        import pyarrow.parquet as pq
        ex = self._tokens(self.tmp / "ex.parquet", ["b", "a"], [2, 1])
        oc = self._tokens(self.tmp / "oc.parquet", ["c", "a", "b"], [9, 8, None])  # b: dropped, never graduated
        with self.assertRaisesRegex(A.Refused, "sha256"):
            A.append_tokens(ex, oc, self.tmp / "out.parquet", exploration_sha256="0" * 64)
        res = A.append_tokens(ex, oc, self.tmp / "out.parquet", exploration_sha256=A.sha256_file(ex))
        self.assertEqual((res["exploration_rows"], res["october_rows"], res["october_dup_mints_dropped"],
                          res["october_dup_graduated"]), (2, 1, 2, 1))
        out = pq.read_table(self.tmp / "out.parquet")
        self.assertEqual(out.slice(0, 2).to_pylist(), pq.read_table(ex).to_pylist())
        self.assertEqual(out.column("mint").to_pylist(), ["b", "a", "c"])

    def test_mid_check(self):
        import duckdb
        con = duckdb.connect()
        p2, lk, bad = self.tmp / "p2.parquet", self.tmp / "lk.parquet", self.tmp / "bad.parquet"
        con.execute(f"COPY (SELECT * FROM (VALUES ('a', 1), ('b', 2)) t(mint, mid)) TO '{p2}' (FORMAT parquet)")
        con.execute(f"COPY (SELECT * FROM (VALUES ('a', 1), ('b', 2), ('c', 3)) t(mint, mid)) TO '{lk}' (FORMAT parquet)")
        con.execute(f"COPY (SELECT * FROM (VALUES ('a', 1), ('c', 2), ('b', 3)) t(mint, mid)) TO '{bad}' (FORMAT parquet)")
        self.assertEqual(A.check_mid_stable(lk, p2), {"p2_tokens": 2, "mid_changed_or_missing": 0})
        with self.assertRaisesRegex(A.Refused, "mid"):
            A.check_mid_stable(bad, p2)



try:
    import numpy  # noqa: F401
    import pandas  # noqa: F401
    HAVE_PANDAS = True
except ImportError:
    HAVE_PANDAS = False


@unittest.skipUnless(HAVE_DUCKDB and HAVE_PANDAS, "duckdb, numpy or pandas missing (the pinned 10_meta.py needs them)")
class MidE0(unittest.TestCase):
    """The `mid` item of the E0 (e0_mid) end to end on a fixture: the pinned 10_meta.py, staged and retargeted, run three times."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fixture_sh(self):
        import pyarrow as pa
        import pyarrow.parquet as pq
        sh = self.tmp / "sh"
        (sh / "bars_1m" / "blk").mkdir(parents=True)
        t0 = 1_790_000_000_000  # 2026-09-21, exploration-like
        # c and a complete at the same ms: the (complete_ms, mint) order breaks the tie; d is not a complete; e is out of the V band
        mints = ["b", "c", "a", "d", "e"]
        cms = [t0 + 2_000, t0 + 1_000, t0 + 1_000, None, t0 + 3_000]
        tok = pa.table({
            "mint": mints, "pool": [f"P{m}" for m in mints], "grad_src": ["complete", "complete", "complete", None, "complete"],
            "v0_lamports": pa.array([17_600_000_000, 17_550_000_000, 17_650_000_000, None, 16_000_000_000], pa.int64()),
            "complete_ms": pa.array(cms, pa.int64()), "ps_first_ms": pa.array([c + 500 if c else None for c in cms], pa.int64()),
            "complete_slot": pa.array([1, 2, 3, None, 4], pa.int64()), "create_ms": pa.array([t0 - 60_000] * 5, pa.int64()),
            "creator": ["k1", "k2", "k1", "k3", "k2"], "name": mints, "symbol": mints, "is_mayhem_mode": [False] * 5,
            "has_create": [True, True, False, True, True], "bc_n_trades": pa.array([10] * 5, pa.int64()),
            "bc_n_traders": pa.array([5] * 5, pa.int64()), "bc_buy_sol": [1.5] * 5, "bc_sell_sol": [0.5] * 5,
            "ps_first_price": [1e-7] * 5})
        pq.write_table(tok, sh / "tokens.parquet")
        pq.write_table(pa.table({"minute_ms": pa.array([t0, t0 + 60_000], pa.int64()), "venue": ["pumpswap", "pump_bonding"],
                                 "n_buys": pa.array([3, 4], pa.int64()), "n_sells": pa.array([1, 2], pa.int64()),
                                 "buy_sol": [0.25, 0.5], "sell_sol": [0.125, None]}), sh / "bars_1m" / "blk" / "d.parquet")
        return sh

    def test_stage_meta_retargets_four_path_lines_only(self):
        meta = A.stage_meta(self.tmp / "look", self.tmp / "sh", self.tmp / "t")
        self.assertEqual(meta.read_bytes(), A.META_PY.read_bytes())
        pinned = A.COMMON2_PY.read_text().splitlines()
        staged = (meta.parent / "common2.py").read_text().splitlines()
        self.assertEqual(len(pinned), len(staged))
        diff = [(a, b) for a, b in zip(pinned, staged) if a != b]
        self.assertEqual([b.split(" = ")[0] for _, b in diff], ["O", "TAPE", "SH", "TMP"])
        self.assertIn(f"SH = '{self.tmp / 'sh'}'", staged)

    def test_e0_mid_same_later_earlier(self):
        import pyarrow.parquet as pq
        sh = self._fixture_sh()
        p2 = A.run_meta(self.tmp / "p2", sh, self.tmp / "tmp_p2")
        uni = pq.read_table(p2["universe"]).to_pylist()
        self.assertEqual([(r["mid"], r["mint"]) for r in uni], [(1, "a"), (2, "c"), (3, "b")])
        appended = self.tmp / "appended.parquet"  # the "E0 day": a subset of the exploration rows (all duplicates)
        pq.write_table(pq.read_table(sh / "tokens.parquet").slice(0, 3), appended)
        with self.assertRaisesRegex(A.Refused, "P2 universe"):
            A.e0_mid(self.tmp / "w0", appended, exploration_sh=sh, exploration_sha256=A.sha256_file(sh / "tokens.parquet"),
                     p2_universe=p2["universe"], p2_universe_sha256="0" * 64, p2_work=p2["universe"].parent, threads=1)
        r = A.e0_mid(self.tmp / "w", appended, exploration_sh=sh, exploration_sha256=A.sha256_file(sh / "tokens.parquet"),
                     p2_universe=p2["universe"], p2_universe_sha256=A.sha256_file(p2["universe"]),
                     p2_work=p2["universe"].parent, threads=1)
        s, l, e = (r["cases"][k] for k in ("same_day", "later_shift", "earlier_shift"))
        self.assertEqual((s["append"]["october_rows"], s["append"]["october_dup_mints_dropped"],
                          s["append"]["october_dup_graduated"]), (0, 3, 3))
        self.assertTrue(s["p2_mint_rows_md5_equal"] and s["creates"]["md5_equal"] and s["mkt"]["md5_equal"])
        self.assertEqual(l["shift"]["ms_columns"], ["complete_ms", "ps_first_ms", "create_ms"])
        self.assertEqual(l["new_rows"], {"n": 3, "min_mid": 4, "max_mid": 6})
        self.assertTrue(l["p2_mint_rows_md5_equal"])
        self.assertIsNone(l["refused"])
        self.assertRegex(e["refused"], "3 exploration tokens change `mid`")
        self.assertTrue(r["pass"])
        later = pq.read_table(self.tmp / "w" / "look-later_shift" / "work" / "universe.parquet").to_pylist()
        self.assertEqual([r["mint"] for r in later][3:], ["a#e0shift", "c#e0shift", "b#e0shift"])


if __name__ == "__main__":
    unittest.main()
