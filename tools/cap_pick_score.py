#!/usr/bin/env python3
"""CAP-PICK scorer, phase 1 (reproduction of the audit's `P_primary`). LAB SCORER. NOT A PROMOTE, NOT GATE EVIDENCE.

Port of the audit simulator `g_reachable_cap_book_rescore/sim.py` (audit 2026-10-08, G) onto the lab's own block layout. The audit read an
audit-only Parquet copy of the tape; this tool reads the lab's `trades/`, `migrations/` hour files (`*.jsonl.zst`) directly, one UTC day at a time.
It must not merge before the owner decides O2/O3. Phase 1 scores G's `P_primary` and nothing else; the judge's variants (per-hour ms/slot, exit lag in
ms, the gross guard, the block_time cap anchor, rent/dust) are phase 2 and are NOT here (`capv_JUDGE.md` section 4).

Book (every parameter is a `Config` field and a CLI flag; the defaults are G's `P_primary`)
  Universe  every canonical PumpSwap migration pool with V in [17.5e9, 17.7e9] (`pool_v_0909.json`), uncensored, one attempt per mint.
  Entry     landing slot X = s0 + k, k = round(1.3 s / that UTC day's seconds-per-slot), END bound (state after every print in slot X).
            s0 = the first PumpSwap print of the canonical pool; mslot = the `complete` slot.
  Guard     min_out at seed x 1.15, net: the buy executes only if net_in / tokens_out <= 1.15 x (67,405,853,863 + V) / 206.9e12 on the landing state.
            A guarded-out buy is a failed tx that pays ONE send fee.
  Exit      tp50 / sl30 on post-trade spot against the post-buy mark, a 300 s wall-clock cap from the landing slot (D = X + round(300 / sec-per-slot)),
            exit lag 2 slots on the END bound (fill = state before the first print with slot >= trigger + lag + 1).
  Size/fee  0.5 SOL, 55,000 lamports per send; the pool fee tier is `tools.paper_curve_math.PUMPSWAP_SOL_FEE_TIERS` on (quote + V, base).
  Fail legs live 1/62 and flat 15% (a fill pays (1-p) pnl + p (-fee)); pressure = `tools.latency_curve.fit_curve` (slopes at scale 1, intercept so the mean
            p over this run's guard-passed sends is 0.289). A guarded-out row is -fee on every leg.

RESERVE CONVENTIONS (this broke the audit once)
  * PumpSwap tape rows carry PRE-trade reserves. Bonding-curve rows carry POST-trade reserves (this scorer never prices a bonding row).
  * PumpSwap price = (quote vault + V) / base. V is added to the quote reserve and nothing else.
  * The state after print i is the PRE-trade reserves of print i+1 (the observed chain). Only the state after the LAST print is derived, and that one uses
    the lab's `tools.paper_price_path.pumpswap_post_trade_reserves` (`--final-state lab`, default). `--final-state g` is the audit's constant 1.25% approximation,
    kept so the reproduction can separate that one difference.
  * Within-slot order: (slot, tx_index, event_index). A row with a null tx_index (oracle-insample-0922) is ordered by file order instead.

Hard limits (asserted; see `check_path_allowed` / `check_hour_allowed`): exploration pools only. Never fresh-0802, fresh-0808, fresh-0828, any forward or
oracle-live path, the EXP-009 hours [2026-09-15T12, 2026-09-18T23), or any hour at or after 2026-10-02T10. Oracle in-sample hours stop at 2026-09-25T06.
No network, no key, no Helius call. Output: `rows.csv` (one row per attempt) and `summary.json`.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

import numpy as np

from tools.latency_curve import Pressure, fit_curve
from tools.paper_curve_math import PUMPSWAP_SOL_FEE_TIERS, venue_fee_ppm
from tools.paper_price_path import VENUE_PUMPSWAP, pumpswap_post_trade_reserves

TOOL = "tools.cap_pick_score"
SCHEMA = "cap_pick_score_v1"

TH = np.array([t for t, _ in PUMPSWAP_SOL_FEE_TIERS], dtype=float)
TP = np.array([p for _, p in PUMPSWAP_SOL_FEE_TIERS], dtype=float)

# --- pinned numbers (G's sim.py / prereg.json) ---------------------------------------------------------------------------------------
SEED_Q = 67_405_853_863
SEED_B = 206_900_000_000_000
G_FINAL_FEE = 0.0125  # the audit's constant for the state after the last print
WINDOW_SLOTS = 7300  # prints in [mslot, mslot + 7300)
UNCENSORED_HORIZON = 6900  # uncensored: s0 + 6900 <= max slot on the loaded trade files
MAX_S0_GAP = 400  # and s0 - mslot <= 400
V_LO, V_HI = 17_500_000_000, 17_700_000_000
TARGET_FAIL_RATE = 0.289
PICK_THRESHOLD = 0.8030766588450794  # EXP-012 frozen threshold (G's strata.py)
DEFAULT_VMAP = "/data/mal/pumpswap-virtual/pool_v_0909.json"

# Slots per hour by UTC day, copied from G's sim.py (itself from verify/d07_exits-d07-F4/indep_wall.py). 2026-09-18 (one hour, 09-18T23) uses 09-19's.
SLOTS_PER_HOUR: dict[str, float] = {
    "2026-08-14": 8652.5, "2026-08-15": 8657.5, "2026-08-16": 8657.0, "2026-08-17": 8685.0, "2026-08-18": 8666.0, "2026-08-19": 8656.0,
    "2026-08-20": 8666.5, "2026-08-21": 9819.5, "2026-08-22": 9813.0, "2026-08-23": 9847.5, "2026-08-24": 9848.5, "2026-08-25": 9845.5,
    "2026-08-26": 9847.5, "2026-08-27": 9842.0, "2026-08-28": 9846.0, "2026-09-03": 11405.0, "2026-09-04": 11418.5, "2026-09-05": 11430.0,
    "2026-09-06": 11366.0, "2026-09-07": 11363.0, "2026-09-08": 11369.5, "2026-09-09": 11366.5, "2026-09-10": 11395.0, "2026-09-11": 11363.5,
    "2026-09-12": 11361.0, "2026-09-13": 11404.0, "2026-09-14": 11402.0, "2026-09-15": 11409.0, "2026-09-18": 13496.5, "2026-09-19": 13496.5,
    "2026-09-20": 13504.5, "2026-09-21": 13495.0, "2026-09-22": 13484.0, "2026-09-23": 13576.5, "2026-09-24": 13546.0, "2026-09-25": 13476.0,
}

# block label -> (CLI flag source). The labels are the audit's `block` column.
BLOCK_P1A, BLOCK_P1C = "fast-pool-0918", "oracle-insample-0922"
BLOCK_P2, BLOCK_P3, BLOCK_P4 = "explore-0814", "fresh-0903", "exp011-0909"
GROUPS = {"P2-P4": (BLOCK_P2, BLOCK_P3, BLOCK_P4), "P1": (BLOCK_P1A, BLOCK_P1C)}
DEFAULT_ROOTS = {
    "p1_fast_dir": "/data/mal/clean-view/fast-pool-2026-09-18T23_2026-09-22T00",
    "p1_oracle_insample_dir": "/data/mal/clean-view/oracle-insample-2026-09-22_25",
    "p2_view_dir": ["/data/mal/clean-view/explore-0814"],
    "p3_root": "/data/mal/blocks-clean/fresh-0903",
    "p4_view_dir": ["/data/mal/clean-view/exp011-0909"],
}

# --- hard limits ---------------------------------------------------------------------------------------------------------------------
FORBIDDEN_PATH_PARTS = ("fresh-0802", "fresh-0808", "fresh-0828", "forward", "oracle-live")
EXP009_HOURS = ("2026-09-15T12", "2026-09-18T23")  # [lo, hi)
CUTOFF_HOUR = "2026-10-02T10"  # nothing at or after this
ORACLE_INSAMPLE_LAST_HOUR = "2026-09-25T06"  # later oracle hours belong to the live-tape row


class Refused(Exception):
    """The tool will not run: a forbidden path, hour or argument."""


def check_path_allowed(path: str | Path) -> None:
    s = str(path)
    for part in FORBIDDEN_PATH_PARTS:
        if part in s:
            raise Refused(f"forbidden path ({part}): {s}")


def check_hour_allowed(hour: str) -> None:
    if EXP009_HOURS[0] <= hour < EXP009_HOURS[1]:
        raise Refused(f"EXP-009 hour {hour} is forbidden")
    if hour >= CUTOFF_HOUR:
        raise Refused(f"hour {hour} is at or after {CUTOFF_HOUR}Z and is forbidden")


# --- config --------------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Config:
    """Every parameter of the book. Defaults are G's `P_primary`."""

    k_seconds: float = 1.3
    bound: str = "END"  # "END" | "START"
    exit_lag: int = 2
    guard: str = "min_out"  # "min_out" | "none"
    guard_ratio: float = 1.15
    cap_seconds: float = 300.0
    tp: float = 0.5
    sl: float = 0.3
    size_lamports: int = 500_000_000
    fee_lamports: int = 55_000
    live_fail: float = 1.0 / 62.0
    flat_fail: float = 0.15
    target_fail: float = TARGET_FAIL_RATE
    final_state: str = "lab"  # "lab" | "g"
    v_lo: int = V_LO
    v_hi: int = V_HI
    window_slots: int = WINDOW_SLOTS
    pick_threshold: float = PICK_THRESHOLD

    def validate(self) -> None:
        if self.bound not in ("END", "START"):
            raise Refused(f"bound must be END or START, got {self.bound}")
        if self.guard not in ("min_out", "none"):
            raise Refused(f"guard must be min_out or none, got {self.guard}")
        if self.final_state not in ("lab", "g"):
            raise Refused(f"final-state must be lab or g, got {self.final_state}")
        if self.k_seconds <= 0 or self.cap_seconds <= 0 or self.exit_lag < 0 or self.size_lamports <= 0 or self.fee_lamports < 0:
            raise Refused("k, cap, size must be positive and lag, fee non-negative")


