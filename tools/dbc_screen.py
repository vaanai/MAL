#!/usr/bin/env python3
"""dbc_screen: in-sample SCREEN pricer for Meteora DBC on-curve rules (P14, push 10-24). PAPER ONLY. A screen is a cheap kill test, not evidence.

    python -m tools.dbc_screen --validate --out DIR     # curve reproduction, order fix, fee-mode, fee distribution, capacity (counts only)
    python -m tools.dbc_screen --count-only --out DIR   # attempts per day per cell, no price
    python -m tools.dbc_screen --out DIR                # price the cells

Data: ONLY exploration hours [2026-10-14T00, 2026-10-17T00) of /data2/mal/venue-tape-backfill/meteora_dbc/ (tools.dbc_price.check_hour_path refuses the reserved holdout).
Rules and pricing are declared in docs/research/dbc-p14/ (IDEAS.md, DECLARE.md) before any count. Pricing details: tools/dbc_price.py docstring and DECLARE.md.
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
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from tools import arena_stack as F
from tools import dbc_price as L

SCHEMA = "dbc_screen_v1"
WARMUP_HOURS = 1
MIN_ATTEMPTS_PER_DAY = 3.0
MIN_N, MIN_DAYS = 100, 5
P_MAX_ENTRY = 0.85
THR_MIN = 5e9
SOL = 1e9


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
    CellDef("D1", "m4", Exit(600, sl=0.90, trail=0.90), 600, (4, 1.0)),
    CellDef("D1", "m6", Exit(600, sl=0.90, trail=0.90), 600, (6, 2.0)),
    CellDef("D1", "m10", Exit(600, sl=0.90, trail=0.90), 600, (10, 4.0)),
    CellDef("D2", "ms30", Exit(900, tp=1.15, sl=0.90), 1e12, (0.30,)),
    CellDef("D2", "ms50", Exit(900, tp=1.15, sl=0.90), 1e12, (0.50,)),
    CellDef("D2", "ms70", Exit(900, tp=1.15, sl=0.90), 1e12, (0.70,)),
    CellDef("D3", "bb1", Exit(300, sl=0.90, trail=0.90), 120, (1.0,)),
    CellDef("D3", "bb2", Exit(300, sl=0.90, trail=0.90), 120, (2.0,)),
    CellDef("D3", "bb4", Exit(300, sl=0.90, trail=0.90), 120, (4.0,)),
    CellDef("D4", "c3", Exit(180, tp=1.10, sl=0.92), 120, (3, 1.0, 0.10)),
    CellDef("D4", "c4", Exit(180, tp=1.10, sl=0.92), 120, (4, 2.0, 0.15)),
    CellDef("D4", "c6", Exit(180, tp=1.10, sl=0.92), 120, (6, 4.0, 0.20)),
)


# ------------------------------------------------------------------------------------------------------------------------------ loading
def load_exploration(root: str | None = None, log=print, start: datetime | None = None, end: datetime | None = None) -> dict[str, Any]:
    """All exploration hours present -> columns sorted by (pool, tape order), order-fixed, with derived columns. Hours missing or incomplete are listed in `bad_hours`."""
    hours = L.exploration_hours(start, end)
    cols: list[dict[str, np.ndarray]] = []
    pid_of: dict[str, int] = {}
    cfg_of: dict[str, int] = {}
    hook_pools: set[int] = set()
    complete_slot: dict[int, int] = {}
    n_create = 0
    present, bad = [], []
    slot_s: dict[int, float] = {}
    for h in hours:
        f = L.hour_file(h, root)
        hk = int(h.timestamp() // 3600)
        if not os.path.exists(f):
            bad.append(hk)
            continue
        r = L.read_hour(f, root)
        if r["incomplete"]:
            bad.append(hk)
            continue
        present.append(hk)
        n_create += len(r["creates"])
        for c in r["completes"]:
            p = pid_of.setdefault(c["pool"], len(pid_of))
            complete_slot[p] = min(int(c["slot"]), complete_slot.get(p, 1 << 60))
        rows = [x for x in r["trades"] if L.usable_row(x)]
        if not rows:
            continue
        a = L.rows_to_arrays(rows)
        a["pid"] = np.empty(len(rows), np.int64)
        a["cfg"] = np.empty(len(rows), np.int64)
        for i, x in enumerate(rows):
            p = pid_of.setdefault(x["pool"], len(pid_of))
            a["pid"][i] = p
            a["cfg"][i] = cfg_of.setdefault(x["config"], len(cfg_of))
            if L.hook_row(x):
                hook_pools.add(p)
        cols.append(a)
        sl, bt = a["slot"], a["bt"]
        if len(rows) > 50 and bt.max() > bt.min():
            slot_s[hk] = float((bt.max() - bt.min()) / max(sl.max() - sl.min(), 1))
        log(f"loaded {h:%Y-%m-%dT%H} sol_trades={len(rows)}")
        del r, rows
    if not cols:
        raise L.Refused("no exploration hours present")
    A = {k: np.concatenate([c[k] for c in cols]) for k in cols[0]}
    order = np.argsort(A["pid"], kind="stable")
    A = {k: v[order] for k, v in A.items()}
    pid, cfg = A["pid"], A["cfg"]
    iso = L.isolated(pid, A["slot"])
    d0 = L.derive(A, pid, cfg, iso)
    d0["iso"] = iso
    perm, ostats, badm = L.order_fix(A, pid, d0)
    A = {k: v[perm] for k, v in A.items()}
    pid, cfg = pid[perm], cfg[perm]
    bad_row = badm[perm]
    d = L.derive(A, pid, cfg, ~bad_row)
    D = {"a": A, "d": d, "pid": pid, "bad_row": bad_row, "order_stats": ostats, "start_ts": hours[0].timestamp(), "root": root, "bad_hours": sorted(bad), "good_hours": sorted(present),
         "slot_s": slot_s, "pool_names": list(pid_of), "n_pools": len(pid_of), "n_configs": len(cfg_of), "hook_pools": hook_pools, "complete_slot": complete_slot, "creates": n_create}
    D["hook_row"] = np.isin(pid, np.array(sorted(hook_pools), np.int64))
    return D


def slot_s_for(slot_s: dict[int, float], hk: int) -> float:
    v = slot_s.get(hk)
    return v if v and 0.1 <= v <= 0.6 else L.SLOT_S_FALLBACK


def k_slots(lat_s: float, ss: float) -> int:
    return max(1, int(round(lat_s / ss)))


def day_of(bt: float) -> str:
    return datetime.fromtimestamp(bt, tz=timezone.utc).strftime("%Y-%m-%d")


class Pool:
    """One pool's prints in (fixed) order with the causal features the rules read."""

    def __init__(self, D: dict[str, Any], sl: slice, pid: int):
        A, d = D["a"], D["d"]
        g = lambda k: A[k][sl]  # noqa: E731
        h = lambda k: d[k][sl]  # noqa: E731
        self.pid = pid
        self.slot, self.bt, self.side = g("slot"), g("bt"), g("side")
        self.S1, self.qres, self.thr = g("S1"), g("qres"), g("thr")
        self.S0 = h("S0")
        self.Lp, self.mode, self.rate = h("Lpost"), h("mode"), h("rate")
        self.qleg = h("qleg")
        self.bad = D["bad_row"][sl]
        self.hook = bool(D["hook_row"][sl][0])
        self.mode_known = bool(h("mode_known")[0])
        n = len(self.slot)
        self.n = n
        self.p_post = L.spot(self.S1)
        p_pre = L.spot(self.S0)
        self.p_pre = np.where(np.isfinite(p_pre), p_pre, self.p_post)
        with np.errstate(all="ignore"):
            self.prog_post = self.qres / self.thr
            self.prog_pre = np.where(self.side == 1, (self.qres - self.qleg) / self.thr, (self.qres + self.qleg) / self.thr)
        buy = self.side == 1
        self.buy_sol = np.where(buy, self.qleg, 0.0)
        self.sell_sol = np.where(~buy, self.qleg, 0.0)
        z = lambda x: np.concatenate([[0.0], np.cumsum(np.nan_to_num(x))])  # noqa: E731
        self.c_buy_n, self.c_sell_n = z(buy.astype(float)), z((~buy).astype(float))
        self.c_buy_sol, self.c_sell_sol = z(self.buy_sol), z(self.sell_sol)
        zero = np.zeros(n, np.int64)
        self.fee_buy = L.ffill(np.where(buy & np.isfinite(self.rate), self.rate, np.nan), zero, L.FALLBACK_FEE)
        self.fee_sell = L.ffill(np.where(~buy & np.isfinite(self.rate), self.rate, np.nan), zero, L.FALLBACK_FEE)
        with np.errstate(all="ignore"):
            self.imp25 = L.buy_impact(self.S1, self.Lp, 0.25 * SOL, self.fee_buy, self.mode)

    def win_lo(self, i: int, w_s: float) -> int:
        return int(np.searchsorted(self.bt, self.bt[i] - w_s, "left"))


