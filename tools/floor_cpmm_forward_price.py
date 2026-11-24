#!/usr/bin/env python3
"""floor_cpmm_forward_price: the ONE pinned reader of the FLOOR-CPMM forward run (docs/research/floor-cpmm/DECLARE.md section 11, Amendment 1, and 11.9 Erratum 1; pinned requirements 11.6 (a)-(e)).

    python -B -m tools.floor_cpmm_forward_price counts --out-dir D --until MS
    python -B -m tools.floor_cpmm_forward_price kill   --out-dir D --until MS

Two modes only.
  counts: prints exactly ONE integer line (the counted set of 11.6 at the check instant) and nothing on stderr; writes no file except the per-instant counts marker (11.6 (b), (c)).
  kill:   refuses unless the counts marker of the same instant records an integer >= 10 (11.6 (d)). The one-look marker is written ONLY after pricing has finished, atomically (tmp file,
          then an exclusive hard link) and it CONTAINS the result, so a crash during pricing leaves no marker and a marker always has its result.
--until must be one of the four check instants (FIRST_HOUR + 336 / 480 / 624 / 800 h). The marker dir is the constant MARKER_DIR outside the sealed dir; there is no CLI override (tests pass
a dir only through a function parameter). Every input is validated before any marker is written: a missing or unparseable pools.json, a pool without a bool wsol_is_token0, a missing first
hour file, zero readable hour files, an unreadable line, or rows out of time order is a refusal (exit 2, no marker). Hour files that start at or after C are never opened; any row with
ms >= C is dropped unseen; the tool refuses to run before C (11.6 (a)).

One streaming pass over the hour files in time order: only each pool's 26 h history (G9), the open positions and the counters are held. Never all rows.

Pricing math is scripts/research/floor_cpmm_replay.py (section 4 unchanged): cpmm_round_trip (haircuts, guard, own-buy re-injection, stranding, TP mark with the cell stake), leg_stats,
and tools.arena_stack.fail_legs / boot_lo. Sample semantics are section 11.2 (decide on a sample, price at the first sample at or after decision + latency).
Section 4: the send fee of a guarded buy (one send, -505,000 lamports) is paid in the K4 leg only: guarded rows are in the rows given to leg_stats as unfilled rows and enter
total_incl_guard / total_ex_top3_incl_guard, which K4 reads. K1 reads mean_sol, which is over FILLED trades and leaves the guard cost out (as the replay's kill_line does).

Choices fixed by 11.9 Erratum 1 (none tuned on a row):
  * G8 reference = FLOOR's: the latest sample at or before ms - 6 h inside the 26 h window (arena_rule_l1._floor_option j6; replay entry_gates).
  * G5 as the replay (simulate_pool): a guarded, unfilled-after-300-s or entry-invalid attempt runs the phantom hold (take-profit scan from the landing sample, else the 24 h time exit) and
    blocks the pool until that exit decision + 60 s. An attempt with no landing sample before C blocks nothing (the replay's unfilled_after_cut).
  * G7 counts only sample pairs lying fully inside [t - 24 h, t] (the first sample of the pair at or after t - 24 h).
Other declared choices: a sample whose owed-fee fields or open_time are null is undecodable = missing (11.3 item 7); entries decided after the counted-set cutoff C - (24 h + 1.9 s + 300 s) are
never scored in any cell. Last-good-state variant: when the pool has no sample after the landing sample the variant uses the headline stranded value.
Everything printed is a forward screen; the pressure leg is a PROXY and never counts for the gate; nothing here says FLOOR-CPMM makes money.
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import json
import math
import os
import re
import sys
import time
from bisect import bisect_left, bisect_right
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from scripts.research import floor_cpmm_replay as RP
from tools import arena_stack as F
from tools.arena_rule_l1 import fifo_cap

SCHEMA = "floor-cpmm-forward-price-v2"
FIRST_HOUR_FILE = "2026-11-23T23.jsonl"                      # pinned: the first hour file of job #3315 (11.9 Erratum 1)
FIRST_HOUR_MS = 1795474800000                                # 2026-11-23T23:00:00Z
CHECK_HOURS = (336, 480, 624, 800)
MARKER_DIR = Path("/data2/mal/floor-cpmm/pricer-marks")      # fixed, outside the sealed forward-1124 dir; no CLI override
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
HEADLINE = (False, True, KILL_STAKE, LAT_DECIDING_MS)       # (raw, g7, stake, lat_ms)
FILE_RE = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d)(?:\.r(\d+))?\.jsonl$")
COVERAGE_GAP = "pools migrated between 2026-11-21T08:51:11Z (pool list's last record) and the recorder's start are in neither the list nor forward discovery; not in the universe"
NOTE = ("FORWARD paper read of FLOOR-CPMM, kill line only. Rule constants carried over from FLOOR (winner's curse of DECLARE header item 4 applies). Both fail models are PumpSwap-calibrated; "
        "no CPMM live fill exists. The pressure leg is a PROXY (11.2 item 8), never the pressure-fail model for the promotion gate. This read cannot clear any gate item and is not a promotion.")
IDLE, PENDING, ACTIVE, EXITWAIT = 0, 1, 2, 3


class Refused(Exception):
    pass


def check_instants(first_hour_ms: int = FIRST_HOUR_MS, hours: tuple[int, ...] = CHECK_HOURS) -> list[int]:
    return [first_hour_ms + h * 3_600_000 for h in hours]


# ------------------------------------------------------------------------------------------------------------------------ small helpers
def hour_start_ms(name: str) -> int:
    return int(datetime.strptime(name, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp() * 1000)


def list_hour_files(out: Path, until: int) -> list[Path]:
    """Hour files in time order whose hour starts before `until`; a file at or after it is never opened. A restart file YYYY-MM-DDTHH.rN.jsonl follows the plain file of its hour."""
    keyed = []
    for p in out.iterdir():
        m = FILE_RE.match(p.name)
        if m and hour_start_ms(m.group(1)) < until:
            keyed.append((m.group(1), int(m.group(2) or 0), p))
    keyed.sort(key=lambda k: (k[0], k[1]))
    return [k[2] for k in keyed]


def load_json_strict(path: Path, what: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise Refused(f"{what} missing or unparseable") from e


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


def veto_reason(r: dict[str, Any]) -> str | None:
    """11.5 on the latest row at or before the decision sample (the sample's own row); null counts as the bad value."""
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


# ------------------------------------------------------------------------------------------------------------------------ one reserve variant of one pool: rolling 26 h history and gates
class Hist:
    """Rolling per-pool history for one reserve variant (corrected or raw-vault): sample ms, spot and the running sum of eligible |dq| (G7). Trimmed to the 26 h window (G9)."""
    __slots__ = ("ms", "spot", "cum", "prev_q", "prev_qb", "prev_ms", "has_prev", "last_q", "last_b", "last_ms")

    def __init__(self) -> None:
        self.ms: list[int] = []
        self.spot: list[float] = []
        self.cum: list[float] = []
        self.has_prev = False
        self.prev_q = self.prev_qb = 0.0
        self.prev_ms = 0
        self.last_q = self.last_b = 0.0
        self.last_ms = 0

    def step(self, ms: int, q: float, b: float, sup: float, open_t: float, gap: bool) -> tuple[bool, bool, bool]:
        """Returns (cand without G7, G7 lower bound >= 1 SOL, liquidity-event pair) for the sample, then appends it to the history."""
        qb = q * b
        liq = g1 = g6 = False
        w = 0.0
        if self.has_prev:
            liq = qb < self.prev_qb * (1 - F.K_TOL)
            g1 = (q < self.prev_q) and not liq
            g6 = (ms - self.prev_ms) <= ALIVE_MS
            if not gap and not liq:
                w = abs(q - self.prev_q)
        spot = q / b if b > 0 else float("nan")
        cum = (self.cum[-1] if self.cum else 0.0) + w
        self.ms.append(ms)
        self.spot.append(spot)
        self.cum.append(cum)
        lo = bisect_left(self.ms, ms - HIST_MS)
        if lo >= 1024:
            del self.ms[:lo], self.spot[:lo], self.cum[:lo]
            lo = 0
        age = ms / 1000.0 - open_t
        ok = g1 and g6 and AGE_LO_S <= age <= AGE_HI_S
        if ok:
            qfl = qb / sup if sup > 0 else float("nan")
            ok = (q - qfl <= RP.Q_STAR) and (qfl >= RP.Q_FLOOR_MIN)  # nan compares False
        if ok:
            j6 = bisect_right(self.ms, ms - ALIVE_MS) - 1
            ok = j6 >= lo and j6 >= 0 and self.spot[j6] > 0 and (spot / self.spot[j6]) < R6_MAX  # G8, FLOOR's reference (11.9 Erratum 1)
        vol_ok = False
        if ok:
            s = bisect_left(self.ms, ms - DAY_MS)  # first sample at or after t - 24 h: only pairs fully inside [t - 24 h, t] count
            vol_ok = (self.cum[-1] - self.cum[s]) >= RP.VOL_MIN
        self.has_prev, self.prev_q, self.prev_qb, self.prev_ms = True, q, qb, ms
        self.last_q, self.last_b, self.last_ms = q, b, ms
        return ok, vol_ok, liq


# ------------------------------------------------------------------------------------------------------------------------ one cell (variant, G7 form, stake, latency) of one pool
def new_counts() -> dict[str, Any]:
    return {"refused_gap": 0, "veto_triggers": {}, "veto_pools": {}, "fee_null_triggers": 0, "fee_null_pools": set(), "unfilled_after_300s": 0, "unfilled_no_landing_sample": 0,
            "guarded": 0, "landed_after_gap": 0, "exits_delayed_by_gap": 0, "longest_exit_delay_s": 0.0, "stranded_no_exit_sample": 0, "stranded_other": 0, "attempt_pools": set(),
            "open_at_end": 0}


class Cell:
    def __init__(self, raw: bool, g7: bool, stake_sol: float, lat_ms: int) -> None:
        self.raw, self.g7, self.stake_sol, self.lat_ms = raw, g7, stake_sol, lat_ms
        self.stake = stake_sol * 1e9
        self.rows: list[dict[str, Any]] = []
        self.cnt = new_counts()


class PoolCell:
    """The state machine of one cell on one pool. Rows are appended to the cell's list."""
    __slots__ = ("c", "pool", "state", "block", "fee", "spot", "q_dec", "t_dec", "land_t", "pending_row", "tok", "net", "mark", "tp_ok", "T", "Qe", "Be", "land_ms", "kind", "row",
                 "xdec", "reason", "exec_t", "mig_day")

    def __init__(self, c: Cell, pool: str) -> None:
        self.c, self.pool, self.state, self.block, self.row = c, pool, IDLE, -1, None

    def _phantom_done(self, xdec: int) -> None:
        self.block = xdec + STEP_MS
        self.state = IDLE
        if self.row is not None:
            self.row["exit_ms"] = xdec

    def step(self, ms: int, q: float, b: float, cand: bool, gap: bool, prev_ms: int, row: dict[str, Any], open_t: float, cutoff: int, until: int) -> None:
        c = self.c
        st = self.state
        if st == PENDING:
            if ms >= self.land_t:
                self._land(ms, q, b, gap)
        elif st == ACTIVE:
            if ms >= self.T:
                self.xdec, self.reason, self.exec_t = self.T, "time_24h", self.T
                self._exit_sample(ms, q, b, gap, prev_ms)
            elif self.tp_ok and b - self.tok > 0 and (q + self.net) / (b - self.tok) >= RP.TP * self.mark:
                self.xdec, self.reason, self.exec_t = ms, "tp_1.5x", ms + TP_LAG_MS
                if self.kind != "filled":
                    self._phantom_done(ms)
                else:
                    self.row.update(exit_ms=ms, reason=self.reason)
                    self.state = EXITWAIT
        elif st == EXITWAIT:
            if ms >= self.exec_t:
                self._exit_sample(ms, q, b, gap, prev_ms)
        if self.state != IDLE or not cand or ms < self.block or ms > cutoff:
            return
        cn = c.cnt
        if gap:
            cn["refused_gap"] += 1
            return
        why = veto_reason(row)
        if why:
            cn["veto_triggers"][why] = cn["veto_triggers"].get(why, 0) + 1
            cn["veto_pools"].setdefault(why, set()).add(self.pool)
            return
        fee = pool_fee(row)
        if fee is None:
            cn["fee_null_triggers"] += 1
            cn["fee_null_pools"].add(self.pool)
            return
        self.fee, self.spot, self.q_dec, self.t_dec, self.land_t = fee, q / b, q, ms, ms + c.lat_ms
        self.mig_day = RP.day_of(open_t)
        self.state = PENDING
        cn["attempt_pools"].add(self.pool)

    def _land(self, ms: int, q: float, b: float, gap_prev: bool) -> None:
        c, cn = self.c, self.c.cnt
        Qe, Be = float(q), float(b)
        self.Qe, self.Be, self.land_ms = Qe, Be, ms
        rt0 = RP.cpmm_round_trip(c.stake, Qe, Be, self.spot, Qe, Be, self.fee)
        self.net, self.tok = rt0["net"], rt0["tokens"]
        self.tp_ok = math.isfinite(self.tok) and 0 < self.tok < Be and Be - self.tok > 0
        self.mark = (Qe + self.net) / (Be - self.tok) if self.tp_ok else float("nan")
        self.tp_ok = self.tp_ok and math.isfinite(self.mark) and self.mark > 0
        self.T = self.t_dec + HOLD_MS
        late = ms - self.t_dec > MAX_LAND_MS
        base = {"pool": self.pool, "entry_ms": self.t_dec, "land_ms": ms, "day": RP.day_of(self.t_dec / 1000), "mig_day": self.mig_day, "flow_rise": max(0.0, Qe - self.q_dec),
                "landed_after_gap": bool(ms - self.t_dec > GAP_MS), "exit_ms": None, "reason": None, "stranded_item6": False, "pnl_last_good": None}
        base["ssb"] = 1 if base["flow_rise"] > 0 else 0
        self.row = None
        if late:  # 11.3 item 3: unfilled, no send paid, no row; the pool is blocked as the replay blocks any attempt (phantom hold)
            cn["unfilled_after_300s"] += 1
            self.kind = "unfilled"
        elif rt0["guarded"]:
            cn["guarded"] += 1
            self.kind = "guarded"
            self.row = {**base, "filled": False, "stranded": False, "pnl": rt0["pnl"]}
            c.rows.append(self.row)
        elif rt0["entry_invalid"]:
            cn["stranded_other"] += 1
            self.kind = "invalid"
            self.row = {**base, "filled": True, "stranded": True, "pnl": rt0["pnl"]}
            c.rows.append(self.row)
        else:
            if base["landed_after_gap"]:
                cn["landed_after_gap"] += 1
            self.kind = "filled"
            self.row = base
        self.state = ACTIVE

    def _exit_sample(self, ms: int, q: float, b: float, gap: bool, prev_ms: int) -> None:
        """The first sample at or after the exit execution time. A phantom attempt only blocks; a filled one is priced."""
        c, cn = self.c, self.c.cnt
        if self.kind != "filled":
            self._phantom_done(self.xdec)
            return
        if gap and prev_ms < self.exec_t:
            cn["exits_delayed_by_gap"] += 1
            cn["longest_exit_delay_s"] = max(cn["longest_exit_delay_s"], (ms - self.exec_t) / 1000.0)
        rt = RP.cpmm_round_trip(c.stake, self.Qe, self.Be, self.spot, float(q), float(b), self.fee)
        if rt["stranded"]:
            cn["stranded_other"] += 1
        self.row.update(filled=True, stranded=bool(rt["stranded"]), pnl=rt["pnl"], exit_ms=self.xdec, reason=self.reason)
        c.rows.append(self.row)
        self.block = self.xdec + STEP_MS
        self.state = IDLE
        self.row = None

    def finish(self, until: int, last_q: float, last_b: float, last_ms: int) -> None:
        """End of the recording (the check instant). Nothing here is read for outcomes; positions with no sample to price them follow 11.3 item 6."""
        c, cn = self.c, self.c.cnt
        if self.state == PENDING:
            cn["unfilled_no_landing_sample"] += 1
        elif self.state in (ACTIVE, EXITWAIT) and self.kind == "filled":
            xdec = self.xdec if self.state == EXITWAIT else self.T
            if xdec > until:
                cn["open_at_end"] += 1
            else:
                pnl = -c.stake - F.SEND_FEE_DECIDING - RENT_LG
                lg = RP.cpmm_round_trip(c.stake, self.Qe, self.Be, self.spot, last_q, last_b, self.fee)["pnl"] if last_ms > self.land_ms else pnl
                self.row.update(filled=True, stranded=True, stranded_item6=True, pnl=pnl, pnl_last_good=lg, exit_ms=xdec, reason=self.reason if self.state == EXITWAIT else "time_24h")
                cn["stranded_no_exit_sample"] += 1
                c.rows.append(self.row)
        self.state = IDLE


# ------------------------------------------------------------------------------------------------------------------------ the streaming pass
class PoolRun:
    def __init__(self, pool: str, wsol0: bool, cells: list[Cell], status_starts: list[int], status_gaps: list[list[int]], cutoff: int, until: int) -> None:
        self.pool, self.wsol0, self.cutoff, self.until = pool, wsol0, cutoff, until
        self.sg_starts, self.sg = status_starts, status_gaps
        self.hist: dict[bool, Hist] = {}
        self.pcs: list[tuple[Cell, PoolCell]] = []
        for c in cells:
            self.hist.setdefault(c.raw, Hist())
            self.pcs.append((c, PoolCell(c, pool)))
        self.prev_ms: int | None = None
        self.first_ms = self.last_ms = 0
        self.gap_time = 0
        self.has_gap = False
        self.n_samples = 0
        self.liq_events = 0
        self.fee_on: Any = None

    def add(self, r: dict[str, Any]) -> None:
        if r.get("creator_fee_on") is not None:
            self.fee_on = r["creator_fee_on"]
        if not valid_sample(r) or r.get("open_time") is None:
            return
        ow = owed_split(r, self.wsol0)
        if ow is None:
            return
        ms = r["ms"]
        gap = False
        if self.prev_ms is not None:
            gap = (ms - self.prev_ms) > GAP_MS
            if self.sg and not gap:
                k = bisect_left(self.sg_starts, ms) - 1  # gaps are consecutive intervals, so only the last one starting before ms can overlap
                gap = k >= 0 and self.sg[k][1] > self.prev_ms
            if gap:
                self.has_gap = True
                self.gap_time += ms - self.prev_ms
        else:
            self.first_ms = ms
        qr, br, sup, open_t = float(r["quote_reserve"]), float(r["base_reserve"]), float(r["token_supply"]), float(r["open_time"])
        res: dict[bool, tuple[bool, bool, bool, float, float]] = {}
        for raw, h in self.hist.items():
            q, b = (qr, br) if raw else (qr - ow[0], br - ow[1])
            ok, vol, liq = h.step(ms, q, b, sup, open_t, gap)
            res[raw] = (ok, vol, liq, q, b)
        if False in res and res[False][2] and ms <= self.cutoff:
            self.liq_events += 1
        prev = self.prev_ms if self.prev_ms is not None else ms
        for c, pc in self.pcs:
            ok, vol, _liq, q, b = res[c.raw]
            pc.step(ms, q, b, ok and (vol or not c.g7), gap, prev, r, open_t, self.cutoff, self.until)
        self.prev_ms = self.last_ms = ms
        self.n_samples += 1

    def finish(self) -> None:
        for c, pc in self.pcs:
            h = self.hist[c.raw]
            pc.finish(self.until, h.last_q, h.last_b, h.last_ms)


def stream(out: Path, until: int, specs: list[tuple[bool, bool, float, int]], pools_meta: dict[str, Any], status_gaps: list[list[int]]) -> dict[str, Any]:
    """ONE pass over the hour files before `until`, in time order. Returns the cells' rows and counters and the load statistics. Raises Refused on bad input."""
    files = list_hour_files(out, until)
    if not files:
        raise Refused("no readable hour file before the check instant")
    cutoff = until - CUTOFF_BACK_MS
    sg = sorted([g for g in status_gaps if g[0] < until], key=lambda g: g[0])
    sgs = [g[0] for g in sg]
    cells = [Cell(*s) for s in specs]
    runs: dict[str, PoolRun] = {}
    last_row_ms: dict[str, int] = {}
    st = {"files_read": 0, "rows_kept": 0, "rows_dropped_ms_ge_until": 0, "first_ms": None}
    for p in files:
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
                except (ValueError, KeyError, TypeError) as e:
                    raise Refused(f"unreadable line in {p.name}") from e
                if ms >= until:
                    st["rows_dropped_ms_ge_until"] += 1
                    continue
                if ms < last_row_ms.get(pool, 0):
                    raise Refused(f"rows out of time order in {p.name}")
                last_row_ms[pool] = ms
                pr = runs.get(pool)
                if pr is None:
                    meta = pools_meta.get(pool)
                    if not isinstance(meta, dict) or not isinstance(meta.get("wsol_is_token0"), bool):
                        raise Refused("a pool with rows has no bool wsol_is_token0 in pools.json")
                    pr = runs[pool] = PoolRun(pool, meta["wsol_is_token0"], cells, sgs, sg, cutoff, until)
                r["ms"] = ms
                pr.add(r)
                st["rows_kept"] += 1
                if st["first_ms"] is None or ms < st["first_ms"]:
                    st["first_ms"] = ms
    if st["rows_kept"] == 0:
        raise Refused("no row kept before the check instant")
    for pr in runs.values():
        pr.finish()
    lost = span = 0.0
    fee_on: dict[str, int] = {}
    for pr in runs.values():
        lost += pr.gap_time
        span += pr.last_ms - pr.first_ms
        k = str(pr.fee_on)
        fee_on[k] = fee_on.get(k, 0) + 1
    res: dict[str, Any] = {"cells": {s: (c.rows, c.cnt) for s, c in zip(specs, cells)}, "load": st, "pools_sampled": len(runs), "pools_with_gap": sum(1 for p in runs.values() if p.has_gap),
                           "share_pool_time_lost_to_gaps": (lost / span) if span else None, "creator_fee_on_distribution": fee_on, "liquidity_events": sum(p.liq_events for p in runs.values())}
    for _rows, _cnt in res["cells"].values():
        _rows.sort(key=lambda r: (r["entry_ms"], r["pool"]))
    return res


# ------------------------------------------------------------------------------------------------------------------------ reports
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
        out["last_good_state_note"] = "with no sample after the landing sample the variant uses the headline stranded value"
    out["fifo_k4_report_only"] = fifo_cap([(r["entry_ms"], r["exit_ms"]) for r in rows if r["filled"] and r["exit_ms"] is not None])
    return out


def verdict(cell: dict[str, Any], n: int) -> dict[str, Any]:
    legs = ["flat"] + (["press_proxy"] if isinstance(cell.get("press_proxy"), dict) and cell["press_proxy"].get("status") != "NOT_AVAILABLE" else [])
    k1 = all(cell[g]["mean_sol"] is not None and cell[g]["mean_sol"] > 0 for g in legs)
    k4 = all(cell[g]["total_ex_top3_incl_guard_sol"] is not None and cell[g]["total_ex_top3_incl_guard_sol"] > 0 for g in legs)
    k2 = cell["flat"]["trades_per_day"] >= K2_MIN_PER_DAY
    return {"legs_read": legs, "K1": k1, "K2": k2, "K4": k4, "scored": n, "verdict": "PASS_FORWARD_KILL_LINE_NOT_A_PROMOTION" if (k1 and k2 and k4) else "KILLED"}


def cell_key(spec: tuple[bool, bool, float, int]) -> str:
    raw, g7, stake, lat = spec
    return f"stake{stake}_lat{lat}ms_{'G7' if g7 else 'noG7_report_only'}_{'raw_vault_variant' if raw else 'corrected'}"


# ------------------------------------------------------------------------------------------------------------------------ markers
def counts_marker(md: Path, until: int) -> Path:
    return md / f"counts-{until}.json"


def kill_marker(md: Path) -> Path:
    return md / "kill-onelook.json"


def publish(path: Path, obj: Any) -> None:
    """Atomic exclusive create: write a tmp file, fsync, hard-link it to `path` (fails if it exists), remove the tmp."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, sort_keys=True, default=str)
        f.flush()
        os.fsync(f.fileno())
    try:
        os.link(tmp, path)
    except FileExistsError:
        raise Refused(f"marker exists: {path.name}") from None
    finally:
        os.unlink(tmp)


# ------------------------------------------------------------------------------------------------------------------------ modes
def validate(out: Path, md: Path, until: int, first_hour_ms: int, check_hours: tuple[int, ...]) -> tuple[dict[str, Any], list[list[int]], dict[str, Any]]:
    if until not in check_instants(first_hour_ms, check_hours):
        raise Refused("--until is not one of the four check instants")
    if not out.is_dir() or not md.is_dir():
        raise Refused("out dir and marker dir must exist")
    if md.resolve() == out.resolve() or out.resolve() in md.resolve().parents:
        raise Refused("marker dir must be outside the out dir")
    first = datetime.fromtimestamp(first_hour_ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H") + ".jsonl"
    if not (out / first).is_file():
        raise Refused(f"first hour file {first} missing")
    pj = load_json_strict(out / "pools.json", "pools.json")
    pools = pj.get("pools") if isinstance(pj, dict) else None
    if not isinstance(pools, dict) or not pools:
        raise Refused("pools.json has no pools dict")
    status = load_json_strict(out / "status.json", "status.json") if (out / "status.json").exists() else {}
    if not isinstance(status, dict):
        raise Refused("status.json unparseable")
    sg = [[int(a), int(c)] for a, c in (status.get("gaps") or []) if isinstance(a, (int, float)) and isinstance(c, (int, float))]
    return pools, sg, status


def mode_counts(out: Path, md: Path, until: int, first_hour_ms: int = FIRST_HOUR_MS, check_hours: tuple[int, ...] = CHECK_HOURS) -> int:
    mk = counts_marker(md, until)
    pools, sg, _status = validate(out, md, until, first_hour_ms, check_hours)
    if mk.exists():
        raise Refused(f"counts already run at this instant (marker {mk.name})")
    res = stream(out, until, [HEADLINE], pools, sg)
    n = count_set(res["cells"][HEADLINE][0])
    publish(mk, {"schema": SCHEMA, "until": until, "count": n})
    return n


def mode_kill(out: Path, md: Path, until: int, first_hour_ms: int = FIRST_HOUR_MS, check_hours: tuple[int, ...] = CHECK_HOURS) -> dict[str, Any]:
    pools, sg, status = validate(out, md, until, first_hour_ms, check_hours)
    if kill_marker(md).exists():
        raise Refused("the one look is spent (kill marker exists)")
    try:
        cm = json.loads(counts_marker(md, until).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cm = None
    if not isinstance(cm, dict) or not isinstance(cm.get("count"), int) or isinstance(cm.get("count"), bool) or cm["count"] < MIN_COUNT:
        raise Refused(f"kill refused: no counts marker with an integer >= {MIN_COUNT} at this instant")
    specs = [(raw, g7, stake, lat) for g7 in (True, False) for raw in (False, True) for stake in STAKES for lat in LATS_MS]
    res = stream(out, until, specs, pools, sg)
    rows = res["cells"][HEADLINE][0]
    cnt = res["cells"][HEADLINE][1]
    n = count_set(rows)
    D = (until - first_hour_ms) / 3_600_000 / 24.0
    cells = {cell_key(s): cell_report(res["cells"][s][0], D) for s in specs}
    dec = cells[cell_key(HEADLINE)]
    ctr = status.get("counters") or {}
    sg_until = [g for g in sg if g[0] < until]
    cnt2 = {**{k: v for k, v in cnt.items() if not isinstance(v, set)}, "veto_pools": {k: len(v) for k, v in cnt["veto_pools"].items()}, "fee_null_pools": len(cnt["fee_null_pools"]),
            "attempt_pools": len(cnt["attempt_pools"]), "liquidity_events_counted_corrected": res["liquidity_events"]}
    result = {"schema": SCHEMA, "note": NOTE, "until_ms": until, "counted_set": n, "counts_marker_integer": cm["count"], "counted_set_matches_marker": n == cm["count"], "recorded_hours": D * 24,
              "D_days": D, "first_row_ms": res["load"]["first_ms"], "recorder_start_ms": status.get("start_ms"), "coverage_gap": COVERAGE_GAP,
              "g8_reference": "latest sample at or before ms - 6 h in the 26 h window (FLOOR's j6; 11.9 Erratum 1)", "headline_cell": cell_key(HEADLINE), "kill": verdict(dec, n), "cells": cells,
              "events": cnt2, "load": res["load"], "universe_pools_sampled": res["pools_sampled"], "pools_with_gap": res["pools_with_gap"],
              "share_pool_time_lost_to_gaps": res["share_pool_time_lost_to_gaps"],
              "recorder_counters_at_read_time": {k: ctr.get(k) for k in ("polls", "gap_ms", "poll_chunk_errors", "row_errors", "polls_failed")},
              "recorder_gap_entries_before_until": sg_until, "recorder_gap_entry_lengths_s_all_not_only_restarts": [(c - a) / 1000.0 for a, c in sg_until],
              "creator_fee_on_distribution": res["creator_fee_on_distribution"],
              "latency_cells_note": "on a 60 s grid both latencies land on the next sample unless a gap intervenes; the cells differ only inside a gap",
              "bias_note": "intra-minute take-profits are missed (exits later or at the time exit); the direction of the bias on entries is unknown (11.2 item 4)",
              "veto_note": "no look-ahead toward eligibility (11.5); vesting and non-fee Token-2022 extensions unchecked",
              "g7_note": "a deposit (q*b rising) is not a liquidity event under 11.2, so it can add |dq| to the G7 lower bound (declared DECLARE-level gap)"}
    publish(kill_marker(md), {"schema": SCHEMA, "until": until, "counted": cm["count"], "result": result})  # marker and result together, only after pricing finished
    return result


def main(argv: list[str] | None = None, now_ms: int | None = None, marker_dir: Path | None = None, first_hour_ms: int = FIRST_HOUR_MS, check_hours: tuple[int, ...] = CHECK_HOURS) -> int:
    """marker_dir, first_hour_ms and check_hours are function parameters for tests only; the CLI has no way to set them."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mode", choices=("counts", "kill"))
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--until", required=True, type=int, help="the check instant C in ms (one of the four registered instants)")
    a = ap.parse_args(argv)
    out, md = Path(a.out_dir), Path(marker_dir) if marker_dir is not None else MARKER_DIR
    try:
        now = int(time.time() * 1000) if now_ms is None else now_ms
        if now < a.until:
            raise Refused("refusing to run before the check instant (--until)")
        if a.mode == "counts":
            print(mode_counts(out, md, a.until, first_hour_ms, check_hours))
        else:
            print(json.dumps(mode_kill(out, md, a.until, first_hour_ms, check_hours), sort_keys=True, default=str))
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
