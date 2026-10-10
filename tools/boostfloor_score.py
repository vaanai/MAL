#!/usr/bin/env python3
"""RULE H5-BOOSTFLOOR v1: paper scorer on exploration tape. Scoring only. No live fills. No forward read.

Port of /data/mal/hunt-1008/h5-flows/{RULE.md, s14_boostdip.py, s15_score_dip.py, common.py} (hunt-1008 h5-flows, rule frozen
2026-10-08, RULE.md sha256 in RULE_SHA256). The rule, in one paragraph: after a non-mayhem graduation the pump.fun BOOST agent buys
about 17.585 SOL of the canonical PumpSwap pool in about 29 slices over about 345 s, whatever the price. PumpSwap prices at
(real quote + V) / base with V about 17.58 SOL. When early sells have drained the pool so that Q = real quote + V <= 40 SOL inside
0..300 s of the first print while BOOST still has budget, buy (one trade per pool, first qualifying print), and sell at
s0 + round(330 s / sps) plus a 0.55 s lag.

This file implements RULE.md and nothing else. It fits no threshold and tunes no parameter; every number below is the rule's. It does
not import the forward runner or the promotion gate.

INPUT. The audit's derived views of the exploration tape (/data/mal/audit-1008/tape), exactly as RULE.md names them:
  <work-dir>/meta/<day>.parquet   canonical pool per graduated mint (mint, s0, v, blk, mbt, ...)
  <work-dir>/paths/<day>.parquet  PumpSwap PRE-trade reserves per print (mint, slot, isbuy, sol, tok, q, b, th, bt), ordered
Default work-dir: /data/mal/audit-1008/work/g_reachable_cap_book_rescore. Parquet is read lazily (pandas + pyarrow, the audit venv);
every function that decides a trade is numpy only, so the unit tests need no pandas.

REFUSALS. Sealed blocks (fresh-0802 / fresh-0808 / fresh-0828), forward-paper / forward-walk / forward-1002 / forward-1016 /
runner output paths, any day not in the exploration allow-list, any graduation in the EXP-009 void hours, and anything at or after
CUTOFF (2026-10-02T10). There is no future read mode in this file. A future read needs its own pre-registration and a reviewed change.

MEMORY. Heavy runs: systemd-run --user --scope -q -p MemoryMax=10G nice -n 15 <python> -m tools.boostfloor_score ... The run waits
while MemAvailable < --min-avail-gb (30). No DuckDB is used (one day of paths is about 1.6M rows; a pool at a time).

OUTPUT. <out-dir>/rows.csv (one row per trade, leg, stake) and <out-dir>/summary.json (gate statistics, per fail model, per leg,
per stake, per block). Nothing here is gate evidence: the confirmation tape is in-sample for the lab.
"""

from __future__ import annotations

if __package__ in (None, ""):  # `python tools/boostfloor_score.py`
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import csv
import json
import math
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from tools.latency_curve import BOOTSTRAP_DRAWS, BOOTSTRAP_SEED, Pressure, fit_curve
from tools.paper_curve_math import pumpswap_sol_fee_ppm

SCHEMA = "boostfloor_score_v1"
RULE_ID = "H5-BOOSTFLOOR v1"
RULE_SHA256 = "c66b1a5990468080d56a97d67619c035cd81e2782eac89c48b94b8c9c9abe56c"  # /data/mal/hunt-1008/h5-flows/RULE.md
DEFAULT_WORK_DIR = "/data/mal/audit-1008/work/g_reachable_cap_book_rescore"