def universe(P: Pool, first_ok_bt: float, comp_slot: int | None) -> np.ndarray:
    ok = (~P.bad) & np.isfinite(P.Lp) & np.isfinite(P.imp25) & (P.imp25 <= L.IMPACT_MAX) & (P.thr >= THR_MIN) & (P.prog_post <= P_MAX_ENTRY) & (P.bt >= first_ok_bt)
    if P.hook or not P.mode_known:
        ok &= False
    if comp_slot is not None:
        ok &= P.slot < comp_slot
    return ok


def triggers_for(cell: CellDef, P: Pool, first_ok_bt: float, comp_slot: int | None) -> list[int]:
    """Indices of prints at which the cell fires (before cooldown). Decides on rows <= the print only."""
    n = P.n
    if n < 3:
        return []
    out: list[int] = []
    buy = P.side == 1
    ok_entry = universe(P, first_ok_bt, comp_slot)
    if cell.idea == "D1":
        N, S = cell.params
        cand = np.nonzero(buy & ok_entry & (P.prog_post >= 0.02) & (P.prog_post <= 0.35))[0]
        for i in cand:
            lo = P.win_lo(i, 60.0)
            if P.c_buy_n[i + 1] - P.c_buy_n[lo] < N:
                continue
            net = (P.c_buy_sol[i + 1] - P.c_buy_sol[lo]) - (P.c_sell_sol[i + 1] - P.c_sell_sol[lo])
            if net < S * SOL:
                continue
            mv = P.p_post[i] / P.p_pre[lo] - 1.0
            if 0.05 <= mv <= 0.40:
                out.append(int(i))
    elif cell.idea == "D2":
        (m,) = cell.params
        cross = np.nonzero(buy & ok_entry & (P.prog_pre < m) & (P.prog_post >= m))[0]
        for i in cross[:1]:  # first crossing per pool, one look
            lo = P.win_lo(i, 120.0)
            net = (P.c_buy_sol[i + 1] - P.c_buy_sol[lo]) - (P.c_sell_sol[i + 1] - P.c_sell_sol[lo])
            if net >= 1.0 * SOL:
                out.append(int(i))
    elif cell.idea == "D3":
        (M,) = cell.params
        imp = P.p_post / P.p_pre - 1.0
        cand = np.nonzero(buy & ok_entry & (P.buy_sol >= M * SOL) & (imp >= 0.02) & (imp <= 0.25) & (P.prog_post >= 0.02) & (P.prog_post <= 0.80))[0]
        out = [int(i) for i in cand]
    elif cell.idea == "D4":
        K, S, dn = cell.params
        cand = np.nonzero((~buy) & ok_entry & (P.prog_post >= 0.05))[0]
        for i in cand:
            lo = P.win_lo(i, 30.0)
            if P.c_sell_n[i + 1] - P.c_sell_n[lo] < K:
                continue
            if (P.c_sell_sol[i + 1] - P.c_sell_sol[lo]) < S * SOL:
                continue
            peak = max(float(P.p_pre[lo]), float(P.p_post[lo:i + 1].max()))
            if P.p_post[i] <= peak * (1.0 - dn):
                out.append(int(i))
    return out


