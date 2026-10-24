#!/usr/bin/env python3
"""launchlab_cpmm_screen: I5 GRAD-FLOOR screen on Raydium CPMM pools that graduated from LaunchLab (P11). PAPER ONLY. A screen is a cheap kill test, not evidence.

    python -m tools.launchlab_cpmm_screen --measure --out DIR
    python -m tools.launchlab_cpmm_screen --out DIR --mig-start 2026-10-07T00 --mig-end 2026-10-17T00     # block A
    python -m tools.launchlab_cpmm_screen --out DIR --mig-start 2026-09-24T00 --mig-end 2026-10-07T00     # block B (only if A survives)

Rule (docs/research/launchlab-p11/DECLARE-I5.md, written before any price): one attempt per WSOL-quoted CPMM pool that graduated from LaunchLab, entry at the first swap at or after
graduation + 300 s; decision at that swap's slot; the buy lands on the state after every swap with slot <= decision + k (k = round(1.9 s / slot_s)); take-profit at 1.5x the decision
spot (post-trade price of a swap at slot >= decision + k + 1), time exit at graduation + 24 h - 30 s (the fetch ends 24 h after graduation), the sell lands k slots after its decision.
CPMM math (verified swap by swap in --measure): out = out_vault * net_in / (in_vault + net_in), net_in = input - trade_fee (- creator_fee when creator_fee_on_input), creator_fee taken
from the output otherwise. Own impact at stake, own buy re-injected, guard 1.15, sends 160,000 x 2, rent 0 (deciding) / 2,039,280 (reported), flat 15% and pressure fail models.
DATA: only /data2/mal/venue-tape-backfill/launchlab_cpmm/pools (exploration-window migrations, history clipped at 2026-10-17T00); any row at or after the holdout start refuses the run.
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from tools import arena_stack as F
from tools import launchlab_price as L
from tools.launchlab_screen import date_boot_lo, slot_s_for, stat

POOL_DIR = "/data2/mal/venue-tape-backfill/launchlab_cpmm/pools"
WSOL = "So11111111111111111111111111111111111111112"
ENTRY_DELAY_S = 300
TP = 1.5
HIST_S = 24 * 3600
EXIT_MARGIN_S = 30
SOL = 1e9
SCHEMA = "launchlab_cpmm_screen_v1"


# ------------------------------------------------------------------------------------------------------------------------ math
def swap_out(vin, vout, amount_in, trade_fee, creator_fee, creator_on_input):
    """Predicted output of one swap (integer-floor in the program; float here). Returns (output_amount_after_fees, gross_before_creator_fee)."""
    net = amount_in - trade_fee - (creator_fee if creator_on_input else 0)
    gross = vout * net / (vin + net)
    return (gross if creator_on_input else gross - creator_fee), gross


def load_pool_file(path: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    end = int(L.EXPLORE_END.timestamp())
    p = subprocess.Popen(["zstd", "-dc", "-q", path], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
    assert p.stdout is not None
    rows = []
    try:
        for line in p.stdout:
            d = json.loads(line)
            if int(d.get("block_time") or 0) >= end:
                raise L.Refused(f"row_at_or_after_holdout_start: {path}")
            rows.append(d)
    finally:
        p.stdout.close()
        p.wait()
    meta = rows[0]
    return meta, [r for r in rows[1:] if r["type"] == "swap"]


def list_pools(mig_start: datetime | None = None, mig_end: datetime | None = None, pool_dir: str = POOL_DIR) -> list[str]:
    out = []
    for n in sorted(os.listdir(pool_dir)):
        if n.startswith("pool-") and n.endswith(".jsonl.zst"):
            out.append(f"{pool_dir}/{n}")
    return out


class CPool:
    def __init__(self, meta: dict[str, Any], swaps: list[dict[str, Any]]):
        self.meta = meta
        self.t0 = int(meta["block_time"])
        n = len(swaps)
        self.n = n
        a = lambda k, dt=np.float64: np.array([s[k] for s in swaps], dt)  # noqa: E731
        self.slot, self.bt = a("slot", np.int64), a("block_time")
        sol_in = np.array([s["input_mint"] == WSOL for s in swaps], bool)
        self.sol_in = sol_in
        vi, vo = a("input_vault_before"), a("output_vault_before")
        self.S0 = np.where(sol_in, vi, vo)  # SOL vault before
        self.T0 = np.where(sol_in, vo, vi)  # token vault before
        amt_in, amt_out = a("input_amount"), a("output_amount")
        tf, cf = a("trade_fee"), a("creator_fee")
        con = np.array([bool(s.get("creator_fee_on_input")) for s in swaps], bool)
        self.tf, self.cf, self.con, self.amt_in, self.amt_out = tf, cf, con, amt_in, amt_out
        # after-state: SOL and token vault after this swap (derived; the next swap's *_before must equal it when no other flow touched the pool)
        net = amt_in - tf - np.where(con, cf, 0.0)
        gross = np.where(con, amt_out, amt_out + cf)  # output before the creator fee (when it comes off the output)
        # The reserve the program prices on excludes protocol / fund / creator fee accruals but keeps the LP share of the trade fee, which the event does not split out:
        # the derived after-state is therefore approximate (LP share of the fee missing, ~0.1-0.2% of the input). It is used only after the LAST swap; elsewhere the next swap's
        # *_vault_before is the exact state.
        self.S1 = np.where(sol_in, self.S0 + net, self.S0 - gross)
        self.T1 = np.where(sol_in, self.T0 - gross, self.T0 + net)
        # price after (SOL per token atom)
        self.p_post = self.S1 / self.T1
        self.p_pre = self.S0 / self.T0
        # observed effective fee rates per direction (causal, last observed)
        r_in = (tf + np.where(con, cf, 0.0)) / np.where(amt_in > 0, amt_in, np.nan)
        r_out = np.where(con, 0.0, cf / np.where(gross > 0, gross, np.nan))
        self.r_in_buy = _ffill(np.where(sol_in, r_in, np.nan), 0.0025 + 0.0005)
        self.r_out_buy = _ffill(np.where(sol_in, r_out, np.nan), 0.0)
        self.r_in_sell = _ffill(np.where(~sol_in, r_in, np.nan), 0.0025)
        self.r_out_sell = _ffill(np.where(~sol_in, r_out, np.nan), 0.0)
        z = lambda x: np.concatenate([[0.0], np.cumsum(x)])  # noqa: E731
        self.c_buy_n = z(sol_in.astype(float))
        self.c_buy_sol = z(np.where(sol_in, amt_in, 0.0))

    def state_after(self, j: int) -> tuple[float, float]:
        """(SOL vault, token vault) after the swap at index j = the next swap's before-state when there is one."""
        if j + 1 < self.n:
            return float(self.S0[j + 1]), float(self.T0[j + 1])
        return float(self.S1[j]), float(self.T1[j])


