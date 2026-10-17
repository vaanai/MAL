"""meta_capture tests. Fakes only: no vendor socket, no Helius, no internet.

The streaming-cap tests use a local HTTP server on 127.0.0.1, allowed through
the SSRF guard explicitly via ``allow_private_hosts``.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import os
import re
import sys
import tempfile
import threading
import time
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tools import meta_capture as mc

try:
    import PIL  # noqa: F401
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False

HOUR0 = 1_760_000_000_000 - (1_760_000_000_000 % 3_600_000)  # an exact hour
HK0 = mc.hour_key(HOUR0)
HK1 = mc.hour_key(HOUR0 + 3_600_000)
MINT = "MintAAAA111"
DROPPED = {
    "initialBuy": 1.5, "solAmount": 2.0, "tokenAmount": 9, "vTokensInBonding": 1,
    "vSolInBonding": 30, "marketCapSol": 28, "price": 0.1, "reserve": 5,
    "pool": "pump", "unknownKey": "zzz",
}
ALLOWED_OUT = {"signature", "mint", "name", "symbol", "creator", "uri", "bondingCurveKey",
               "slot", "vendor_time", "t_recv_ms"}


def create_payload(**extra):
    p = {"txType": "create", "mint": MINT, "name": "N", "symbol": "S", "signature": "sig1",
         "traderPublicKey": "Creator1", "uri": "ipfs://cid1", "bondingCurveKey": "BC1",
         "slot": 77, "timestamp": 1_760_000_000, **DROPPED}
    p.update(extra)
    return p


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class FakeHttp:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    async def __call__(self, url, timeout, max_bytes):
        self.calls.append(url)
        res = self.routes(url)
        if isinstance(res, Exception):
            raise res
        if len(res) > max_bytes:
            raise mc.FetchTooBig()
        return res


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def make(d, routes=lambda u: mc.HttpError(503), clock=None, **kw):
    clock = clock or Clock(HOUR0 + 100)
    col = mc.Collector(mc.Store(Path(d), git_ref="abc123"), gateways=("https://g1/ipfs/", "https://g2/ipfs/"),
                       http_get=FakeHttp(routes), clock_ms=clock, **kw)
    return col, clock


def meta_rows(col, hk=HK0):
    return [json.loads(x) for x in col.store.meta_path(hk).read_text().splitlines()]


class AllowlistTests(unittest.TestCase):
    def test_unknown_and_dropped_keys_never_reach_disk(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            col, _ = make(d)
            col.on_message(create_payload(), HOUR0 + 100)
            text = col.store.creates_path(HK0).read_text()
            for key in list(DROPPED) + ["txType"]:
                self.assertNotIn(key, text)
            row = json.loads(text)
            self.assertEqual(set(row), ALLOWED_OUT)
            self.assertEqual(row["creator"], "Creator1")
            self.assertEqual(row["bondingCurveKey"], "BC1")

    def test_non_create_dropped(self) -> None:
        self.assertIsNone(mc.create_row({"txType": "buy", "mint": MINT}, 1))
        self.assertIsNone(mc.create_row({"txType": "create"}, 1))

    def test_length_cap_and_type_check(self) -> None:
        row = mc.create_row(create_payload(name="x" * 5000, symbol={"a": 1}, uri=["u"]), 1)
        assert row
        self.assertEqual(len(row["name"]), 512)
        self.assertIsNone(row["symbol"])
        self.assertIsNone(row["uri"])


class UriTests(unittest.TestCase):
    GW = ["https://a.example/ipfs/", "https://b.example/ipfs"]

    def test_ipfs_scheme_and_known_gateway(self) -> None:
        self.assertEqual(mc.candidate_urls("ipfs://cid/x.json", self.GW),
                         ["https://a.example/ipfs/cid/x.json", "https://b.example/ipfs/cid/x.json"])
        self.assertEqual(mc.candidate_urls("https://ipfs.io/ipfs/cid", self.GW)[0],
                         "https://a.example/ipfs/cid")

    def test_plain_https_untouched_and_junk_rejected(self) -> None:
        self.assertEqual(mc.candidate_urls("https://x.org/m.json", self.GW), ["https://x.org/m.json"])
        self.assertEqual(mc.candidate_urls("file:///etc/passwd", self.GW), [])

    def test_uri_kind(self) -> None:
        self.assertEqual(mc.uri_kind("ipfs://c"), "cid")
        self.assertEqual(mc.uri_kind("https://ipfs.io/ipfs/c"), "cid")
        self.assertEqual(mc.uri_kind("https://x.org/m.json"), "mutable")


class JsonTests(unittest.TestCase):
    def test_allowlist_cut_and_caps(self) -> None:
        doc = {"name": "n" * 900, "symbol": "s", "description": "x" * 5000, "twitter": "t",
               "telegram": 5, "website": "w", "image": "ipfs://i", "createdOn": "pump.fun",
               "showName": True, "attributes": [1], "price": 3, "initialBuy": 4}
        out = mc.extract_meta(doc)
        self.assertEqual(len(out["description"]), 2000)
        self.assertEqual(len(out["name"]), 512)
        self.assertNotIn("telegram", out)
        self.assertEqual(set(out), {"name", "symbol", "description", "twitter", "website",
                                    "image", "createdOn", "showName"})


class ImageTests(unittest.TestCase):
    @unittest.skipUnless(HAVE_PIL, "Pillow not installed")
    def test_downsample_and_pixel_cap(self) -> None:
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (640, 480), (255, 0, 0)).save(buf, format="PNG")
        png, info = mc.process_image(buf.getvalue())
        assert png is not None
        self.assertEqual(Image.open(io.BytesIO(png)).size, (224, 224))
        self.assertEqual(info["img_sha256"], hashlib.sha256(buf.getvalue()).hexdigest())
        self.assertIn("img_png_sha256", info)
        big = io.BytesIO()
        Image.new("L", (3000, 2000)).save(big, format="PNG")
        png2, info2 = mc.process_image(big.getvalue())
        self.assertIsNone(png2)
        self.assertTrue(info2["img_too_big"])

    def test_fallback_without_pillow(self) -> None:
        saved = sys.modules.get("PIL")
        sys.modules["PIL"] = None  # makes ``from PIL import Image`` raise ImportError
        try:
            raw = b"not really an image"
            png, info = mc.process_image(raw)
        finally:
            if saved is None:
                del sys.modules["PIL"]
            else:
                sys.modules["PIL"] = saved
        self.assertIsNone(png)
        self.assertEqual(info["img_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(info["img_bytes"], len(raw))


DOC = json.dumps({"name": "SECRETNAME", "image": "ipfs://img", "twitter": "https://twitter.example/a",
                  "telegram": "https://t.me/x", "website": "https://site.example/",
                  "initialBuy": 9, "description": "SECRETDESC"}).encode()


def doc_routes(u):
    return DOC if "cid1" in u else b"imgbytes"


class ScheduleTests(unittest.TestCase):
    def test_fixed_schedule_fake_clock(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            col, clock = make(d)
            col.on_message(create_payload(), HOUR0 + 100)
            ran = []
            for dt, expect in [(0, 1), (29_999, 0), (30_100, 1), (119_000, 0),
                               (120_100, 1), (599_000, 0), (600_100, 1), (700_000, 0)]:
                clock.t = HOUR0 + 100 + dt
                ran.append(run(col.step()))
                self.assertEqual(ran[-1], expect, dt)
            rows = meta_rows(col)
            self.assertEqual([r["attempt"] for r in rows], [0, 1, 2, 3])
            self.assertEqual([r["scheduled_ms"] - (HOUR0 + 100) for r in rows],
                             [0, 30_000, 120_000, 600_000])
            self.assertEqual(rows[0]["status"], "http_503")
            self.assertEqual(rows[0]["uri_kind"], "cid")
            self.assertEqual(rows[0]["gateway"], "g1")
            self.assertEqual(rows[1]["gateway"], "g2")
            self.assertEqual(col.heap, [])

    def test_schedule_survives_long_queue(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            col, clock = make(d)
            col.on_message(create_payload(), HOUR0 + 100)
            clock.t = HOUR0 + 100 + 200_000  # queue was stuck: attempts 0,1,2 all overdue
            for _ in range(3):
                run(col.step())
            self.assertEqual([r["attempt"] for r in meta_rows(col)], [0, 1, 2])
            self.assertEqual(meta_rows(col)[1]["scheduled_ms"], HOUR0 + 100 + 30_000)

    def test_stop_at_first_ok_and_record_fields(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            n = {"c": 0}

            def routes(u):
                if "cid1" in u:
                    n["c"] += 1
                    return mc.FetchTimeout() if n["c"] == 1 else DOC
                return b"imgbytes"
            col, clock = make(d, routes)
            col.on_message(create_payload(), HOUR0 + 100)
            clock.t = HOUR0 + 700_000
            for _ in range(5):
                run(col.step())
            rows = meta_rows(col)
            self.assertEqual([(r["attempt"], r["status"]) for r in rows], [(0, "timeout"), (1, "ok")])
            ok = rows[1]
            self.assertEqual(ok["sha256"], hashlib.sha256(DOC).hexdigest())
            self.assertEqual(ok["bytes"], len(DOC))
            self.assertEqual(ok["img_sha256"], hashlib.sha256(b"imgbytes").hexdigest())
            self.assertNotIn("initialBuy", ok)
            self.assertEqual(col.heap, [])

    def test_links_never_fetched_and_logs_have_no_content(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            records: list[str] = []

            class H(logging.Handler):
                def emit(self, r):
                    records.append(r.getMessage())
            h = H()
            logging.getLogger().addHandler(h)
            old = logging.getLogger().level
            logging.getLogger().setLevel(logging.DEBUG)
            try:
                col, _ = make(d, doc_routes)
                col.on_message(create_payload(), HOUR0 + 100)
                run(col.step())
            finally:
                logging.getLogger().removeHandler(h)
                logging.getLogger().setLevel(old)
            row = meta_rows(col)[0]
            self.assertEqual(row["twitter"], "https://twitter.example/a")
            for u in col.http_get.calls:
                for bad in ("twitter.example", "t.me", "site.example"):
                    self.assertNotIn(bad, u)
            self.assertEqual(len(col.http_get.calls), 2)  # metadata json + image only
            joined = " ".join(records)
            for secret in ("SECRETNAME", "SECRETDESC", "twitter.example", "t.me"):
                self.assertNotIn(secret, joined)

    def test_bad_json_and_recursion(self) -> None:
        for body in (b"<html>", b"[" * 100_000 + b"]" * 100_000):
            with tempfile.TemporaryDirectory() as d:
                col, _ = make(d, lambda u, b=body: b)
                col.on_message(create_payload(), HOUR0 + 100)
                run(col.step())
                self.assertEqual(meta_rows(col)[0]["status"], "bad_json")

    def test_disk_guard_skips_image_only(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            col, _ = make(d, doc_routes, min_free_gb=10**9)
            col.on_message(create_payload(), HOUR0 + 100)
            run(col.step())
            r = meta_rows(col)[0]
            self.assertEqual((r["status"], r["img_status"]), ("ok", "skipped_disk"))
            self.assertEqual(len(col.http_get.calls), 1)

    def test_backlog_bounded_and_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            col, _ = make(d, max_pending=1)
            col.on_message(create_payload(), HOUR0 + 100)
            col.on_message(create_payload(mint="MintBBB"), HOUR0 + 101)
            self.assertEqual(col.dropped, 1)
            self.assertEqual(len(col.heap), 1)
            self.assertEqual(meta_rows(col)[0]["status"], "dropped_backlog")


class StatusClassTests(unittest.TestCase):
    def test_classes(self) -> None:
        for exc, want in [(mc.FetchTimeout(), "timeout"), (mc.HttpError(404), "http_404"),
                          (mc.FetchTooBig(), "too_big"), (mc.BlockedUrl("x"), "blocked_url")]:
            http = FakeHttp(lambda u, e=exc: e)
            self.assertEqual(run(mc.get_once("https://x/y", http, 10))[0], want)
        self.assertEqual(run(mc.get_once("https://x/y", FakeHttp(lambda u: b"hi"), 10)), ("ok", b"hi"))


class SsrfAndStreamingTests(unittest.TestCase):
    def test_guard(self) -> None:
        for url in ("ftp://x/y", "http://example.org/x", "https://10.0.0.1/x",
                    "https://169.254.169.254/latest", "https://127.0.0.1/x", "https://[::1]/x"):
            with self.assertRaises(mc.BlockedUrl, msg=url):
                mc.check_url(url)
        mc.check_url("http://127.0.0.1:1/x", allow_private_hosts=frozenset({"127.0.0.1"}))

    @classmethod
    def setUpClass(cls) -> None:
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # noqa: ANN002
                pass

            def do_GET(self):  # noqa: N802
                if self.path == "/ok":
                    self.send_response(200); self.end_headers(); self.wfile.write(b"hello")
                elif self.path == "/redir":
                    self.send_response(302); self.send_header("Location", "http://127.0.0.1/ok")
                    self.end_headers()
                elif self.path == "/big":
                    self.send_response(200); self.end_headers()
                    try:
                        for _ in range(100):
                            self.wfile.write(b"x" * 10_000)
                    except OSError:
                        pass
                elif self.path == "/slow":
                    self.send_response(200); self.end_headers()
                    try:
                        for _ in range(30):
                            self.wfile.write(b"x"); self.wfile.flush(); time.sleep(0.1)
                    except OSError:
                        pass
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        cls.srv.daemon_threads = True
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.srv.shutdown()

    def get(self, path, max_bytes=1000, timeout=5.0):
        return mc._urllib_get(self.base + path, timeout, max_bytes,
                              allow_private_hosts=frozenset({"127.0.0.1"}))

    def test_ok(self) -> None:
        self.assertEqual(self.get("/ok"), b"hello")

    def test_streaming_size_cap(self) -> None:
        with self.assertRaises(mc.FetchTooBig):
            self.get("/big", max_bytes=50_000)

    def test_wall_clock_deadline(self) -> None:
        t0 = time.monotonic()
        with self.assertRaises(mc.FetchTimeout):
            self.get("/slow", timeout=0.5)
        self.assertLess(time.monotonic() - t0, 2.0)

    def test_redirect_not_followed(self) -> None:
        with self.assertRaises(mc.HttpError) as cm:
            self.get("/redir")
        self.assertEqual(cm.exception.status, 302)


class SealTests(unittest.TestCase):
    def test_receive_hour_seal_chain_and_chmod(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            col, clock = make(d, doc_routes)
            recv = HOUR0 + 3_599_000  # last second of hour 0
            col.on_message(create_payload(), recv)
            clock.t = HOUR0 + 3_600_500  # fetch finishes in hour 1
            run(col.step())
            self.assertTrue(col.store.meta_path(HK0).exists())  # keyed by RECEIVE hour
            self.assertFalse(col.store.meta_path(HK1).exists())
            self.assertTrue(col.store.img_path(HK0, MINT).exists() or not HAVE_PIL)
            clock.t = HOUR0 + 3_600_000 + 899_000
            self.assertEqual(col.store.seal_due(clock.t), [])
            clock.t = HOUR0 + 3_600_000 + 900_000
            self.assertEqual(col.store.seal_due(clock.t), [HK0])
            out = Path(d)
            lines = (out / "MANIFEST.sha256").read_text().splitlines()
            self.assertTrue(any(l.endswith(f"creates/{HK0}.jsonl") for l in lines))
            for l in lines:
                sha, rel = l.split("  ", 1)
                self.assertEqual(sha, hashlib.sha256((out / rel).read_bytes()).hexdigest())
                self.assertEqual(os.stat(out / rel).st_mode & 0o777, 0o440)
            cap = json.loads((out / "capture" / f"{HK0}.CAPTURE.json").read_text())
            self.assertEqual(cap["git_ref"], "abc123")
            self.assertEqual(cap["tool_sha256"], hashlib.sha256(Path(mc.__file__).read_bytes()).hexdigest())
            chain1 = json.loads((out / "MANIFEST.chain").read_text().splitlines()[0])
            self.assertEqual(chain1["prev"], "0" * 64)
            self.assertEqual(chain1["manifest_sha256"],
                             hashlib.sha256(("\n".join(lines) + "\n").encode()).hexdigest())
            # a late write into the sealed hour is refused
            self.assertFalse(col.store.add_create(mc.create_row(create_payload(), recv)))
            # hour 1 seals later and chains to hash of the first chain line
            col.on_message(create_payload(mint="M2"), HOUR0 + 3_700_000)
            self.assertEqual(col.store.seal_due(HOUR0 + 2 * 3_600_000 + 900_000), [HK1])
            c2 = json.loads((out / "MANIFEST.chain").read_text().splitlines()[1])
            first_line = (out / "MANIFEST.chain").read_text().splitlines()[0]
            self.assertEqual(c2["prev"], hashlib.sha256(first_line.encode()).hexdigest())
            # chain state survives a restart
            self.assertEqual(mc.Store(out).last_chain,
                             hashlib.sha256((out / "MANIFEST.chain").read_text().splitlines()[1].encode()).hexdigest())

    def test_resume_across_hour_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            col, clock = make(d)
            col.on_message(create_payload(), HOUR0 + 3_590_000)
            clock.t = HOUR0 + 3_590_000
            run(col.step())  # attempt 0 fails, attempt 1 queued
            # restart one minute into hour 1
            col2, _ = make(d, clock=Clock(HOUR0 + 3_660_000))
            self.assertEqual(col2.recover(), 3)
            self.assertEqual(sorted(a for _, _, a, _ in col2.heap), [1, 2, 3])
            # restart after the seal delay: earlier unsealed hour gets manifest lines
            col3, _ = make(d, clock=Clock(HOUR0 + 3_600_000 + 900_000))
            col3.store.seal_due(col3.clock_ms())
            self.assertIn(HK0, col3.store.sealed)
            self.assertEqual(col3.recover(), 0)


class GuardTests(unittest.TestCase):
    def test_rss_guard_stops_fetch_scheduling(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            col, _ = make(d, rss_fn=lambda: 2000.0, max_rss_mb=1500)
            col.on_message(create_payload(), HOUR0 + 100)
            self.assertTrue(col.rss_exceeded)
            self.assertEqual(col.heap, [])

    def test_numbers(self) -> None:
        self.assertGreaterEqual(mc.rss_mb(), 0.0)
        self.assertGreater(mc.free_gb(Path(tempfile.gettempdir())), 0.0)


class FakeWs:
    """Fake ``websockets`` module: connect #1 fails, #2 delivers one frame then stops."""

    def __init__(self, stop, mode):
        self.stop, self.mode, self.connects = stop, mode, 0

    def module(self):
        outer = self
        m = types.ModuleType("websockets")
        ex = types.ModuleType("websockets.exceptions")

        class WSE(Exception):
            pass

        class CC(WSE):
            pass
        ex.ConnectionClosed, ex.WebSocketException = CC, WSE

        class Conn:
            async def __aenter__(self_):
                outer.connects += 1
                if outer.mode == "fail" or outer.connects == 1:
                    raise OSError("boom")
                self_.sent = False
                return self_

            async def __aexit__(self_, *a):
                return False

            async def send(self_, msg):
                outer.sent = msg

            async def recv(self_):
                if not getattr(self_, "frame", False):
                    self_.frame = True
                    return json.dumps(create_payload())
                outer.stop.set()  # SIGTERM equivalent
                return "[]"
        m.connect = lambda *a, **k: Conn()
        m.exceptions = ex
        return {"websockets": m, "websockets.exceptions": ex}