# ------------------------------------------------------------------------------------------------------------------------------ attempt resolution
def resolve(cell: CellDef, P: Pool, idx: list[int], D: dict[str, Any], comp_slot: int | None, lat_in: float = L.LAT_S, lat_out: float = L.LAT_S) -> tuple[list[dict[str, Any]], dict[str, int]]:
    refused = {"bad_hour": 0, "block_edge": 0, "cooldown": 0, "no_state": 0, "bad_state": 0, "completed_before_entry": 0}
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
        cap = sd + max(1, int(round(ex.hold_s / ss)))
        last_bt = bt + ex.hold_s + 2 * lat_out + 30
        if last_bt >= end_bt:
            refused["block_edge"] += 1
            continue
        if any(h not in good for h in range(int((bt - 120) // 3600), int(last_bt // 3600) + 1)):
            refused["bad_hour"] += 1
            continue
        ref = float(P.p_post[i])
        x = sd + k_in
        je = int(np.searchsorted(P.slot, x, "right")) - 1
        if je < i:
            refused["no_state"] += 1
            continue
        if comp_slot is not None and comp_slot <= x:
            refused["completed_before_entry"] += 1
            continue
        m0 = int(np.searchsorted(P.slot, sd + k_in + 1, "left"))
        m1 = int(np.searchsorted(P.slot, cap, "right"))
        fire_slot, reason = cap, "time"
        if m1 > m0 and (ex.tp or ex.sl or ex.trail):
            pp = np.where(P.bad[m0:m1], np.nan, P.p_post[m0:m1])
            hit = np.zeros(len(pp), bool)
            with np.errstate(invalid="ignore"):
                if ex.tp:
                    hit |= pp >= ref * ex.tp
                if ex.sl:
                    hit |= pp <= ref * ex.sl
                if ex.trail:
                    pk = np.maximum.accumulate(np.maximum(np.nan_to_num(pp, nan=0.0), ref))
                    hit |= pp <= pk * ex.trail
            if hit.any():
                j = int(np.argmax(hit))
                fire_slot = int(P.slot[m0 + j])
                v = float(pp[j])
                reason = "tp" if (ex.tp and v >= ref * ex.tp) else ("sl" if (ex.sl and v <= ref * ex.sl) else "trail")
        xe = fire_slot + k_out
        jx = int(np.searchsorted(P.slot, xe, "right")) - 1
        jf = int(np.searchsorted(P.slot, fire_slot, "right")) - 1
        if P.bad[je] or P.bad[jx] or not (np.isfinite(P.Lp[je]) and np.isfinite(P.Lp[jx])):
            refused["bad_state"] += 1
            continue
        ended = bool(comp_slot is not None and comp_slot <= xe)
        # DIAGNOSTIC only (added after the first look, does not change a label): the last on-curve state at or before the completion slot, for the strand sensitivity
        jc = jx if not ended else max(int(np.searchsorted(P.slot, comp_slot, "right")) - 1, je)
        exit_bt = bt + (fire_slot - sd) * ss
        busy_until_bt = exit_bt + cell.cooldown_s
        lo2 = int(np.searchsorted(P.slot, x - int(round(2.0 / ss)), "left"))
        hi2 = int(np.searchsorted(P.slot, x, "right"))
        ix = int(np.searchsorted(P.slot, x, "left"))
        out.append({
            "pid": P.pid, "bt": bt, "date": day_of(bt), "i": i, "reason": reason, "ended": ended, "mode": int(P.mode[je]),
            "S_e": float(P.S1[je]), "L_e": float(P.Lp[je]), "spot": ref, "S_x": float(P.S1[jx]), "L_x": float(P.Lp[jx]),
            "fee_in": float(P.fee_buy[i]), "fee_out": float(P.fee_sell[jf]), "room": float(P.thr[je] - P.qres[je]),
            "ssb": float(P.c_buy_n[hi2] - P.c_buy_n[ix]), "nearby": float(P.c_buy_sol[hi2] - P.c_buy_sol[lo2]), "hold_real_s": (fire_slot - sd) * ss, "prog": float(P.prog_post[i]), "thr": float(P.thr[i]),
            "S_c": float(P.S1[jc]), "L_c": float(P.Lp[jc]) if np.isfinite(P.Lp[jc]) else float("nan"),
        })
    return out, refused


def cell_attempts(cell: CellDef, D: dict[str, Any], lat_in: float = L.LAT_S, lat_out: float = L.LAT_S) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, int]]:
    pid = D["pid"]
    bounds = np.flatnonzero(np.diff(pid)) + 1
    starts = np.concatenate([[0], bounds])
    ends = np.concatenate([bounds, [len(pid)]])
    first_ok_bt = D["start_ts"] + WARMUP_HOURS * 3600
    att: list[dict[str, Any]] = []
    ref_tot = {"bad_hour": 0, "block_edge": 0, "cooldown": 0, "no_state": 0, "bad_state": 0, "completed_before_entry": 0}
    stats = {"pools": 0, "trigger_prints": 0}
    for s, e in zip(starts, ends):
        if e - s < 3:
            continue
        P = Pool(D, slice(int(s), int(e)), int(pid[s]))
        stats["pools"] += 1
        cs = D["complete_slot"].get(P.pid)
        idx = triggers_for(cell, P, first_ok_bt, cs)
        if not idx:
            continue
        stats["trigger_prints"] += len(idx)
        a, r = resolve(cell, P, idx, D, cs, lat_in, lat_out)
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
        "per_day_n": {u: int((d == u).sum()) for u in sorted(set(dates))},
    }


