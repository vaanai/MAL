"""Tests for tools/h5_forward_extract.py (EXP-024 forward extractor, E0-H5).

Fixtures are fabricated rows in temp dirs; no real tape is read. Run from the repo root with duckdb 1.5.6:
  /data/mal/audit-1008/venv/bin/python -m unittest tools.test_h5_forward_extract -v
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import h5_forward_extract as X

try:
    import duckdb
except ImportError:  # pragma: no cover
    duckdb = None

HAVE = duckdb is not None and duckdb.__version__ == X.DUCKDB_VERSION and shutil.which("zstd") is not None
V_IN, V_OUT = 17_580_000_000, 17_800_000_000


def tr(mint, pool, slot, tx, ev, side="buy", venue="pumpswap", trader="w1"):
    return {"venue": venue, "mint": mint, "trader": trader, "side": side, "sol_lamports": 100_000_000,
            "token_raw": 5_000_000, "quote_reserve": 1_000_000_000 + slot, "base_reserve": 9_000_000_000,
            "pool": pool, "slot": slot, "tx_index": tx, "event_index": ev, "block_time": 1_790_000_000 + slot,
            "lp_fee": 1, "protocol_fee": 2, "creator_fee": 3, "signature": f"s{slot}{tx}{ev}"}


def mg(mint, slot, typ="complete"):
    return {"type": typ, "mint": mint, "trader": "t", "bonding_curve": "bc", "slot": slot, "tx_index": 0,
            "event_index": 0, "block_time": 1_790_000_000 + slot, "signature": f"m{slot}"}


def cr(mint, slot):
    return {"mint": mint, "creator": "c", "trader": "c", "name": "n", "symbol": "s", "is_mayhem_mode": False,
            "quote_reserve": 1, "base_reserve": 2, "real_token_reserves": 3, "token_raw": 4, "slot": slot,
            "tx_index": 0, "event_index": 0, "block_time": 1_790_000_000 + slot, "signature": f"c{slot}"}


# hour -> kind -> rows. Day 2026-09-20 = hours T00, T01; 09-19T23 carries the create.
FIX = {
    "2026-09-19T23": {"trades": [tr("A", "PA", 400, 0, 0, venue="pump")], "creates": [cr("A", 500)], "migrations": []},
    "2026-09-20T00": {
        "trades": [tr("A", "PB", 1001, 0, 0), tr("A", "PA", 1005, 3, 0), tr("A", "PA", 1005, 1, 0, side="sell"),
                   tr("A", "PA", 999, 0, 0), tr("A", "PA", 1002, 0, 0, venue="pump"), tr("B", "PC", 2001, 0, 0)],
        "creates": [],
        "migrations": [mg("A", 1000), mg("A", 1003, "create_pool"), mg("B", 2000)],
    },
    "2026-09-20T01": {"trades": [tr("A", "PA", 1010, 0, 0), tr("A", "PA", 8300, 0, 0)], "creates": [], "migrations": []},
}
VMAP = {"v": {"PA": V_IN, "PB": V_OUT, "PZ": None}}


def write_raw(root: Path, fix=FIX) -> dict[tuple[str, str], Path]:
    out = {}
    for hour, kinds in fix.items():
        for kind, rows in kinds.items():
            d = root / kind
            d.mkdir(parents=True, exist_ok=True)
            p = d / f"{kind}-{hour}.jsonl"
            p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
            subprocess.run(["zstd", "-q", "--rm", "-f", str(p)], check=True)
            out[(kind, hour)] = d / f"{kind}-{hour}.jsonl.zst"
    return out


@unittest.skipUnless(HAVE, "needs duckdb 1.5.6 and zstd")
class ExtractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.files = write_raw(self.tmp / "raw")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_both(self, out: Path) -> dict:
        con = X.connect(duckdb, out / "tmp", memory="1GB", threads=2)
        for (kind, hour), p in sorted(self.files.items()):
            X.convert_hour(con, "fast-pool-0918", kind, hour, p, out / "tape")
        vm = self.tmp / "vmap-fixture.json"
        vm.write_text(json.dumps(VMAP))
        stats = X.extract_day(con, out / "tape", out, "2026-09-20", X.load_vmap(vm))
        con.close()
        return stats

    def test_canonical_pool_window_order(self):
        out = self.tmp / "out"
        stats = self.run_both(out)
        self.assertEqual(stats["migs"], 2)
        c = duckdb.connect()
        meta = c.execute(f"SELECT mint, pool, mslot, s0, v, npools, blk, cslot, day, uncensored FROM '{out}/meta/2026-09-20.parquet'").fetchall()
        self.assertEqual(meta, [("A", "PA", 1000, 1005, V_IN, 1, "fast-pool-0918", 500, "2026-09-20", True)])
        paths = c.execute(f"SELECT mint, slot, isbuy FROM '{out}/paths/2026-09-20.parquet'").fetchall()
        # PB (V out of range), slot 999 (before complete), bonding rows, slot 8300 (window end) are out;
        # within slot 1005 the order is tx_index 1 (sell) then 3 (buy).
        self.assertEqual(paths, [("A", 1005, False), ("A", 1005, True), ("A", 1010, True)])
        cols = [r[0] for r in c.execute(f"DESCRIBE SELECT * FROM '{out}/paths/2026-09-20.parquet'").fetchall()]
        self.assertEqual(cols, ["mint", "slot", "isbuy", "sol", "tok", "q", "b", "th", "bt"])

    def test_rerun_is_md5_identical(self):
        self.run_both(self.tmp / "o1")
        self.run_both(self.tmp / "o2")
        for part, order in (("meta", X.META_ORDER), ("paths", None)):
            a = X.canonical_md5(duckdb, self.tmp / "o1" / part / "2026-09-20.parquet", order)
            b = X.canonical_md5(duckdb, self.tmp / "o2" / part / "2026-09-20.parquet", order)
            self.assertEqual(a["rows_md5"], b["rows_md5"])

    def test_ties_follow_raw_line_order_and_order_check(self):
        # same (slot, tx_index, event_index) twice, and null tx_index twice: raw line order decides
        fix = {h: {k: list(v) for k, v in kinds.items()} for h, kinds in FIX.items()}
        n1, n2 = tr("A", "PA", 1008, 0, 0, trader="p"), tr("A", "PA", 1008, 0, 0, side="sell", trader="q")
        n1["tx_index"] = n2["tx_index"] = None
        fix["2026-09-20T00"]["trades"] += [tr("A", "PA", 1007, 2, 0, trader="x"),
                                           tr("A", "PA", 1007, 2, 0, side="sell", trader="y"), n1, n2]
        shutil.rmtree(self.tmp / "raw")
        self.files = write_raw(self.tmp / "raw", fix)
        out = self.tmp / "o"
        self.run_both(out)
        c = duckdb.connect()
        p = out / "paths" / "2026-09-20.parquet"
        got = c.execute(f"SELECT slot, isbuy FROM '{p}' WHERE slot IN (1007, 1008)").fetchall()
        self.assertEqual(got, [(1007, True), (1007, False), (1008, True), (1008, False)])
        meta = out / "meta" / "2026-09-20.parquet"

        def swapped(slot: int) -> Path:
            q = self.tmp / f"sw{slot}.parquet"
            c.execute(f"COPY (SELECT * EXCLUDE (rn) FROM (SELECT row_number() OVER () rn, * FROM '{p}') "
                      f"ORDER BY slot, CASE WHEN slot={slot} THEN -rn ELSE rn END) TO '{q}' (FORMAT parquet)")
            return q
        chk = X.order_check(duckdb, p, swapped(1008), out / "tape", meta)
        self.assertEqual((chk["groups_moved"], chk["ok"]), (1, True))
        # a move inside a slot the key orders (1005: tx 1 then tx 3) fails
        self.assertFalse(X.order_check(duckdb, p, swapped(1005), out / "tape", meta)["ok"])

    def test_meta_md5_ignores_row_order(self):
        c = duckdb.connect()
        p1, p2 = self.tmp / "a.parquet", self.tmp / "b.parquet"
        c.execute(f"COPY (SELECT * FROM (VALUES ('A', 1), ('B', NULL)) t(mint, s0)) TO '{p1}' (FORMAT parquet)")
        c.execute(f"COPY (SELECT * FROM (VALUES ('B', NULL), ('A', 1)) t(mint, s0)) TO '{p2}' (FORMAT parquet)")
        self.assertEqual(X.canonical_md5(duckdb, p1, "mint")["rows_md5"], X.canonical_md5(duckdb, p2, "mint")["rows_md5"])
        self.assertNotEqual(X.canonical_md5(duckdb, p1, None)["rows_md5"], X.canonical_md5(duckdb, p2, None)["rows_md5"])

    def test_strict_lines_refuse(self):
        con = X.connect(duckdb, self.tmp / "t", memory="1GB", threads=1)
        tape = self.tmp / "o" / "tape"
        # malformed JSON and a type that does not convert: ParseFailed (a Refused in e0), no parquet or .tmp left
        for i, line in enumerate(('{"venue": "pumpswap", "slot": \n', '{"venue": "pumpswap", "slot": "abc"}\n')):
            bad = self.tmp / "raw" / "trades" / f"trades-2026-09-20T0{2 + i}.jsonl"
            bad.write_text(line, encoding="utf-8")
            with self.assertRaises(X.ParseFailed):
                X.convert_hour(con, "fast-pool-0918", "trades", f"2026-09-20T0{2 + i}", bad, tape)
        self.assertEqual(list((tape / "trades").iterdir()), [])
        self.assertTrue(issubclass(X.ParseFailed, X.Refused))
        # an uncompressed file that parses converts (duckdb 1.5.6 needs compression='uncompressed', not 'none')
        good = self.tmp / "raw" / "trades" / "trades-2026-09-20T05.jsonl"
        good.write_text(json.dumps(tr("A", "PA", 1, 0, 0)) + "\n", encoding="utf-8")
        self.assertTrue(X.convert_hour(con, "fast-pool-0918", "trades", "2026-09-20T05", good, tape).is_file())
        # an I/O error (corrupt zstd frame, missing file) is not a parse error: a plain Refused, whole run
        corrupt = self.tmp / "raw" / "trades" / "trades-2026-09-20T06.jsonl.zst"
        corrupt.write_bytes(b"not a zstd frame")
        for src in (corrupt, self.tmp / "raw" / "trades" / "missing.jsonl.zst"):
            with self.assertRaises(X.Refused) as cm:
                X.convert_hour(con, "fast-pool-0918", "trades", "2026-09-20T06", src, tape)
            self.assertNotIsInstance(cm.exception, X.ParseFailed)

    def test_hole_rule_excludes_by_usable_hours_only(self):
        out = self.tmp / "o"
        con = X.connect(duckdb, out / "tmp", memory="1GB", threads=2)
        for (kind, hour), p in sorted(self.files.items()):
            X.convert_hour(con, "fast-pool-0918", kind, hour, p, out / "tape")
        vm = self.tmp / "vmap-fixture.json"
        vm.write_text(json.dumps(VMAP))
        hours = {"2026-09-19T23", "2026-09-20T00", "2026-09-20T01"}
        full = X.extract_day(con, out / "tape", self.tmp / "full", "2026-09-20", X.load_vmap(vm), usable=hours)
        # T01 not usable: A and B migrate in T00, so both are excluded, whatever their pools or trades
        hole = X.extract_day(con, out / "tape", self.tmp / "hole", "2026-09-20", X.load_vmap(vm),
                             usable=hours - {"2026-09-20T01"})
        con.close()
        self.assertEqual((full["excluded_hole_mints"], full["migs"]), (0, 2))
        self.assertEqual((hole["excluded_hole_mints"], hole["migs"]), (2, 0))
        c = duckdb.connect()
        self.assertEqual(c.execute(f"SELECT count(*) FROM '{self.tmp}/hole/meta/2026-09-20.parquet'").fetchone()[0], 0)
        self.assertEqual(c.execute(f"SELECT count(*) FROM '{self.tmp}/hole/paths/2026-09-20.parquet'").fetchone()[0], 0)
        # with every hour usable the rows equal the e0 path (usable=None)
        ref = self.tmp / "ref"
        self.run_both(ref)
        for part, order in (("meta", X.META_ORDER), ("paths", None)):
            self.assertEqual(X.canonical_md5(duckdb, self.tmp / "full" / part / "2026-09-20.parquet", order)["rows_md5"],
                             X.canonical_md5(duckdb, ref / part / "2026-09-20.parquet", order)["rows_md5"])

    def test_forbidden_source(self):
        with self.assertRaises(X.Refused):
            X.check_source(Path("/data/mal/blocks/forward-1002ev/trades/x.jsonl.zst"))
        with self.assertRaises(X.Refused):
            X.check_source(Path("/data/mal/blocks/fresh-0828/w1"))

    def forward(self, out: Path) -> dict:
        """run_forward on the fixture walk (self.files as walked; the verify lines hash the files as they are now)."""
        walk = self.tmp / "raw"
        (walk / "checkpoint.json").write_text(json.dumps({"hours": {h: {"status": "sealed"} for h in FIX}}))
        lines = [{"hour": h, "issues": [], "content": {},
                  "sha256": {k: X.sha256_file(self.files[(k, h)]) for k in X.KINDS}} for h in FIX]
        (walk / "verify.jsonl").write_text("".join(json.dumps(r) + "\n" for r in lines))
        ledger = self.tmp / "FINAL_READS.jsonl"
        ledger.write_text(json.dumps({"experiment": "EXP-012", "final": True}) + "\n")
        e0 = self.tmp / "E0-H5.json"
        e0.write_text(json.dumps({"pass": True, "day": X.E0_DAY, "block": X.E0_BLOCK, "duckdb": X.DUCKDB_VERSION,
                                  "blobs": {"tools/h5_forward_extract.py": X.git_blob_sha(X.REPO / "tools/h5_forward_extract.py")}}))
        vm = self.tmp / "vmap.json"
        vm.write_text(json.dumps(VMAP))
        with mock.patch.multiple(X, FWD_DIR=walk, FINAL_LEDGER=ledger, FWD_FROM="2026-09-19T23", FWD_TO="2026-09-20T03"):
            self.assertEqual(X.run_forward(out, vm, e0), 0)
        return json.loads((out / "manifest.json").read_text())

    def test_forward_end_to_end_on_fixture_walk(self):
        out = self.tmp / "fwd"
        man = self.forward(out)
        self.assertEqual(man["reason_counts"], {"ok": 3, "not_walked": 1})
        self.assertEqual(man["days"], ["2026-09-19", "2026-09-20"])
        self.assertEqual(man["excluded_hole_mints_by_day"], {"2026-09-19": 0, "2026-09-20": 0})
        self.assertEqual(man["vmap_role"], X.VMAP_ROLE)
        self.assertIn("not an Am.1 V0 source", man["vmap_role"])
        self.assertEqual(man["blobs"]["tools/forward_v_join.py"], X.git_blob_sha(X.REPO / "tools/forward_v_join.py"))
        ref = self.tmp / "ref"
        self.run_both(ref)
        for part, order in (("meta", X.META_ORDER), ("paths", None)):
            want = X.canonical_md5(duckdb, ref / part / "2026-09-20.parquet", order)["rows_md5"]
            got = man["outputs"][f"{part}/2026-09-20"]["rows_md5"]
            # blk differs (forward-1002 vs fast-pool-0918) in meta only
            if part == "paths":
                self.assertEqual(got, want)
        c = duckdb.connect()
        self.assertEqual(c.execute(f"SELECT blk FROM '{out}/meta/2026-09-20.parquet'").fetchall(), [("forward-1002",)])

    def test_forward_parse_failed_hour_is_dropped_and_holes_exclude(self):
        # T01's migrations file does not parse (its sha still matches the verify line): the whole hour is dropped,
        # T01's trades parquet (written first) is removed, and A and B (migrated in T00) are excluded by the hole rule
        p = self.files[("migrations", "2026-09-20T01")]
        raw = p.with_suffix("")
        raw.write_text('{"type": "complete", "slot": \n', encoding="utf-8")
        subprocess.run(["zstd", "-q", "--rm", "-f", str(raw)], check=True)
        out = self.tmp / "fwd"
        man = self.forward(out)
        self.assertEqual(man["reason_counts"], {"ok": 2, "parse_failed": 1, "not_walked": 1})
        self.assertEqual(man["hour_reasons"]["2026-09-20T01"], "parse_failed")
        self.assertEqual(man["excluded_hole_mints_by_day"], {"2026-09-19": 0, "2026-09-20": 2})
        self.assertEqual(sorted(q.name for q in (out / "tape").rglob("2026-09-20T01*")), [])
        c = duckdb.connect()
        self.assertEqual(c.execute(f"SELECT count(*) FROM '{out}/meta/2026-09-20.parquet'").fetchone()[0], 0)

    def test_forward_other_convert_error_refuses_the_run(self):
        with mock.patch.object(X, "convert_hour", side_effect=X.Refused("convert trades x failed (IOException)")):
            with self.assertRaisesRegex(X.Refused, "IOException"):
                self.forward(self.tmp / "fwd")
        self.assertFalse((self.tmp / "fwd" / "manifest.json").exists())

    def test_forward_hour_needs_creates_and_migrations_sha(self):
        walk = self.tmp / "raw"
        h = "2026-09-20T00"
        (walk / "checkpoint.json").write_text(json.dumps({"hours": {h: {"status": "sealed"}}}))
        line = {"hour": h, "issues": [], "content": {}, "sha256": {"trades": X.sha256_file(self.files[("trades", h)])}}
        (walk / "verify.jsonl").write_text(json.dumps(line) + "\n")
        self.assertEqual(X.forward_hour(walk, h), ({}, "creates_not_verified"))
        line["sha256"]["creates"] = X.sha256_file(self.files[("creates", h)])
        line["sha256"]["migrations"] = "0" * 64
        (walk / "verify.jsonl").write_text(json.dumps(line) + "\n")
        self.assertEqual(X.forward_hour(walk, h), ({}, "migrations_sha_mismatch"))


class ForwardSealTest(unittest.TestCase):
    """Refusals that happen before anything under forward-1002 is opened."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.walk = self.tmp / "forward-1002-does-not-exist"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_fwd(self, ledger: Path, e0: Path | None = None) -> None:
        with mock.patch.multiple(X, FWD_DIR=self.walk, FINAL_LEDGER=ledger):
            X.run_forward(self.tmp / "out", self.tmp / "vmap.json", e0 or self.tmp / "E0-H5.json")

    def test_no_ledger_refuses(self):
        with self.assertRaisesRegex(X.Refused, "sealed until the DEC-016 FINAL"):
            self.run_fwd(self.tmp / "missing.jsonl")
        self.assertFalse((self.tmp / "out").exists())

    def test_test_window_marker_is_not_final(self):
        led = self.tmp / "FINAL_READS.jsonl"
        led.write_text(json.dumps({"final": True, "test_window": True}) + "\n")
        with self.assertRaisesRegex(X.Refused, "FINAL marker is not in the FINAL ledger"):
            self.run_fwd(led)
        self.assertFalse((self.tmp / "out").exists())

    @unittest.skipUnless(HAVE, "needs duckdb 1.5.6")
    def test_e0_record_required_and_bound_to_blob(self):
        led = self.tmp / "FINAL_READS.jsonl"
        led.write_text(json.dumps({"final": True}) + "\n")
        with self.assertRaisesRegex(X.Refused, "no readable E0-H5 record"):
            self.run_fwd(led)
        e0 = self.tmp / "E0-H5.json"
        base = {"pass": True, "day": X.E0_DAY, "block": X.E0_BLOCK, "duckdb": X.DUCKDB_VERSION}
        e0.write_text(json.dumps({**base, "pass": False}))
        with self.assertRaisesRegex(X.Refused, "not a PASS"):
            self.run_fwd(led, e0)
        e0.write_text(json.dumps({**base, "blobs": {"tools/h5_forward_extract.py": "0" * 40}}))
        with self.assertRaisesRegex(X.Refused, "different extractor blob"):
            self.run_fwd(led, e0)
        self.assertFalse((self.tmp / "out").exists())

    def test_constants(self):
        hours = X.hour_list(X.FWD_FROM, X.FWD_TO)
        self.assertEqual((hours[0], hours[-1], len(hours)), ("2026-10-09T23", "2026-10-16T00", 146))
        e0 = X.hour_list(X.E0_FROM, X.E0_TO)
        self.assertEqual((e0[0], e0[-1], len(e0)), ("2026-09-19T00", "2026-09-21T01", 50))
        self.assertFalse(any(X.EXP009[0] <= h < X.EXP009[1] for h in e0))


if __name__ == "__main__":
    unittest.main()