# ---- the rule's numbers (RULE.md). Do not edit without a new pre-registration -------------------------
V_LO = 17.5e9  # lamports; canonical pool V range
V_HI = 17.7e9
SPS_LO = 0.15  # seconds per slot must lie in (SPS_LO, SPS_HI)
SPS_HI = 0.6
MIN_PATH_PRINTS = 10
SPS_MIN_SLOT_SPAN = 300  # slots; the pool's path must span more than this for sps to be defined
BOOST_WINDOW_SLOTS = 1_600  # int(400 s / 0.25 s): the pool's first 1,600 slots
BOOST_MIN_BUYS = 3
BOOST_BUY_MIN = 0.2e9
BOOST_BUY_MAX = 2.0e9
BOOST_TOTAL_MAX = 17.7e9
BOOST_BUDGET = 17.585e9
BOOST_DONE_FRAC = 0.999  # trigger needs spent < 0.999 * budget
Q_STAR_SOL = 40.0  # Q = real quote + V after the sell, in SOL
T_MIN_S = 0.0
T_MAX_S = 300.0
EXIT_AFTER_S0_S = 330.0  # "end" exit: trigger slot s0 + round(330 / sps)
ENTRY_S = {"primary": 1.3, "binding": 1.9}
EXIT_LAG_S = 0.55
PRESSURE_WINDOW_S = 2.0
PRIO_LAMPORTS = 55_000  # per send, on the buy and on the sell
FLAT_FAIL = 0.15
STAKES_LAMPORTS = (("0.1", 100_000_000.0), ("0.25", 250_000_000.0))
LEGS = ("primary", "binding")
FAIL_MODELS = ("flat", "press", "nofail")
SELL_PRICE_APPROX = 0.9875  # only for the last print's post-trade state (no later print exists)

# ---- exploration sets (REPORT.md of h5-flows) ----------------------------------------------------------
DISCOVERY_DAYS = tuple(f"2026-08-{d:02d}" for d in range(14, 29))  # explore-0814: 2026-08-14T12 .. 08-28T11
CONFIRMATION_DAYS = tuple(f"2026-09-{d:02d}" for d in list(range(3, 16)) + list(range(18, 26)))
ALLOWED_BLOCKS = ("explore-0814", "fresh-0903", "exp011-0909", "fast-pool-0918", "oracle-insample-0922")
SETS = {"discovery": DISCOVERY_DAYS, "confirmation": CONFIRMATION_DAYS}

# ---- refusals (same constants as tools/cap_pick_gate_replay.py; checked in the tests) ------------------
FORBIDDEN_NAMES = ("fresh-0802", "fresh-0808", "fresh-0828", "oracle-live", "forward-paper", "forward-walk", "forward_walk",
                   "forward-1002", "forward-1016", "exp012-gate", "arm-audit", "arm_audit", "runner-status", "heartbeat",
                   "positions", "/var/lib/mal/paper", ".env", "helius", "keypair")
VOID_LO = "2026-09-15T12"  # EXP-009 hours [2026-09-15T12, 2026-09-18T23)
VOID_HI = "2026-09-18T23"
CUTOFF = "2026-10-02T10"  # nothing at or after this hour
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class Refused(Exception):
    pass


def refuse_name(path: str | Path) -> None:
    s = str(path)
    for bad in FORBIDDEN_NAMES:
        if bad in s:
            raise Refused(f"{s}: refused ({bad!r} is outside the exploration pools)")


def refuse_hour(hour: str, what: str = "") -> None:
    if VOID_LO <= hour < VOID_HI:
        raise Refused(f"{what or hour}: refused (EXP-009 hour {hour} in [{VOID_LO}, {VOID_HI}))")
    if hour >= CUTOFF:
        raise Refused(f"{what or hour}: refused (hour {hour} is at or after {CUTOFF}Z)")


def refuse_day(day: str) -> None:
    if not _DAY_RE.match(day):
        raise Refused(f"{day!r}: refused (not a YYYY-MM-DD day)")
    refuse_name(day)
    if f"{day}T00" >= CUTOFF or f"{day}T23" >= CUTOFF:
        raise Refused(f"{day}: refused (day reaches the {CUTOFF}Z cutoff; this scorer reads whole days)")
    if day not in DISCOVERY_DAYS and day not in CONFIRMATION_DAYS:
        raise Refused(f"{day}: refused (not an exploration day of the h5-flows discovery or confirmation sets)")


def refuse_block(blk: str) -> None:
    refuse_name(blk)
    if blk not in ALLOWED_BLOCKS:
        raise Refused(f"block {blk!r}: refused (not one of {ALLOWED_BLOCKS})")


