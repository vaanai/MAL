#!/usr/bin/env python3
"""floor_cpmm_forward_price: the ONE pinned reader of the FLOOR-CPMM forward run (docs/research/floor-cpmm/DECLARE.md section 11, Amendment 1; pinned requirements 11.6 (a)-(e)).

    python -m tools.floor_cpmm_forward_price counts --out-dir D --marker-dir M --until MS
    python -m tools.floor_cpmm_forward_price kill   --out-dir D --marker-dir M --until MS

Two modes only.
  counts: prints exactly ONE integer line (the counted set of 11.6 at the check instant) and nothing on stderr; writes no file except the counts marker (11.6 (b), (c)).
  kill:   refuses unless the counts marker of the same instant records an integer >= 10 (11.6 (d)); writes the one-look marker FIRST (exclusive create), so it can never run twice.
Every mode: --until <ms> is the check instant C. Hour files starting at or after C are never opened; any row with ms >= C is dropped unseen; the tool refuses to run before C (11.6 (a)).

Pricing math is scripts/research/floor_cpmm_replay.py (section 4 unchanged): cpmm_round_trip (haircuts, guard, own-buy re-injection, stranding, TP mark with the cell stake), leg_stats,
and tools.arena_stack.fail_legs / boot_lo. Sample semantics are section 11.2 (decide on a sample, price at the first sample at or after decision + latency).
Section 4: the send fee of a guarded buy (one send, -505,000 lamports) is paid in the K1/K4 legs: guarded rows are in the rows given to leg_stats as unfilled rows and enter
total_incl_guard / total_ex_top3_incl_guard, which K1 and K4 read.

Choices the DECLARE leaves open (declared here, none tuned):
  * G8 reference = FLOOR's: the latest sample at or before ms - 6 h inside the 26 h window (arena_rule_l1._floor_option j6), not the previous sample.
  * An unfilled attempt (guarded, or landing > 300 s later) blocks the pool until its landing sample; a filled one until exit decision + 60 s (G5).
  * A sample whose owed-fee fields or open_time are null is undecodable = missing (11.3 item 7).
  * Entries decided after the counted-set cutoff C - (24 h + 1.9 s + 300 s) are never scored, in any cell (so no position is ever open at the read).
Everything printed is IN-SAMPLE-style screening of a forward run; the pressure leg is a PROXY and never counts for the gate; nothing here says FLOOR-CPMM makes money.
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import json
import math
import re
import sys
import time
from bisect import bisect_left
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from scripts.research import floor_cpmm_replay as RP
from tools import arena_stack as F
from tools.arena_rule_l1 import fifo_cap

SCHEMA = "floor-cpmm-forward-price-v1"
STEP_MS = 60_000
GAP_MS = 2 * STEP_MS                 # 11.3 item 1
MAX_LAND_MS = 300_000                # 11.3 item 3
HOLD_MS = 86_400_000 + 1_900         # 24 h + 1.9 s
CUTOFF_BACK_MS = HOLD_MS + MAX_LAND_MS
TP_LAG_MS = 550                      # 0.55 s as time (11.2 item 3)
AGE_LO_S, AGE_HI_S = 24 * 3600, 200 * 3600
ALIVE_MS = 6 * 3600_000
HIST_MS = 26 * 3600_000
DAY_MS = 86_400_000
LAT_DECIDING_MS = 4085
LATS_MS = (1900, 4085)
STAKES = (0.10, 0.25)
KILL_STAKE = 0.25
MIN_COUNT = 10
K2_MIN_PER_DAY = 0.3
RENT_LG = F.RENT_STRANDED
R6_MAX = 0.80
FILE_RE = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d)(\.r\d+)?\.jsonl$")
COVERAGE_GAP = "pools migrated between 2026-11-21T08:51:11Z (pool list's last record) and the recorder's start are in neither the list nor forward discovery; not in the universe"
NOTE = ("FORWARD paper read of FLOOR-CPMM, kill line only. Rule constants carried over from FLOOR (winner's curse of DECLARE header item 4 applies). Both fail models are PumpSwap-calibrated; "
        "no CPMM live fill exists. The pressure leg is a PROXY (11.2 item 8), never the pressure-fail model for the promotion gate. This read cannot clear any gate item and is not a promotion.")


class Refused(Exception):
    pass


# ------------------------------------------------------------------------------------------------------------------------ loading
def hour_start_ms(name: str) -> int:
    return int(datetime.strptime(name, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp() * 1000)


def load_rows(out: Path, until: int) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Rows by pool, sorted by ms. Files whose hour starts at or after `until` are never opened; rows with ms >= until are dropped unseen."""
    by: dict[str, list[dict[str, Any]]] = {}
    st = {"files_read": 0, "rows_kept": 0, "rows_dropped_ms_ge_until": 0, "bad_lines": 0, "first_ms": None}
    for p in sorted(out.iterdir()):
        m = FILE_RE.match(p.name)
        if not m or hour_start_ms(m.group(1)) >= until:
            continue
        st["files_read"] += 1
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    ms = int(r["ms"])
                    pool = r["pool"]
                except (ValueError, KeyError, TypeError):
                    st["bad_lines"] += 1
                    continue
                if ms >= until:
                    st["rows_dropped_ms_ge_until"] += 1
                    continue
                by.setdefault(pool, []).append(r)
                st["rows_kept"] += 1
                if st["first_ms"] is None or ms < st["first_ms"]:
                    st["first_ms"] = ms
    for rows in by.values():
        rows.sort(key=lambda r: r["ms"])
    return by, st


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


