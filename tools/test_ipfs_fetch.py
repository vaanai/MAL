"""tools/ipfs_fetch.py tests. Fakes only: injected clock, no sleeping, no sockets (getaddrinfo is patched)."""

from __future__ import annotations

import socket
import unittest
from unittest.mock import patch

from tools import ipfs_fetch as ipf

CID0 = "Qm" + "a" * 44
CID1 = "bafy" + "a" * 55
PUBLIC = [(2, 1, 6, "", ("93.184.216.34", 443))]
PRIVATE = [(2, 1, 6, "", ("10.0.0.5", 443))]


class Clk:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def pool_of(*keys, rps=1.0, burst=2.0):
    clk = Clk()
    return ipf.GatewayPool(keys, rps=rps, burst=burst, clock=clk), clk


class BucketTests(unittest.TestCase):
    def test_defaults_pinned(self) -> None:
        self.assertEqual((ipf.DEFAULT_RPS, ipf.DEFAULT_BURST, ipf.MAX_TOKEN_WAIT_S), (1.0, 2.0, 5.0))
        self.assertEqual((ipf.RETRY_AFTER_CAP_S, ipf.RETRY_AFTER_DEFAULT_S, ipf.MAX_REDIRECTS),
                         (300.0, 60.0, 2))
        self.assertEqual(ipf.DEFAULT_GATEWAYS, ("https://gateway.pinata.cloud/ipfs/",
                                                "https://ipfs.io/ipfs/", "https://dweb.link/ipfs/"))

    def test_burst_then_refill(self) -> None:
        pool, clk = pool_of("a")
        self.assertEqual(pool.reserve(["a"]), ("a", 0.0))
        self.assertEqual(pool.reserve(["a"]), ("a", 0.0))
        self.assertAlmostEqual(pool.reserve(["a"])[1], 1.0)
        clk.t += 100
        self.assertEqual(pool.reserve(["a"]), ("a", 0.0))  # refilled, capped at burst

    def test_wait_over_five_seconds_is_rate_limited_all(self) -> None:
        pool, _ = pool_of("a")
        waits = [pool.reserve(["a"])[1] for _ in range(7)]  # 2 free, then 1 s .. 5 s
        self.assertEqual(waits, [0.0, 0.0, 1.0, 2.0, 3.0, 4.0, 5.0])
        self.assertIsNone(pool.reserve(["a"]))              # 6 s would exceed the window
        self.assertEqual(pool.counts["rate_limited_all"], 1)

    def test_prefers_ready_gateway_in_pinned_order(self) -> None:
        pool, _ = pool_of("a", "b")
        pool.reserve(["a"])
        pool.reserve(["a"])
        self.assertEqual(pool.reserve(["a", "b"]), ("b", 0.0))
        self.assertEqual(pool.reserve(["a", "b"]), ("b", 0.0))
        key, wait = pool.reserve(["a", "b"])                # none ready: first in pinned order
        self.assertEqual((key, wait > 0), ("a", True))

    def test_bad_args(self) -> None:
        with self.assertRaises(ValueError):
            ipf.GatewayPool(("a",), rps=0)