def _hour_of_epoch_s(t: int) -> str:
    return time.strftime("%Y-%m-%dT%H", time.gmtime(int(t)))


# ---- pool state -------------------------------------------------------------------------------------
@dataclass
class PoolPath:
    """PumpSwap prints of one canonical pool, ordered. q, b are PRE-trade reserves; q excludes V."""

    sl: np.ndarray  # int64 slot
    q: np.ndarray  # float64 real quote lamports, pre-trade
    b: np.ndarray  # float64 base raw units, pre-trade
    isb: np.ndarray  # bool, buy of base with SOL
    sol: np.ndarray  # float64 lamports of the print
    tok: np.ndarray  # float64 base raw units of the print
    th: np.ndarray  # uint64 trader hash
    bt: np.ndarray  # int64 block time, seconds


def tier_fee(q: float, b: float) -> float:
    """Canonical PumpSwap tier fee as a fraction, on the market cap (SOL) of the pool state (q includes V)."""
    return pumpswap_sol_fee_ppm(q / b * 1e6) / 1e6


def seconds_per_slot(sl: np.ndarray, bt: np.ndarray) -> float:
    """Pool seconds per slot from block_time over its path. nan when the path is too short."""
    ok = bt > 0
    if ok.sum() > 2 and sl[ok][-1] > sl[ok][0] + SPS_MIN_SLOT_SPAN:
        return float((bt[ok][-1] - bt[ok][0]) / max(1, sl[ok][-1] - sl[ok][0]))
    return float("nan")


def sps_ok(sps: float) -> bool:
    return bool(SPS_LO < sps < SPS_HI)


def detect_boost_wallet(sl: np.ndarray, s0: int, th: np.ndarray, sol: np.ndarray, isb: np.ndarray) -> int | None:
    """BOOST wallet = buy-only wallet with >= 3 buys, each 0.2..2.0 SOL, total <= 17.7 SOL, in the pool's first 1,600 slots.

    The most buys wins; a tie goes to the lowest wallet hash. Completion of the 17.585 SOL budget is not required, so an
    early stop counts against the rule. Live equivalent: the per-pool BOOST vault PDA.
    """
    first = (sl - s0) <= BOOST_WINDOW_SLOTS
    if not first.any():
        return None
    t, s, b = th[first], np.asarray(sol[first], float), isb[first]
    uniq, inv = np.unique(t, return_inverse=True)
    n = np.bincount(inv)
    tot = np.bincount(inv, weights=s)
    n_sell = np.bincount(inv, weights=(~b).astype(float))
    mn = np.full(len(uniq), np.inf)
    mx = np.full(len(uniq), -np.inf)
    np.minimum.at(mn, inv, s)
    np.maximum.at(mx, inv, s)
    cand = np.flatnonzero((n_sell == 0) & (n >= BOOST_MIN_BUYS) & (mn >= BOOST_BUY_MIN) & (mx <= BOOST_BUY_MAX) & (tot <= BOOST_TOTAL_MAX))
    if len(cand) == 0:
        return None
    return int(uniq[cand[int(np.argmax(n[cand]))]])  # argmax = first max = lowest hash on a tie


def boost_spent(th: np.ndarray, sol: np.ndarray, boost: int | None) -> np.ndarray:
    """spent(i) = cumulative BOOST buy lamports up to and including print i."""
    if boost is None:
        return np.zeros(len(th))
    return np.cumsum(np.where(th == np.uint64(boost), np.asarray(sol, float), 0.0))