def concentration(att: list[dict[str, Any]], flat: np.ndarray) -> dict[str, Any]:
    """DIAGNOSTIC (added after the first look): per-pool concentration and threshold bands of the flat-model pnl (SOL)."""
    pids = np.array([a["pid"] for a in att])
    thr = np.array([a["thr"] for a in att]) / SOL
    up = np.unique(pids)
    tot = np.array([flat[pids == u].sum() for u in up])
    best = int(np.argmax(tot))
    out = {"pools": int(len(up)), "best_pool_total": float(tot.max()), "total_ex_best_pool": float(flat.sum() - tot.max()), "best3_pools_total": float(np.sort(tot)[::-1][:3].sum()),
           "attempts_in_best_pool": int((pids == up[best]).sum())}
    for nm, m in (("thr<20", thr < 20), ("20<=thr<60", (thr >= 20) & (thr < 60)), ("thr>=60", thr >= 60)):
        out[nm] = {"n": int(m.sum()), "mean": float(flat[m].mean()) if m.any() else None, "total": float(flat[m].sum())}
    return out


def price_cell(att: list[dict[str, Any]], stake_sol: float, rent: int = L.RENT_DECIDING, strand_at_last: bool = False) -> dict[str, Any]:
    g = lambda k: np.array([a[k] for a in att], float)  # noqa: E731
    stake = stake_sol * SOL
    mode = np.array([a["mode"] for a in att])
    ended = np.array([a["ended"] for a in att])
    S_x, L_x = g("S_x"), g("L_x")
    if strand_at_last:  # OPTIMISTIC sensitivity: a position whose curve completed is sold at the last on-curve state (ignores the migration, the new pool's fee and any latency)
        S_x, L_x = np.where(ended, g("S_c"), S_x), np.where(ended, g("L_c"), L_x)
        ended = np.zeros(len(att), bool)
    pnl, filled, stranded = L.round_trip(stake, g("S_e"), g("L_e"), g("spot"), g("fee_in"), mode, S_x, L_x, g("fee_out"), quote_room=g("room"),
                                         rent=rent, ended=ended, return_stranded=True)
    flat, press, c, p = F.fail_legs(pnl, filled, g("ssb"), g("nearby"), send=L.SEND_LAMPORTS)
    dates = [a["date"] for a in att]
    res = {"filled": int(filled.sum()), "stranded": int(stranded.sum()), "guard_unfilled": int((~filled).sum()), "press_intercept": c, "mean_p": float(p[filled].mean()) if filled.any() else None}
    fi, fo = g("fee_in")[filled], g("fee_out")[filled]
    q = lambda v: [float(t) for t in np.quantile(v, [0.1, 0.5, 0.9, 0.99])] if len(v) else None  # noqa: E731
    res["fee_paid_bps"] = {"buy_q10_50_90_99": q(fi * 1e4), "sell_q10_50_90_99": q(fo * 1e4), "buy_mean": float(fi.mean() * 1e4) if len(fi) else None, "sell_mean": float(fo.mean() * 1e4) if len(fo) else None,
                           "round_trip_mean_bps": float((fi + fo).mean() * 1e4) if len(fi) else None, "share_rt_ge_100bps": float(((fi + fo) >= 0.01).mean()) if len(fi) else None}
    for name, v in (("flat", flat), ("press", press), ("nofail", pnl)):
        res[name] = stat(v / SOL, dates)
    res["concentration"] = concentration(att, flat / SOL)
    return res


