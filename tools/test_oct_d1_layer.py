"""Tests for tools/oct_d1_layer.py. Synthetic hour files in a tmp dir only: no October, forward or walk file is opened.

Needs pyarrow and duckdb (e.g. /data/mal/audit-1008/venv/bin/python -m unittest tools.test_oct_d1_layer);
skipped where they are missing.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

try:
    import duckdb  # noqa: F401
    import pyarrow as pa
    import pyarrow.parquet as pq

    HAVE = True
except ImportError:  # pragma: no cover
    HAVE = False

from tools import mal_catalog_october as oc

if HAVE:
    from tools import oct_d1_layer as layer

DS = "forward-1002"
H0, H1 = "2026-10-02T15", "2026-10-02T16"
T0 = int(datetime(2026, 10, 2, 15, 0, tzinfo=timezone.utc).timestamp())
NOW = datetime(2026, 10, 16, 5, 0, tzinfo=timezone.utc)
CB = 279_900_000_000_000  # == layer.COMPLETE_VIRTUAL_TOKEN_RESERVE_RAW
FORBIDDEN_EXTRAS = {"lp_fee": 7, "protocol_fee": 7, "creator_fee": 7, "pool": "POOLX", "pool_quote_amount": 7,
                    "virtual_quote_reserves": 7, "fm_buy_sol": 1.5, "fm_flow_sol": 1.5, "ix_name": "buy",
                    "buyback_fee": 7, "block_time": 1}


def trade(mint, trader, side, slot, ei, ts, lam=1_000_000_000, q=30_000_000_000, b=1_000_000_000_000_000,
          venue="pump_bonding", **extra):
    r = {"v": 1, "type": "trade", "feed": "f", "venue": venue, "mint": mint, "trader": trader, "side": side,
         "sol_lamports": lam, "token_raw": 5_000_000, "sol": lam / 1e9, "quote_reserve": q, "base_reserve": b,
         "slot": slot, "signature": f"s{slot}{ei}", "event_index": ei, "event_ts": ts, "commitment": "confirmed",
         "pool": None if venue == "pump_bonding" else "POOL", "quote_mint": "WSOL"}
    r.update(FORBIDDEN_EXTRAS)
    r.update(extra)
    return r


def create(mint, slot, ts):
    return {"v": 1, "type": "create", "mint": mint, "slot": slot, "event_index": 0, "event_ts": ts, "signature": "c",
            "creator": "CREATORSECRET", "name": "NAMESECRET", "symbol": "SYMSECRET", "is_mayhem_mode": False}


def write_zst(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = "".join(json.dumps(r) + "\n" for r in rows).encode()
    with pa.OSFile(str(path), "wb") as f:
        with pa.CompressedOutputStream(f, "zstd") as z:
            z.write(data)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        if not HAVE:
            self.skipTest("pyarrow/duckdb not available")
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "blocks"
        self.out = self.tmp / "out"
        (self.tmp / "FINAL_READS.jsonl").write_text(json.dumps({"final": True}) + "\n")
        oc._reset_latch_for_tests()
        self.patches = [mock.patch.object(oc, "BLOCKS_ROOT", self.root),
                        mock.patch.object(oc, "DEFAULT_FINAL_LEDGER", self.tmp / "FINAL_READS.jsonl"),
                        mock.patch.object(oc, "_now", lambda: NOW)]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])
        self.addCleanup(oc._reset_latch_for_tests)
        self.build()

    def build(self, trades0=None, trades1=None, migs=None, drop=None):
        ds = self.root / DS
        hours = layer._hours(H0, oc.FORWARD_END_EXCLUSIVE)
        for h in hours:
            for kind in ("trades", "creates", "migrations"):
                write_zst(ds / kind / f"{kind}-{h}.jsonl.zst", [])
        t = T0
        a = [trade("A", "w1", "buy", 100, 0, t + 10, lam=2_000_000_000, extra_x=1),
             trade("A", "w2", "buy", 101, 0, t + 20, b=500_000_000_000_000),
             trade("A", "w1", "buy", 102, 0, t + 30, b=CB),  # the complete trade (post-state)
             trade("A", "w3", "buy", 102, 1, t + 31, b=CB),  # after the complete trade, same slot
             trade("A", "w3", "sell", 103, 0, t + 40, b=CB),
             trade("A", "p1", "buy", 104, 0, t + 50, venue="pumpswap", b=40_000_000_000_000)]  # pool row
        b = [trade("B", "w1", "buy", 200, 0, t + 60, b=CB),  # B completes too (a migrated mint always did)
             trade("B", "w4", "sell", 201, 0, t + 61),
             trade("B", "w5", "buy", 300, 0, t + 70)]  # at/after B's migrate tx (300, 0)
        c = [trade("C", "w2", "buy", 400, 0, t + 80)]
        rows0 = trades0 if trades0 is not None else a + b + c
        write_zst(ds / "trades" / f"trades-{H0}.jsonl.zst", rows0)
        rows1 = trades1 if trades1 is not None else [trade("A", "w9", "buy", 500, 0, T0 + 3700),  # after complete, next hour
                                                     trade("C", "w2", "sell", 501, 0, T0 + 3710)]
        write_zst(ds / "trades" / f"trades-{H1}.jsonl.zst", rows1)
        write_zst(ds / "creates" / f"creates-{H0}.jsonl.zst", [create("A", 90, T0 + 1), create("B", 91, T0 + 2)])
        mig = migs if migs is not None else [{"type": "migration", "mint": "B", "slot": 300, "event_index": 0},
                                              {"type": "migration", "mint": "A", "slot": 150, "event_index": 0}]
        write_zst(ds / "migrations" / f"migrations-{H0}.jsonl.zst", mig)
        if drop:
            (ds / drop[0] / f"{drop[0]}-{drop[1]}.jsonl.zst").unlink()

    def run_layer(self, end="2026-10-02T17"):
        err = io.StringIO()
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = layer.main(["--dataset", DS, "--start", H0, "--end", end, "--out", str(self.out)])
        return rc, out.getvalue(), err.getvalue()

    def tokens(self):
        return {r["mint"]: r for r in pq.read_table(self.out / "tokens_d1.parquet").to_pylist()}


class LayerTests(Base):
    def test_outputs_exclude_post_complete_post_migrate_and_pool_rows(self):
        rc, stdout, err = self.run_layer()
        self.assertEqual(rc, 0, err)
        tk = self.tokens()
        self.assertEqual(set(tk), {"A", "B", "C"})
        self.assertTrue(tk["A"]["reached_complete"])
        self.assertEqual((tk["A"]["complete_slot"], tk["A"]["complete_event_index"]), (102, 0))
        self.assertEqual(tk["A"]["complete_ms"], (T0 + 30) * 1000)
        self.assertEqual(tk["A"]["bc_n_trades"], 3)  # rows 4,5, the pool row and next-hour row are out
        self.assertEqual(tk["A"]["bc_n_traders"], 2)
        self.assertEqual(tk["A"]["bc_buy_sol"], 4.0)
        self.assertEqual(tk["A"]["bc_sell_sol"], None)
        self.assertEqual(tk["A"]["create_ms"], (T0 + 1) * 1000)
        self.assertTrue(tk["A"]["has_create"])
        self.assertEqual(tk["B"]["bc_n_trades"], 1)  # the sell after complete and (300, 0), the migrate tx, are out
        self.assertTrue(tk["B"]["reached_complete"])
        self.assertFalse(tk["C"]["reached_complete"])
        self.assertFalse(tk["C"]["has_create"])
        self.assertEqual(tk["C"]["bc_n_trades"], 2)  # C never completes: next-hour sell is in
        m = json.loads((self.out / "MANIFEST.json").read_text())
        sk = m["skipped_rows"]
        self.assertEqual(sk["pool_venue"], 1)
        self.assertEqual(sk["after_migrate"], 2)  # B (300,0) and the next-hour print of migrated A
        self.assertEqual(sk["after_complete"], 3)  # A: same-slot next event, later sell; B: the sell
        self.assertEqual(m["complete_detection"]["exact_constant"], 2)
        self.assertEqual(m["complete_detection"]["qa_traded_mints_with_migrate_pos_but_no_detected_complete"], 0)

    def test_forbidden_columns_and_values_never_reach_outputs(self):
        self.assertEqual(self.run_layer()[0], 0)
        files = [self.out / "tokens_d1.parquet", self.out / "wallet_mint_d1.parquet",
                 *sorted((self.out / "bars_1m").glob("*.parquet")),
                *sorted((self.out / "trades_d1").glob("*.parquet"))]
        self.assertTrue(files[2:], "a bars file was written")
        self.assertTrue((self.out / "trades_d1" / "2026-10-02.parquet").exists())
        blob = b""
        for f in files:
            cols = pq.read_schema(f).names
            layer.check_output_columns(cols)
            for c in cols:
                low = c.lower()
                for bad in ("pool", "v_lamports", "creator", "name", "symbol", "mayhem", "fee", "fm_", "ps_", "grad",
                            "pumpswap", "v0", "migrat", "flow"):
                    self.assertNotIn(bad, low, (f.name, c))
            blob += f.read_bytes()
        self.assertEqual(sorted(pq.read_schema(self.out / "tokens_d1.parquet").names), sorted([
            "mint", "create_ms", "create_slot", "create_event_index", "has_create", "reached_complete", "complete_ms",
            "complete_slot", "complete_event_index", "bc_first_ms", "bc_last_ms", "bc_buy_sol", "bc_sell_sol",
            "bc_n_trades", "bc_n_traders", "bc_first_quote_reserve", "bc_first_base_reserve"]))
        for secret in (b"CREATORSECRET", b"NAMESECRET", b"SYMSECRET", b"POOLX"):
            self.assertNotIn(secret, blob)

    def test_bars_columns_and_new_buyer_values(self):
        self.assertEqual(self.run_layer()[0], 0)
        bars = pq.read_table(self.out / "bars_1m" / "2026-10-02.parquet")
        self.assertEqual(bars.column_names, [
            "mint", "venue", "minute_ms", "open", "high", "low", "close", "vwap", "buy_sol", "sell_sol", "n_buys",
            "n_sells", "n_distinct_buyers", "n_distinct_sellers", "net_sol", "max_buy_sol", "max_sell_sol",
            "last_quote_reserve", "last_base_reserve", "last_slot", "new_buy_sol", "n_new_buyers", "new_buyer_share"])
        rows = {(r["mint"], r["minute_ms"]): r for r in bars.to_pylist()}
        self.assertTrue(all(r["venue"] == "pump_bonding" for r in rows.values()))
        a = rows[("A", (T0 // 60) * 60 * 1000)]  # A's three kept prints are all in minute 0
        self.assertEqual((a["n_buys"], a["n_sells"], a["last_slot"]), (3, 0, 102))
        self.assertEqual(a["n_new_buyers"], 2)  # w1 and w2 both make their first print of A in that minute
        self.assertEqual(a["new_buy_sol"], 4.0)
        self.assertEqual(a["new_buyer_share"], 1.0)
        self.assertEqual(a["last_base_reserve"], CB)
        wm = pq.read_table(self.out / "wallet_mint_d1.parquet").to_pylist()
        w1a = [r for r in wm if r["trader"] == "w1" and r["mint"] == "A"][0]
        self.assertEqual((w1a["n_buys"], w1a["buy_sol"]), (2, 3.0))
        self.assertFalse([r for r in wm if r["trader"] in ("p1", "w9", "w5")])  # pool / post-complete / post-migrate

    def test_trades_d1_prints_are_masked_bonding_only(self):
        self.assertEqual(self.run_layer()[0], 0)
        t = pq.read_table(self.out / "trades_d1" / "2026-10-02.parquet")
        self.assertEqual(t.column_names, ["mint", "slot", "event_index", "ms", "seq", "side", "sol", "sol_lamports",
                                          "token_raw", "quote_reserve", "base_reserve", "price", "trader"])
        rows = t.to_pylist()
        self.assertEqual(len(rows), 6)  # A 3 + B 1 + C 2; no pool, post-complete or post-migrate print
        self.assertFalse([r for r in rows if r["trader"] in ("p1", "w9", "w5")])
        self.assertEqual(rows, sorted(rows, key=lambda r: (r["slot"] * 100000 + r["event_index"], r["seq"])))
        m = json.loads((self.out / "MANIFEST.json").read_text())
        self.assertEqual(m["bonding_prints_per_hour"], {H0: 5, H1: 1})
        self.assertIn("NOT AVAILABLE", m["quote_mint"])

    def test_manifest_lists_sha256_for_every_input(self):
        self.assertEqual(self.run_layer()[0], 0)
        m = json.loads((self.out / "MANIFEST.json").read_text())
        self.assertEqual(m["role"], oc.ROLE_OCT_D1)
        self.assertIn("final_ledger", m["resolved_gates"])
        self.assertTrue(m["git_head"])
        mig_hours = layer._hours(H0, oc.FORWARD_END_EXCLUSIVE)
        self.assertEqual(len(m["inputs"]), 2 * 2 + len(mig_hours))
        kinds = {i["kind"] for i in m["inputs"]}
        self.assertEqual(kinds, {"trades", "creates", "migrations"})
        for i in m["inputs"]:
            self.assertEqual(i["sha256"], sha(Path(i["path"])))
            self.assertNotIn("pools", i["path"])
        self.assertEqual([c["hour"] for c in m["coverage_per_hour"]], [H0, H1])
        c0 = m["coverage_per_hour"][0]
        self.assertEqual((c0["bonding_prints"], c0["mints_with_prints"], c0["creates"], c0["completes"]), (5, 3, 2, 2))
        for k, v in m["outputs"].items():
            self.assertEqual(v["sha256"], sha(self.out / k))
        self.assertIn("tokens_d1.parquet", m["row_counts"])
        self.assertIn("git_head", self.run_layer()[1])  # the stdout summary carries the same facts

    def test_stdout_summary_has_no_row_content(self):
        rc, stdout, _ = self.run_layer()
        self.assertEqual(rc, 0)
        for s in ("w1", "CREATORSECRET", "POOLX"):
            self.assertNotIn(s, stdout)

    def test_non_bonding_row_that_would_be_kept_is_refused(self):
        bad = trade("A", "p1", "buy", 104, 0, T0 + 50, venue="pumpswap")
        real = oc.guarded_rows

        def leaky(role, rows, *a, **k):
            return iter([dict(bad)])

        with mock.patch.object(oc, "guarded_rows", leaky):
            rc, _, err = self.run_layer()
        self.assertEqual(rc, 2)
        self.assertIn("pumpswap", err)
        self.assertFalse((self.out / "tokens_d1.parquet").exists())
        self.assertIs(oc.guarded_rows, real)

    def test_check_read_runs_before_any_open_and_refusal_stops_everything(self):
        events: list[str] = []
        real_cr, real_lines, real_sha, real_mp = oc.check_read, oc._lines, layer._sha256, oc.migrate_positions

        def cr(*a, **k):
            events.append("check_read")
            self.assertEqual(a[1], oc.ROLE_OCT_D1)
            return real_cr(*a, **k)

        def ln(p):
            events.append("open")
            return real_lines(p)

        def sh(p):
            events.append("open")
            return real_sha(p)

        def mp(*a, **k):
            events.append("migrate_positions")
            return real_mp(*a, **k)

        with mock.patch.object(oc, "check_read", cr), mock.patch.object(layer, "_sha256", sh), \
                mock.patch.object(oc, "migrate_positions", mp):
            self.assertEqual(self.run_layer()[0], 0)
        self.assertEqual(events[0], "check_read")
        self.assertEqual(events.count("check_read"), 1)
        self.assertLess(events.index("check_read"), events.index("open"))
        self.assertLess(events.index("check_read"), events.index("migrate_positions"))
        # before the release time check_read refuses and nothing is opened or written
        shutil_out = self.out
        oc._reset_latch_for_tests()
        opened: list[str] = []
        with mock.patch.object(oc, "_now", lambda: datetime(2026, 10, 16, 1, 0, tzinfo=timezone.utc)), \
                mock.patch.object(layer, "_sha256", lambda p: opened.append("x")), \
                mock.patch.object(oc, "_lines", lambda p: opened.append("y") or iter(())):
            self.out = self.tmp / "out2"
            rc, _, err = self.run_layer()
        self.assertEqual(rc, 2)
        self.assertIn("check_read refused", err)
        self.assertEqual(opened, [])
        self.assertFalse((self.out / "tokens_d1.parquet").exists())
        self.assertNotEqual(shutil_out, self.out)

    def test_wrong_window_and_missing_input_refused(self):
        rc, _, err = self.run_layer(end="2026-10-16T02")  # walk 2 hours are never released
        self.assertEqual(rc, 2)
        self.assertIn("refused", err.lower())
        self.build(drop=("trades", H1))
        rc, _, err = self.run_layer()
        self.assertEqual(rc, 2)
        self.assertIn("missing", err)

    def test_gate_stops_when_a_migrated_mint_has_no_detected_complete(self):
        (self.out).mkdir(parents=True)
        (self.out / "MANIFEST.json").write_text("{}")  # stale manifest from an earlier run
        self.build(trades1=[trade("D", "w1", "buy", 500, 0, T0 + 3700)],
                   migs=[{"type": "migration", "mint": "A", "slot": 150, "event_index": 0},
                         {"type": "migration", "mint": "B", "slot": 300, "event_index": 0},
                         {"type": "migration", "mint": "D", "slot": 600, "event_index": 0}])
        rc, _, err = self.run_layer()
        self.assertEqual(rc, 2)
        self.assertIn("complete-detection gate failed", err)
        self.assertFalse((self.out / "tokens_d1.parquet").exists())
        self.assertFalse((self.out / "MANIFEST.json").exists())

    def test_gate_stops_when_constant_does_not_fit(self):
        rows = [trade("A", "w1", "buy", 100, 0, T0 + 10, b=CB - 5)]  # below, never exact
        self.build(trades0=rows, trades1=[], migs=[{"type": "migration", "mint": "A", "slot": 150, "event_index": 0}])
        rc, _, err = self.run_layer()
        self.assertEqual(rc, 2)
        self.assertIn("hit the constant exactly", err)

    def test_precount_writes_counts_only(self):
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            rc = layer.main(["--dataset", DS, "--start", H0, "--end", "2026-10-02T17", "--out", str(self.out),
                             "--precount"])
        self.assertEqual(rc, 0, err.getvalue())
        pc = json.loads((self.out / "PRECOUNT.json").read_text())
        self.assertEqual(pc["complete_detection"]["exact_constant"], 2)
        self.assertFalse((self.out / "tokens_d1.parquet").exists())
        self.assertFalse((self.out / "MANIFEST.json").exists())

    def test_gate_function(self):
        self.assertEqual(layer.complete_gate_reasons(0, 0, 0, 0), [])
        self.assertEqual(layer.complete_gate_reasons(1000, 5, 100, 0), [])
        self.assertTrue(layer.complete_gate_reasons(1000, 6, 100, 0))
        self.assertTrue(layer.complete_gate_reasons(1000, 0, 90, 10))
        self.assertTrue(layer.complete_gate_reasons(10, 0, 0, 0))

    def test_output_column_guard(self):
        for bad in ("v_lamports", "ps_first_price", "pool", "creator", "fm_flow_sol", "grad_ms", "v0_lamports",
                    "synthetic_class", "fee_sol_reported", "is_mayhem_mode", "name"):
            with self.assertRaises(layer.LayerRefused, msg=bad):
                layer.check_output_columns(["mint", bad])
        layer.check_output_columns(["mint", "reached_complete", "complete_ms", "new_buyer_share"])


class OctoberRoleTests(unittest.TestCase):
    def test_existing_role_module_still_imports(self):
        self.assertEqual(oc.ROLE_OCT_D1, "oct-pregrad-d1")


if __name__ == "__main__":
    unittest.main()
