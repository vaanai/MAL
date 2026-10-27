import itertools, numpy as np
def signs(D): return np.array(list(itertools.product([1,-1], repeat=D)), dtype=float)
def pval(d, S):  # exact one-sided sign-flip, d: (draws, D)
    obs = d.sum(1); flips = (d[:, None, :] * S[None]).sum(2)
    return (flips >= obs[:, None] - 1e-12).mean(1)
def sim(rhoL, rhoT, kappa, D, per_day=20, N=8, draws=1500, seed=1):
    rng = np.random.default_rng(seed); P = per_day * D
    z = rng.standard_normal((draws, P, N))
    e1 = rng.standard_normal((draws, P, N)); e2 = kappa * e1 + np.sqrt(1 - kappa**2) * rng.standard_normal((draws, P, N))
    L = rhoL * z + np.sqrt(1 - rhoL**2) * e1; T = rhoT * z + np.sqrt(1 - rhoT**2) * e2
    iL = L.argmax(2)[..., None]; iT = T.argmax(2)[..., None]
    rL = np.take_along_axis(z, iL, 2)[..., 0]; rT = np.take_along_axis(z, iT, 2)[..., 0]; rU = z.mean(2)
    same = (iL == iT).mean()
    dLT = (rL - rT).reshape(draws, D, per_day).sum(2); dLU = (rL - rU).reshape(draws, D, per_day).sum(2)
    S = signs(D); pLT = pval(dLT, S); pLU = pval(dLU, S)
    a, b = pLT <= 0.025, pLU <= 0.025
    return a.mean(), b.mean(), (a & b).mean(), same
print("D rhoL rhoT kappa | P(LT) P(LU) P(both) share_same_pick")
for D in (8, 10):
    for rhoT in (0.0, 0.10):
        for kappa in (0.5, 0.8):
            for rhoL in (0.0, 0.05, 0.10, 0.17, 0.25, 0.30):
                a, b, c, s = sim(rhoL, rhoT, kappa, D)
                print(f"{D} {rhoL:.2f} {rhoT:.2f} {kappa:.1f} | {a:.3f} {b:.3f} {c:.3f} {s:.3f}")
