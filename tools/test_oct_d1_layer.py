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


def write_raw_zst(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pa.OSFile(str(path), "wb") as f:
        with pa.CompressedOutputStream(f, "zstd") as z:
            z.write(data)


def mk(i: int) -> str:
    """A 40-char base58 mint (digits mapped to letters; no 0, O, I, l) so the strict mint regex can read it."""
    return "".join("ABCDEFGHJK"[int(d)] for d in str(i)).rjust(40, "Z")


LA, LC, LD = "A" * 40, "C" * 40, "D" * 40


class MalformedTests(Base):
    """QP comment 6220843906: R1 per file, R2 per stream, R3 last line, R4 taint exclusion, R5 sidecar."""

    def setUp(self):
        super().setUp()
        self.build(trades0=[], trades1=[], migs=[])

    def lines_of(self, rows):
        return [(json.dumps(r) + "\n").encode() for r in rows]

    def put(self, kind, hour, lines):
        write_raw_zst(self.root / DS / kind / f"{kind}-{hour}.jsonl.zst", b"".join(lines))

    def put_creates(self, hour, n, extra=(), start=0):
        """n creates of pad mints (create-only mints count in the tokens_d1 mint total) plus extra raw lines."""
        rows = [create(mk(i), 90 + i, T0 + 1) for i in range(start, start + n)]
        self.put("creates", hour, self.lines_of(rows) + list(extra))

    def good_trades(self):
        t = T0
        return self.lines_of([trade(LA, "w1", "buy", 100, 0, t + 10), trade(LC, "w2", "buy", 400, 0, t + 80),
                              trade(LC, "w3", "buy", 401, 0, t + 81)])

    def many(self, k, mint=LA):
        return self.lines_of([trade(mint, "w1", "buy", 1000, i, T0 + 5, b=CB + 1) for i in range(k)])

    def sidecar(self):
        return json.loads((self.out / layer.SIDECAR).read_text())

    def tokens_set(self):
        return set(self.tokens())

    def files_have_mint(self, mint):
        found = []
        for f in [self.out / "wallet_mint_d1.parquet", *sorted((self.out / "bars_1m").glob("*.parquet")),
                  *sorted((self.out / "trades_d1").glob("*.parquet"))]:
            found.append(mint in {r["mint"] for r in pq.read_table(f, columns=["mint"]).to_pylist()})
        return any(found)

    # ---- R1 / R2 / R3 -------------------------------------------------------------------------------------------
    def test_rule_functions_use_integer_limits(self):
        a, t = layer.allowed_malformed, layer.allowed_stream_total
        self.assertEqual([a("creates", n) for n in (0, 1_500, 9_999, 10_000, 25_000, 100_000, 900_000)],
                         [1, 1, 1, 1, 2, 10, 10])
        self.assertEqual(a("trades", 1_500), 1)
        self.assertEqual(a("migrations", 1_000_000), 0)
        self.assertEqual([t(n) for n in (0, 199_999, 200_000, 1_000_000, 33_000_000)], [1, 1, 2, 10, 330])
        f = layer.malformed_refusal_reasons
        self.assertEqual(f("creates", 1_500, 1, False), [])
        self.assertTrue(f("creates", 1_500, 2, False))
        self.assertTrue(f("migrations", 50, 1, False))
        self.assertTrue(f("trades", 1_000_000, 0, True))  # R3
        self.assertTrue(f("trades", 1_000_000, 11, False))
        self.assertEqual(f("trades", 1_000_000, 10, False), [])

    def test_creates_1500_lines_one_bad_passes_two_refuse(self):
        bad = b'{"type": "create", "mint": "%s", "sl\n' % LD.encode()
        lines = self.lines_of([create(mk(i), 90 + i, T0 + 1) for i in range(750)]) + [bad] + \
            self.lines_of([create(mk(i), 90 + i, T0 + 1) for i in range(750, 1499)])
        self.assertEqual(len(lines), 1500)
        self.put("creates", H0, lines)
        self.put("trades", H0, self.good_trades())
        rc, _, err = self.run_layer()
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.sidecar()["creates_lost"], 1)
        self.put("creates", H0, lines[:100] + [bad, bad] + lines[100:750] + lines[751:])
        rc, _, err = self.run_layer()
        self.assertEqual(rc, 2)
        self.assertIn("exceed the limit 1 (creates)", err)
        self.assertFalse((self.out / "MANIFEST.json").exists())
        self.assertEqual(self.sidecar()["files_with_malformed"][0]["n_malformed"], 2)  # sidecar still written

    def test_migrations_one_bad_line_refuses(self):
        mig = (json.dumps({"type": "migration", "mint": "B", "slot": 300, "event_index": 0}) + "\n").encode()
        self.put("migrations", H0, [mig] * 5000 + [b"garbage\n"] + [mig])
        self.put("trades", H0, self.good_trades())
        rc, _, err = self.run_layer()
        self.assertEqual(rc, 2)
        self.assertIn("(migrations)", err)
        self.assertEqual(self.sidecar()["files_with_malformed"][0]["stream"], "migrations")

    def test_stream_window_cap(self):
        self.put_creates(H0, 800)
        bad = b"{oops\n"
        good = self.good_trades() + self.many(3000)
        self.put("trades", H0, [good[0], bad] + good[1:])
        rc, _, err = self.run_layer()
        self.assertEqual(rc, 0, err)  # 1 bad line of ~3k: file allows 1, stream allows max(1, ...) = 1
        self.put("trades", H0, [good[0], bad] + good[1:])
        self.put("trades", H1, [good[0], bad] + good[1:])  # each file is within R1; the stream total 2 > 1 (R2)
        rc, _, err = self.run_layer()
        self.assertEqual(rc, 2)
        self.assertIn("stream trades: 2 malformed lines", err)
        self.assertIn("window limit 1", err)

    def test_malformed_last_line_refuses(self):
        self.put_creates(H0, 800)
        self.put("trades", H0, self.many(2000) + self.good_trades() + [b'{"type": "trade", "mint": "C", "sl'])
        rc, _, err = self.run_layer()
        self.assertEqual(rc, 2)
        self.assertIn("last line is malformed", err)
        self.assertTrue(self.sidecar()["files_with_malformed"][0]["last_line_malformed"])
        self.assertFalse((self.out / "MANIFEST.json").exists())

    def test_non_object_json_and_bad_utf8_are_malformed(self):
        self.put_creates(H0, 800)
        g = self.good_trades()
        self.put("trades", H0, [g[0], b"[1, 2]\n"] + g[1:] + self.many(100))
        rc, stdout, err = self.run_layer()
        self.assertEqual(rc, 0, err)
        self.assertIn("n=1 first_line=2", stdout)
        self.put("trades", H0, [g[0], b"\xff\xfe{}\n"] + g[1:] + self.many(100))
        rc, stdout, err = self.run_layer()
        self.assertEqual(rc, 0, err)
        self.assertIn("n=1 first_line=2", stdout)

    # ---- R4 -----------------------------------------------------------------------------------------------------
    def test_decoded_mint_is_excluded_from_all_outputs(self):
        self.put_creates(H0, 800, extra=self.lines_of([create(LC, 95, T0 + 2)]))
        g = self.good_trades()
        bad = b'{"type": "trade", "mint": "%s", "trader": "x", "mint": "%s", "sl\n' % (LC.encode(), LD.encode())
        self.put("trades", H0, [g[0], bad] + g[1:])
        rc, _, err = self.run_layer()
        self.assertEqual(rc, 0, err)
        tk = self.tokens()
        self.assertIn(LA, tk)
        self.assertNotIn(LC, tk)  # not kept with missing fields: absent altogether
        self.assertNotIn(LD, tk)
        self.assertEqual(tk[LA]["bc_n_trades"], 1)
        self.assertTrue(self.files_have_mint(LA))
        self.assertFalse(self.files_have_mint(LC))
        sc = self.sidecar()
        self.assertEqual(sc["tainted_mints"], {LC: "decoded"})
        m = json.loads((self.out / "MANIFEST.json").read_text())
        self.assertEqual((m["n_excluded_mints"], m["trades_lost"], m["creates_lost"]), (1, 1, 0))
        self.assertEqual(m["excluded_mints_with_no_detected_complete"], 0)

    def test_trades_line_without_mint_taints_every_mint_with_prints_that_hour(self):
        self.put_creates(H0, 800)
        g = self.good_trades()
        self.put("trades", H0, [g[0], b"not json\n"] + g[1:])
        self.put("trades", H1, self.lines_of([trade(LD, "w5", "buy", 500, 0, T0 + 3700)]))
        rc, _, err = self.run_layer()
        self.assertEqual(rc, 0, err)
        tk = self.tokens()
        self.assertEqual({LA, LC} & set(tk), set())  # both printed in H0
        self.assertIn(LD, tk)  # prints only in H1
        self.assertEqual(self.sidecar()["tainted_mints"], {LA: "hour_fallback", LC: "hour_fallback"})
        self.assertFalse(self.files_have_mint(LA))

    def test_creates_line_without_mint_taints_no_create_mints_first_printed_in_hour_or_next(self):
        self.put("creates", H0, [b"{broken\n"] + [ln for ln in self.lines_of([create(mk(i), 90 + i, T0 + 1) for i in range(800)])]
                 + self.lines_of([create(LA, 95, T0 + 2)]))
        mid = T0 + 3600 * 1 + 5  # first print in H1: inside [H0, H0 + 2 h)
        late = T0 + 7200 + 5  # first print in H0 + 2 h: outside
        self.put("trades", H0, self.lines_of([trade(LA, "w1", "buy", 100, 0, T0 + 10), trade(LC, "w2", "buy", 101, 0, mid)]))
        self.put("trades", "2026-10-02T17", self.lines_of([trade(LD, "w3", "buy", 700, 0, late)]))
        rc, _, err = self.run_layer(end="2026-10-02T18")
        self.assertEqual(rc, 0, err)
        tk = self.tokens()
        self.assertIn(LA, tk)  # has a create: kept
        self.assertTrue(tk[LA]["has_create"])
        self.assertNotIn(LC, tk)  # no create, first print inside the window
        self.assertIn(LD, tk)  # no create, first print outside it
        self.assertFalse(tk[LD]["has_create"])
        self.assertEqual(self.sidecar()["tainted_mints"], {LC: "hour_fallback"})

    def test_more_than_half_percent_excluded_refuses(self):
        g = self.good_trades()
        bad = b'{"type": "trade", "mint": "%s", "sl\n' % LC.encode()
        # mints in tokens_d1: LA, LC and n pad mints. One excluded: refuses iff 1 * 200 > n + 2.
        self.put_creates(H0, 197)
        self.put("trades", H0, [g[0], bad] + g[1:])
        rc, _, err = self.run_layer()
        self.assertEqual(rc, 2)
        self.assertIn("exceed 0.5%", err)
        self.assertFalse((self.out / "tokens_d1.parquet").exists())
        self.assertEqual(self.sidecar()["taint_summary"]["n_excluded_mints"], 1)  # sidecar written on refusal
        self.put_creates(H0, 198)  # 200 mints: 1 * 200 > 200 is false
        rc, _, err = self.run_layer()
        self.assertEqual(rc, 0, err)

    def test_c_gate_is_computed_before_the_drop(self):
        # B migrated, its only complete buy is on a lost line that names B: C1 still sees the miss (gate refuses), and the
        # precount reports the excluded mint among the no-detected-complete ones.
        B = "B" * 40
        self.build(trades0=[], trades1=[], migs=[{"type": "migration", "mint": B, "slot": 300, "event_index": 0}])
        self.put_creates(H0, 800)
        bad = b'{"type": "trade", "mint": "%s", "sl\n' % B.encode()
        self.put("trades", H0, self.lines_of([trade(B, "w1", "buy", 200, 0, T0 + 5)]) + [bad] + self.good_trades())
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = layer.main(["--dataset", DS, "--start", H0, "--end", "2026-10-02T17", "--out", str(self.out), "--precount"])
        self.assertEqual(rc, 2)  # 1 of 1 traded migrated mints has no detected complete: C1 fails, unchanged by the drop
        pc = json.loads((self.out / "PRECOUNT.json").read_text())
        self.assertEqual(pc["complete_detection"]["qa_traded_mints_with_migrate_pos_but_no_detected_complete"], 1)
        self.assertEqual((pc["n_excluded_mints"], pc["excluded_mints_with_no_detected_complete"]), (1, 1))

    # ---- R5 -----------------------------------------------------------------------------------------------------
    def test_sidecar_fields_per_skipped_line(self):
        self.put_creates(H0, 800)
        g = self.good_trades()
        bad = b'{"type": "trade", "mint": "%s", "x": {"mint":"%s"}, "mint": "%s", "sl\n' % (LC.encode(), LD.encode(), LC.encode())
        self.put("trades", H0, [g[0], bad, g[1], g[2]])  # slots 100 | bad | 400, 401
        rc, stdout, err = self.run_layer()
        self.assertEqual(rc, 0, err)
        self.assertIn(f"SKIP_MALFORMED file=trades-{H0}.jsonl.zst n=1 first_line=2", stdout)
        sc = self.sidecar()
        (f,) = sc["files_with_malformed"]
        (r,) = f["skipped_lines"]
        self.assertEqual((f["stream"], f["n_malformed"], f["n_lines"], f["first_line"]), ("trades", 1, 4, 2))
        self.assertEqual(r["line"], 2)
        self.assertEqual(r["byte_offset"], len(g[0]))
        self.assertEqual(r["bytes"], len(bad))
        self.assertEqual(r["mints_decoded"], [LC, LD])
        self.assertEqual(r["n_mint_matches"], 3)
        self.assertEqual((r["prev_valid_slot"], r["next_valid_slot"]), (100, 400))
        self.assertEqual((r["creates_lost"], r["trades_lost"]), (0, 1))
        self.assertEqual((sc["creates_lost"], sc["trades_lost"]), (0, 1))
        self.assertEqual(sc["tainted_mints"], {LC: "decoded"})
        self.assertEqual(sc["rule"]["source"], "QP comment 6220843906")
        self.assertNotIn("sl\\n", json.dumps(sc))  # offsets and mints, never line content
        m = json.loads((self.out / "MANIFEST.json").read_text())
        for k in ("creates_lost", "trades_lost", "n_excluded_mints", "excluded_mints_with_no_detected_complete"):
            self.assertIn(k, m)
        self.assertTrue((self.out / "README_D1_USAGE.md").exists())
        self.assertIn("EXCLUDED", (self.out / "README_D1_USAGE.md").read_text())

    def test_offsets_of_two_bad_lines_on_a_refusal(self):
        self.put_creates(H0, 800)
        g = self.good_trades()
        bad1, bad2 = b'{"type": "trade", "mint": \n', b"not json at all\n"
        self.put("trades", H0, [g[0], bad1, g[1], b"\n", bad2, g[2]])
        rc, _, err = self.run_layer()
        self.assertEqual(rc, 2)
        (f,) = self.sidecar()["files_with_malformed"]
        off1 = len(g[0])
        off2 = off1 + len(bad1) + len(g[1]) + 1
        self.assertEqual([(o["line"], o["byte_offset"], o["bytes"]) for o in f["skipped_lines"]],
                         [(2, off1, len(bad1)), (5, off2, len(bad2))])
        self.assertEqual([(o["prev_valid_slot"], o["next_valid_slot"]) for o in f["skipped_lines"]], [(100, 400), (400, 401)])
        self.assertNotIn("not json at all", json.dumps(self.sidecar()))

    # ---- memory and precount ------------------------------------------------------------------------------------
    def test_rss_is_checked_inside_the_line_loop(self):
        self.put("trades", H0, self.many(25))
        calls: list[str] = []
        with mock.patch.object(layer, "RSS_CHECK_EVERY_LINES", 10), \
                mock.patch.object(layer, "check_rss", lambda lim, where: calls.append(where)):
            self.assertEqual(self.run_layer()[0], 0)
        self.assertIn(f"trades-{H0}.jsonl.zst line 10", calls)
        self.assertIn(f"trades-{H0}.jsonl.zst line 20", calls)

    def test_rss_self_check_aborts_cleanly(self):
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            rc = layer.main(["--dataset", DS, "--start", H0, "--end", "2026-10-02T17", "--out", str(self.out),
                             "--max-rss-gb", "0.0001"])
        self.assertEqual(rc, 2)
        self.assertIn("exceeds --max-rss-gb", err.getvalue())
        self.assertFalse((self.out / "MANIFEST.json").exists())

    def test_default_rss_limit_is_below_the_16gb_job_limit_with_margin(self):
        ap_default = 13.0
        with mock.patch.object(layer, "run", lambda a: self.assertEqual(a.max_rss_gb, ap_default) or {"precount": {}}):
            with contextlib.redirect_stdout(io.StringIO()):
                layer.main(["--dataset", DS, "--start", H0, "--end", "2026-10-02T17", "--out", str(self.out), "--precount"])

    def test_precount_mode_prints_skip_lines_and_input_hashes(self):
        self.put_creates(H0, 800)
        g = self.good_trades()
        self.put("trades", H0, [g[0], b"bad\n", g[1], g[2]] + self.many(2000))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = layer.main(["--dataset", DS, "--start", H0, "--end", "2026-10-02T17", "--out", str(self.out),
                             "--precount"])
        self.assertEqual(rc, 0, err.getvalue())
        self.assertIn("SKIP_MALFORMED file=", out.getvalue())
        self.assertIn("sha256=", out.getvalue())
        self.assertIn("resolved_gates", out.getvalue())
        pc = json.loads((self.out / "PRECOUNT.json").read_text())
        self.assertEqual(pc["malformed_lines"]["trades"]["malformed"], 1)
        self.assertEqual((pc["trades_lost"], pc["creates_lost"], pc["n_excluded_mints"]), (1, 0, 2))
        self.assertTrue((self.out / layer.SIDECAR).exists())
        self.assertFalse((self.out / "README_D1_USAGE.md").exists())
        self.assertFalse((self.out / "tokens_d1.parquet").exists())

    def test_readme_ships_with_full_run(self):
        self.assertEqual(self.run_layer()[0], 0)
        txt = (self.out / "README_D1_USAGE.md").read_text()
        self.assertIn("Exploration only", txt)
        self.assertIn("No join", txt)
        self.assertEqual(json.loads((self.out / layer.SIDECAR).read_text())["files_with_malformed"], [])


