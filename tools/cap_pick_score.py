#!/usr/bin/env python3
"""CAP-PICK scorer, phase 2 (G's `P_primary` plus the judge's spec switches). LAB SCORER. NOT A PROMOTE, NOT GATE EVIDENCE.

Port of the audit simulator `g_reachable_cap_book_rescore/sim.py` (audit 2026-10-08, G) onto the lab's own block layout. The audit read an
audit-only Parquet copy of the tape; this tool reads the lab's `trades/`, `migrations/` hour files (`*.jsonl.zst`) directly, one UTC day at a time.
It must not merge before the owner decides O2/O3. Phase 1 reproduced G's `P_primary` (job #386: 24,272 attempts, P2-P4 exact to the lamport). Phase 2
adds the CAP-PICK judge's spec items (`capv_JUDGE.md` section 4) as switches. EVERY SWITCH DEFAULTS TO G's `P_primary`, so a run with no new flag is the
phase-1 book.

Book (every parameter is a `Config` field and a CLI flag; the defaults are G's `P_primary`)
  Universe  every canonical PumpSwap migration pool with V in [17.5e9, 17.7e9] (`pool_v_0909.json`), uncensored, one attempt per mint.
  Entry     landing slot X = s0 + k, END bound (state after every print in slot X). s0 = the first PumpSwap print of the canonical pool (the pool-create
            slot; the anchor is NOT the migrate slot, and `s0_minus_mslot` is recorded per attempt); mslot = the `complete` slot.
            k = entry latency (--k-seconds 1.3, or --entry-latency-ms) / seconds-per-slot, rounded (--k-rounding round|ceil), with seconds-per-slot from
            --k-mode day (G: that UTC day's table, `SLOTS_PER_HOUR`) or --k-mode hour (the attempt's UTC hour measured on the tape, see below).
  Guard     min_out at seed x 1.15 (see GUARD FORMULA). A guarded-out buy is a failed tx that pays ONE send fee.
  Exit      tp50 / sl30 on post-trade spot against the post-buy mark. 300 s cap (see CAP ANCHOR). Exit lag on the END bound: fill = state before the first
            print with slot >= trigger + lag + 1; the deadline sell lands at the deadline + lag. lag = --exit-lag slots (G: 2), or --exit-lag-ms.
  Size/fee  0.5 SOL, 55,000 lamports per send; the pool fee tier is `tools.paper_curve_math.PUMPSWAP_SOL_FEE_TIERS` on (quote + V, base).
  Fail legs live 1/62 (report-only) and flat 15% (a fill pays (1-p) pnl + p (-fee)); pressure = `tools.latency_curve.fit_curve` (slopes at scale 1, intercept
            so the mean p over this run's guard-passed sends is 0.289). A guarded-out row is -fee on every leg.

PER-HOUR MS/SLOT (--k-mode hour, --exit-lag-ms)
  An attempt's hour is the UTC hour file that holds its `complete` event. That hour's slots-per-hour is MEASURED on the tape: the lowest slot among the
  first 2,000 rows and the highest slot among the last 400 kB of the hour's trade file, each with its own `block_time`:
      slots_per_hour = (slot_hi - slot_lo) / (block_time_hi - block_time_lo) x 3600        (needs >= 1,800 s of block_time between them)
  ms/slot = 3.6e6 / slots_per_hour. An hour that cannot be measured falls back to the day table (counted in `counts.hour_sph_fallback`, and listed in
  `hour_sph` in summary.json) and is refused if the day has no table either. --hour-sph-json (hour -> slots per hour) overrides the measurement.
  Mapping ms -> slots. Entry: x = latency_s / (ms_per_slot / 1000); k = round(x) (Python round, half to even, G's rule) or ceil(x - 1e-9)
  (--k-rounding). 1.3 s is 6.5 slots at 200 ms: round -> 6, ceil -> 7. Exit lag: ALWAYS ceil(lag_ms / ms_per_slot - 1e-9), with the attempt's hour.
  The 2 s pressure look-back uses the same seconds-per-slot as k. The day-mean cap (G) always uses the day table.

GUARD FORMULA (--guard-basis net|gross)
  seed price  seed_p = (67,405,853,863 + V0) / 206.9e12, V0 = the V-map value of the canonical pool (a pool-account read at decision time in the live system).
  landing state (Q incl. V, B); pool fee tier f from (Q, B); net_in = size x (1 - f); tokens_out = B x net_in / (Q + net_in).
  net   (G, default): the buy executes iff  net_in / tokens_out <= ratio x seed_p            (ratio 1.15; no integer rounding; this is G's sim.py).
  gross (as the executor sends it): min_out = ceil(size / (ratio x seed_p)) base units; the buy executes iff floor(tokens_out) >= min_out. size is the
        SOL input including the pool fee, so this is the same test as  size / floor(tokens_out) <= ratio x seed_p  and is stricter than `net` by 1/(1-f).
  A pool already above seed x ratio at landing (a synthetic-migration pool) is a reject in either basis. `exec_ratio` (net) and `exec_ratio_gross` are
  both recorded per attempt. A guarded-out buy is a failed tx and pays ONE send fee (-fee on every leg), in both bases.
  Caveat: `gross` is the PROPOSED executor rule. `tools/probe_executor.py` today takes min_out from the fresh quote (judge item 6), so the replacement needs
  an md5 decision-equivalence replay against this rule before any live use. The tool prices min_out with V0 from the V map and never re-reads the pool.

CAP ANCHOR (--cap-anchor day-mean|block-time)
  day-mean (G): deadline slot D = X + round(300 / day seconds-per-slot).
  block-time: the deadline is the first print whose block_time >= block_time(landing) + 300 s, where block_time(landing) is that of the last print at slot
  <= X (prints are the only slot clock on the tape). D = the slot of that print; triggers are scanned over prints before it; the deadline sell fills at the
  first print with slot >= D + lag + 1. If no print reaches the deadline the fill is the state after the last print. A block_time that is null on the
  landing print (or all prints) falls back to day-mean for that attempt (`cap_anchor_used` per row: day-mean | block-time | day-mean-fallback, empty on a
  guarded-out row, which has no deadline; `counts.cap_bt_fallback` counts the fallbacks). The exit lag applies to the deadline sell as well as to a tp / sl sell.

RENT (--rent-lamports, --rent-mode none|always)
  none (G, default): no rent. always: rent_lamports is charged on every FILLED trip, before the fail mix (a stress leg: the real executor refunds the
  token-account rent when the sell closes the account; the lab constant is 2,039,280). A guarded-out buy creates no account and pays no rent.
  The two flags must agree (mode always needs lamports > 0; lamports > 0 needs mode always), so a forgotten flag cannot silently charge nothing.

GATE STATISTICS (`books.<scope>.gate.<leg>` in summary.json; flat and press are the binding legs, live and nofail are report-only)
  Per scope (each block, P1, P2-P4, all; and `picks` / `non_picks` when --picks is given) and leg: n attempts, n fills, mean per attempt and per fill (% of
  the stake and SOL), trade-level CI90 lower bound (attempts; 1,000 bootstrap draws, seed 1, 5th percentile: the promotion gate), date-cluster CI90 lower bound
  (1,000 date resamples, seed 1; a cluster is the UTC date alone), total SOL ex the top 3 attempts, total SOL ex the best date, dates positive / dates, the
  date-level one-sided t p (`p_date_t`; t = mean of date means / (sd / sqrt(W)), W - 1 df), and the trade-level one-sided bootstrap p (`p_trade_boot`).
  `p_trade_boot` is REPORT-ONLY. It is the share of --boot-p-draws bootstrap means <= 0 (default 10,000, seed 1: DEC-021 section 5), its own draws, not the
  CI90's 1,000; the first 1,000 are the same resamples. The deciding p is the day-level t (`p_date_t`). --boot-p-draws touches only `p_trade_boot`: `rows.csv`
  and every other statistic are unchanged by it. summary.json `bootstrap` records the draw counts.

PICKS (--picks, --book all|picks)
  --picks takes the EXP-012 score CSV (header mint,score; a pick is score >= --pick-threshold) or a JSONL of live-gate decisions (one object per mint with
  `mint` and `decision`; `decision == "pick"` is a pick, any other decision is a non-pick; lines without `mint` are skipped; the replay's `kind: "decision"` rows
  win over `kind: "dead"` rows). --book all (G, default) scores every attempt and reports the pick subset beside it; --book picks keeps ONLY the picks as
  attempts (unscored and non-pick mints are not attempts), refits the pressure intercept on the picks' own fills, and reports `books.picks`.

RESERVE CONVENTIONS (this broke the audit once)
  * PumpSwap tape rows carry PRE-trade reserves. Bonding-curve rows carry POST-trade reserves (this scorer never prices a bonding row).
  * PumpSwap price = (quote vault + V) / base. V is added to the quote reserve and nothing else.
  * The state after print i is the PRE-trade reserves of print i+1 (the observed chain). Only the state after the LAST print is derived, and that one uses
    the lab's `tools.paper_price_path.pumpswap_post_trade_reserves` (`--final-state lab`, default). `--final-state g` is the audit's constant 1.25% approximation,
    kept so the reproduction can separate that one difference.
  * Within-slot order: (slot, tx_index, event_index). A row with a null tx_index (oracle-insample-0922) is ordered by file order instead.

T2: REPORT-ONLY BOOST-PROGRESS EXIT (--exit-mode, --boost-cut, --paired; ARTIFACTS/lab/cap-pick-t2-boost-exit-predeclare-2026-10-08.md)
  --exit-mode cap (default) is the book above, byte for byte. --exit-mode boost90 adds rule B90: exit at the first print where one wallet's cumulative buys on this
  pool reach 0.9 x 17.585 SOL (G's causal detector on the tape's `trader` field: no sell so far, every buy <= 2 SOL, >= 8 buys, prints with slot <= s0 + 2,500), or
  at the same 300 s cap, whichever comes first; a tp / sl at the same print or earlier wins. The keeper address HTVZVEQ... is NOT on the tape (it signs the crank; the
  swap's account is a per-pool PDA), so the detector is behavioural. --paired simulates the other exit mode on the same path and adds `summary.paired`: B90 minus cap
  per attempt in pp of stake, with the date-cluster CI90 (1,000 UTC-date resamples, seed 1), per scope and leg. --boost-cut f (0 < f <= 1) is a STRESS REPLAY: the
  detected keeper's buys are kept while its cumulative buy SOL <= f x 17.585 SOL and the later ones are removed; the constant-product path is re-simulated from the
  remaining prints' amounts (buys keep SOL in, sells keep tokens in; PRE-trade convention, V on the quote) from the first removed print on. Approximations are in
  `cut_boost_path`. For f < 0.9 B90 cannot fire on a cut path (declared in the pre-declaration). None of this is gate evidence and none of it touches EXP-022.

Hard limits (asserted; see `check_path_allowed` / `check_inputs_allowed` / `check_hour_allowed`): exploration pools only. Never fresh-0802, fresh-0808, fresh-0828,
any forward or oracle-live path, the EXP-009 hours [2026-09-15T12, 2026-09-18T23), or any hour at or after 2026-10-02T10. Oracle in-sample hours stop at 2026-09-25T06.
INPUT PATHS (--picks, --hour-sph-json, --sph-json, --vmap, and every tape source dir) are checked first in `run()`, before anything is opened, listed or read. A path
is refused if it, its normalised form or its symlink-resolved form contains (case-insensitive) fresh-0802, fresh-0808, fresh-0828, forward (so also forward-paper and
exp012-forward), oracle-live, arm-audit, exp012-gate, positions, heartbeat or runner, or starts with /data/mal/exp012-forward or /var/lib/mal-live. The legitimate
inputs are the V map (/data/mal/pumpswap-virtual/pool_v_0909.json), the audit's /data/mal/audit-1008/... files and the gate-replay outputs under /data/mal/cap-pick-score/.
No network, no key, no Helius call. Output: `rows.csv` (one row per attempt) and `summary.json`.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
from array import array
from dataclasses import asdict, dataclass, replace
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
BOOT_P_DRAWS = 10_000  # DEC-021 section 5: the trade-level bootstrap p (10,000 draws, seed 1). Report-only; the CI90 keeps the gate's 1,000 draws
PICK_THRESHOLD = 0.8030766588450794  # EXP-012 frozen threshold (G's strata.py)
BOOST_BUDGET_LAMPORTS = 17_585_000_000  # pump.fun's BOOST buyer spends 17.585 SOL per pool (pre-declaration section 1; a constant, not a flag)
B90_FRAC = 0.9  # rule B90 fires when one wallet's cumulative buys reach this share of the budget (pinned; not a flag)
BOOST_WINDOW_SLOTS = 2500  # the detector looks at prints with slot <= s0 + 2,500 (G's sim.py)
BOOST_MAX_BUY_LAMPORTS = 2_000_000_000  # G: every buy of the BOOST wallet so far is <= 2 SOL
BOOST_MIN_BUYS = 8  # G: at least 8 buys
EXIT_MODES = ("cap", "boost90")
DEFAULT_VMAP = "/data/mal/pumpswap-virtual/pool_v_0909.json"
SLOT_TOL = 1e-9  # ceil(x - SLOT_TOL): a float that is an integer to 1e-9 slot is that integer
HOUR_HEAD_LINES = 2000  # per-hour ms/slot measurement: lowest slot among the first rows ...
HOUR_TAIL_BYTES = 400_000  # ... and highest slot among the last bytes of the hour file
HOUR_MIN_SPAN_S = 1800  # the two sample points must be this many block_time seconds apart
HOUR_SPH_RANGE = (6000.0, 20000.0)  # a measured slots-per-hour outside this is not a slot clock (the lab sees 8,650 to 13,600; 200 ms slots are 18,000)

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
# A path is refused when ANY of these appears in it (case-insensitive substring, on the path as given, normalised, and with symlinks resolved).
FORBIDDEN_PATH_PARTS = (
    "fresh-0802", "fresh-0808", "fresh-0828",  # sealed blocks
    "forward", "oracle-live",  # forward walks (includes exp012-forward and forward-paper) and the live oracle tape
    "forward-paper", "arm-audit", "exp012-gate", "positions", "heartbeat", "runner",  # forward-paper / runner / gate-runner files
)
# ... or when it starts with one of these (normalised, and with symlinks resolved).
FORBIDDEN_PATH_PREFIXES = ("/data/mal/exp012-forward", "/var/lib/mal-live")
# The input files the scorer reads, as `run()` receives them. The legitimate ones are the V map (/data/mal/pumpswap-virtual/pool_v_0909.json), the audit's
# /data/mal/audit-1008/... files and the gate-replay outputs under /data/mal/cap-pick-score/.
INPUT_FILE_ARGS = ("picks", "hour_sph_json", "sph_json", "vmap")
INPUT_DIR_ARGS = ("p1_fast_dir", "p1_oracle_insample_dir", "p3_root")
INPUT_DIR_LIST_ARGS = ("p2_view_dir", "p4_view_dir")
EXP009_HOURS = ("2026-09-15T12", "2026-09-18T23")  # [lo, hi)
CUTOFF_HOUR = "2026-10-02T10"  # nothing at or after this
ORACLE_INSAMPLE_LAST_HOUR = "2026-09-25T06"  # later oracle hours belong to the live-tape row


class Refused(Exception):
    """The tool will not run: a forbidden path, hour or argument."""


def _path_forms(path: str | Path) -> list[str]:
    """The path as given, normalised, and with symlinks resolved (lower case). Only `lstat` / `readlink` run here: nothing is opened or read.
    A relative path that resolves under the working directory is tested relative to it, so the name of the caller's own checkout cannot refuse it."""
    s = os.fspath(path)
    real = os.path.realpath(s)
    cwd = os.path.realpath(os.getcwd())
    if not os.path.isabs(s) and (real == cwd or real.startswith(cwd + os.sep)):
        real = os.path.relpath(real, cwd)
    return [f.lower() for f in (s, os.path.normpath(s), real)]