# --- pricing -------------------------------------------------------------------------------------------------------------------------


def fee_ppm(q: Any, b: Any) -> Any:
    """Pool fee tier on market cap = (quote + V) / (base * 1000) * 1e9. `q` already includes V (G's convention)."""
    mcap = q / (b * 1000.0) * 1e9
    idx = np.searchsorted(TH, mcap + 1e-9, side="right") - 1
    return TP[np.clip(idx, 0, len(TP) - 1)]


def sec_per_slot(day: str, sph: Mapping[str, float]) -> float:
    if day not in sph:
        raise Refused(f"no measured slots-per-hour for {day}")
    return 3600.0 / sph[day]


def k_for(day: str, seconds: float, sph: Mapping[str, float]) -> int:
    """k = round(seconds / that UTC day's measured seconds-per-slot) (Python round, as G)."""
    return int(round(seconds / sec_per_slot(day, sph)))


def final_state(is_buy: bool, q: float, b: float, sol: float, tok: float, mode: str) -> tuple[float, float] | None:
    """Reserves after the LAST print. Tape reserves are PRE-trade, so the state after it is derived: `lab` = tools.paper_price_path
    .pumpswap_post_trade_reserves (the lab's handling; fee tier from the row's own price, as `print_from_trade_row` does); `g` = the audit's constant."""
    if mode == "g":
        if is_buy:
            return q + sol * (1 - G_FINAL_FEE), b - tok
        return q - sol / (1 - G_FINAL_FEE), b + tok
    if not all(math.isfinite(v) for v in (q, b, sol, tok)):
        return None
    price = q / (b * 1000) if b > 0 else 0.0
    posted = pumpswap_post_trade_reserves(
        side="buy" if is_buy else "sell",
        quote_reserve=int(q),
        base_reserve=int(b),
        sol_lamports=int(sol),
        token_raw=int(tok),
        fee_ppm=venue_fee_ppm(VENUE_PUMPSWAP, price * 1_000_000_000),
    )
    if posted is None:
        return None
    return float(posted[0]), float(posted[1])