def rows_md5(out: Path) -> str:
    """md5 over every output row of the D1 layer (tokens, wallet_mint, bars, trades), files in sorted order."""
    h = hashlib.md5()
    for f in [out / "tokens_d1.parquet", out / "wallet_mint_d1.parquet", *sorted((out / "bars_1m").glob("*.parquet")),
              *sorted((out / "trades_d1").glob("*.parquet"))]:
        h.update(f.name.encode())
        for r in pq.read_table(f).to_pylist():
            h.update(json.dumps(r, sort_keys=True, default=str).encode())
    return h.hexdigest()


# md5 of the output rows of the fixture below, produced by the code at cf73edd (before the QP rule change, no malformed lines).
EQUIV_MD5 = "16577e35b4e4f2defb28a90127c0121b"


class DecisionEquivalenceTests(Base):
    def test_output_rows_unchanged_vs_cf73edd(self):
        extra = [trade("D", None, "buy", 520, 0, T0 + 3720), trade("D", "w7", "sell", 521, 0, T0 + 3730, b=CB - 1)]
        self.build(trades1=[trade("A", "w9", "buy", 500, 0, T0 + 3700), trade("C", "w2", "sell", 501, 0, T0 + 3710)] + extra)
        self.assertEqual(self.run_layer()[0], 0)
        self.assertEqual(rows_md5(self.out), EQUIV_MD5)


class OctoberRoleTests(unittest.TestCase):
    def test_existing_role_module_still_imports(self):
        self.assertEqual(oc.ROLE_OCT_D1, "oct-pregrad-d1")


if __name__ == "__main__":
    unittest.main()