def _ffill(x: np.ndarray, fallback: float) -> np.ndarray:
    idx = np.where(np.isfinite(x), np.arange(len(x)), -1)
    np.maximum.accumulate(idx, out=idx)
    return np.where(idx >= 0, x[np.maximum(idx, 0)], fallback)


# ------------------------------------------------------------------------------------------------------------------------ attempt
def attempt(P: CPool, slot_s: float, lat_in: float = L.LAT_S, lat_out: float = L.LAT_S) -> dict[str, Any] | str:
    if not P.meta.get("wsol_in_pool"):
        return "non_wsol"
    if P.n < 5:
        return "too_few_swaps"
    k_in = max(1, int(round(lat_in / slot_s)))
    k_out = max(1, int(round(lat_out / slot_s)))
    T = P.t0 + ENTRY_DELAY_S
    end_ts = P.t0 + HIST_S - EXIT_MARGIN_S
    win_end = min(P.t0 + HIST_S, int(L.EXPLORE_END.timestamp()))
    if win_end < P.t0 + HIST_S:
        return "history_clipped_by_holdout_edge"
    if P.meta.get("truncated"):
        return "history_truncated"
    i = int(np.searchsorted(P.bt, T, "left"))
    if i >= P.n or P.bt[i] > T + 600:
        return "no_swap_near_entry_time"
    sd = int(P.slot[i])
    ref = float(P.p_post[i])
    x = sd + k_in
    je = int(np.searchsorted(P.slot, x, "right")) - 1
    S_e, T_e = P.state_after(je)
    # time cap decision slot: the slot of the last swap with bt <= end_ts
    jcap = int(np.searchsorted(P.bt, end_ts, "right")) - 1
    cap = int(P.slot[jcap])
    m0 = int(np.searchsorted(P.slot, sd + k_in + 1, "left"))
    m1 = int(np.searchsorted(P.slot, cap, "right"))
    fire_slot, reason = cap, "time"
    if m1 > m0:
        hit = P.p_post[m0:m1] >= ref * TP
        if hit.any():
            j = int(np.argmax(hit))
            fire_slot, reason = int(P.slot[m0 + j]), "tp"
    xe = fire_slot + k_out
    jx = int(np.searchsorted(P.slot, xe, "right")) - 1
    S_x, T_x = P.state_after(jx)
    lo2 = int(np.searchsorted(P.slot, x - int(round(2.0 / slot_s)), "left"))
    hi2 = int(np.searchsorted(P.slot, x, "right"))
    ix = int(np.searchsorted(P.slot, x, "left"))
    date = datetime.fromtimestamp(P.bt[i], tz=timezone.utc).strftime("%Y-%m-%d")
    return {"pool": P.meta["cpmm_pool"], "date": date, "bt": float(P.bt[i]), "reason": reason, "spot": ref, "S_e": S_e, "T_e": T_e, "S_x": S_x, "T_x": T_x,
            "r_in_buy": float(P.r_in_buy[i]), "r_out_buy": float(P.r_out_buy[i]), "r_in_sell": float(P.r_in_sell[jx]), "r_out_sell": float(P.r_out_sell[jx]),
            "ssb": float(P.c_buy_n[hi2] - P.c_buy_n[ix]), "nearby": float(P.c_buy_sol[hi2] - P.c_buy_sol[lo2]), "ref_price_end_over_ref": float(S_x / T_x / ref) if T_x else None,
            "hold_s": (fire_slot - sd) * slot_s}


