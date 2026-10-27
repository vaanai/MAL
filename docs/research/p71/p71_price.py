"""P71 step 2: copy-test pricing (docs/research/p71/PREREG.md). fresh-0903 and exp011-0909 only.
Run: /data/mal/audit-1008/venv/bin/python p71_price.py TOP50.parquet OUT.parquet [--count]   (--count: no pricing, counts only)
Entry/exit math is p55_price.py / s14_boostdip.py (PumpSwap PRE-trade reserves, V added to q, state after slot X = pre-state of first print with slot > X)."""
import glob, math, os, sys
import numpy as np, pandas as pd
sys.path.insert(0, "/home/claude/.miscusi/repos/vaanai/MAL")
from tools.paper_curve_math import PUMPSWAP_SOL_FEE_TIERS  # noqa: E402

P = "/data/mal/audit-1008/work/g_reachable_cap_book_rescore"
BLOCKS = ("fresh-0903", "exp011-0909")
TH = np.array([t for t, _ in PUMPSWAP_SOL_FEE_TIERS], float); TP = np.array([p for _, p in PUMPSWAP_SOL_FEE_TIERS], float)
PRIO = 55_000
STAKES = {"s01": 0.1e9, "s025": 0.25e9, "s1": 1.0e9}
MIN_LEAD = 0.05e9; SEED_BIG = 100e9; SEED_ROW = 987_654_320
LEG_B, TIMER_S, EL2 = 1.9, 3600.0, 2


def tier(Q, B):
    return TP[np.searchsorted(TH, Q / B * 1e6 + 1e-9, "right") - 1] / 1e6


def main(top_path, out, count_only):
    top = pd.read_parquet(top_path)
    wset = np.sort(top.th.values.astype(np.uint64))
    rows, skipped = [], dict(entry_after_lead_sell=0, no_pool_v_or_sps=0, dust=0)
    ndays = {}
    for f in sorted(glob.glob(f"{P}/meta/*.parquet")):
        day = os.path.basename(f)[:-8]
        m = pd.read_parquet(f)
        m = m[m.blk.isin(BLOCKS) & (m.v >= 17.5e9) & (m.v <= 17.7e9)]
        if m.empty:
            continue
        paths = pd.read_parquet(f"{P}/paths/{day}.parquet")
        paths = paths[paths.mint.isin(m.mint)]
        hit = paths[paths.th.isin(wset) & paths.isbuy & (paths.sol <= SEED_BIG) & (paths.sol != SEED_ROW)].mint.unique()
        m = m.set_index("mint")
        for mint, g in paths[paths.mint.isin(hit)].groupby("mint", sort=False):
            mm = m.loc[mint]; V = float(mm.v); s0 = int(mm.s0); blk = mm.blk
            g = g[g.slot >= s0]
            sl = g.slot.values; q = g.q.values + V; b = g.b.values; isb = g.isbuy.values
            sol = g.sol.values.astype(float); tok = g.tok.values; th = g.th.values; bt = g.bt.values
            ok = bt > 0
            sps = (bt[ok][-1] - bt[ok][0]) / max(1, sl[ok][-1] - sl[ok][0]) if ok.sum() > 2 and sl[ok][-1] > sl[ok][0] + 300 else np.nan
            if not (0.15 < sps < 0.6):
                skipped["no_pool_v_or_sps"] += 1; continue
            trade = (sol <= SEED_BIG) & (sol != SEED_ROW)
            lead = isin_w = np.isin(th, wset) & trade
            qpost = np.r_[q[1:], q[-1] + np.where(isb[-1], 0.9875 * sol[-1], -sol[-1] / 0.9875)]
            bpost = np.r_[b[1:], b[-1] - (tok[-1] if isb[-1] else -tok[-1])]
            kb = math.ceil(LEG_B / sps - 1e-9); k2 = int(round(2.0 / sps)); ktimer = int(round(TIMER_S / sps))

            def state(X):
                j = np.searchsorted(sl, X, "right")
                return (q[j], b[j]) if j < len(sl) else (qpost[-1], bpost[-1])
            for i in np.flatnonzero(lead & isb):
                if sol[i] < MIN_LEAD:
                    skipped["dust"] += 1; continue
                w = th[i]; X = int(sl[i]) + kb
                sells = np.flatnonzero((th == w) & (~isb) & trade & (sl > sl[i]))
                s_slot = int(sl[sells[0]]) if len(sells) else None
                tim_trig = min(X + ktimer, int(sl[-1])); tim_land = tim_trig + EL2
                land_w = s_slot + kb if s_slot is not None else None
                if land_w is not None and land_w <= X:
                    skipped["entry_after_lead_sell"] += 1; continue
                if land_w is not None and land_w <= tim_land:
                    land, kind = land_w, "lead_sell"
                else:
                    land, kind = tim_land, "timer"
                land2 = (s_slot + EL2) if (s_slot is not None and s_slot + EL2 > X and s_slot + EL2 <= tim_land) else None
                ix = np.searchsorted(sl, X, "left"); hi = np.searchsorted(sl, X, "right"); lo2 = np.searchsorted(sl, X - k2, "left")
                ssb = int(isb[ix:hi].sum()); nb = float(sol[lo2:hi][isb[lo2:hi]].sum())
                row = dict(day=day, blk=blk, mint=mint, wallet=int(w), lead_slot=int(sl[i]), X=X, kind=kind, land=int(land),
                           trunc=bool(kind == "timer" and tim_trig == int(sl[-1])), hold_slots=int(land - X), sps=sps, ssb=ssb, nb=nb, lead_sol=sol[i])
                if not count_only:
                    qe, be = state(X)
                    for sk, S in STAKES.items():
                        f = tier(qe, be); net = S * (1 - f); tk = be * net / (qe + net)

                        def sell(L):
                            qx, bx = state(L); Q = qx + net; B = bx - tk
                            return tk * Q / (B + tk) * (1 - tier(Q, B + tk)) - S - 2 * PRIO
                        row[f"pnl_{sk}"] = sell(land)
                        if sk == "s01":
                            row["pnl_s01_lag2"] = sell(land2) if (kind == "lead_sell" and land2 is not None) else (row["pnl_s01"] if kind == "timer" else np.nan)
                rows.append(row)
        print(day, len(rows), flush=True)
    d = pd.DataFrame(rows)
    d.to_parquet(out)
    print("skipped", skipped)
    print("copies", len(d), "days", d.day.nunique(), d.groupby("blk").size().to_dict(), "wallets with a copy", d.wallet.nunique(), "kinds", d.kind.value_counts().to_dict(), "trunc", int(d.trunc.sum()))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], "--count" in sys.argv)
