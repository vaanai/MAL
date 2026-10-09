"""C1 v2 ML / book helpers (ML venv: numpy, lightgbm, sklearn; no pandas). Used identically in discovery and in the one CONFIRMATION score."""
import math
import numpy as np

SIZE = 250_000_000; FEE = 55_000
B_SLOT, B_SOL, TARGET_FAIL = 0.8, 0.35, 0.289
FLAT = 0.15


def load(path):
    z = np.load(path, allow_pickle=False)
    return {k: z[k] for k in z.files}


def fidx(d, names):
    fn = list(d['fnames'])
    return [fn.index(n) for n in names]


def col(d, name):
    return d['F'][:, list(d['fnames']).index(name)]


def stage1_mask(d, rule):
    """rule = (a, s, k, L): (v5 >= a & surge >= s) | new5 >= k, AND qreal >= L; always inside the pass-A superset."""
    a, s, k, L = rule
    v5, sg, nw, qr = col(d, 'v5'), col(d, 'surge'), col(d, 'new5'), col(d, 'qreal')
    return (((v5 >= a) & (sg >= s)) | (nw >= k)) & (qr >= L)


def book(d, sel, ex, leg='p', cool=60):
    """Non-overlapping book: rows in time order; take a selected row when its mint has no open position (previous exit time + cool <= t).
    Returns indices of taken rows."""
    t, mid = d['t'], d['mid']
    xt = d[f'xt_{ex}_{leg}']
    order = np.lexsort((mid, t))
    free = {}
    taken = []
    for i in order:
        if not sel[i]:
            continue
        m = int(mid[i])
        if t[i] < free.get(m, -1):
            continue
        taken.append(i)
        free[m] = max(int(xt[i]), int(t[i])) + cool
    return np.array(taken, dtype=np.int64)


def sigmoid(z):
    return 1.0 / (1.0 + np.exp(-z))


def press_p(ssb, nearby, filled):
    """latency_curve.fit_curve: slopes at scale 1, intercept so that the mean p over the filled sends is 0.289."""
    z0 = B_SLOT * np.log1p(ssb) + B_SOL * np.log1p(nearby / 1e9)
    zf = z0[filled]
    lo, hi = -40.0, 40.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if sigmoid(mid + zf).mean() > TARGET_FAIL:
            hi = mid
        else:
            lo = mid
    c = (lo + hi) / 2
    return sigmoid(c + z0), c


def legs(d, idx, ex, leg='p'):
    """Per-attempt pnl (lamports) on the nofail, flat and pressure legs."""
    pnl = d[f'pnl_{ex}_{leg}'][idx]
    guard = d[f'guard_{leg}'][idx] > 0
    filled = ~guard
    flat = np.where(filled, (1 - FLAT) * pnl + FLAT * (-FEE), pnl)
    p, c = press_p(d[f'ssb_{leg}'][idx].astype(float), d[f'nearby_{leg}'][idx].astype(float), filled)
    press = np.where(filled, (1 - p) * pnl + p * (-FEE), pnl)
    return dict(nofail=pnl, flat=flat, press=press, p=p, c=c, filled=filled)


def gate(x_lamports, dates, size=SIZE):
    """cap_pick_score.gate_stats subset: trade CI90 lo (1,000 draws, seed 1, 5th pct), date-cluster CI90 lo (1,000 date resamples, seed 1)."""
    x = np.asarray(x_lamports, float) / 1e9
    n = len(x)
    if n == 0:
        return dict(n=0)
    sz = size / 1e9
    rng = np.random.default_rng(1)
    lo_t = float(np.percentile(x[rng.integers(0, n, size=(1000, n))].mean(1), 5))
    ud, inv = np.unique(np.asarray(dates), return_inverse=True)
    dsum = np.bincount(inv, weights=x); dn = np.bincount(inv)
    w = len(ud)
    rng2 = np.random.default_rng(1)
    di = rng2.integers(0, w, size=(1000, w))
    lo_d = float(np.percentile(dsum[di].sum(1) / dn[di].sum(1), 5))
    return dict(n=n, days=w, days_pos=int((dsum > 0).sum()), mean_pct=100 * x.mean() / sz, ci_trade_lo_pct=100 * lo_t / sz, ci_date_lo_pct=100 * lo_d / sz,
                total_sol=float(x.sum()), ex_top3_sol=float(x.sum() - np.sort(x)[-3:].sum()), ex_best_day_sol=float(x.sum() - dsum.max()),
                win_rate=float((x > 0).mean()))


