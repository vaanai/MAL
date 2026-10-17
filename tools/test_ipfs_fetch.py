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
        self.assertEqual(c("https://i.imgur.com/x.png").host_class, "https:i.imgur.com")
        o = c("https://Evil.Example:8443/x?y=1")
        self.assertEqual((o.host_class, o.fetchable), ("refused_scheme", False))  # odd port: not fetched
        o = c("https://Evil.Example/x?y=1")
        self.assertEqual((o.host_class, o.direct_url, o.fetchable),
                         ("https:evil.example", "https://evil.example/x?y=1", True))
        self.assertEqual(c("https://evil.example:443/x").host_class, "https:evil.example")
        for bad in ("http://example.org/m.json", "file:///etc/passwd", "ftp://x/y", "data:text/plain,hi", None, ""):
            p = c(bad)
            self.assertEqual((p.host_class, p.fetchable), ("refused_scheme", False), bad)

    def test_arweave_rewrite(self) -> None:
        p = ipf.classify_uri("ar://" + "B" * 43 + "/meta.json")
        self.assertEqual((p.host_class, p.direct_url), ("arweave", "https://arweave.net/" + "B" * 43 + "/meta.json"))
        self.assertEqual(ipf.classify_uri("ar://x").host_class, "refused_scheme")  # not an id
        self.assertEqual(ipf.classify_uri("http://arweave.net/" + "A" * 43).host_class, "refused_scheme")

    def test_host_fields_record_only_what_is_ruled(self) -> None:
        self.assertEqual(ipf.host_fields(ipf.classify_uri("ipfs://c")), {"uri_host_class": "ipfs_cid"})
        self.assertEqual(ipf.host_fields(ipf.classify_uri("https://x.org/p?secret=1")),
                         {"uri_host_class": "https:x.org"})  # hostname only, no path/query
        self.assertEqual(ipf.host_fields(ipf.classify_uri("http://x.org/p")), {"uri_host_class": "refused_scheme"})

    def test_no_traversal_or_userinfo_or_odd_port(self) -> None:
        self.assertEqual(ipf.classify_uri("ipfs://cid/../../x").host_class, "refused_scheme")
        for bad in ("https://user:pw@arweave.net/" + "A" * 43, "https://u@x.org/m", "https://arweave.net:9000/x",
                    "https://x.org:8443/m"):
            self.assertEqual(ipf.classify_uri(bad).host_class, "refused_scheme", bad)
        self.assertEqual(ipf.classify_uri("https://[2001:db8::1]/m").direct_url, "https://[2001:db8::1]/m")

    def test_plan_urls(self) -> None:
        gws = ("https://g1/ipfs/", "https://g2/ipfs")
        self.assertEqual(ipf.plan_urls(ipf.classify_uri("ipfs://c/x"), gws),
                         [("https://g1/ipfs/", "https://g1/ipfs/c/x"), ("https://g2/ipfs", "https://g2/ipfs/c/x")])
        self.assertEqual(ipf.plan_urls(ipf.classify_uri("https://x.org/m"), gws),
                         [("https://x.org/", "https://x.org/m")])  # own pool key per host
        self.assertEqual(ipf.plan_urls(ipf.classify_uri("http://x.org/m"), gws), [])


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
        self.assertIsNotNone(self.target("https://cdn.example.org/other/path.json"))  # any public https host
        self.assertIsNotNone(self.target(f"https://{CID1}.ipfs.dweb.link/"))
        self.assertEqual(self.target(f"/ipfs/{CID0}"), f"https://ipfs.io/ipfs/{CID0}")  # relative

    def test_refused(self) -> None:
        for loc in ("http://ipfs.io/ipfs/" + CID0,        # not https
                    f"https://example.com:8443/ipfs/{CID0}",
                    f"https://u:p@example.com/ipfs/{CID0}",
                    "", None, "file:///etc/passwd"):
            self.assertIsNone(self.target(loc), loc)

    def test_ssrf_check_applies_to_target(self) -> None:
        def deny(url):
            raise ipf.BlockedUrl("private_address")
        self.assertIsNone(self.target("https://arweave.net/x", check=deny))

    def test_private_resolution_refused(self) -> None:
        with patch.object(socket, "getaddrinfo", lambda *a, **k: PRIVATE):
            self.assertIsNone(ipf.redirect_target("https://ipfs.io/ipfs/x", "https://arweave.net/x"))
            for url in ("https://10.0.0.1/x", "https://100.64.0.1/x", "https://224.0.0.1/x", "https://169.254.169.254/x", "https://127.0.0.1/x",
                        "https://[::1]/x", "http://example.org/x"):
                with self.assertRaises(ipf.BlockedUrl, msg=url):
                    ipf.check_url(url)
        with patch.object(socket, "getaddrinfo", lambda *a, **k: PUBLIC):
            self.assertIsNotNone(ipf.redirect_target("https://ipfs.io/ipfs/x", "https://arweave.net/x"))


