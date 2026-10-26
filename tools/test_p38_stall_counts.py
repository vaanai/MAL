"""Tests for the P38 stall trigger, continuity check and kill-rule logic (synthetic, no tape)."""
import unittest

from tools import p38_stall_counts as s


def q_at(p):
    return (30.0 + 85.0 * p) * 1e9


class TestTrigger(unittest.TestCase):
    END = 10**9

    def test_fires_after_20_min_by_time_passage(self):
        T = [1000, 1600, 2000]
        Q = [q_at(0.91), q_at(0.915), q_at(0.92)]
        # anchor at 1000 (p .91); at c = 2200 window = [1000-print, prints <= 2200] -> range .01 < .02
        self.assertEqual(s.find_trigger(T, Q, None, None, self.END), 2200)

    def test_band_too_wide_then_settles(self):
        T = [1000, 1500, 3000]
        Q = [q_at(0.90), q_at(0.93), q_at(0.931)]
        # windows anchored at 1000 include .90 and .93 (range .03) -> fail; anchor 1500 at c = 2700 holds
        self.assertEqual(s.find_trigger(T, Q, None, None, self.END), 2700)

    def test_below_090_never_fires(self):
        T = [1000, 3000]
        Q = [q_at(0.899), q_at(0.899)]
        self.assertIsNone(s.find_trigger(T, Q, None, None, self.END))

    def test_complete_before_decision_blocks(self):
        T = [1000]
        Q = [q_at(0.95)]
        self.assertIsNone(s.find_trigger(T, Q, None, 2100, self.END))
        # complete in the same second as the decision is still a trigger (counted as a race)
        self.assertEqual(s.find_trigger(T, Q, None, 2200, self.END), 2200)

    def test_unclean_print_blocks(self):
        T = [1000]
        Q = [q_at(0.95)]
        self.assertIsNone(s.find_trigger(T, Q, 1500, None, self.END))
        self.assertEqual(s.find_trigger(T, Q, 2300, None, self.END), 2200)

    def test_block_end(self):
        self.assertIsNone(s.find_trigger([1000], [q_at(0.95)], None, None, 2200))

    def test_completing_print_in_window_blocks(self):
        T = [1000, 1100]
        Q = [q_at(0.995), 115.004e9]
        self.assertIsNone(s.find_trigger(T, Q, None, None, self.END))


class TestChain(unittest.TestCase):
    def test_chain(self):
        T = [10, 20, 30, 40]
        self.assertTrue(s.chain_ok(T, [False, True, True, True], 15, 40))
        self.assertFalse(s.chain_ok(T, [False, True, False, True], 15, 40))
        self.assertFalse(s.chain_ok(T, [True, True, True, True], 5, 40))  # no anchor
        self.assertTrue(s.chain_ok(T, [True, True, True, False], 15, 35))


class TestVerdict(unittest.TestCase):
    def base(self, **kw):
        d = {"median_triggers_per_full_day": 9, "uncensored": 100, "race_share_4.4s": 0.05,
             "race_share_1.9s": 0.02, "p_complete_6h": 0.6}
        d.update(kw)
        return d

    def test_advance(self):
        self.assertEqual(s.verdict(self.base())["verdict"], "ADVANCE-TO-PRICING-DECLARE")

    def test_kills(self):
        self.assertEqual(s.verdict(self.base(median_triggers_per_full_day=7.5))["failed"], ["K0"])
        self.assertEqual(s.verdict(self.base(**{"race_share_4.4s": 0.11}))["failed"], ["K0b"])
        self.assertEqual(s.verdict(self.base(p_complete_6h=0.49))["failed"], ["K0c"])

    def test_thin(self):
        v = s.verdict(self.base(uncensored=29))
        self.assertEqual(v["verdict"], "NOT_DECIDABLE")


class TestSeal(unittest.TestCase):
    def test_manifest_refusal(self):
        man = [{"kind": "trades", "hour": "2026-08-14T12", "block": "explore-0814",
                "src": "/data/mal/clean-view/fresh-0808/w1/trades/x.jsonl.zst"},
               {"kind": "migrations", "hour": "2026-08-14T12", "block": "explore-0814",
                "src": "/data/mal/clean-view/explore-0814/w7/migrations/x.jsonl.zst"}]
        with self.assertRaises(SystemExit):
            s.check_manifest(man, "explore-0814", ["2026-08-14T12"])

    def test_hours(self):
        h = s.hour_list("2026-09-03T12", "2026-09-09T12")
        self.assertEqual(len(h), 144)
        self.assertEqual(h[0], "2026-09-03T12")


if __name__ == "__main__":
    unittest.main()
