"""Tests for the exit-rule walk of tools/damm_grad_price.py on a synthetic series (no data, no network)."""
import unittest
from types import SimpleNamespace

import numpy as np

from tools import damm_grad_price as G


def ser(prices, slots=None):
    n = len(prices)
    slots = np.arange(100, 100 + n) if slots is None else np.array(slots)
    s = SimpleNamespace(slot=slots, p=np.array(prices, float), t0=1000, cov_bt=10**9)
    s.at = lambda x: int(np.searchsorted(s.slot, x, "right")) - 1
    return s


class Rule(unittest.TestCase):
    def test_tp_fires_at_first_print_over_threshold(self):
        s = ser([1.0, 1.1, 1.3, 1.6, 2.0, 1.0])
        r = G.ser_rule(s, 100, 1.0, 1.0, 1.5, None, None, 10**6, False, 0.4, 5)
        self.assertEqual((r["fire_slot"], r["reason"]), (103, "tp"))
        self.assertEqual(r["jx"], 5)  # landing 5 slots later = after the last print

    def test_fired_pre_exits_at_first_damm_print(self):
        s = ser([0.5, 0.6, 0.7])
        r = G.ser_rule(s, 100, 1.0, 1.0, 1.5, 0.9, 0.9, 10**6, True, 0.4, 1)
        self.assertEqual((r["fire_slot"], r["reason"]), (100, "fired_pre"))

    def test_cap_and_sl(self):
        s = ser([1.0, 0.95, 0.85, 0.5])
        r = G.ser_rule(s, 100, 1.0, 1.0, None, 0.9, None, 10**6, False, 0.4, 1)
        self.assertEqual((r["fire_slot"], r["reason"]), (102, "sl"))
        r = G.ser_rule(s, 100, 1.0, 1.0, None, None, None, 101, False, 0.4, 1)
        self.assertEqual((r["fire_slot"], r["reason"]), (101, "time"))

    def test_history_end_refused(self):
        s = ser([1.0, 1.0])
        s.cov_bt = 1002
        self.assertIsNone(G.ser_rule(s, 100, 1.0, 1.0, None, None, None, 101, False, 0.4, 5))


if __name__ == "__main__":
    unittest.main()