def build_states(
    slot: np.ndarray, isbuy: np.ndarray, sol: np.ndarray, tok: np.ndarray, q: np.ndarray, b: np.ndarray, v: float, mode: str
) -> tuple[np.ndarray, np.ndarray, int] | None:
    """(Qpre, Bpre, fallbacks), n+1 entries each: state BEFORE print i is the PRE-trade reserves of print i (quote + V); entry n is the state after the last
    print. None when any reserve is non-positive (the mint is not an attempt). `fallbacks` = 1 if the lab final state could not be derived and G's was used."""
    fb = 0
    fs = final_state(bool(isbuy[-1]), float(q[-1]), float(b[-1]), float(sol[-1]), float(tok[-1]), mode)
    if fs is None:
        fs = final_state(bool(isbuy[-1]), float(q[-1]), float(b[-1]), float(sol[-1]), float(tok[-1]), "g")
        fb = 1
    ql, bl = fs
    qpre = np.concatenate([q, [ql]]) + v
    bpre = np.concatenate([b, [bl]])
    if (qpre <= 0).any() or (bpre <= 0).any():
        return None
    return qpre, bpre, fb


# --- the book ------------------------------------------------------------------------------------------------------------------------


def simulate_attempt(
    cfg: Config, *, day: str, sph: Mapping[str, float], v: float, s0: int, slot: np.ndarray, isbuy: np.ndarray, sol: np.ndarray, qpre: np.ndarray, bpre: np.ndarray
) -> dict[str, Any]:
    """One attempt on one mint's ordered path. Mirrors G's `run_day` inner loop for `tpsl_wall` exits and the `min_out` / `none` guards."""
    s_ = sec_per_slot(day, sph)
    k = k_for(day, cfg.k_seconds, sph)
    x = s0 + k  # landing slot
    off = 1 if cfg.bound == "END" else 0
    je = int(np.searchsorted(slot, x + off, "left"))
    size, fee = cfg.size_lamports, cfg.fee_lamports
    qe, be = qpre[je], bpre[je]
    f = fee_ppm(qe, be)
    net = size * (1 - f / 1e6)
    tokens = be * net / (qe + net)
    mark = (qe + net) / (be - tokens)
    seed_p = (SEED_Q + v) / SEED_B
    spot_drift = (qe / be) / seed_p - 1
    exec_ratio = (net / tokens) / seed_p
    # pressure inputs: buys in the landing slot; buy lamports in the 2 s up to and including the landing slot
    signed_buy = np.where(isbuy, sol, 0.0)
    lo2 = int(np.searchsorted(slot, x - int(round(2.0 / s_)), "left"))
    hi2 = int(np.searchsorted(slot, x + 1, "left"))
    ix = int(np.searchsorted(slot, x, "left"))
    ssb = int(isbuy[ix:hi2].sum())
    nearby = float(signed_buy[lo2:hi2].sum())
    base = dict(k=k, landing_slot=int(x), spot_drift=float(spot_drift), exec_ratio=float(exec_ratio), ssb=ssb, nearby_lamports=nearby, size=size, fee=fee)
    if cfg.guard == "min_out" and exec_ratio > cfg.guard_ratio:
        return dict(base, status="guarded", exit_type="guard", hold_slots=0, pnl=-float(fee))
    d = x + int(round(cfg.cap_seconds / s_))
    icap = int(np.searchsorted(slot, d, "left"))
    hit = -1
    exit_type = "deadline"
    if icap > je:
        qa = qpre[je + 1 : icap + 1] + net
        ba = bpre[je + 1 : icap + 1] - tokens
        ret = (qa / ba) / mark - 1.0
        cond = (ret >= cfg.tp) | (ret <= -cfg.sl)
        i = int(np.argmax(cond))
        if cond[i]:
            hit = je + i
            exit_type = "tp" if ret[i] >= cfg.tp else "sl"
    if hit >= 0:
        fi = int(np.searchsorted(slot, slot[hit] + cfg.exit_lag + off, "left"))
        xs = int(slot[hit])
    else:
        fi = int(np.searchsorted(slot, d + cfg.exit_lag + off, "left"))
        xs = d
    qa_f, ba_f = qpre[fi] + net, bpre[fi] - tokens
    gross = tokens * qa_f / (ba_f + tokens)
    sellval = gross * (1 - fee_ppm(qa_f, ba_f) / 1e6)
    pnl = float(sellval) - size - 2 * fee
    return dict(base, status="filled", exit_type=exit_type, hold_slots=xs - int(x), pnl=pnl)


