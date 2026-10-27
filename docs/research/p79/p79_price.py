"""P79 pricing of the pre-registered rule (PREREG.md). Usage: python -I p79_price.py PS_MAYHEM.parquet OUT_TRADES.parquet
Exploration blocks only. PumpSwap PRE-trade reserves, V = 0 (mayhem pool), P55 math. One parameter set."""
import math
import sys

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, "/data2/mal/wt/p79")
from tools.latency_curve import Pressure, fit_curve  # noqa: E402
from tools.paper_curve_math import PUMPSWAP_SOL_FEE_TIERS  # noqa: E402

AGENT = "BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s"
TH = np.array([t for t, _ in PUMPSWAP_SOL_FEE_TIERS], float)
TP = np.array([p for _, p in PUMPSWAP_SOL_FEE_TIERS], float)
PRIO = 55_000
LAG_IN = 2
HORIZON = 20
LAG_OUT = 2
WINDOW0 = 150
MIN_Q = 1.0e9
STAKES = (0.25e9, 1.0e9)


def tier(Q, B):
    return TP[np.searchsorted(TH, Q / B * 1e6 + 1e-9, "right") - 1] / 1e6


def main(src, out):
    c = duckdb.connect()
    d = c.sql(f"""select * from '{src}' where pool in (select distinct pool from '{src}' where trader='{AGENT}')
                  order by pool, slot, tx_index, event_index""").df()
    print("agent pools rows", len(d), "pools", d.pool.nunique(), flush=True)
    rows = []
    cnt = dict(pools=0, signal=0, no_state=0, shallow=0, drained=0)
    for pool, g in d.groupby("pool", sort=False):
        cnt["pools"] += 1
        sl = g.slot.values; isb = (g.side.values == "buy"); sol = g.sol_lamports.values.astype(float)
        q = g.q.values; b = g.b.values; tr = g.trader.values; bt = g.block_time.values
        s0 = sl[0]
        ag = np.flatnonzero(tr == AGENT)
        sig = None
        for k in range(1, len(ag)):
            i, j = ag[k - 1], ag[k]
            if (not isb[i]) and (not isb[j]) and sl[j] - s0 <= WINDOW0:
                sig = j
                break
        if sig is None:
            continue
        cnt["signal"] += 1
        S0 = sl[sig]
        X = S0 + LAG_IN
        last_q = q[-1] + (0.9875 * sol[-1] if isb[-1] else -sol[-1] / 0.9875)
        last_b = b[-1] - 0  # token delta of the last print is not stored in this view; see state()
        tok = g.token_raw.values.astype(float)
        last_b = b[-1] - (tok[-1] if isb[-1] else -tok[-1])

        def state(Y):
            j = np.searchsorted(sl, Y, "right")
            return (q[j], b[j]) if j < len(sl) else (last_q, last_b)

        jx = np.searchsorted(sl, X, "right")
        if jx >= len(sl):
            cnt["no_state"] += 1
            continue
        qe, be = state(X)
        if qe < MIN_Q or be <= 0:
            cnt["shallow"] += 1
            continue
        qx, bx = state(X + HORIZON + LAG_OUT)
        ssb = int(np.sum(isb & (sl == X)))
        nb = float(np.sum(sol[isb & (sl <= X) & (sl > X - 5)]))
        row = dict(pool=pool, mint=g.mint.values[0], day=pd.to_datetime(bt[sig], unit="s").strftime("%Y-%m-%d"), block=g.block.values[0],
                   S0=int(S0), X=int(X), qe=qe, be=be, ssb=ssb, nb=nb)
        for S in STAKES:
            f = tier(qe, be); net = S * (1 - f); tk = be * net / (qe + net)
            Q = qx + net; B = bx - tk
            if B <= 0:
                cnt["drained"] += 1
                pnl = -S - 2 * PRIO
            else:
                proceeds = tk * Q / (B + tk) * (1 - tier(Q, B + tk))
                pnl = proceeds - S - 2 * PRIO
            row[f"pnl_{S/1e9:g}"] = pnl
        rows.append(row)
    print(cnt, flush=True)
    t = pd.DataFrame(rows)
    curve = fit_curve([Pressure(int(a), int(round(n))) for a, n in zip(t.ssb, t.nb)])
    t["p"] = [curve.p(Pressure(int(a), int(round(n)))) for a, n in zip(t.ssb, t.nb)]
    t.to_parquet(out)
    print("entries", len(t), "mean p", t.p.mean(), "drained (pnl set to total loss, counted per stake):", cnt["drained"])


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
