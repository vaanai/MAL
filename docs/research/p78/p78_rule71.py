"""P78 step 2, rule 2 (P71 copy) inside one fee band. Usage: p78_rule71.py BAND_LO BAND_HI OUT_PREFIX [count]
Ranking logic = p71_rank.py (explore-0814 only), copy pricing = p71_price.py (fresh-0903 + exp011-0909), constants unchanged.
Adapter changes only: clean-view slim rows (mcap >= 9,820 SOL retained), no meta.v window, V from the row, in-band filter on the lead buy print,
state lookups inside a segment (slot-gap <= 150 retained prints), per-pool sps from block time."""
import sys, os, glob, math
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from p78_common import *
from p72_run_frozen import tier  # tier(Q,B) on (q+V)/b

LO, HI, OUT = float(sys.argv[1]), float(sys.argv[2]), sys.argv[3]
COUNT = len(sys.argv) > 4
N_HOUR, MIN_RT, TOPN = 30, 5, 50
SEED_BIG, SEED_ROW = 100e9, 987_654_320
STAKES = {"s01": 0.1e9, "s025": 0.25e9, "s1": 1.0e9}
MIN_LEAD = 0.05e9; LEG_B, TIMER_S, EL2 = 1.9, 3600.0, 2
BLK_SEP = ("fresh-0903", "exp011-0909")
IMAX = np.iinfo(np.int64).max
days_all = sorted({os.path.basename(f).split("_")[1][:10] for f in glob.glob(f"{ROWS}/*.parquet")})


def ndays(blocks):
    return [dd for dd in days_all if len({os.path.basename(f).split("_")[1] for f in day_files(dd, blocks)}) >= 12]


# ---------------- ranking (explore-0814) ----------------
pools, hours = [], []
adays = ndays(("explore-0814",))
for day in adays:
    g = load_day(day, ("explore-0814",))
    if g is None:
        continue
    g = g[(g.sol <= SEED_BIG) & (g.sol != SEED_ROW)].copy()
    g["hr"] = g.bt // 3600; g["sol"] = g.sol.astype(float)
    hours.append(g.groupby(["th", "hr"]).size().rename("n").reset_index())     # B1 counts all retained prints of the wallet
    g = g[(g.mc >= LO) & (g.mc < HI)]                                           # in-band prints only for round trips
    g["bsol"] = np.where(g.isbuy, g.sol, 0.0); g["ssol"] = np.where(~g.isbuy, g.sol, 0.0)
    g["btok"] = np.where(g.isbuy, g.tok, 0.0); g["stok"] = np.where(~g.isbuy, g.tok, 0.0)
    g["bslot"] = np.where(g.isbuy, g.slot, IMAX); g["sslot"] = np.where(~g.isbuy, g.slot, IMAX)
    a = g.groupby(["th", "pool"]).agg(bsol=("bsol", "sum"), ssol=("ssol", "sum"), btok=("btok", "sum"), stok=("stok", "sum"), nb=("isbuy", "sum"),
                                       n=("isbuy", "size"), fb=("bslot", "min"), fs=("sslot", "min")).reset_index()
    sb = g[g.isbuy][["th", "pool", "slot"]].drop_duplicates(); ss = g[~g.isbuy][["th", "pool", "slot"]].drop_duplicates()
    same = sb.merge(ss, on=["th", "pool", "slot"])[["th", "pool"]].drop_duplicates(); same["same"] = True
    a = a.merge(same, on=["th", "pool"], how="left"); a["same"] = a.same.fillna(False).astype(bool); a["day"] = day
    pools.append(a)
a = pd.concat(pools)
# a (wallet,pool) may span several days: re-aggregate across days
a = a.groupby(["th", "pool"]).agg(bsol=("bsol", "sum"), ssol=("ssol", "sum"), btok=("btok", "sum"), stok=("stok", "sum"), nb=("nb", "sum"), n=("n", "sum"),
                                   fb=("fb", "min"), fs=("fs", "min"), same=("same", "any")).reset_index()
h = pd.concat(hours).groupby(["th", "hr"]).n.sum().reset_index()
b1 = set(h[h.n > N_HOUR].th)
rt = a[(a.btok > 0) & (a.stok > 0)].copy()
rt["net"] = rt.ssol * np.minimum(1.0, rt.btok / rt.stok) - rt.bsol * np.minimum(1.0, rt.stok / rt.btok)
rt["fast"] = (rt.fs - rt.fb) < 2
w = rt.groupby("th").agg(score=("net", "sum"), n_rt=("net", "size"), frac_fast=("fast", "mean"), b2=("same", "any")).reset_index()
w["b1"] = w.th.isin(b1); w["b3"] = w.frac_fast >= 1 / 3; w["bot"] = w.b1 | w.b2 | w.b3
rk = w[w.n_rt >= MIN_RT].sort_values("score", ascending=False)
print(f"ranking days {len(adays)}; (wallet,pool) rows {len(a)}; round-trip wallets {len(w)}; ranked (>=5 rt) {len(rk)}")
if len(rk):
    print("bot flags among ranked: B1", int(rk.b1.sum()), "B2", int(rk.b2.sum()), "B3", int(rk.b3.sum()), "any", int(rk.bot.sum()), "of", len(rk))
    print("unfiltered top 50 flagged", int(rk.head(TOPN).bot.sum()))
