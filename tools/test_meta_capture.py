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
import unittest.mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tools import ipfs_fetch as ipf
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
    gws = ("https://g1/ipfs/", "https://g2/ipfs/")
    kw.setdefault("pool", ipf.GatewayPool(gws, rps=1e6, burst=1e6))
    col = mc.Collector(mc.Store(Path(d), git_ref="abc123"), gateways=gws,
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

    def test_other_https_host_direct_and_http_or_junk_rejected(self) -> None:
        self.assertEqual(mc.candidate_urls("https://x.org/m.json", self.GW), ["https://x.org/m.json"])
        self.assertEqual(mc.candidate_urls("http://x.org/m.json", self.GW), [])
        self.assertEqual(mc.candidate_urls("ar://" + "B" * 43, self.GW), ["https://arweave.net/" + "B" * 43])
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


def tiny_png() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (0, 128, 255)).save(buf, format="PNG")
    return buf.getvalue()


def doc_routes(u):
    if "cid1" in u:
        return DOC
    return tiny_png() if HAVE_PIL else b"imgbytes"


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
            self.assertEqual(rows[1]["gateway"], "g1")  # first non-cooling gateway in pinned order
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
            self.assertIn("img_sha256", ok)
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


class BadImageTests(unittest.TestCase):
    @unittest.skipUnless(HAVE_PIL, "Pillow not installed")
    def test_undecodable_image_records_bad_image(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            col, _ = make(d, lambda u: DOC if "cid1" in u else b"not an image")
            col.on_message(create_payload(), HOUR0 + 100)
            run(col.step())
            r = meta_rows(col)[0]
            self.assertEqual((r["status"], r["img_status"]), ("ok", "bad_image"))
            self.assertFalse(col.store.img_path(HK0, MINT).exists())
            self.assertEqual(r["img_sha256"], hashlib.sha256(b"not an image").hexdigest())


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
            if HAVE_PIL:
                from PIL import Image
                png_path = col.store.img_path(HK0, MINT)
                self.assertEqual(Image.open(png_path).size, (224, 224))
                row = meta_rows(col)[0]
                self.assertEqual(row["img_status"], "ok")
                self.assertEqual(row["img_sha256"], hashlib.sha256(tiny_png()).hexdigest())
                self.assertEqual(row["img_png_sha256"], hashlib.sha256(png_path.read_bytes()).hexdigest())
            else:
                self.assertFalse(col.store.img_path(HK0, MINT).exists())
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
            self.assertEqual(cap["tool_sha256"], [hashlib.sha256(Path(mc.__file__).read_bytes()).hexdigest()])
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
            self.assertEqual(col2.recover(), 1)
            self.assertEqual([a for _, _, a, _ in col2.heap], [1])
            # run to completion after the restart: exactly one row per attempt number
            col2.clock_ms = Clock(HOUR0 + 4_300_000)
            for _ in range(6):
                run(col2.step())
            self.assertEqual(sorted(r["attempt"] for r in meta_rows(col2)), [0, 1, 2, 3])
            self.assertEqual(len(col2.http_get.calls), 3)
            # restart after the seal delay: earlier unsealed hour gets manifest lines
            col3, _ = make(d, clock=Clock(HOUR0 + 3_600_000 + 900_000))
            col3.store.seal_due(col3.clock_ms())
            self.assertIn(HK0, col3.store.sealed)
            self.assertEqual(col3.recover(), 0)


class GatewayTests(unittest.TestCase):
    def test_pinned_default_gateways(self) -> None:
        self.assertEqual(mc.DEFAULT_GATEWAYS, ("https://gateway.pinata.cloud/ipfs/",
                                               "https://ipfs.io/ipfs/", "https://dweb.link/ipfs/"))
        self.assertIn("pump.mypinata.cloud", mc.KNOWN_GATEWAY_HOSTS)
        self.assertEqual(mc.candidate_urls("https://pump.mypinata.cloud/ipfs/cid", mc.DEFAULT_GATEWAYS)[0],
                         "https://gateway.pinata.cloud/ipfs/cid")


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
                logging.getLogger("websockets.client").debug(
                    "< TEXT %s", json.dumps(create_payload()))
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
        self.assertEqual([g["event"] for g in gaps], ["process_start", "disconnected:OSError", "connected"])

    def test_backoff_sleep_wakes_on_stop(self) -> None:
        _, rc, dt, fake = self._run("fail")
        self.assertEqual(rc, 0)
        self.assertLess(dt, 1.0)

    def test_rss_exit_code(self) -> None:
        _, rc, _, _ = self._run("reconnect", rss=lambda: 5000.0, max_rss_mb=1500)
        self.assertEqual(rc, 3)


class LoggingTests(unittest.TestCase):
    def test_debug_never_logs_frames_or_dropped_fields(self) -> None:
        records: list[str] = []

        class H(logging.Handler):
            def emit(self, r):
                records.append(r.getMessage())
        h = H()
        root = logging.getLogger()
        old, old_ws = root.level, logging.getLogger("websockets").level
        root.addHandler(h)
        try:
            mc.configure_logging("DEBUG")
            self.assertEqual(root.level, logging.DEBUG)
            self.assertEqual(logging.getLogger("websockets.client").getEffectiveLevel(), logging.WARNING)
            RunTests()._run("reconnect")  # fake ws debug-logs a full vendor frame on every recv
            logging.getLogger("websockets.client").debug("> TEXT initialBuy marketCapSol")
        finally:
            root.removeHandler(h)
            root.setLevel(old)
            logging.getLogger("websockets").setLevel(old_ws)
        joined = " ".join(records)
        for bad in ("initialBuy", "marketCapSol", "TEXT", "solAmount", "Creator1"):
            self.assertNotIn(bad, joined)

    def test_attempt_error_logs_type_only_and_counts(self) -> None:
        records: list[logging.LogRecord] = []

        class H(logging.Handler):
            def emit(self, r):
                records.append(r)
        h = H()
        logging.getLogger().addHandler(h)
        try:
            with tempfile.TemporaryDirectory() as d:
                col, _ = make(d)

                async def boom(item):
                    raise ValueError("SECRETNAME leaked in message")
                col._attempt = boom
                col.on_message(create_payload(), HOUR0 + 100)
                run(col.step())
                cap = json.loads(col.store.capture_path(HK0).read_text())
        finally:
            logging.getLogger().removeHandler(h)
        self.assertEqual(cap["error_counts"], {"ValueError": 1})
        errs = [r for r in records if "attempt_error" in r.getMessage()]
        self.assertEqual(len(errs), 1)
        self.assertEqual(errs[0].getMessage(), "attempt_error type=ValueError")
        self.assertIsNone(errs[0].exc_info)
        self.assertNotIn("SECRETNAME", " ".join(r.getMessage() for r in records))


class NoHeliusTests(unittest.TestCase):
    def test_source_has_no_rpc_or_helius(self) -> None:
        text = Path(mc.__file__).read_text()
        text += Path(ipf.__file__).read_text()
        for needle in ("helius", "api-key", "api_key", "getblock", "rpc"):
            self.assertNotIn(needle, text.lower())
        self.assertEqual(re.findall(r"os\.environ\.get\(\s*\"([A-Z_]+)\"", text), ["MAL_PUMPPORTAL_WS_URL"])


class RedirectFollowTests(unittest.TestCase):
    """_urllib_get follows at most 2 allowed redirects; the single-hop request is faked, DNS is patched."""

    def run_get(self, script):
        calls = []

        def fake_once(url, timeout, max_bytes, deadline, **k):
            calls.append(url)
            step = script[len(calls) - 1]
            if isinstance(step, bytes):
                return step
            raise ipf.HttpError(step[0], location=step[1])
        with unittest.mock.patch.object(mc, "_urllib_once", fake_once), \
                unittest.mock.patch("socket.getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))]):
            return calls, mc._urllib_get("https://ipfs.io/ipfs/x", 5, 100)

    def test_follows_allowed_redirects_up_to_two(self) -> None:
        calls, body = self.run_get([(302, "https://dweb.link/a"), (301, "https://arweave.net/b"), b"doc"])
        self.assertEqual(body, b"doc")
        self.assertEqual(calls, ["https://ipfs.io/ipfs/x", "https://dweb.link/a", "https://arweave.net/b"])

    def test_third_redirect_refused_keeps_3xx(self) -> None:
        with self.assertRaises(ipf.HttpError) as cm:
            self.run_get([(302, "https://dweb.link/a"), (302, "https://dweb.link/b"), (307, "https://dweb.link/c")])
        self.assertEqual(cm.exception.status, 307)

    def test_disallowed_target_keeps_status(self) -> None:
        with self.assertRaises(ipf.HttpError) as cm:
            self.run_get([(302, "http://dweb.link/x")])  # plain http target
        self.assertEqual(mc._status_of(cm.exception), "http_302")

    def test_private_target_keeps_status(self) -> None:
        with unittest.mock.patch.object(
                mc, "_urllib_once",
                lambda *a, **k: (_ for _ in ()).throw(ipf.HttpError(302, location="https://arweave.net/x"))), \
                unittest.mock.patch("socket.getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("10.0.0.5", 443))]):
            with self.assertRaises(ipf.HttpError) as cm:
                mc._urllib_get("https://ipfs.io/ipfs/x", 5, 100)
        self.assertEqual(cm.exception.status, 302)


class GatewayChoiceTests(unittest.TestCase):
    GWS = ("https://g1/ipfs/", "https://g2/ipfs/")

    def test_first_non_cooling_gateway_and_429_cooldown(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            pool = ipf.GatewayPool(self.GWS, clock=lambda: 1000.0)

            def routes(u):
                return ipf.HttpError(429, retry_after=30) if "//g1/" in u else DOC
            col, ms = make(d, routes, pool=pool)
            col.on_message(create_payload(), HOUR0 + 100)
            run(col.step())                       # attempt 0: g1 answers 429 with Retry-After 30
            ms.t = HOUR0 + 100 + 30_100
            run(col.step())                       # attempt 1: g1 is cooling, so g2
            rows = meta_rows(col)
            self.assertEqual([(r["attempt"], r["gateway"], r["status"]) for r in rows],
                             [(0, "g1", "http_429"), (1, "g2", "ok")])
            self.assertEqual(rows[0]["uri_host_class"], "ipfs_cid")
            self.assertNotIn("uri_host", rows[0])
            self.assertEqual(col.store.gateway_counts["http_429"], 1)

    def test_rate_limited_all_makes_no_request_and_schedule_kept(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            pool = ipf.GatewayPool(self.GWS, clock=lambda: 1000.0)
            for g in self.GWS:
                pool.report(g, "http_429")
            col, ms = make(d, lambda u: DOC, pool=pool)
            col.on_message(create_payload(), HOUR0 + 100)
            for dt in (0, 30_100, 120_100, 600_100):
                ms.t = HOUR0 + 100 + dt
                run(col.step())
            rows = meta_rows(col)
            self.assertEqual([r["status"] for r in rows], ["rate_limited_all"] * 4)
            self.assertEqual([r["scheduled_ms"] - (HOUR0 + 100) for r in rows], [0, 30_000, 120_000, 600_000])
            self.assertEqual(col.http_get.calls, [])
            self.assertIsNone(rows[0]["gateway"])

    def test_token_wait_is_slept_and_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            slept = []

            async def fake_sleep(s):
                slept.append(s)
            pool = ipf.GatewayPool(("https://g1/ipfs/",), rps=1.0, burst=1.0, clock=lambda: 1000.0)
            pool.reserve(["https://g1/ipfs/"])      # drain the only token
            col, _ = make(d, lambda u: DOC, pool=pool, sleep=fake_sleep)
            col.gateways = ("https://g1/ipfs/",)
            col.on_message(create_payload(), HOUR0 + 100)
            run(col.step())
            self.assertEqual(slept[0], 1.0)
            self.assertEqual(meta_rows(col)[0]["wait_ms"], 1000)

    def test_http_scheme_refused_without_request(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            col, _ = make(d, lambda u: DOC)
            for uri in ("http://site.example/m.json", "data:text/plain,hi"):
                col.on_message(create_payload(uri=uri, mint=uri[:4] + "Z"), HOUR0 + 100)
            run(col.step())
            rows = meta_rows(col)
            self.assertEqual([(r["status"], r["uri_host_class"]) for r in rows],
                             [("non_ipfs_uri", "refused_scheme")] * 2)
            self.assertEqual(col.http_get.calls, [])

    def test_any_https_host_fetched_directly_with_own_bucket(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            pool = ipf.GatewayPool(("https://g1/ipfs/", "https://g2/ipfs/"), clock=lambda: 1000.0)

            def routes(u):
                return ipf.HttpError(429, retry_after=20) if "busy.example" in u else DOC
            col, ms = make(d, routes, pool=pool)
            col.on_message(create_payload(uri="https://Busy.example/m.json?k=v", mint="MintBusy"), HOUR0 + 100)
            col.on_message(create_payload(uri="https://meta.example/m.json", mint="MintOk"), HOUR0 + 100)
            run(col.step())
            by = {r["mint"]: r for r in meta_rows(col)}
            self.assertEqual((by["MintBusy"]["status"], by["MintBusy"]["gateway"], by["MintBusy"]["uri_host_class"]),
                             ("http_429", "busy.example", "https:busy.example"))
            self.assertEqual((by["MintOk"]["status"], by["MintOk"]["uri_host_class"]), ("ok", "https:meta.example"))
            self.assertNotIn("k=v", json.dumps(by["MintBusy"]))
            self.assertNotIn("uri_host", by["MintBusy"])
            self.assertAlmostEqual(pool.cooldown_left("https://busy.example/"), 20.0)
            self.assertEqual(pool.cooldown_left("https://meta.example/"), 0.0)  # own bucket, own cooldown
            self.assertEqual(pool.cooldown_left("https://g1/ipfs/"), 0.0)

    def test_arweave_direct_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            col, _ = make(d, lambda u: DOC if "cid1" in u or "C" * 43 in u else b"img")
            col.on_message(create_payload(uri="ar://" + "C" * 43), HOUR0 + 100)
            run(col.step())
            r = meta_rows(col)[0]
            self.assertEqual((r["status"], r["gateway"], r["uri_host_class"]), ("ok", "arweave.net", "arweave"))
            self.assertEqual(col.http_get.calls[0], "https://arweave.net/" + "C" * 43)

    def test_capture_json_records_ipfs_fetch_hash(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            col, _ = make(d)
            col.store.write_capture(HK0)
            doc = json.loads(col.store.capture_path(HK0).read_text())
            self.assertEqual(doc["ipfs_fetch_sha256"],
                             [hashlib.sha256(Path(ipf.__file__).read_bytes()).hexdigest()])
            self.assertIn("gateway_counts", doc)

    def test_schedule_constants_unchanged(self) -> None:
        self.assertEqual(mc.SCHEDULE_MS, (0, 30_000, 120_000, 600_000))
        self.assertEqual(mc.DEFAULT_GATEWAYS, ipf.DEFAULT_GATEWAYS)


class RestartCaptureTests(unittest.TestCase):
    def test_gap_log_and_versions_survive_restart(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            s1 = mc.Store(Path(d), git_ref="ref1")
            s1.tool_sha256, s1.ipfs_fetch_sha256 = "a" * 64, "b" * 64
            s1.process_start(HOUR0 + 1_000)
            s1.gap_event(HOUR0 + 2_000, "connected")
            s1.gap_event(HOUR0 + 3_000, "disconnected:OSError")
            # restart on new code in the same hour
            s2 = mc.Store(Path(d), git_ref="ref2")
            s2.tool_sha256, s2.ipfs_fetch_sha256 = "c" * 64, "d" * 64
            s2.process_start(HOUR0 + 4_000)
            s2.gap_event(HOUR0 + 5_000, "connected")
            cap = json.loads(s2.capture_path(HK0).read_text())
            events = [(g["t_ms"] - HOUR0, g["event"]) for g in cap["gap_log"]]
            self.assertEqual(events, [(1_000, "process_start"), (2_000, "connected"),
                                      (3_000, "disconnected:OSError"), (4_000, "process_start"),
                                      (5_000, "connected")])
            starts = [g for g in cap["gap_log"] if g["event"] == "process_start"]
            self.assertEqual([(g["tool_sha256"], g["ipfs_fetch_sha256"]) for g in starts],
                             [("a" * 64, "b" * 64), ("c" * 64, "d" * 64)])
            self.assertEqual(cap["tool_sha256"], ["a" * 64, "c" * 64])
            self.assertEqual(cap["ipfs_fetch_sha256"], ["b" * 64, "d" * 64])
            self.assertEqual([v["git_ref"] for v in cap["tool_versions"]], ["ref1", "ref2"])

    def test_restart_extends_earlier_unsealed_hour_and_skips_sealed(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            s1 = mc.Store(Path(d), git_ref="r")
            s1.gap_event(HOUR0 + 10, "connected")
            s1.gap_event(HOUR0 + 3_600_000 + 10, "connected")
            s1.add_create({"mint": "M", "t_recv_ms": HOUR0 + 20})
            s1.seal(HK0)
            sealed_before = s1.capture_path(HK0).read_bytes()
            s2 = mc.Store(Path(d), git_ref="r2")
            s2.process_start(HOUR0 + 3_600_000 + 500)
            self.assertEqual(s2.capture_path(HK0).read_bytes(), sealed_before)  # sealed hour untouched
            cap1 = json.loads(s2.capture_path(HK1).read_text())
            self.assertEqual([g["event"] for g in cap1["gap_log"]], ["connected", "process_start"])

    def test_legacy_single_sha_capture_becomes_a_list(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            s1 = mc.Store(Path(d))
            atomic = mc.atomic_write
            atomic(s1.capture_path(HK0), json.dumps({"hour": HK0, "tool_sha256": "e" * 64, "git_ref": "old",
                                                      "gap_log": [{"t_ms": 1, "event": "connected"}]}).encode())
            s2 = mc.Store(Path(d), git_ref="new")
            s2.process_start(HOUR0 + 5)
            cap = json.loads(s2.capture_path(HK0).read_text())
            self.assertEqual(cap["tool_sha256"], ["e" * 64, s2.tool_sha256])
            self.assertEqual(cap["gap_log"][0], {"t_ms": 1, "event": "connected"})


class FirstHopTests(unittest.TestCase):
    def test_odd_port_and_userinfo_never_requested(self) -> None:
        with unittest.mock.patch("socket.getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))]):
            for url in ("https://h.example:8443/x", "https://u:p@h.example/x"):
                with self.assertRaises(mc.BlockedUrl, msg=url):
                    mc._urllib_get(url, 2, 100)
        for uri in ("https://h.example:8443/x", "https://u:p@h.example/x"):
            self.assertEqual(mc.candidate_urls(uri, mc.DEFAULT_GATEWAYS), [], uri)


class SsrfStillRefusesTests(unittest.TestCase):
    def test_private_addresses_refused_by_default_get(self) -> None:
        for url in ("https://10.0.0.1/x", "https://169.254.169.254/latest", "https://127.0.0.1/x"):
            with self.assertRaises(mc.BlockedUrl, msg=url):
                mc._urllib_get(url, 2, 100)


LOCAL_ORIGIN = "http://127.0.0.1:8080"


class TimedHttp(FakeHttp):
    def __init__(self, routes):
        super().__init__(routes)
        self.timeouts = []

    async def __call__(self, url, timeout, max_bytes):
        self.timeouts.append((url, timeout))
        return await super().__call__(url, timeout, max_bytes)


def make_local(d, routes, clock=None, local=None, pool=None, **kw):
    clock = clock or Clock(HOUR0 + 100)
    gws = ("https://g1/ipfs/", "https://g2/ipfs/")
    local = local or ipf.parse_local_gateway(LOCAL_ORIGIN, rps=1e6, burst=1e6)
    http = TimedHttp(routes)
    col = mc.Collector(mc.Store(Path(d), git_ref="abc123"), gateways=gws, http_get=http, clock_ms=clock,
                       local=local, pool=pool or ipf.GatewayPool(gws, rps=1e6, burst=1e6), **kw)
    return col, clock, http


class LocalGatewayCaptureTests(unittest.TestCase):
    def test_schedule_unchanged_and_source_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            col, clock, http = make_local(d, lambda u: mc.HttpError(404))  # final answer, no breaker
            col.on_message(create_payload(), HOUR0 + 100)
            for dt, expect in [(0, 1), (29_999, 0), (30_100, 1), (119_000, 0),
                               (120_100, 1), (599_000, 0), (600_100, 1), (700_000, 0)]:
                clock.t = HOUR0 + 100 + dt
                self.assertEqual(run(col.step()), expect, dt)
            rows = meta_rows(col)
            self.assertEqual(mc.SCHEDULE_MS, (0, 30_000, 120_000, 600_000))
            self.assertEqual([r["attempt"] for r in rows], [0, 1, 2, 3])
            self.assertEqual([r["scheduled_ms"] - (HOUR0 + 100) for r in rows], [0, 30_000, 120_000, 600_000])
            self.assertEqual({r["gateway"] for r in rows}, {"local"})
            self.assertEqual(len(http.calls), 4)  # one request per attempt, local 404 is final, no second
            self.assertTrue(all(u.startswith(LOCAL_ORIGIN + "/ipfs/") for u in http.calls))

    def test_local_ok_uses_local_timeouts_public_keeps_10s(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            col, clock, http = make_local(d, doc_routes)
            col.on_message(create_payload(), HOUR0 + 100)
            run(col.step())
            rows = meta_rows(col)
            self.assertEqual((rows[0]["status"], rows[0]["gateway"]), ("ok", "local"))
            self.assertEqual([t for _, t in http.timeouts], [20.0, 30.0] if HAVE_PIL else [20.0, 30.0])
        with tempfile.TemporaryDirectory() as d:
            pool = ipf.GatewayPool(("https://g1/ipfs/", "https://g2/ipfs/"), rps=1e6, burst=1e6)
            col, clock, http = make_local(d, doc_routes, pool=pool)
            pool.add_pinned(col.local.key, 1e6, 1e6)
            pool.report(col.local.key, "http_429", 300)  # local cooling: public used, 10 s timeout
            col.on_message(create_payload(), HOUR0 + 100)
            run(col.step())
            self.assertEqual(meta_rows(col)[0]["gateway"], "g1")
            self.assertEqual({t for _, t in http.timeouts}, {10.0})

    def test_local_404_is_final_one_request(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            col, clock, http = make_local(d, lambda u: mc.HttpError(404))
            col.on_message(create_payload(), HOUR0 + 100)
            run(col.step())
            self.assertEqual(len(http.calls), 1)
            r = meta_rows(col)[0]
            self.assertEqual((r["status"], r["gateway"]), ("http_404", "local"))

    def test_local_429_cools_then_public(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            pool = ipf.GatewayPool(("https://g1/ipfs/", "https://g2/ipfs/"), rps=1e6, burst=1e6,
                                   clock=lambda: 1000.0)
            col, clock, http = make_local(
                d, lambda u: mc.HttpError(429, retry_after=60) if "127.0.0.1" in u else DOC, pool=pool)
            col.on_message(create_payload(), HOUR0 + 100)
            run(col.step())
            clock.t = HOUR0 + 100 + 30_100
            run(col.step())
            self.assertEqual([(r["gateway"], r["status"]) for r in meta_rows(col)[:2]],
                             [("local", "http_429"), ("g1", "ok")])

    def test_capture_json_records_origin_and_source_counts(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            col, clock, http = make_local(d, lambda u: mc.HttpError(503))
            col.on_message(create_payload(), HOUR0 + 100)
            run(col.step())
            col.store.write_capture(HK0)
            doc = json.loads(col.store.capture_path(HK0).read_text())
            self.assertEqual(doc["local_gateway"], LOCAL_ORIGIN)
            self.assertEqual(doc["source_counts"], {"local": 1})
        with tempfile.TemporaryDirectory() as d:
            col, _ = make(d)
            col.store.write_capture(HK0)
            doc = json.loads(col.store.capture_path(HK0).read_text())
            self.assertIsNone(doc["local_gateway"])

    def test_real_get_exempts_only_the_configured_origin(self) -> None:
        class H(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                if self.path.endswith("/redir"):
                    self.send_response(302)
                    self.send_header("Location", "http://127.0.0.1:%d/ipfs/x/" % self.server.server_port)
                    self.end_headers()
                    return
                if self.path.endswith("/off"):
                    self.send_response(302)
                    self.send_header("Location", "http://127.0.0.1:%d/ipfs/x" % (self.server.server_port + 1))
                    self.end_headers()
                    return
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"hello")

            def log_message(self, *a):
                pass
        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            origin = "http://127.0.0.1:%d" % srv.server_port
            self.assertEqual(mc._urllib_get(origin + "/ipfs/x", 5, 100, local_origin=origin), b"hello")
            self.assertEqual(mc._urllib_get(origin + "/ipfs/redir", 5, 100, local_origin=origin), b"hello")
            with self.assertRaises(ipf.HttpError) as cm:  # off-origin redirect is not followed
                mc._urllib_get(origin + "/ipfs/off", 5, 100, local_origin=origin)
            self.assertEqual(cm.exception.status, 302)
            other = "http://127.0.0.1:%d/ipfs/x" % (srv.server_port + 1)
            with self.assertRaises(mc.BlockedUrl):
                mc._urllib_get(other, 5, 100, local_origin=origin)
            with self.assertRaises(mc.BlockedUrl):
                mc._urllib_get("http://localhost:%d/ipfs/x" % srv.server_port, 5, 100, local_origin=origin)
            with self.assertRaises(mc.BlockedUrl):  # no exemption configured: loopback refused
                mc._urllib_get(origin + "/ipfs/x", 5, 100)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_cli_refuses_bad_local_gateway(self) -> None:
        with unittest.mock.patch.dict(sys.modules, {"websockets": types.ModuleType("websockets")}):
            with tempfile.TemporaryDirectory() as d:
                for bad in ("http://localhost:8080", "http://127.0.0.1", "https://127.0.0.1:8080"):
                    self.assertEqual(mc.main(["--out", d, "--local-gateway", bad]), 2, bad)


class PClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class LocalBreakerTests(unittest.TestCase):
    GWS = ("https://g1/ipfs/", "https://g2/ipfs/")

    def setup(self, d, routes):
        pc = PClock()
        pool = ipf.GatewayPool(self.GWS, rps=1e6, burst=1e6, clock=pc)
        col, clock, http = make_local(d, routes, pool=pool)
        return col, clock, http, pool, pc

    def test_refused_falls_through_to_public_in_same_attempt(self) -> None:
        def routes(u):
            return ipf.ConnectionRefused() if "127.0.0.1" in u else DOC
        with tempfile.TemporaryDirectory() as d:
            col, clock, http, pool, pc = self.setup(d, routes)
            col.on_message(create_payload(), HOUR0 + 100)
            run(col.step())
            r = meta_rows(col)[0]
            self.assertEqual((r["attempt"], r["status"], r["gateway"]), (0, "ok", "g1"))
            self.assertEqual(http.calls[0], LOCAL_ORIGIN + "/ipfs/cid1")
            self.assertEqual(http.calls[1], "https://g1/ipfs/cid1")
            self.assertGreater(pool.cooldown_left(col.local.key), 0)
            self.assertEqual(col.source_counts["local"], 1)

    def test_timeout_cools_ends_attempt_next_attempt_public_then_recovers(self) -> None:
        def routes(u):
            if "127.0.0.1" in u:
                return mc.FetchTimeout()
            return DOC
        with tempfile.TemporaryDirectory() as d:
            col, clock, http, pool, pc = self.setup(d, routes)
            col.on_message(create_payload(), HOUR0 + 100)
            run(col.step())                                   # attempt 0: local timeout, no fallthrough
            self.assertEqual(len(http.calls), 1)
            r0 = meta_rows(col)[0]
            self.assertEqual((r0["status"], r0["gateway"]), ("timeout", "local"))
            self.assertAlmostEqual(pool.cooldown_left(col.local.key), 30.0)
            clock.t = HOUR0 + 100 + 30_100
            pc.t += 10                                        # still cooling
            run(col.step())
            self.assertEqual(meta_rows(col)[1]["gateway"], "g1")
            self.assertEqual(meta_rows(col)[1]["status"], "ok")

    def test_local_recovers_after_cooldown_and_backoff_doubles_capped(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            col, clock, http, pool, pc = self.setup(d, lambda u: mc.HttpError(503))
            k = col.local.key
            pool.report(k, "http_503")
            self.assertAlmostEqual(pool.cooldown_left(k), 30.0)
            pc.t += 31
            self.assertEqual(pool.reserve([k, self.GWS[0]])[0], k)   # recovered
            pool.report(k, "http_503")
            self.assertAlmostEqual(pool.cooldown_left(k), 60.0)      # doubled
            for _ in range(6):
                pc.t += 400
                pool.report(k, "timeout")
            self.assertLessEqual(pool.cooldown_left(k), 300.0)
            pc.t += 301
            pool.report(k, "ok")                                      # success resets the backoff
            pool.report(k, "http_404")
            pool.report(k, "timeout")
            self.assertAlmostEqual(pool.cooldown_left(k), 30.0)

    def test_404_and_429_do_not_trip_breaker(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            col, clock, http, pool, pc = self.setup(d, lambda u: mc.HttpError(404))
            pool.report(col.local.key, "http_404")
            self.assertEqual(pool.cooldown_left(col.local.key), 0.0)
            self.assertEqual(pool.counts["local_breaker_trips"], 0)


class ProxyAndConcurrencyTests(unittest.TestCase):
    def test_opener_ignores_proxy_env(self) -> None:
        class H(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"direct")

            def log_message(self, *a):
                pass
        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            origin = "http://127.0.0.1:%d" % srv.server_port
            env = {"http_proxy": "http://127.0.0.1:9", "HTTP_PROXY": "http://127.0.0.1:9",
                   "https_proxy": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9"}
            with unittest.mock.patch.dict(os.environ, env):
                self.assertTrue(any(isinstance(h, mc.urllib.request.ProxyHandler)  # the default would proxy
                                    for h in mc.urllib.request.build_opener().handlers))
                self.assertFalse(any(isinstance(h, mc.urllib.request.ProxyHandler)
                                     for h in mc._OPENER.handlers))
                self.assertEqual(mc._urllib_get(origin + "/ipfs/x", 5, 100, local_origin=origin), b"direct")
        finally:
            srv.shutdown()
            srv.server_close()

    def test_default_concurrency(self) -> None:
        for argv, want in ((["--local-gateway", LOCAL_ORIGIN], 16), ([], 4),
                           (["--local-gateway", LOCAL_ORIGIN, "--concurrency", "3"], 3)):
            seen = {}

            class FakeCol:
                def __init__(self, *a, **k):
                    seen.update(k)

            async def fake_run(*a, **k):
                return 0
            with tempfile.TemporaryDirectory() as d, \
                    unittest.mock.patch.dict(sys.modules, {"websockets": types.ModuleType("websockets")}), \
                    unittest.mock.patch.object(mc, "Collector", FakeCol), \
                    unittest.mock.patch.object(mc, "run", fake_run):
                self.assertEqual(mc.main(["--out", d, *argv]), 0)
            self.assertEqual(seen["concurrency"], want, argv)


if __name__ == "__main__":
    unittest.main()