def round_trip(stake, a: dict[str, np.ndarray], send=L.SEND_LAMPORTS, rent=L.RENT_DECIDING, guard=L.GUARD, rent_stranded=L.RENT_STRANDED):
    """Vectorised CPMM round trip in lamports. Returns (pnl, filled, stranded)."""
    stake = np.asarray(stake, float)
    with np.errstate(all="ignore"):
        net = stake * (1 - a["r_in_buy"])
        tok = a["T_e"] * net / (a["S_e"] + net) * (1 - a["r_out_buy"])
        bad = ~(np.isfinite(tok) & (tok > 0) & np.isfinite(a["S_x"]) & np.isfinite(a["T_x"]))
        gd = ((stake / tok) / a["spot"] > guard) & ~bad
        S2, T2 = a["S_x"] + net, a["T_x"] - tok  # our buy re-injected (tokens left the vault)
        net_t = tok * (1 - a["r_in_sell"])
        gross = S2 * net_t / (T2 + net_t)
        sv = gross * (1 - a["r_out_sell"])
        st = (bad | ~(gross <= S2) | (sv < send) | ~(T2 > 0)) & ~gd
        pnl = np.where(gd, -float(send), np.where(st, -stake - send - rent_stranded, sv - stake - 2 * send - rent))
    return pnl, ~gd, st


def price(att: list[dict[str, Any]], stake_sol: float, rent: int = L.RENT_DECIDING) -> dict[str, Any]:
    a = {k: np.array([x[k] for x in att], float) for k in ("S_e", "T_e", "S_x", "T_x", "spot", "r_in_buy", "r_out_buy", "r_in_sell", "r_out_sell", "ssb", "nearby")}
    pnl, filled, stranded = round_trip(stake_sol * SOL, a, rent=rent)
    flat, press, c, p = F.fail_legs(pnl, filled, a["ssb"], a["nearby"], send=L.SEND_LAMPORTS)
    dates = [x["date"] for x in att]
    res = {"filled": int(filled.sum()), "stranded": int(stranded.sum()), "guard_unfilled": int((~filled).sum()), "press_intercept": c}
    for nm, v in (("flat", flat), ("press", press), ("nofail", pnl)):
        res[nm] = stat(v / SOL, dates)
    return res


