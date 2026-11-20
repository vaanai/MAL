"""Tests for tools/token_signal_recorder.py. No live network: a fake transport serves fixtures.

fixtures/token_signal_recorder/pump_coin_ok.json is a synthetic shape (pump.fun
answers automated clients with a challenge, so no real body was recorded)."""
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


def make(tmp, fetch, pump=False, cap=400, mints=(SOL, OTHER)):
    mf = os.path.join(tmp, "mints.txt")
    with open(mf, "w") as fh:
        fh.write("# c\n" + "\n".join(mints) + "\nnot-a-mint\n" + SOL + "\n")
    clk = Clock()
    c = t.Client(fetch=fetch, sleep=clk.sleep, clock=clk, max_calls_per_hour=cap)
    return t.Recorder(c, os.path.join(tmp, "out"), mf, pump=pump), clk


def rows(tmp):
    out = []
    d = os.path.join(tmp, "out")
    for n in sorted(os.listdir(d)):
        with open(os.path.join(d, n)) as fh:
            out += [json.loads(x) for x in fh]
    return out


OK = (200, {}, b"[]")


class T(unittest.TestCase):
    def test_read_mints_dedupes_and_validates(self):
        with tempfile.TemporaryDirectory() as tmp:
            rec, _ = make(tmp, Fake([]))
            self.assertEqual(t.read_mints(rec.mints_file), [SOL, OTHER])

    def test_jupiter_rows(self):
        trend = json.dumps([{"id": OTHER}, {"id": SOL}]).encode()
        f = Fake([("toptrending", [(200, {}, trend)]), ("toporganicscore", [OK]),
                  ("search", [(200, {}, fx("jupiter_search_sol.json"))])])
        with tempfile.TemporaryDirectory() as tmp:
            rec, _ = make(tmp, f)
            info = rec.cycle()
            self.assertEqual(info["errors"], [])
            r = rows(tmp)
            tok = {x["mint"]: x for x in r if x["source"] == "jupiter_token"}
            self.assertEqual(set(tok), {SOL, OTHER})
            self.assertTrue(tok[SOL]["fields"]["found"])
            self.assertIn("organicScore", tok[SOL]["fields"])
            self.assertIn("strict", tok[SOL]["fields"]["tags"])
            self.assertEqual(tok[SOL]["fields"]["stats1h"].keys(), set(t.STATS_KEYS))
            self.assertEqual(tok[OTHER]["fields"], {"found": False})
            rk = {x["mint"]: x["fields"] for x in r if x["source"] == "jupiter_rank"}
            self.assertEqual(rk[SOL]["trending_rank"], 1)
            self.assertIsNone(rk[SOL]["organic_rank"])
            self.assertEqual(rk[OTHER]["trending_rank"], 0)
            self.assertTrue(all(x["ts"].endswith("Z") for x in r))
            self.assertEqual(len(f.urls), 3)  # 2 lists + 1 search batch

    def test_batches_of_100(self):
        mints = ["A" * 40 + "".join(chr(97 + (i // d) % 10) for d in (1000, 100, 10, 1)) for i in range(150)]
        f = Fake([("search", [OK]), ("top", [OK])])
        with tempfile.TemporaryDirectory() as tmp:
            rec, _ = make(tmp, f, mints=mints)
            rec.cycle()
            self.assertEqual(sum("search" in u for u in f.urls), 2)

    def test_min_gap_enforced(self):
        f = Fake([("", [OK])])
        with tempfile.TemporaryDirectory() as tmp:
            rec, clk = make(tmp, f)
            rec.cycle()
            self.assertGreaterEqual(clk.t, 2 * 2.5)  # 3 calls -> 2 gaps

    def test_retry_then_ok(self):
        f = Fake([("toptrending", [(429, {"Retry-After": "3"}, b"x"), OK]),
                  ("", [OK])])
        with tempfile.TemporaryDirectory() as tmp:
            rec, _ = make(tmp, f)
            info = rec.cycle()
            self.assertEqual(info["errors"], [])
            self.assertEqual(sum("toptrending" in u for u in f.urls), 2)

    def test_hourly_cap(self):
        f = Fake([("", [OK])])
        with tempfile.TemporaryDirectory() as tmp:
            rec, _ = make(tmp, f, cap=2)
            info = rec.cycle()
            self.assertTrue(info["capped"])
            self.assertEqual(len(f.urls), 2)
            st = [x for x in rows(tmp) if x["source"] == "recorder"]
            self.assertTrue(st[0]["fields"]["capped"])

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
            rec, _ = make(tmp, f, pump=True, mints=(SOL, OTHER, "B" * 44))
            rec.cycle()
            self.assertEqual(sum("pump.fun" in u for u in f.urls), 3)
            blocked = [x for x in rows(tmp) if x["source"] == "pump_coin"]
            self.assertEqual(len(blocked), 1)
            self.assertTrue(blocked[0]["fields"]["blocked"])
            n = len(f.urls)
            rec.cycle()  # disabled now: no more pump calls
            self.assertEqual(sum("pump.fun" in u for u in f.urls), 3)
            self.assertGreater(len(f.urls), n)

    def test_pump_ok_rows(self):
        f = Fake([("pump.fun", [(200, {}, fx("pump_coin_ok.json"))]), ("", [OK])])
        with tempfile.TemporaryDirectory() as tmp:
            rec, _ = make(tmp, f, pump=True)
            rec.cycle()
            p = [x for x in rows(tmp) if x["source"] == "pump_coin"]
            self.assertEqual(len(p), 2)
            self.assertEqual(p[0]["fields"]["reply_count"], 7)
            self.assertIs(p[0]["fields"]["is_currently_live"], False)

    def test_pump_off_by_default(self):
        f = Fake([("", [OK])])
        with tempfile.TemporaryDirectory() as tmp:
            rec, _ = make(tmp, f)
            rec.cycle()
            self.assertFalse(any("pump.fun" in u for u in f.urls))

    def test_html_200_is_block(self):
        c = t.Client(fetch=lambda u: (200, {}, b"<html>"), sleep=lambda s: None)
        with self.assertRaises(t.Blocked):
            c.get_json("u")

    def test_stop_event_exits_run(self):
        f = Fake([("", [OK])])
        with tempfile.TemporaryDirectory() as tmp:
            rec, _ = make(tmp, f)
            rec.c.stop.set()
            self.assertEqual(t.run(rec, 900), 0)
            self.assertEqual(f.urls, [])

    def test_sigterm_flag_stops_between_cycles(self):
        f = Fake([("", [OK])])
        with tempfile.TemporaryDirectory() as tmp:
            rec, _ = make(tmp, f)
            rec.c.sleep = lambda s: None
            orig = rec.cycle

            def once():
                r = orig()
                rec.c.stop.set()  # as the SIGTERM handler would
                return r
            rec.cycle = once
            self.assertEqual(t.run(rec, 0.01), 0)

    def test_user_agent_generic(self):
        self.assertNotIn("@", t.UA)
        self.assertTrue(t.UA.startswith("token-signal-recorder/"))


if __name__ == "__main__":
    unittest.main()
