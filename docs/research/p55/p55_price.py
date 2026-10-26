"""P55 pricing of the pre-registered BOOST-end exit (docs/research/p55/PREREG.md). Exploration days only.

Run with: /data/mal/audit-1008/venv/bin/python p55_price.py OUT.parquet
Mirrors the frozen s14_boostdip.py entry/exit math (PumpSwap PRE-trade reserves; state after slot X = pre-state of the
first print with slot > X). The only new thing is the exit trigger (E1/E2) and the exit lag.
"""
import math
import sys

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

sys.path.insert(0, "/home/claude/.miscusi/repos/vaanai/MAL")
from tools.paper_curve_math import PUMPSWAP_SOL_FEE_TIERS  # noqa: E402

P = "/data/mal/audit-1008/work/g_reachable_cap_book_rescore"
FROZEN = "/data/mal/hunt-1008/h5-lossfilter/frozen_binding_trades.parquet"
TH = np.array([t for t, _ in PUMPSWAP_SOL_FEE_TIERS], float)
TP = np.array([p for _, p in PUMPSWAP_SOL_FEE_TIERS], float)
PRIO = 55_000
BUDGET = 17.585e9
S = 0.1e9
REM_SMALL = 1.0e9          # E1: R <= 1.0 SOL
STALL_S = 36.0             # E2: seconds without a causal BOOST buy
LEG_B = 1.9
EL_FROZEN_S = 0.55
EL_PRIMARY = 2             # slots


def tier(Q, B):
    return TP[np.searchsorted(TH, Q / B * 1e6 + 1e-9, "right") - 1] / 1e6