# ------------------------------------------------------------------------------------------------------------------------ samples
OWED = ("protocol_fees_token_0", "protocol_fees_token_1", "fund_fees_token_0", "fund_fees_token_1", "creator_fees_token_0", "creator_fees_token_1")


def owed_split(r: dict[str, Any], wsol0: bool) -> tuple[int, int] | None:
    """11.2 item 5: wsol_is_token0 true: quote-side owed = _token_0 fields, base-side = _token_1; else swapped. None when a field is null."""
    if any(r.get(k) is None for k in OWED):
        return None
    t0 = r["protocol_fees_token_0"] + r["fund_fees_token_0"] + r["creator_fees_token_0"]
    t1 = r["protocol_fees_token_1"] + r["fund_fees_token_1"] + r["creator_fees_token_1"]
    return (t0, t1) if wsol0 else (t1, t0)


def valid_sample(r: dict[str, Any]) -> bool:
    return r.get("err") is None and r.get("quote_reserve") is not None and r.get("base_reserve") is not None and r.get("token_supply") is not None


class PoolS:
    """Valid samples of one pool as arrays, plus the full row list (vetoes read the latest row, err rows included)."""

    def __init__(self, pool: str, rows: list[dict[str, Any]], wsol0: bool, raw: bool, status_gaps: list[list[int]]) -> None:
        self.pool, self.rows, self.raw = pool, rows, raw
        self.all_ms = [r["ms"] for r in rows]
        keep, qs, bs = [], [], []
        for r in rows:
            if not valid_sample(r) or r.get("open_time") is None:
                continue
            ow = owed_split(r, wsol0)
            if ow is None:
                continue
            keep.append(r)
            qs.append(float(r["quote_reserve"]) - (0 if raw else ow[0]))
            bs.append(float(r["base_reserve"]) - (0 if raw else ow[1]))
        self.s = keep
        self.ms = np.array([r["ms"] for r in keep], dtype=np.int64)
        self.q, self.b = np.array(qs, float), np.array(bs, float)
        self.sup = np.array([float(r["token_supply"]) for r in keep], float)
        self.open_t = np.array([float(r["open_time"]) for r in keep], float)
        n = len(keep)
        gp = np.zeros(n, bool)
        if n > 1:
            gp[1:] = (self.ms[1:] - self.ms[:-1]) > GAP_MS
            for a, c in status_gaps:  # an interval covered by a recorder gap entry or a restart (11.3 item 1)
                gp[1:] |= (self.ms[:-1] < c) & (self.ms[1:] > a)
        self.gap_prev = gp
        self.ms_list = self.ms.tolist()


