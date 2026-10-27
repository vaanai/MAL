"""HV8 power, v1 (QP CHANGE G on #1010). Arithmetic only, no data. Run: /data/mal/venv/bin/python -I power_hv8_v1.py
Part 1: HV7 QP convention (headline): per-trade d_LU ~ N(IC_L*1.4264*s, (1.06 s)^2) [var s^2 (1 + 1/8)],
        d_LT = mu_L - mu_T + s (e_L - e_T) (sd 1.41 s, no pick coincidence), e_L shared by both contrasts; scale-free, so s drops out.
Part 2: power_hv8.py's order-statistic score model (optimistic bound) at 10 trades/day.
Exact one-sided sign-flip on daily sums, p <= 0.025, 1,500 draws per cell, seed 1."""
import itertools, numpy as np
EMAX8 = 1.4264
def signs(D): return np.array(list(itertools.product([1, -1], repeat=D)), dtype=float)
def pval(d, S):
    obs = d.sum(1); flips = (d[:, None, :] * S[None]).sum(2)
    return (flips >= obs[:, None] - 1e-12).mean(1)
def conv(rhoL, rhoT, D, per_day, draws=1500, seed=1):
    rng = np.random.default_rng(seed); P = per_day * D
    eL, eT, eU = (rng.standard_normal((draws, P)) for _ in range(3))
    muL, muT = rhoL * EMAX8, rhoT * EMAX8
    dLU = (muL + eL - eU / np.sqrt(8)).reshape(draws, D, per_day).sum(2)
    dLT = (muL - muT + eL - eT).reshape(draws, D, per_day).sum(2)
    S = signs(D); a = pval(dLT, S) <= 0.025; b = pval(dLU, S) <= 0.025
    return a.mean(), b.mean(), (a & b).mean()
def ostat(rhoL, rhoT, kappa, D, per_day, N=8, draws=1500, seed=1):
    rng = np.random.default_rng(seed); P = per_day * D
    z = rng.standard_normal((draws, P, N))
    e1 = rng.standard_normal((draws, P, N)); e2 = kappa * e1 + np.sqrt(1 - kappa**2) * rng.standard_normal((draws, P, N))
    L = rhoL * z + np.sqrt(1 - rhoL**2) * e1; T = rhoT * z + np.sqrt(1 - rhoT**2) * e2
    rL = np.take_along_axis(z, L.argmax(2)[..., None], 2)[..., 0]; rT = np.take_along_axis(z, T.argmax(2)[..., None], 2)[..., 0]
    dLT = (rL - rT).reshape(draws, D, per_day).sum(2); dLU = (rL - z.mean(2)).reshape(draws, D, per_day).sum(2)
    S = signs(D); a = pval(dLT, S) <= 0.025; b = pval(dLU, S) <= 0.025
    return a.mean(), b.mean(), (a & b).mean()
ICS = (0.0, 0.05, 0.10, 0.17, 0.25, 0.30)
print("PART 1 HV7 convention: D per_day twinIC | IC_L: P(LT) P(LU) P(both)")
for D in (10, 8):
    for per_day in (20, 10):
        for rhoT in (0.0, 0.10):
            cells = [conv(r, rhoT, D, per_day) for r in ICS]
            print(D, per_day, rhoT, "|", "  ".join(f"{r:.2f}:{a:.3f}/{b:.3f}/{c:.3f}" for r, (a, b, c) in zip(ICS, cells)))
print("PART 2 order-statistic (optimistic bound), 10/day: D twinIC kappa | IC_L: P(both)")
for D in (10,):
    for rhoT in (0.0, 0.10):
        for kappa in (0.5, 0.8):
            cells = [ostat(r, rhoT, kappa, D, 10) for r in ICS]
            print(D, rhoT, kappa, "|", "  ".join(f"{r:.2f}:{c:.3f}" for r, (a, b, c) in zip(ICS, cells)))