def apply_legs(rows: list[dict[str, Any]], cfg: Config) -> dict[str, Any]:
    """Fail legs on each attempt, in place. A guarded-out row is already -fee on every leg and is not mixed. Pressure intercept: `latency_curve.fit_curve`
    over the guard-passed sends of THIS run."""
    fills = [r for r in rows if r["status"] == "filled"]
    curve = fit_curve([Pressure(r["ssb"], int(r["nearby_lamports"])) for r in fills]) if fills else None
    for r in rows:
        pnl, fee = r["pnl"], float(r["fee"])
        r["pnl_nofail"] = pnl
        if r["status"] != "filled":
            r["p_press"] = 0.0
            r["pnl_live"] = r["pnl_flat"] = r["pnl_press"] = pnl
            continue
        pp = curve.p(Pressure(r["ssb"], int(r["nearby_lamports"])))
        r["p_press"] = pp
        r["pnl_live"] = (1 - cfg.live_fail) * pnl + cfg.live_fail * (-fee)
        r["pnl_flat"] = (1 - cfg.flat_fail) * pnl + cfg.flat_fail * (-fee)
        r["pnl_press"] = (1 - pp) * pnl + pp * (-fee)
    return {"pressure_intercept": None if curve is None else curve.intercept, "pressure_mean_p": (sum(r["p_press"] for r in fills) / len(fills)) if fills else None}


# --- statistics (G's analyze.py) -----------------------------------------------------------------------------------------------------


def stats(x_lamports: Sequence[float], days: Sequence[str], size: float) -> dict[str, Any]:
    """Mean per attempt as % of stake; gate trade-level CI90 (1,000 draws, seed 1, 5th pct); date-cluster CI90 (1,000 day resamples, seed 1);
    ex-top-3 SOL, ex-best-day SOL, days positive."""
    x = np.asarray(x_lamports, float) / 1e9
    n = len(x)
    if n == 0:
        return {"n": 0}
    rng = np.random.default_rng(1)
    idx = rng.integers(0, n, size=(1000, n))
    lo_t = np.percentile(x[idx].mean(1), 5)
    ud, inv = np.unique(np.asarray(days), return_inverse=True)
    dsum = np.bincount(inv, weights=x)
    dn = np.bincount(inv)
    rng2 = np.random.default_rng(1)
    di = rng2.integers(0, len(ud), size=(1000, len(ud)))
    lo_d = np.percentile(dsum[di].sum(1) / dn[di].sum(1), 5)
    s = np.sort(x)
    sz = size / 1e9
    return {
        "n": n, "days": int(len(ud)), "mean_pct": float(100 * x.mean() / sz), "ci_trade_lo_pct": float(100 * lo_t / sz), "ci_date_lo_pct": float(100 * lo_d / sz),
        "total_sol": float(x.sum()), "ex_top3_sol": float(x.sum() - s[-3:].sum()), "ex_best_day_sol": float(x.sum() - dsum.max()), "days_pos": f"{int((dsum > 0).sum())}/{len(ud)}",
    }


LEGS = ("live", "flat", "press", "nofail")