def candidates(P: PoolS, g7: bool) -> dict[str, np.ndarray]:
    """Vectorised gates G1 (non-liquidity drop of the corrected quote), G3, E1, E2, G6, G7 (lower bound, optional), G8. G5, gap refusal and vetoes are the caller's."""
    n = len(P.ms)
    z = np.zeros(n, bool)
    if n < 2:
        return {"cand": z, "liq": z}
    ms, q, b = P.ms, P.q, P.b
    qb = q * b
    liq = z.copy()
    liq[1:] = qb[1:] < qb[:-1] * (1 - F.K_TOL)
    g1 = z.copy()
    g1[1:] = (q[1:] < q[:-1]) & ~liq[1:]
    age = ms / 1000.0 - P.open_t
    ok = g1 & (age >= AGE_LO_S) & (age <= AGE_HI_S)
    with np.errstate(divide="ignore", invalid="ignore"):
        qfl = np.where(P.sup > 0, qb / P.sup, np.nan)
        spot = np.where(b > 0, q / b, np.nan)
    ok &= (q - qfl <= RP.Q_STAR) & (qfl >= RP.Q_FLOOR_MIN)
    g6 = z.copy()
    g6[1:] = (ms[1:] - ms[:-1]) <= ALIVE_MS
    ok &= g6
    lo = np.searchsorted(ms, ms - HIST_MS, side="left")
    j6 = np.searchsorted(ms, ms - ALIVE_MS, side="right") - 1
    with np.errstate(divide="ignore", invalid="ignore"):
        r6 = spot / spot[np.clip(j6, 0, n - 1)]
    ok &= (j6 >= lo) & (j6 >= 0) & (r6 < R6_MAX)
    if g7:
        dq = np.zeros(n)
        dq[1:] = np.abs(q[1:] - q[:-1])
        w = np.where(~P.gap_prev & ~liq, dq, 0.0)
        w[0] = 0.0
        cum = np.cumsum(w)
        p0 = np.searchsorted(ms, ms - DAY_MS, side="right")
        base = np.where(p0 >= 1, cum[np.clip(p0 - 1, 0, n - 1)], 0.0)
        ok &= (cum - base) >= RP.VOL_MIN
    return {"cand": ok, "liq": liq}


def veto_reason(r: dict[str, Any]) -> str | None:
    """11.5 on the latest row at or before the decision sample; null counts as the bad value. Then the 11.4 null-rate exclusion."""
    lp = r.get("lp_locked_frac")
    if not (isinstance(lp, (int, float)) and not isinstance(lp, bool) and lp >= RP.LP_MIN):
        return "lp_locked_lt_0.95"
    if r.get("mint_authority_set") is not False:
        return "mint_authority_set"
    if r.get("freeze_authority_set") is not False:
        return "freeze_authority_set"
    tp = r.get("token_program")
    if tp is None:
        return "token_program_null"
    if tp != "token" and (tp != "token-2022" or r.get("token_2022_transfer_fee") is not False):
        return "token_2022_transfer_fee"
    return None


def pool_fee(r: dict[str, Any]) -> float | None:
    """11.4: trade_fee_rate + creator_fee_rate (ppm of 1e6) where enable_creator_fee is true. None (null rate) = excluded, never assumed."""
    t = r.get("trade_fee_rate")
    if t is None:
        return None
    c = 0
    if r.get("enable_creator_fee") is True:
        c = r.get("creator_fee_rate")
        if c is None:
            return None
    return (t + c) / 1e6


# ------------------------------------------------------------------------------------------------------------------------ one pool, one cell
def new_counts() -> dict[str, Any]:
    return {"refused_gap": 0, "liquidity_events": 0, "veto_triggers": {}, "veto_pools": {}, "fee_null_triggers": 0, "unfilled_after_300s": 0, "unfilled_no_landing_sample": 0,
            "guarded": 0, "landed_after_gap": 0, "exits_delayed_by_gap": 0, "longest_exit_delay_s": 0.0, "stranded_no_exit_sample": 0, "stranded_other": 0, "triggered_pools": 0}


