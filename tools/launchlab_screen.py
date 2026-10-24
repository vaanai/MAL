#!/usr/bin/env python3
"""launchlab_screen: in-sample SCREEN pricer for Raydium LaunchLab on-curve rules (P11, push 10-24). PAPER ONLY. A screen is a cheap kill test, not evidence.

    python -m tools.launchlab_screen --measure --out DIR            # curve / fee / structure measurements on the exploration hours
    python -m tools.launchlab_screen --count-only --out DIR         # attempts per day per cell, no price
    python -m tools.launchlab_screen --out DIR                      # price the cells

Data: ONLY exploration hours [2026-09-24T00, 2026-10-17T00) of /data2/mal/venue-tape-backfill/launchlab/ (tools.launchlab_price.check_hour_path refuses the reserved holdout
[10-17T00, 10-24T00) and everything else). Rules and pricing are declared in docs/research/launchlab-p11/ (IDEAS.md, DECLARE.md) before any count.

Pricing (tools.launchlab_price): decision at the signal print's slot; the buy lands on the state after every print with slot <= decision + k (k = round(1.9 s / slot_s), slot_s per UTC
hour from the tape); exits (time cap, tp / sl / trail on the post-trade spot of prints at slot >= decision + k + 1) land k slots after their decision. Constant-product curve with the
pool's last observed fee rate per side; own impact at the stake; own buy re-injected at exit; guard 1.15; sends 160,000 x 2; rent 0 (deciding) and 2,039,280 (reported).
Fail models: flat 15% and pressure (slope scale 1, intercept refit per cell, mean p 0.289) via tools.arena_stack.fail_legs. Ssb = buy prints in the landing slot, nearby = buy lamports in the
2 s before it (as the pump kit). A position whose sell lands after the curve ended (pool_status != fund seen) is priced stranded and counted.
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import hashlib
import json
import math
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np

from tools import arena_stack as F
from tools import launchlab_price as L

SCHEMA = "launchlab_screen_v1"
WARMUP_HOURS = 1
MIN_ATTEMPTS_PER_DAY = 3.0
MIN_N, MIN_DAYS = 100, 5
P_LO_ENTRY = 0.0
P_MAX_ENTRY = 0.85


# ------------------------------------------------------------------------------------------------------------------------------ rules (declared in IDEAS.md)
@dataclass(frozen=True)
class Exit:
    hold_s: float
    tp: float | None = None
    sl: float | None = None
    trail: float | None = None


@dataclass(frozen=True)
class CellDef:
    idea: str
    name: str
    exit: Exit
    cooldown_s: float
    params: tuple


CELLS: tuple[CellDef, ...] = (
    CellDef("I1", "m4", Exit(600, sl=0.90, trail=0.90), 600, (4, 1.0)),
    CellDef("I1", "m6", Exit(600, sl=0.90, trail=0.90), 600, (6, 2.0)),
    CellDef("I1", "m10", Exit(600, sl=0.90, trail=0.90), 600, (10, 4.0)),
    CellDef("I2", "ms30", Exit(900, tp=1.15, sl=0.90), 1e12, (0.30,)),
    CellDef("I2", "ms50", Exit(900, tp=1.15, sl=0.90), 1e12, (0.50,)),
    CellDef("I2", "ms70", Exit(900, tp=1.15, sl=0.90), 1e12, (0.70,)),
    CellDef("I3", "bb1", Exit(300, sl=0.90, trail=0.90), 120, (1.0,)),
    CellDef("I3", "bb2", Exit(300, sl=0.90, trail=0.90), 120, (2.0,)),
    CellDef("I3", "bb4", Exit(300, sl=0.90, trail=0.90), 120, (4.0,)),
    CellDef("I4", "c3", Exit(180, tp=1.10, sl=0.92), 120, (3, 1.0, 0.10)),
    CellDef("I4", "c4", Exit(180, tp=1.10, sl=0.92), 120, (4, 2.0, 0.15)),
    CellDef("I4", "c6", Exit(180, tp=1.10, sl=0.92), 120, (6, 4.0, 0.20)),
)
SOL = 1e9


class Pool:
    """One pool's prints in tape order with the causal features the rules read (all arrays indexed by print)."""

    def __init__(self, a: dict[str, np.ndarray], sl: slice, pid: int):
        g = lambda k: a[k][sl]  # noqa: E731
        self.pid = pid
        self.slot, self.bt, self.side, self.status = g("slot"), g("bt"), g("side"), g("status")
        self.vq, self.vb, self.tbs = g("vq"), g("vb"), g("tbs")
        self.rq0, self.rb0, self.rq1, self.rb1 = g("rq0"), g("rb0"), g("rq1"), g("rb1")
        self.ain, self.aout, self.fees = g("ain"), g("aout"), g("fees")
        self.exact_in = g("exact_in")
        n = len(self.slot)
        self.n = n
        self.p_pre = L.spot(self.vq, self.vb, self.rq0, self.rb0)
        self.p_post = L.spot(self.vq, self.vb, self.rq1, self.rb1)
        self.prog_pre = self.rb0 / self.tbs
        self.prog_post = self.rb1 / self.tbs
        buy = self.side == 1
        self.buy_sol = np.where(buy, self.ain, 0.0)  # lamports in (gross of fee)
        self.sell_sol = np.where(~buy, self.aout + self.fees, 0.0)  # gross output
        rate = L.trade_fee_rate(self.side, self.ain, self.aout, self.fees)
        self.fee_buy = _ffill(np.where(buy & np.isfinite(rate), rate, np.nan), L.FALLBACK_FEE_BUY)
        self.fee_sell = _ffill(np.where(~buy & np.isfinite(rate), rate, np.nan), L.FALLBACK_FEE_SELL)
        z = lambda x: np.concatenate([[0.0], np.cumsum(x)])  # noqa: E731
        self.c_buy_n, self.c_sell_n = z(buy.astype(float)), z((~buy).astype(float))
        self.c_buy_sol, self.c_sell_sol = z(self.buy_sol), z(self.sell_sol)

    def win_lo(self, i: int, w_s: float) -> int:
        return int(np.searchsorted(self.bt, self.bt[i] - w_s, "left"))


