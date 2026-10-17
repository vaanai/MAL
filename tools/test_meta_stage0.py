"""Tests for tools/meta_stage0.py. Fakes for RPC and HTTP; no network."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tools.meta_stage0 as m
from tools import ipfs_fetch as ipf
from observe.trade_decode import b58encode
from tools.pump_history_backfill import CreditBudget, CreditBudgetExceeded

URI = "ipfs://QmFakeCid"
IMG = "ipfs://QmFakeImg"
KEY = "SUPERSECRETKEY123456"


def _s(text: str) -> bytes:
    raw = text.encode()
    return len(raw).to_bytes(4, "little") + raw


def create_blob(mint_b: bytes, uri: str = URI) -> bytes:
    return b"".join(
        [
            bytes.fromhex("1b72a94ddeeb6376"), _s("Name"), _s("SYM"), _s(uri),
            mint_b, bytes([9] * 32), bytes(range(32, 64)), bytes([7] * 32),
            (1790318932).to_bytes(8, "little", signed=True),
            (1_000_000).to_bytes(8, "little"), (30_000_000_000).to_bytes(8, "little"),
            (800_000).to_bytes(8, "little"), (10**15).to_bytes(8, "little"),
        ]
    )


MINT_B = bytes(range(32))
MINT = b58encode(MINT_B)


def tx_with_log(uri: str = URI) -> dict:
    blob = base64.b64encode(create_blob(MINT_B, uri)).decode()
    return {"meta": {"logMessages": ["Program log: x", f"Program data: {blob}"]}, "transaction": {}}


def _ts(text: str) -> int:
    from datetime import datetime, timezone

    return int(datetime.strptime(text, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp())


T_OK = _ts("2026-08-15T00")


def row(i: int, grp: str = "G", block: str = "b1") -> dict:
    return {"mint": f"M{i}", "grp": grp, "block": block, "signature": f"S{i}",
            "block_time": T_OK, "create_ms": T_OK * 1000}


class FakeHttp:
    def __init__(self, table=None, default=(200, b"{}")):
        self.table, self.default, self.calls = table or {}, default, []

    def __call__(self, url, timeout, max_bytes, ranged):
        self.calls.append((url, timeout, max_bytes, ranged))
        v = self.table.get(url, self.default)
        if isinstance(v, Exception):
            raise v
        return v


GW = ["https://gw1.invalid/ipfs/", "https://gw2.invalid/ipfs/"]
JSON_URL = "https://gw1.invalid/ipfs/QmFakeCid"
IMG_URL = "https://gw1.invalid/ipfs/QmFakeImg"


class SampleTests(unittest.TestCase):
    def _fixture(self, d: str):
        import duckdb

        tp, cdir = Path(d) / "t.parquet", Path(d) / "creates"
        cdir.mkdir()
        con = duckdb.connect()
        con.execute("create table t(mint varchar, has_create boolean, is_mayhem_mode boolean,"
                    " grad_ms bigint, block varchar, create_ms bigint)")
        toks = []
        for i in range(20):
            grad = 1 if i % 2 == 0 else None
            toks.append((f"m{i}", True, False, grad, "blk", T_OK * 1000 + i))
        toks += [
            ("mayhem", True, True, None, "blk", T_OK * 1000),
            ("nocreate", False, False, None, "blk", T_OK * 1000),
            ("gap_hour", True, False, None, "blk", _ts("2026-09-01T00") * 1000),  # between segments
            ("oct", True, False, None, "blk", _ts("2026-10-02T00") * 1000),
        ]
        con.executemany("insert into t values (?,?,?,?,?,?)", toks)
        con.execute(f"copy t to '{tp}' (format parquet)")

        def write(hour: str, rows):
            con.execute("create or replace table c(mint varchar, signature varchar, slot bigint, block_time bigint)")
            con.executemany("insert into c values (?,?,?,?)", rows)
            con.execute(f"copy c to '{cdir}/{hour}.parquet' (format parquet)")

        write("2026-08-15T00", [(t[0], "sig_" + t[0], 5, T_OK) for t in toks[:20] if t[0] != "m3"])
        # files outside the allowed hours: must never be read (garbage content would break the query)
        for h in ("2026-09-01T00", "2026-10-02T00"):
            (cdir / f"{h}.parquet").write_bytes(b"not a parquet file")
        return str(tp), str(cdir), toks

    def test_deterministic_order_and_filters(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            tp, cdir, toks = self._fixture(d)
            a = m.build_sample(3, tp, cdir)
            self.assertEqual(a, m.build_sample(3, tp, cdir))
            self.assertEqual(sum(r["grp"] == "G" for r in a), 3)
            self.assertEqual(sum(r["grp"] == "N" for r in a), 3)
            self.assertFalse({r["mint"] for r in a} & {"mayhem", "nocreate", "m3", "gap_hour", "oct"})
            for g in "GN":
                pool = [t[0] for t in toks[:20] if t[0] != "m3" and ((t[3] is not None) == (g == "G"))]
                want = sorted(pool, key=lambda x: (hashlib.sha256((m.HASH_SALT + x).encode()).hexdigest(), x))[:3]
                self.assertEqual([r["mint"] for r in a if r["grp"] == g], want)

    def test_salt_pinned(self) -> None:
        self.assertEqual(m.HASH_SALT, "MAL-STAGE0-2026-10-17")
        self.assertEqual(m.MAX_CALLS_DEFAULT, 3000)
        self.assertEqual(
            m.DEFAULT_GATEWAYS,
            ("https://gateway.pinata.cloud/ipfs/", "https://ipfs.io/ipfs/", "https://dweb.link/ipfs/"),
        )

    def test_pump_mypinata_rewritten(self) -> None:
        self.assertEqual(
            m.candidate_urls("https://pump.mypinata.cloud/ipfs/QmX", m.DEFAULT_GATEWAYS),
            [g + "QmX" for g in m.DEFAULT_GATEWAYS],
        )

    def test_hour_filter_builds_only_allowed_paths(self) -> None:
        hours = m.allowed_hours()
        self.assertEqual(len(hours), 14 * 24 + 12 * 24 + 152)
        self.assertEqual(hours[0], "2026-08-14T12")
        self.assertIn("2026-08-28T11", hours)
        self.assertNotIn("2026-08-28T12", hours)
        self.assertNotIn("2026-09-03T11", hours)
        self.assertIn("2026-09-18T23", hours)
        self.assertEqual(hours[-1], "2026-09-25T06")
        self.assertFalse([h for h in hours if h.startswith("2026-10")])
        with tempfile.TemporaryDirectory() as d:
            for h in ("2026-08-14T12", "2026-08-28T12", "2026-10-01T00"):
                (Path(d) / f"{h}.parquet").write_bytes(b"x")
            self.assertEqual(m.allowed_create_files(d), [f"{d}/2026-08-14T12.parquet"])

    def test_refuses_october_and_out_of_range(self) -> None:
        oct_row = dict(row(1), block_time=_ts("2026-10-01T00"), create_ms=_ts("2026-10-01T00") * 1000)
        with self.assertRaises(m.RefusedOutOfRange):
            m.refuse_out_of_range([row(0), oct_row])
        gap = dict(row(2), block_time=_ts("2026-09-01T00"))
        with self.assertRaises(m.RefusedOutOfRange):
            m.refuse_out_of_range([gap])
        with self.assertRaises(m.RefusedOutOfRange):
            m.refuse_out_of_range([dict(row(3), block_time=None)])
        m.refuse_out_of_range([row(0)])
        # run() refuses before any rpc call
        calls = []
        with tempfile.TemporaryDirectory() as d, self.assertRaises(m.RefusedOutOfRange):
            m.run([oct_row], Path(d), lambda s: calls.append(s), FakeHttp(), GW, CreditBudget(5, 1))
        self.assertEqual(calls, [])

    def test_write_sample_sha(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            rows = [dict(row(1), create_ms=1, slot=2)]
            digest = m.write_sample(rows, Path(d))
            self.assertEqual(digest, hashlib.sha256((Path(d) / "sample.parquet").read_bytes()).hexdigest())
            self.assertIn(digest, (Path(d) / "sample.parquet.sha256").read_text())


class DecodeTests(unittest.TestCase):
    def test_uri_from_log(self) -> None:
        self.assertEqual(m.uri_from_tx(tx_with_log(), MINT), URI)

    def test_wrong_mint_and_none(self) -> None:
        self.assertIsNone(m.uri_from_tx(tx_with_log(), "OtherMint"))
        self.assertIsNone(m.uri_from_tx({"meta": {"logMessages": []}}, MINT))


class StatusTests(unittest.TestCase):
    def check(self, tx, http, r=None):
        r = r or dict(row(1), mint=MINT)
        return m.check_mint(r, lambda s: tx, http, GW)

    def test_full_ok(self) -> None:
        meta = {"description": "hi", "twitter": "x", "image": IMG}
        http = FakeHttp({JSON_URL: (200, json.dumps(meta).encode()), IMG_URL: (206, b"png")})
        st = self.check(tx_with_log(), http)
        self.assertTrue(all(st[k] for k in ("tx_ok", "uri_found", "json_ok", "has_description", "has_any_social", "image_ok")))
        self.assertIsNone(st["error"])
        self.assertNotIn("hi", json.dumps(st))  # no description text stored
        img_call = [c for c in http.calls if c[0] == IMG_URL][0]
        self.assertEqual((img_call[1], img_call[2], img_call[3]), (10.0, 65536, True))

    def test_tx_missing_and_uri_missing(self) -> None:
        self.assertEqual(self.check(None, FakeHttp())["error"], "tx_missing")
        st = self.check({"meta": {"logMessages": []}}, FakeHttp())
        self.assertEqual((st["tx_ok"], st["uri_found"], st["error"]), (True, False, "uri_not_found"))

    def test_json_failures(self) -> None:
        st = self.check(tx_with_log(), FakeHttp(default=(404, b"")))
        self.assertEqual(st["error"], "json_http_404")
        self.assertFalse(st["json_ok"])
        http = FakeHttp(default=(200, b"<html>"))
        self.assertEqual(self.check(tx_with_log(), http)["error"], "json_parse")
        self.assertEqual(self.check(tx_with_log(), FakeHttp(default=TimeoutError()))["error"], "json_timeout")

    def test_three_attempts_rotate_gateways(self) -> None:
        http = FakeHttp(default=(503, b""))
        self.check(tx_with_log(), http)
        self.assertEqual(len(http.calls), 3)
        self.assertEqual(http.calls[0][0].split("/")[2], "gw1.invalid")
        self.assertEqual(http.calls[1][0].split("/")[2], "gw2.invalid")

    def test_no_social_no_image(self) -> None:
        http = FakeHttp({JSON_URL: (200, b'{"description": "  ", "twitter": ""}')})
        st = self.check(tx_with_log(), http)
        self.assertTrue(st["json_ok"])
        self.assertFalse(st["has_description"] or st["has_any_social"])
        self.assertEqual(st["error"], "image_missing")

    def test_image_fail(self) -> None:
        http = FakeHttp({JSON_URL: (200, json.dumps({"image": IMG}).encode()), IMG_URL: (404, b"")}, default=(404, b""))
        st = self.check(tx_with_log(), http)
        self.assertTrue(st["json_ok"])
        self.assertFalse(st["image_ok"])
        self.assertEqual(st["error"], "image_http_404")

    def test_non_ipfs_uri_refused_without_fetch(self) -> None:
        for bad in ("http://169.254.169.254/latest", "https://example.com/meta.json",
                    "https://ipfs.io/ipfs/../../etc", "ipfs://", "file:///etc/passwd"):
            http = FakeHttp()
            st = self.check(tx_with_log(bad), http)
            self.assertEqual(st["error"], "json_non_ipfs_uri", bad)
            self.assertEqual(http.calls, [], bad)
        # a non-ipfs image url is refused too
        http = FakeHttp({JSON_URL: (200, b'{"image": "http://127.0.0.1/x.png"}')})
        st = self.check(tx_with_log(), http)
        self.assertEqual(st["error"], "image_non_ipfs_uri")
        self.assertEqual(len(http.calls), 1)

    def test_known_gateway_url_rewritten_to_pinned(self) -> None:
        http = FakeHttp({JSON_URL: (200, b"{}")})
        self.check(tx_with_log("https://evil.example/ipfs/QmFakeCid"), http)
        self.assertEqual(http.calls[0][0], JSON_URL)

    def test_no_gateway_or_keep_json_flags(self) -> None:
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                m.main(["--out", "/nonexistent", "--gateway", "https://x"])
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                m.main(["--out", "/nonexistent", "--keep-json"])


class NoRedirectTests(unittest.TestCase):
    def test_redirect_refused(self) -> None:
        import http.server
        import threading

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:9/")
                self.end_headers()

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            status, body = m.urllib_get(f"http://127.0.0.1:{srv.server_port}/", 5, 100, False,
                                        allow_private_hosts=frozenset({"127.0.0.1"}))
        finally:
            srv.shutdown()
        self.assertEqual((status, body), (302, b""))

    def test_read_deadline(self) -> None:
        class Slow:
            def read(self, n):
                return b"x"

        t = iter([0.0, 1.0, 2.0, 99.0, 100.0])
        with self.assertRaises(TimeoutError):
            m.read_with_deadline(Slow(), 10**6, 50.0, clock=lambda: next(t))


class BudgetAndDeadlineTests(unittest.TestCase):
    def test_cumulative_cap_across_runs(self) -> None:
        http = FakeHttp({JSON_URL: (200, b"{}")})
        sample = [row(i) for i in range(10)]
        with patch.object(m, "uri_from_tx", lambda tx, mint=None: URI), tempfile.TemporaryDirectory() as d:
            out = Path(d)
            total = 0
            for _ in range(3):
                b = m.PersistentBudget(out / "calls.json", 4)

                def rpc(sig, b=b):
                    if not b.reserve():
                        raise CreditBudgetExceeded(b.used, b.cap)
                    return tx_with_log()

                s = m.run(sample, out, rpc, http, GW, b)
            self.assertEqual(json.loads((out / "calls.json").read_text())["calls"], 4)
            self.assertEqual(s["calls_used"], 4)
            self.assertTrue(s["cap_hit"])

    def test_retries_count_and_persist(self) -> None:
        class Lim:
            def acquire(self):
                pass

        with tempfile.TemporaryDirectory() as d:
            b = m.PersistentBudget(Path(d) / "calls.json", 10)
            with patch.object(m._OPENER, "open", side_effect=OSError("x")):
                with self.assertRaises(RuntimeError):
                    m.make_rpc("https://h.invalid/", b, Lim())("S")
            self.assertEqual(json.loads((Path(d) / "calls.json").read_text())["calls"], 3)
            self.assertEqual(m.PersistentBudget(Path(d) / "calls.json", 10).used, 3)

    def test_job_deadline(self) -> None:
        ticks = iter([0, 1, 2, 5000, 5001, 5002, 5003])
        http = FakeHttp({JSON_URL: (200, b"{}")})
        b = CreditBudget(100, 1)
        with patch.object(m, "uri_from_tx", lambda tx, mint=None: URI), tempfile.TemporaryDirectory() as d:
            s = m.run([row(i) for i in range(5)], Path(d), lambda sig: tx_with_log(), http, GW, b,
                      max_runtime_s=3000, clock=lambda: next(ticks))
            self.assertTrue(s["deadline_hit"])
            self.assertEqual(s["by_group"]["G"]["n"], 2)

    def test_reused_sample_sha_checked(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            m.write_sample([dict(row(1), slot=1)], out)
            self.assertEqual(len(m.load_reused_sample(out)), 1)
            (out / "sample.parquet.sha256").write_text("0" * 64 + "  sample.parquet\n")
            with self.assertRaises(SystemExit):
                m.load_reused_sample(out)

    def test_helius_reads_only_named_var(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            env = Path(d) / "e"
            env.write_text("OTHER_KEY=wrong\nexport HELIUS_API_KEY=abc123\n")
            self.assertTrue(m.read_helius_url(str(env)).endswith("api-key=abc123"))
            env.write_text("OTHER_KEY=wrong\n")
            with self.assertRaises(SystemExit):
                m.read_helius_url(str(env))

    def test_helius_both_forms_and_stdin_never_leak(self) -> None:
        for form, value in (("bare", KEY), ("url", f"https://mainnet.helius-rpc.com/?api-key={KEY}")):
            with tempfile.TemporaryDirectory() as d:
                env = Path(d) / "e"
                env.write_text(f"HELIUS_API_KEY={value}\n")
                url = m.read_helius_url(str(env))
                self.assertEqual(url, f"https://mainnet.helius-rpc.com/?api-key={KEY}", form)
                # stdin form
                with patch("sys.stdin", io.StringIO(f"HELIUS_API_KEY={value}\n")):
                    self.assertEqual(m.read_helius_url("-"), url)
                # full main() run reading the env from stdin; nothing may leak
                out = Path(d) / "out"
                buf, err, o = io.StringIO(), io.StringIO(), io.StringIO()
                handler = logging.StreamHandler(buf)
                logging.getLogger().addHandler(handler)
                seen = {}
                try:
                    with patch("sys.stdin", io.StringIO(f"HELIUS_API_KEY={value}\n")), \
                            patch.object(m, "build_sample", lambda n: [row(1), row(2, "N")]), \
                            patch.object(m, "write_sample", lambda rows, p: "dead"), \
                            patch.object(m, "make_rpc", lambda u, b, l: seen.setdefault("u", u) and
                                         (lambda sig: (_ for _ in ()).throw(RuntimeError("http_500")))), \
                            contextlib.redirect_stderr(err), contextlib.redirect_stdout(o):
                        m.main(["--out", str(out), "--helius-env", "-"])
                finally:
                    logging.getLogger().removeHandler(handler)
                self.assertIn(KEY, seen["u"])
                text = buf.getvalue() + err.getvalue() + o.getvalue()
                for f in out.rglob("*"):
                    if f.is_file():
                        text += f.read_text(errors="ignore")
                self.assertNotIn(KEY, text)
        # exceptions do not carry the value either
        with tempfile.TemporaryDirectory() as d:
            env = Path(d) / "e"
            env.write_text("HELIUS_API_KEY=bad key&x\n")
            with self.assertRaises(SystemExit) as cm:
                m.read_helius_url(str(env))
            self.assertNotIn("bad key", str(cm.exception))

    def test_lower_bounds_reported(self) -> None:
        s = m.summarize([st("G", True), st("N", True)], 0)
        self.assertEqual(set(s["lower_bounds_json_ok_wilson90"]), {"G", "N"})
        self.assertLess(s["lower_bounds_json_ok_wilson90"]["G"], 1.0)


class RunTests(unittest.TestCase):
    def rpc_ok(self, budget):
        def rpc(sig):
            if not budget.reserve():
                raise CreditBudgetExceeded(budget.used, budget.cap)
            return tx_with_log()
        return rpc

    def test_call_cap(self) -> None:
        budget = CreditBudget(3, 1)
        http = FakeHttp({JSON_URL: (200, b'{"image":"%s"}' % IMG.encode()), IMG_URL: (200, b"x")})
        sample = [dict(row(i), mint=MINT) for i in range(10)]
        # same mint repeated would be skipped as seen; use distinct names with the same decodable tx
        with patch.object(m, "uri_from_tx", lambda tx, mint=None: URI):
            sample = [row(i) for i in range(10)]
            with tempfile.TemporaryDirectory() as d:
                s = m.run(sample, Path(d), self.rpc_ok(budget), http, GW, budget)
                self.assertTrue(s["cap_hit"])
                self.assertEqual(s["calls_used"], 3)
                self.assertEqual(len(m.load_done(Path(d) / "status.jsonl")), 3)

    def test_resume_skips_done(self) -> None:
        http = FakeHttp({JSON_URL: (200, b'{"image":"%s"}' % IMG.encode()), IMG_URL: (200, b"x")})
        seen_sigs = []
        budget = CreditBudget(100, 1)

        def rpc(sig):
            seen_sigs.append(sig)
            budget.reserve()
            return tx_with_log()

        with patch.object(m, "uri_from_tx", lambda tx, mint=None: URI), tempfile.TemporaryDirectory() as d:
            sample = [row(i) for i in range(4)]
            m.run(sample[:2], Path(d), rpc, http, GW, budget)
            with (Path(d) / "status.jsonl").open("a") as fh:
                fh.write('{"mint": "torn')  # torn tail must not break resume
            seen_sigs.clear()
            s = m.run(sample, Path(d), rpc, http, GW, budget)
            self.assertEqual(seen_sigs, ["S2", "S3"])
            self.assertEqual(s["by_group"]["G"]["n"], 4)

    def test_make_rpc_counts_every_attempt_and_caps(self) -> None:
        budget = CreditBudget(2, 1)

        class Lim:
            def acquire(self):
                pass

        with patch("urllib.request.urlopen", side_effect=OSError("boom")):
            call = m.make_rpc("https://h.invalid/?api-key=" + KEY, budget, Lim())
            with self.assertRaises(CreditBudgetExceeded):
                call("S")
        self.assertEqual(budget.used, 2)


class WilsonCensorTests(unittest.TestCase):
    def test_wilson(self) -> None:
        lo, hi = m.wilson90(97, 100)
        self.assertAlmostEqual(lo, 0.9273, places=3)
        self.assertAlmostEqual(hi, 0.9879, places=3)
        self.assertEqual(m.wilson90(0, 0), None)
        lo, hi = m.wilson90(100, 100)
        self.assertEqual(hi, 1.0)
        s = m.summarize([st("G", True), st("N", False)], 0)
        self.assertEqual(len(s["by_group"]["G"]["json_ok_wilson90"]), 2)
        self.assertEqual(len(s["by_block"]["b1"]["N"]["json_ok_wilson90"]), 2)

    def test_censoring_near_segment_end(self) -> None:
        end = _ts("2026-08-28T12") * 1000
        h = 3600 * 1000
        rows = [
            {"grp": "G", "create_ms": end - 1 * h},
            {"grp": "G", "create_ms": end - 25 * h},
            {"grp": "N", "create_ms": end - 23 * h},
            {"grp": "N", "create_ms": end - 30 * h},
            {"grp": "N", "create_ms": _ts("2026-09-25T07") * 1000 - h},
        ]
        c = m.censoring(rows)
        self.assertEqual((c["G"]["n"], c["G"]["near_segment_end"]), (2, 1))
        self.assertEqual((c["N"]["n"], c["N"]["near_segment_end"]), (3, 2))
        s = m.summarize([], 0, sample=rows)
        self.assertEqual(s["censoring"]["N"]["near_segment_end"], 2)
        self.assertIn("never be used as a feature", s["note"])


def st(grp, json_ok, block="b1"):
    return {"grp": grp, "block": block, "json_ok": json_ok, "tx_ok": True, "error": None}


class SummaryTests(unittest.TestCase):
    def make(self, g_ok, n_ok, total=100):
        rows = [st("G", i < g_ok) for i in range(total)] + [st("N", i < n_ok) for i in range(total)]
        return m.summarize(rows, 5)

    def test_pass(self) -> None:
        s = self.make(97, 96)
        self.assertTrue(s["pass"])
        self.assertAlmostEqual(s["json_ok_gap_G_minus_N"], 0.01)
        self.assertEqual(s["by_group"]["G"]["json_ok"], 97)
        self.assertEqual(s["calls_used"], 5)

    def test_fail_low_share(self) -> None:
        self.assertFalse(self.make(94, 95)["pass"])
        self.assertFalse(self.make(95, 94)["pass"])

    def test_fail_gap(self) -> None:
        s = self.make(100, 97)  # gap 3 points
        self.assertFalse(s["pass"])
        self.assertTrue(self.make(99, 97)["pass"])  # exactly 2 points passes

    def test_empty_group_fails(self) -> None:
        s = m.summarize([st("G", True)], 0)
        self.assertFalse(s["pass"])
        self.assertIsNone(s["json_ok_gap_G_minus_N"])

    def test_per_block(self) -> None:
        s = m.summarize([st("G", True, "a"), st("G", False, "b"), st("N", True, "a")], 0)
        self.assertEqual(set(s["by_block"]), {"a", "b"})
        self.assertEqual(s["by_block"]["b"]["G"]["json_ok"], 0)


class KeyLogTests(unittest.TestCase):
    def test_key_never_logged(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            env = Path(d) / "h.env"
            env.write_text(f"HELIUS_API_KEY={KEY}\n")
            out = Path(d) / "out"
            captured = {}

            def fake_make_rpc(url, budget, limiter):
                captured["url"] = url
                return lambda sig: (_ for _ in ()).throw(RuntimeError("http_500"))

            buf = io.StringIO()
            handler = logging.StreamHandler(buf)
            root = logging.getLogger()
            root.addHandler(handler)
            err, o = io.StringIO(), io.StringIO()
            try:
                with patch.object(m, "build_sample", lambda n: [row(1), row(2, "N")]), \
                        patch.object(m, "write_sample", lambda rows, p: "deadbeef"), \
                        patch.object(m, "make_rpc", fake_make_rpc), \
                        contextlib.redirect_stderr(err), contextlib.redirect_stdout(o):
                    rc = m.main(["--out", str(out), "--helius-env", str(env), "--max-calls", "5"])
            finally:
                root.removeHandler(handler)
            self.assertEqual(rc, 0)
            self.assertIn(KEY, captured["url"])  # the key was used in-process
            everything = buf.getvalue() + err.getvalue() + o.getvalue()
            for p in out.rglob("*"):
                if p.is_file():
                    everything += p.read_text(errors="ignore")
            self.assertNotIn(KEY, everything)
            self.assertIn("calls_used", everything)


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def fast_pool(gateways=GW) -> ipf.GatewayPool:
    return ipf.GatewayPool(tuple(gateways), rps=1e6, burst=1e6)


class RateLimitFetchTests(unittest.TestCase):
    def test_429_with_retry_after_cools_gateway_and_next_try_uses_other(self) -> None:
        clk = FakeClock()
        pool = ipf.GatewayPool(tuple(GW), clock=clk)

        class Http429(FakeHttp):
            def __call__(self, url, timeout, max_bytes, ranged):
                self.calls.append((url, timeout, max_bytes, ranged))
                if "gw1" in url:
                    return m.HttpResult(429, b"", 45.0)
                return (200, b"{}")

        http = Http429()
        body, err = m.fetch_with_retry(URI, GW, http, 100, False, pool)
        self.assertEqual((body, err), (b"{}", None))
        self.assertEqual([c[0].split("/")[2] for c in http.calls], ["gw1.invalid", "gw2.invalid"])
        self.assertAlmostEqual(pool.cooldown_left(GW[0]), 45.0)
        self.assertEqual(pool.counts["http_429"], 1)

    def test_all_cooling_is_rate_limited_all_with_no_request(self) -> None:
        pool = ipf.GatewayPool(tuple(GW), clock=FakeClock())
        for g in GW:
            pool.report(g, "http_429")
        http = FakeHttp()
        self.assertEqual(m.fetch_with_retry(URI, GW, http, 100, False, pool), (None, "rate_limited_all"))
        self.assertEqual(http.calls, [])
        self.assertEqual(pool.counts["rate_limited_all"], 1)

    def test_token_wait_is_slept(self) -> None:
        pool = ipf.GatewayPool((GW[0],), rps=1.0, burst=1.0, clock=FakeClock())
        pool.reserve([GW[0]])
        slept = []
        body, err = m.fetch_with_retry(URI, [GW[0]], FakeHttp(), 100, False, pool, slept.append)
        self.assertEqual((body, slept), (b"{}", [1.0]))

    def test_check_mint_error_names(self) -> None:
        pool = ipf.GatewayPool(tuple(GW), clock=FakeClock())
        for g in GW:
            pool.report(g, "http_429")
        st = m.check_mint(dict(row(1), mint=MINT), lambda s: tx_with_log(), FakeHttp(), GW, pool)
        self.assertEqual(st["error"], "json_rate_limited_all")
        # two 429s cool both gateways; the third try then finds none and makes no request
        p2 = fast_pool()
        http = FakeHttp(default=(429, b""))
        st = m.check_mint(dict(row(1), mint=MINT), lambda s: tx_with_log(), http, GW, p2)
        self.assertEqual((st["error"], len(http.calls), p2.counts["http_429"]), ("json_rate_limited_all", 2, 2))


class RedirectGetTests(unittest.TestCase):
    """urllib_get redirect policy with the single hop faked; DNS patched, no sockets."""

    def go(self, script):
        import socket
        import urllib.error
        calls = []

        def fake_once(url, timeout, max_bytes, ranged, deadline, allow):
            calls.append(url)
            step = script[len(calls) - 1]
            if isinstance(step, bytes):
                return m.HttpResult(200, step)
            hdrs = {"Location": step[1]}
            raise urllib.error.HTTPError(url, step[0], "x", hdrs, None)  # type: ignore[arg-type]
        with patch.object(m, "_get_once", fake_once), \
                patch("socket.getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))]):
            return calls, m.urllib_get("https://ipfs.io/ipfs/x", 5, 100, False)

    def test_follow_two_then_stop(self) -> None:
        calls, res = self.go([(302, "https://dweb.link/a"), (301, "https://arweave.net/b"), b"doc"])
        self.assertEqual((res[0], res[1]), (200, b"doc"))
        self.assertEqual(len(calls), 3)
        calls, res = self.go([(302, "https://dweb.link/a"), (302, "https://dweb.link/b"), (307, "https://dweb.link/c")])
        self.assertEqual((res[0], res[1], len(calls)), (307, b"", 3))

    def test_refused_targets_keep_3xx(self) -> None:
        for loc in ("https://evil.example/x", "http://ipfs.io/ipfs/" + "Qm" + "a" * 44, "file:///etc/passwd"):
            calls, res = self.go([(302, loc)])
            self.assertEqual((res[0], res[1], len(calls)), (302, b"", 1), loc)

    def test_private_target_keeps_3xx(self) -> None:
        import socket
        import urllib.error

        def fake_once(url, *a):
            raise urllib.error.HTTPError(url, 302, "x", {"Location": "https://arweave.net/x"}, None)  # type: ignore[arg-type]
        with patch.object(m, "_get_once", fake_once), \
                patch("socket.getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("10.0.0.5", 443))]):
            res = m.urllib_get("https://ipfs.io/ipfs/x", 5, 100, False)
        self.assertEqual((res[0], res[1]), (302, b""))

    def test_ssrf_refuses_private_first_hop(self) -> None:
        with patch("socket.getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("10.0.0.5", 443))]):
            with self.assertRaises(ipf.BlockedUrl):
                m.urllib_get("https://arweave.net/x", 5, 100, False)


class HostClassAndUrisTests(unittest.TestCase):
    AR = "ar://" + "D" * 43

    def test_arweave_and_other_host_classes_in_status(self) -> None:
        ar_url = "https://arweave.net/" + "D" * 43
        http = FakeHttp({ar_url: (200, b"{}")})
        st = m.check_mint(dict(row(1), mint=MINT), lambda s: tx_with_log(self.AR), http, GW, fast_pool())
        self.assertTrue(st["json_ok"])
        self.assertEqual(st["uri_host_class"], "arweave")
        self.assertNotIn("uri_host", st)
        self.assertEqual(http.calls[0][0], ar_url)
        st = m.check_mint(dict(row(1), mint=MINT), lambda s: tx_with_log("https://Evil.example/p?q=1"),
                          FakeHttp(), GW, fast_pool())
        self.assertEqual((st["error"], st["uri_host_class"], st["uri_host"]),
                         ("json_non_ipfs_uri", "other", "evil.example"))
        self.assertNotIn("q=1", json.dumps(st))
        st = m.check_mint(dict(row(1), mint=MINT), lambda s: tx_with_log("https://i.imgur.com/a.json"),
                          FakeHttp(), GW, fast_pool())
        self.assertEqual(st["uri_host_class"], "allowlisted:i.imgur.com")

    def test_uris_jsonl_round_trip_and_no_rpc_on_refetch(self) -> None:
        sample = [row(i) for i in range(3)]
        calls = []

        def rpc(sig):
            calls.append(sig)
            return tx_with_log()
        with patch.object(m, "uri_from_tx", lambda tx, mint=None: URI), tempfile.TemporaryDirectory() as d:
            old, new = Path(d) / "old", Path(d) / "new"
            m.run(sample, old, rpc, FakeHttp({JSON_URL: (200, b"{}")}), GW, CreditBudget(10, 1), pool=fast_pool())
            self.assertEqual(len(calls), 3)
            stored = m.load_uris(old / "uris.jsonl")
            self.assertEqual(stored, {f"M{i}": URI for i in range(3)})
            # status rows and summary never carry the uri
            for f in ("status.jsonl", "summary.json"):
                self.assertNotIn("QmFakeCid", (old / f).read_text())
            # new dir, uris given from the old one: zero rpc, uris copied into the new dir
            calls.clear()
            s = m.run(sample, new, rpc, FakeHttp({JSON_URL: (200, b"{}")}), GW, CreditBudget(10, 1),
                      pool=fast_pool(), extra_uris=stored)
            self.assertEqual(calls, [])
            self.assertEqual(m.load_uris(new / "uris.jsonl"), stored)
            self.assertEqual(s["by_group"]["G"]["json_ok"], 3)
            self.assertTrue(all(r["uri_cached"] for r in m.load_done(new / "status.jsonl")))
            # a torn last line is ignored
            with (new / "uris.jsonl").open("a") as fh:
                fh.write('{"mint": "x", "ur')
            self.assertEqual(m.load_uris(new / "uris.jsonl"), stored)

    def test_summary_host_classes_and_rate_limit_counts(self) -> None:
        sts = [
            {"grp": "G", "uri_found": True, "json_ok": True, "uri_host_class": "ipfs_cid"},
            {"grp": "G", "uri_found": True, "json_ok": False, "uri_host_class": "ipfs_cid", "error": "json_http_429"},
            {"grp": "N", "uri_found": True, "json_ok": False, "uri_host_class": "other", "uri_host": "a.example",
             "error": "json_non_ipfs_uri"},
            {"grp": "N", "uri_found": True, "json_ok": False, "uri_host_class": "ipfs_cid",
             "error": "json_rate_limited_all"},
            {"grp": "N", "uri_found": False, "json_ok": False, "error": "tx_missing"},
        ]
        s = m.summarize(sts, 5, gateway_counts={"http_429": 7, "rate_limited_all": 1})
        self.assertEqual(s["uri_host_classes"]["classes"]["ipfs_cid"], {"n": 3, "json_ok": 1})
        self.assertEqual(s["uri_host_classes"]["other_hosts"], {"a.example": 1})
        self.assertEqual(s["rate_limit_mint_errors"], {"http_429": 1, "rate_limited_all": 1, "non_ipfs_uri": 1})
        self.assertEqual(s["gateway_counts"]["http_429"], 7)


class ResumeFromTests(unittest.TestCase):
    def test_adopt_sample_checks_sha_and_dir(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            old, new = Path(d) / "old", Path(d) / "new"
            old.mkdir()
            new.mkdir()
            digest = m.write_sample([dict(row(1), slot=1)], old)
            m.adopt_sample(old, new)
            self.assertEqual(hashlib.sha256((new / "sample.parquet").read_bytes()).hexdigest(), digest)
            self.assertIn(digest, (new / "sample.parquet.sha256").read_text())
            with self.assertRaises(SystemExit):
                m.adopt_sample(old, old)          # same dir would share the old calls.json
            (old / "sample.parquet.sha256").write_text("0" * 64 + "  sample.parquet\n")
            with self.assertRaises(SystemExit):
                m.adopt_sample(old, Path(d) / "other")
            with self.assertRaises(SystemExit):
                m.adopt_sample(Path(d) / "missing", new)

    def test_new_dir_has_its_own_rpc_cap(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            old, new = Path(d) / "old", Path(d) / "new"
            old.mkdir()
            new.mkdir()
            (old / "calls.json").write_text('{"calls": 2000}')
            b = m.PersistentBudget(new / "calls.json", 3000)
            self.assertEqual(b.used, 0)
            self.assertEqual(m.MAX_CALLS_DEFAULT, 3000)
            self.assertEqual(m.PersistentBudget(old / "calls.json", 3000).used, 2000)

    def test_gateway_defaults_and_flags(self) -> None:
        self.assertEqual((ipf.DEFAULT_RPS, ipf.DEFAULT_BURST), (1.0, 2.0))
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                m.main(["--out", "/nonexistent", "--gateway", "https://x"])  # gateways stay pinned


if __name__ == "__main__":
    unittest.main()