def book_stats(rows: Sequence[Mapping[str, Any]], size: float) -> dict[str, Any]:
    out: dict[str, Any] = {}
    scopes: list[tuple[str, tuple[str, ...] | None]] = [(b, (b,)) for b in (BLOCK_P2, BLOCK_P3, BLOCK_P4, BLOCK_P1A, BLOCK_P1C)]
    scopes += [(g, blocks) for g, blocks in GROUPS.items()] + [("all", None)]
    for name, blocks in scopes:
        sel = [r for r in rows if blocks is None or r["block"] in blocks]
        if not sel:
            continue
        days = [r["day"] + "|" + r["block"] for r in sel]
        cell: dict[str, Any] = {"fills": sum(1 for r in sel if r["status"] == "filled"), "guarded": sum(1 for r in sel if r["status"] == "guarded")}
        for leg in LEGS:
            cell[leg] = stats([r["pnl_" + leg] for r in sel], days, size)
        out[name] = cell
    return out


# --- hour files ----------------------------------------------------------------------------------------------------------------------

HOUR_RE = re.compile(r"^(trades|creates|migrations)-(\d{4}-\d{2}-\d{2}T\d{2})(\.deduped)?\.jsonl\.zst$")


@dataclass(frozen=True)
class Source:
    block: str
    dirs: tuple[Path, ...]


def expand_view_dirs(path: str | Path) -> list[Path]:
    """A view dir (has `trades/`) or a parent whose children are views (explore-0814/w1..w7, fresh-0903/w1..w3, exp011-0909/b,c)."""
    p = Path(path)
    check_path_allowed(p)
    if (p / "trades").is_dir():
        return [p]
    kids = sorted(c for c in p.iterdir() if c.is_dir() and (c / "trades").is_dir()) if p.is_dir() else []
    if not kids:
        raise Refused(f"{p} is not a view dir and has no view children")
    return kids


def build_sources(args: argparse.Namespace) -> list[Source]:
    """Order matters (first source wins a duplicated hour, as the audit's converter did): P2, P3, P4, P1A, P1C."""
    out: list[Source] = []
    spec: list[tuple[str, list[str]]] = [
        (BLOCK_P2, list(args.p2_view_dir or [])),
        (BLOCK_P3, [args.p3_root] if args.p3_root else []),
        (BLOCK_P4, list(args.p4_view_dir or [])),
        (BLOCK_P1A, [args.p1_fast_dir] if args.p1_fast_dir else []),
        (BLOCK_P1C, [args.p1_oracle_insample_dir] if args.p1_oracle_insample_dir else []),
    ]
    for block, paths in spec:
        dirs: list[Path] = []
        for p in paths:
            dirs += expand_view_dirs(p)
        if dirs:
            out.append(Source(block, tuple(dirs)))
    if not out:
        raise Refused("no source given (use --default-roots or the --p1/p2/p3/p4 flags)")
    return out


def index_hours(sources: Sequence[Source]) -> dict[str, dict[str, tuple[Path, str]]]:
    """kind -> hour -> (file, block). First file wins a duplicated hour; `trades-H.deduped.jsonl.zst` sorts before `trades-H.jsonl.zst`, as in the audit."""
    idx: dict[str, dict[str, tuple[Path, str]]] = {"trades": {}, "migrations": {}}
    for src in sources:
        for d in src.dirs:
            check_path_allowed(d)
            for kind in ("trades", "migrations"):
                kdir = d / kind
                if not kdir.is_dir():
                    continue
                for f in sorted(kdir.glob("*.jsonl.zst")):
                    m = HOUR_RE.match(f.name)
                    if not m or m.group(1) != kind:
                        continue
                    hour = m.group(2)
                    if src.block == BLOCK_P1C and hour > ORACLE_INSAMPLE_LAST_HOUR:
                        continue
                    check_hour_allowed(hour)
                    idx[kind].setdefault(hour, (f, src.block))
    return idx


def zcat_grep(path: Path, needle: str) -> Iterator[str]:
    """Lines of a zstd file containing `needle` (fixed string). Decompression and filtering happen in subprocesses, as `exp012_virtual_rescore._zcat_lines`."""
    p1 = subprocess.Popen(["zstdcat", str(path)], stdout=subprocess.PIPE)
    p2 = subprocess.Popen(["grep", "-a", "-F", needle], stdin=p1.stdout, stdout=subprocess.PIPE, text=True, errors="replace")
    assert p1.stdout is not None and p2.stdout is not None
    p1.stdout.close()
    try:
        yield from p2.stdout
    finally:
        p2.kill()
        p1.kill()
        p2.wait()
        p1.wait()


_SLOT_RE = re.compile(rb'"slot":(\d+)')