def post_trade_state(q: np.ndarray, b: np.ndarray, isb: np.ndarray, sol: np.ndarray, tok: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Post-trade (q, b) of print i = pre-trade (q, b) of print i+1. The last print has no successor: approximate it."""
    last_q = q[-1] + (SELL_PRICE_APPROX * sol[-1] if isb[-1] else -sol[-1] / SELL_PRICE_APPROX)
    last_b = b[-1] - (tok[-1] if isb[-1] else -tok[-1])
    return np.r_[q[1:], last_q], np.r_[b[1:], last_b]


def find_trigger(sl: np.ndarray, s0: int, sps: float, isb: np.ndarray, qpost: np.ndarray, spent: np.ndarray) -> int | None:
    """First print that is a sell, inside [0, 300] s of the first print, leaves Q <= 40 SOL, with BOOST budget left."""
    t = (sl - s0) * sps
    cond = (qpost / 1e9 <= Q_STAR_SOL) & (t <= T_MAX_S) & (t >= T_MIN_S) & (~isb) & (spent < BOOST_BUDGET * BOOST_DONE_FRAC)
    idx = np.flatnonzero(cond)
    return int(idx[0]) if len(idx) else None


def state_at(sl: np.ndarray, q: np.ndarray, b: np.ndarray, qpost: np.ndarray, bpost: np.ndarray, slot: int) -> tuple[float, float]:
    """END bound: pool state after every print in slots <= `slot` (= pre-trade of the first print with a later slot)."""
    j = int(np.searchsorted(sl, slot, "right"))
    return (float(q[j]), float(b[j])) if j < len(sl) else (float(qpost[-1]), float(bpost[-1]))


def pressure_at(sl: np.ndarray, isb: np.ndarray, sol: np.ndarray, landing: int, sps: float) -> tuple[int, float]:
    """(buys in the landing slot, buy lamports in the PRESSURE_WINDOW_S up to and including the landing slot)."""
    ix = int(np.searchsorted(sl, landing, "left"))
    hi = int(np.searchsorted(sl, landing, "right"))
    lo = int(np.searchsorted(sl, landing - int(round(PRESSURE_WINDOW_S / sps)), "left"))
    return int(isb[ix:hi].sum()), float(sol[lo:hi][isb[lo:hi]].sum())


def entry_slots(sps: float) -> dict[str, int]:
    return {leg: math.ceil(s / sps - 1e-9) for leg, s in ENTRY_S.items()}


def fill_round_trip(qe: float, be: float, qx: float, bx: float, stake: float) -> tuple[float, float]:
    """(pnl lamports, gross) of one buy at landing state (qe, be) and one sell at exit state (qx, bx). Own impact on Q incl. V.

    Buy: net = S (1 - f), tokens = B net / (Q + net). Our buy stays in the pool until the sell, so the exit state carries it.
    Sell: proceeds = tokens Q' / (B' + tokens) (1 - tier fee). Priority fee 55,000 lamports on each send.
    """
    f = tier_fee(qe, be)
    net = stake * (1 - f)
    tk = be * net / (qe + net)
    q2 = qx + net
    b2 = bx - tk
    proceeds = tk * q2 / (b2 + tk) * (1 - tier_fee(q2, b2 + tk))
    return proceeds - stake - 2 * PRIO_LAMPORTS, (q2 / b2) / (qe / be) - 1


@dataclass
class PoolResult:
    status: str  # ok | short | sps | no_trigger | no_exit | unsorted
    rows: list[dict[str, Any]] = field(default_factory=list)


def score_pool(path: PoolPath, s0: int, v: float) -> PoolResult:
    """One pool, at most one trigger, both entry legs, both stakes. `path` must already be restricted to slot >= s0."""
    sl = path.sl
    if len(sl) < MIN_PATH_PRINTS:
        return PoolResult("short")
    if np.any(np.diff(sl) < 0):
        return PoolResult("unsorted")
    sps = seconds_per_slot(sl, path.bt)
    if not sps_ok(sps):
        return PoolResult("sps")
    q = path.q + v
    b, isb, sol, tok = path.b, path.isb, np.asarray(path.sol, float), path.tok
    boost = detect_boost_wallet(sl, s0, path.th, sol, isb)
    spent = boost_spent(path.th, sol, boost)
    qpost, bpost = post_trade_state(q, b, isb, sol, tok)
    i = find_trigger(sl, s0, sps, isb, qpost, spent)
    if i is None:
        return PoolResult("no_trigger")
    exit_slot = s0 + int(round(EXIT_AFTER_S0_S / sps))
    el = math.ceil(EXIT_LAG_S / sps - 1e-9)
    rows: list[dict[str, Any]] = []
    for leg, k in entry_slots(sps).items():
        landing = int(sl[i]) + k
        if exit_slot <= landing:
            continue
        qe, be = state_at(sl, q, b, qpost, bpost, landing)
        qx, bx = state_at(sl, q, b, qpost, bpost, exit_slot + el)
        ssb, nb = pressure_at(sl, isb, sol, landing, sps)
        hi = int(np.searchsorted(sl, landing, "right"))
        rem = (BOOST_BUDGET - spent[min(hi, len(sl)) - 1]) / 1e9
        for label, stake in STAKES_LAMPORTS:
            pnl, gross = fill_round_trip(qe, be, qx, bx, stake)
            rows.append(dict(leg=leg, stake_sol=label, landing_slot=landing, exit_landing_slot=exit_slot + el, sps=sps, trig_t_s=float((sl[i] - s0) * sps),
                             q_trig_sol=float(qpost[i] / 1e9), ssb=ssb, nb_lamports=nb, rem_boost_sol=float(rem), gross=float(gross),
                             pnl_nofail_lamports=float(pnl)))
    return PoolResult("ok" if rows else "no_exit", rows)


# ---- fail legs and gate statistics ------------------------------------------------------------------
def apply_fail_legs(rows: list[dict[str, Any]]) -> None:
    """Add p_fail, pnl_flat_lamports, pnl_press_lamports in place. The pressure curve is fit per entry leg on this book's sends
    (tools.latency_curve.fit_curve: slopes 0.8 / 0.35 at scale 1, intercept refit so mean p = 0.289)."""
    for leg in LEGS:
        idx = [i for i, r in enumerate(rows) if r["leg"] == leg and r["stake_sol"] == STAKES_LAMPORTS[0][0]]
        if not idx:
            continue
        curve = fit_curve([Pressure(int(rows[i]["ssb"]), int(rows[i]["nb_lamports"])) for i in idx])
        for r in rows:
            if r["leg"] != leg:
                continue
            p = curve.p(Pressure(int(r["ssb"]), int(r["nb_lamports"])))
            pnl = r["pnl_nofail_lamports"]
            r["p_fail"] = p
            r["pnl_flat_lamports"] = (1 - FLAT_FAIL) * pnl + FLAT_FAIL * (-PRIO_LAMPORTS)
            r["pnl_press_lamports"] = (1 - p) * pnl + p * (-PRIO_LAMPORTS)


def gate_stats(pnl_lamports: Sequence[float], days: Sequence[str], stake_lamports: float, draws: int = BOOTSTRAP_DRAWS, seed: int = BOOTSTRAP_SEED) -> dict[str, Any]:
    """Date-cluster bootstrap: `draws` resamples of the days (with replacement), mean of the pooled trades, 5th / 95th percentile."""
    x = np.asarray(pnl_lamports, float) / 1e9
    d = np.asarray(days)
    if len(x) == 0:
        return {"n": 0}
    ud = np.unique(d)
    idx = {u: np.flatnonzero(d == u) for u in ud}
    per_day = np.array([x[idx[u]].sum() for u in ud])
    rng = np.random.default_rng(seed)
    bm = np.array([np.concatenate([x[idx[ud[j]]] for j in rng.choice(len(ud), size=len(ud), replace=True)]).mean() for _ in range(draws)])
    srt = np.sort(x)[::-1]
    stake = stake_lamports / 1e9
    return dict(n=int(len(x)), days=int(len(ud)), mean_pct=float(100 * x.mean() / stake), mean_sol=float(x.mean()),
                ci5_pct=float(100 * np.percentile(bm, 5) / stake), ci95_pct=float(100 * np.percentile(bm, 95) / stake),
                days_pos=int((per_day > 0).sum()), total_sol=float(x.sum()), ex_top3_sol=float(x.sum() - srt[:3].sum()),
                ex_best_day_sol=float(x.sum() - per_day.max()), median_pct=float(100 * np.median(x) / stake))


def gate_arithmetic(flat: dict[str, Any], press: dict[str, Any]) -> dict[str, Any]:
    """The CLAUDE.md promotion-gate arithmetic on these numbers. NOT gate evidence: it is not out-of-sample for the lab."""
    def ok(g: dict[str, Any]) -> dict[str, bool]:
        if g.get("n", 0) == 0:
            return dict(n_ge_100=False, days_ge_5=False, majority_days_pos=False, ci5_gt_0=False, ex_top3_gt_0=False)
        return dict(n_ge_100=g["n"] >= 100, days_ge_5=g["days"] >= 5, majority_days_pos=2 * g["days_pos"] > g["days"],
                    ci5_gt_0=g["ci5_pct"] > 0, ex_top3_gt_0=g["ex_top3_sol"] > 0)
    a, b = ok(flat), ok(press)
    return dict(flat=a, press=b, all=bool(all(a.values()) and all(b.values())),
                note="arithmetic only; exploration-grade; the confirmation tape is in-sample for the lab")


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    blocks = sorted({r["blk"] for r in rows})
    for leg in LEGS:
        for label, stake in STAKES_LAMPORTS:
            sel = [r for r in rows if r["leg"] == leg and r["stake_sol"] == label]
            if not sel:
                continue
            cell: dict[str, Any] = {}
            for fm in FAIL_MODELS:
                col = f"pnl_{fm}_lamports"
                cell[fm] = gate_stats([r[col] for r in sel], [r["day"] for r in sel], stake)
            cell["gate_arithmetic"] = gate_arithmetic(cell["flat"], cell["press"])
            gross = np.array([r["gross"] for r in sel])
            flat = np.array([r["pnl_flat_lamports"] for r in sel])
            big = gross > 1.0
            cell["tail"] = dict(gross_median_pct=float(100 * np.median(gross)), share_gross_gt_100pct=float(big.mean()),
                                flat_pnl_share_from_gross_gt_100pct=float(flat[big].sum() / flat.sum()) if flat.sum() != 0 else None)
            cell["by_block"] = {
                blk: {fm: gate_stats([r[f"pnl_{fm}_lamports"] for r in sel if r["blk"] == blk], [r["day"] for r in sel if r["blk"] == blk], stake)
                      for fm in ("flat", "press")}
                for blk in blocks
            }
            out[f"{leg}|{label}"] = cell
    return out


# ---- I/O ------------------------------------------------------------------------------------------------
def wait_for_memory(min_avail_gb: float, poll_s: float = 30.0, max_wait_s: float = 3600.0, meminfo: str = "/proc/meminfo") -> float:
    """Block while MemAvailable < min_avail_gb. Returns MemAvailable in GB. Raises Refused after max_wait_s."""
    waited = 0.0
    while True:
        avail = 0.0
        for line in Path(meminfo).read_text().splitlines():
            if line.startswith("MemAvailable:"):
                avail = int(line.split()[1]) / 1024 / 1024
        if avail >= min_avail_gb:
            return avail
        if waited >= max_wait_s:
            raise Refused(f"MemAvailable {avail:.1f} GB < {min_avail_gb} GB after {waited:.0f} s")
        print(f"waiting: MemAvailable {avail:.1f} GB < {min_avail_gb} GB", file=sys.stderr, flush=True)
        time.sleep(poll_s)
        waited += poll_s


def load_day(work_dir: Path, day: str):
    """Yield (mint, blk, s0, v, PoolPath) for the day's V-range canonical pools that have exactly one meta row. Needs pandas + pyarrow."""
    import pandas as pd  # lazy: the audit venv

    refuse_day(day)
    meta_p, paths_p = work_dir / "meta" / f"{day}.parquet", work_dir / "paths" / f"{day}.parquet"
    for p in (meta_p, paths_p):
        refuse_name(p)
    meta = pd.read_parquet(meta_p)
    meta = meta[(meta.v >= V_LO) & (meta.v <= V_HI)]
    for blk in meta.blk.unique():
        refuse_block(str(blk))
    for t in meta.mbt:
        refuse_hour(_hour_of_epoch_s(int(t)), f"{day} graduation")
    counts = meta.mint.value_counts()
    meta = meta[meta.mint.isin(counts[counts == 1].index)].set_index("mint")
    paths = pd.read_parquet(paths_p)
    paths = paths[paths.mint.isin(meta.index)]
    if len(paths) and _hour_of_epoch_s(int(paths.bt.max())) >= CUTOFF:
        raise Refused(f"{paths_p}: refused (a print is at or after {CUTOFF}Z)")
    for mint, g in paths.groupby("mint", sort=False):
        mm = meta.loc[mint]
        s0, v = int(mm.s0), float(mm.v)
        g = g[g.slot >= s0]
        yield str(mint), str(mm.blk), s0, v, PoolPath(sl=g.slot.values.astype(np.int64), q=g.q.values.astype(float), b=g.b.values.astype(float),
                                                      isb=g.isbuy.values.astype(bool), sol=g.sol.values.astype(float), tok=g.tok.values.astype(float),
                                                      th=g.th.values.astype(np.uint64), bt=g.bt.values.astype(np.int64))