class GuardAddressTests(unittest.TestCase):
    def test_non_public_classes_refused_and_public_allowed(self) -> None:
        for addr in ("10.1.2.3", "127.0.0.1", "169.254.169.254", "100.64.0.1", "100.127.255.255", "224.0.0.1",
                     "192.168.1.1", "172.16.0.1", "0.0.0.0", "::1", "fe80::1", "fc00::1", "ff02::1",
                     "::ffff:10.0.0.1", "::ffff:127.0.0.1", "::ffff:100.64.0.1", "::ffff:169.254.169.254",
                     "64:ff9b::808:808", "64:ff9b::a00:1", "64:ff9b:1::1"):
            fam = 10 if ":" in addr else 2
            with patch.object(socket, "getaddrinfo", lambda *a, a_=addr, f_=fam, **k: [(f_, 1, 6, "", (a_, 443))]):
                with self.assertRaises(ipf.BlockedUrl, msg=addr):
                    ipf.check_url("https://h.example/x")
        with patch.object(socket, "getaddrinfo", lambda *a, **k: PUBLIC):
            ipf.check_url("https://h.example/x")
        for addr in ("::ffff:8.8.8.8", "2606:4700:4700::1111"):  # public mapped v4 / public v6 stay allowed
            with patch.object(socket, "getaddrinfo", lambda *a, a_=addr, **k: [(10, 1, 6, "", (a_, 443))]):
                ipf.check_url("https://h.example/x")
        # one private address among public ones is enough to refuse
        mixed = PUBLIC + PRIVATE
        with patch.object(socket, "getaddrinfo", lambda *a, **k: mixed):
            with self.assertRaises(ipf.BlockedUrl):
                ipf.check_url("https://h.example/x")

    def test_first_hop_port_and_userinfo_refused(self) -> None:
        with patch.object(socket, "getaddrinfo", lambda *a, **k: PUBLIC):
            for url in ("https://h.example:8443/x", "https://h.example:80/x", "https://u:p@h.example/x",
                        "https://u@h.example/x"):
                with self.assertRaises(ipf.BlockedUrl, msg=url):
                    ipf.check_url(url)
            ipf.check_url("https://h.example:443/x")
            ipf.check_url("https://h.example/x")
        # tests may exempt a loopback server explicitly, port included
        ipf.check_url("http://127.0.0.1:8123/x", allow_private_hosts=frozenset({"127.0.0.1"}))

    def test_host_state_capped_and_only_idle_evicted(self) -> None:
        clk = Clk()
        pool = ipf.GatewayPool(("https://gw/ipfs/",), clock=clk)
        pool.reserve(["https://gw/ipfs/"])                        # a pinned entry in use
        keys = [f"https://h{i}.example/" for i in range(ipf.MAX_HOST_STATES)]
        for k in keys:
            pool.reserve([k])                                      # each used once: bucket not full yet
        cooling = keys[0]
        pool.report(cooling, "http_429")
        self.assertEqual(len(pool.gw), ipf.MAX_HOST_STATES + 1)
        # at the cap and nothing idle: a new host is served by an untracked one-off, nothing is evicted
        self.assertEqual(pool.reserve(["https://new1.example/"]), ("https://new1.example/", 0.0))
        self.assertEqual(len(pool.gw), ipf.MAX_HOST_STATES + 1)
        self.assertNotIn("https://new1.example/", pool.gw)
        clk.t += 10                                                # buckets refill: all idle except the cooler
        self.assertEqual(pool.reserve(["https://new2.example/"])[0], "https://new2.example/")
        self.assertIn("https://new2.example/", pool.gw)
        self.assertIn(cooling, pool.gw)                            # cooling entry kept
        self.assertIn("https://gw/ipfs/", pool.gw)                 # pinned kept
        self.assertEqual(len(pool.gw), 3)                          # pinned + cooling + new2
        self.assertGreater(pool.cooldown_left(cooling), 0)

    def test_per_host_bucket_and_cooldown_for_direct_hosts(self) -> None:
        pool = ipf.GatewayPool(ipf.DEFAULT_GATEWAYS, clock=Clk())
        (key, url), = ipf.plan_urls(ipf.classify_uri("https://meta.example/a.json"), ipf.DEFAULT_GATEWAYS)
        self.assertEqual(pool.reserve([key]), (key, 0.0))
        pool.report(key, "http_429")
        self.assertIsNone(pool.reserve([key]))
        (other, _), = ipf.plan_urls(ipf.classify_uri("https://other.example/a.json"), ipf.DEFAULT_GATEWAYS)
        self.assertEqual(pool.reserve([other]), (other, 0.0))  # a different host is unaffected