def tail_max_slot(path: Path, tail_bytes: int = 400_000) -> int | None:
    """Max slot in the last `tail_bytes` of a decompressed trade hour file (rows are time ordered; this is the file's max slot to within the tail)."""
    p1 = subprocess.Popen(["zstdcat", str(path)], stdout=subprocess.PIPE)
    p2 = subprocess.Popen(["tail", "-c", str(tail_bytes)], stdin=p1.stdout, stdout=subprocess.PIPE)
    assert p1.stdout is not None and p2.stdout is not None
    p1.stdout.close()
    data = p2.stdout.read()
    p2.wait()
    p1.wait()
    slots = [int(s) for s in _SLOT_RE.findall(data)]
    return max(slots) if slots else None


def order_key(slot: int, tx_index: int | None, event_index: int, seq: int) -> tuple[int, int, int, int]:
    """Within-slot order: (slot, tx_index, event_index); a row with a null tx_index (oracle-insample-0922) is ordered by file order (`seq`) alone, as the audit did.
    `seq` is the read sequence (hour-file order, then row order) and breaks every remaining tie, so the order is deterministic."""
    if tx_index is None:
        return (slot, 0, 0, seq)
    return (slot, tx_index, event_index, seq)


def _num(v: Any) -> float:
    return float(v) if v is not None else float("nan")


def read_day(day: str, idx: Mapping[str, Mapping[str, tuple[Path, str]]], vband: Mapping[str, int], cfg: Config, log: Any) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    """meta + ordered path arrays per uncensored canonical migration of one UTC day. Mirrors G's extract.py (hours of the day + the next two trade hours;
    `complete` events of the day's hours; PumpSwap prints of canonical V-band pools in [mslot, mslot + 7300))."""
    diag = {"hours": 0, "migrations": 0, "mints_with_canonical_pool": 0, "censored": 0, "multipool": 0, "bad_json": 0, "skipped_incomplete_migrations": 0}
    dh = sorted(h for h in idx["trades"] if h[:10] == day)
    if not dh:
        return {}, diag
    last = datetime.strptime(dh[-1], "%Y-%m-%dT%H")
    nxt = [(last + timedelta(hours=i)).strftime("%Y-%m-%dT%H") for i in (1, 2)]
    tf = dh + [h for h in nxt if h in idx["trades"]]
    if len([h for h in dh if h in idx["migrations"]]) < len(dh):
        diag["skipped_incomplete_migrations"] = 1
        return {}, diag
    diag["hours"] = len(dh)
    mig: dict[str, list[Any]] = {}  # mint -> [mslot, mbt, block]
    for h in dh:
        path, block = idx["migrations"][h]
        for line in zcat_grep(path, '"complete"'):
            try:
                r = json.loads(line)
            except ValueError:
                diag["bad_json"] += 1
                continue
            m = r.get("mint")
            if r.get("type") != "complete" or not isinstance(m, str) or not isinstance(r.get("slot"), int):
                continue
            cur = mig.get(m)
            bt = r.get("block_time")
            if cur is None:
                mig[m] = [r["slot"], bt, block]
            else:
                if r["slot"] < cur[0]:
                    cur[0], cur[2] = r["slot"], block
                if isinstance(bt, int) and (cur[1] is None or bt < cur[1]):
                    cur[1] = bt
    diag["migrations"] = len(mig)
    by_mint: dict[str, list[tuple]] = {}
    seq = 0
    for h in tf:
        path, _block = idx["trades"][h]
        t0 = time.time()
        for line in zcat_grep(path, '"pumpswap"'):
            i = line.find('"mint":"')
            if i < 0:
                continue
            m = line[i + 8 : line.find('"', i + 8)]
            mg = mig.get(m)
            if mg is None:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                diag["bad_json"] += 1
                continue
            if r.get("venue") != "pumpswap":
                continue
            pool = r.get("pool")
            if pool not in vband:
                continue
            slot = r.get("slot")
            if not isinstance(slot, int) or not (mg[0] <= slot < mg[0] + cfg.window_slots):
                continue
            seq += 1
            by_mint.setdefault(m, []).append((slot, r.get("tx_index"), r.get("event_index") or 0, seq, pool, r.get("side") == "buy",
                                              _num(r.get("sol_lamports")), _num(r.get("token_raw")), _num(r.get("quote_reserve")), _num(r.get("base_reserve"))))
        log(f"{day} {h} pumpswap rows kept so far={seq} ({time.time() - t0:.0f}s)")
    max_slot = max([s for s in (tail_max_slot(idx["trades"][h][0]) for h in tf[-2:]) if s is not None] or [0])
    out: dict[str, dict[str, Any]] = {}
    for m, rows in by_mint.items():
        s0_by_pool: dict[str, int] = {}
        for rr in rows:
            if rr[4] not in s0_by_pool or rr[0] < s0_by_pool[rr[4]]:
                s0_by_pool[rr[4]] = rr[0]
        pool = min(s0_by_pool, key=lambda p: (s0_by_pool[p], p))
        diag["mints_with_canonical_pool"] += 1
        diag["multipool"] += int(len(s0_by_pool) > 1)
        s0 = s0_by_pool[pool]
        mslot = mig[m][0]
        if not (s0 + UNCENSORED_HORIZON <= max_slot and s0 - mslot <= MAX_S0_GAP):
            diag["censored"] += 1
            continue
        path_rows = sorted((rr for rr in rows if rr[4] == pool), key=lambda rr: order_key(rr[0], rr[1], rr[2], rr[3]))
        out[m] = {"pool": pool, "v": float(vband[pool]), "s0": s0, "mslot": mslot, "block": mig[m][2], "rows": path_rows}
    return out, diag