clean = rk[~rk.bot]; top = clean.head(TOPN).copy()
print("clean ranked", len(clean), "top50 n", len(top), "score sum SOL", top.score.sum() / 1e9 if len(top) else 0)
top.to_parquet(OUT + "_top50.parquet")
wset = np.sort(top.th.values.astype(np.uint64))
if len(top) == 0:
    print("NOT_DECIDABLE: empty copy list"); sys.exit(0)

# ---------------- copy test ----------------
rows = []; skipped = dict(entry_after_lead_sell=0, no_sps=0, dust=0)
sdays = ndays(BLK_SEP)
for day in sdays:
    d = load_day(day, BLK_SEP)
    if d is None:
        continue
    hit = d[d.th.isin(wset) & d.isbuy & (d.sol <= SEED_BIG) & (d.sol != SEED_ROW) & (d.mc >= LO) & (d.mc < HI)].pool.unique()
    for pool, g in d[d.pool.isin(hit)].groupby("pool", sort=False):
        sps = pool_sps(g)
        if not (0.15 < sps < 0.6):
            skipped["no_sps"] += 1; continue
        kb = math.ceil(LEG_B / sps - 1e-9); k2 = int(round(2.0 / sps)); ktimer = int(round(TIMER_S / sps))
        for lo, hi in segments(g):
            h_ = g.iloc[lo:hi]
            sl = h_.slot.values; q = h_.q.values + h_.v.values; b = h_.b.values; isb = h_.isbuy.values
            sol = h_.sol.values.astype(float); tok = h_.tok.values; th = h_.th.values; mc = h_.mc.values
            trade = (sol <= SEED_BIG) & (sol != SEED_ROW)
            qpost = np.r_[q[1:], q[-1] + np.where(isb[-1], 0.9875 * sol[-1], -sol[-1] / 0.9875)]
            bpost = np.r_[b[1:], b[-1] - (tok[-1] if isb[-1] else -tok[-1])]
            def state(X):
                j = np.searchsorted(sl, X, "right")
                return (q[j], b[j]) if j < len(sl) else (qpost[-1], bpost[-1])
            lead = np.isin(th, wset) & trade
            for i in np.flatnonzero(lead & isb & (mc >= LO) & (mc < HI)):
                if sol[i] < MIN_LEAD:
                    skipped["dust"] += 1; continue
                wl = th[i]; X = int(sl[i]) + kb
                sells = np.flatnonzero((th == wl) & (~isb) & trade & (sl > sl[i]))
                s_slot = int(sl[sells[0]]) if len(sells) else None
                tim_trig = min(X + ktimer, int(sl[-1])); tim_land = tim_trig + EL2
                land_w = s_slot + kb if s_slot is not None else None
                if land_w is not None and land_w <= X:
                    skipped["entry_after_lead_sell"] += 1; continue
                if X >= sl[-1]:
                    continue
                if land_w is not None and land_w <= tim_land:
                    land, kind = land_w, "lead_sell"
                else:
                    land, kind = tim_land, "timer"
                ix = np.searchsorted(sl, X, "left"); hi_ = np.searchsorted(sl, X, "right"); lo2 = np.searchsorted(sl, X - k2, "left")
                row = dict(day=day, blk=h_.blk.values[0], mint=pool, wallet=int(wl), lead_slot=int(sl[i]), X=X, kind=kind, land=int(land),
                           trunc=bool(kind == "timer" and tim_trig == int(sl[-1])), ssb=int(isb[ix:hi_].sum()), nb=float(sol[lo2:hi_][isb[lo2:hi_]].sum()), lead_sol=sol[i])
                if not COUNT:
                    qe, be = state(X)
                    for sk, S in STAKES.items():
                        f = tier(qe, be); net = S * (1 - f); tk = be * net / (qe + net)
                        qx, bx = state(land); Q = qx + net; B = bx - tk
                        row[f"pnl_{sk}"] = tk * Q / (B + tk) * (1 - tier(Q, B + tk)) - S - 2 * PRIO
                rows.append(row)
    print(day, len(rows), flush=True)
x = pd.DataFrame(rows); x.to_parquet(OUT + "_copy.parquet")
print("skipped", skipped, "| copies", len(x), "days", x.day.nunique() if len(x) else 0, "of", len(sdays), "wallets with a copy", x.wallet.nunique() if len(x) else 0)
if len(x) and not COUNT:
    print("kinds", x.kind.value_counts().to_dict(), "trunc", int(x.trunc.sum()))
    for sk, S in STAKES.items():
        report(x.assign(pnl=x[f"pnl_{sk}"]), S, len(sdays), f"P71 copy in band, stake {S / 1e9} SOL")
    wal = x.assign(pnl=x.pnl_s01).groupby("wallet").pnl.sum() / 1e9
    print("best wallet share of total (nofail, 0.1 SOL):", wal.max() / wal.sum() if wal.sum() else None, "wallets with a priced copy", len(wal))
