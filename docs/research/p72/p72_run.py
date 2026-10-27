"""P72 sell-cluster absorb. Frozen by PREREG.md (K=3, W=10, X=2%, reserve>=50 SOL, L=2, 0.25 SOL, T=+10%, S=-10%, cap 600 s, exit lag 2).
Usage: p72_run.py count            -> counts only, no price
       p72_run.py price OUT.parquet [STAKE_SOL]   -> prices every cluster
Run with /data/mal/audit-1008/venv/bin/python under systemd-run --user --scope -p MemoryMax=8G.
PumpSwap PRE-trade reserves; quote = q+V (p55_price.py convention)."""
import glob, math, os, sys
import numpy as np, pandas as pd, pyarrow.parquet as pq
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)).split("/docs/research")[0])
sys.path.insert(0, "/home/claude/.miscusi/repos/vaanai/MAL")
from tools.paper_curve_math import PUMPSWAP_SOL_FEE_TIERS  # noqa: E402

P = "/data/mal/audit-1008/work/g_reachable_cap_book_rescore"
TH = np.array([t for t, _ in PUMPSWAP_SOL_FEE_TIERS], float)
TP = np.array([p for _, p in PUMPSWAP_SOL_FEE_TIERS], float)
PRIO = 55_000
K, W, XPCT, RESMIN = 3, 10, 0.02, 50e9
L, EL, TGT, STOP, CAP_S = 2, 2, 0.10, -0.10, 600.0


def tier(Q, B):
    return TP[np.searchsorted(TH, Q / B * 1e6 + 1e-9, "right") - 1] / 1e6


def pool_arrays(g, V):
    sl = g.slot.values; q = g.q.values + V; b = g.b.values; isb = g.isbuy.values
    sol = g.sol.values.astype(float); tok = g.tok.values; th = g.th.values; bt = g.bt.values
    ok = bt > 0
    sps = (bt[ok][-1] - bt[ok][0]) / max(1, sl[ok][-1] - sl[ok][0]) if ok.sum() > 2 and sl[ok][-1] > sl[ok][0] + 300 else np.nan
    return sl, q, b, isb, sol, tok, th, sps


def clusters(sl, q, isb, sol, th):
    """Causal detector. Returns list of print indices j where a cluster is declared."""
    out = []; run = []; fired = False
    for j in range(len(sl)):
        if isb[j]:
            run = []; fired = False
            continue
        run.append(j)
        if fired:
            continue
        w = [i for i in run if sl[i] >= sl[j] - W]
        if len({th[i] for i in w}) >= K and sol[w].sum() >= XPCT * q[w[0]] and q[j] >= RESMIN:
            out.append(j); fired = True
    return out


def price_one(sl, q, b, isb, sol, th, sps, j, stake):
    qpost_last = q[-1] + (0.9875 * sol[-1] if isb[-1] else -sol[-1] / 0.9875)
    bpost_last = b[-1] - 0  # last base only used when no later print exists; approximated by last pre-state
    def state(X):
        k = np.searchsorted(sl, X, "right")
        return (q[k], b[k]) if k < len(sl) else (qpost_last, b[-1])
    X = sl[j] + L
    if X >= sl[-1]:
        return None
    qe, be = state(X)
    f = tier(qe, be); net = stake * (1 - f); tk = be * net / (qe + net)
    def val(Y):
        qy, by = state(Y); Q = qy + net; B = by - tk
        return tk * Q / (B + tk) * (1 - tier(Q, B + tk))
    cap = X + int(round(CAP_S / sps))
    reason = "cap"; ytr = cap
    for m in range(np.searchsorted(sl, X, "right") , len(sl)):
        if sl[m] == sl[m - 1]:
            continue
        y = sl[m - 1]
        if y <= X:
            continue
        if y >= cap:
            break
        v = val(y) / stake - 1
        if v >= TGT: reason = "tp"; ytr = y; break
        if v <= STOP: reason = "sl"; ytr = y; break
    censored = reason == "cap" and sl[-1] < cap
    pnl = val(ytr + EL) - stake - 2 * PRIO
    ix = np.searchsorted(sl, X, "left"); hi = np.searchsorted(sl, X, "right"); lo2 = np.searchsorted(sl, X - int(round(2.0 / sps)), "left")
    ssb = int(isb[ix:hi].sum()); nb = float(sol[lo2:hi][isb[lo2:hi]].sum())
    return dict(pnl=pnl, reason=reason, censored=bool(censored), ssb=ssb, nb=nb, X=int(X), ytr=int(ytr))


def main():
    mode = sys.argv[1]
    stake = float(sys.argv[3]) * 1e9 if mode == "price" and len(sys.argv) > 3 else 0.25e9
    days = sorted(os.path.basename(f)[:10] for f in glob.glob(f"{P}/meta/*.parquet"))
    rows = []; pools = 0; skipped_sps = 0; days_with_paths = 0; sign_ok = sign_n = 0; unsorted = 0
    for day in days:
        pp = f"{P}/paths/{day}.parquet"
        if not os.path.exists(pp):
            continue
        days_with_paths += 1
        meta = pd.read_parquet(f"{P}/meta/{day}.parquet"); meta = meta[(meta.v >= 17.5e9) & (meta.v <= 17.7e9)].set_index("mint")
        paths = pq.read_table(pp).to_pandas()
        for mint, g in paths.groupby("mint", sort=False):
            if mint not in meta.index:
                continue
            V = float(meta.loc[mint, "v"]); pools += 1
            sl, q, b, isb, sol, tok, th, sps = pool_arrays(g, V)
            if not (0.15 < sps < 0.6):
                skipped_sps += 1; continue
            if (np.diff(sl) < 0).any():
                unsorted += 1; continue
            dq = np.diff(q); s = np.where(isb[:-1], 1, -1)   # PRE-trade: q[j+1]-q[j] has the sign of print j
            m = dq != 0; sign_ok += int((np.sign(dq[m]) == s[m]).sum()); sign_n += int(m.sum())
            for j in clusters(sl, q, isb, sol, th):
                r = dict(day=day, mint=mint, j=int(j), slot=int(sl[j]), q_sol=q[j] / 1e9, died=bool(q[-1] < 0.5 * q[j]))
                if mode == "price":
                    pr = price_one(sl, q, b, isb, sol, th, sps, j, stake)
                    if pr is None:
                        r["unfilled"] = True
                    else:
                        r.update(pr); r["unfilled"] = False
                rows.append(r)
        print(day, pools, len(rows), flush=True)
    d = pd.DataFrame(rows)
    print(f"PRE-trade check: sign(q[j+1]-q[j]) matches side of print j in {sign_ok}/{sign_n} = {sign_ok / max(1, sign_n):.4f}")
    print(f"days_with_paths={days_with_paths} pools={pools} skipped_sps={skipped_sps} unsorted={unsorted} clusters={len(d)}")
    if len(d):
        print(f"clusters per day (over {days_with_paths} days) = {len(d) / days_with_paths:.4f}; days with >=1 cluster = {d.day.nunique()}")
        print("per day:", d.groupby("day").size().to_dict())
    if mode == "price":
        d.to_parquet(sys.argv[2])


if __name__ == "__main__":
    main()
