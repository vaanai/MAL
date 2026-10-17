"""meta_capture tests. Fakes only: no socket, no HTTP, no Helius."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from tools import meta_capture as mc

try:
    import PIL  # noqa: F401
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False

HOUR0 = 1_760_000_000_000 - (1_760_000_000_000 % 3_600_000)  # an exact hour
MINT = "MintAAAA111"
DROPPED = {
    "initialBuy": 1.5, "solAmount": 2.0, "tokenAmount": 9, "vTokensInBonding": 1,
    "vSolInBonding": 30, "marketCapSol": 28, "price": 0.1, "bondingCurve": "BC",
    "signature": "sig", "reserve": 5,
}


def create_payload(**extra):
    p = {"txType": "create", "mint": MINT, "name": "N", "symbol": "S",
         "traderPublicKey": "Creator1", "uri": "ipfs://cid1", "pool": "pump",
         "timestamp": 1_760_000_000, **DROPPED}
    p.update(extra)
    return p


class AllowlistTests(unittest.TestCase):
    def test_dropped_fields_never_reach_disk(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            store = mc.Store(Path(d))
            row = mc.create_row(create_payload(), 123)
            assert row is not None
            store.add_create(row, HOUR0)
            text = store.creates_path(mc.hour_key(HOUR0)).read_text()
            for key, val in DROPPED.items():
                self.assertNotIn(key, text)
            self.assertEqual(set(row) - {"schema"}, set(mc.CREATE_ALLOW) | {"t_recv_ms"})
            self.assertEqual(row["creator"], "Creator1")
            self.assertEqual(row["t_recv_ms"], 123)

    def test_non_create_dropped(self) -> None:
        self.assertIsNone(mc.create_row({"txType": "buy", "mint": MINT}, 1))
        self.assertIsNone(mc.create_row({"txType": "create"}, 1))


class UriTests(unittest.TestCase):
    GW = ["https://a.example/ipfs/", "https://b.example/ipfs"]

    def test_ipfs_scheme(self) -> None:
        self.assertEqual(mc.candidate_urls("ipfs://cid/x.json", self.GW),
                         ["https://a.example/ipfs/cid/x.json", "https://b.example/ipfs/cid/x.json"])

    def test_known_gateway_rewritten(self) -> None:
        self.assertEqual(mc.candidate_urls("https://ipfs.io/ipfs/cid", self.GW)[0],
                         "https://a.example/ipfs/cid")

    def test_plain_https_untouched_and_junk_rejected(self) -> None:
        self.assertEqual(mc.candidate_urls("https://x.org/m.json", self.GW), ["https://x.org/m.json"])
        self.assertEqual(mc.candidate_urls("file:///etc/passwd", self.GW), [])


class JsonTests(unittest.TestCase):
    def test_allowlist_and_description_cut(self) -> None:
        doc = {"name": "n", "symbol": "s", "description": "x" * 5000, "twitter": "t",
               "telegram": "g", "website": "w", "image": "ipfs://i", "createdOn": "pump.fun",
               "showName": True, "attributes": [1], "price": 3, "initialBuy": 4}
        out = mc.extract_meta(doc)
        self.assertEqual(len(out["description"]), 2000)
        self.assertEqual(set(out), {"name", "symbol", "description", "twitter", "telegram",
                                    "website", "image", "createdOn", "showName"})


class ImageTests(unittest.TestCase):
    @unittest.skipUnless(HAVE_PIL, "Pillow not installed")
    def test_downsample(self) -> None:
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (640, 480), (255, 0, 0)).save(buf, format="PNG")
        png, info = mc.process_image(buf.getvalue())
        assert png is not None
        self.assertEqual(Image.open(io.BytesIO(png)).size, (224, 224))
        self.assertEqual(info["img_bytes"], len(buf.getvalue()))

    def test_fallback_without_pillow(self) -> None:
        import sys
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


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class FetchStatusTests(unittest.TestCase):
    GW = ("https://g1/ipfs/", "https://g2/ipfs/")

    def test_ok_first_gateway(self) -> None:
        http = FakeHttp(lambda u: b"{}")
        st, body = run(mc.fetch_bytes("ipfs://c", self.GW, http, 100))
        self.assertEqual((st, body), ("ok", b"{}"))
        self.assertEqual(http.calls, ["https://g1/ipfs/c"])

    def test_retry_falls_to_next_gateway_max_three(self) -> None:
        http = FakeHttp(lambda u: mc.HttpError(503) if "g1" in u else mc.FetchTimeout())
        st, body = run(mc.fetch_bytes("ipfs://c", self.GW, http, 100))
        self.assertEqual((st, body), ("http_503", None))  # attempt 3 wraps to g1
        self.assertEqual(len(http.calls), 3)
        self.assertEqual(http.calls[:2], ["https://g1/ipfs/c", "https://g2/ipfs/c"])

    def test_http_status_and_too_big(self) -> None:
        http = FakeHttp(lambda u: mc.HttpError(404))
        self.assertEqual(run(mc.fetch_bytes("ipfs://c", self.GW, http, 100))[0], "http_404")
        big = FakeHttp(lambda u: b"x" * 50)
        st, _ = run(mc.fetch_bytes("ipfs://c", self.GW, big, 10))
        self.assertEqual(st, "too_big")
        self.assertEqual(len(big.calls), 1)

    def _collect(self, routes, **kw):
        d = tempfile.mkdtemp()
        col = mc.Collector(mc.Store(Path(d)), gateways=self.GW, http_get=FakeHttp(routes),
                           clock_ms=lambda: HOUR0 + 5, **kw)

        async def go():
            col.on_message(create_payload(), HOUR0)
            await col.drain()
        run(go())
        meta = [json.loads(x) for x in col.store.meta_path(mc.hour_key(HOUR0)).read_text().splitlines()]
        return col, meta[0]

    def test_bad_json(self) -> None:
        _, m = self._collect(lambda u: b"<html>")
        self.assertEqual(m["status"], "bad_json")

    def test_end_to_end_ok_and_disk_guard(self) -> None:
        doc = json.dumps({"name": "n", "image": "ipfs://img", "initialBuy": 9}).encode()
        routes = lambda u: doc if u.endswith("/c1") or "cid1" in u else b"imgbytes"
        col, m = self._collect(routes)
        self.assertEqual((m["status"], m["img_status"]), ("ok", "ok"))
        self.assertNotIn("initialBuy", m)
        self.assertEqual(m["img_sha256"], hashlib.sha256(b"imgbytes").hexdigest() if not HAVE_PIL else m["img_sha256"])
        calls_ok = len(col.http_get.calls)
        # disk guard: image not fetched
        col2, m2 = self._collect(routes, min_free_gb=10**9)
        self.assertEqual(m2["img_status"], "skipped_disk")
        self.assertEqual(len(col2.http_get.calls), calls_ok - 1)


class RolloverTests(unittest.TestCase):
    def test_manifest_on_hour_close(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            store = mc.Store(Path(d))
            r1 = mc.create_row(create_payload(), 1)
            r2 = mc.create_row(create_payload(mint="MintBBB"), 2)
            assert r1 and r2
            store.add_create(r1, HOUR0 + 10)
            store.add_meta({"mint": MINT, "status": "ok"}, b"PNG", HOUR0 + 20)
            self.assertFalse((Path(d) / "manifest.jsonl").exists())
            store.add_create(r2, HOUR0 + 3_600_000 + 1)  # next hour closes the first
            lines = (Path(d) / "manifest.jsonl").read_text().splitlines()
            self.assertEqual(len(lines), 1)
            e = json.loads(lines[0])
            h0 = mc.hour_key(HOUR0)
            self.assertEqual(e["hour"], h0)
            self.assertTrue(e["hour_complete"])
            self.assertEqual((e["creates_n"], e["meta_n"], e["img_n"]), (1, 1, 1))
            self.assertEqual(e["creates_sha256"],
                             hashlib.sha256((Path(d) / f"creates-{h0}.jsonl").read_bytes()).hexdigest())
            self.assertTrue((Path(d) / "img" / h0 / f"{MINT}.png").exists())
            self.assertTrue(store.creates_path(mc.hour_key(HOUR0 + 3_600_000)).exists())
            self.assertFalse(list(Path(d).rglob("*.tmp")))

    def test_resume_appends_same_hour_file(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            r = mc.create_row(create_payload(), 1)
            assert r
            mc.Store(Path(d)).add_create(r, HOUR0)
            mc.Store(Path(d)).add_create(r, HOUR0 + 1)
            self.assertEqual(len(mc.Store(Path(d)).creates_path(mc.hour_key(HOUR0)).read_text().splitlines()), 2)


class GuardTests(unittest.TestCase):
    def test_rss_guard_stops_fetching(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            http = FakeHttp(lambda u: b"{}")
            col = mc.Collector(mc.Store(Path(d)), http_get=http, rss_fn=lambda: 2000.0,
                               max_rss_mb=1500, clock_ms=lambda: HOUR0)

            async def go():
                col.on_message(create_payload(), HOUR0)
                await col.drain()
            run(go())
            self.assertTrue(col.rss_exceeded)
            self.assertEqual(http.calls, [])

    def test_rss_reader_and_free_space_are_numbers(self) -> None:
        self.assertGreaterEqual(mc.rss_mb(), 0.0)
        self.assertGreater(mc.free_gb(Path(tempfile.gettempdir())), 0.0)


class NoHeliusTests(unittest.TestCase):
    def test_source_has_no_rpc_or_helius(self) -> None:
        src = Path(mc.__file__).read_text().lower()
        for needle in ("helius", "api-key", "api_key", "getblock", "rpc"):
            self.assertNotIn(needle, src)
        # the only environment variable read is the websocket url
        import re
        self.assertEqual(re.findall(r"os\.environ\.get\(\s*\"([A-Z_]+)\"", Path(mc.__file__).read_text()),
                         ["MAL_PUMPPORTAL_WS_URL"])


if __name__ == "__main__":
    unittest.main()