class CooldownTests(unittest.TestCase):
    def test_429_default_60s_then_backoff_cap_and_reset(self) -> None:
        pool, clk = pool_of("a", "b")
        pool.report("a", "http_429")
        self.assertAlmostEqual(pool.cooldown_left("a"), 60.0)
        self.assertEqual(pool.reserve(["a", "b"])[0], "b")  # a skipped while cooling
        for expect in (120.0, 240.0, 300.0, 300.0):
            clk.t += pool.cooldown_left("a") + 1
            pool.report("a", "http_429")
            self.assertAlmostEqual(pool.cooldown_left("a"), expect)
        clk.t += 301
        pool.report("a", "ok")                              # success resets the strikes
        pool.report("a", "http_429")
        self.assertAlmostEqual(pool.cooldown_left("a"), 60.0)

    def test_concurrent_429s_count_as_one_strike(self) -> None:
        pool, _ = pool_of("a")
        for _ in range(4):
            pool.report("a", "http_429")
        self.assertAlmostEqual(pool.cooldown_left("a"), 60.0)
        self.assertEqual(pool.counts["http_429"], 4)

    def test_retry_after_honoured_and_capped(self) -> None:
        pool, _ = pool_of("a", "b", "c", "d")
        pool.report("a", "http_429", 7)
        pool.report("b", "http_429", 9999)
        pool.report("c", "http_503", 12)
        pool.report("d", "http_429", 0)
        self.assertAlmostEqual(pool.cooldown_left("a"), 7.0)
        self.assertAlmostEqual(pool.cooldown_left("b"), 300.0)
        self.assertAlmostEqual(pool.cooldown_left("c"), 12.0)
        self.assertAlmostEqual(pool.cooldown_left("d"), 1.0)  # floor

    def test_retry_after_backs_off_on_repeat(self) -> None:
        pool, clk = pool_of("a")
        pool.report("a", "http_429", 10)
        clk.t += 11
        pool.report("a", "http_429", 10)
        self.assertAlmostEqual(pool.cooldown_left("a"), 20.0)

    def test_503_without_retry_after_and_other_statuses_do_not_cool(self) -> None:
        pool, _ = pool_of("a")
        for st in ("http_503", "http_404", "timeout", "http_0", "http_302"):
            pool.report("a", st)
        self.assertFalse(pool.cooling("a"))

    def test_all_cooling_is_rate_limited_all(self) -> None:
        pool, clk = pool_of("a", "b")
        pool.report("a", "http_429")
        pool.report("b", "http_429")
        self.assertIsNone(pool.reserve(["a", "b"]))
        clk.t += 61
        self.assertIsNotNone(pool.reserve(["a", "b"]))

    def test_parse_retry_after(self) -> None:
        self.assertEqual(ipf.parse_retry_after("30"), 30.0)
        self.assertIsNone(ipf.parse_retry_after(None))
        self.assertIsNone(ipf.parse_retry_after("soon"))
        # 2026-10-21 07:28:30 UTC = 1792567710
        self.assertEqual(ipf.parse_retry_after("Wed, 21 Oct 2026 07:28:30 GMT", now=lambda: 1_792_567_700.0), 10.0)
        self.assertEqual(ipf.parse_retry_after("Wed, 21 Oct 2026 07:28:30 GMT", now=lambda: 1_792_568_000.0), 0.0)


class ClassifyTests(unittest.TestCase):
    def test_classes(self) -> None:
        c = ipf.classify_uri
        self.assertEqual(c("ipfs://" + CID0).host_class, "ipfs_cid")
        self.assertEqual(c("https://ipfs.io/ipfs/" + CID0 + "/x.json").host_class, "ipfs_cid")
        self.assertEqual(c("https://evil.example/ipfs/" + CID0).ipfs_path, CID0)  # rewritten, never contacted
        self.assertEqual(c(f"https://{CID1}.ipfs.dweb.link/m.json").ipfs_path, CID1 + "/m.json")
        self.assertEqual(c("https://arweave.net/" + "A" * 43).host_class, "arweave")
        self.assertEqual(c("https://gateway.irys.xyz/" + "A" * 43).host_class, "arweave")
        self.assertEqual(c("https://i.imgur.com/x.png").host_class, "allowlisted:i.imgur.com")
        o = c("https://Evil.Example:8443/x?y=1")
        self.assertEqual((o.host_class, o.host, o.fetchable), ("other", "evil.example", False))
        self.assertEqual(c("file:///etc/passwd").host_class, "other")
        self.assertEqual(c(None).host_class, "other")

    def test_arweave_rewrite(self) -> None:
        p = ipf.classify_uri("ar://" + "B" * 43 + "/meta.json")
        self.assertEqual((p.host_class, p.direct_url), ("arweave", "https://arweave.net/" + "B" * 43 + "/meta.json"))
        self.assertEqual(ipf.classify_uri("ar://x").host_class, "other")  # not an id
        self.assertEqual(ipf.classify_uri("http://arweave.net/" + "A" * 43).direct_url,
                         "https://arweave.net/" + "A" * 43)               # pinned host: upgraded to https

    def test_host_fields_record_only_what_is_ruled(self) -> None:
        self.assertEqual(ipf.host_fields(ipf.classify_uri("ipfs://c")), {"uri_host_class": "ipfs_cid"})
        self.assertEqual(ipf.host_fields(ipf.classify_uri("https://i.imgur.com/a.png")),
                         {"uri_host_class": "allowlisted:i.imgur.com"})
        self.assertEqual(ipf.host_fields(ipf.classify_uri("https://x.org/p?secret=1")),
                         {"uri_host_class": "other", "uri_host": "x.org"})  # hostname only, no path/query

    def test_no_traversal_or_userinfo_or_odd_port(self) -> None:
        self.assertEqual(ipf.classify_uri("ipfs://cid/../../x").host_class, "other")
        self.assertEqual(ipf.classify_uri("https://user:pw@arweave.net/" + "A" * 43).host_class, "other")
        self.assertFalse(ipf.classify_uri("https://arweave.net:9000/" + "A" * 43).fetchable)

    def test_plan_urls(self) -> None:
        gws = ("https://g1/ipfs/", "https://g2/ipfs")
        self.assertEqual(ipf.plan_urls(ipf.classify_uri("ipfs://c/x"), gws),
                         [("https://g1/ipfs/", "https://g1/ipfs/c/x"), ("https://g2/ipfs", "https://g2/ipfs/c/x")])
        self.assertEqual(ipf.plan_urls(ipf.classify_uri("https://x.org/"), gws), [])