def check_path_allowed(path: str | Path) -> None:
    s = os.fspath(path)
    forms = _path_forms(s)
    for part in FORBIDDEN_PATH_PARTS:
        if any(part in f for f in forms):
            raise Refused(f"forbidden path ({part}): {s}")
    for prefix in FORBIDDEN_PATH_PREFIXES:
        if any(f.startswith(prefix) for f in forms):
            raise Refused(f"forbidden path (under {prefix}): {s}")


def check_inputs_allowed(args: argparse.Namespace) -> None:
    """Every input path of a run, checked BEFORE anything is opened: the pick set, the two slots-per-hour overrides, the V map and the tape source dirs."""
    for name in INPUT_FILE_ARGS + INPUT_DIR_ARGS:
        v = getattr(args, name, None)
        if v:
            check_path_allowed(v)
    for name in INPUT_DIR_LIST_ARGS:
        for v in getattr(args, name, None) or []:
            check_path_allowed(v)


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
    k_mode: str = "day"  # "day" (G: the SPH day table) | "hour" (the attempt's UTC hour measured on the tape)
    k_rounding: str = "round"  # "round" (G: Python round) | "ceil"
    exit_lag_ms: float | None = None  # None = exit_lag slots (G); else ceil(ms / hour ms-per-slot)
    guard_basis: str = "net"  # "net" (G) | "gross" (as the executor sends min_out)
    cap_anchor: str = "day-mean"  # "day-mean" (G) | "block-time"
    rent_lamports: int = 0
    rent_mode: str = "none"  # "none" (G) | "always" (stress leg)
    book: str = "all"  # "all" (G) | "picks"
    v_lo: int = V_LO
    v_hi: int = V_HI
    window_slots: int = WINDOW_SLOTS
    pick_threshold: float = PICK_THRESHOLD
    boot_p_draws: int = BOOT_P_DRAWS  # report-only p_trade_boot; the CI90 draws stay at BOOT_DRAWS (the gate)
    exit_mode: str = "cap"  # "cap" (G: the 300 s cap) | "boost90" (T2 rule B90, report-only)
    boost_cut: float | None = None  # T2 stress replay: keep the keeper's buys up to this share of the BOOST budget; None = the tape as it is
    paired: bool = False  # T2: also simulate the other exit mode on the same path and report B90 minus cap

    def validate(self) -> None:
        if self.bound not in ("END", "START"):
            raise Refused(f"bound must be END or START, got {self.bound}")
        if self.guard not in ("min_out", "none"):
            raise Refused(f"guard must be min_out or none, got {self.guard}")
        if self.final_state not in ("lab", "g"):
            raise Refused(f"final-state must be lab or g, got {self.final_state}")
        if self.k_seconds <= 0 or self.cap_seconds <= 0 or self.exit_lag < 0 or self.size_lamports <= 0 or self.fee_lamports < 0:
            raise Refused("k, cap, size must be positive and lag, fee non-negative")
        for name, val, ok in (("k-mode", self.k_mode, ("day", "hour")), ("k-rounding", self.k_rounding, ("round", "ceil")),
                              ("guard-basis", self.guard_basis, ("net", "gross")), ("cap-anchor", self.cap_anchor, ("day-mean", "block-time")),
                              ("rent-mode", self.rent_mode, ("none", "always")), ("book", self.book, ("all", "picks"))):
            if val not in ok:
                raise Refused(f"{name} must be one of {ok}, got {val}")
        if self.exit_lag_ms is not None and not self.exit_lag_ms >= 0:
            raise Refused("exit-lag-ms must be non-negative")
        if self.rent_lamports < 0:
            raise Refused("rent-lamports must be non-negative")
        if isinstance(self.boot_p_draws, bool) or not isinstance(self.boot_p_draws, int) or self.boot_p_draws < 1:
            raise Refused(f"boot-p-draws must be a positive integer, got {self.boot_p_draws!r}")
        if self.exit_mode not in EXIT_MODES:
            raise Refused(f"exit-mode must be one of {EXIT_MODES}, got {self.exit_mode}")
        if self.boost_cut is not None and not (isinstance(self.boost_cut, (int, float)) and not isinstance(self.boost_cut, bool) and 0.0 < self.boost_cut <= 1.0):
            raise Refused(f"boost-cut must be in (0, 1], got {self.boost_cut!r}")
        if (self.rent_mode == "always") != (self.rent_lamports > 0):  # a rent amount without the mode (or the mode without an amount) would silently charge nothing
            raise Refused("--rent-mode always needs --rent-lamports > 0, and --rent-lamports > 0 needs --rent-mode always (default: none, 0)")

    @property
    def needs_trader(self) -> bool:
        """The T2 switches read the tape's `trader` field. With none of them set the field is not read at all, so the default run is the phase-1 run."""
        return self.exit_mode != "cap" or self.boost_cut is not None or self.paired

    @property
    def needs_hour_sph(self) -> bool:
        return self.k_mode == "hour" or self.exit_lag_ms is not None


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


