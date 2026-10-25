import numpy as np

from tools import p28_counts as P


def test_impact_share_depth_bands():
    # one-way impact s/(Q+s) <= 1.5%: Q=17.6 passes at 0.25 SOL, fails at 1.0 SOL; Q=100 passes at 1.0 SOL
    r = P.impact_share([17.6, 100.0])
    assert r["0.25"] == 1.0
    assert r["1.0"] == 0.5


def test_impact_share_empty():
    assert P.impact_share([])["0.25"] is None


def test_cooldown_filter_per_pool():
    p = np.array([1, 1, 1, 2])
    t = np.array([0, 100, 400, 50])
    assert P.cooldown_filter(p, t, 300).tolist() == [True, False, True, True]
