"""tools.tape_coverage with synthetic tapes. No network, no host access."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import tempfile
import tracemalloc
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from tools import tape_coverage as tc
from tools import tape_coverage_scan as scan

H17 = tc.parse_instant("2026-10-01T17")
H18 = H17 + tc.HOUR_MS
H19 = H18 + tc.HOUR_MS


def row(sig: str, t_ms: int, idx: int = 0, venue: str = "pump_bonding", **extra) -> dict:
    out = {
        "v": 1,
        "venue": venue,
        "mint": "MINT_SECRET",
        "trader": "WALLET_SECRET",
        "sol_lamports": 123456789,
        "signature": sig,
        "event_index": idx,
        "t_recv_ms": t_ms,
    }
    out.update(extra)
    return out


def write_hour(directory: Path, hour_ms: int, rows: list[dict], *, zst: bool = False) -> Path:
    stamp = scan.hour_stamp(hour_ms)
    body = "".join(json.dumps(r) + "\n" for r in rows)
    plain = directory / f"trades-{stamp}.jsonl"
    plain.write_text(body, encoding="utf-8")
    if zst:
        subprocess.run(["zstd", "-q", "-f", "--rm", str(plain)], check=True)
        return directory / f"trades-{stamp}.jsonl.zst"
    return plain


def sides(fast_rows: list[dict], oracle_rows: list[dict], lo: int, hi: int):
    with tempfile.TemporaryDirectory() as tmp:
        fdir, odir = Path(tmp, "f"), Path(tmp, "o")
        fdir.mkdir()
        odir.mkdir()
        for d, rows in ((fdir, fast_rows), (odir, oracle_rows)):
            by_hour: dict[int, list[dict]] = {}
            for r in rows:
                by_hour.setdefault(r["t_recv_ms"] - r["t_recv_ms"] % tc.HOUR_MS, []).append(r)
            for h, rs in by_hour.items():
                write_hour(d, h, rs)
        margin = 10_000
        fast = tc.load_local(fdir, lo - margin, hi + margin, "fast")
        oracle = tc.load_local(odir, lo - margin, hi + margin, "oracle")
    return fast, oracle


class IdentityTests(unittest.TestCase):
    def test_event_index_makes_trades_in_one_tx_distinct(self) -> None:
        oracle = [row("A", H17 + 1000, 0), row("A", H17 + 1000, 1), row("B", H17 + 2000, 0)]
        fast = [row("A", H17 + 1050, 0), row("B", H17 + 2050, 0)]
        f, o = sides(fast, oracle, H17, H18)
        rep = tc.compare(f, o, H17, H18)
        self.assertEqual(rep["pooled"]["oracle_rows"], 3)
        self.assertEqual(rep["pooled"]["matched"], 2)  # A:1 is not covered by fast's A:0
        self.assertAlmostEqual(rep["pooled"]["coverage"], 2 / 3)
        # By signature all three Oracle rows' signatures are there: A and B.
        sig = rep["pooled"]["signature_level"]
        self.assertEqual((sig["oracle_rows"], sig["matched"]), (2, 2))

    def test_duplicates_keep_earliest_stamp(self) -> None:
        oracle = [row("A", H17 + 1000)]
        fast = [row("A", H17 + 1300), row("A", H17 + 1100), row("A", H17 + 1200)]
        f, o = sides(fast, oracle, H17, H18)
        rep = tc.compare(f, o, H17, H18)
        self.assertEqual(rep["duplicates_dropped"]["fast_identity"], 2)
        self.assertEqual(rep["pooled"]["lag_ms_fast_minus_oracle"]["all"]["p50"], 100.0)

    def test_hash_is_stable_and_separates_index(self) -> None:
        self.assertEqual(tc.hash64("A:0"), tc.hash64("A:0"))
        self.assertNotEqual(tc.hash64("A:0"), tc.hash64("A:1"))


class CoverageMathTests(unittest.TestCase):
    def test_per_hour_pooled_and_reverse(self) -> None:
        # Hour 17: Oracle 10 rows, fast has 9 of them + 1 extra. Hour 18: Oracle 10, fast has 10.
        oracle, fast = [], []
        for i in range(10):
            oracle.append(row(f"s17-{i}", H17 + 1000 * (i + 1)))
            if i != 3:
                fast.append(row(f"s17-{i}", H17 + 1000 * (i + 1) - 40))
            oracle.append(row(f"s18-{i}", H18 + 1000 * (i + 1)))
            fast.append(row(f"s18-{i}", H18 + 1000 * (i + 1) - 40))
        fast.append(row("fast-only", H17 + 5500))
        f, o = sides(fast, oracle, H17, H19)
        rep = tc.compare(f, o, H17, H19)
        h17, h18 = rep["hours"]
        self.assertEqual((h17["oracle_rows"], h17["matched"]), (10, 9))
        self.assertAlmostEqual(h17["coverage"], 0.9)
        self.assertFalse(h17["meets_bar"])
        self.assertEqual((h18["oracle_rows"], h18["matched"]), (10, 10))
        self.assertTrue(h18["meets_bar"])
        p = rep["pooled"]
        self.assertEqual((p["oracle_rows"], p["matched"], p["oracle_only"]), (20, 19, 1))
        self.assertAlmostEqual(p["coverage"], 19 / 20)
        self.assertEqual(rep["verdict"], "PASS")  # 0.95 is at the bar, not under it
        self.assertEqual(rep["hours_below_bar"], [scan.hour_stamp(H17)])
        # Reverse: fast has 20 rows in window (9 + 1 extra + 10), 19 are on Oracle.
        self.assertEqual(p["fast_rows"], 20)
        self.assertEqual(p["fast_only"], 1)
        self.assertAlmostEqual(p["reverse_coverage"], 19 / 20)

    def test_verdict_fails_below_bar_and_flags_short_evidence(self) -> None:
        oracle = [row(f"s{i}", H17 + 1000 * (i + 1)) for i in range(100)]
        fast = [row(f"s{i}", H17 + 1000 * (i + 1)) for i in range(94)]
        f, o = sides(fast, oracle, H17, H18)
        rep = tc.compare(f, o, H17, H18)
        self.assertEqual(rep["verdict"], "FAIL")
        self.assertEqual(rep["pooled"]["matched"], 94)
        self.assertFalse(rep["evidence_sufficient"])  # one hour only

    def test_two_complete_hours_make_evidence_sufficient(self) -> None:
        oracle = [row("a", H17 + 5), row("b", H18 + 5)]
        fast = [row("a", H17 + 5), row("b", H18 + 5)]
        f, o = sides(fast, oracle, H17, H19)
        rep = tc.compare(f, o, H17, H19)
        self.assertEqual(rep["complete_hours"], 2)
        self.assertTrue(rep["evidence_sufficient"])

    def test_listener_start_inside_window_warns_and_is_not_complete(self) -> None:
        oracle = [row(f"s{i}", H17 + 60_000 * i) for i in range(1, 30)]
        fast = [row(f"s{i}", H17 + 60_000 * i) for i in range(15, 30)]
        f, o = sides(fast, oracle, H17, H18)
        rep = tc.compare(f, o, H17, H18)
        self.assertEqual(rep["complete_hours"], 0)
        self.assertTrue(any("first row" in w for w in rep["warnings"]))
        self.assertEqual(rep["verdict"], "FAIL")

    def test_margin_rows_match_across_the_window_edge(self) -> None:
        # Oracle stamps the trade just before the edge, fast just after it. Both are in the scan margin.
        oracle = [row("edge", H18 - 50), row("x", H17 + 10)]
        fast = [row("edge", H18 + 30), row("x", H17 + 10)]
        f, o = sides(fast, oracle, H17, H18)
        rep = tc.compare(f, o, H17, H18)
        self.assertEqual(rep["pooled"]["oracle_rows"], 2)
        self.assertEqual(rep["pooled"]["matched"], 2)
        self.assertEqual(rep["pooled"]["fast_rows"], 1)  # the fast stamp is outside the window

    def test_empty_inputs(self) -> None:
        f, o = sides([], [], H17, H18)
        rep = tc.compare(f, o, H17, H18)
        self.assertEqual(rep["verdict"], "NO_DATA")
        self.assertIsNone(rep["pooled"]["coverage"])


class LagTests(unittest.TestCase):
    def test_percentiles_sign_and_outliers(self) -> None:
        deltas = [-100, -80, -60, -40, -20, 0, 20, 40, 60, 80, 100]
        oracle = [row(f"s{i}", H17 + 1000 * (i + 1)) for i in range(len(deltas) + 1)]
        fast = [row(f"s{i}", H17 + 1000 * (i + 1) + d) for i, d in enumerate(deltas)]
        fast.append(row(f"s{len(deltas)}", H17 + 1000 * (len(deltas) + 1) + 9_000))  # 9 s outlier
        f, o = sides(fast, oracle, H17, H18)
        lag = tc.compare(f, o, H17, H18)["pooled"]["lag_ms_fast_minus_oracle"]
        inside = lag["within_5s"]
        self.assertEqual(inside["n"], 11)
        self.assertEqual(inside["p50"], 0.0)
        self.assertEqual(inside["p10"], -80.0)  # linear percentile of the 11 values
        self.assertEqual(inside["p90"], 80.0)
        self.assertEqual(lag["outliers_over_5s"], 1)
        self.assertEqual(lag["all"]["n"], 12)
        self.assertEqual(lag["fast_first_share"], 5 / 12)

    def test_fast_minus_oracle_is_negative_when_fast_is_first(self) -> None:
        f, o = sides([row("a", H17 + 900)], [row("a", H17 + 1000)], H17, H18)
        self.assertEqual(tc.compare(f, o, H17, H18)["pooled"]["lag_ms_fast_minus_oracle"]["all"]["p50"], -100.0)


class VenueAndSchemaTests(unittest.TestCase):
    def test_venue_split_and_name_mapping(self) -> None:
        self.assertEqual(tc.classify_venue("pump_bonding"), "bonding")
        self.assertEqual(tc.classify_venue("pumpswap"), "pumpswap")
        self.assertEqual(tc.classify_venue("weird"), "other")
        oracle = [row("a", H17 + 1, venue="pump_bonding"), row("b", H17 + 2, venue="pumpswap"), row("c", H17 + 3, venue="pumpswap")]
        fast = [row("a", H17 + 1, venue="pump_bonding"), row("b", H17 + 2, venue="pump_swap")]
        f, o = sides(fast, oracle, H17, H18)
        rep = tc.compare(f, o, H17, H18)
        self.assertEqual(rep["venues"]["bonding"]["coverage"], 1.0)
        self.assertEqual(rep["venues"]["pumpswap"]["oracle_rows"], 2)
        self.assertEqual(rep["venues"]["pumpswap"]["matched"], 1)
        self.assertEqual(rep["matched_venue_class_disagreements"], 0)

    def test_field_name_and_type_differences_on_matched_rows(self) -> None:
        oracle = [row("a", H17 + 1, quote_is_wsol=True, event_ts=5)]
        fast = [row("a", H17 + 1, feed="public_rpc_logs", quote_is_wsol=True, event_ts=5.0)]
        f, o = sides(fast, oracle, H17, H18)
        pairs = tc.compare(f, o, H17, H18)["schema_pairs_on_matched_rows"]
        self.assertEqual(len(pairs), 1)
        pr = pairs[0]
        self.assertFalse(pr["identical"])
        self.assertEqual(pr["only_fast"], ["feed"])
        self.assertEqual(pr["only_oracle"], [])
        self.assertEqual(pr["type_differs"], {"event_ts": {"fast": "float", "oracle": "int"}})

    def test_identical_schema(self) -> None:
        f, o = sides([row("a", H17 + 1)], [row("a", H17 + 1)], H17, H18)
        pairs = tc.compare(f, o, H17, H18)["schema_pairs_on_matched_rows"]
        self.assertTrue(pairs[0]["identical"])


class StreamingTests(unittest.TestCase):
    def test_scan_output_carries_no_row_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            write_hour(Path(tmp), H17, [row("SIG1", H17 + 5, 2), row("SIG2", H17 + 6)])
            out = io.StringIO()
            footer = scan.write_stream(Path(tmp), H17, H18, out)
        text = out.getvalue()
        self.assertNotIn("MINT_SECRET", text)
        self.assertNotIn("WALLET_SECRET", text)
        self.assertNotIn("123456789", text)
        self.assertIn(f"SIG1 2 {H17 + 5} pump_bonding 0", text)
        self.assertEqual(footer["rows_out"], 2)
        self.assertTrue(text.rstrip().splitlines()[-1].startswith("#done "))

    def test_truncated_stream_is_an_error(self) -> None:
        with self.assertRaises(RuntimeError):
            tc.load_stream(iter([f"SIG 0 {H17} pumpswap 0\n"]), "oracle")

    def test_half_written_last_line_is_counted_not_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_hour(Path(tmp), H17, [row("A", H17 + 5)])
            with open(path, "a", encoding="utf-8") as fh:
                fh.write('{"signature": "B", "t_recv_ms": 17')
            out = io.StringIO()
            footer = scan.write_stream(Path(tmp), H17, H18, out)
        self.assertEqual(footer["rows_out"], 1)
        self.assertEqual(footer["bad_lines"], 1)

    @unittest.skipUnless(shutil.which("zstd"), "zstd binary not installed")
    def test_sealed_zst_hour_is_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            write_hour(Path(tmp), H17, [row("A", H17 + 5), row("B", H17 + 6)], zst=True)
            rows = list(scan.scan_rows(Path(tmp), H17, H18, scan.SchemaTable(), {}))
        self.assertEqual([r[0] for r in rows], ["A", "B"])

    def test_missing_hour_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = io.StringIO()
            footer = scan.write_stream(Path(tmp), H17, H19, out)
        self.assertEqual(footer["missing"], [scan.hour_stamp(H17), scan.hour_stamp(H18)])

    def test_scan_is_lazy_and_memory_is_bounded(self) -> None:
        n = 60_000
        with tempfile.TemporaryDirectory() as tmp:
            body = "".join(json.dumps(row(f"sig{i:08d}" + "x" * 80, H17 + i)) + "\n" for i in range(n))
            Path(tmp, f"trades-{scan.hour_stamp(H17)}.jsonl").write_text(body, encoding="utf-8")
            file_bytes = len(body)
            gen = scan.scan_rows(Path(tmp), H17, H18, scan.SchemaTable(), {})
            self.assertTrue(hasattr(gen, "__next__"))  # a generator, not a list
            tracemalloc.start()
            count = sum(1 for _ in gen)  # consume without retaining
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
        self.assertEqual(count, n)
        self.assertLess(peak, 300_000)  # a few hundred KB, nothing like the file
        self.assertGreater(file_bytes, 10_000_000)

    def test_side_stores_compact_columns_not_strings(self) -> None:
        n = 50_000
        side = tc.Side("t")
        tracemalloc.start()
        before, _ = tracemalloc.get_traced_memory()
        for i in range(n):
            side.add(f"sig{i:08d}" + "x" * 80, 0, H17 + i, "pumpswap", 0)
        after, _ = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        per_row = (after - before) / n
        self.assertLess(per_row, 40)  # 8+8+8+1+1 bytes of columns plus array slack

    def test_remote_path_uses_nice_python_stdin_and_no_shell(self) -> None:
        captured: dict = {}

        class FakeProc:
            def __init__(self, cmd, **kw):
                captured["cmd"] = cmd
                captured["stdin_name"] = getattr(kw["stdin"], "name", "")
                self.stdout = io.StringIO(
                    f"SIG 0 {H17 + 3} pumpswap 0\n#schema 0 " + json.dumps({"signature": "str"}) + "\n#done {}\n"
                )
                self.stderr = io.StringIO("")

            def wait(self):
                return 0

        side = tc.load_remote("mal-core-0", "/var/lib/mal/sealed/trades", H17, H18, "oracle", runner=FakeProc)
        self.assertEqual(len(side), 1)
        cmd = captured["cmd"]
        self.assertEqual(cmd[:2], ["ssh", "mal-core-0"])
        self.assertIn("nice", cmd)
        self.assertEqual(cmd[cmd.index("python3") + 1], "-")
        self.assertTrue(captured["stdin_name"].endswith("tape_coverage_scan.py"))

    def test_remote_nonzero_exit_raises(self) -> None:
        class BadProc:
            def __init__(self, cmd, **kw):
                self.stdout = io.StringIO("#done {}\n")
                self.stderr = io.StringIO("Permission denied")

            def wait(self):
                return 255

        with self.assertRaises(RuntimeError):
            tc.load_remote("mal-core-0", "/x", H17, H18, "oracle", runner=BadProc)


class CliTests(unittest.TestCase):
    def test_parse_instant(self) -> None:
        self.assertEqual(tc.parse_instant("2026-10-01T17"), H17)
        self.assertEqual(tc.parse_instant("2026-10-01T17:00:00Z"), H17)
        self.assertEqual(tc.parse_instant("2026-10-01T17:30"), H17 + 1_800_000)
        with self.assertRaises(ValueError):
            tc.parse_instant("yesterday")

    def test_end_to_end_with_local_stand_in_for_oracle(self) -> None:
        oracle = [row(f"s{i}", H17 + 1000 * (i + 1)) for i in range(20)]
        fast = [row(f"s{i}", H17 + 1000 * (i + 1) - 25) for i in range(20)]
        with tempfile.TemporaryDirectory() as tmp:
            fdir, odir = Path(tmp, "f"), Path(tmp, "o")
            fdir.mkdir()
            odir.mkdir()
            write_hour(fdir, H17, fast)
            write_hour(odir, H17, oracle)
            jpath, mpath = Path(tmp, "r.json"), Path(tmp, "r.md")

            def fake_remote(host, directory, lo, hi, name, runner=None):
                return tc.load_local(odir, lo, hi, name)

            with mock.patch.object(tc, "load_remote", fake_remote), mock.patch("builtins.print"):
                rc = tc.main(
                    ["--start", "2026-10-01T17", "--end", "2026-10-01T18", "--fast-dir", str(fdir),
                     "--json-out", str(jpath), "--md-out", str(mpath)]
                )
            rep = json.loads(jpath.read_text())
            md = mpath.read_text()
        self.assertEqual(rc, 0)
        self.assertEqual(rep["verdict"], "PASS")
        self.assertEqual(rep["pooled"]["coverage"], 1.0)
        self.assertEqual(rep["pooled"]["lag_ms_fast_minus_oracle"]["all"]["p50"], -25.0)
        self.assertIn("Verdict against the 95% bar", md)
        self.assertIn("Negative = fast host first", md)
        self.assertNotIn("MINT_SECRET", md + json.dumps(rep))


def chain_row(sig: str, block_time_s: int, idx: int = 0, venue: str = "pump_bonding", **extra) -> dict:
    """A pump_history_backfill trades row: block_time in seconds, null receive stamps."""
    out = {
        "v": 1,
        "venue": venue,
        "mint": "MINT_SECRET",
        "trader": "WALLET_SECRET",
        "sol_lamports": 123456789,
        "signature": sig,
        "event_index": idx,
        "source": "backfill_getblock",
        "block_time": block_time_s,
        "t_recv": None,
        "t_recv_ms": None,
    }
    out.update(extra)
    return out


def write_chain(root: Path, rows: list[dict], *, subdir: str = "trades", zst: bool = False) -> None:
    """Write chain hour files by block_time hour, under root/<subdir>/ like a backfill output dir."""
    target = root / subdir if subdir else root
    target.mkdir(parents=True, exist_ok=True)
    by_hour: dict[int, list[dict]] = {}
    for r in rows:
        ms = r["block_time"] * 1000
        by_hour.setdefault(ms - ms % tc.HOUR_MS, []).append(r)
    for h, rs in by_hour.items():
        write_hour(target, h, rs, zst=zst)


def sides3(fast_rows, oracle_rows, chain_rows, lo: int, hi: int, *, margin: int = 10_000, **chain_kw):
    with tempfile.TemporaryDirectory() as tmp:
        fdir, odir, cdir = Path(tmp, "f"), Path(tmp, "o"), Path(tmp, "c")
        fdir.mkdir()
        odir.mkdir()
        for d, rows in ((fdir, fast_rows), (odir, oracle_rows)):
            by_hour: dict[int, list[dict]] = {}
            for r in rows:
                by_hour.setdefault(r["t_recv_ms"] - r["t_recv_ms"] % tc.HOUR_MS, []).append(r)
            for h, rs in by_hour.items():
                write_hour(d, h, rs)
        write_chain(cdir, chain_rows, **chain_kw)
        fast = tc.load_local(fdir, lo - margin, hi + margin, "fast")
        oracle = tc.load_local(odir, lo - margin, hi + margin, "oracle")
        chain = tc.load_local(cdir, lo - margin, hi + margin, "chain", source="chain")
    return fast, oracle, chain


S17 = H17 // 1000  # block_time seconds at the start of each hour
S18 = H18 // 1000


def three_way_data():
    """22 chain rows. Fast has 20 of them plus 2 not on chain; Oracle has 17 plus 1 not on chain."""
    chain, fast, oracle = [], [], []
    for i in range(10):
        ven = "pump_bonding" if i % 2 == 0 else "pumpswap"
        chain.append(chain_row(f"c17-{i}", S17 + i + 1, venue=ven))
        chain.append(chain_row(f"c18-{i}", S18 + i + 1))
        if i != 3:
            fast.append(row(f"c17-{i}", H17 + 1000 * (i + 1) + 300, venue=ven))
        if i <= 4:
            oracle.append(row(f"c17-{i}", H17 + 1000 * (i + 1) + 2000, venue=ven))
        fast.append(row(f"c18-{i}", H18 + 1000 * (i + 1) + 300))
        oracle.append(row(f"c18-{i}", H18 + 1000 * (i + 1) + 2000))
    chain += [chain_row("multi", S17 + 20, 0), chain_row("multi", S17 + 20, 1)]
    fast += [row("multi", H17 + 20_300, 0), row("multi", H17 + 20_300, 2)]  # idx 2: decode difference
    fast.append(row("ghost", H17 + 30_000, venue="pumpswap"))  # signature chain lacks
    fast.append(row("c17-0", H17 + 1_500))  # a duplicate of an earlier identity
    oracle += [row("multi", H17 + 22_000, 0), row("multi", H17 + 22_000, 1)]
    oracle.append(row("c18-0", H18 + 1_100, 1))  # same signature, event_index chain lacks
    return fast, oracle, chain


class ChainSourceTests(unittest.TestCase):
    def test_block_time_rows_become_ms_stamps_and_ignore_null_t_recv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            write_chain(Path(tmp), [chain_row("A", S17 + 5, 1, venue="pumpswap")], subdir="")
            rows = list(scan.scan_rows(Path(tmp), H17, H18, scan.SchemaTable(), {}, "block_time"))
        self.assertEqual(rows[0][:4], ("A", 1, (S17 + 5) * 1000, "pumpswap"))

    def test_window_uses_block_time_not_receive_time(self) -> None:
        rows = [
            chain_row("before", S17 - 1),
            chain_row("at_lo", S17),
            chain_row("inside", S17 + 3599),
            chain_row("at_hi", S18),
            # t_recv_ms inside the window must not rescue a block_time outside it
            chain_row("late", S18 + 7, t_recv_ms=H17 + 5),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            write_chain(Path(tmp), rows, subdir="")
            got = [r[0] for r in scan.scan_rows(Path(tmp), H17, H18, scan.SchemaTable(), {}, "block_time")]
        self.assertEqual(got, ["at_lo", "inside"])

    def test_row_without_block_time_is_a_bad_line_and_tape_mode_skips_chain_rows(self) -> None:
        bad = chain_row("noblock", 0)
        del bad["block_time"]
        with tempfile.TemporaryDirectory() as tmp:
            write_hour(Path(tmp), H17, [chain_row("ok", S17 + 1), bad])
            stats: dict = {}
            got = list(scan.scan_rows(Path(tmp), H17, H18, scan.SchemaTable(), stats, "block_time"))
            self.assertEqual(len(got), 1)
            self.assertEqual(stats["bad_lines"], 1)
            tape_stats: dict = {}
            self.assertEqual(list(scan.scan_rows(Path(tmp), H17, H18, scan.SchemaTable(), tape_stats)), [])
            self.assertEqual(tape_stats["bad_lines"], 2)  # t_recv_ms is null on every chain row

    def test_unknown_time_field_rejected(self) -> None:
        with self.assertRaises(ValueError):
            list(scan.scan_rows(Path("."), H17, H18, scan.SchemaTable(), {}, "slot"))

    def test_hour_files_found_in_root_or_trades_subdir_and_sealed(self) -> None:
        for subdir in ("", "trades"):
            with tempfile.TemporaryDirectory() as tmp:
                write_chain(Path(tmp), [chain_row("A", S17 + 1)], subdir=subdir)
                side = tc.load_local(Path(tmp), H17, H18, "chain", source="chain")
            self.assertEqual(len(side), 1, subdir)
        if shutil.which("zstd"):
            with tempfile.TemporaryDirectory() as tmp:
                write_chain(Path(tmp), [chain_row("A", S17 + 1)], zst=True)
                side = tc.load_local(Path(tmp), H17, H18, "chain", source="chain")
            self.assertEqual(len(side), 1)
            self.assertEqual(side.t[0], (S17 + 1) * 1000)

    def test_chain_side_is_marked_and_footer_names_the_time_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            write_chain(Path(tmp), [chain_row("A", S17 + 1)])
            side = tc.load_local(Path(tmp), H17, H18, "chain", source="chain")
            tape = tc.load_local(Path(tmp), H17, H18, "x")  # the same dir read as a tape
        self.assertEqual(side.source, "chain")
        self.assertEqual(side.footer["time_field"], "block_time")
        self.assertEqual(tape.source, "tape")
        self.assertEqual(len(tape), 0)

    def test_stream_stamped_by_the_wrong_field_is_refused(self) -> None:
        stream = [f"SIG 0 {H17 + 3} pumpswap 0\n", '#done {"time_field":"t_recv_ms"}\n']
        with self.assertRaises(RuntimeError):
            tc.load_stream(iter(stream), "chain", "chain")
        with self.assertRaises(RuntimeError):  # an old scan script with no time_field is a tape scan
            tc.load_stream(iter([f"SIG 0 {H17 + 3} pumpswap 0\n", "#done {}\n"]), "chain", "chain")
        self.assertEqual(len(tc.load_stream(iter(stream), "fast")), 1)

    def test_remote_chain_scan_asks_for_block_time(self) -> None:
        captured: dict = {}

        class FakeProc:
            def __init__(self, cmd, **kw):
                captured["cmd"] = cmd
                self.stdout = io.StringIO(f"SIG 0 {H17 + 3000} pumpswap 0\n" + '#done {"time_field":"block_time"}\n')
                self.stderr = io.StringIO("")

            def wait(self):
                return 0

        side = tc.load_remote(
            "mal-research-0", "/data/mal/blocks/truth-1001", H17, H18, "chain", runner=FakeProc, source="chain"
        )
        self.assertEqual(side.source, "chain")
        cmd = captured["cmd"]
        self.assertEqual(cmd[:2], ["ssh", "mal-research-0"])
        self.assertEqual(cmd[cmd.index("python3") + 1], "-")
        self.assertEqual(cmd[-4:], ["/data/mal/blocks/truth-1001", str(H17), str(H18), "block_time"])

    def test_no_lag_is_computed_against_chain(self) -> None:
        fast, oracle, chain = sides3([row("a", H17 + 1300)], [], [chain_row("a", S17 + 1)], H17, H18)
        rep = tc.compare(fast, chain, H17, H18)
        self.assertEqual(rep["pooled"]["matched"], 1)
        self.assertIsNone(rep["pooled"]["lag_ms_fast_minus_oracle"])
        self.assertIsNone(rep["hours"][0]["lag_ms_fast_minus_oracle"])
        self.assertTrue(all(v["lag_ms_fast_minus_oracle"] is None for v in rep["venues"].values()))
        self.assertEqual(rep["schema_pairs_on_matched_rows"], [])
        # the tape-vs-tape comparison still has lag
        self.assertIsNotNone(tc.compare(fast, fast, H17, H18)["pooled"]["lag_ms_fast_minus_oracle"])

    def test_unsealed_chain_hour_is_warned(self) -> None:
        fast, oracle, chain = sides3([row("a", H17 + 1300)], [], [chain_row("a", S17 + 1)], H17, H18)
        rep = tc.compare_chain(fast, oracle, chain, H17, H18)
        self.assertTrue(any("not zstd-sealed" in w for w in rep["warnings"]))


class ThreeWayCoverageTests(unittest.TestCase):
    def setUp(self) -> None:
        fast_rows, oracle_rows, chain_rows = three_way_data()
        f, o, c = sides3(fast_rows, oracle_rows, chain_rows, H17, H19)
        self.f, self.o, self.c = f, o, c
        self.ch = tc.compare_chain(f, o, c, H17, H19)
        self.fr, self.orr = self.ch["fast_vs_chain"], self.ch["oracle_vs_chain"]

    def test_pooled_coverage_of_each_tape_against_chain(self) -> None:
        fp, op = self.fr["pooled"], self.orr["pooled"]
        self.assertEqual(fp["oracle_rows"], 22)  # chain rows (the reference)
        self.assertEqual(fp["matched"], 20)
        self.assertAlmostEqual(fp["coverage"], 20 / 22)
        self.assertEqual(op["oracle_rows"], 22)
        self.assertEqual(op["matched"], 17)
        self.assertAlmostEqual(op["coverage"], 17 / 22)

    def test_per_hour(self) -> None:
        f17, f18 = self.fr["hours"]
        o17, o18 = self.orr["hours"]
        self.assertEqual((f17["oracle_rows"], f17["matched"]), (12, 10))  # 10 + multi:0, multi:1
        self.assertEqual((f18["oracle_rows"], f18["matched"]), (10, 10))
        self.assertEqual((o17["oracle_rows"], o17["matched"]), (12, 7))  # c17-0..4, multi:0, multi:1
        self.assertEqual((o18["oracle_rows"], o18["matched"]), (10, 10))
        self.assertAlmostEqual(f17["coverage"], 10 / 12)
        self.assertAlmostEqual(o17["coverage"], 7 / 12)

    def test_venue_split_is_by_chain_row_venue(self) -> None:
        fv, ov = self.fr["venues"], self.orr["venues"]
        self.assertEqual((fv["bonding"]["oracle_rows"], fv["bonding"]["matched"]), (17, 16))
        self.assertEqual((fv["pumpswap"]["oracle_rows"], fv["pumpswap"]["matched"]), (5, 4))
        self.assertEqual((ov["bonding"]["matched"], ov["pumpswap"]["matched"]), (15, 2))

    def test_tape_rows_not_in_chain(self) -> None:
        fp, op = self.fr["pooled"], self.orr["pooled"]
        # fast in window: 20 matched + multi:2 + ghost; the repeated c17-0 is dropped as a duplicate
        self.assertEqual((fp["fast_rows"], fp["reverse_matched"], fp["fast_only"]), (22, 20, 2))
        self.assertEqual((op["fast_rows"], op["reverse_matched"], op["fast_only"]), (18, 17, 1))
        self.assertEqual(self.fr["duplicates_dropped"]["fast_identity"], 1)
        # signature level separates decode differences (sig on chain) from rows chain lacks entirely
        self.assertEqual(fp["signature_level"]["fast_only"], 1)  # ghost
        self.assertEqual(op["signature_level"]["fast_only"], 0)  # c18-0:1 and multi share chain signatures
        self.assertEqual(self.fr["fast_venues"]["bonding"]["fast_only"], 1)  # multi:2
        self.assertEqual(self.fr["fast_venues"]["pumpswap"]["fast_only"], 1)  # ghost
        self.assertEqual(self.orr["fast_venues"]["bonding"]["fast_only"], 1)  # c18-0:1

    def test_per_hour_sums_equal_pooled(self) -> None:
        for rep in (self.fr, self.orr):
            self.assertEqual(sum(h["fast_only"] for h in rep["hours"]), rep["pooled"]["fast_only"])
            self.assertEqual(sum(h["matched"] for h in rep["hours"]), rep["pooled"]["matched"])

    def test_verdict_is_the_fast_vs_chain_one(self) -> None:
        self.assertEqual(self.ch["complete_hours"], 2)
        self.assertEqual(self.ch["bar_verdict"], "FAIL")  # 90.9% < 95%
        self.assertEqual(self.ch["verdict"], "FAIL")

    def test_existing_fast_vs_oracle_is_unchanged_by_the_pairwise_runs(self) -> None:
        before = tc.compare(self.f, self.o, H17, H19)
        tc.compare_chain(self.f, self.o, self.c, H17, H19)
        self.assertEqual(before, tc.compare(self.f, self.o, H17, H19))
        self.assertIsNotNone(before["pooled"]["lag_ms_fast_minus_oracle"])


class ChainVerdictTests(unittest.TestCase):
    def _run(self, n_hours: int, covered: float):
        chain, fast = [], []
        for h in range(n_hours):
            base = S17 + 3600 * h
            for i in range(100):
                chain.append(chain_row(f"s{h}-{i}", base + i + 1))
                if i < int(covered * 100):
                    fast.append(row(f"s{h}-{i}", (base + i + 1) * 1000 + 200))
        hi = H17 + n_hours * tc.HOUR_MS
        f, o, c = sides3(fast, [], chain, H17, hi)
        return tc.compare_chain(f, o, c, H17, hi)

    def test_pass_at_the_bar_with_two_complete_hours(self) -> None:
        ch = self._run(2, 0.95)
        self.assertEqual((ch["verdict"], ch["complete_hours"]), ("PASS", 2))
        self.assertTrue(ch["evidence_sufficient"])

    def test_fail_below_the_bar(self) -> None:
        self.assertEqual(self._run(2, 0.94)["verdict"], "FAIL")

    def test_inconclusive_with_fewer_than_two_complete_hours(self) -> None:
        ch = self._run(1, 1.0)
        self.assertEqual(ch["bar_verdict"], "PASS")
        self.assertEqual(ch["verdict"], "INCONCLUSIVE")
        self.assertFalse(ch["evidence_sufficient"])
        self.assertEqual(self._run(1, 0.5)["verdict"], "INCONCLUSIVE")  # a miss on one hour is not a FAIL either

    def test_no_chain_rows_is_no_data(self) -> None:
        f, o, c = sides3([row("a", H17 + 5)], [], [], H17, H19)
        self.assertEqual(tc.compare_chain(f, o, c, H17, H19)["verdict"], "NO_DATA")

    def test_compare_chain_needs_a_chain_side(self) -> None:
        f, o = sides([row("a", H17 + 5)], [row("a", H17 + 5)], H17, H18)
        with self.assertRaises(ValueError):
            tc.compare_chain(f, o, o, H17, H18)


class ChainCliTests(unittest.TestCase):
    def test_end_to_end_with_local_chain_dir(self) -> None:
        n = 20
        chain = [chain_row(f"s{i}", S17 + i + 1) for i in range(n)]
        fast = [row(f"s{i}", H17 + 1000 * (i + 1) + 150) for i in range(n)] + [row("extra", H17 + 9_000)]
        oracle = [row(f"s{i}", H17 + 1000 * (i + 1) + 400) for i in range(n - 2)]
        with tempfile.TemporaryDirectory() as tmp:
            fdir, odir, cdir = Path(tmp, "f"), Path(tmp, "o"), Path(tmp, "c")
            fdir.mkdir()
            odir.mkdir()
            write_hour(fdir, H17, fast)
            write_hour(odir, H17, oracle)
            write_chain(cdir, chain)
            jpath, mpath = Path(tmp, "r.json"), Path(tmp, "r.md")

            def fake_remote(host, directory, lo, hi, name, runner=None, source="tape"):
                return tc.load_local(odir, lo, hi, name, source)

            argv = ["--start", "2026-10-01T17", "--end", "2026-10-01T18", "--fast-dir", str(fdir),
                    "--chain-dir", str(cdir), "--json-out", str(jpath), "--md-out", str(mpath)]
            with mock.patch.object(tc, "load_remote", fake_remote), mock.patch("builtins.print"):
                rc = tc.main(argv)
            rep = json.loads(jpath.read_text())
            md = mpath.read_text()
        ch = rep["chain_truth"]
        self.assertEqual(ch["verdict"], "INCONCLUSIVE")  # one hour
        self.assertEqual(rc, 1)  # only a PASS exits 0
        self.assertEqual(ch["fast_vs_chain"]["pooled"]["coverage"], 1.0)
        self.assertAlmostEqual(ch["oracle_vs_chain"]["pooled"]["coverage"], 18 / 20)
        self.assertEqual(ch["fast_vs_chain"]["pooled"]["fast_only"], 1)
        self.assertIn("Tape coverage against chain truth", md)
        self.assertIn("no lag is computed against chain", md)
        self.assertIn("Tape rows chain does not have", md)
        self.assertIn("INCONCLUSIVE", md)
        # the existing fast-vs-Oracle report is still there, with its lag
        self.assertIn("Fast trade tape coverage against Oracle", md)
        self.assertEqual(rep["pooled"]["lag_ms_fast_minus_oracle"]["all"]["n"], 18)
        blob = md + json.dumps(rep)
        self.assertNotIn("MINT_SECRET", blob)
        self.assertNotIn("WALLET_SECRET", blob)

    def test_without_chain_dir_the_report_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fdir, odir = Path(tmp, "f"), Path(tmp, "o")
            fdir.mkdir()
            odir.mkdir()
            write_hour(fdir, H17, [row("a", H17 + 5)])
            write_hour(odir, H17, [row("a", H17 + 5)])
            jpath = Path(tmp, "r.json")

            def fake_remote(host, directory, lo, hi, name, runner=None):
                return tc.load_local(odir, lo, hi, name)

            with mock.patch.object(tc, "load_remote", fake_remote), mock.patch("builtins.print") as pr:
                tc.main(["--start", "2026-10-01T17", "--end", "2026-10-01T18", "--fast-dir", str(fdir),
                         "--json-out", str(jpath)])
            rep = json.loads(jpath.read_text())
        self.assertNotIn("chain_truth", rep)
        self.assertNotIn("chain truth", str(pr.call_args))

    def test_exit_code_zero_on_chain_pass_over_two_hours(self) -> None:
        chain = [chain_row(f"s{i}", S17 + 1 + i * 100) for i in range(36)]
        chain += [chain_row(f"t{i}", S18 + 1 + i * 100) for i in range(36)]
        fast = [row(r["signature"], r["block_time"] * 1000 + 200) for r in chain]
        with tempfile.TemporaryDirectory() as tmp:
            fdir, odir, cdir = Path(tmp, "f"), Path(tmp, "o"), Path(tmp, "c")
            fdir.mkdir()
            odir.mkdir()
            write_hour(fdir, H17, [r for r in fast if r["t_recv_ms"] < H18])
            write_hour(fdir, H18, [r for r in fast if r["t_recv_ms"] >= H18])
            write_hour(odir, H17, [])
            write_chain(cdir, chain)

            def fake_remote(host, directory, lo, hi, name, runner=None, source="tape"):
                return tc.load_local(odir, lo, hi, name, source)

            with mock.patch.object(tc, "load_remote", fake_remote), mock.patch("builtins.print"):
                rc = tc.main(["--start", "2026-10-01T17", "--end", "2026-10-01T19", "--fast-dir", str(fdir),
                              "--chain-dir", str(cdir)])
        self.assertEqual(rc, 0)

    def test_chain_ssh_routes_through_load_remote_with_chain_source(self) -> None:
        seen = []

        def fake_remote(host, directory, lo, hi, name, runner=None, source="tape"):
            seen.append((host, directory, name, source))
            if source == "chain":
                return tc.Side(name, source)
            return tc.load_stream(iter(['#done {}\n']), name)

        with tempfile.TemporaryDirectory() as tmp:
            fdir = Path(tmp, "f")
            fdir.mkdir()
            write_hour(fdir, H17, [row("a", H17 + 5)])
            with mock.patch.object(tc, "load_remote", fake_remote), mock.patch("builtins.print"):
                tc.main(["--start", "2026-10-01T17", "--end", "2026-10-01T18", "--fast-dir", str(fdir),
                         "--chain-dir", "/data/mal/blocks/truth-1001", "--chain-ssh", "mal-research-0"])
        self.assertIn(("mal-research-0", "/data/mal/blocks/truth-1001", "chain", "chain"), seen)


if __name__ == "__main__":
    unittest.main()