def slots_for(seconds: float, sec_slot: float, rounding: str = "round") -> int:
    """Seconds -> whole slots at `sec_slot` seconds per slot. round = Python round (half to even), G's rule; ceil = smallest integer >= x (x - 1e-9, so
    1.2 s at 400 ms is 3, not 4)."""
    x = seconds / sec_slot
    if rounding == "ceil":
        return max(0, math.ceil(x - SLOT_TOL))
    if rounding == "round":
        return int(round(x))
    raise Refused(f"rounding must be round or ceil, got {rounding}")


def hour_sph_from_points(slot_lo: int, bt_lo: int, slot_hi: int, bt_hi: int) -> float | None:
    """Slots per hour from two (slot, block_time) points of one hour file; None if the points are too close in block_time to say."""
    if bt_hi - bt_lo < HOUR_MIN_SPAN_S or slot_hi <= slot_lo:
        return None
    sph = (slot_hi - slot_lo) / (bt_hi - bt_lo) * 3600.0
    return sph if HOUR_SPH_RANGE[0] <= sph <= HOUR_SPH_RANGE[1] else None


_ROW_SLOT_RE = re.compile(rb'"slot":(\d+)')
_ROW_BT_RE = re.compile(rb'"block_time":(\d+)')


def _slot_bt(line: bytes) -> tuple[int, int] | None:
    ms, mb = _ROW_SLOT_RE.search(line), _ROW_BT_RE.search(line)
    return (int(ms.group(1)), int(mb.group(1))) if ms and mb else None


