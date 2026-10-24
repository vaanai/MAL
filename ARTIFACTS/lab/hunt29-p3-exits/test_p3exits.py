"""Synthetic tests for p3exits (no market data). Run: KIT=<worktree> python3 -m pytest -q test_p3exits.py"""
import os, sys
sys.path.insert(0, os.environ.get("KIT", "/data2/mal/wt/p3-exits"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, "/data/mal/hunt-1008/push-1016/hunt24/l4-audit")
import numpy as np
import tools.arena_screen as S
import p3exits as X
ORIG = S._price_exit  # captured before any patch import


def pool(n=400, seed=1, drift=0.0, vol=0.02):
    rng = np.random.default_rng(seed)
    q, b, v = 0.4e9, 3e14, 17.58e9
    sl = np.cumsum(rng.integers(1, 6, n)) + 1000
    ib, so, tk, Q, B, V = [], [], [], [], [], []
    for _ in range(n):
        buy = rng.random() < 0.5 + drift
        sol = float(rng.lognormal(np.log(0.3e9), 1.0))
        Qe = q + v
        if buy:
            tok = b * sol * 0.9875 / (Qe + sol * 0.9875)
        else:
            tok = b * sol / (Qe - sol) if Qe > 2 * sol else b * 0.001
        ib.append(buy); so.append(sol); tk.append(tok); Q.append(q); B.append(b); V.append(v)
        q = q + sol * 0.9875 if buy else max(q - sol, 0.01e9)
        b = b - tok if buy else b + tok
    return dict(sl=sl.astype(np.int64), ib=np.array(ib), so=np.array(so), tk=np.array(tk), q=np.array(Q), b=np.array(B), v=np.array(V))


def run(spec, a, sd=1100, rate=2.5):
    return X.exit_p3(a, S.post_price(a), sd, spec, rate, 1.0 / rate)


def test_tp_matches_tpfix():
    import l1_rule_tpfix  # patches S._price_exit with the reference; compare against it directly
    ref = l1_rule_tpfix._price_exit_fix
    X.install()
    for seed in range(20):
        a = pool(seed=seed, drift=0.08)
        spec = S.ExitSpec(300.0, tp=1.5)
        assert ref(a, S.post_price(a), 1100, spec, 2.5, 0.4) == run(spec, a), seed


def test_time_only_is_kit():
    X.install()
    a = pool()
    spec = S.ExitSpec(120.0, cell="T6")
    assert run(spec, a) == ORIG(a, S.post_price(a), 1100, spec, 2.5, 0.4)


def test_sl_trail_is_kit():
    X.install()
    a = pool(seed=3)
    spec = S.ExitSpec(300.0, sl=0.88, trail=0.85, cell="RC_r2h")
    assert run(spec, a) == ORIG(a, S.post_price(a), 1100, spec, 2.5, 0.4)


def test_causal_truncation():
    """Cutting the rows after the exit slot must not move the exit (trail, burst, tp)."""
    X.install()
    cells = {"TR30": S.ExitSpec(600.0, cell="TR30"), "B2": S.ExitSpec(600.0, tp=1.5, cell="B2"), "BR": S.ExitSpec(600.0, tp=1.5, cell="BR")}
    X.register("B2", burst=0.8e9)  # a low threshold so the burst fires in the synthetic tape
    X.register("TR30", trail_arm=1.02, trail=0.97)
    X.register("BR", burst_rel=1.2, burst_min=0.5e9)
    fired = 0
    for seed in range(30):
        a = pool(seed=seed, drift=0.1)
        for c, spec in cells.items():
            xs, why, _ = run(spec, a)
            m = int(np.searchsorted(a["sl"], xs, "right"))
            cut = {k: v[:m] for k, v in a.items()}
            xs2, why2, _ = run(spec, cut)
            assert (xs, why) == (xs2, why2), (seed, c)
            fired += why == 3
    assert fired > 0  # the test exercised the trail / burst branch


def test_burst_code_and_window():
    X.install()
    a = pool(seed=5, drift=0.2)
    X.register("B2", burst=0.5e9)
    xs, why, _ = run(S.ExitSpec(600.0, tp=1.5, cell="B2"), a)
    assert why in (0, 1, 3)
    j = int(np.searchsorted(a["sl"], xs, "right")) - 1
    if why == 3:
        W = int(round(60 * 2.5))
        w = (a["sl"] > a["sl"][j] - W) & (np.arange(len(a["sl"])) <= j)
        assert a["so"][w & a["ib"]].sum() >= 0.5e9