def label(cell_res: dict[str, Any]) -> str:
    r = cell_res.get("deciding")
    if not r or not r.get("flat", {}).get("n"):
        return "NO-ATTEMPTS"
    sub = [r["flat"], r["press"]]
    pos = all(s["mean"] > 0 and (s["ex_top3"] or 0) > 0 and s["ex_best_day"] > 0 for s in sub)
    return "MARGINAL" if pos else "FAIL"


# ------------------------------------------------------------------------------------------------------------------------------ validation / capacity (counts only)
def validate_all(D: dict[str, Any]) -> dict[str, Any]:
    A, d = D["a"], D["d"]
    n = len(A["slot"])
    buy = A["side"] == 1
    out: dict[str, Any] = {"sol_trade_rows": n, "pools": D["n_pools"], "configs": D["n_configs"], "creates": D["creates"], "completed_pools": len(D["complete_slot"]),
                           "hook_pools": len(D["hook_pools"]), "good_hours": len(D["good_hours"]), "bad_hours": D["bad_hours"], "order_fix": D["order_stats"],
                           "bad_rows": int(D["bad_row"].sum()), "curve_all_rows": L.validate(A, d), "curve_excl_bad_rows": L.validate(A, d, exclude=D["bad_row"])}
    out["config_mode"] = {"configs_mode_input": int(len({int(c) for c in np.unique(A['cfg'][d['mode'] == 1])})), "configs_with_evidence": int(len(np.unique(A['cfg'][d['mode_known']]))),
                          "rows_mode_unknown": int((~d["mode_known"]).sum())}
    rate = d["rate"]
    q = lambda v: [float(t) for t in np.quantile(v, [0.01, 0.1, 0.5, 0.9, 0.99])]  # noqa: E731
    out["fee_bps_all_rows"] = {"buy": q(rate[buy & np.isfinite(rate)] * 1e4), "sell": q(rate[~buy & np.isfinite(rate)] * 1e4)}
    vals, cnt = np.unique(np.round(rate[np.isfinite(rate)] * 1e4), return_counts=True)
    out["fee_bps_top_rounded"] = [{"bps": float(v), "rows": int(c)} for c, v in sorted(zip(cnt.tolist(), vals.tolist()), reverse=True)[:8]]
    out["share_rows_fee_gt_100bps"] = float((rate[np.isfinite(rate)] > 0.01).mean())
    return out


