"""Tests for tools/r4b_features.py: synthetic-mint unit tests, plus E-a (fixture reproduction) when the exploration data is on disk.

numpy-only for the unit tests. E-a needs duckdb + pandas (run with /data/mal/c1nf-parity-venv/bin/python -m pytest) and is skipped when the
data path, duckdb or pandas is missing.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

from tools import r4b_features as rf

REPO = Path(__file__).resolve().parents[1]

MBT = 1_790_000_000 + 50          # not a multiple of 120 on purpose
CLK0 = MBT - 200
SPS = 2                           # slots per second in the synthetic clock
SLOT0 = 5_000_000


def slot_of_bt(b):
    return SLOT0 + (b - CLK0) * SPS


def make_prep(gr=None, gr_first_slot=None, end=MBT + 49 * 3600):
    bt = np.arange(CLK0, end, dtype=np.int64)
    slot = slot_of_bt(bt)
    m0 = (CLK0 // 60) * 60
    mnt = np.arange(m0, end, 60, dtype=np.int64)
    n = len(mnt)
    cols = {"n_ps": np.ones(n), "v_ps": np.full(n, 2e9), "n_bd": np.full(n, 3.0), "v_bd": np.full(n, 5e8), "n_cr": np.full(n, 4.0)}
    return rf.build_prep(bt, slot, mnt, cols, gr=np.array([MBT] if gr is None else gr), gr_first_slot=gr_first_slot)


def make_prints(n=1500, seed=3, gap=100, first=30):
    rng = np.random.default_rng(seed)
    bt = MBT + first + np.arange(n) * gap
    so = rng.integers(2e8, 3e9, n).astype(np.int64)
    ib = rng.random(n) < 0.55
    q = 40e9 + np.cumsum(np.where(ib, so, -so) * 0.3)
    q = np.maximum(q, 5e9)
    bb = 1.0e14 * 40e9 / q
    tk = (so * bb / q * 0.9).astype(np.int64)
    th = rng.integers(0, 120, n).astype(np.uint64)
    return dict(slot=slot_of_bt(bt) + 1, bt=bt, isbuy=ib, sol=so, tok=tk, q=q, b=bb, th=th)


def const(**kw):
    d = dict(mid=7, mbt=MBT, mslot=slot_of_bt(MBT) - 3, s0=slot_of_bt(MBT) + 4, v=17.58e9, cbt=float(MBT - 900), has_creator=True, nb=12.0,
             ntr=5.0, bbuy=3e9, bsell=1e9, cre_sell=2e8)
    d.update(kw)
    return rf.MintConst(**d)


def run(prep, pr, c=None, ch=None, **kw):
    c = c or const()
    ch = pr["th"][0] if ch is None else ch
    return rf.mint_features(prep, c, ch, pr["slot"], pr["bt"], pr["isbuy"], pr["sol"], pr["tok"], pr["q"], pr["b"], pr["th"], **kw)


@pytest.fixture(scope="module")
def prep():
    return make_prep()


def test_feature_order_and_dtype(prep):
    assert len(rf.FEATS) == 63 and len(set(rf.FEATS)) == 63
    assert rf.MODEL_FEATURES[0] == "f_age_s" and rf.MODEL_FEATURES[-1] == "f_c_v"
    ft = Path("/data/mal/hunt-1008/laya-opt/frozen/r4-b/features.txt")
    if ft.exists():
        assert ft.read_text().split() == rf.MODEL_FEATURES
    res = run(prep, make_prints(), seg_end=None)
    assert res.X.dtype == np.float32 and res.X.shape == (len(res.I), 63) and res.I.dtype == np.int64
    assert res.I.shape[1] == 3


def test_grid_edges_forward_and_replay(prep):
    pr = make_prints(n=2000, gap=100)
    res = run(prep, pr, seg_end=None)
    T = res.I[:, 1]
    assert (T % 120 == 0).all()
    assert T.min() >= ((MBT + 600 + 119) // 120) * 120
    assert T.max() <= MBT + 48 * 3600
    assert (T - MBT).min() >= 600
    # seg_end: grid stops at seg_end - 4h - 600
    seg_end = MBT + 20 * 3600
    res2 = run(prep, pr, seg_end=seg_end)
    assert res2.I[:, 1].max() <= seg_end - 4 * 3600 - 600
    assert res2.I[:, 1].max() > seg_end - 4 * 3600 - 600 - 120 - 100   # prints are dense enough to reach the cut
    # the shared grid points are identical
    k = np.isin(res.I[:, 1], res2.I[:, 1])
    assert np.array_equal(res.X[k], res2.X, equal_nan=True)
    # a seg_end too early gives no grid
    assert run(prep, pr, seg_end=MBT + 4 * 3600 + 600) is None


def test_first_grid_point_is_ceil120(prep):
    pr = make_prints(gap=20, first=5)
    res = run(prep, pr, seg_end=None)
    assert res.I[0, 1] == ((MBT + 600 + 119) // 120) * 120
    # mbt exactly on a grid boundary: t_lo = mbt + 600 itself
    mbt2 = (MBT // 120) * 120 + 120
    assert ((mbt2 + 600 + 119) // 120) * 120 == mbt2 + 600


def test_only_prints_before_sd_are_used(prep):
    pr = make_prints(n=300, gap=100, first=30)
    res = run(prep, pr, seg_end=None)
    T, sd = res.I[:, 1], res.I[:, 2]
    # clock: sd is the first slot whose block_time >= T
    assert np.array_equal(sd, slot_of_bt(T))
    # (a) a new print AT slot == sd for some T must not change that row
    i = len(T) // 2
    # insert a print exactly at slot sd[i] (block_time T[i]) in order
    pos = int(np.searchsorted(pr["slot"], sd[i]))
    ins = {k: np.insert(v, pos, v[pos - 1]) for k, v in pr.items()}
    ins["slot"][pos] = sd[i]
    ins["bt"][pos] = T[i]
    res_a = run(prep, ins, seg_end=None)
    ia = np.nonzero(res_a.I[:, 1] == T[i])[0]
    ib_ = np.nonzero(T == T[i])[0]
    assert len(ia) == 1 and len(ib_) == 1
    assert np.array_equal(res_a.X[ia[0]], res.X[ib_[0]], equal_nan=True)
    # (b) a print at slot sd - 1 IS used (np_all grows by one)
    ins2 = {k: np.insert(v, pos, v[pos - 1]) for k, v in pr.items()}
    ins2["slot"][pos] = sd[i] - 1
    ins2["bt"][pos] = T[i] - 1
    # keep slot order: previous print must be < sd - 1
    assert ins2["slot"][pos - 1] < ins2["slot"][pos]
    res_b = run(prep, ins2, seg_end=None)
    ib2 = np.nonzero(res_b.I[:, 1] == T[i])[0][0]
    npall = rf.FEATS.index("np_all")
    assert res_b.X[ib2, npall] == res.X[ib_[0], npall] + 1


def test_decision_needs_a_print_in_last_10_minutes(prep):
    pr = make_prints(n=400, gap=100)
    # remove a stretch of prints so a decision gap opens
    keep = np.ones(400, bool)
    keep[100:200] = False
    pr2 = {k: v[keep] for k, v in pr.items()}
    res = run(prep, pr2, seg_end=None)
    T = res.I[:, 1]
    last_bt = pr2["bt"]
    for t in T:
        k = np.searchsorted(last_bt, t, "left") - 1
        assert k >= 0 and t - 600 <= last_bt[k] < t
    gap_lo, gap_hi = pr["bt"][99] + 600, pr["bt"][200]
    assert not ((T > gap_lo) & (T < gap_hi)).any()
    # t_since_last stays below 600 for every decision
    assert (res.X[:, rf.FEATS.index("t_since_last")] <= 600).all()


def test_complete_minute_rule():
    # minutes: values 1,2,3,... one per minute starting at m0; window [t-w, t) includes only whole minutes below floor(t)
    m0 = 1_790_000_040
    mnt = m0 + 60 * np.arange(100)
    vals = np.arange(1, 101, dtype=float)
    z = np.zeros(100)
    p = rf.build_prep([m0], [1], mnt, {"n_ps": vals, "v_ps": z, "n_bd": z, "v_bd": z, "n_cr": z}, gr=[0])
    for t, w, lo, hi in ((m0 + 600, 600, 0, 10), (m0 + 600 + 59, 600, 0, 10), (m0 + 600 + 59, 3600 - 3000, 0, 10), (m0 + 660, 600, 1, 11)):
        want = vals[lo:hi].sum()
        assert p.mwin("n_ps", np.array([t]), w)[0] == want, (t, w)
    # mid-minute t: the minute containing t is NOT counted, and the window start is floored: [t-600, t) with t = m0+630 -> minutes 0..9
    assert p.mwin("n_ps", np.array([m0 + 630]), 600)[0] == vals[0:10].sum()
    # a window before the first minute is clipped to zero
    assert p.mwin("n_ps", np.array([m0 - 5000]), 600)[0] == 0
    # beyond the last minute the sum saturates at the total
    assert p.mwin("n_ps", np.array([m0 + 60 * 500]), 60 * 500)[0] == vals.sum()


def test_prep_from_rows_matches_arrays():
    clock = {"bt": np.array([10, 10, 11, 12, 12]), "smin": np.array([105, 100, 110, 121, 120])}
    mkt = {"mnt": np.array([0, 60, 0]), "n_ps": [1, 2, 3], "v_ps": [10, 20, 30], "n_bd": [0, 0, 1], "v_bd": [0, 0, 5]}
    cr = {"mnt": np.array([60, 120]), "n_cr": [9, 8]}
    p = rf.build_prep_from_rows(clock, mkt, cr, gr=[5, 1])
    assert list(p.clock_bt) == [10, 11, 12] and list(p.clock_slot) == [100, 110, 120]
    assert p.m0 == 0 and p.nm == 2
    assert list(p.cum["n_ps"]) == [0, 4, 6]
    assert list(p.cum["n_cr"]) == [0, 0, 9]
    assert list(p.gr) == [1, 5]
    assert p.slot_at(np.array([11, 12, 13]))[0] == 110 and p.slot_at(np.array([99]))[0] == 120   # clamped to last second


def test_constant_vq_equals_none(prep):
    pr = make_prints()
    c = const()
    a = run(prep, pr, c=c, seg_end=None, vq=None)
    b = run(prep, pr, c=c, seg_end=None, vq=np.full(len(pr["slot"]), c.v))
    assert a.X.tobytes() == b.X.tobytes() and np.array_equal(a.I, b.I)


def test_varying_vq_changes_pool_features_but_not_constants(prep):
    pr = make_prints()
    c = const()
    a = run(prep, pr, c=c, seg_end=None)
    vq = np.full(len(pr["slot"]), c.v)
    vq[len(vq) // 2:] += 2e9
    b = run(prep, pr, c=c, seg_end=None, vq=vq, return_state=True)
    assert np.array_equal(a.I, b.I)
    assert not np.array_equal(a.X, b.X, equal_nan=True)
    i = rf.FEATS.index("c_v")
    assert np.array_equal(a.X[:, i], b.X[:, i])                    # c_v uses V0, not the per-row V
    liq = rf.FEATS.index("liq_sol")
    late = a.I[:, 1] > pr["bt"][len(vq) // 2] + 1
    assert (b.X[late, liq] > a.X[late, liq]).all()
    # derived final state uses the last row's V
    ra = run(prep, pr, c=c, seg_end=None, return_state=True)
    assert b.state["QX"][-1] - ra.state["QX"][-1] == pytest.approx(2e9)


def test_v0_sets_seed_price_and_c_v(prep):
    pr = make_prints()
    a = run(prep, pr, c=const(v=17.5e9), seg_end=None)
    b = run(prep, pr, c=const(v=17.7e9), seg_end=None, vq=np.full(len(pr["slot"]), 17.5e9))
    r_seed = rf.FEATS.index("r_seed")
    c_v = rf.FEATS.index("c_v")
    assert np.allclose(a.X[:, c_v], 17.5) and np.allclose(b.X[:, c_v], 17.7)
    assert not np.array_equal(a.X[:, r_seed], b.X[:, r_seed])


def test_grads_rule_frozen_default_and_asof(prep):
    gr = np.array([MBT, MBT + 3000, MBT + 3300, MBT + 3400])
    # first-print slots: grad 2 prints late (after the decision near it), grad 3 never prints (NaN)
    fs = np.array([slot_of_bt(MBT + 10), slot_of_bt(MBT + 3000) + 2, slot_of_bt(MBT + 4000), np.nan])
    p = make_prep(gr=gr, gr_first_slot=fs)
    pr = make_prints(gap=50)
    base = run(p, pr, seg_end=None)
    default = run(p, pr, seg_end=None, grads_rule="frozen")
    assert base.X.tobytes() == default.X.tobytes()
    asof = run(p, pr, seg_end=None, grads_rule="asof_pda_first_print")
    g10, g1h = rf.FEATS.index("m_grads_10m"), rf.FEATS.index("m_grads_1h")
    others = [i for i in range(63) if i not in (g10, g1h)]
    assert np.array_equal(base.X[:, others], asof.X[:, others], equal_nan=True)
    T, sd = base.I[:, 1], base.I[:, 2]
    for i in range(len(T)):
        want1 = sum(1 for g, f in zip(gr, fs) if T[i] - 3600 <= g < T[i] and f < sd[i])
        want10 = sum(1 for g, f in zip(gr, fs) if T[i] - 600 <= g < T[i] and f < sd[i])
        assert asof.X[i, g1h] == want1 and asof.X[i, g10] == want10
        assert base.X[i, g1h] == sum(1 for g in gr if T[i] - 3600 <= g < T[i])
    assert (asof.X[:, g1h] <= base.X[:, g1h]).all() and (asof.X[:, g1h] < base.X[:, g1h]).any()
    # never-printed graduation is never counted
    assert asof.X[:, g1h].max() <= 3
    with pytest.raises(ValueError):
        run(make_prep(gr=gr), pr, seg_end=None, grads_rule="asof_pda_first_print")
    with pytest.raises(ValueError):
        run(p, pr, seg_end=None, grads_rule="nope")


def test_forward_order_total_and_null_txi_groups():
    slot = np.array([5, 5, 5, 5, 4])
    txi = np.array([np.nan, np.nan, 3, np.nan, np.nan])
    evi = np.array([1, 1, 0, 2, 0])
    frn = np.array([40, 30, 20, 10, 50])
    perm = rf.forward_order(slot, txi, evi, frn)
    # slot 4 first; slot 5: non-null tx 3 (idx2) BEFORE the nulls; nulls by evi then frn: (evi1,frn30)=1,(evi1,frn40)=0,(evi2)=3
    assert list(perm) == [4, 2, 1, 0, 3]
    assert rf.mixed_null_txi_slots(slot, txi) == 1              # slot 5 mixes null and non-null
    assert rf.mixed_null_txi_slots(slot, np.array([1, 2, 3, 4, 5.0])) == 0
    assert rf.mixed_null_txi_slots(np.array([1, 1, 2]), np.array([np.nan, np.nan, 3.0])) == 0
    assert rf.null_txi_tie_groups(slot, txi, evi) == 1          # idx 0 and 1 share (slot 5, evi 1)
    assert rf.null_txi_tie_groups(slot, np.array([1, 2, 3, 4, 5.0]), evi) == 0
    with pytest.raises(ValueError):
        rf.forward_order(slot, txi, evi, None)
    with pytest.raises(ValueError):
        rf.forward_order(slot, txi, evi, np.array([1, 1, 2, 3, 4]))   # duplicate (slot, null, evi, frn)


def test_creator_hash_float64_cast_matches_training(prep):
    pr = make_prints(n=600)
    big = np.uint64(2**63 + 12345)                     # not exactly representable in float64
    pr["th"] = pr["th"].copy()
    pr["th"][::7] = big
    pr["th"][3::7] = np.uint64(2**63 + 12345 + 1)      # distinct uint64, same float64 as `big`
    c = const()
    r_u64 = run(prep, pr, c=c, ch=big, seg_end=None)
    r_f64 = run(prep, pr, c=c, ch=float(big), seg_end=None)          # training: pandas float64 ch
    assert r_u64.X.tobytes() == r_f64.X.tobytes()
    i = rf.FEATS.index("cre_sell_post")
    # the colliding hash is treated as the creator, as in training
    pr_b = dict(pr)
    pr_b["th"] = pr["th"].copy()
    pr_b["th"][3::7] = np.uint64(5)
    r_exact = run(prep, pr_b, c=c, ch=big, seg_end=None)
    ib = rf.FEATS.index("cre_buy_post")
    assert r_u64.X[-1, ib] + r_u64.X[-1, i] > r_exact.X[-1, ib] + r_exact.X[-1, i]


def test_degenerate_inputs_return_none(prep):
    pr = make_prints(n=2)
    assert run(prep, pr, seg_end=None) is None                        # n < 3
    pr = make_prints(n=50)
    pr["q"] = pr["q"].copy()
    pr["q"][3] = -1e12
    assert run(prep, pr, seg_end=None) is None                        # non-positive pool state


def test_bond_constants():
    ib = np.array([True, True, False, False])
    sol = np.array([5, 7, 2, 3])
    th = np.array([1, 2, 1, 3], np.uint64)
    d = rf.bond_constants(ib, sol, th, np.uint64(1), True)
    assert d == dict(nb=4.0, ntr=3.0, bbuy=12.0, bsell=5.0, cre_sell=2.0)
    d = rf.bond_constants(ib, sol, th, None, False)
    assert d["cre_sell"] == 0.0
    d = rf.bond_constants([], [], [], None, False)
    assert all(np.isnan(v) for v in d.values())


# ---- E-a: fixture reproduction on exploration data ---------------------------------------------------------------------------------
def _load_e_a():
    spec = importlib.util.spec_from_file_location("r4b_equiv_a", REPO / "scripts" / "research" / "r4b_equiv_a.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["r4b_equiv_a"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_e_a_fixture_reproduction():
    pytest.importorskip("duckdb")
    pytest.importorskip("pandas")
    ea = _load_e_a()
    if not ea.data_available():
        pytest.skip("exploration data path missing")
    X, I, fx = ea.build_x()
    ok, info = ea.compare(X, I, fx)
    print(info)
    assert info["fixture_rows"] == 617
    assert ok, info
    assert info["X_dtype"] == "float32"


def test_forward_order_on_synthetic_mint(prep):
    """Shuffle a mint's prints (with null / non-null tx_index mixed, slot ties) and restore order via forward_order; features are then
    identical to the in-order run."""
    pr = make_prints(n=400, gap=100)
    n = len(pr["slot"])
    rng = np.random.default_rng(11)
    txi = np.where(rng.random(n) < 0.5, np.nan, rng.integers(0, 50, n).astype(float))
    pr["slot"] = pr["slot"].copy()
    pr["slot"][1::2] = pr["slot"][0::2][: n // 2]            # tie slot pairs (equal bt too, then bt monotone)
    pr["bt"] = pr["bt"].copy()
    pr["bt"][1::2] = pr["bt"][0::2][: n // 2]
    evi = rng.integers(0, 3, n)
    frn = np.arange(n)
    perm = rf.forward_order(pr["slot"], txi, evi, frn)
    sh = rng.permutation(n)
    pr_sh = {k: v[sh] for k, v in pr.items()}
    perm2 = rf.forward_order(pr_sh["slot"], txi[sh], evi[sh], frn[sh])
    back = {k: v[perm2] for k, v in pr_sh.items()}
    ref = {k: v[perm] for k, v in pr.items()}
    for k in ref:
        assert np.array_equal(ref[k], back[k])
    res = run(prep, ref, seg_end=None)
    res2 = run(prep, back, seg_end=None)
    assert res is not None and res.X.tobytes() == res2.X.tobytes()
    assert rf.mixed_null_txi_slots(pr["slot"], txi) > 0