def measure_hour_sph(path: Path) -> float | None:
    """One UTC hour file's slots-per-hour on the tape: (lowest slot among the first HOUR_HEAD_LINES rows, highest slot among the last HOUR_TAIL_BYTES bytes),
    each with its own block_time (the span is taken from block_time, so it does not need the file to start and end on the hour). None if unmeasurable."""
    p1 = subprocess.Popen(["zstdcat", str(path)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    p2 = subprocess.Popen(["head", "-n", str(HOUR_HEAD_LINES)], stdin=p1.stdout, stdout=subprocess.PIPE)
    assert p1.stdout is not None and p2.stdout is not None
    p1.stdout.close()
    head = p2.stdout.read()
    p2.wait()
    p1.wait()
    p3 = subprocess.Popen(["zstdcat", str(path)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    p4 = subprocess.Popen(["tail", "-c", str(HOUR_TAIL_BYTES)], stdin=p3.stdout, stdout=subprocess.PIPE)
    assert p3.stdout is not None and p4.stdout is not None
    p3.stdout.close()
    tail = p4.stdout.read()
    p4.wait()
    p3.wait()
    lo = [pt for pt in map(_slot_bt, head.split(b"\n")) if pt is not None]
    hi = [pt for pt in map(_slot_bt, tail.split(b"\n")[1:]) if pt is not None]  # [0] may be cut mid-row
    if not lo or not hi:
        return None
    (slot_lo, bt_lo), (slot_hi, bt_hi) = min(lo), max(hi)
    return hour_sph_from_points(slot_lo, bt_lo, slot_hi, bt_hi)


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


def bt_deadline_index(slot: np.ndarray, bt: np.ndarray | None, x: int, cap_seconds: float) -> int | None:
    """Index of the first print whose block_time >= block_time(landing) + cap_seconds, where block_time(landing) is that of the last print at slot <= X.
    A null block_time is -1 and is carried forward from the earlier prints (running maximum), so the series is non-decreasing. Returns len(slot) when no
    print reaches the deadline, and None when the landing block_time is unusable (no print at or before X, or no valid block_time yet)."""
    if bt is None:
        return None
    il = int(np.searchsorted(slot, x, "right")) - 1
    if il < 0:
        return None
    run = np.maximum.accumulate(bt)
    if run[il] <= 0:
        return None
    return int(np.searchsorted(run, run[il] + cap_seconds, "left"))


def trader_id(trader: Any) -> int:
    """Stable 64-bit id of a trader string (blake2b); 0 means 'no trader' and is never a wallet."""
    if not isinstance(trader, str) or not trader:
        return 0
    return int.from_bytes(hashlib.blake2b(trader.encode(), digest_size=8).digest(), "little", signed=True) or 1


def boost_scan(trader: np.ndarray, isbuy: np.ndarray, sol: np.ndarray, slot: np.ndarray, s0: int, frac: float = B90_FRAC) -> tuple[int, int] | None:
    """G's causal BOOST-progress detector (`sim.py` `boost_cross`). Over the prints with slot <= s0 + 2,500, a wallet qualifies at a print of its own when, counting
    that print: its cumulative buy lamports >= frac x 17.585 SOL, it has made no sell, every buy so far is <= 2 SOL, and it has made >= 8 buys. Returns
    (index of the first print, in path order, at which any wallet qualifies, that wallet's id), or None. Only prints up to the trigger are needed to decide it, so it
    is a causal rule even though this function reads the whole path."""
    m = int(np.searchsorted(slot, s0 + BOOST_WINDOW_SLOTS, "right"))
    if m == 0:
        return None
    tr, ib, amt = trader[:m], isbuy[:m].astype(bool), sol[:m]
    thr = frac * BOOST_BUDGET_LAMPORTS
    bm = ib & (tr != 0)
    if not bm.any():
        return None
    wallets, inv = np.unique(tr[bm], return_inverse=True)
    tot, cnt = np.bincount(inv, weights=amt[bm]), np.bincount(inv)
    best: tuple[int, int] | None = None
    for wid in wallets[(tot >= thr) & (cnt >= BOOST_MIN_BUYS)]:
        idx = np.flatnonzero(tr == wid)
        b = ib[idx]
        a = np.where(b, amt[idx], 0.0)
        ok = (np.cumsum(a) >= thr) & (np.cumsum(~b) == 0) & (np.maximum.accumulate(a) <= BOOST_MAX_BUY_LAMPORTS) & (np.cumsum(b) >= BOOST_MIN_BUYS)
        if ok.any():
            first = int(idx[int(np.argmax(ok))])
            if best is None or first < best[0]:
                best = (first, int(wid))
    return best


def cut_boost_path(f: float, *, trader: np.ndarray, slot: np.ndarray, isbuy: np.ndarray, sol: np.ndarray, tok: np.ndarray, qpre: np.ndarray, bpre: np.ndarray,
                   bt: np.ndarray, s0: int) -> dict[str, Any]:
    """T2 stress replay (--boost-cut f). The keeper is the wallet `boost_scan` finds on the ORIGINAL path (the whole path is used: this builds a counterfactual and is
    not a trading rule). Its buys are kept while its cumulative buy lamports, including that buy, are <= f x 17.585 SOL; every later buy of it is removed. Every other print
    is kept at its slot and in its order. The state before the first removed print is the tape's; from there each kept print is applied in order to the running state
    (X = quote + V, B = base; `qpre` / `bpre` already carry V), with the fee tier from `fee_ppm(X, B)` as for our own buy:
        buy  keeps its SOL S in:    net = S x (1 - fee); tokens = B x net / (X + net); X += net; B -= tokens           (the whole fee leaves the pool, as
                                                                                                                           `pumpswap_pool_quote_delta` does on tape rows)
        sell keeps its tokens T in: gross = X x T / (B + T); SOL out = gross x (1 - fee); X -= gross; B += T
    Both leave X x B unchanged. APPROXIMATIONS: other traders do not react to the missing flow; the LP share of a real fee that stays in the pool is ignored; no integer
    rounding or slippage limit; after the first removal the path drifts from what the chain would have done by an unknown amount. A mint with no detected keeper, or
    with nothing past the cut, is returned unchanged (same arrays)."""
    out: dict[str, Any] = dict(trader=trader, slot=slot, isbuy=isbuy, sol=sol, tok=tok, qpre=qpre, bpre=bpre, bt=bt, dropped=0, keeper=0)
    hit = boost_scan(trader, isbuy, sol, slot, s0)
    if hit is None:
        return out
    wid = hit[1]
    out["keeper"] = wid
    kb = np.flatnonzero((trader == wid) & isbuy.astype(bool))
    drop_idx = kb[np.cumsum(sol[kb]) > f * BOOST_BUDGET_LAMPORTS]
    if drop_idx.size == 0:
        return out
    n = len(slot)
    drop = np.zeros(n, bool)
    drop[drop_idx] = True
    i0 = int(drop_idx[0])
    kept = np.flatnonzero(~drop)
    sol2, tok2 = sol[kept].astype(float).copy(), tok[kept].astype(float).copy()
    q2, b2 = np.empty(len(kept) + 1), np.empty(len(kept) + 1)
    x, b = float(qpre[i0]), float(bpre[i0])
    for pos, j in enumerate(kept):
        if j < i0:  # before the first removal the observed state stands
            q2[pos], b2[pos] = qpre[j], bpre[j]
            continue
        q2[pos], b2[pos] = x, b
        fee = float(fee_ppm(x, b)) / 1e6
        if isbuy[j]:
            net = sol2[pos] * (1 - fee)
            t = b * net / (x + net)
            tok2[pos] = t
            x, b = x + net, b - t
        else:
            gross = x * tok2[pos] / (b + tok2[pos])
            sol2[pos] = gross * (1 - fee)
            x, b = x - gross, b + tok2[pos]
    q2[-1], b2[-1] = x, b
    out.update(trader=trader[kept], slot=slot[kept], isbuy=isbuy[kept], sol=sol2, tok=tok2, qpre=q2, bpre=b2, bt=bt[kept], dropped=int(drop_idx.size))
    return out


def simulate_attempt(
    cfg: Config, *, day: str, sph: Mapping[str, float], v: float, s0: int, slot: np.ndarray, isbuy: np.ndarray, sol: np.ndarray, qpre: np.ndarray, bpre: np.ndarray,
    hour: str | None = None, hour_s: float | None = None, bt: np.ndarray | None = None, boost_idx: int | None = None,
) -> dict[str, Any]:
    """One attempt on one mint's ordered path. Mirrors G's `run_day` inner loop for `tpsl_wall` exits and the `min_out` / `none` guards.
    `hour_s` = seconds per slot of the attempt's UTC hour (needed by --k-mode hour and --exit-lag-ms); `bt` = block_time per print, -1 if null (needed by
    --cap-anchor block-time). `boost_idx` = index of the print at which `boost_scan` fires on THIS path (T2; used only when cfg.exit_mode == "boost90")."""
    s_day = sec_per_slot(day, sph) if (cfg.k_mode == "day" or cfg.cap_anchor == "day-mean") else None
    if (cfg.k_mode == "hour" or cfg.exit_lag_ms is not None) and hour_s is None:
        raise Refused(f"no measured seconds-per-slot for the hour of this {day} attempt (hour={hour})")
    s_ = s_day if cfg.k_mode == "day" else hour_s
    assert s_ is not None
    k = slots_for(cfg.k_seconds, s_, cfg.k_rounding)
    lag = cfg.exit_lag if cfg.exit_lag_ms is None else max(0, math.ceil(cfg.exit_lag_ms / 1000.0 / hour_s - SLOT_TOL))  # type: ignore[operator]
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
    exec_ratio_gross = (size / tokens) / seed_p
    # pressure inputs: buys in the landing slot; buy lamports in the 2 s up to and including the landing slot
    signed_buy = np.where(isbuy, sol, 0.0)
    lo2 = int(np.searchsorted(slot, x - int(round(2.0 / s_)), "left"))
    hi2 = int(np.searchsorted(slot, x + 1, "left"))
    ix = int(np.searchsorted(slot, x, "left"))
    ssb = int(isbuy[ix:hi2].sum())
    nearby = float(signed_buy[lo2:hi2].sum())
    base = dict(k=k, landing_slot=int(x), spot_drift=float(spot_drift), exec_ratio=float(exec_ratio), exec_ratio_gross=float(exec_ratio_gross), ssb=ssb,
                nearby_lamports=nearby, size=size, fee=fee, exit_lag_slots=lag, cap_anchor_used="", rent=0)
    if cfg.guard == "min_out":
        if cfg.guard_basis == "net":
            rejected = exec_ratio > cfg.guard_ratio
        else:  # gross: the executor's integer min_out against the floored tokens out
            rejected = math.floor(tokens) < math.ceil(size / (cfg.guard_ratio * seed_p))
        if rejected:
            return dict(base, status="guarded", exit_type="guard", hold_slots=0, pnl=-float(fee))
    n = len(slot)
    icap_bt = bt_deadline_index(slot, bt, x, cfg.cap_seconds) if cfg.cap_anchor == "block-time" else None
    observed = True
    if icap_bt is not None:  # block-time anchor
        icap = icap_bt
        observed = icap < n
        d = max(int(slot[icap]) if observed else int(slot[-1]), x)
        base["cap_anchor_used"] = "block-time"
    else:
        if cfg.cap_anchor == "block-time":  # block_time unusable on this path: G's day-mean slots
            s_cap = sec_per_slot(day, sph)
            base["cap_anchor_used"] = "day-mean-fallback"
        else:
            s_cap = s_day
            base["cap_anchor_used"] = "day-mean"
        d = x + int(round(cfg.cap_seconds / s_cap))
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
    if cfg.exit_mode == "boost90" and boost_idx is not None and boost_idx < icap:  # B90: strictly before the deadline print; a tp / sl at the same print or earlier wins
        bh = max(boost_idx, je)
        if hit < 0 or bh < hit:
            hit, exit_type = bh, "boost"
    if hit >= 0:
        fi = int(np.searchsorted(slot, slot[hit] + lag + off, "left"))
        xs = int(slot[hit])
    else:
        fi = int(np.searchsorted(slot, d + lag + off, "left")) if observed else n
        xs = d
    qa_f, ba_f = qpre[fi] + net, bpre[fi] - tokens
    gross = tokens * qa_f / (ba_f + tokens)
    sellval = gross * (1 - fee_ppm(qa_f, ba_f) / 1e6)
    pnl = float(sellval) - size - 2 * fee
    if cfg.rent_mode == "always" and cfg.rent_lamports:
        pnl -= cfg.rent_lamports
        base["rent"] = cfg.rent_lamports
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
    for r in rows:  # T2 --paired: the other exit mode on the same attempt (same fill set, so the same pressure curve)
        pa = r.get("pnl_alt")
        if pa is None:
            continue
        fee = float(r["fee"])
        r["pnl_alt_nofail"] = pa
        if r["status"] != "filled":
            r["pnl_alt_live"] = r["pnl_alt_flat"] = r["pnl_alt_press"] = pa
            continue
        r["pnl_alt_live"] = (1 - cfg.live_fail) * pa + cfg.live_fail * (-fee)
        r["pnl_alt_flat"] = (1 - cfg.flat_fail) * pa + cfg.flat_fail * (-fee)
        r["pnl_alt_press"] = (1 - r["p_press"]) * pa + r["p_press"] * (-fee)
    return {"pressure_intercept": None if curve is None else curve.intercept, "pressure_mean_p": (sum(r["p_press"] for r in fills) / len(fills)) if fills else None}


# --- statistics (G's analyze.py) -----------------------------------------------------------------------------------------------------


LEGS = ("live", "flat", "press", "nofail")
GATE_ROLE = {"flat": "binding", "press": "binding", "live": "report-only", "nofail": "report-only"}
BOOT_DRAWS, BOOT_SEED = 1000, 1


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction of the incomplete beta function (modified Lentz)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 400):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-16:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """Regularised incomplete beta function I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lead = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return lead * _betacf(a, b, x) / a
    return 1.0 - lead * _betacf(b, a, 1.0 - x) / b


def t_sf(t: float, df: float) -> float:
    """P(T > t) for Student's t with `df` degrees of freedom (numpy/math only: scipy is not a lab dependency)."""
    if math.isinf(t):
        return 0.0 if t > 0 else 1.0
    p = 0.5 * betainc(df / 2.0, 0.5, df / (df + t * t))
    return p if t >= 0 else 1.0 - p


def boot_means(x: np.ndarray, draws: int, seed: int = BOOT_SEED, chunk_cells: int = 4_000_000) -> np.ndarray:
    """Means of `draws` bootstrap resamples of `x` (n draws of n, with replacement), seed `seed`. The draws come in chunks so that 10,000 x 24,000 attempts is not
    one 2 GB index array; the generator is read in the same order, so the result equals one `rng.integers(0, n, size=(draws, n))` call, and the first 1,000 draws
    are the gate's 1,000 draws."""
    n = len(x)
    rng = np.random.default_rng(seed)
    out = np.empty(draws)
    step = max(1, chunk_cells // n)
    for lo in range(0, draws, step):
        hi = min(draws, lo + step)
        out[lo:hi] = x[rng.integers(0, n, size=(hi - lo, n))].mean(1)
    return out


def gate_stats(pnl_lamports: Sequence[float], fills: Sequence[bool], dates: Sequence[str], size: float, p_draws: int = BOOT_P_DRAWS) -> dict[str, Any]:
    """The promotion-gate statistics of one leg on one scope (judge items 8 and 3). `pnl_lamports` is one entry per ATTEMPT (guard rejects and failed sends at
    -fee); `fills` marks the filled ones. A cluster is the UTC date alone.
    The CI90 lower bounds are the gate's: 1,000 draws, seed 1, 5th percentile. `p_trade_boot` is REPORT-ONLY: the share of `p_draws` (default 10,000, seed 1;
    DEC-021 section 5) bootstrap means <= 0. The deciding p is the day-level t (`p_date_t`)."""
    x = np.asarray(pnl_lamports, float) / 1e9
    fl = np.asarray(fills, bool)
    n = len(x)
    if n == 0:
        return {"n_attempts": 0, "n_fills": 0}
    sz = size / 1e9
    rng = np.random.default_rng(BOOT_SEED)
    boot = x[rng.integers(0, n, size=(BOOT_DRAWS, n))].mean(1)
    lo_t = float(np.percentile(boot, 5))
    p_boot = boot if p_draws == BOOT_DRAWS else boot_means(x, p_draws)  # the first BOOT_DRAWS of p_boot are `boot`
    ud, inv = np.unique(np.asarray(dates), return_inverse=True)
    dsum = np.bincount(inv, weights=x)
    dn = np.bincount(inv)
    w = len(ud)
    rng2 = np.random.default_rng(BOOT_SEED)
    di = rng2.integers(0, w, size=(BOOT_DRAWS, w))
    lo_d = float(np.percentile(dsum[di].sum(1) / dn[di].sum(1), 5))
    dmean = dsum / dn
    p_day: float | None = None
    t_day: float | None = None
    if w >= 2:
        sd = float(dmean.std(ddof=1))
        if sd > 0:
            t_day = float(dmean.mean() / (sd / math.sqrt(w)))
            p_day = t_sf(t_day, w - 1)
        else:
            p_day = 0.0 if dmean.mean() > 0 else 1.0
    mean = float(x.mean())
    nf = int(fl.sum())
    mean_fill = float(x[fl].mean()) if nf else None
    return {
        "n_attempts": n, "n_fills": nf,
        "mean_per_attempt_pct": 100 * mean / sz, "mean_per_attempt_sol": mean,
        "mean_per_fill_pct": None if mean_fill is None else 100 * mean_fill / sz, "mean_per_fill_sol": mean_fill,
        "ci_trade_lo_pct": 100 * lo_t / sz, "ci_trade_lo_sol": lo_t,
        "ci_date_lo_pct": 100 * lo_d / sz, "ci_date_lo_sol": lo_d,
        "total_sol": float(x.sum()), "ex_top3_sol": float(x.sum() - np.sort(x)[-3:].sum()), "ex_best_day_sol": float(x.sum() - dsum.max()),
        "dates": w, "dates_pos": int((dsum > 0).sum()),
        "p_trade_boot": float((p_boot <= 0).mean()), "p_trade_boot_draws": int(p_draws), "t_date": t_day, "p_date_t": p_day,
    }


def stats(x_lamports: Sequence[float], days: Sequence[str], size: float) -> dict[str, Any]:
    """Mean per attempt as % of stake; gate trade-level CI90 (1,000 draws, seed 1, 5th pct); date-cluster CI90 (1,000 day resamples, seed 1);
    ex-top-3 SOL, ex-best-day SOL, days positive. (G's analyze.py; a cluster here is `day|block`. `gate_stats` clusters on the date alone.)"""
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


def book_stats(rows: Sequence[Mapping[str, Any]], size: float, p_draws: int = BOOT_P_DRAWS) -> dict[str, Any]:
    out: dict[str, Any] = {}
    scopes: list[tuple[str, tuple[str, ...] | None]] = [(b, (b,)) for b in (BLOCK_P2, BLOCK_P3, BLOCK_P4, BLOCK_P1A, BLOCK_P1C)]
    scopes += [(g, blocks) for g, blocks in GROUPS.items()] + [("all", None)]
    for name, blocks in scopes:
        sel = [r for r in rows if blocks is None or r["block"] in blocks]
        if not sel:
            continue
        days = [r["day"] + "|" + r["block"] for r in sel]
        fills = [r["status"] == "filled" for r in sel]
        cell: dict[str, Any] = {"fills": sum(fills), "guarded": sum(1 for r in sel if r["status"] == "guarded")}
        for leg in LEGS:
            cell[leg] = stats([r["pnl_" + leg] for r in sel], days, size)
        cell["gate"] = {leg: {"role": GATE_ROLE[leg], **gate_stats([r["pnl_" + leg] for r in sel], fills, [r["day"] for r in sel], size, p_draws)} for leg in LEGS}
        out[name] = cell
    return out


def paired_stats(diff_lamports: Sequence[float], dates: Sequence[str], size: float, fires: int) -> dict[str, Any]:
    """T2: B90 minus cap per attempt. Mean in pp of stake; date-cluster CI90 = 5th and 95th percentile of 1,000 UTC-date resamples (seed 1, the gate's resample)."""
    x = np.asarray(diff_lamports, float) / 1e9
    n = len(x)
    sz = size / 1e9
    ud, inv = np.unique(np.asarray(dates), return_inverse=True)
    dsum, dn = np.bincount(inv, weights=x), np.bincount(inv)
    w = len(ud)
    boot = None
    if w:
        di = np.random.default_rng(BOOT_SEED).integers(0, w, size=(BOOT_DRAWS, w))
        boot = dsum[di].sum(1) / dn[di].sum(1)
    return {"n_attempts": n, "n_differ": int((x != 0).sum()), "b90_fires": int(fires), "mean_pp": 100 * float(x.mean()) / sz, "total_sol": float(x.sum()),
            "ci90_date_lo_pp": None if boot is None else 100 * float(np.percentile(boot, 5)) / sz, "ci90_date_hi_pp": None if boot is None else 100 * float(np.percentile(boot, 95)) / sz,
            "dates": w, "dates_b90_better": int((dsum > 0).sum()), "dates_b90_worse": int((dsum < 0).sum())}


def paired_report(rows: Sequence[Mapping[str, Any]], cfg: Config, have_picks: bool) -> dict[str, Any]:
    """summary.paired[book][scope][leg] = paired_stats of (B90 pnl - cap pnl) over the attempts of that scope. books: `all` (when --book all) and `picks` (the pick
    subset, when --picks is given; P1 pick scores from cache_table are in-sample and are not used for a decision: pre-declaration section 2)."""
    b90_is_primary = cfg.exit_mode == "boost90"
    size = float(cfg.size_lamports)
    scopes: list[tuple[str, tuple[str, ...] | None]] = [(b, (b,)) for b in (BLOCK_P2, BLOCK_P3, BLOCK_P4, BLOCK_P1A, BLOCK_P1C)]
    scopes += [(g, blocks) for g, blocks in GROUPS.items()] + [("all", None)]
    books: list[tuple[str, list[Mapping[str, Any]]]] = [("all", list(rows))] if cfg.book == "all" else []
    if have_picks:
        books.append(("picks", [r for r in rows if r["pick"] == "pick"]))
    out: dict[str, Any] = {"definition": "B90 minus the 300 s cap, per attempt, pp of stake; same (date, mint), same entry, guard and price path", "books": {}}
    for bname, brows in books:
        cells: dict[str, Any] = {}
        for name, blocks in scopes:
            sel = [r for r in brows if blocks is None or r["block"] in blocks]
            if not sel:
                continue
            fires = sum(1 for r in sel if (r["exit_type"] if b90_is_primary else r["alt_exit_type"]) == "boost")
            cell: dict[str, Any] = {"attempts": len(sel), "b90_fires": fires}
            for leg in LEGS:
                diff = [(r["pnl_" + leg] - r["pnl_alt_" + leg]) if b90_is_primary else (r["pnl_alt_" + leg] - r["pnl_" + leg]) for r in sel]
                cell[leg] = {"role": GATE_ROLE[leg], **paired_stats(diff, [r["day"] for r in sel], size, fires)}
            cells[name] = cell
        out["books"][bname] = cells
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


class _Rows:
    """One mint's kept PumpSwap rows as typed arrays (about 70 bytes a row; a day holds millions, so no per-row tuples)."""

    __slots__ = ("slot", "tx", "ev", "seq", "pid", "bt", "buy", "sol", "tok", "q", "b", "tr", "track")

    def __init__(self, track: bool = False) -> None:
        self.track = track  # T2: keep a hashed trader id per row (only when a T2 switch is on, so the default run holds the same arrays as phase 1)
        self.tr = array("q")
        self.slot, self.tx, self.ev, self.seq, self.pid, self.bt = (array("q") for _ in range(6))
        self.buy = array("b")
        self.sol, self.tok, self.q, self.b = (array("d") for _ in range(4))

    def add(self, slot: int, tx: int, ev: int, seq: int, pid: int, buy: bool, sol: float, tok: float, q: float, b: float, bt: int = -1, tr: int = 0) -> None:
        if self.track:
            self.tr.append(tr)
        self.bt.append(bt)
        self.slot.append(slot)
        self.tx.append(tx)
        self.ev.append(ev)
        self.seq.append(seq)
        self.pid.append(pid)
        self.buy.append(1 if buy else 0)
        self.sol.append(sol)
        self.tok.append(tok)
        self.q.append(q)
        self.b.append(b)

    def arrays(self) -> dict[str, np.ndarray]:
        out = {k: np.frombuffer(getattr(self, k), dtype=np.int64) for k in ("slot", "tx", "ev", "seq", "pid", "bt")}
        if self.track:
            out["tr"] = np.frombuffer(self.tr, dtype=np.int64)
        out["buy"] = np.frombuffer(self.buy, dtype=np.int8).astype(bool)
        out.update({k: np.frombuffer(getattr(self, k), dtype=np.float64) for k in ("sol", "tok", "q", "b")})
        return out


def order_perm(slot: np.ndarray, tx: np.ndarray, ev: np.ndarray, seq: np.ndarray) -> np.ndarray:
    """Permutation sorting by `order_key` (tx = -1 stands for a null tx_index)."""
    null = tx < 0
    return np.lexsort((seq, np.where(null, 0, ev), np.where(null, 0, tx), slot))


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
    mig: dict[str, list[Any]] = {}  # mint -> [mslot, mbt, block, hour of the file holding the earliest `complete`]
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
                mig[m] = [r["slot"], bt, block, h]
            else:
                if r["slot"] < cur[0]:
                    cur[0], cur[2], cur[3] = r["slot"], block, h
                if isinstance(bt, int) and (cur[1] is None or bt < cur[1]):
                    cur[1] = bt
    diag["migrations"] = len(mig)
    by_mint: dict[str, _Rows] = {}
    pool_ids: dict[str, int] = {}
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
            rows = by_mint.get(m)
            if rows is None:
                rows = by_mint[m] = _Rows(cfg.needs_trader)
            tx = r.get("tx_index")
            btv = r.get("block_time")
            rows.add(slot, -1 if tx is None else int(tx), int(r.get("event_index") or 0), seq, pool_ids.setdefault(pool, len(pool_ids)), r.get("side") == "buy",
                     _num(r.get("sol_lamports")), _num(r.get("token_raw")), _num(r.get("quote_reserve")), _num(r.get("base_reserve")),
                     btv if isinstance(btv, int) and not isinstance(btv, bool) and btv > 0 else -1,
                     trader_id(r.get("trader")) if cfg.needs_trader else 0)
        log(f"{day} {h} pumpswap rows kept so far={seq} ({time.time() - t0:.0f}s)")
    max_slot = max([s for s in (tail_max_slot(idx["trades"][h][0]) for h in tf[-2:]) if s is not None] or [0])
    pool_names = {i: p for p, i in pool_ids.items()}
    out: dict[str, dict[str, Any]] = {}
    for m, rows in by_mint.items():
        a = rows.arrays()
        s0_by_pool = {int(pid): int(a["slot"][a["pid"] == pid].min()) for pid in np.unique(a["pid"])}
        pid = min(s0_by_pool, key=lambda i: (s0_by_pool[i], pool_names[i]))
        pool = pool_names[pid]
        diag["mints_with_canonical_pool"] += 1
        diag["multipool"] += int(len(s0_by_pool) > 1)
        s0 = s0_by_pool[pid]
        mslot = mig[m][0]
        if not (s0 + UNCENSORED_HORIZON <= max_slot and s0 - mslot <= MAX_S0_GAP):
            diag["censored"] += 1
            continue
        keep = np.flatnonzero(a["pid"] == pid)
        perm = keep[order_perm(a["slot"][keep], a["tx"][keep], a["ev"][keep], a["seq"][keep])]
        out[m] = {"pool": pool, "v": float(vband[pool]), "s0": s0, "mslot": mslot, "block": mig[m][2], "hour": mig[m][3],
                  **{c: a[c][perm] for c in ("slot", "bt", "buy", "sol", "tok", "q", "b")}, **({"tr": a["tr"][perm]} if cfg.needs_trader else {})}
    return out, diag


def load_vband(path: str | Path, lo: int, hi: int) -> dict[str, int]:
    v = json.loads(Path(path).read_text())["v"]
    return {k: int(x) for k, x in v.items() if x is not None and lo <= x <= hi}


@dataclass(frozen=True)
class PickSet:
    """A pick set: the EXP-012 score CSV (`kind == "csv"`, a pick is score >= threshold) or a JSONL of live-gate decisions (`kind == "jsonl"`, a pick is
    decision == "pick")."""

    kind: str
    score: Mapping[str, float]
    decision: Mapping[str, str]

    def __len__(self) -> int:
        return len(self.decision) if self.kind == "jsonl" else len(self.score)

    def label(self, mint: str, threshold: float) -> tuple[str, str, str]:
        """(pick | non_pick | unscored, score as text, raw decision)."""
        sc = self.score.get(mint)
        score = "" if sc is None else repr(sc)
        if self.kind == "jsonl":
            dec = self.decision.get(mint)
            return ("unscored", score, "") if dec is None else (("pick" if dec == "pick" else "non_pick"), score, dec)
        return ("unscored", "", "") if sc is None else (("pick" if sc >= threshold else "non_pick"), score, "")


def load_picks(path: str | Path) -> PickSet:
    """Read --picks: sniffed by the first non-blank character (`{` = JSONL of decisions, else the mint,score CSV).
    JSONL: one object per line with `mint` and `decision` (the format of the gate replay's `replay` output: a meta line, then `kind: "decision"` rows and
    `kind: "dead"` rows). A line without `mint` is skipped (the meta line). `decision == "pick"` is a pick, anything else is a non-pick. The first
    `decision` row of a mint wins; a `dead` row only counts when the mint has no decision row. An optional numeric `score` is kept."""
    text = Path(path).read_text(encoding="utf-8")
    if text.lstrip()[:1] == "{":
        decision: dict[str, str] = {}
        is_dead: set[str] = set()
        score: dict[str, float] = {}
        for no, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except ValueError as e:
                raise Refused(f"--picks JSONL line {no} is not JSON") from e
            if not isinstance(r, dict) or "mint" not in r:
                continue
            m, dec = r["mint"], r.get("decision")
            if not isinstance(m, str) or not isinstance(dec, str):
                raise Refused(f"--picks JSONL line {no}: `mint` and `decision` must be strings")
            dead = r.get("kind") == "dead"
            if m in decision and (dead or m not in is_dead):
                continue  # the first decision row stays; a dead row never overrides
            decision[m] = dec
            if dead:
                is_dead.add(m)
            else:
                is_dead.discard(m)
                sc = r.get("score")
                if isinstance(sc, (int, float)) and not isinstance(sc, bool):
                    score[m] = float(sc)
        return PickSet("jsonl", score, decision)
    out: dict[str, float] = {}
    rd = csv.DictReader(text.splitlines())
    if not rd.fieldnames or "mint" not in rd.fieldnames or "score" not in rd.fieldnames:
        raise Refused("--picks CSV needs a header with columns mint,score")
    for r in rd:
        out[r["mint"]] = float(r["score"])
    return PickSet("csv", out, {})


def run(args: argparse.Namespace, cfg: Config, log: Any) -> dict[str, Any]:
    check_inputs_allowed(args)  # first: a refused input path is never opened, listed or read
    cfg.validate()
    sources = build_sources(args)
    idx = index_hours(sources)
    vband = load_vband(args.vmap, cfg.v_lo, cfg.v_hi)
    sph = dict(SLOTS_PER_HOUR)
    if args.sph_json:
        sph = {k: float(x) for k, x in json.loads(Path(args.sph_json).read_text()).items()}
    hour_sph: dict[str, dict[str, Any]] = {}  # hour -> {slots_per_hour, source}
    if getattr(args, "hour_sph_json", None):
        hour_sph = {h: {"slots_per_hour": float(x), "source": "json"} for h, x in json.loads(Path(args.hour_sph_json).read_text()).items()}
    days = sorted({h[:10] for h in idx["trades"]})
    if args.only_day:
        unknown = sorted(set(args.only_day) - set(days))
        if unknown:
            raise Refused(f"no trade hours for --only-day {unknown}")
        days = [d for d in days if d in args.only_day]
    if cfg.book == "picks" and not args.picks:
        raise Refused("--book picks needs --picks")
    picks = load_picks(args.picks) if args.picks else None
    rows: list[dict[str, Any]] = []
    tot = {"hours": 0, "migrations": 0, "mints_with_canonical_pool": 0, "censored": 0, "multipool": 0, "bad_json": 0, "skipped_incomplete_migrations": 0,
           "bad_reserves": 0, "final_state_fallback": 0, "hour_sph_fallback": 0, "cap_bt_fallback": 0}
    days_done: list[str] = []

    def hour_seconds(h: str, day: str) -> float:
        if h not in hour_sph:
            m = measure_hour_sph(idx["trades"][h][0])
            if m is not None:
                hour_sph[h] = {"slots_per_hour": m, "source": "tape"}
            elif day in sph:
                hour_sph[h] = {"slots_per_hour": sph[day], "source": "day-table-fallback"}
                tot["hour_sph_fallback"] += 1
                log(f"{h}: ms/slot not measurable on the tape; using the day table")
            else:
                raise Refused(f"cannot measure ms/slot for hour {h} and {day} has no day table (use --hour-sph-json)")
        return 3600.0 / hour_sph[h]["slots_per_hour"]

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
            slot, isbuy, sol, tok, q, b = (md[c] for c in ("slot", "buy", "sol", "tok", "q", "b"))
            st = build_states(slot, isbuy, sol, tok, q, b, md["v"], cfg.final_state)
            if st is None:
                tot["bad_reserves"] += 1
                continue
            tot["final_state_fallback"] += st[2]
            hs = hour_seconds(md["hour"], day) if cfg.needs_hour_sph else None
            qpre, bpre, bt_, boost_idx = st[0], st[1], md["bt"], None
            t2: dict[str, Any] = {}
            if cfg.needs_trader:  # T2: stress cut, then the causal detector on the path the book actually trades
                trd = md["tr"]
                if cfg.boost_cut is not None:
                    c = cut_boost_path(cfg.boost_cut, trader=trd, slot=slot, isbuy=isbuy, sol=sol, tok=tok, qpre=qpre, bpre=bpre, bt=bt_, s0=md["s0"])
                    trd, slot, isbuy, sol, qpre, bpre, bt_ = (c[k_] for k_ in ("trader", "slot", "isbuy", "sol", "qpre", "bpre", "bt"))
                    t2.update(boost_cut_dropped=c["dropped"], boost_cut_keeper=int(c["keeper"] != 0))
                scan = boost_scan(trd, isbuy, sol, slot, md["s0"])
                boost_idx = None if scan is None else scan[0]
                t2.update(boost_found=int(scan is not None), boost_trigger_slot="" if scan is None else int(slot[scan[0]]))
            common = dict(day=day, sph=sph, v=md["v"], s0=md["s0"], slot=slot, isbuy=isbuy, sol=sol, qpre=qpre, bpre=bpre, hour=md["hour"], hour_s=hs, bt=bt_, boost_idx=boost_idx)
            r = simulate_attempt(cfg, **common)
            if cfg.paired:
                ra = simulate_attempt(replace(cfg, exit_mode="cap" if cfg.exit_mode == "boost90" else "boost90"), **common)
                r.update(alt_exit_type=ra["exit_type"], alt_hold_slots=ra["hold_slots"], pnl_alt=ra["pnl"])
            r.update(t2)
            tot["cap_bt_fallback"] += int(r["cap_anchor_used"] == "day-mean-fallback")
            r.update(day=day, block=md["block"], mint=m, pool=md["pool"], v=int(md["v"]), mslot=md["mslot"], s0=md["s0"], s0_minus_mslot=md["s0"] - md["mslot"],
                     hour=md["hour"], ms_per_slot_hour="" if hs is None else hs * 1000.0)
            if picks is None:
                r["pick"], r["score"], r["pick_decision"] = "", "", ""
            else:
                r["pick"], r["score"], r["pick_decision"] = picks.label(m, cfg.pick_threshold)
            rows.append(r)
        log(f"{day}: attempts so far {len(rows)}")
    n_before = len(rows)
    n_picks, n_non, n_unscored = (sum(1 for r in rows if r["pick"] == lab) for lab in ("pick", "non_pick", "unscored"))
    if cfg.book == "picks":  # the pick-only book: unscored and non-pick mints are not attempts; the pressure intercept refits on the picks' own fills
        rows = [r for r in rows if r["pick"] == "pick"]
    leg_info = apply_legs(rows, cfg)
    size = float(cfg.size_lamports)
    summary: dict[str, Any] = {
        "schema": SCHEMA, "tool": TOOL, "phase": 2, "banner": "LAB SCORER. NOT A PROMOTE, NOT GATE EVIDENCE. G's P_primary plus the judge's spec switches (all default to G); must not merge before owner O2/O3.",
        "config": asdict(cfg), "sources": {s.block: [str(d) for d in s.dirs] for s in sources}, "days": days_done,
        "vmap": {"path": str(args.vmap), "sha256": hashlib.sha256(Path(args.vmap).read_bytes()).hexdigest(), "pools_in_band": len(vband)},
        "picks": None if picks is None else {"path": str(args.picks), "sha256": hashlib.sha256(Path(args.picks).read_bytes()).hexdigest(), "format": picks.kind,
                                             "scored": len(picks), "threshold": cfg.pick_threshold if picks.kind == "csv" else None},
        "bootstrap": {"ci90_draws": BOOT_DRAWS, "ci90_seed": BOOT_SEED, "p_trade_boot_draws": cfg.boot_p_draws, "p_trade_boot_seed": BOOT_SEED, "p_trade_boot_role": "report-only"},
        "forbidden": {"path_parts": list(FORBIDDEN_PATH_PARTS), "path_prefixes": list(FORBIDDEN_PATH_PREFIXES), "exp009_hours": list(EXP009_HOURS), "cutoff_hour": CUTOFF_HOUR, "oracle_insample_last_hour": ORACLE_INSAMPLE_LAST_HOUR},
        "counts": {**tot, "attempts": len(rows), "fills": sum(1 for r in rows if r["status"] == "filled"), "guarded": sum(1 for r in rows if r["status"] == "guarded"),
                   "attempts_before_book_filter": n_before, "pick_attempts": n_picks, "non_pick_attempts": n_non, "unscored_attempts": n_unscored},
        "hour_sph": {h: {**v_, "ms_per_slot": 3.6e6 / v_["slots_per_hour"]} for h, v_ in sorted(hour_sph.items())},
        "fail_legs": {"live": cfg.live_fail, "flat": cfg.flat_fail, "pressure_target_mean_p": cfg.target_fail, **leg_info},
        "exit_types": {t: sum(1 for r in rows if r["exit_type"] == t) for t in ("tp", "sl", "deadline", "guard") + (("boost",) if cfg.exit_mode == "boost90" else ())},
        "books": {},
    }
    if cfg.book == "all":
        summary["books"]["all"] = book_stats(rows, size, cfg.boot_p_draws)
    if picks is not None:
        summary["books"]["picks"] = book_stats([r for r in rows if r["pick"] == "pick"], size, cfg.boot_p_draws)
        if cfg.book == "all":
            summary["books"]["non_picks"] = book_stats([r for r in rows if r["pick"] == "non_pick"], size, cfg.boot_p_draws)
    if cfg.needs_trader:
        summary["t2"] = {
            "banner": "REPORT-ONLY, EXPLORATION ONLY. Not gate evidence; does not touch EXP-022.", "exit_mode": cfg.exit_mode, "boost_cut": cfg.boost_cut, "paired": cfg.paired,
            "budget_lamports": BOOST_BUDGET_LAMPORTS, "b90_frac": B90_FRAC, "window_slots": BOOST_WINDOW_SLOTS, "detector": "G's causal behavioural detector on the tape `trader` field",
            "counts": {"attempts": len(rows), "boost_found": sum(int(r.get("boost_found", 0)) for r in rows), "keepers_cut": sum(int(r.get("boost_cut_keeper", 0)) for r in rows),
                       "mints_with_prints_removed": sum(1 for r in rows if r.get("boost_cut_dropped", 0)), "prints_removed": sum(int(r.get("boost_cut_dropped", 0)) for r in rows)},
        }
        if cfg.paired:
            summary["paired"] = paired_report(rows, cfg, picks is not None)
    summary["_rows"] = rows
    return summary


ROW_COLUMNS = (
    "day", "block", "mint", "pool", "v", "mslot", "s0", "k", "landing_slot", "status", "exit_type", "hold_slots", "spot_drift", "exec_ratio", "ssb",
    "nearby_lamports", "size", "fee", "pnl_nofail", "pnl_live", "pnl_flat", "pnl_press", "p_press", "pick", "score",
    # phase 2 (appended: the 25 columns above are byte-identical to phase 1 under the defaults)
    "s0_minus_mslot", "hour", "ms_per_slot_hour", "exit_lag_slots", "exec_ratio_gross", "cap_anchor_used", "rent", "pick_decision",
)


# T2 columns, appended only when a T2 switch is on (so the default rows.csv header is the phase-2 header)
ROW_COLUMNS_T2 = ("boost_found", "boost_trigger_slot", "boost_cut_dropped", "boost_cut_keeper", "alt_exit_type", "alt_hold_slots", "pnl_alt_nofail", "pnl_alt_live", "pnl_alt_flat", "pnl_alt_press")


def write_outputs(summary: dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = summary.pop("_rows")
    cf = summary.get("config", {})
    cols = ROW_COLUMNS + (ROW_COLUMNS_T2 if (cf.get("exit_mode", "cap") != "cap" or cf.get("boost_cut") is not None or cf.get("paired")) else ())
    with open(out_dir / "rows.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in sorted(rows, key=lambda r: (r["day"], r["mint"])):
            w.writerow({c: (repr(r[c]) if isinstance(r.get(c), float) else r.get(c, "")) for c in cols})
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
    ap.add_argument("--picks", default=None, help="pick set: CSV with header mint,score (EXP-012 scores) or a JSONL of live-gate decisions (mint, decision); adds a pick column and pick-only books")
    ap.add_argument("--pick-threshold", type=float, default=PICK_THRESHOLD, help="CSV scores only")
    ap.add_argument("--book", choices=("all", "picks"), default="all", help="all = G's book (every attempt); picks = only the picks are attempts (needs --picks)")
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--only-day", action="append", default=None, help="UTC day YYYY-MM-DD (repeatable); default every day with trade hours")
    ap.add_argument("--sph-json", default=None, help="day -> slots per hour override (default: G's table)")
    ap.add_argument("--hour-sph-json", default=None, help="hour (YYYY-MM-DDTHH) -> slots per hour override for --k-mode hour / --exit-lag-ms (default: measured on the tape)")
    d = Config()
    ap.add_argument("--k-seconds", type=float, default=None, help=f"entry latency in seconds (default {d.k_seconds})")
    ap.add_argument("--entry-latency-ms", type=float, default=None, help="entry latency in ms (alternative to --k-seconds)")
    ap.add_argument("--k-mode", choices=("day", "hour"), default=d.k_mode, help="seconds-per-slot for k: day = G's day table; hour = the attempt's UTC hour measured on the tape")
    ap.add_argument("--k-rounding", choices=("round", "ceil"), default=d.k_rounding, help="latency / ms-per-slot -> slots: round = Python round (G); ceil")
    ap.add_argument("--bound", choices=("END", "START"), default=d.bound)
    ap.add_argument("--exit-lag", type=int, default=None, help=f"exit lag in slots (default {d.exit_lag}; not with --exit-lag-ms)")
    ap.add_argument("--exit-lag-ms", type=float, default=None, help="exit lag in ms, mapped to slots with the attempt's hour ms/slot, ceil (e.g. 550 primary, 1350 pessimistic)")
    ap.add_argument("--guard", choices=("min_out", "none"), default=d.guard)
    ap.add_argument("--guard-ratio", type=float, default=d.guard_ratio)
    ap.add_argument("--guard-basis", choices=("net", "gross"), default=d.guard_basis, help="net = G (net_in / tokens_out); gross = size incl. the pool fee, with integer min_out")
    ap.add_argument("--cap-seconds", type=float, default=d.cap_seconds)
    ap.add_argument("--cap-anchor", choices=("day-mean", "block-time"), default=d.cap_anchor, help="day-mean = G (day SPH slots); block-time = first print with block_time >= landing + cap")
    ap.add_argument("--tp", type=float, default=d.tp)
    ap.add_argument("--sl", type=float, default=d.sl)
    ap.add_argument("--size-lamports", type=int, default=d.size_lamports)
    ap.add_argument("--fee-lamports", type=int, default=d.fee_lamports)
    ap.add_argument("--rent-lamports", type=int, default=d.rent_lamports, help="token-account rent per filled trip when --rent-mode always (lab constant 2039280)")
    ap.add_argument("--rent-mode", choices=("none", "always"), default=d.rent_mode, help="none = G; always = charge the rent on every filled trip (stress leg)")
    ap.add_argument("--live-fail", type=float, default=d.live_fail)
    ap.add_argument("--flat-fail", type=float, default=d.flat_fail)
    ap.add_argument("--boot-p-draws", type=int, default=d.boot_p_draws, help="bootstrap draws (seed 1) for the REPORT-ONLY trade-level p_trade_boot (DEC-021 section 5: 10,000); the CI90 lower bounds keep the gate's 1,000 draws")
    ap.add_argument("--exit-mode", choices=EXIT_MODES, default=d.exit_mode, help="cap = G's 300 s cap (default, byte-identical to phase 1); boost90 = T2 rule B90, report-only")
    ap.add_argument("--boost-cut", type=float, default=None, help="T2 stress replay: keep the keeper's buys up to this share (0 < f <= 1) of the 17.585 SOL budget and re-simulate the path (default: the tape as it is)")
    ap.add_argument("--paired", action="store_true", help="T2: also simulate the other exit mode on the same path; adds summary.paired (B90 minus cap, pp of stake, date-cluster CI90) and the alt_* / boost_* row columns")
    ap.add_argument("--final-state", choices=("lab", "g"), default=d.final_state, help="state after the last print: lab = pumpswap_post_trade_reserves; g = the audit's 1.25%% constant")
    return ap


def config_from_args(a: argparse.Namespace) -> Config:
    d = Config()
    if a.k_seconds is not None and a.entry_latency_ms is not None:
        raise Refused("give --k-seconds or --entry-latency-ms, not both")
    if a.exit_lag is not None and a.exit_lag_ms is not None:
        raise Refused("give --exit-lag (slots) or --exit-lag-ms, not both")
    k_seconds = a.k_seconds if a.k_seconds is not None else (a.entry_latency_ms / 1000.0 if a.entry_latency_ms is not None else d.k_seconds)
    return Config(
        k_seconds=k_seconds, bound=a.bound, exit_lag=d.exit_lag if a.exit_lag is None else a.exit_lag, guard=a.guard, guard_ratio=a.guard_ratio, cap_seconds=a.cap_seconds,
        tp=a.tp, sl=a.sl, size_lamports=a.size_lamports, fee_lamports=a.fee_lamports, live_fail=a.live_fail, flat_fail=a.flat_fail, final_state=a.final_state,
        pick_threshold=a.pick_threshold, k_mode=a.k_mode, k_rounding=a.k_rounding, exit_lag_ms=a.exit_lag_ms, guard_basis=a.guard_basis, cap_anchor=a.cap_anchor,
        rent_lamports=a.rent_lamports, rent_mode=a.rent_mode, book=a.book, boot_p_draws=a.boot_p_draws,
        exit_mode=getattr(a, "exit_mode", d.exit_mode), boost_cut=getattr(a, "boost_cut", None), paired=bool(getattr(a, "paired", False)),
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