def measure(pools: list[CPool]) -> dict[str, Any]:
    n = ok = close = 0
    rin, rout_c, rin_t = [], [], []
    cont = nxt = 0
    for P in pools:
        if not P.meta.get("wsol_in_pool"):
            continue
        for j in range(P.n):
            net = P.amt_in[j] - P.tf[j] - (P.cf[j] if P.con[j] else 0.0)
            vin = P.S0[j] if P.sol_in[j] else P.T0[j]
            vout = P.T0[j] if P.sol_in[j] else P.S0[j]
            pred, _ = swap_out(vin, vout, P.amt_in[j], P.tf[j], P.cf[j], P.con[j])
            n += 1
            ok += abs(pred - P.amt_out[j]) <= 1
            close += abs(pred - P.amt_out[j]) <= max(2, 1e-9 * P.amt_out[j])
            if j + 1 < P.n:
                nxt += 1
                cont += (abs(P.S1[j] - P.S0[j + 1]) <= 2e-3 * P.S0[j + 1]) and (abs(P.T1[j] - P.T0[j + 1]) <= 2e-3 * P.T0[j + 1])
        sin = P.sol_in
        rin.append(((P.tf + np.where(P.con, P.cf, 0)) / P.amt_in)[sin])
        rin_t.append(((P.tf + np.where(P.con, P.cf, 0)) / P.amt_in)[~sin])
    cat = lambda xs: np.concatenate(xs) if xs else np.array([])  # noqa: E731
    q = lambda x: [float(v) for v in np.quantile(x, [0.1, 0.5, 0.9])] if len(x) else None  # noqa: E731
    return {"wsol_pools": sum(1 for P in pools if P.meta.get("wsol_in_pool")), "swaps_checked": n, "output_within_1_atom": ok / max(n, 1), "output_within_1e-9": close / max(n, 1),
            "state_continuity_within_0.2pct_share": cont / max(nxt, 1), "buy_input_fee_rate_q10_50_90": q(cat(rin)), "sell_input_fee_rate_q10_50_90": q(cat(rin_t))}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--mig-start", default="2026-09-24T00")
    ap.add_argument("--mig-end", default="2026-10-17T00")
    ap.add_argument("--pool-dir", default=POOL_DIR)
    ns = ap.parse_args(argv)
    out = Path(os.path.realpath(ns.out))
    out.mkdir(parents=True, exist_ok=True)
    pa = lambda v: datetime.strptime(v, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp()  # noqa: E731
    lo, hi = pa(ns.mig_start), pa(ns.mig_end)
    if hi > L.EXPLORE_END.timestamp() or lo < L.EXPLORE_START.timestamp():
        raise L.Refused("migration window outside the exploration window")
    pools = []
    for f in list_pools(pool_dir=ns.pool_dir):
        meta, sw = load_pool_file(f)
        if lo <= int(meta["block_time"]) < hi and sw:
            pools.append(CPool(meta, sw))
    all_meta = {"schema": SCHEMA, "taken_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "mig_window": [ns.mig_start, ns.mig_end],
                "kit_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "pools_loaded": len(pools)}
    if ns.measure:
        res = {**all_meta, "measure": measure(pools)}
        (out / "measure.json").write_text(json.dumps(res, indent=1))
        print(json.dumps(res, indent=1))
        return 0
    att, refused = [], {}
    for P in pools:
        ss = 0.22
        r = attempt(P, ss)
        if isinstance(r, str):
            refused[r] = refused.get(r, 0) + 1
        else:
            att.append(r)
    entry: dict[str, Any] = {"attempts": len(att), "refused": refused, "pools_loaded": len(pools), "exits": {r: sum(1 for a in att if a["reason"] == r) for r in ("tp", "time")}}
    if att:
        entry["deciding"] = price(att, L.STAKE_DECIDING)
        entry["stake_1sol"] = price(att, L.STAKE_BIG)
        entry["rent_charged"] = price(att, L.STAKE_DECIDING, rent=L.RENT_REPORT)
        att2 = [a for a in (attempt(P, 0.22, L.LAT_S, L.EXIT_LAT_SENS_S) for P in pools) if not isinstance(a, str)]
        entry["exit_lag_3p4s"] = price(att2, L.STAKE_DECIDING) if att2 else None
        entry["per_pool"] = [{k: a[k] for k in ("pool", "date", "reason", "hold_s", "ref_price_end_over_ref")} for a in att]
    (out / "screens.json").write_text(json.dumps({**all_meta, "cell": "gf_tp150_24h", **entry}, indent=1, default=float))
    print(json.dumps({k: entry[k] for k in ("attempts", "refused", "exits")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