def meets(g):
    return bool(g.get('n', 0) >= 100 and g['days'] >= 5 and g['days_pos'] * 2 > g['days'] and g['ci_trade_lo_pct'] > 0 and g['ex_top3_sol'] > 0)


def fmt(g):
    if g.get('n', 0) == 0:
        return 'n=0'
    return (f"n={g['n']} days={g['days']} pos={g['days_pos']} mean={g['mean_pct']:.3f}% ciT={g['ci_trade_lo_pct']:.3f}% ciD={g['ci_date_lo_pct']:.3f}% "
            f"tot={g['total_sol']:.4f} exTop3={g['ex_top3_sol']:.4f} exBestDay={g['ex_best_day_sol']:.4f} win={g['win_rate']:.3f}")


def lgb_params(obj='huber'):
    return dict(objective=obj, alpha=0.05 if obj == 'huber' else 0.9, learning_rate=0.03, num_leaves=31, min_data_in_leaf=300, feature_fraction=0.7,
                bagging_fraction=0.8, bagging_freq=1, lambda_l2=10.0, num_threads=3, seed=1, verbose=-1, deterministic=True, force_col_wise=True)


def botsim(t_in, t_out, r, qreal, dates, K=4, bank0=1.0, liq_frac=0.02, max_stake=2.0):
    """Bot illustration: bankroll bank0 SOL, at most K concurrent positions, stake = min(equity_cash_at_entry / K-free-share, liq_frac x pool real quote).
    r = return per unit stake of each trade (from the 0.25 SOL sim; larger stakes would pay more impact, so this is optimistic above 0.25 SOL).
    Events in time order; an entry is skipped when K positions are open. Returns daily end equity path, max drawdown, n taken."""
    order = np.argsort(t_in, kind='stable')
    cash = bank0; open_ = []  # (t_out, stake, r)
    eq_pts = []; taken = 0; skipped = 0
    for i in order:
        ti = t_in[i]
        still = []
        for (to, st, rr) in open_:
            if to <= ti:
                cash += st * (1 + rr)
            else:
                still.append((to, st, rr))
        open_ = still
        if len(open_) >= K:
            skipped += 1; continue
        equity = cash + sum(st for _, st, _ in open_)
        stake = min(equity / K, cash, liq_frac * max(float(qreal[i]), 0.0))
        if max_stake is not None:
            stake = min(stake, max_stake)
        if stake <= 1e-6:
            skipped += 1; continue
        # constant-product impact correction for stakes above the simulated 0.25 SOL: extra round-trip cost ~ 2 x (stake - 0.25) / (real quote + V)
        r_eff = float(r[i]) - 2.0 * max(stake - 0.25, 0.0) / (max(float(qreal[i]), 0.0) + 17.5845)
        cash -= stake; open_.append((t_out[i], stake, r_eff)); taken += 1
        eq_pts.append((ti, cash + sum(st for _, st, _ in open_), dates[i]))
    for (to, st, rr) in open_:
        cash += st * (1 + rr)
    # daily end-of-day equity (at cost for open positions)
    path = {}
    for ti, e, dd in eq_pts:
        path[dd] = e
    eqs = np.array([bank0] + [e for _, e, _ in eq_pts] + [cash])
    peak = np.maximum.accumulate(eqs); mdd = float(((eqs - peak) / peak).min())
    return dict(final=cash, taken=taken, skipped=skipped, max_dd=mdd, daily=path)
