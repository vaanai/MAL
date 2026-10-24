#!/usr/bin/env python3
"""dbc_p21: P21 EXPLORATION screen of D7 FLOOR (p20-dbc-start-floor) and D9 SCHEDULED (p20-dbc-fee-floor-clock) on Meteora DBC. PAPER ONLY. No key, no RPC.

Rules: docs/research/dbc-p21/DECLARE.md (committed before any count or price; QP-RULING-1024-p20). Re-uses the P14/P17 loader and pricer (tools.dbc_screen, tools.dbc_price);
those files are not changed (the m10 forward read pins them).

    python3 tools/dbc_p21.py --spent-root /data2/mal/venue-tape-backfill/p17_union --count-only --out DIR   # config decode, D9 fee validation, attempts per day per cell
    python3 tools/dbc_p21.py --spent-root /data2/mal/venue-tape-backfill/p17_union --out DIR                # the one pricing run

Guards (unit test tools/test_dbc_p21.py): no DBC hour >= 2026-10-25T00Z or >= the end of the last block used; no DAMM print at/after those instants (this module reads none);
no path under fwdv1/ or meteora_dbc_holdout/; no D1 cell (no D1 trigger, feature, position or strand continuation is defined here).
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
from statistics import NormalDist
from typing import Any

import numpy as np

from tools import arena_stack as F
from tools import dbc_config as C
from tools import dbc_price as L
from tools import dbc_screen as S

SCHEMA = "dbc_p21_v1"
SOL = 1e9
HARD_END = datetime(2026, 10, 25, 0, tzinfo=timezone.utc)  # QP: nothing at/after this instant, DBC or DAMM
BLOCK_END = datetime(2026, 10, 24, 0, tzinfo=timezone.utc)  # end of the last block used (E1 + E2)
FORBIDDEN_PARTS = ("fwdv1", "meteora_dbc_holdout")
D1_NAMES = {"m4", "m6", "m10"}
SWAP3_START = datetime(2026, 10, 16, 4, tzinfo=timezone.utc).timestamp()
E2_START = datetime(2026, 10, 17, 0, tzinfo=timezone.utc).timestamp()
M_BEFORE, M_AFTER = 26, 32
FEE_DEN = 1e9
BPS_MAX = 10_000.0
X_PEAK = 2.0
AGE_MIN_S = 1800.0
D9_MIN_DUR_S, D9_MIN_DUR_SLOTS = 60.0, 150
D9_MIN_DROP = 0.0010  # 10 bps
D9_ALIVE_S = 600.0
CAPITAL_SOL = 1.29
DEFAULT_CFG_RAW = "/data/mal/hunt-1008/push-1016/hunt28/p17/cfg_raw_all.json"
DEFAULT_CFG_SUMMARY = str(Path(__file__).resolve().parent.parent / "docs/research/dbc-p17/data/cfg_summary.json")


class Refused(L.Refused):
    pass


# ------------------------------------------------------------------------------------------------------------------------------ guards
def guard_path(path: str | os.PathLike) -> None:
    for p in (os.path.abspath(str(path)), os.path.realpath(str(path))):
        if any(part in FORBIDDEN_PARTS for part in Path(p).parts):
            raise Refused(f"forbidden_root: {path}")


def guard_hour(h: datetime, block_end: datetime = BLOCK_END) -> None:
    if h >= HARD_END or h >= block_end:
        raise Refused(f"hour_at_or_after_block_end: {h:%Y-%m-%dT%H}")


def guard_hour_file(path: str | os.PathLike, block_end: datetime = BLOCK_END) -> datetime:
    guard_path(path)
    h = L.hour_of_name(os.path.basename(str(path)))
    if h is None:
        raise Refused(f"not_a_tape_hour_file: {path}")
    guard_hour(h, block_end)
    return h


def guard_damm_ts(ts: float, block_end: datetime = BLOCK_END) -> None:
    """DAMM v2 prints: refused at/after 2026-10-25T00Z and at/after the end of the last DBC block used. (This module reads no DAMM print.)"""
    if ts >= min(HARD_END, block_end).timestamp():
        raise Refused(f"damm_print_at_or_after_block_end: {ts}")


def refuse_d1(name: str, idea: str) -> None:
    if idea == "D1" or name in D1_NAMES:
        raise Refused(f"d1_cell_refused: {name}")


# ------------------------------------------------------------------------------------------------------------------------------ cells
@dataclass(frozen=True)
class Cell:
    idea: str
    name: str
    hold_s: float
    s: float = 0.0  # D7 floor band
    tp: float | None = None
    primary: bool = False


CELLS: tuple[Cell, ...] = (
    Cell("D7", "d7_s102_h6", 6 * 3600.0, s=1.02, tp=1.5),
    Cell("D7", "d7_s105_h6", 6 * 3600.0, s=1.05, tp=1.5, primary=True),
    Cell("D7", "d7_s102_h24", 24 * 3600.0, s=1.02, tp=1.5),
    Cell("D7", "d7_s105_h24", 24 * 3600.0, s=1.05, tp=1.5),
    Cell("D9", "d9_t60", 60.0, primary=True),
    Cell("D9", "d9_t300", 300.0),
)
for _c in CELLS:
    refuse_d1(_c.name, _c.idea)


# ------------------------------------------------------------------------------------------------------------------------------ config fee schedule
def decode_configs(raw: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], int]:
    out, bad = {}, 0
    for k, v in raw.items():
        try:
            a = C.decode_b64(v["data_b64"])
        except (ValueError, KeyError):
            bad += 1
            continue
        c = a["config"]
        bf = c["pool_fees"]["base_fee"]
        out[k] = {"sqrt_start_price": int(c["sqrt_start_price"]), "activation_type": int(c["activation_type"]), "mode": int(bf["base_fee_mode"]),
                  "cliff": int(bf["cliff_fee_numerator"]), "f1": int(bf["first_factor"]), "f2": int(bf["second_factor"]), "f3": int(bf["third_factor"]),
                  "dyn": bool(c["pool_fees"]["dynamic_fee"]["initialized"]), "hook": a["kind"] == "ConfigWithTransferHook"}
    return out, bad


def schedule_duration(cf: dict[str, Any]) -> int | None:
    """Points (slots or seconds) from activation to the end of the fee scheduler / rate limiter; None if no schedule of a known mode."""
    if cf["mode"] in (0, 1):
        return int(cf["f1"]) * int(cf["f2"])
    if cf["mode"] == 2:
        return int(cf["f2"])
    return None


def base_fee_at(cf: dict[str, Any], elapsed) -> np.ndarray:
    """Config base fee (fraction) at `elapsed` points since activation (vectorised). Modes 0/1: scheduler; mode 2: the cliff fee (the size increment of the rate limiter
    applies only to buys above reference_amount inside the window; it is not modelled, our buys after expiry pay the cliff fee)."""
    e = np.maximum(np.asarray(elapsed, float), 0.0)
    cliff = float(cf["cliff"])
    if cf["mode"] in (0, 1):
        n, freq, red = float(cf["f1"]), float(cf["f2"]), float(cf["f3"])
        period = np.minimum(n, np.floor(e / freq)) if freq > 0 else np.full_like(e, n)
        num = np.maximum(cliff - period * red, 0.0) if cf["mode"] == 0 else cliff * (1.0 - red / BPS_MAX) ** period
    else:
        num = np.full_like(e, cliff)
    return num / FEE_DEN


def schedule_ok(cf: dict[str, Any]) -> bool:
    dur = schedule_duration(cf)
    if dur is None:
        return False
    if cf["activation_type"] == 0 and dur < D9_MIN_DUR_SLOTS:
        return False
    if cf["activation_type"] == 1 and dur < D9_MIN_DUR_S:
        return False
    if cf["mode"] in (0, 1):
        f0, f1 = base_fee_at(cf, [0.0, float(dur)])
        return bool(f0 - f1 >= D9_MIN_DROP)
    return cf["f1"] > 0


# ------------------------------------------------------------------------------------------------------------------------------ loading
def load(root: str, log=print) -> dict[str, Any]:
    """tools.dbc_screen.load_exploration over every allowed hour, plus the create rows (pool -> create row) and a slot -> block-time map of all trade rows."""
    guard_path(root)
    creates: dict[str, dict[str, Any]] = {}
    smap: list[np.ndarray] = []
    orig = L.read_hour

    def rh(path, r=None):
        guard_hour_file(path)
        out = orig(path, r)
        for c in out["creates"]:
            creates.setdefault(c["pool"], {k: c.get(k) for k in ("slot", "block_time", "activation_point", "config", "t_recv_ms")})
        if out["trades"]:
            sl = np.array([x["slot"] for x in out["trades"]], np.int64)
            bt = np.array([L.block_time_s(x) for x in out["trades"]], float)
            smap.append(np.stack([sl.astype(float), bt]))
        return out

    L.read_hour = rh
    try:
        D = S.load_exploration(root, log=log, end=BLOCK_END)
    finally:
        L.read_hour = orig
    m = np.concatenate(smap, axis=1)
    o = np.argsort(m[0], kind="stable")
    D["map_slot"], D["map_bt"] = m[0][o].astype(np.int64), np.maximum.accumulate(m[1][o])
    D["create_rows"] = creates
    D["block_end_bt"] = BLOCK_END.timestamp()
    return D


def slot_at_time(D: dict[str, Any], t: float) -> int | None:
    i = int(np.searchsorted(D["map_bt"], t, "left"))
    return int(D["map_slot"][i]) if i < len(D["map_slot"]) else None


def time_at_slot(D: dict[str, Any], s: int) -> float:
    i = int(np.searchsorted(D["map_slot"], s, "right")) - 1
    return float(D["map_bt"][max(i, 0)])


# ------------------------------------------------------------------------------------------------------------------------------ triggers
def d7_first(p_post: np.ndarray, bt: np.ndarray, ok: np.ndarray, start_spot: float, create_bt: float, s: float, x_peak: float = X_PEAK, age: float = AGE_MIN_S) -> int | None:
    """First print index (causal: running max over prints <= i) at the floor band after a prior peak and the minimum age; None if none. No D1 feature is read."""
    runmax = np.maximum.accumulate(np.nan_to_num(p_post, nan=0.0))
    cond = ok & (p_post <= s * start_spot) & (runmax >= x_peak * start_spot) & ((bt - create_bt) >= age)
    idx = np.flatnonzero(cond)
    return int(idx[0]) if len(idx) else None


# ------------------------------------------------------------------------------------------------------------------------------ resolution
def resolve_one(P, D: dict[str, Any], cs: int | None, i_dec: int, sd: int, bt_dec: float, x_land: int, hold_s: float, tp: float | None, lat_out: float,
                ignore_bad: bool = False, fees=None) -> tuple[dict[str, Any] | None, str | None]:
    """One attempt: decision at print i_dec (state after it), decision slot sd, buy lands on the state after every print with slot <= x_land. Exit: tp (post spot >= ref*tp) else the cap.
    `fees(je, jx, jf, x_land, xe)` -> (fee_in, fee_out) overrides the P14 last-observed-rate fees (D9). Mirrors tools.dbc_screen.resolve."""
    good = set(D["good_hours"])
    hk = int(bt_dec // 3600)
    ss = S.slot_s_for(D["slot_s"], hk)
    k_out = S.k_slots(lat_out, ss)
    cap = sd + max(1, int(round(hold_s / ss)))
    last_bt = bt_dec + hold_s + 2 * lat_out + 30
    if last_bt >= D["block_end_bt"]:
        return None, "block_edge"
    if any(h not in good for h in range(int((bt_dec - 120) // 3600), int(last_bt // 3600) + 1)):
        return None, "bad_hour"
    ref = float(P.p_post[i_dec])
    je = int(np.searchsorted(P.slot, x_land, "right")) - 1
    if je < i_dec or je < 0:
        return None, "no_state"
    if cs is not None and cs <= x_land:
        return None, "completed_before_entry"
    m0 = int(np.searchsorted(P.slot, x_land + 1, "left"))
    m1 = int(np.searchsorted(P.slot, cap, "right"))
    fire_slot, reason = cap, "time"
    if tp and m1 > m0:
        pp = P.p_post[m0:m1] if ignore_bad else np.where(P.bad[m0:m1], np.nan, P.p_post[m0:m1])
        with np.errstate(invalid="ignore"):
            hit = pp >= ref * tp
        if hit.any():
            fire_slot, reason = int(P.slot[m0 + int(np.argmax(hit))]), "tp"
    xe = fire_slot + k_out
    jx = int(np.searchsorted(P.slot, xe, "right")) - 1
    jf = int(np.searchsorted(P.slot, fire_slot, "right")) - 1
    if (not ignore_bad and (P.bad[je] or P.bad[jx])) or not (np.isfinite(P.Lp[je]) and np.isfinite(P.Lp[jx])):
        return None, "bad_state"
    ended = bool(cs is not None and cs <= xe)
    jc = jx if not ended else max(int(np.searchsorted(P.slot, cs, "right")) - 1, je)
    lo2 = int(np.searchsorted(P.slot, x_land - int(round(2.0 / ss)), "left"))
    hi2 = int(np.searchsorted(P.slot, x_land, "right"))
    ix = int(np.searchsorted(P.slot, x_land, "left"))
    fin, fout = (float(P.fee_buy[i_dec]), float(P.fee_sell[jf])) if fees is None else fees(je, jx, jf, x_land, xe)
    hold_real = (fire_slot - sd) * ss
    return {
        "pid": P.pid, "bt": bt_dec, "date": S.day_of(bt_dec), "i": i_dec, "reason": reason, "ended": ended, "mode": int(P.mode[je]),
        "S_e": float(P.S1[je]), "L_e": float(P.Lp[je]), "spot": ref, "S_x": float(P.S1[jx]), "L_x": float(P.Lp[jx]),
        "fee_in": fin, "fee_out": fout, "room": float(P.thr[je] - P.qres[je]),
        "ssb": float(P.c_buy_n[hi2] - P.c_buy_n[ix]), "nearby": float(P.c_buy_sol[hi2] - P.c_buy_sol[lo2]), "hold_real_s": hold_real, "prog": float(P.prog_post[i_dec]),
        "thr": float(P.thr[i_dec]), "S_c": float(P.S1[jc]), "L_c": float(P.Lp[jc]) if np.isfinite(P.Lp[jc]) else float("nan"),
        "era": "swap2" if bt_dec < SWAP3_START else ("swap3_e1" if bt_dec < E2_START else "e2"),
        "t_open": bt_dec + (x_land - sd) * ss, "t_close": bt_dec + hold_real + lat_out,
    }, None


# ------------------------------------------------------------------------------------------------------------------------------ the scan
def pool_iter(D: dict[str, Any]):
    pid = D["pid"]
    bounds = np.flatnonzero(np.diff(pid)) + 1
    starts = np.concatenate([[0], bounds])
    ends = np.concatenate([bounds, [len(pid)]])
    for s, e in zip(starts, ends):
        yield int(s), int(e), int(pid[s])


def scan(D: dict[str, Any], cfgs: dict[str, dict[str, Any]], variants: dict[str, dict[str, Any]], seed: int = 1) -> dict[str, Any]:
    """Walk every pool once. `variants`: {key: {"cell": Cell, "lat_in": s, "lat_out": s, "d9_off": slots or None, "ignore_bad": bool, "control": bool}}.
    Returns {key: {"att": [...], "refused": {...}}} plus pool-level exclusion counts per day and the D9 fee validation counts."""
    res = {k: {"att": [], "refused": {}} for k in variants}
    first_ok_bt = D["start_ts"] + S.WARMUP_HOURS * 3600
    excl: dict[str, dict[str, int]] = {}
    cand: dict[str, dict[str, int]] = {"d9_pools_by_mode": {}, "d9_refused": {}, "d7_pools_with_peak": {}}
    val = {k: {"n": 0, "le_1bp": 0, "le_10bp": 0} for k in ("pre_nodyn", "post_nodyn", "pre_dyn", "post_dyn")}
    floor_viol = [0, 0]
    rng = np.random.default_rng(seed)

    def bump(dd, k, day, v=1):
        dd.setdefault(k, {})
        dd[k][day] = dd[k].get(day, 0) + v

    def refuse(key, why):
        r = res[key]["refused"]
        r[why] = r.get(why, 0) + 1

    names, cfgn = D["pool_names"], D["cfg_names"]
    for s0, e0, pid in pool_iter(D):
        if e0 - s0 < 3:
            continue
        P = S.Pool(D, slice(s0, e0), pid)
        day = S.day_of(float(P.bt[0]))
        pool = names[pid]
        cfg = cfgn[int(D["a"]["cfg"][s0])]
        cr = D["create_rows"].get(pool)
        cf = cfgs.get(cfg)
        if P.hook:
            bump(excl, "hook_xfer_unmapped", day)
            continue
        if cf is None:
            bump(excl, "config_not_decoded", day)
            continue
        if cr is None or cr.get("activation_point") is None:
            bump(excl, "no_create_row_in_hours", day)
            continue
        bump(excl, "pools_in_universe_base", day)
        cs = D["complete_slot"].get(pid)
        ok = S.universe(P, first_ok_bt, cs)
        create_bt = float(cr.get("block_time") or (cr["t_recv_ms"] / 1000.0))
        start_spot = float(L.spot(float(cf["sqrt_start_price"])))
        floor_viol[0] += int(np.sum(~P.bad))
        floor_viol[1] += int(np.sum((~P.bad) & (P.p_post < start_spot * (1 - 1e-6))))
        # ---------------- D7
        if np.nanmax(P.p_post) >= X_PEAK * start_spot:
            bump(cand, "d7_pools_with_peak", day)
        d7_idx: dict[float, int | None] = {}
        for key, v in variants.items():
            c = v["cell"]
            if c.idea != "D7":
                continue
            if c.s not in d7_idx:
                d7_idx[c.s] = d7_first(P.p_post, P.bt, ok, start_spot, create_bt, c.s)
            i = d7_idx[c.s]
            if i is None:
                continue
            bt = float(P.bt[i])
            ss = S.slot_s_for(D["slot_s"], int(bt // 3600))
            sd = int(P.slot[i])
            a, why = resolve_one(P, D, cs, i, sd, bt, sd + S.k_slots(v["lat_in"], ss), c.hold_s, c.tp, v["lat_out"], v.get("ignore_bad", False))
            if a is None:
                refuse(key, why)
            else:
                res[key]["att"].append(a)
        # ---------------- D9
        if not any(v["cell"].idea == "D9" for v in variants.values()):
            continue
        if not schedule_ok(cf):
            continue
        bump(cand, "d9_pools_by_mode", str(cf["mode"]))
        act = int(cr["activation_point"])
        dur = schedule_duration(cf)
        ts_type = cf["activation_type"] == 1
        exp_point = act + int(dur)
        exp_slot = slot_at_time(D, float(exp_point)) if ts_type else exp_point
        exp_bt = float(exp_point) if ts_type else time_at_slot(D, exp_point)
        # fee per print of this pool from the config schedule (point = slot or block time)
        pts = P.bt if ts_type else P.slot.astype(float)
        base_row = base_fee_at(cf, pts - act)
        # validation (counts only; never selects pools or times)
        rate = D["d"]["rate"][s0:e0]
        for nm, msk in (("pre", (pts >= act) & (pts < exp_point)), ("post", (pts >= exp_point) & (P.bt < exp_bt + 600))):
            msk = msk & np.isfinite(rate) & (~P.bad)
            dif = np.abs(rate[msk] - base_row[msk])
            vv = val[nm + ("_dyn" if cf["dyn"] else "_nodyn")]
            vv["n"] += int(msk.sum())
            vv["le_1bp"] += int((dif <= 1e-4).sum())
            vv["le_10bp"] += int((dif <= 1e-3).sum())

        def dyn_excess(j: int, side: int) -> float:
            if not cf["dyn"]:
                return 0.0
            m = np.flatnonzero((P.side[: j + 1] == side) & np.isfinite(rate[: j + 1]))
            return float(max(0.0, rate[m[-1]] - base_row[m[-1]])) if len(m) else 0.0

        def fees(je, jx, jf, x_land, xe, _i=None):
            pt_in = time_at_slot(D, x_land) if ts_type else float(x_land)
            pt_out = time_at_slot(D, xe) if ts_type else float(xe)
            fi = float(base_fee_at(cf, [pt_in - act])[0]) + dyn_excess(je, 1)
            fo = float(base_fee_at(cf, [pt_out - act])[0]) + dyn_excess(jf, 0)
            return fi, fo

        def attempt(dec_slot: int, dec_bt: float, key: str, v: dict[str, Any], why_prefix: str = "") -> None:
            c = v["cell"]
            i = int(np.searchsorted(P.slot, dec_slot, "left")) - 1  # last print with slot < dec_slot
            if i < 0:
                return refuse(key, why_prefix + "no_print_before_decision")
            if dec_bt - float(P.bt[i]) > D9_ALIVE_S:
                return refuse(key, why_prefix + "not_alive")
            if not ok[i]:
                return refuse(key, why_prefix + "universe_at_decision")
            ss = S.slot_s_for(D["slot_s"], int(dec_bt // 3600))
            off = v["d9_off"]
            off_slots = {"0": -1, "2": 2, "k19": S.k_slots(1.9, ss), "k25": S.k_slots(2.5, ss)}[off]
            x_land = dec_slot + off_slots
            a, why = resolve_one(P, D, cs, i, dec_slot, dec_bt, x_land, c.hold_s, None, v["lat_out"], v.get("ignore_bad", False), fees)
            if a is None:
                return refuse(key, why_prefix + str(why))
            a["d9_mode"] = cf["mode"]
            a["dyn"] = cf["dyn"]
            res[key]["att"].append(a)

        ctrl_t = None
        act_bt = float(act) if ts_type else time_at_slot(D, act)
        for key, v in variants.items():
            if v["cell"].idea != "D9":
                continue
            if v.get("control"):  # report-only: only pools where the cell's deciding variant made an attempt; random instant in [activation + 60 s, expiry + 3,600 s]
                main = res[v["cell"].name]["att"]
                if not main or main[-1]["pid"] != pid:
                    continue
                if ctrl_t is None:
                    ctrl_t = float(rng.uniform(act_bt + 60.0, exp_bt + 3600.0))
                cslot = slot_at_time(D, ctrl_t)
                if cslot is None:
                    refuse(key, "control_outside_hours")
                    continue
                attempt(int(cslot), ctrl_t, key, v)
                continue
            if exp_slot is None or exp_bt < first_ok_bt:
                refuse(key, "expiry_outside_hours")
                continue
            if cs is not None and cs < exp_slot:
                refuse(key, "completed_before_expiry")
                continue
            attempt(int(exp_slot), exp_bt, key, v)
    return {"res": res, "excl": excl, "cand": cand, "fee_validation": val, "floor_check": {"rows": floor_viol[0], "rows_below_start_spot": floor_viol[1]}}


# ------------------------------------------------------------------------------------------------------------------------------ pricing and stats
def price_arrays(att: list[dict[str, Any]], stake_sol: float, rent: int = L.RENT_DECIDING, strand_at_last: bool = False) -> dict[str, np.ndarray]:
    g = lambda k: np.array([a[k] for a in att], float)  # noqa: E731
    stake = stake_sol * SOL
    mode = np.array([a["mode"] for a in att])
    ended = np.array([a["ended"] for a in att], bool)
    S_x, L_x = g("S_x"), g("L_x")
    if strand_at_last:
        S_x, L_x = np.where(ended, g("S_c"), S_x), np.where(ended, g("L_c"), L_x)
        ended = np.zeros(len(att), bool)
    pnl, filled, stranded = L.round_trip(stake, g("S_e"), g("L_e"), g("spot"), g("fee_in"), mode, S_x, L_x, g("fee_out"), quote_room=g("room"), rent=rent, ended=ended,
                                         return_stranded=True)
    flat, press, c, p = F.fail_legs(pnl, filled, g("ssb"), g("nearby"), send=L.SEND_LAMPORTS)
    flat50 = np.where(filled, 0.5 * pnl - 0.5 * L.SEND_LAMPORTS, pnl)
    return {"pnl": pnl / SOL, "flat": flat / SOL, "press": press / SOL, "flat50": flat50 / SOL, "filled": filled, "stranded": stranded, "press_c": np.array([c]),
            "fee_in": g("fee_in"), "fee_out": g("fee_out")}


def bonf_lo(x: np.ndarray, m: int = M_AFTER) -> float | None:
    if len(x) < 2:
        return None
    z = NormalDist().inv_cdf(1 - 0.05 / m)
    return float(x.mean() - z * x.std(ddof=1) / np.sqrt(len(x)))


def stat(x: np.ndarray, dates: list[str]) -> dict[str, Any]:
    r = S.stat(x, dates)
    if r.get("n"):
        r["bonf_lo_m32"] = bonf_lo(np.asarray(x, float))
        r["pos_majority"] = r["days_pos"] * 2 > r["days"]
    return r


def is_marginal(st: dict[str, Any]) -> bool:
    return bool(st.get("n")) and st["mean"] > 0 and (st["ex_top3"] or 0) > 0 and st["ex_best_day"] > 0


def summarize(att: list[dict[str, Any]], A: dict[str, np.ndarray], models=("flat", "press")) -> dict[str, Any]:
    dates = [a["date"] for a in att]
    era = np.array([a["era"] for a in att])
    out: dict[str, Any] = {"attempts": len(att), "filled": int(A["filled"].sum()), "guard_unfilled": int((~A["filled"]).sum()), "stranded": int(A["stranded"].sum()),
                           "press_intercept": float(A["press_c"][0])}
    fi, fo = A["fee_in"][A["filled"]], A["fee_out"][A["filled"]]
    out["fee_paid_bps"] = {"buy_mean": float(fi.mean() * 1e4) if len(fi) else None, "sell_mean": float(fo.mean() * 1e4) if len(fo) else None,
                           "round_trip_mean": float((fi + fo).mean() * 1e4) if len(fi) else None,
                           "round_trip_q10_50_90": [float(t) for t in np.quantile((fi + fo) * 1e4, [0.1, 0.5, 0.9])] if len(fi) else None}
    for mdl in models:
        x = A[mdl]
        out[mdl] = stat(x, dates)
        e2 = era == "e2"
        out[mdl + "_E2"] = stat(x[e2], [d for d, k in zip(dates, e2) if k])
        out[mdl + "_era"] = {e: {"n": int((era == e).sum()), "mean": float(x[era == e].mean()) if (era == e).any() else None, "total": float(x[era == e].sum())}
                             for e in ("swap2", "swap3_e1", "e2")}
    return out


def capital(att: list[dict[str, Any]], A: dict[str, np.ndarray], stake: float = 0.25, cap_sol: float = CAPITAL_SOL) -> dict[str, Any]:
    o = np.argsort([a["bt"] for a in att], kind="stable")
    open_until: list[float] = []
    max_open = 0
    keep = np.zeros(len(att), bool)
    skipped = 0
    slots = int(cap_sol // stake)
    events = []
    for j in o:
        a = att[j]
        open_until = [t for t in open_until if t > a["t_open"]]
        if A["filled"][j]:
            events.append((a["t_open"], a["t_close"]))
        if A["filled"][j] and len(open_until) >= slots:
            skipped += 1
            continue
        keep[j] = True
        if A["filled"][j]:
            open_until.append(a["t_close"])
    # uncapped max concurrent exposure
    ev = sorted([(t0, 1) for t0, _ in events] + [(t1, -1) for _, t1 in events], key=lambda z: (z[0], z[1]))
    cur = 0
    for _, dlt in ev:
        cur += dlt
        max_open = max(max_open, cur)
    dates = [a["date"] for a, k in zip(att, keep) if k]
    return {"max_concurrent_open": max_open, "max_exposure_sol": max_open * stake, "cap_sol": cap_sol, "skipped_by_cap": skipped,
            "flat": stat(A["flat"][keep], dates), "press": stat(A["press"][keep], dates)}


def label(s: dict[str, Any]) -> str:
    if not s.get("attempts"):
        return "NO-ATTEMPTS"
    return "MARGINAL" if is_marginal(s["flat"]) and is_marginal(s["press"]) else "FAIL"


def advance(c: Cell, s: dict[str, Any], req: dict[str, Any] | None) -> dict[str, Any]:
    chk = {}
    for mdl in ("flat", "press"):
        for blk in ("", "_E2"):
            st = s[mdl + blk]
            chk[f"{mdl}{blk or '_all'}_marginal_and_majority"] = bool(is_marginal(st) and st.get("pos_majority"))
        chk[f"required_latency_{mdl}_mean_gt0"] = bool(req and req[mdl].get("n") and req[mdl]["mean"] > 0)
    return {"primary": c.primary, "checks": chk, "advances": bool(c.primary and all(chk.values()))}


# ------------------------------------------------------------------------------------------------------------------------------ main
def variants_for(cells: list[Cell], count_only: bool) -> dict[str, dict[str, Any]]:
    v: dict[str, dict[str, Any]] = {}
    for c in cells:
        refuse_d1(c.name, c.idea)
        if c.idea == "D7":
            v[c.name] = {"cell": c, "lat_in": 1.9, "lat_out": 1.9}
            if not count_only:
                for li in (1.0, 1.3, 2.5, 3.4):
                    v[f"{c.name}|in{li}"] = {"cell": c, "lat_in": li, "lat_out": 1.9}
                v[f"{c.name}|out3.4"] = {"cell": c, "lat_in": 1.9, "lat_out": 3.4}
                v[f"{c.name}|nobad"] = {"cell": c, "lat_in": 1.9, "lat_out": 1.9, "ignore_bad": True}
        else:
            v[c.name] = {"cell": c, "d9_off": "2", "lat_out": 1.9}
            if not count_only:
                for off in ("0", "k19", "k25"):
                    v[f"{c.name}|land{off}"] = {"cell": c, "d9_off": off, "lat_out": 1.9}
                v[f"{c.name}|nobad"] = {"cell": c, "d9_off": "2", "lat_out": 1.9, "ignore_bad": True}
                v[f"{c.name}|control"] = {"cell": c, "d9_off": "2", "lat_out": 1.9, "control": True}
    return v


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--spent-root", required=True)
    ap.add_argument("--count-only", action="store_true")
    ap.add_argument("--cells", default="")
    ap.add_argument("--cfg-raw", default=DEFAULT_CFG_RAW)
    ap.add_argument("--cfg-summary", default=DEFAULT_CFG_SUMMARY)
    ns = ap.parse_args(argv)
    guard_path(ns.spent_root)
    guard_path(ns.out)
    out = Path(S.check_out_dir(ns.out))
    out.mkdir(parents=True, exist_ok=True)
    target = out / ("counts.json" if ns.count_only else "screens.json")
    if target.exists():
        raise Refused(f"output_exists_one_run_only: {target}")
    L.enable_swap3_pricing()
    print("config thresholds loaded:", L.load_config_thresholds(ns.cfg_summary), flush=True)
    raw = json.loads(Path(ns.cfg_raw).read_text())
    cfgs, cfg_bad = decode_configs(raw)
    L.CFG_UNMAPPED.clear()
    L.CFG_UNMAPPED.update(k for k in raw if k not in cfgs)
    print(f"configs decoded {len(cfgs)}, failed {cfg_bad}", flush=True)
    L.enable_spent_exploration(ns.spent_root)
    t0 = time.time()
    D = load(ns.spent_root, log=lambda *_: None)
    print(f"loaded {len(D['a']['slot'])} SOL trade rows, {D['n_pools']} pools, creates {len(D['create_rows'])}, bad hours {len(D['bad_hours'])} in {time.time() - t0:.0f}s", flush=True)
    cells = [c for c in CELLS if not ns.cells or c.name in ns.cells.split(",")]
    V = variants_for(cells, ns.count_only)
    sc = scan(D, cfgs, V)
    nday = max(len(D["good_hours"]) / 24.0, 1e-9)
    modes = {}
    for cf in cfgs.values():
        modes[str(cf["mode"])] = modes.get(str(cf["mode"]), 0) + 1
    meta = {"schema": SCHEMA, "taken_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "kit_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "screen_sha256": hashlib.sha256(Path(S.__file__).read_bytes()).hexdigest(), "price_sha256": hashlib.sha256(Path(L.__file__).read_bytes()).hexdigest(),
            "good_hours": len(D["good_hours"]), "bad_hours": D["bad_hours"], "days": nday, "pools": D["n_pools"], "create_rows": len(D["create_rows"]),
            "configs_decoded": len(cfgs), "configs_failed": cfg_bad, "configs_by_base_fee_mode": modes,
            "configs_schedule_ok": sum(1 for cf in cfgs.values() if schedule_ok(cf)), "exclusions_by_day": sc["excl"], "candidates": sc["cand"],
            "d9_fee_validation_counts": sc["fee_validation"], "floor_check": sc["floor_check"], "m_before": M_BEFORE, "m_after": M_AFTER,
            "slot_s_by_hour": {str(k): v for k, v in sorted(D["slot_s"].items())}}
    res: dict[str, Any] = {}
    for c in cells:
        base = sc["res"][c.name]
        att = base["att"]
        rate = len(att) / nday
        e: dict[str, Any] = {"idea": c.idea, "primary": c.primary, "def": c.__dict__, "attempts": len(att), "attempts_per_day": rate,
                             "attempts_by_day": {u: sum(1 for a in att if a["date"] == u) for u in sorted({a["date"] for a in att})},
                             "attempts_by_era": {k: sum(1 for a in att if a["era"] == k) for k in ("swap2", "swap3_e1", "e2")}, "refused": base["refused"]}
        if c.idea == "D9":
            e["attempts_by_mode"] = {str(k): sum(1 for a in att if a["d9_mode"] == k) for k in (0, 1, 2)}
        if ns.count_only or rate < S.MIN_ATTEMPTS_PER_DAY or not att:
            e["priced"] = False
            e["dropped_below_rate_bar"] = (not ns.count_only) and rate < S.MIN_ATTEMPTS_PER_DAY
        else:
            e["priced"] = True
            Adec = price_arrays(att, 0.25)
            e["deciding"] = summarize(att, Adec)
            e["label"] = label(e["deciding"])
            e["report_only"] = {
                "stake_0p1": summarize(att, price_arrays(att, 0.1)), "stake_1p0": summarize(att, price_arrays(att, 1.0)),
                "rent_0p25": summarize(att, price_arrays(att, 0.25, rent=L.RENT_REPORT)), "rent_0p1": summarize(att, price_arrays(att, 0.1, rent=L.RENT_REPORT)),
                "rent_1p0": summarize(att, price_arrays(att, 1.0, rent=L.RENT_REPORT)),
                "strand_at_last_price_OPTIMISTIC": summarize(att, price_arrays(att, 0.25, strand_at_last=True)),
            }
            for key, v in V.items():
                if v["cell"] is not c or key == c.name:
                    continue
                a2 = sc["res"][key]["att"]
                nm = key.split("|", 1)[1]
                e["report_only"][nm] = {"refused": sc["res"][key]["refused"], **(summarize(a2, price_arrays(a2, 0.25)) if a2 else {"attempts": 0})}
            req = e["report_only"].get("in2.5") if c.idea == "D7" else e["report_only"].get("landk25")
            e["advance"] = advance(c, e["deciding"], req)
            e["exits"] = {r: sum(1 for a in att if a["reason"] == r) for r in ("time", "tp")}
            if c.idea == "D7":
                e["control_time_cap_share_of_filled"] = float(np.mean([a["reason"] == "time" for a, f in zip(att, Adec["filled"]) if f])) if Adec["filled"].any() else None
                e["capital"] = capital(att, Adec)
            else:
                e["report_only"]["flat50_race"] = stat(Adec["flat50"], [a["date"] for a in att])
                for md in (0, 1, 2):
                    msk = np.array([a["d9_mode"] == md for a in att])
                    if msk.any():
                        e["report_only"][f"mode{md}_flat"] = stat(Adec["flat"][msk], [a["date"] for a, k in zip(att, msk) if k])
        res[c.name] = e
        print(c.name, json.dumps({k: e[k] for k in ("attempts", "attempts_per_day", "priced") if k in e}), e.get("label", ""), flush=True)
    target.write_text(json.dumps({**meta, "cells": res}, indent=1, default=float))
    print("wrote", target, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