def _ffill(x: np.ndarray, fallback: float) -> np.ndarray:
    idx = np.where(np.isfinite(x), np.arange(len(x)), -1)
    np.maximum.accumulate(idx, out=idx)
    out = np.where(idx >= 0, x[np.maximum(idx, 0)], fallback)
    return out


def _in_universe(P: "Pool") -> np.ndarray:
    """Declared universe U (DECLARE amendment 1, after the measure step, before any count): the standard curve, total_base_sell = 793.1e12 atoms and virtual_quote in [20e9, 40e9].
    The tape has no quote mint, so a quote of another token with a similar atom magnitude cannot be told apart (said in the report)."""
    return (np.abs(P.tbs - 793.1e12) < 1e9) & (P.vq >= 20e9) & (P.vq <= 40e9)


def triggers_for(cell: CellDef, P: Pool, first_ok_bt: float) -> list[int]:
    """Indices of prints at which the cell fires (before cooldown). Decides on rows <= the print only."""
    n = P.n
    if n < 3:
        return []
    out: list[int] = []
    buy = P.side == 1
    ok_entry = _in_universe(P) & (P.status == 0) & (P.prog_post <= P_MAX_ENTRY) & (P.bt >= first_ok_bt) & (P.exact_in == 1)
    if cell.idea == "I1":
        N, S = cell.params
        cand = np.nonzero(buy & ok_entry & (P.prog_post >= 0.02) & (P.prog_post <= 0.35))[0]
        for i in cand:
            lo = P.win_lo(i, 60.0)
            nb = P.c_buy_n[i + 1] - P.c_buy_n[lo]
            if nb < N:
                continue
            net = (P.c_buy_sol[i + 1] - P.c_buy_sol[lo]) - (P.c_sell_sol[i + 1] - P.c_sell_sol[lo])
            if net < S * SOL:
                continue
            mv = P.p_post[i] / P.p_pre[lo] - 1.0
            if 0.05 <= mv <= 0.40:
                out.append(int(i))
    elif cell.idea == "I2":
        (m,) = cell.params
        cross = np.nonzero(buy & ok_entry & (P.prog_pre < m) & (P.prog_post >= m))[0]
        for i in cross[:1]:  # first crossing per pool
            lo = P.win_lo(i, 120.0)
            net = (P.c_buy_sol[i + 1] - P.c_buy_sol[lo]) - (P.c_sell_sol[i + 1] - P.c_sell_sol[lo])
            if net >= 1.0 * SOL:
                out.append(int(i))
        if len(cross) and not out:
            # the first crossing failed the flow condition; later re-crossings are NOT tried (one look per pool, declared)
            pass
    elif cell.idea == "I3":
        (M,) = cell.params
        imp = P.p_post / P.p_pre - 1.0
        cand = np.nonzero(buy & ok_entry & (P.ain >= M * SOL) & (imp >= 0.02) & (imp <= 0.25) & (P.prog_post >= 0.02) & (P.prog_post <= 0.80))[0]
        out = [int(i) for i in cand]
    elif cell.idea == "I4":
        K, S, d = cell.params
        cand = np.nonzero((~buy) & ok_entry & (P.prog_post >= 0.05))[0]
        for i in cand:
            lo = P.win_lo(i, 30.0)
            ns = P.c_sell_n[i + 1] - P.c_sell_n[lo]
            if ns < K:
                continue
            if (P.c_sell_sol[i + 1] - P.c_sell_sol[lo]) < S * SOL:
                continue
            peak = max(float(P.p_pre[lo]), float(P.p_post[lo:i + 1].max()))
            if P.p_post[i] <= peak * (1.0 - d):
                out.append(int(i))
    return out