def load_vband(path: str | Path, lo: int, hi: int) -> dict[str, int]:
    v = json.loads(Path(path).read_text())["v"]
    return {k: int(x) for k, x in v.items() if x is not None and lo <= x <= hi}


def run(args: argparse.Namespace, cfg: Config, log: Any) -> dict[str, Any]:
    cfg.validate()
    sources = build_sources(args)
    idx = index_hours(sources)
    vband = load_vband(args.vmap, cfg.v_lo, cfg.v_hi)
    sph = dict(SLOTS_PER_HOUR)
    if args.sph_json:
        sph = {k: float(x) for k, x in json.loads(Path(args.sph_json).read_text()).items()}
    days = sorted({h[:10] for h in idx["trades"]})
    if args.only_day:
        unknown = sorted(set(args.only_day) - set(days))
        if unknown:
            raise Refused(f"no trade hours for --only-day {unknown}")
        days = [d for d in days if d in args.only_day]
    picks = load_picks(args.picks) if args.picks else None
    rows: list[dict[str, Any]] = []
    tot = {"hours": 0, "migrations": 0, "mints_with_canonical_pool": 0, "censored": 0, "multipool": 0, "bad_json": 0, "skipped_incomplete_migrations": 0,
           "bad_reserves": 0, "final_state_fallback": 0}
    days_done: list[str] = []
    for day in days:
        meta, diag = read_day(day, idx, vband, cfg, log)
        for k_, v_ in diag.items():
            tot[k_] += v_
        if not meta:
            log(f"{day}: no attempts ({diag})")
            continue
        days_done.append(day)
        for m in sorted(meta):
            md = meta[m]
            cols = list(zip(*md["rows"]))
            slot = np.array(cols[0], dtype=np.int64)
            isbuy = np.array(cols[5], dtype=bool)
            sol = np.array(cols[6], dtype=float)
            tok = np.array(cols[7], dtype=float)
            q = np.array(cols[8], dtype=float)
            b = np.array(cols[9], dtype=float)
            st = build_states(slot, isbuy, sol, tok, q, b, md["v"], cfg.final_state)
            if st is None:
                tot["bad_reserves"] += 1
                continue
            tot["final_state_fallback"] += st[2]
            r = simulate_attempt(cfg, day=day, sph=sph, v=md["v"], s0=md["s0"], slot=slot, isbuy=isbuy, sol=sol, qpre=st[0], bpre=st[1])
            r.update(day=day, block=md["block"], mint=m, pool=md["pool"], v=int(md["v"]), mslot=md["mslot"], s0=md["s0"])
            if picks is None:
                r["pick"] = ""
            else:
                sc = picks.get(m)
                r["pick"] = "unscored" if sc is None else ("pick" if sc >= cfg.pick_threshold else "non_pick")
                r["score"] = "" if sc is None else repr(sc)
            rows.append(r)
        log(f"{day}: attempts so far {len(rows)}")
    leg_info = apply_legs(rows, cfg)
    size = float(cfg.size_lamports)
    summary: dict[str, Any] = {
        "schema": SCHEMA, "tool": TOOL, "phase": 1, "banner": "LAB SCORER. NOT A PROMOTE, NOT GATE EVIDENCE. Reproduction of the audit's P_primary; must not merge before owner O2/O3.",
        "config": asdict(cfg), "sources": {s.block: [str(d) for d in s.dirs] for s in sources}, "days": days_done,
        "vmap": {"path": str(args.vmap), "sha256": hashlib.sha256(Path(args.vmap).read_bytes()).hexdigest(), "pools_in_band": len(vband)},
        "picks": None if picks is None else {"path": str(args.picks), "sha256": hashlib.sha256(Path(args.picks).read_bytes()).hexdigest(), "scored": len(picks), "threshold": cfg.pick_threshold},
        "forbidden": {"path_parts": list(FORBIDDEN_PATH_PARTS), "exp009_hours": list(EXP009_HOURS), "cutoff_hour": CUTOFF_HOUR, "oracle_insample_last_hour": ORACLE_INSAMPLE_LAST_HOUR},
        "counts": {**tot, "attempts": len(rows), "fills": sum(1 for r in rows if r["status"] == "filled"), "guarded": sum(1 for r in rows if r["status"] == "guarded")},
        "fail_legs": {"live": cfg.live_fail, "flat": cfg.flat_fail, "pressure_target_mean_p": cfg.target_fail, **leg_info},
        "exit_types": {t: sum(1 for r in rows if r["exit_type"] == t) for t in ("tp", "sl", "deadline", "guard")},
        "books": {"all": book_stats(rows, size)},
    }
    if picks is not None:
        summary["books"]["picks"] = book_stats([r for r in rows if r["pick"] == "pick"], size)
        summary["books"]["non_picks"] = book_stats([r for r in rows if r["pick"] == "non_pick"], size)
    summary["_rows"] = rows
    return summary


