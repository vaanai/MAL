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
from observe.trade_decode import b58encode
from tools.pump_history_backfill import CreditBudget, CreditBudgetExceeded

URI = "ipfs://QmFakeCid"
IMG = "https://img.invalid/a.png"
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


def row(i: int, grp: str = "G", block: str = "b1") -> dict:
    return {"mint": f"M{i}", "grp": grp, "block": block, "signature": f"S{i}"}


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


class SampleTests(unittest.TestCase):
    def test_deterministic_order_and_filters(self) -> None:
        import duckdb

        with tempfile.TemporaryDirectory() as d:
            tp, cp = Path(d) / "t.parquet", Path(d) / "c.parquet"
            con = duckdb.connect()
            toks = []
            for i in range(20):
                toks.append((f"m{i}", True, i % 5 == 0 and False, 1000 + i if i % 2 == 0 else None, "blk"))
            toks.append(("mayhem", True, True, None, "blk"))
            toks.append(("nocreate", False, False, None, "blk"))
            con.execute("create table t(mint varchar, has_create boolean, is_mayhem_mode boolean, grad_ms bigint, block varchar, create_ms bigint default 1)")
            con.executemany("insert into t(mint,has_create,is_mayhem_mode,grad_ms,block) values (?,?,?,?,?)", toks)
            con.execute(f"copy t to '{tp}' (format parquet)")
            con.execute("create table c(mint varchar, signature varchar, slot bigint, block_time bigint)")
            con.executemany(
                "insert into c values (?,?,?,?)",
                [(t[0], "sig_" + t[0], 5, 6) for t in toks if t[0] != "m3"]
                + [("m0", "sig_dup_later", 9, 7)],
            )
            con.execute(f"copy c to '{cp}' (format parquet)")
            a = m.build_sample(3, str(tp), str(cp))
            b = m.build_sample(3, str(tp), str(cp))
            self.assertEqual(a, b)
            self.assertEqual({r["grp"] for r in a}, {"G", "N"})
            self.assertEqual(sum(r["grp"] == "G" for r in a), 3)
            self.assertEqual(sum(r["grp"] == "N" for r in a), 3)
            ids = {r["mint"] for r in a}
            self.assertFalse(ids & {"mayhem", "nocreate", "m3"})
            for g in "GN":
                mints = [r["mint"] for r in a if r["grp"] == g]
                everything = [t[0] for t in toks if t[0] not in ("mayhem", "nocreate", "m3")
                              and ((t[3] is not None) == (g == "G"))]
                want = sorted(everything, key=lambda s: (hashlib.sha256(s.encode()).digest(), s))[:3]
                self.assertEqual(mints, want)
            self.assertEqual([r["signature"] for r in a if r["mint"] == "m0"] or ["sig_m0"], ["sig_m0"])

    def test_write_sample_sha(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            rows = [dict(row(1), create_ms=1, slot=2, block_time=3)]
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
        http = FakeHttp({JSON_URL: (200, json.dumps(meta).encode()), IMG: (206, b"png")})
        st = self.check(tx_with_log(), http)
        self.assertTrue(all(st[k] for k in ("tx_ok", "uri_found", "json_ok", "has_description", "has_any_social", "image_ok")))
        self.assertIsNone(st["error"])
        self.assertNotIn("hi", json.dumps(st))  # no description text stored
        img_call = [c for c in http.calls if c[0] == IMG][0]
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
        http = FakeHttp({JSON_URL: (200, json.dumps({"image": IMG}).encode()), IMG: (404, b"")})
        st = self.check(tx_with_log(), http)
        self.assertTrue(st["json_ok"])
        self.assertFalse(st["image_ok"])
        self.assertEqual(st["error"], "image_http_404")

    def test_keep_json_off_by_default(self) -> None:
        http = FakeHttp({JSON_URL: (200, b'{"description":"d"}')})
        with tempfile.TemporaryDirectory() as d:
            m.check_mint(dict(row(1), mint=MINT), lambda s: tx_with_log(), http, GW, None)
            self.assertEqual(list(Path(d).iterdir()), [])
            m.check_mint(dict(row(1), mint=MINT), lambda s: tx_with_log(), http, GW, Path(d) / "json")
            self.assertTrue((Path(d) / "json" / f"{MINT}.json").exists())


class RunTests(unittest.TestCase):
    def rpc_ok(self, budget):
        def rpc(sig):
            if not budget.reserve():
                raise CreditBudgetExceeded(budget.used, budget.cap)
            return tx_with_log()
        return rpc

    def test_call_cap(self) -> None:
        budget = CreditBudget(3, 1)
        http = FakeHttp({JSON_URL: (200, b'{"image":"%s"}' % IMG.encode()), IMG: (200, b"x")})
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
        http = FakeHttp({JSON_URL: (200, b'{"image":"%s"}' % IMG.encode()), IMG: (200, b"x")})
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


if __name__ == "__main__":
    unittest.main()