def sim_pool(P: PoolS, cand: dict[str, np.ndarray], stake_sol: float, lat_ms: int, until: int, cutoff: int, cnt: dict[str, Any], count_events: bool) -> list[dict[str, Any]]:
    ms, q, b, n = P.ms_list, P.q, P.b, len(P.ms)
    stake = stake_sol * 1e9
    out: list[dict[str, Any]] = []
    block = -1
    idx = np.nonzero(cand["cand"])[0].tolist()
    if count_events:
        cnt["liquidity_events"] += int(cand["liq"][P.ms <= cutoff].sum())
    for i in idx:
        t = ms[i]
        if t > cutoff:
            break
        if t < block:
            continue
        if P.gap_prev[i]:
            if count_events:
                cnt["refused_gap"] += 1
            continue
        vr = P.rows[max(0, bisect_left(P.all_ms, t + 1) - 1)]
        why = veto_reason(vr)
        if why:
            if count_events:
                cnt["veto_triggers"][why] = cnt["veto_triggers"].get(why, 0) + 1
                cnt["veto_pools"].setdefault(why, set()).add(P.pool)
            continue
        fee = pool_fee(vr)
        if fee is None:
            if count_events:
                cnt["fee_null_triggers"] += 1
            continue
        j = bisect_left(ms, t + lat_ms)
        if j >= n:
            if count_events:
                cnt["unfilled_no_landing_sample"] += 1
            block = until
            continue
        if ms[j] - t > MAX_LAND_MS:  # 11.3 item 3: unfilled, no send paid
            if count_events:
                cnt["unfilled_after_300s"] += 1
            block = ms[j]
            continue
        spot = float(q[i] / b[i])
        Qe, Be = float(q[j]), float(b[j])
        rt0 = RP.cpmm_round_trip(stake, Qe, Be, spot, Qe, Be, fee)
        base = {"pool": P.pool, "entry_ms": t, "land_ms": ms[j], "day": RP.day_of(t / 1000), "mig_day": RP.day_of(P.open_t[i]), "flow_rise": max(0.0, Qe - float(q[i])),
                "landed_after_gap": bool(ms[j] - t > GAP_MS), "exit_ms": None, "reason": None, "stranded_item6": False, "pnl_last_good": None}
        base["ssb"] = 1 if base["flow_rise"] > 0 else 0
        if rt0["guarded"]:
            if count_events:
                cnt["guarded"] += 1
            out.append({**base, "filled": False, "stranded": False, "pnl": rt0["pnl"], "exit_ms": t})
            block = ms[j]
            continue
        if base["landed_after_gap"] and count_events:
            cnt["landed_after_gap"] += 1
        if rt0["entry_invalid"]:
            if count_events:
                cnt["stranded_other"] += 1
            out.append({**base, "filled": True, "stranded": True, "pnl": rt0["pnl"], "exit_ms": ms[j]})
            block = ms[j] + STEP_MS
            continue
        net, tok = rt0["net"], rt0["tokens"]
        mark = (Qe + net) / (Be - tok)
        T = t + HOLD_MS
        xdec, reason = T, "time_24h"
        k = j + 1
        while k < n and ms[k] < T:
            if b[k] - tok > 0 and (q[k] + net) / (b[k] - tok) >= RP.TP * mark:
                xdec, reason = ms[k], "tp_1.5x"
                break
            k += 1
        exec_t = xdec + (TP_LAG_MS if reason == "tp_1.5x" else 0)
        xs = bisect_left(ms, exec_t)
        base.update(exit_ms=xdec, reason=reason)
        block = xdec + STEP_MS
        if xs >= n:  # 11.3 item 6: exit decided inside the recording but no sample to price it
            if xdec > until:
                base["open_at_end"] = True
            else:
                lg = RP.cpmm_round_trip(stake, Qe, Be, spot, float(q[n - 1]), float(b[n - 1]), fee) if n - 1 > j else None
                pnl = -stake - F.SEND_FEE_DECIDING - RENT_LG
                base.update(stranded_item6=True, pnl_last_good=(lg["pnl"] if lg else pnl))
                if count_events:
                    cnt["stranded_no_exit_sample"] += 1
                out.append({**base, "filled": True, "stranded": True, "pnl": pnl})
            continue
        if xs > 0 and ms[xs] - ms[xs - 1] > GAP_MS and ms[xs - 1] < exec_t:
            if count_events:
                cnt["exits_delayed_by_gap"] += 1
                cnt["longest_exit_delay_s"] = max(cnt["longest_exit_delay_s"], (ms[xs] - exec_t) / 1000.0)
        rt = RP.cpmm_round_trip(stake, Qe, Be, spot, float(q[xs]), float(b[xs]), fee)
        if rt["stranded"] and count_events:
            cnt["stranded_other"] += 1
        out.append({**base, "filled": True, "stranded": bool(rt["stranded"]), "pnl": rt["pnl"]})
    return out