LOCAL = "http://127.0.0.1:8080"


class LocalGatewayTests(unittest.TestCase):
    def test_parse_accepts_only_literal_loopback_with_port(self) -> None:
        g = ipf.parse_local_gateway("http://127.0.0.1:8080")
        self.assertEqual((g.origin, g.key, g.rps, g.burst, g.json_timeout_s, g.image_timeout_s, g.breaker_base_s, g.breaker_max_s),
                         (LOCAL, LOCAL + "/ipfs/", 20.0, 20.0, 20.0, 30.0, 30.0, 300.0))
        self.assertEqual(ipf.parse_local_gateway("http://[::1]:8080/").origin, "http://[::1]:8080")
        for bad in ("http://localhost:8080", "http://127.0.0.2:8080", "http://127.0.0.1", "https://127.0.0.1:8080",
                    "http://u:p@127.0.0.1:8080", "http://127.0.0.1:8080@evil.example", "http://0127.0.0.1:8080",
                    "http://127.0.0.1:08080", "http://127.0.0.1:8080/ipfs/x", "http://127.0.0.1:8080?x=1",
                    "http://10.0.0.5:8080", "http://[::ffff:127.0.0.1]:8080", "http://0.0.0.0:8080",
                    "http://127.0.0.1:0", "http://127.0.0.1:99999", " http://127.0.0.1:8080"):
            with self.assertRaises(ValueError, msg=bad):
                ipf.parse_local_gateway(bad)
        with self.assertRaises(ValueError):
            ipf.parse_local_gateway(LOCAL, rps=0)

    def test_only_that_origin_is_exempt(self) -> None:
        with patch("socket.getaddrinfo", side_effect=AssertionError("no DNS for the exempt origin")):
            ipf.check_url(LOCAL + "/ipfs/" + CID0, local_origin=LOCAL)
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 8081))]):
            for url in ("http://127.0.0.1:8081/ipfs/x", "http://localhost/ipfs/x", "http://localhost:8080/ipfs/x",
                        "https://127.0.0.1:8080/ipfs/x", "http://[::1]:8080/ipfs/x", "http://127.0.0.1/ipfs/x",
                        "http://127.0.0.1:8080@evil.example/x"):
                with self.assertRaises(ipf.BlockedUrl, msg=url):
                    ipf.check_url(url, local_origin=LOCAL)
            with self.assertRaises(ipf.BlockedUrl):
                ipf.check_url(LOCAL + "/ipfs/x")  # no exemption configured
            with self.assertRaises(ipf.BlockedUrl):
                ipf.check_url("https://example.org/x", local_origin=LOCAL)  # resolves to loopback
            with self.assertRaises(ipf.BlockedUrl):
                ipf.check_url("http://u:p@127.0.0.1:8080/x", local_origin=LOCAL)

    def test_redirects_from_local(self) -> None:
        def chk(u):
            ipf.check_url(u, frozenset(), frozenset(), LOCAL)
        base = LOCAL + "/ipfs/" + CID0
        with patch("socket.getaddrinfo", return_value=PUBLIC):
            self.assertEqual(ipf.redirect_target(base, "/ipfs/" + CID0 + "/", chk, LOCAL),
                             LOCAL + "/ipfs/" + CID0 + "/")
            for loc in ("/api/v0/id", "/webui", LOCAL + "/api/v0/id", "/ipfs/../api/v0/id"):
                self.assertIsNone(ipf.redirect_target(base, loc, chk, LOCAL), loc)
            self.assertEqual(ipf.redirect_target(base, "https://ipfs.io/ipfs/" + CID0, chk, LOCAL),
                             "https://ipfs.io/ipfs/" + CID0)
            for loc in ("http://127.0.0.1:8081/ipfs/x", "http://localhost:8080/ipfs/x", "https://evil.example/x",
                        "http://[::1]:8080/ipfs/x", "http://u:p@127.0.0.1:8080/x", "https://ipfs.io/other"):
                self.assertIsNone(ipf.redirect_target(base, loc, chk, LOCAL), loc)
            # a public gateway can never bounce us to the local origin
            self.assertIsNone(ipf.redirect_target("https://ipfs.io/ipfs/x", LOCAL + "/ipfs/x", chk, LOCAL))

    def test_plan_urls_local_first_only_for_ipfs(self) -> None:
        g = ipf.parse_local_gateway(LOCAL)
        pairs = ipf.plan_urls(ipf.classify_uri("ipfs://" + CID0), ipf.DEFAULT_GATEWAYS, g)
        self.assertEqual([k for k, _ in pairs], [g.key, *ipf.DEFAULT_GATEWAYS])
        self.assertEqual(pairs[0][1], LOCAL + "/ipfs/" + CID0)
        direct = ipf.plan_urls(ipf.classify_uri("ar://" + "a" * 43), ipf.DEFAULT_GATEWAYS, g)
        self.assertEqual(len(direct), 1)
        self.assertNotIn(LOCAL, direct[0][1])

    def test_own_bucket_and_429_cooldown(self) -> None:
        pool, clk = pool_of("pub")
        g = ipf.parse_local_gateway(LOCAL, rps=20, burst=20)
        pool.add_pinned(g.key, g.rps, g.burst)
        for _ in range(20):
            self.assertEqual(pool.reserve([g.key, "pub"]), (g.key, 0.0))
        self.assertEqual(pool.reserve([g.key, "pub"]), ("pub", 0.0))  # local bucket empty, public has tokens
        clk.t += 0.06                                                  # 20 rps: one token back
        self.assertEqual(pool.reserve([g.key, "pub"]), (g.key, 0.0))
        pool.report(g.key, "http_429", 10)
        self.assertEqual(pool.reserve([g.key, "pub"])[0], "pub")       # cooling local is skipped
        self.assertEqual(pool.sources, {"local": 21, "public": 2})

    def test_fallback_rule(self) -> None:
        for st in ("timeout", "http_500", "http_503", "http_504", "http_0", "net_OSError"):
            self.assertTrue(ipf.local_may_fall_back(st), st)
        for st in ("http_404", "http_403", "http_400", "http_302", "ok", "blocked_url", "http_429"):
            self.assertFalse(ipf.local_may_fall_back(st), st)


if __name__ == "__main__":
    unittest.main()
