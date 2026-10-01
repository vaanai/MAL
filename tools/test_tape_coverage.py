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


if __name__ == "__main__":
    unittest.main()