# ------------------------------------------------------------------------------------------------------------------------------ loading
def load_exploration(root: str = L.BACKFILL_ROOT, log=print, start: datetime | None = None, end: datetime | None = None) -> dict[str, Any]:
    """All exploration hours present -> columns sorted by (pool, original order). Hours missing or incomplete are listed in `bad_hours`."""
    hours = L.exploration_hours(start, end)
    cols: list[dict[str, np.ndarray]] = []
    pool_names: list[np.ndarray] = []
    pid_of: dict[str, int] = {}
    present, bad = [], []
    slot_s: dict[int, float] = {}
    for h in hours:
        f = L.hour_file(h, root)
        hk = int(h.timestamp() // 3600)
        if not os.path.exists(f):
            bad.append(hk)
            continue
        rows, incomplete = L.read_hour(f, root)
        if incomplete:
            bad.append(hk)
            continue
        present.append(hk)
        if not rows:
            continue
        a = L.rows_to_arrays(rows)
        ids = np.empty(len(rows), np.int64)
        for i, r in enumerate(rows):
            ids[i] = pid_of.setdefault(r["pool"], len(pid_of))
        a["pid"] = ids
        cols.append(a)
        sl, bt = a["slot"], a["bt"]
        if len(rows) > 50 and bt.max() > bt.min():
            slot_s[hk] = float((bt.max() - bt.min()) / max(sl.max() - sl.min(), 1))
        log(f"loaded {h:%Y-%m-%dT%H} rows={len(rows)}")
    if not cols:
        raise L.Refused("no exploration hours present")
    A = {k: np.concatenate([c[k] for c in cols]) for k in cols[0]}
    order = np.argsort(A["pid"], kind="stable")
    A = {k: v[order] for k, v in A.items()}
    return {"start_ts": hours[0].timestamp(), "root": root, "a": A, "bad_hours": sorted(bad), "good_hours": sorted(present), "slot_s": slot_s, "n_pools": len(pid_of), "pool_names": list(pid_of)}


def slot_s_for(slot_s: dict[int, float], hk: int) -> float:
    v = slot_s.get(hk)
    return v if v and 0.1 <= v <= 0.6 else L.SLOT_S_FALLBACK


def k_slots(lat_s: float, ss: float) -> int:
    return max(1, int(round(lat_s / ss)))


# ------------------------------------------------------------------------------------------------------------------------------ attempt resolution
def resolve(cell: CellDef, P: Pool, idx: list[int], D: dict[str, Any], lat_in: float = L.LAT_S, lat_out: float = L.LAT_S) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Cooldown + exits + landing states for the triggers of one pool. Returns (attempt dicts, refusal counts)."""
    refused = {"bad_hour": 0, "block_edge": 0, "cooldown": 0, "no_state": 0}
    out = []
    busy_until_bt = -1.0
    good = set(D["good_hours"])
    end_bt = (max(D["good_hours"]) + 1) * 3600.0
    ex = cell.exit
    for i in idx:
        bt = float(P.bt[i])
        if bt < busy_until_bt:
            refused["cooldown"] += 1
            continue
        hk = int(bt // 3600)
        ss = slot_s_for(D["slot_s"], hk)
        k_in, k_out = k_slots(lat_in, ss), k_slots(lat_out, ss)
        sd = int(P.slot[i])
        hold_slots = max(1, int(round(ex.hold_s / ss)))
        cap = sd + hold_slots
        # hours the attempt touches: the feature window (-120 s) to the exit landing (+ margin)
        last_bt = bt + ex.hold_s + 2 * lat_out + 30
        if last_bt >= end_bt:
            refused["block_edge"] += 1
            continue
        if any(h not in good for h in range(int((bt - 120) // 3600), int(last_bt // 3600) + 1)):
            refused["bad_hour"] += 1
            continue
        ref = float(P.p_post[i])
        # entry state: after all prints with slot <= sd + k_in
        x = sd + k_in
        je = int(np.searchsorted(P.slot, x, "right")) - 1
        if je < i:
            refused["no_state"] += 1
            continue
        # exit scan over prints with slot in [sd + k_in + 1, cap]
        m0 = int(np.searchsorted(P.slot, sd + k_in + 1, "left"))
        m1 = int(np.searchsorted(P.slot, cap, "right"))
        fire_slot, reason = cap, "time"
        if m1 > m0 and (ex.tp or ex.sl or ex.trail):
            pp = P.p_post[m0:m1]
            hit = np.zeros(len(pp), bool)
            if ex.tp:
                hit |= pp >= ref * ex.tp
            if ex.sl:
                hit |= pp <= ref * ex.sl
            if ex.trail:
                pk = np.maximum.accumulate(np.maximum(pp, ref))
                hit |= pp <= pk * ex.trail
            if hit.any():
                j = int(np.argmax(hit))
                fire_slot = int(P.slot[m0 + j])
                v = float(pp[j])
                reason = "tp" if (ex.tp and v >= ref * ex.tp) else ("sl" if (ex.sl and v <= ref * ex.sl) else "trail")
        xe = fire_slot + k_out
        jx = int(np.searchsorted(P.slot, xe, "right")) - 1
        ended = bool((P.status[: jx + 1] != 0).any())
        # busy until the exit decision + cooldown (in seconds on the block-time axis)
        exit_bt = bt + (fire_slot - sd) * ss
        busy_until_bt = exit_bt + cell.cooldown_s
        lo2 = int(np.searchsorted(P.slot, x - int(round(2.0 / ss)), "left"))
        hi2 = int(np.searchsorted(P.slot, x, "right"))
        ix = int(np.searchsorted(P.slot, x, "left"))
        out.append({
            "pid": P.pid, "bt": bt, "date": datetime.fromtimestamp(bt, tz=timezone.utc).strftime("%Y-%m-%d"), "i": i, "reason": reason, "ended": ended,
            "vq": float(P.vq[je]), "vb": float(P.vb[je]), "rq_e": float(P.rq1[je]), "rb_e": float(P.rb1[je]), "spot": ref,
            "rq_x": float(P.rq1[jx]), "rb_x": float(P.rb1[jx]), "fee_in": float(P.fee_buy[i]), "fee_out": float(P.fee_sell[jx]),
            "ssb": float(P.c_buy_n[hi2] - P.c_buy_n[ix]), "nearby": float(P.c_buy_sol[hi2] - P.c_buy_sol[lo2]), "hold_real_s": (fire_slot - sd) * ss,
            "prog": float(P.prog_post[i]),
        })
    return out, refused


def cell_attempts(cell: CellDef, D: dict[str, Any], lat_in: float = L.LAT_S, lat_out: float = L.LAT_S) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, int]]:
    A = D["a"]
    pid = A["pid"]
    bounds = np.flatnonzero(np.diff(pid)) + 1
    starts = np.concatenate([[0], bounds])
    ends = np.concatenate([bounds, [len(pid)]])
    first_ok_bt = D["start_ts"] + WARMUP_HOURS * 3600
    att: list[dict[str, Any]] = []
    ref_tot = {"bad_hour": 0, "block_edge": 0, "cooldown": 0, "no_state": 0}
    stats = {"pools": 0, "trigger_prints": 0}
    for s, e in zip(starts, ends):
        if e - s < 3:
            continue
        P = Pool(A, slice(int(s), int(e)), int(pid[s]))
        stats["pools"] += 1
        idx = triggers_for(cell, P, first_ok_bt)
        if not idx:
            continue
        stats["trigger_prints"] += len(idx)
        a, r = resolve(cell, P, idx, D, lat_in, lat_out)
        att += a
        for k, v in r.items():
            ref_tot[k] += v
    return att, ref_tot, stats


# ------------------------------------------------------------------------------------------------------------------------------ pricing and stats
def date_boot_lo(dates: list[str], x: np.ndarray, draws: int = F.BOOT_DRAWS, seed: int = F.BOOT_SEED) -> float | None:
    ud = sorted(set(dates))
    if len(ud) < 2:
        return None
    d = np.array(dates)
    sums = np.array([x[d == u].sum() for u in ud])
    cnts = np.array([(d == u).sum() for u in ud], float)
    rng = np.random.default_rng(seed)
    ms = []
    for _ in range(draws):
        pick = rng.integers(0, len(ud), len(ud))
        ms.append(sums[pick].sum() / cnts[pick].sum())
    return float(np.percentile(ms, F.BOOT_PCT))


def stat(x: np.ndarray, dates: list[str]) -> dict[str, Any]:
    x = np.asarray(x, float)
    n = len(x)
    if n == 0:
        return {"n": 0}
    srt = np.sort(x)[::-1]
    d = np.array(dates)
    per_day = {u: float(x[d == u].sum()) for u in sorted(set(dates))}
    best = max(per_day, key=per_day.get)
    return {
        "n": n, "mean": float(x.mean()), "total": float(x.sum()), "ex_top3": float(srt[3:].sum()) if n > 3 else None,
        "ex_best_day": float(x.sum() - per_day[best]), "best_day": best, "days": len(per_day), "days_pos": sum(v > 0 for v in per_day.values()),
        "ci90_lo_trade": F.boot_lo(x), "ci90_lo_date": date_boot_lo(dates, x), "per_day": per_day,
    }


def price_cell(att: list[dict[str, Any]], stake_sol: float, rent: int = L.RENT_DECIDING) -> dict[str, Any]:
    g = lambda k: np.array([a[k] for a in att], float)  # noqa: E731
    stake = stake_sol * SOL
    pnl, filled, stranded = L.round_trip(stake, g("vq"), g("vb"), g("rq_e"), g("rb_e"), g("spot"), g("fee_in"), g("rq_x"), g("rb_x"), g("fee_out"),
                                         rent=rent, ended=np.array([a["ended"] for a in att]), return_stranded=True)
    flat, press, c, p = F.fail_legs(pnl, filled, g("ssb"), g("nearby"), send=L.SEND_LAMPORTS)
    dates = [a["date"] for a in att]
    res = {"filled": int(filled.sum()), "stranded": int(stranded.sum()), "guard_unfilled": int((~filled).sum()), "press_intercept": c, "mean_p": float(p[filled].mean()) if filled.any() else None}
    for name, v in (("flat", flat), ("press", press), ("nofail", pnl)):
        res[name] = stat(v / SOL, dates)
    return res


def label(cell_res: dict[str, Any]) -> str:
    r = cell_res.get("deciding")
    if not r or not r.get("flat", {}).get("n"):
        return "NO-ATTEMPTS"
    sub = [r["flat"], r["press"]]
    pos = all(s["mean"] > 0 and (s["ex_top3"] or 0) > 0 and s["ex_best_day"] > 0 for s in sub)
    full = pos and all((s["ci90_lo_trade"] or -1) > 0 and (s["ci90_lo_date"] or -1) > 0 and s["n"] >= MIN_N and s["days"] >= MIN_DAYS and s["days_pos"] * 2 > s["days"] for s in sub)
    if full:
        return "SCREEN-PASS-CANDIDATE"
    if pos:
        return "MARGINAL"
    return "FAIL"


# ------------------------------------------------------------------------------------------------------------------------------ measure
def measure(D: dict[str, Any]) -> dict[str, Any]:
    A = D["a"]
    n = len(A["slot"])
    out: dict[str, Any] = {"rows": n, "pools": D["n_pools"], "good_hours": len(D["good_hours"]), "bad_hours": D["bad_hours"]}
    buy = A["side"] == 1
    ex = A["exact_in"] == 1
    out["exact_out_rows"] = int((~ex).sum())
    with np.errstate(all="ignore"):
        net = A["ain"] - A["fees"]
        bo = np.floor((A["vb"] - A["rb0"]) * net / (A["vq"] + A["rq0"] + net))
        b_ok = buy & ex & (bo == A["aout"]) & (A["rb1"] - A["rb0"] == A["aout"]) & (A["rq1"] - A["rq0"] == net)
        gr = np.floor((A["vq"] + A["rq0"]) * A["ain"] / (A["vb"] - A["rb0"] + A["ain"]))
        s_ok = (~buy) & ex & (gr == A["rq0"] - A["rq1"]) & (A["aout"] == gr - A["fees"]) & (A["rb0"] - A["rb1"] == A["ain"])
        b_close = buy & ex & (np.abs(bo - A["aout"]) <= 1)
        s_close = (~buy) & ex & (np.abs(gr - (A["rq0"] - A["rq1"])) <= 1)
    out["buy_rows_exact_in"] = int((buy & ex).sum())
    out["buy_exact_share"] = float(b_ok.sum() / max((buy & ex).sum(), 1))
    out["buy_within_1_share"] = float(b_close.sum() / max((buy & ex).sum(), 1))
    out["sell_rows_exact_in"] = int(((~buy) & ex).sum())
    out["sell_exact_share"] = float(s_ok.sum() / max(((~buy) & ex).sum(), 1))
    out["sell_within_1_share"] = float(s_close.sum() / max(((~buy) & ex).sum(), 1))
    rate = L.trade_fee_rate(A["side"], A["ain"], A["aout"], A["fees"])
    for nm, m in (("buy", buy), ("sell", ~buy)):
        r = rate[m & np.isfinite(rate)]
        q = np.quantile(r, [0.01, 0.1, 0.5, 0.9, 0.99]) if len(r) else []
        vals, cnt = np.unique(np.round(r, 4), return_counts=True)
        top = sorted(zip(cnt.tolist(), vals.tolist()), reverse=True)[:8]
        out[f"fee_rate_{nm}"] = {"quantiles_1_10_50_90_99": [float(v) for v in q], "top_rounded_rates": [{"rate": v, "rows": c} for c, v in top]}
    key = np.stack([A["vq"], A["vb"], A["tbs"]], 1)
    uk, uc = np.unique(key, axis=0, return_counts=True)
    order = np.argsort(-uc)[:8]
    out["curve_params_top"] = [{"virtual_quote": float(uk[i][0]), "virtual_base": float(uk[i][1]), "total_base_sell": float(uk[i][2]), "rows": int(uc[i])} for i in order]
    out["curve_param_sets"] = int(len(uk))
    out["status_counts"] = {nm: int((A["status"] == v).sum()) for nm, v in (("fund", 0), ("migrate", 1), ("trade", 2))}
    fund = A["status"] == 0
    out["max_real_quote_after_fund_sol"] = float(A["rq1"][fund].max() / SOL) if fund.any() else None
    out["max_progress_fund"] = float((A["rb1"][fund] / A["tbs"][fund]).max()) if fund.any() else None
    mig = cre = 0
    for hk in D["good_hours"]:
        h = datetime.fromtimestamp(hk * 3600, tz=timezone.utc)
        m, c = L.count_migrate_ix(L.hour_file(h, D.get("root", L.BACKFILL_ROOT)), D.get("root", L.BACKFILL_ROOT))
        mig, cre = mig + m, cre + c
    out["migrate_ix_rows"], out["create_rows"], out["good_hours_scanned"] = mig, cre, len(D["good_hours"])
    out["slot_s_by_hour_median"] = float(np.median(list(D["slot_s"].values()))) if D["slot_s"] else None
    sizes = A["ain"][buy] / SOL
    out["buy_sol_quantiles_50_90_99"] = [float(v) for v in np.quantile(sizes, [0.5, 0.9, 0.99])]
    return out


# ------------------------------------------------------------------------------------------------------------------------------ main
def check_out_dir(out: str) -> str:
    real = os.path.realpath(out)
    if real.startswith(os.path.realpath(L.BACKFILL_ROOT)) or real.startswith(os.path.realpath(os.path.dirname(L.BACKFILL_ROOT))):
        raise L.Refused(f"out_dir_inside_backfill_tree: {out}")
    return real


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--count-only", action="store_true")
    ap.add_argument("--cells", default="")
    ap.add_argument("--root", default=L.BACKFILL_ROOT)
    ap.add_argument("--start", default="", help="block start hour YYYY-MM-DDTHH (inside the exploration window)")
    ap.add_argument("--end", default="", help="block end hour (exclusive)")
    ns = ap.parse_args(argv)
    out = Path(check_out_dir(ns.out))
    out.mkdir(parents=True, exist_ok=True)
    cnt = L.count_hour_files(ns.root)
    print(json.dumps({"file_counts": cnt}), flush=True)
    t0 = time.time()
    pa = lambda v: datetime.strptime(v, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc) if v else None  # noqa: E731
    start, end = pa(ns.start), pa(ns.end)
    D = load_exploration(ns.root, start=start, end=end)
    print(f"loaded {len(D['a']['slot'])} trade rows, {D['n_pools']} pools, bad hours {len(D['bad_hours'])} in {time.time() - t0:.0f}s", flush=True)
    meta = {"schema": SCHEMA, "taken_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "file_counts": cnt, "kit_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "price_sha256": hashlib.sha256(Path(L.__file__).read_bytes()).hexdigest(), "bad_hours": D["bad_hours"], "window": [datetime.fromtimestamp(D["start_ts"], tz=timezone.utc).isoformat(), ns.end or L.EXPLORE_END.isoformat()], "block_args": [ns.start, ns.end]}
    if ns.measure:
        (out / "measure.json").write_text(json.dumps({**meta, "measure": measure(D)}, indent=1))
        print((out / "measure.json").read_text())
        return 0
    cells = [c for c in CELLS if not ns.cells or c.name in ns.cells.split(",")]
    days = (L.EXPLORE_END - L.EXPLORE_START).days - 0  # 23 calendar days incl. the warm-up hour; per-day rates use the days with attempts' span
    res: dict[str, Any] = {}
    for c in cells:
        att, refused, st = cell_attempts(c, D)
        dts = sorted({a["date"] for a in att})
        rate = len(att) / max(len(D["good_hours"]) / 24.0, 1e-9)
        entry: dict[str, Any] = {"idea": c.idea, "params": c.params, "exit": c.exit.__dict__, "cooldown_s": c.cooldown_s, "attempts": len(att), "attempts_per_day": rate,
                                 "refused": refused, "pools_scanned": st["pools"], "trigger_prints": st["trigger_prints"], "days_with_attempts": len(dts)}
        if ns.count_only or rate < MIN_ATTEMPTS_PER_DAY or not att:
            entry["priced"] = False
            entry["dropped_below_rate_bar"] = (not ns.count_only) and rate < MIN_ATTEMPTS_PER_DAY
        else:
            entry["priced"] = True
            entry["deciding"] = price_cell(att, L.STAKE_DECIDING)
            entry["stake_1sol"] = price_cell(att, L.STAKE_BIG)
            entry["rent_charged"] = price_cell(att, L.STAKE_DECIDING, rent=L.RENT_REPORT)
            att2, _, _ = cell_attempts(c, D, L.LAT_S, L.EXIT_LAT_SENS_S)
            entry["exit_lag_3p4s"] = price_cell(att2, L.STAKE_DECIDING) if att2 else None
            entry["exits"] = {r: sum(1 for a in att if a["reason"] == r) for r in ("time", "tp", "sl", "trail")}
            entry["label"] = label(entry)
        res[c.name] = entry
        print(c.name, json.dumps({k: entry[k] for k in ("attempts", "attempts_per_day", "priced") if k in entry}), entry.get("label", ""), flush=True)
    (out / ("counts.json" if ns.count_only else "screens.json")).write_text(json.dumps({**meta, "cells": res}, indent=1, default=float))
    return 0


if __name__ == "__main__":
    sys.exit(main())