def score_days(work_dir: Path, days: Sequence[str], log=print) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows: list[dict[str, Any]] = []
    diag: dict[str, int] = dict(pools=0, ok=0, short=0, sps=0, no_trigger=0, no_exit=0, unsorted=0)
    for day in days:
        n0 = len(rows)
        for mint, blk, s0, v, path in load_day(work_dir, day):
            res = score_pool(path, s0, v)
            diag["pools"] += 1
            diag[res.status] += 1
            for r in res.rows:
                rows.append(dict(day=day, blk=blk, mint=mint, **r))
        log(f"{day} +{len(rows) - n0} rows (total {len(rows)})")
    apply_fail_legs(rows)
    return rows, diag


ROW_COLUMNS = ("day", "blk", "mint", "leg", "stake_sol", "landing_slot", "exit_landing_slot", "sps", "trig_t_s", "q_trig_sol", "ssb", "nb_lamports",
               "rem_boost_sol", "gross", "pnl_nofail_lamports", "p_fail", "pnl_flat_lamports", "pnl_press_lamports")


def write_outputs(out_dir: Path, rows: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "rows.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(ROW_COLUMNS)
        for r in rows:
            w.writerow([repr(r[c]) if isinstance(r[c], float) else r[c] for c in ROW_COLUMNS])
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--set", choices=sorted(SETS), help="h5-flows discovery (08-14..08-28) or confirmation (09-03..09-25) days")
    g.add_argument("--days", help="comma-separated exploration days (each must be in a set)")
    ap.add_argument("--work-dir", default=DEFAULT_WORK_DIR)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--min-avail-gb", type=float, default=30.0)
    ap.add_argument("--max-wait-s", type=float, default=3600.0)
    a = ap.parse_args(argv)
    try:
        days = list(SETS[a.set]) if a.set else [d.strip() for d in a.days.split(",") if d.strip()]
        for d in days:
            refuse_day(d)
        work_dir, out_dir = Path(a.work_dir), Path(a.out_dir)
        refuse_name(work_dir)
        refuse_name(out_dir)
        wait_for_memory(a.min_avail_gb, max_wait_s=a.max_wait_s)
        rows, diag = score_days(work_dir, days, log=lambda s: print(s, file=sys.stderr, flush=True))
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    summary = dict(schema=SCHEMA, rule=RULE_ID, rule_sha256=RULE_SHA256, set=a.set, days=days, work_dir=str(work_dir), diagnostics=diag,
                   params=dict(v_range_lamports=[V_LO, V_HI], q_star_sol=Q_STAR_SOL, t_window_s=[T_MIN_S, T_MAX_S], exit_after_s0_s=EXIT_AFTER_S0_S,
                               entry_s=ENTRY_S, exit_lag_s=EXIT_LAG_S, prio_lamports=PRIO_LAMPORTS, flat_fail=FLAT_FAIL, bootstrap=[BOOTSTRAP_DRAWS, BOOTSTRAP_SEED]),
                   results=summarize(rows),
                   caveat="Exploration-grade. Not gate evidence. The confirmation tape is in-sample for the lab. Impact keeps other traders' deltas fixed.")
    write_outputs(out_dir, rows, summary)
    print(json.dumps({k: summary["results"][k]["flat"] for k in summary["results"]}, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
