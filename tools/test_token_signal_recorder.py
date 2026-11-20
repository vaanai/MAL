"""Tests for tools/token_signal_recorder.py. No live network: a fake transport serves fixtures.

fixtures/token_signal_recorder/pump_coin_ok.json is a synthetic shape (pump.fun
answers automated clients with a challenge, so no real body was recorded)."""
import http.client
import json
import os
import sys
import threading
import unittest
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import token_signal_recorder as t  # noqa: E402

FX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "fixtures", "token_signal_recorder")
SOL = "So11111111111111111111111111111111111111112"
OTHER = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def fx(name):
    with open(os.path.join(FX, name), "rb") as fh:
        return fh.read()


class Fake:
    def __init__(self, routes):
        self.routes = routes  # list of (substring, [responses])
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        for sub, resp in self.routes:
            if sub in url:
                r = resp.pop(0) if len(resp) > 1 else resp[0]
                return r
        return 404, {}, b"{}"


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def make(tmp, fetch, pump=False, cap=400, mints=None, tries=3):
    mf = None
    if mints is not None:
        mf = os.path.join(tmp, "mints.txt")
        with open(mf, "w") as fh:
            fh.write("# c\n" + "\n".join(mints) + "\nnot-a-mint\n" + (mints[0] if mints else "") + "\n")
    clk = Clock()
    c = t.Client(fetch=fetch, sleep=clk.sleep, clock=clk, max_calls_per_hour=cap, tries=tries)
    return t.Recorder(c, os.path.join(tmp, "out"), mf, pump=pump), clk


def rows(tmp):
    out = []
    d = os.path.join(tmp, "out")
    for n in sorted(os.listdir(d)):
        with open(os.path.join(d, n)) as fh:
            out += [json.loads(x) for x in fh]
    return out


OK = (200, {}, b"[]")
PRICE_WORDS = ("usdPrice", "liquidity", "priceChange", "Volume", "fdv", "mcap")


def lst(*ids):
    return json.dumps([{"id": i, "organicScore": 50.0 + n, "organicScoreLabel": "medium",
                        "isVerified": False, "holderCount": 10 + n, "usdPrice": 1.0,
                        "liquidity": 5.0} for n, i in enumerate(ids)]).encode()


def lists_fake(trend, organic, extra=()):
    return Fake([("toptrending", [(200, {}, lst(*trend))]), ("toporganicscore", [(200, {}, lst(*organic))]),
                 ("search", [(200, {}, fx("jupiter_search_sol.json"))]), *extra])