class RunTests(unittest.TestCase):
    def _run(self, mode, rss=lambda: 1.0, **kw):
        d = tempfile.mkdtemp()
        col, _ = make(d, rss_fn=rss, **kw)

        async def go():
            stop = asyncio.Event()
            fake = FakeWs(stop, mode)
            saved = {k: sys.modules.get(k) for k in ("websockets", "websockets.exceptions")}
            sys.modules.update(fake.module())
            try:
                if mode == "fail":
                    asyncio.get_running_loop().call_later(0.2, stop.set)
                t0 = time.monotonic()
                rc = await mc.run("wss://fake", col, stop, None)
                return rc, time.monotonic() - t0, fake
            finally:
                for k, v in saved.items():
                    if v is None:
                        sys.modules.pop(k, None)
                    else:
                        sys.modules[k] = v
        rc, dt, fake = run(go())
        return col, rc, dt, fake

    def test_reconnect_backoff_sigterm_and_gap_log(self) -> None:
        col, rc, dt, fake = self._run("reconnect")
        self.assertEqual(rc, 0)
        self.assertEqual(fake.connects, 2)  # 1 s backoff, then connected
        self.assertGreaterEqual(dt, 1.0)
        self.assertIn('"subscribeNewToken"', fake.sent)
        self.assertEqual(len(list((col.store.out / "creates").glob("*.jsonl"))), 1)  # real recv clock
        gaps = json.loads(col.store.capture_path(HK0).read_text())["gap_log"]
        self.assertEqual([g["event"] for g in gaps], ["disconnected:OSError", "connected"])

    def test_backoff_sleep_wakes_on_stop(self) -> None:
        _, rc, dt, fake = self._run("fail")
        self.assertEqual(rc, 0)
        self.assertLess(dt, 1.0)

    def test_rss_exit_code(self) -> None:
        _, rc, _, _ = self._run("reconnect", rss=lambda: 5000.0, max_rss_mb=1500)
        self.assertEqual(rc, 3)


class NoHeliusTests(unittest.TestCase):
    def test_source_has_no_rpc_or_helius(self) -> None:
        text = Path(mc.__file__).read_text()
        for needle in ("helius", "api-key", "api_key", "getblock", "rpc"):
            self.assertNotIn(needle, text.lower())
        self.assertEqual(re.findall(r"os\.environ\.get\(\s*\"([A-Z_]+)\"", text), ["MAL_PUMPPORTAL_WS_URL"])


if __name__ == "__main__":
    unittest.main()