def load_picks(path: str | Path) -> dict[str, float]:
    out: dict[str, float] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        rd = csv.DictReader(fh)
        if not rd.fieldnames or "mint" not in rd.fieldnames or "score" not in rd.fieldnames:
            raise Refused("--picks CSV needs a header with columns mint,score")
        for r in rd:
            out[r["mint"]] = float(r["score"])
    return out


ROW_COLUMNS = (
    "day", "block", "mint", "pool", "v", "mslot", "s0", "k", "landing_slot", "status", "exit_type", "hold_slots", "spot_drift", "exec_ratio", "ssb",
    "nearby_lamports", "size", "fee", "pnl_nofail", "pnl_live", "pnl_flat", "pnl_press", "p_press", "pick", "score",
)


def write_outputs(summary: dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = summary.pop("_rows")
    with open(out_dir / "rows.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=ROW_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in sorted(rows, key=lambda r: (r["day"], r["mint"])):
            w.writerow({c: (repr(r[c]) if isinstance(r.get(c), float) else r.get(c, "")) for c in ROW_COLUMNS})
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")


# --- CLI -----------------------------------------------------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--p1-fast-dir", default=None, help="P1A fast-pool view (block fast-pool-0918)")
    ap.add_argument("--p1-oracle-insample-dir", default=None, help="P1C oracle in-sample view (block oracle-insample-0922; hours stop at 09-25T06)")
    ap.add_argument("--p2-view-dir", action="append", default=None, help="P2 explore-0814 view dir, or its parent holding w1..w7")
    ap.add_argument("--p3-root", default=None, help="P3 fresh-0903 root holding w1..w3 (blocks-clean)")
    ap.add_argument("--p4-view-dir", action="append", default=None, help="P4 exp011-0909 view dir, or its parent holding b and c")
    ap.add_argument("--default-roots", action="store_true", help="fill every omitted source flag with its canonical exploration path")
    ap.add_argument("--vmap", default=DEFAULT_VMAP, help="pool -> V map (json with a `v` object)")
    ap.add_argument("--picks", default=None, help="CSV with header mint,score (EXP-012 scores); adds a pick column and pick-only books")
    ap.add_argument("--pick-threshold", type=float, default=PICK_THRESHOLD)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--only-day", action="append", default=None, help="UTC day YYYY-MM-DD (repeatable); default every day with trade hours")
    ap.add_argument("--sph-json", default=None, help="day -> slots per hour override (default: G's table)")
    d = Config()
    ap.add_argument("--k-seconds", type=float, default=d.k_seconds)
    ap.add_argument("--bound", choices=("END", "START"), default=d.bound)
    ap.add_argument("--exit-lag", type=int, default=d.exit_lag, help="slots")
    ap.add_argument("--guard", choices=("min_out", "none"), default=d.guard)
    ap.add_argument("--guard-ratio", type=float, default=d.guard_ratio)
    ap.add_argument("--cap-seconds", type=float, default=d.cap_seconds)
    ap.add_argument("--tp", type=float, default=d.tp)
    ap.add_argument("--sl", type=float, default=d.sl)
    ap.add_argument("--size-lamports", type=int, default=d.size_lamports)
    ap.add_argument("--fee-lamports", type=int, default=d.fee_lamports)
    ap.add_argument("--live-fail", type=float, default=d.live_fail)
    ap.add_argument("--flat-fail", type=float, default=d.flat_fail)
    ap.add_argument("--final-state", choices=("lab", "g"), default=d.final_state, help="state after the last print: lab = pumpswap_post_trade_reserves; g = the audit's 1.25%% constant")
    return ap


def config_from_args(a: argparse.Namespace) -> Config:
    return Config(
        k_seconds=a.k_seconds, bound=a.bound, exit_lag=a.exit_lag, guard=a.guard, guard_ratio=a.guard_ratio, cap_seconds=a.cap_seconds, tp=a.tp, sl=a.sl,
        size_lamports=a.size_lamports, fee_lamports=a.fee_lamports, live_fail=a.live_fail, flat_fail=a.flat_fail, final_state=a.final_state, pick_threshold=a.pick_threshold,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.default_roots:
        for k_, v_ in DEFAULT_ROOTS.items():
            if not getattr(args, k_):
                setattr(args, k_, v_)

    def log(msg: str) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)

    try:
        summary = run(args, config_from_args(args), log)
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    write_outputs(summary, args.out_dir)
    c = summary["counts"]
    log(f"done: attempts={c['attempts']} fills={c['fills']} guarded={c['guarded']} out={args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