# ------------------------------------------------------------------------------------------------------------------------ cells
def build_pools(by: dict[str, list[dict[str, Any]]], pools_meta: dict[str, Any], raw: bool, status_gaps: list[list[int]], missing: list[str]) -> list[PoolS]:
    res = []
    for pool, rows in sorted(by.items()):
        meta = pools_meta.get(pool)
        if not isinstance(meta, dict) or not isinstance(meta.get("wsol_is_token0"), bool):
            missing.append(pool)
            continue
        res.append(PoolS(pool, rows, meta["wsol_is_token0"], raw, status_gaps))
    return res


def run_cell(pools: list[PoolS], g7: bool, stake: float, lat_ms: int, until: int, cutoff: int, count_events: bool = False) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cnt = new_counts()
    for P in pools:
        c = candidates(P, g7)
        r = sim_pool(P, c, stake, lat_ms, until, cutoff, cnt, count_events)
        if r and count_events:
            cnt["triggered_pools"] += 1
        rows += r
    rows = [r for r in rows if not r.get("open_at_end")]
    rows.sort(key=lambda r: (r["entry_ms"], r["pool"]))
    return rows, cnt


def count_set(rows: list[dict[str, Any]]) -> int:
    return sum(1 for r in rows if r["filled"])


def leg_block(rows: list[dict[str, Any]], D: float, key: str = "pnl") -> dict[str, Any]:
    if not rows:
        return {"flat": RP.leg_stats([], np.array([]), D), "press_proxy": {"status": "NOT_AVAILABLE"}, "press_proxy_intercept": None, "filled_no_fail_mean_sol": None,
                "note_filled_no_fail": "no fail blend"}
    pnl = np.array([r[key] if r[key] is not None else r["pnl"] for r in rows], float)
    filled = np.array([r["filled"] for r in rows], bool)
    ssb = np.array([r["ssb"] for r in rows], float)
    nb = np.array([r["flow_rise"] for r in rows], float)
    out: dict[str, Any] = {}
    try:
        flat, press, c, _p = F.fail_legs(pnl, filled, ssb, nb)
        out["flat"] = RP.leg_stats(rows, flat, D)
        if not np.all(np.isfinite(press)) or not filled.any():
            raise ValueError("press")
        out["press_proxy"] = {"label": "PROXY (11.2 item 8): flow_rise / buy_flag, not the pressure-fail model, never counts for the gate", **RP.leg_stats(rows, press, D)}
        out["press_proxy_intercept"] = c
    except Exception:  # noqa: BLE001
        flat = np.where(filled, (1 - F.FAIL_FLAT) * pnl - F.FAIL_FLAT * F.SEND_FEE_DECIDING, pnl)
        out["flat"] = RP.leg_stats(rows, flat, D)
        out["press_proxy"] = {"status": "NOT_AVAILABLE"}
        out["press_proxy_intercept"] = None
    nf = pnl[filled]
    out["filled_no_fail_mean_sol"] = float(nf.mean() / 1e9) if len(nf) else None
    out["note_filled_no_fail"] = "no fail blend"
    return out


def cell_report(rows: list[dict[str, Any]], D: float) -> dict[str, Any]:
    out = leg_block(rows, D)
    out["guarded_unfilled"] = sum(1 for r in rows if not r["filled"])
    out["stranded_in_headline"] = sum(1 for r in rows if r["stranded"])
    out["stranded_item6"] = sum(1 for r in rows if r["stranded_item6"])
    out["stranded_pools_item6"] = sorted({r["pool"] for r in rows if r["stranded_item6"]})
    out["exit_reasons"] = {k: sum(1 for r in rows if r["reason"] == k) for k in ("tp_1.5x", "time_24h")}
    if any(r["stranded_item6"] for r in rows):
        out["last_good_state_variant"] = leg_block(rows, D, key="pnl_last_good")
    out["fifo_k4_report_only"] = fifo_cap([(r["entry_ms"], r["exit_ms"]) for r in rows if r["filled"] and r["exit_ms"] is not None])
    return out


