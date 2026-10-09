#!/usr/bin/env python3
"""EXP-025 (C1-NF) power table. Stylised simulation. NOT evidence of an edge, NOT a gate computation on any real row.

Inputs are only the VERIFY numbers copied into ARTIFACTS/exp025/verify/ (results.json: daily_505 and sim.main_505; VERIFY.md section 5-6). It opens no
tape, no forward row, no sealed block. Everything the model does not know is a named assumption below and printed with the table.

Model (per counted decision day d, W days from 2026-10-10T00):
  n_d ~ NegBin(mean = LAMBDA, dispersion fitted to the 21 September daily counts); LAMBDA = 20/day (VERIFY: 419 / 21 = 19.95) or 12/day (sensitivity: walk-2
  days lose CAP-PICK picks by the boolean oracle, and October volume is unmeasured).
  Trade return r (fraction of stake, no-fail, deciding cell 1.3 s, 505,000 lamports per send): a 4-part mixture calibrated to the VERIFY numbers
  (win rate 66.8%, 22/419 trades lose >= 90%, median +10.17%, p95 +63.17%, no-fail mean +9.625%, top-3 average about +165%, top-10 about +122%).
  Edge k: the positive parts are scaled by theta so the rent-inclusive no-fail mean is k x (9.625% - 0.816%). k = 1 is "September holds", k = 0.5 is "half the September effect".
  Rent: 2,039,280 lamports (0.816% of stake) is charged on every fill in the deciding cell (the pessimistic reading of EXP-025 section 6).
  Day effect: additive, N(0, tau_d^2), tau_d^2 = the between-day variance of the September daily means in excess of the trade-level noise (floored at 0).
  Fail legs are gate-weighted as mlcommon.legs does (every attempt weighted by its expected value: flat = 0.85 pnl - 0.15 fee, not random draws). Flat p = 0.15 (VERIFY, rent-inclusive: flat +7.458%); pressure p_eff chosen so that at k = 1 the pressure mean equals VERIFY's rent-inclusive +6.640% (the real pressure leg has a per-trade p). A failed
  send costs 505,000 lamports on a 0.25 SOL stake. Failure is independent of r (stylised); it enters as a weight, so both legs are linear in r.
Two looks: Look 1 = first 7 dates at ALPHA_LOOK1, Look 2 = cumulative 14 dates at ALPHA_LOOK2, only if Look 1 did not pass (--mode looks, the default).
Gate per leg (all legs must pass): n >= 100; >= 5 dates with a strict majority positive; trade bootstrap CI90 lower bound > 0 (1,000 draws, 5th percentile);
  total > 0 after removing the top 3 trades; total > 0 after removing the best date; date-cluster CI90 lower bound > 0 (1,000 draws); one-sided day-level t
  (df = W - 1, empty dates dropped) with p <= alpha; optionally mean > 0 in each half of the dates.
Not simulated: the 1.9 s cell (VERIFY: flat +8.203, pressure +6.902, about the same), the binding legs of EXP-025 section 6, and correlated fill failure
  (VERIFY section 6 bounds it: +1.855% if the best 10% of fills fail).
Usage: python tools/exp025_power.py [--mode looks|single] [--sims 1500] [--json out.json]   (looks = the registered two-look design; single = the one-window table)
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "..", "ARTIFACTS", "exp025", "verify", "results.json")

STAKE_LAMPORTS = 250_000_000
SEND_FEE = 505_000
FAIL_COST = SEND_FEE / STAKE_LAMPORTS  # 0.00202 of stake
RENT = 2_039_280 / STAKE_LAMPORTS  # 0.008157 of stake, charged on every fill in the deciding cell (EXP-025 section 6)

# VERIFY numbers (VERIFY.md section 5.1 and 6; results.json sim.main_505)
SEPT_NOFAIL_MEAN = 0.09625244620786022
SEPT_FLAT_MEAN = 0.08151
SEPT_PRESS_MEAN = 0.07220  # no rent
SEPT_PRESS_MEAN_RENT = 0.06640  # VERIFY 5.2, 1.3 s pressure, + rent 2,039,280 per fill
P_LOSE90 = 22 / 419
WIN_RATE = 0.668
MEDIAN = 0.1017
P95 = 0.6317
TOP3_AVG = (10.082443740273359 - 8.846251358638687) / 0.25 / 3  # from total and ex-top-3 (SOL, no-fail), stake 0.25
TOP10_AVG = (10.082443740273359 - 7.0249498330033555) / 0.25 / 10
FLAT_P = 0.15

# The two-look design (owner answer 2026-10-09: "Two looks"). ONE constant pair, easy to edit. (0.005, 0.020) is quant-proof's recommended split (2026-10-09).
# It must sum to 0.025 (DEC-025 slot 3 with k = 1) and must equal the EXP025_ALPHA_LOOK1 / EXP025_ALPHA_LOOK2 lines in
# EXP/EXP-025-c1nf-part1-prereg.md section 0 (tools/test_exp025.py checks both).
ALPHA_LOOK1 = 0.005
ALPHA_LOOK2 = 0.020
LOOK1_DATES = 7   # counted decision dates [2026-10-10, 2026-10-17)
TOTAL_DATES = 14  # cumulative [2026-10-10, 2026-10-24)
SD_FROM_CI = (0.08454 - 0.05762) / 1.645 * math.sqrt(422)  # JUDGE-4 3.3.5 arithmetic, for comparison only


def t_sf(t: float, df: int) -> float:
    """One-sided P(T_df >= t) via the regularised incomplete beta (continued fraction); no scipy."""
    if df <= 0:
        return float("nan")
    x = df / (df + t * t)
    p2 = _betainc(df / 2.0, 0.5, x)  # two-sided p for |t|
    return 0.5 * p2 if t >= 0 else 1.0 - 0.5 * p2


def _betainc(a: float, b: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(lbeta + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def _betacf(a: float, b: float, x: float) -> float:
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = tiny if abs(d) < tiny else d
    d = 1.0 / d
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = tiny if abs(d) < tiny else d
        c = 1.0 + aa / c
        c = tiny if abs(c) < tiny else c
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = tiny if abs(d) < tiny else d
        c = 1.0 + aa / c
        c = tiny if abs(c) < tiny else c
        d = 1.0 / d
        de = d * c
        h *= de
        if abs(de - 1.0) < 3e-16:
            break
    return h


class Model:
    """Calibrated return mixture. Parts: L (>= 90% loss), M (mid loss), body (win, below p95), top (win, above p95)."""

    P_TOP = 0.05
    BODY_S = 0.9

    def __init__(self):
        self.pL = P_LOSE90
        self.pTop = self.P_TOP
        self.pBody = WIN_RATE - self.pTop
        self.pM = 1.0 - self.pL - self.pBody - self.pTop
        self.M_BETA = (1.0, 3.5)  # placeholder; set below
        self.L_BETA = (1.0, 6.0)  # r = -1 + 0.09 * Beta(1, 6)
        # body: lognormal(mu, S) truncated to (0, P95); mu set so the overall median is MEDIAN
        z = np.random.default_rng(0).standard_normal(2_000_000)
        want = (0.5 - self.pL - self.pM) / self.pBody  # share of the body below the median
        lo, hi = -4.0, 1.0
        for _ in range(60):
            mu = 0.5 * (lo + hi)
            x = np.exp(mu + self.BODY_S * z)
            x = x[x < P95]
            share = float(np.mean(x < MEDIAN))
            if share > want:
                lo = mu
            else:
                hi = mu
        self.mu = 0.5 * (lo + hi)
        x = np.exp(self.mu + self.BODY_S * z)
        self.body_mean = float(np.mean(x[x < P95]))
        e_l = -1.0 + 0.09 * self.L_BETA[0] / sum(self.L_BETA)
        # top tail: P95 + Exp(tau). tau is set so the average of the 10 largest of ~21 tail draws equals the VERIFY top-10 average (+122%):
        # the i-th largest of n unit exponentials has mean sum_{j=i}^{n} 1/j.
        n_top = max(int(round(self.pTop * 419)), 10)
        units = np.mean([sum(1.0 / j for j in range(i, n_top + 1)) for i in range(1, 11)])
        self.tau = (TOP10_AVG - P95) / units
        # mid-loss part: -0.9 * Beta(1, b), with b set so the no-fail mean is SEPT_NOFAIL_MEAN at theta = 1
        e_m = (SEPT_NOFAIL_MEAN - self.pL * e_l - self.pBody * self.body_mean - self.pTop * (P95 + self.tau)) / self.pM
        if not (-0.9 < e_m < 0.0):
            raise ValueError(f"calibration failed: mid-loss mean {e_m}")
        self.M_BETA = (1.0, -0.9 / e_m - 1.0)
        self.neg_mean = self.pL * e_l + self.pM * e_m
        self.pos_mean = self.pBody * self.body_mean + self.pTop * (P95 + self.tau)

    def theta_for(self, k: float) -> float:
        """Scale of the positive parts so the RENT-INCLUSIVE no-fail mean is k x its September value (SEPT_NOFAIL_MEAN - RENT)."""
        target_gross = RENT + k * (SEPT_NOFAIL_MEAN - RENT)
        return (target_gross - self.neg_mean) / self.pos_mean

    def sample(self, rng: np.random.Generator, n: int, theta: float) -> np.ndarray:
        u = rng.random(n)
        r = np.empty(n)
        iL = u < self.pL
        iM = (~iL) & (u < self.pL + self.pM)
        iB = (~iL) & (~iM) & (u < self.pL + self.pM + self.pBody)
        iT = ~(iL | iM | iB)
        r[iL] = -1.0 + 0.09 * rng.beta(*self.L_BETA, iL.sum())
        r[iM] = -0.9 * rng.beta(*self.M_BETA, iM.sum())
        nb = int(iB.sum())
        if nb:
            out = np.empty(0)
            while out.size < nb:
                x = np.exp(self.mu + self.BODY_S * rng.standard_normal(2 * nb + 16))
                out = np.concatenate([out, x[x < P95]])
            r[iB] = theta * out[:nb]
        r[iT] = theta * (P95 + rng.exponential(self.tau, int(iT.sum())))
        return r

    def summary(self, n: int = 1_000_000) -> dict:
        rng = np.random.default_rng(7)
        r = self.sample(rng, n, 1.0)
        s = np.sort(r)[::-1]
        m = 419
        return {
            "mean": float(r.mean()), "median": float(np.median(r)), "p95": float(np.quantile(r, 0.95)), "win_rate": float((r > 0).mean()),
            "p_lose90": float((r <= -0.9).mean()), "sd": float(r.std()),
            "top3_avg_of_419": float(np.mean([np.sort(rng.choice(r, m))[::-1][:3].mean() for _ in range(300)])),
            "top10_avg_of_419": float(np.mean([np.sort(rng.choice(r, m))[::-1][:10].mean() for _ in range(300)])),
        }


def load_september():
    d = json.load(open(RESULTS))["daily_505"]
    n = np.array([v["n"] for v in d.values()], float)
    flat_total = np.array([v["flat"] for v in d.values()], float)
    return n, flat_total


def day_structure(model: Model):
    n, flat_total = load_september()
    var_n = float(n.var(ddof=1))
    mean_n = float(n.mean())
    nb_r = mean_n ** 2 / max(var_n - mean_n, 1e-9)
    day_mean = flat_total / n / 0.25  # fraction of stake
    sd_trade = model.summary(400_000)["sd"]
    between = float(day_mean.var(ddof=1))
    noise = float(np.mean(sd_trade ** 2 / n))
    return {"nb_r": nb_r, "mean_n": mean_n, "var_n": var_n, "sd_trade": sd_trade, "between_var": between, "noise_var": noise,
            "tau_d2": max(0.0, between - noise)}


def pressure_p() -> float:
    # (1 - p) * (nofail - RENT) - p * FAIL_COST = the VERIFY rent-inclusive pressure mean at k = 1
    return (SEPT_NOFAIL_MEAN - RENT - SEPT_PRESS_MEAN_RENT) / (SEPT_NOFAIL_MEAN - RENT + FAIL_COST)


def leg_returns(r: np.ndarray, p_press: float):
    """The gate's fail legs (mlcommon.legs): every attempt is weighted by its expected value, not drawn at random.
    flat = 0.85 * pnl + 0.15 * (-fee); pressure = (1 - p) * pnl + p * (-fee). pnl here is the rent-inclusive fill return."""
    return [(1.0 - FLAT_P) * r - FLAT_P * FAIL_COST, (1.0 - p_press) * r - p_press * FAIL_COST]


def leg_pass(x: np.ndarray, day: np.ndarray, ndays: int, rng: np.random.Generator, alphas, halves: bool) -> dict:
    """x: per-trade leg return (fraction of stake); day: day index 0..ndays-1. Returns per-item booleans and the day-level p."""
    n = x.size
    out = {"n": n >= 100}
    sums = np.bincount(day, weights=x, minlength=ndays)
    cnts = np.bincount(day, minlength=ndays).astype(float)
    live = cnts > 0
    W = int(live.sum())
    out["days"] = W >= 5 and int((sums[live] > 0).sum()) * 2 > W
    if n < 2 or W < 2:
        for k in ("ci_trade", "ex3", "exbest", "ci_date", "half"):
            out[k] = False
        out["p"] = 1.0
        return out
    idx = rng.integers(0, n, (1000, n))
    out["ci_trade"] = float(np.quantile(x[idx].mean(1), 0.05)) > 0
    out["ex3"] = (x.sum() - np.sort(x)[-3:].sum()) > 0
    out["exbest"] = (x.sum() - sums.max()) > 0
    s_l, c_l = sums[live], cnts[live]
    di = rng.integers(0, W, (1000, W))
    out["ci_date"] = float(np.quantile(s_l[di].sum(1) / c_l[di].sum(1), 0.05)) > 0
    dm = s_l / c_l
    sd = dm.std(ddof=1)
    t = dm.mean() / (sd / math.sqrt(W)) if sd > 0 else (math.inf if dm.mean() > 0 else -math.inf)
    out["p"] = t_sf(t, W - 1) if math.isfinite(t) else (0.0 if t > 0 else 1.0)
    h = ndays // 2 if ndays % 2 == 0 else (ndays + 1) // 2
    a = day < h
    out["half"] = (not halves) or (x[a].mean() > 0 and x[~a].mean() > 0 if a.any() and (~a).any() else False)
    return out


def simulate(model, ds, windows, ks, lambdas, alphas, sims, seed=1):
    rows = []
    p_press = pressure_p()
    for W in windows:
        for k in ks:
            theta = model.theta_for(k)
            for lam in lambdas:
                rng = np.random.default_rng(seed)
                acc = {(a, hv): 0 for a in alphas for hv in (False, True)}
                acc_basic = 0
                nsum = 0
                for _ in range(sims):
                    cnt = rng.negative_binomial(ds["nb_r"], ds["nb_r"] / (ds["nb_r"] + lam), W)
                    n = int(cnt.sum())
                    nsum += n
                    day = np.repeat(np.arange(W), cnt)
                    r = model.sample(rng, n, theta)
                    if ds["tau_d2"] > 0:
                        r = np.maximum(r + np.repeat(rng.normal(0.0, math.sqrt(ds["tau_d2"]), W), cnt), -1.0)
                    r = r - RENT  # deciding cell: rent on every fill
                    legs = leg_returns(r, p_press)
                    res = [leg_pass(x, day, W, rng, alphas, True) for x in legs]
                    base = all(all(v for kk, v in rr.items() if kk not in ("p", "half")) for rr in res)
                    acc_basic += int(all(rr["n"] and rr["days"] and rr["ci_trade"] and rr["ex3"] for rr in res))
                    pmax = max(rr["p"] for rr in res)
                    half_ok = all(rr["half"] for rr in res)
                    for a in alphas:
                        acc[(a, False)] += int(base and pmax <= a)
                        acc[(a, True)] += int(base and pmax <= a and half_ok)
                row = {"window_days": W, "edge_k": k, "per_day": lam, "mean_n": nsum / sims, "claude_md_gate_only": acc_basic / sims}
                for a in alphas:
                    row[f"pass_alpha_{a}"] = acc[(a, False)] / sims
                    row[f"pass_alpha_{a}_with_halves"] = acc[(a, True)] / sims
                rows.append(row)
    return rows


FWD_DATES = 6  # dates 10-10 .. 10-15 are on forward-1002; dates 10-16 .. 10-23 (index 6 .. 13) are on walk 2

SCENARIOS = [
    {"name": "20/day on all dates", "lam_fwd": 20.0, "lam_walk2": 20.0, "day_sd": None},
    {"name": "12/day on all dates", "lam_fwd": 12.0, "lam_walk2": 12.0, "day_sd": None},
    {"name": "20/day forward-1002, 12/day walk-2 dates (oracle exclusion)", "lam_fwd": 20.0, "lam_walk2": 12.0, "day_sd": None},
    {"name": "20/day with a day effect, SD 0.03 of stake", "lam_fwd": 20.0, "lam_walk2": 20.0, "day_sd": 0.03},
]


def _scenario(sc):
    if isinstance(sc, dict):
        return sc
    return {"name": f"{sc}/day", "lam_fwd": float(sc), "lam_walk2": float(sc), "day_sd": None}


def simulate_looks(model, ds, ks, scenarios, splits, sims, seed=1, look1_dates=LOOK1_DATES, total_dates=TOTAL_DATES):
    """Two-look read. Look 1 uses the first look1_dates dates; Look 2 is cumulative over total_dates and runs only if Look 1 did not pass.
    One simulated October serves both looks (the same trades), so the looks are correctly correlated. splits: list of (alpha1, alpha2).
    Each look passes only if every section 7 item holds on both gate-weighted legs (halves by date included) and the larger day-level p <= the look's alpha.
    scenarios: dicts with lam_fwd (trades/day on dates 0..FWD_DATES-1), lam_walk2 (later dates) and day_sd (SD of an additive day effect, None = the fitted value)."""
    rows = []
    p_press = pressure_p()
    for k in ks:
        theta = model.theta_for(k)
        for sc in map(_scenario, scenarios):
            rng = np.random.default_rng(seed)
            lam = np.where(np.arange(total_dates) < FWD_DATES, sc["lam_fwd"], sc["lam_walk2"])
            tau2 = ds["tau_d2"] if sc["day_sd"] is None else sc["day_sd"] ** 2
            rec = []
            n1s = n2s = 0
            for _ in range(sims):
                cnt = rng.negative_binomial(ds["nb_r"], ds["nb_r"] / (ds["nb_r"] + lam))
                n = int(cnt.sum())
                day = np.repeat(np.arange(total_dates), cnt)
                r = model.sample(rng, n, theta)
                if tau2 > 0:
                    r = np.maximum(r + np.repeat(rng.normal(0.0, math.sqrt(tau2), total_dates), cnt), -1.0)
                r = r - RENT
                legs = leg_returns(r, p_press)
                out = []
                for nd in (look1_dates, total_dates):
                    m = day < nd
                    res = [leg_pass(x[m], day[m], nd, rng, None, True) for x in legs]
                    base = all(all(v for kk, v in rr.items() if kk != "p") for rr in res)
                    out.append((base, max(rr["p"] for rr in res), int(m.sum())))
                n1s += out[0][2]
                n2s += out[1][2]
                rec.append(out)
            row = {"edge_k": k, "scenario": sc["name"], "mean_n_look1": n1s / sims, "mean_n_look2": n2s / sims}
            for a1, a2 in splits:
                l1 = np.array([o[0][0] and o[0][1] <= a1 for o in rec])
                l2 = np.array([o[1][0] and o[1][1] <= a2 for o in rec])
                row[f"{a1}:{a2}"] = {"look1": float(l1.mean()), "look2_given_look1_not_passed": float((~l1 & l2).mean()), "either": float((l1 | (~l1 & l2)).mean())}
            row["single_look_14_at_0.025"] = float(np.mean([o[1][0] and o[1][1] <= 0.025 for o in rec]))
            rows.append(row)
    return rows


def main_looks(a, model, ds) -> int:
    splits = [tuple(float(y) for y in x.split(":")) for x in a.splits.split(",")]
    if (ALPHA_LOOK1, ALPHA_LOOK2) not in splits:
        splits.insert(0, (ALPHA_LOOK1, ALPHA_LOOK2))
    ks = [float(x) for x in a.ks.split(",")]
    rows = simulate_looks(model, ds, ks, SCENARIOS, splits, a.sims)
    se = math.sqrt(0.25 / a.sims)
    print(f"\ntwo looks: Look 1 = first {LOOK1_DATES} dates, Look 2 = cumulative {TOTAL_DATES} dates, runs only if Look 1 did not pass.")
    print(f"pass probability, both GATE-WEIGHTED fail legs (0.85 pnl - 0.15 fee, not random draws), 1.3 s cell, rent per fill; {a.sims} sims per row, Monte Carlo SE <= {se:.3f}")
    print(f"registered split (ALPHA_LOOK1, ALPHA_LOOK2) = ({ALPHA_LOOK1}, {ALPHA_LOOK2}); other splits shown for comparison")
    for r in rows:
        print(f"\nedge {r['edge_k']}  scenario: {r['scenario']}  mean n: look 1 {r['mean_n_look1']:.0f}, look 2 {r['mean_n_look2']:.0f}   (one 14-date read at alpha 0.025: {r['single_look_14_at_0.025']:.3f})")
        print("  split (a1:a2)    look 1   look 2 (if 1 not passed)   either")
        for a1, a2 in splits:
            c = r[f"{a1}:{a2}"]
            print(f"  {str(a1) + ':' + str(a2):<14}  {c['look1']:>7.3f}  {c['look2_given_look1_not_passed']:>14.3f}            {c['either']:>7.3f}")
    if a.json:
        json.dump({"model": model.summary(), "day_structure": ds, "pressure_p": pressure_p(), "sims": a.sims, "alpha_look1": ALPHA_LOOK1,
                   "alpha_look2": ALPHA_LOOK2, "rows": rows}, open(a.json, "w"), indent=1, sort_keys=True)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sims", type=int, default=1500)
    ap.add_argument("--json", default=None)
    ap.add_argument("--windows", default="6,7,10,14")
    ap.add_argument("--ks", default="1,0.5,0.25")
    ap.add_argument("--lambdas", default="20,12")
    ap.add_argument("--alphas", default="0.025,0.0125,0.00833")
    ap.add_argument("--mode", choices=("looks", "single"), default="looks")
    ap.add_argument("--splits", default="0.008:0.017,0.003:0.022,0.010:0.015")
    a = ap.parse_args(argv)
    model = Model()
    ds = day_structure(model)
    summ = model.summary()
    print("calibration (model vs reported):")
    print(f"  no-fail mean  {summ['mean']:.4f} vs {SEPT_NOFAIL_MEAN:.4f}")
    print(f"  median        {summ['median']:.4f} vs {MEDIAN}")
    print(f"  p95           {summ['p95']:.4f} vs {P95}")
    print(f"  win rate      {summ['win_rate']:.4f} vs {WIN_RATE}")
    print(f"  P(loss>=90%)  {summ['p_lose90']:.4f} vs {P_LOSE90:.4f}")
    print(f"  top-3 avg     {summ['top3_avg_of_419']:.3f} vs {TOP3_AVG:.3f}   top-10 avg {summ['top10_avg_of_419']:.3f} vs {TOP10_AVG:.3f}")
    print(f"  trade SD      {summ['sd']:.4f} (JUDGE-4 CI-derived SD for the 422-trade C1 columns: {SD_FROM_CI:.4f})")
    print(f"  day structure {json.dumps({k: round(v, 6) for k, v in ds.items()})}")
    print(f"  pressure p_eff {pressure_p():.4f} (flat {FLAT_P}); failed send {FAIL_COST * 100:.3f}% of stake")
    if a.mode == "looks":
        return main_looks(a, model, ds)
    windows = [int(x) for x in a.windows.split(",")]
    ks = [float(x) for x in a.ks.split(",")]
    lambdas = [float(x) for x in a.lambdas.split(",")]
    alphas = [float(x) for x in a.alphas.split(",")]
    rows = simulate(model, ds, windows, ks, lambdas, alphas, a.sims)
    se = math.sqrt(0.25 / a.sims)
    print(f"\npass probability, both fail legs, 1.3 s cell; {a.sims} sims per row, Monte Carlo SE <= {se:.3f}")
    hdr = "window  edge  /day  mean_n  CLAUDE.md-only  " + "  ".join(f"a={x}" for x in alphas) + "   (with halves, a=%s)" % alphas[0]
    print(hdr)
    for r in rows:
        print(f"{r['window_days']:>5}d  {r['edge_k']:<5} {r['per_day']:<5} {r['mean_n']:>6.0f}  {r['claude_md_gate_only']:>12.3f}  "
              + "  ".join(f"{r[f'pass_alpha_{x}']:>7.3f}" for x in alphas) + f"   {r[f'pass_alpha_{alphas[0]}_with_halves']:.3f}")
    if a.json:
        json.dump({"model": summ, "day_structure": ds, "pressure_p": pressure_p(), "sims": a.sims, "rows": rows}, open(a.json, "w"), indent=1, sort_keys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