class RedirectRuleTests(unittest.TestCase):
    @staticmethod
    def ok(url):
        return None

    def target(self, loc, base="https://ipfs.io/ipfs/x", check=None):
        return ipf.redirect_target(base, loc, check or self.ok)

    def test_allowed(self) -> None:
        self.assertEqual(self.target(f"https://example.com/ipfs/{CID0}/a.json"),
                         f"https://example.com/ipfs/{CID0}/a.json")
        self.assertIsNotNone(self.target("https://arweave.net/whatever"))
        self.assertIsNotNone(self.target("https://dweb.link/anything"))
        self.assertIsNotNone(self.target(f"https://{CID1}.ipfs.dweb.link/"))
        self.assertEqual(self.target(f"/ipfs/{CID0}"), f"https://ipfs.io/ipfs/{CID0}")  # relative

    def test_refused(self) -> None:
        for loc in ("http://ipfs.io/ipfs/" + CID0,        # not https
                    "https://example.com/ipfs/notacid",   # invalid cid
                    "https://example.com/other",          # neither path nor gateway host
                    "https://i.imgur.com/x.png",          # metadata host is not a gateway host
                    f"https://example.com:8443/ipfs/{CID0}",
                    f"https://u:p@example.com/ipfs/{CID0}",
                    f"https://example.com/ipfs/{CID0}/../x",
                    "", None, "file:///etc/passwd"):
            self.assertIsNone(self.target(loc), loc)

    def test_ssrf_check_applies_to_target(self) -> None:
        def deny(url):
            raise ipf.BlockedUrl("private_address")
        self.assertIsNone(self.target("https://arweave.net/x", check=deny))

    def test_private_resolution_refused(self) -> None:
        with patch.object(socket, "getaddrinfo", lambda *a, **k: PRIVATE):
            self.assertIsNone(ipf.redirect_target("https://ipfs.io/ipfs/x", "https://arweave.net/x"))
            for url in ("https://10.0.0.1/x", "https://169.254.169.254/x", "https://127.0.0.1/x",
                        "https://[::1]/x", "http://example.org/x"):
                with self.assertRaises(ipf.BlockedUrl, msg=url):
                    ipf.check_url(url)
        with patch.object(socket, "getaddrinfo", lambda *a, **k: PUBLIC):
            self.assertIsNotNone(ipf.redirect_target("https://ipfs.io/ipfs/x", "https://arweave.net/x"))


if __name__ == "__main__":
    unittest.main()