def verdict(cell: dict[str, Any], n: int) -> dict[str, Any]:
    legs = ["flat"] + (["press_proxy"] if isinstance(cell.get("press_proxy"), dict) and cell["press_proxy"].get("status") != "NOT_AVAILABLE" else [])
    k1 = all(cell[g]["mean_sol"] is not None and cell[g]["mean_sol"] > 0 for g in legs)
    k4 = all(cell[g]["total_ex_top3_incl_guard_sol"] is not None and cell[g]["total_ex_top3_incl_guard_sol"] > 0 for g in legs)
    k2 = cell["flat"]["trades_per_day"] >= K2_MIN_PER_DAY
    return {"legs_read": legs, "K1": k1, "K2": k2, "K4": k4, "scored": n, "verdict": "PASS_FORWARD_KILL_LINE_NOT_A_PROMOTION" if (k1 and k2 and k4) else "KILLED"}


# ------------------------------------------------------------------------------------------------------------------------ markers
def counts_marker(md: Path, until: int) -> Path:
    return md / f"counts-{until}.json"


def kill_marker(md: Path) -> Path:
    return md / "kill-onelook.json"


def write_new(path: Path, obj: Any) -> None:
    try:
        with open(path, "x", encoding="utf-8") as f:
            json.dump(obj, f, sort_keys=True)
            f.flush()
    except FileExistsError:
        raise Refused(f"marker exists: {path.name}") from None


# ------------------------------------------------------------------------------------------------------------------------ driver
def prepare(out: Path, until: int) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any], dict[str, Any], list[list[int]], dict[str, Any]]:
    by, st = load_rows(out, until)
    pools_json = load_json(out / "pools.json", {})
    pools_meta = pools_json.get("pools") or {}
    status = load_json(out / "status.json", {})
    sg = [[int(a), int(c)] for a, c in (status.get("gaps") or []) if isinstance(a, (int, float)) and isinstance(c, (int, float))]
    return by, st, pools_meta, sg, status


def deciding_rows(out: Path, until: int) -> tuple[list[dict[str, Any]], dict[str, Any], list[PoolS], dict[str, Any], dict[str, Any], list[list[int]], dict[str, Any]]:
    by, st, pm, sg, status = prepare(out, until)
    missing: list[str] = []
    pools = build_pools(by, pm, False, sg, missing)
    cutoff = until - CUTOFF_BACK_MS
    rows, cnt = run_cell(pools, True, KILL_STAKE, LAT_DECIDING_MS, until, cutoff, count_events=True)
    st["pools_without_pools_json_entry"] = len(missing)
    return rows, cnt, pools, st, pm, sg, status


def mode_counts(out: Path, md: Path, until: int) -> int:
    mk = counts_marker(md, until)
    if mk.exists():
        raise Refused(f"counts already run at this instant (marker {mk.name})")
    rows, *_ = deciding_rows(out, until)
    n = count_set(rows)
    write_new(mk, {"schema": SCHEMA, "until": until, "count": n})
    return n