class T(unittest.TestCase):
    def test_read_mints_dedupes_and_validates(self):
        with tempfile.TemporaryDirectory() as tmp:
            rec, _ = make(tmp, Fake([]), mints=[SOL, OTHER])
            self.assertEqual(t.read_mints(rec.mints_file), [SOL, OTHER])

    def test_full_lists_recorded_without_mints_file(self):
        f = lists_fake([OTHER, SOL], [SOL])
        with tempfile.TemporaryDirectory() as tmp:
            rec, _ = make(tmp, f)
            info = rec.cycle()
            self.assertEqual(info["errors"], [])
            r = rows(tmp)
            ls = [x for x in r if x["source"] == "jupiter_list"]
            self.assertEqual([(x["fields"]["list"], x["fields"]["rank"], x["mint"]) for x in ls],
                             [("toptrending", 0, OTHER), ("toptrending", 1, SOL), ("toporganicscore", 0, SOL)])
            tok = {x["mint"]: x for x in r if x["source"] == "jupiter_token"}
            self.assertEqual(set(tok), {SOL, OTHER})  # search covers the union of list mints
            self.assertTrue(tok[SOL]["fields"]["found"])
            self.assertIn("strict", tok[SOL]["fields"]["tags"])
            self.assertEqual(tok[OTHER]["fields"], {"found": False})
            self.assertEqual(len(f.urls), 3)
            self.assertTrue(all("limit=100" in u for u in f.urls if "top" in u))

    def test_no_price_or_outcome_fields_anywhere(self):
        f = lists_fake([SOL], [SOL])
        with tempfile.TemporaryDirectory() as tmp:
            rec, _ = make(tmp, f)
            rec.cycle()
            raw = "".join(json.dumps(x) for x in rows(tmp))
            for w in PRICE_WORDS:
                self.assertNotIn(w, raw)

    def test_header_every_cycle_with_pins(self):
        f = lists_fake([SOL], [SOL])
        with tempfile.TemporaryDirectory() as tmp:
            rec, _ = make(tmp, f, mints=[OTHER])
            rec.cycle()
            rec.cycle()
            h = [x["fields"] for x in rows(tmp) if x["source"] == "recorder"]
            self.assertEqual(len(h), 2)
            self.assertEqual(h[0]["kind"], "cycle")
            self.assertNotEqual(h[0]["cycle_id"], h[1]["cycle_id"])
            self.assertEqual(len(h[0]["tool_sha256"]), 64)
            self.assertEqual(h[0]["tool_sha256"], t.sha256_file(t.__file__))
            self.assertEqual(h[0]["mints_file_sha256"], t.sha256_file(rec.mints_file))
            self.assertEqual(len(h[0]["lists_sha256"]), 64)
            self.assertEqual(h[0]["n_list_mints"], 1)
            self.assertEqual(h[0]["n_file_mints"], 1)
            self.assertEqual(h[0]["lists_ok"], [True, True])
            self.assertEqual(h[0]["calls"], 3)
            self.assertIn("tool_git", h[0])

    def test_batches_of_100(self):
        ids = ["".join(chr(97 + (i // d) % 10) for d in (1000, 100, 10, 1)).ljust(44, "A") for i in range(150)]
        f = Fake([("toptrending", [(200, {}, lst(*ids[:100]))]),
                  ("toporganicscore", [(200, {}, lst(*ids[50:150]))]), ("search", [OK])])
        with tempfile.TemporaryDirectory() as tmp:
            rec, _ = make(tmp, f)
            rec.cycle()
            self.assertEqual(sum("search" in u for u in f.urls), 2)

    def test_min_gap_enforced(self):
        f = Fake([("", [OK])])
        with tempfile.TemporaryDirectory() as tmp:
            rec, clk = make(tmp, f, mints=[SOL])
            rec.cycle()
            self.assertGreaterEqual(clk.t, 2 * 2.5)

    def test_retry_then_ok(self):
        f = Fake([("toptrending", [(429, {"Retry-After": "3"}, b"x"), OK]), ("", [OK])])
        with tempfile.TemporaryDirectory() as tmp:
            rec, _ = make(tmp, f)
            info = rec.cycle()
            self.assertEqual(info["errors"], [])
            self.assertEqual(sum("toptrending" in u for u in f.urls), 2)

    def test_hourly_cap(self):
        f = Fake([("", [OK])])
        with tempfile.TemporaryDirectory() as tmp:
            rec, _ = make(tmp, f, cap=1)
            info = rec.cycle()
            self.assertTrue(info["capped"])
            self.assertEqual(len(f.urls), 1)
            h = [x for x in rows(tmp) if x["source"] == "recorder"]
            self.assertTrue(h[0]["fields"]["capped"])

    def test_cap_window_slides(self):
        clk = Clock()
        c = t.Client(fetch=Fake([("", [OK])]), sleep=clk.sleep, clock=clk, max_calls_per_hour=1, min_gap_s=0)
        c.get_json("u")
        with self.assertRaises(t.CapReached):
            c.get_json("u")
        clk.t += 3600
        c.get_json("u")

    def test_pump_challenge_disables_source(self):
        f = Fake([("pump.fun", [(403, {}, fx("pump_challenge.html"))]), ("", [OK])])
        with tempfile.TemporaryDirectory() as tmp:
            rec, _ = make(tmp, f, pump=True, mints=[SOL, OTHER, "B" * 44])
            rec.cycle()
            self.assertEqual(sum("pump.fun" in u for u in f.urls), 3)
            blocked = [x for x in rows(tmp) if x["source"] == "pump_coin"]
            self.assertEqual(len(blocked), 1)
            self.assertTrue(blocked[0]["fields"]["blocked"])
            rec.cycle()
            self.assertEqual(sum("pump.fun" in u for u in f.urls), 3)

    def test_pump_5xx_trips_with_cap_across_cycles(self):  # reviewer B2
        f = Fake([("pump.fun", [(503, {}, b'{"e":1}')]), ("", [OK])])
        with tempfile.TemporaryDirectory() as tmp:
            rec, _ = make(tmp, f, pump=True, mints=[SOL])
            for _ in range(6):
                rec.cycle()
            self.assertEqual(rec.pump_blocks, 3)
            self.assertEqual(sum("pump.fun" in u for u in f.urls), 3 * 3)  # 3 blocks x 3 tries, then off

    def test_pump_429_html_is_immediate_block(self):
        f = Fake([("pump.fun", [(429, {}, fx("pump_challenge.html"))]), ("", [OK])])
        with tempfile.TemporaryDirectory() as tmp:
            rec, _ = make(tmp, f, pump=True, mints=[SOL])
            rec.cycle()
            self.assertEqual(sum("pump.fun" in u for u in f.urls), 1)

    def test_pump_ok_rows(self):
        f = Fake([("pump.fun", [(200, {}, fx("pump_coin_ok.json"))]), ("", [OK])])
        with tempfile.TemporaryDirectory() as tmp:
            rec, _ = make(tmp, f, pump=True, mints=[SOL, OTHER])
            rec.cycle()
            p = [x for x in rows(tmp) if x["source"] == "pump_coin"]
            self.assertEqual(len(p), 2)
            self.assertEqual(p[0]["fields"]["reply_count"], 7)

    def test_pump_off_by_default(self):
        f = Fake([("", [OK])])
        with tempfile.TemporaryDirectory() as tmp:
            rec, _ = make(tmp, f, mints=[SOL])
            rec.cycle()
            self.assertFalse(any("pump.fun" in u for u in f.urls))

    def test_html_200_is_block(self):
        c = t.Client(fetch=lambda u: (200, {}, b"<html>"), sleep=lambda s: None)
        with self.assertRaises(t.Blocked):
            c.get_json("u")

    def test_incomplete_read_is_retried_not_fatal(self):  # reviewer B1
        calls = []

        def fetch(u):
            calls.append(u)
            if len(calls) == 1:
                raise http.client.IncompleteRead(b"x")
            return OK
        c = t.Client(fetch=fetch, sleep=lambda s: None, min_gap_s=0)
        self.assertEqual(c.get_json("u"), [])
        self.assertEqual(len(calls), 2)
        c2 = t.Client(fetch=lambda u: (_ for _ in ()).throw(http.client.IncompleteRead(b"x")),
                      sleep=lambda s: None, min_gap_s=0)
        with self.assertRaises(RuntimeError):
            c2.get_json("u")

    def test_missing_mints_file_keeps_last_good(self):
        f = Fake([("", [OK])])
        with tempfile.TemporaryDirectory() as tmp:
            rec, _ = make(tmp, f, mints=[SOL])
            rec.cycle()
            os.remove(rec.mints_file)
            info = rec.cycle()
            self.assertTrue(any("mints_file" in e for e in info["errors"]))
            h = [x["fields"] for x in rows(tmp) if x["source"] == "recorder"]
            self.assertEqual(h[-1]["n_file_mints"], 1)
            self.assertIsNone(h[-1]["mints_file_sha256"])

    def test_malformed_list_elements(self):
        bad = json.dumps([1, None, "x", {"id": 5}, {"id": SOL, "organicScore": 9}]).encode()
        f = Fake([("toptrending", [(200, {}, bad)]), ("toporganicscore", [(200, {}, b'{"a":1}')]),
                  ("search", [(200, {}, json.dumps([3, {"id": SOL, "stats1h": "x"}]).encode())])])
        with tempfile.TemporaryDirectory() as tmp:
            rec, _ = make(tmp, f)
            info = rec.cycle()
            self.assertEqual(info["errors"], [])
            r = rows(tmp)
            self.assertEqual(len([x for x in r if x["source"] == "jupiter_list"]), 1)
            self.assertEqual(info["lists_ok"], [True, False])

    def test_run_survives_cycle_exception(self):
        f = Fake([("", [OK])])
        with tempfile.TemporaryDirectory() as tmp:
            rec, clk = make(tmp, f)
            n = {"i": 0}
            orig = rec._jupiter

            def boom(*a):
                n["i"] += 1
                if n["i"] == 1:
                    raise ValueError("boom")
                return orig(*a)
            rec._jupiter = boom
            rec.c.stop.wait = lambda s: clk.sleep(s) or (n["i"] >= 3 and rec.c.stop.set())
            self.assertEqual(t.run(rec, 900), 0)
            self.assertGreaterEqual(n["i"], 3)
            h = [x["fields"] for x in rows(tmp) if x["source"] == "recorder"]
            self.assertIn("ValueError", h[0]["errors"][0])

    def test_run_survives_write_failure(self):
        f = Fake([("", [OK])])
        with tempfile.TemporaryDirectory() as tmp:
            rec, clk = make(tmp, f)
            n = {"i": 0}

            def bad_write(*a, **k):
                n["i"] += 1
                raise OSError("disk")
            rec.write = bad_write
            rec.c.stop.wait = lambda s: n["i"] >= 3 and rec.c.stop.set()
            self.assertEqual(t.run(rec, 900), 0)

    def test_max_hours_ends_run(self):
        f = Fake([("", [OK])])
        with tempfile.TemporaryDirectory() as tmp:
            rec, clk = make(tmp, f)
            rec.c.stop.wait = lambda s: clk.sleep(s)
            self.assertEqual(t.run(rec, 900, max_hours=1), 0)
            self.assertLessEqual(clk.t, 3600 + 900 + 60)
            self.assertGreaterEqual(len([x for x in rows(tmp) if x["source"] == "recorder"]), 3)

    def test_stop_event_exits_run(self):
        f = Fake([("", [OK])])
        with tempfile.TemporaryDirectory() as tmp:
            rec, _ = make(tmp, f)
            rec.c.stop.set()
            self.assertEqual(t.run(rec, 900), 0)
            self.assertEqual(f.urls, [])

    def test_stop_after_min_gap_sleep_sends_no_call(self):  # N2
        clk = Clock()
        stop = threading.Event()
        calls = []
        c = t.Client(fetch=lambda u: calls.append(u) or OK, sleep=lambda s: (clk.sleep(s), stop.set()),
                     clock=clk, stop=stop)
        c.get_json("a")
        with self.assertRaises(InterruptedError):
            c.get_json("b")
        self.assertEqual(calls, ["a"])

    def test_user_agent_generic(self):
        self.assertNotIn("@", t.UA)
        self.assertTrue(t.UA.startswith("token-signal-recorder/"))


if __name__ == "__main__":
    unittest.main()