def main(out):
    fz = pd.read_parquet(FROZEN)
    res = []
    for day, xd in fz.groupby("day"):
        xd = xd.set_index("mint")
        meta = pd.read_parquet(f"{P}/meta/{day}.parquet")
        meta = meta[(meta.v >= 17.5e9) & (meta.v <= 17.7e9)]
        paths = pq.read_table(f"{P}/paths/{day}.parquet", filters=[("mint", "in", list(xd.index))]).to_pandas()
        for mint, g in paths.groupby("mint", sort=False):
            mm = meta[meta.mint == mint]
            if len(mm) != 1:
                continue
            mm = mm.iloc[0]
            V = float(mm.v)
            s0 = int(mm.s0)
            g = g[g.slot >= s0]
            sl = g.slot.values; q = g.q.values + V; b = g.b.values; isb = g.isbuy.values
            sol = g.sol.values.astype(float); tok = g.tok.values; th = g.th.values; bt = g.bt.values
            ok = bt > 0
            sps = (bt[ok][-1] - bt[ok][0]) / max(1, sl[ok][-1] - sl[ok][0]) if ok.sum() > 2 and sl[ok][-1] > sl[ok][0] + 300 else np.nan
            if not (0.15 < sps < 0.6):
                continue
            tsec = (sl - s0) * sps
            qpost = np.r_[q[1:], q[-1] + np.where(isb[-1], 0.9875 * sol[-1], -sol[-1] / 0.9875)]
            bpost = np.r_[b[1:], b[-1] - (tok[-1] if isb[-1] else -tok[-1])]
            # frozen BOOST wallet (look-ahead identity) only to locate the trigger print, as the frozen code does
            first400 = (sl - s0) <= 1600
            h = pd.DataFrame({"th": th[first400], "sol": sol[first400], "isb": isb[first400]})
            gg = h.groupby("th").agg(tot=("sol", "sum"), ob=("isb", "all"), n=("sol", "size"), mn=("sol", "min"), mx=("sol", "max"))
            cand = gg[(gg.ob) & (gg.n >= 3) & (gg.mn >= 0.2e9) & (gg.mx <= 2.0e9) & (gg.tot <= 17.7e9)].sort_values("n", ascending=False)
            bw = cand.index[0] if len(cand) else -1
            spent = np.cumsum(np.where(th == bw, sol, 0.0))
            trig = np.flatnonzero((qpost / 1e9 <= 40) & (tsec <= 300) & (tsec >= 0) & (~isb) & (spent < BUDGET * 0.999))
            if len(trig) == 0:
                continue
            i = trig[0]
            ref = xd.loc[mint]
            if abs(tsec[i] - ref.trig_t) > 1e-6:
                continue
            si = sl[i]

            def state(X):
                j = np.searchsorted(sl, X, "right")
                return (q[j], b[j]) if j < len(sl) else (qpost[-1], bpost[-1])

            kb = math.ceil(LEG_B / sps - 1e-9)
            X = si + kb
            qe, be = state(X)
            f = tier(qe, be); net = S * (1 - f); tk = be * net / (qe + net)
            xs_frozen = s0 + int(round(330.0 / sps))
            if xs_frozen <= X:
                continue

            def sell(xs, el):
                qx, bx = state(xs + el)
                Q = qx + net; B = bx - tk
                proceeds = tk * Q / (B + tk) * (1 - tier(Q, B + tk))
                return proceeds - S - 2 * PRIO

            # causal detector, walked print by print from the start of the pool to the frozen exit trigger
            wal = {}   # th -> [n, tot, mn, mx, allbuy, last_slot]
            qual = set()
            kstall = math.ceil(STALL_S / sps)
            trig_e1 = trig_e2 = None
            for j in range(len(sl)):
                if sl[j] > xs_frozen:
                    break
                if (sl[j] - s0) <= 1600:
                    w = wal.get(th[j])
                    if w is None:
                        w = wal[th[j]] = [0, 0.0, 1e30, 0.0, True, sl[j]]
                    w[0] += 1; w[1] += sol[j]; w[2] = min(w[2], sol[j]); w[3] = max(w[3], sol[j]); w[4] = w[4] and bool(isb[j])
                    if isb[j]:
                        w[5] = sl[j]
                    if w[4] and w[0] >= 3 and w[2] >= 0.2e9 and w[3] <= 2.0e9 and w[1] <= 17.7e9:
                        qual.add(th[j])
                    else:
                        qual.discard(th[j])
                if sl[j] <= X or not qual:
                    continue
                bwc = max(qual, key=lambda k: wal[k][0])
                rem = BUDGET - wal[bwc][1]
                if trig_e1 is None and rem <= REM_SMALL:
                    trig_e1 = j
                if trig_e2 is None and rem > REM_SMALL and sl[j] - wal[bwc][5] >= kstall:
                    trig_e2 = j
                if trig_e1 is not None and trig_e2 is not None:
                    break
            cands_all = [t for t in (trig_e1, trig_e2) if t is not None]
            xs_e1 = sl[trig_e1] if trig_e1 is not None else None
            xs_e2 = sl[trig_e2] if trig_e2 is not None else None
            xs_new = min([xs_frozen] + [sl[t] for t in cands_all])
            el_f = math.ceil(EL_FROZEN_S / sps - 1e-9)
            row = dict(day=day, mint=mint, blk=ref.blk, set=ref["set"], sps=sps, stored_pnl=ref.pnl,
                       pnl_base_frozenlag=sell(xs_frozen, el_f), pnl_base_el2=sell(xs_frozen, EL_PRIMARY),
                       pnl_new_el2=sell(xs_new, EL_PRIMARY), pnl_new_frozenlag=sell(xs_new, el_f),
                       pnl_e1only_el2=sell(min(xs_frozen, xs_e1) if xs_e1 else xs_frozen, EL_PRIMARY),
                       pnl_e2only_el2=sell(min(xs_frozen, xs_e2) if xs_e2 else xs_frozen, EL_PRIMARY),
                       changed=bool(xs_new < xs_frozen), e1=xs_e1 is not None, e2=xs_e2 is not None,
                       exit_adv_s=(xs_frozen - xs_new) * sps, p_fail=ref.p_fail, stored_flat=ref.flat, stored_press=ref.press)
            res.append(row)
        print(day, len(res), flush=True)
    pd.DataFrame(res).to_parquet(out)


if __name__ == "__main__":
    main(sys.argv[1])