def mode_kill(out: Path, md: Path, until: int) -> dict[str, Any]:
    mk = counts_marker(md, until)
    cm = load_json(mk, None)
    if not isinstance(cm, dict) or not isinstance(cm.get("count"), int) or isinstance(cm.get("count"), bool) or cm["count"] < MIN_COUNT:
        raise Refused(f"kill refused: no counts marker with an integer >= {MIN_COUNT} at this instant")
    write_new(kill_marker(md), {"schema": SCHEMA, "until": until, "counted": cm["count"]})  # the one look is spent before anything is priced
    rows, cnt, pools, st, pm, sg, status = deciding_rows(out, until)
    n = count_set(rows)
    cutoff = until - CUTOFF_BACK_MS
    first = st["first_ms"] if st["first_ms"] is not None else until
    rec_h = (until - (first // 3_600_000) * 3_600_000) / 3_600_000
    D = rec_h / 24.0
    missing: list[str] = []
    raw_pools = build_pools(load_rows(out, until)[0], pm, True, sg, missing)
    cells: dict[str, Any] = {}
    for g7 in (True, False):
        for raw in (False, True):
            pp = raw_pools if raw else pools
            for stake in STAKES:
                for lat in LATS_MS:
                    if g7 and not raw and stake == KILL_STAKE and lat == LAT_DECIDING_MS:
                        r = rows
                    else:
                        r, _c = run_cell(pp, g7, stake, lat, until, cutoff)
                    key = f"stake{stake}_lat{lat}ms_{'G7' if g7 else 'noG7_report_only'}_{'raw_vault_variant' if raw else 'corrected'}"
                    cells[key] = cell_report(r, D)
    dec = cells[f"stake{KILL_STAKE}_lat{LAT_DECIDING_MS}ms_G7_corrected"]
    all_pool_gaps = 0
    lost = 0.0
    span = 0.0
    for P in pools:
        g = P.gap_prev
        if len(P.ms) > 1:
            all_pool_gaps += int(g.any())
            d = np.diff(P.ms)
            lost += float(d[g[1:]].sum())
            span += float(P.ms[-1] - P.ms[0])
    fee_on: dict[str, int] = {}
    for P in pools:
        v = str(next((r.get("creator_fee_on") for r in reversed(P.rows) if r.get("creator_fee_on") is not None), None))
        fee_on[v] = fee_on.get(v, 0) + 1
    ctr = status.get("counters") or {}
    veto_pools = {k: len(v) for k, v in cnt["veto_pools"].items()}
    cnt2 = {**cnt, "veto_pools": veto_pools}
    return {"schema": SCHEMA, "note": NOTE, "until_ms": until, "counted_set": n, "counts_marker_integer": cm["count"], "recorded_hours": rec_h, "D_days": D,
            "start_instant_first_row_ms": st["first_ms"], "recorder_start_ms": status.get("start_ms"), "coverage_gap": COVERAGE_GAP,
            "g8_reference": "latest sample at or before ms - 6 h in the 26 h window (FLOOR's j6)", "headline_cell": f"stake{KILL_STAKE}_lat{LAT_DECIDING_MS}ms_G7_corrected",
            "kill": verdict(dec, n), "cells": cells, "events": cnt2, "load": st, "universe_pools_sampled": len(pools),
            "pools_with_gap": all_pool_gaps, "share_pool_time_lost_to_gaps": (lost / span) if span else None,
            "recorder_counters": {k: ctr.get(k) for k in ("polls", "gap_ms", "poll_chunk_errors", "row_errors", "polls_failed")}, "recorder_gaps": sg,
            "restart_pause_lengths_s": [(c - a) / 1000.0 for a, c in sg], "creator_fee_on_distribution": fee_on,
            "latency_cells_note": "on a 60 s grid both latencies land on the next sample unless a gap intervenes; the cells differ only inside a gap",
            "bias_note": "intra-minute take-profits are missed (exits later or at the time exit); the direction of the bias on entries is unknown (11.2 item 4)",
            "veto_note": "no look-ahead toward eligibility (11.5); vesting and non-fee Token-2022 extensions unchecked"}


def main(argv: list[str] | None = None, now_ms: int | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mode", choices=("counts", "kill"))
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--marker-dir", required=True, help="where the counts / one-look markers live (never inside --out-dir)")
    ap.add_argument("--until", required=True, type=int, help="the check instant C in ms")
    a = ap.parse_args(argv)
    out, md = Path(a.out_dir), Path(a.marker_dir)
    try:
        if md.resolve() == out.resolve() or out.resolve() in md.resolve().parents:
            raise Refused("marker dir must be outside the out dir")
        if not out.is_dir() or not md.is_dir():
            raise Refused("out dir and marker dir must exist")
        now = int(time.time() * 1000) if now_ms is None else now_ms
        if now < a.until:
            raise Refused("refusing to run before the check instant (--until)")
        if a.mode == "counts":
            print(mode_counts(out, md, a.until))
        else:
            print(json.dumps(mode_kill(out, md, a.until), sort_keys=True, default=str))
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