def capacity(D: dict[str, Any]) -> dict[str, Any]:
    """Counts only. Per UTC day: SOL-quoted rows, rows (and buy rows) whose post-state lets a 0.25 SOL buy move the spot by <= 2%, in tradable pools (no hook, mode known, thr>=5 SOL, not bad)."""
    pid = D["pid"]
    bounds = np.flatnonzero(np.diff(pid)) + 1
    starts = np.concatenate([[0], bounds])
    ends = np.concatenate([bounds, [len(pid)]])
    days: dict[str, dict[str, float]] = {}
    band = {"thr<20": [0, 0], "20<=thr<60": [0, 0], "thr>=60": [0, 0]}
    imp_all = []
    first_ok_bt = D["start_ts"] + WARMUP_HOURS * 3600
    pools_ok = set()
    for s, e in zip(starts, ends):
        P = Pool(D, slice(int(s), int(e)), int(pid[s]))
        cs = D["complete_slot"].get(P.pid)
        tradable = (not P.hook) and P.mode_known and np.isfinite(P.thr[0]) and (P.thr[0] >= THR_MIN)
        fin = np.isfinite(P.imp25) & (~P.bad)
        ok = universe(P, first_ok_bt, cs)
        dnum = (P.bt // 86400).astype(np.int64)
        for u in np.unique(dnum):
            m = dnum == u
            key = datetime.fromtimestamp(int(u) * 86400, tz=timezone.utc).strftime("%Y-%m-%d")
            dd = days.setdefault(key, {"rows": 0, "buys": 0, "tradable_rows": 0, "tradable_buys": 0, "imp_le_2pct_rows": 0, "imp_le_2pct_buys": 0, "universe_rows": 0, "pools_with_imp_le_2pct": 0})
            dd["rows"] += int(m.sum())
            dd["buys"] += int((m & (P.side == 1)).sum())
            if tradable:
                dd["tradable_rows"] += int((m & ~P.bad).sum())
                dd["tradable_buys"] += int((m & (P.side == 1) & ~P.bad).sum())
                c = m & fin & (P.imp25 <= L.IMPACT_MAX) & (P.prog_post <= P_MAX_ENTRY)
                dd["imp_le_2pct_rows"] += int(c.sum())
                dd["imp_le_2pct_buys"] += int((c & (P.side == 1)).sum())
                dd["universe_rows"] += int((m & ok).sum())
                if c.any():
                    dd["pools_with_imp_le_2pct"] += 1
        if tradable and fin.any():
            imp_all.append(P.imp25[fin])
            b = 0 if P.thr[0] < 20e9 else (1 if P.thr[0] < 60e9 else 2)
            nm = list(band)[b]
            band[nm][0] += int(fin.sum())
            band[nm][1] += int((fin & (P.imp25 <= L.IMPACT_MAX)).sum())
    iall = np.concatenate(imp_all) if imp_all else np.array([])
    return {"per_day": days, "by_threshold_band_rows_and_rows_imp_le_2pct": band,
            "imp25_quantiles_10_25_50_75_90": [float(t) for t in np.quantile(iall, [0.1, 0.25, 0.5, 0.75, 0.9])] if len(iall) else None,
            "share_rows_imp_le_2pct": float((iall <= L.IMPACT_MAX).mean()) if len(iall) else None}


# ------------------------------------------------------------------------------------------------------------------------------ main
def check_out_dir(out: str) -> str:
    real = os.path.realpath(out)
    if real.startswith(os.path.realpath(L.BACKFILL_ROOT)) or real.startswith(os.path.realpath(L.HOLDOUT_ROOT)) or real.startswith(os.path.realpath(os.path.dirname(L.BACKFILL_ROOT))):
        raise L.Refused(f"out_dir_inside_backfill_tree: {out}")
    return real


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--count-only", action="store_true")
    ap.add_argument("--cells", default="")
    ap.add_argument("--root", default=None)
    ap.add_argument("--start", default="")
    ap.add_argument("--end", default="")
    ns = ap.parse_args(argv)
    out = Path(check_out_dir(ns.out))
    out.mkdir(parents=True, exist_ok=True)
    cnt = L.count_hour_files(ns.root)
    print(json.dumps({"file_counts": cnt}), flush=True)
    t0 = time.time()
    pa = lambda v: datetime.strptime(v, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc) if v else None  # noqa: E731
    D = load_exploration(ns.root, start=pa(ns.start), end=pa(ns.end))
    print(f"loaded {len(D['a']['slot'])} SOL trade rows, {D['n_pools']} pools, bad hours {len(D['bad_hours'])} in {time.time() - t0:.0f}s", flush=True)
    meta = {"schema": SCHEMA, "taken_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "file_counts": cnt, "kit_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "price_sha256": hashlib.sha256(Path(L.__file__).read_bytes()).hexdigest(), "bad_hours": D["bad_hours"], "block_args": [ns.start, ns.end]}
    if ns.validate:
        res = {**meta, "validate": validate_all(D), "capacity": capacity(D)}
        (out / "validate.json").write_text(json.dumps(res, indent=1, default=float))
        print((out / "validate.json").read_text())
        return 0
    cells = [c for c in CELLS if not ns.cells or c.name in ns.cells.split(",")]
    res: dict[str, Any] = {}
    nday = max(len(D["good_hours"]) / 24.0, 1e-9)
    for c in cells:
        att, refused, st = cell_attempts(c, D)
        dts = sorted({a["date"] for a in att})
        rate = len(att) / nday
        entry: dict[str, Any] = {"idea": c.idea, "params": c.params, "exit": c.exit.__dict__, "cooldown_s": c.cooldown_s, "attempts": len(att), "attempts_per_day": rate,
                                 "attempts_by_day": {u: sum(1 for a in att if a["date"] == u) for u in dts}, "refused": refused, "pools_scanned": st["pools"], "trigger_prints": st["trigger_prints"]}
        if ns.count_only or rate < MIN_ATTEMPTS_PER_DAY or not att:
            entry["priced"] = False
            entry["dropped_below_rate_bar"] = (not ns.count_only) and rate < MIN_ATTEMPTS_PER_DAY
        else:
            entry["priced"] = True
            entry["deciding"] = price_cell(att, L.STAKE_DECIDING)
            entry["stake_1sol"] = price_cell(att, L.STAKE_BIG)
            entry["rent_charged"] = price_cell(att, L.STAKE_DECIDING, rent=L.RENT_REPORT)
            att2, _, _ = cell_attempts(c, D, L.LAT_S, L.EXIT_LAT_SENS_S)
            entry["strand_at_last_price_OPTIMISTIC"] = price_cell(att, L.STAKE_DECIDING, strand_at_last=True)
            entry["exit_lag_3p4s"] = price_cell(att2, L.STAKE_DECIDING) if att2 else None
            entry["exits"] = {r: sum(1 for a in att if a["reason"] == r) for r in ("time", "tp", "sl", "trail")}
            entry["label"] = label(entry)
        res[c.name] = entry
        print(c.name, json.dumps({k: entry[k] for k in ("attempts", "attempts_per_day", "priced") if k in entry}), entry.get("label", ""), flush=True)
    (out / ("counts.json" if ns.count_only else "screens.json")).write_text(json.dumps({**meta, "cells": res}, indent=1, default=float))
    return 0


if __name__ == "__main__":
    sys.exit(main())
