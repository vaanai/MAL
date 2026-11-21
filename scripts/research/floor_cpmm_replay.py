#!/usr/bin/env python3
"""floor_cpmm_replay: the FLOOR-CPMM replay pricer (step 3). PAPER ONLY, in-sample, no network, no tape.

Implements docs/research/floor-cpmm/DECLARE.md (frozen at main 9f784ac7, blob c4719da6) and nothing else. "DECLARE sN" below = its section N.
It reads ONE window-mode fetch dir written by `tools/launchlab_cpmm_fetch.py window` (`pools/pool-<pool>.jsonl[.zst]`, `window-checkpoint.json`) plus the same migrations
list the fetch was run with, and prints the DECLARE s7 outputs for 4 cells (stakes 0.10 / 0.25 SOL x latencies 1.9 / 4.085 s) under both fail legs and the s9 kill line.

    python scripts/research/floor_cpmm_replay.py --fetch-dir DIR --migrations LIST.json --run-end 2026-11-21T20:00:00Z [--out result.json]

Exit 0 = priced (the verdict word may still be KILLED or NOT_DECIDABLE); exit 2 = refused (DECLARE s8: a missing or truncated pool, a checkpoint that shows a cap stop,
a fetch dir under a WINDOW_FORBIDDEN_OUT root, or a bad argument). The result text says: in-sample, not blind, not a gate item, never "makes money".

pool_meta contract (the keys the migrations list must carry through into the pool_meta row; a missing key excludes the pool, counted as `meta_missing:<key>`):
  token_mint (or mint0 + mint1 with WSOL on one side), supply (token supply S in raw units, `token_supply` accepted), mint_authority, freeze_authority (key present, null = none),
  transfer_fee (bool: Token-2022 transfer-fee extension present), lp_locked_frac (0..1), amm_config, trade_fee_rate, creator_fee_rate (ppm of 1e6; 0 when not charged).
Reused from tools/arena_stack.py (not re-implemented): constants, k_slots, fail_legs, press_intercept, boot_lo. Replaced by CPMM forms, as DECLARE s4 names them: fee_ppm (the pool's own
rate), the V_LO checks (Q > 0, B > 0), the exit lag (max(2, ceil(...)), no assert), `round_trip` (cpmm_round_trip below; parity-tested against arena_stack.round_trip).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from bisect import bisect_left, bisect_right
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import arena_stack as F  # noqa: E402
from tools import launchlab_cpmm_fetch as C  # noqa: E402
from tools import launchlab_price as L  # noqa: E402
from tools.arena_pool_table import HIST_HOURS  # noqa: E402
from tools.arena_rule_l1 import fifo_cap  # noqa: E402

WSOL = C.WSOL
WIN_START = C.WIN_START
AGE_LO, AGE_HI = 24 * 3600, 200 * 3600
ALIVE_S = 6 * 3600
Q_STAR = 0.5e9          # E1: excess <= 0.5 SOL
Q_FLOOR_MIN = 10e9      # E2: q_floor >= 10 SOL
VOL_MIN = 1e9
R6_MAX = 0.80
TP = 1.5
HOLD_S = 86400.0
LEG_S = 1.9
COOLDOWN_S = 60
LP_MIN = 0.95
SLOT_S_FALLBACK = 0.4
SLOT_S_MIN_SPAN = 1800
STAKES = (0.10, 0.25)
LATENCIES = (1.9, 4.085)
KILL_CELL = (0.25, 4.085)
K2_MIN_PER_DAY = 0.3
K3_MIN_FILLED = 20
FEE_TOL = 1  # lamports per swap in the fee cross-check (ceil rounding)
STAT_NOTE = ("IN-SAMPLE screen, not blind; survivorship and look-ahead in the universe vetoes; both fail models are PumpSwap-calibrated; cannot clear any promotion-gate item; "
             "a pass of K1-K4 is not a promotion and does not say the book makes money (DECLARE preamble, s9).")


class Refused(Exception):
    pass


def iso_ts(s: str) -> int:
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def day_of(ts: float) -> str:
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%d")


# ------------------------------------------------------------------------------------------------------------------------ fetch dir
def read_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix == ".zst":
        from tools.arena_zst import open_zst

        rd = open_zst(path)
        buf = bytearray()
        while True:
            b = rd.read()
            if not b:
                break
            buf += b
        rd.close()
        text = bytes(buf).decode("utf-8")
    else:
        text = path.read_text(encoding="utf-8")
    return [json.loads(ln) for ln in text.splitlines() if ln.strip()]


def check_fetch_dir(fd: Path) -> dict[str, Any]:
    """Refuse a fetch dir under a WINDOW_FORBIDDEN_OUT root; return the window checkpoint (required)."""
    r = os.path.realpath(str(fd))
    for root in C.WINDOW_FORBIDDEN_OUT:
        root = os.path.realpath(root)
        if r == root or r.startswith(root + os.sep):
            raise Refused(f"fetch_dir_under_sealed_root: {root}")
    if not fd.is_dir():
        raise Refused("fetch_dir_missing")
    if (fd / "checkpoint.json").exists():
        raise Refused("fetch_dir_holds_other_run_checkpoint")
    cpf = fd / C.WINDOW_CP
    if not cpf.is_file():
        raise Refused("window_checkpoint_missing")
    cp = json.loads(cpf.read_text())
    for key in ("cap_reached", "cap_stop", "cap_stopped", "stopped", "exit_cap"):
        if cp.get(key):
            raise Refused(f"window_checkpoint_shows_cap_stop: {key}")
    return cp


def load_fetch(fd: Path, migs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Every listed pool must be present, complete and untruncated, else NOT_DECIDABLE (DECLARE s8). Returns ([{meta, rows}], checkpoint)."""
    cp = check_fetch_dir(fd)
    done = cp.get("window") or {}
    pools = []
    for m in migs:
        pool = m["pool"]
        res = done.get(pool)
        if not res:
            raise Refused(f"NOT_DECIDABLE: pool {pool} missing from the checkpoint (cap stop or incomplete fetch)")
        if res.get("status") != "ok" or res.get("truncated"):
            raise Refused(f"NOT_DECIDABLE: pool {pool} truncated or not ok in the checkpoint")
        base = fd / "pools" / f"pool-{pool}.jsonl"
        if Path(str(base) + ".part").exists() or base.with_suffix(".part").exists():
            raise Refused(f"NOT_DECIDABLE: pool {pool} has a partial file")
        f = next((p for p in (Path(str(base) + ".zst"), base) if p.is_file()), None)
        if f is None:
            raise Refused(f"NOT_DECIDABLE: pool {pool} file missing")
        rows = read_rows(f)
        if not rows or rows[0].get("type") != "pool_meta":
            raise Refused(f"NOT_DECIDABLE: pool {pool} has no pool_meta head")
        if rows[0].get("truncated"):
            raise Refused(f"NOT_DECIDABLE: pool {pool} head says truncated")
        pools.append({"meta": rows[0], "rows": rows[1:], "pool": pool})
    return pools, cp


# ------------------------------------------------------------------------------------------------------------------------ pool model
_REQUIRED = ("supply", "mint_authority", "freeze_authority", "transfer_fee", "lp_locked_frac", "amm_config", "trade_fee_rate", "creator_fee_rate")


def meta_get(meta: dict[str, Any], key: str):
    if key == "supply" and "supply" not in meta and "token_supply" in meta:
        return meta["token_supply"]
    return meta[key]


def universe_reason(meta: dict[str, Any]) -> str | None:
    """DECLARE s2 vetoes on the pool_meta (current-state reads). None = in the universe."""
    for key in _REQUIRED:
        if key not in meta and not (key == "supply" and "token_supply" in meta):
            return f"meta_missing:{key}"
    m0, m1 = meta.get("mint0"), meta.get("mint1")
    tok = meta.get("token_mint") or (m1 if m0 == WSOL else m0)
    if not tok or (m0 is not None and WSOL not in (m0, m1)) or meta.get("wsol_in_pool") is False:
        return "non_wsol_quote"
    if meta_get(meta, "lp_locked_frac") is None or meta_get(meta, "lp_locked_frac") < LP_MIN:
        return "lp_not_locked"
    if meta_get(meta, "mint_authority") is not None:
        return "mint_authority"
    if meta_get(meta, "freeze_authority") is not None:
        return "freeze_authority"
    if meta_get(meta, "transfer_fee"):
        return "transfer_fee"
    s = meta_get(meta, "supply")
    if not isinstance(s, (int, float)) or isinstance(s, bool) or s <= 0:
        return "bad_supply"
    return None


class Pool:
    """One pool's swaps with the reserves after each swap. Arrays are in (slot, signature, event_index) order."""

    def __init__(self, rec: dict[str, Any]) -> None:
        meta = rec["meta"]
        self.id = rec["pool"]
        self.meta = meta
        self.M = int(meta["block_time"])
        m0, m1 = meta.get("mint0"), meta.get("mint1")
        self.token = meta.get("token_mint") or (m1 if m0 == WSOL else m0)
        self.reason = universe_reason(meta)
        self.fee_rate = 0
        self.amm_config = meta.get("amm_config")
        self.supply = 0.0
        self.n_ix = sum(1 for r in rec["rows"] if r.get("type") == "ix")
        sw = [r for r in rec["rows"] if r.get("type") == "swap"]
        self.dropped_before_window = sum(1 for r in sw if int(r["block_time"]) < WIN_START)
        sw = [r for r in sw if int(r["block_time"]) >= WIN_START]
        key = lambda r: (r["slot"], r.get("signature") or "", r.get("event_index") or 0)  # noqa: E731
        sw.sort(key=key)
        ixk = sorted(key(r) for r in rec["rows"] if r.get("type") == "ix")
        self.sw = sw
        self.slots = [int(r["slot"]) for r in sw]
        self.bts = [int(r["block_time"]) for r in sw]
        self.buy: list[bool] = []
        self.wsol: list[int] = []
        self.q: list[int] = []
        self.r: list[int] = []
        self.spot: list[float] = []
        self.hidx = [b // 3600 for b in self.bts]
        self.fee_checked = 0
        self.fee_bad = 0
        if self.reason is None:
            self.reason = self._build(sw, ixk, key)
        if self.reason is None:
            self.fee_rate = int(meta_get(meta, "trade_fee_rate")) + int(meta_get(meta, "creator_fee_rate"))
            self.supply = float(meta_get(meta, "supply"))

    def _build(self, sw: list[dict[str, Any]], ixk: list[tuple], key) -> str | None:
        tok = self.token
        before: list[tuple[int, int]] = []  # (q_before, r_before) per swap
        for r in sw:
            im, om = r.get("input_mint"), r.get("output_mint")
            if im is None or om is None:
                return "swap_no_mint"
            if {im, om} != {tok, WSOL}:
                return "swap_foreign_mint"
            buy = im == WSOL
            self.buy.append(buy)
            self.wsol.append(int(r["input_amount"] if buy else r["output_amount"]))
            iv, ov = int(r["input_vault_before"]), int(r["output_vault_before"])
            before.append((iv, ov) if buy else (ov, iv))
        n = len(sw)
        for i, r in enumerate(sw):
            nxt = before[i + 1] if i + 1 < n else None
            between = bool(nxt) and any(key(r) < k < key(sw[i + 1]) for k in ixk)
            if nxt is not None and not between:
                qa, ra = nxt  # state after swap i = state before swap i+1 (no non-swap instruction between)
            else:
                qa, ra = self._derive(r, before[i], self.buy[i])
            self.q.append(qa)
            self.r.append(ra)
            self.spot.append(qa / ra if ra > 0 else float("nan"))
        cfg_rate = int(meta_get(self.meta, "trade_fee_rate"))
        cr_rate = int(meta_get(self.meta, "creator_fee_rate"))
        for r in sw:  # DECLARE s5 cross-check, per swap, +-FEE_TOL lamports (ceil rounding)
            if "trade_fee" not in r:
                continue
            ia = int(r["input_amount"])
            ok = abs(int(r["trade_fee"]) - math.ceil(ia * cfg_rate / 1e6)) <= FEE_TOL
            if r.get("creator_fee_on_input") and cr_rate:
                ok = ok and abs(int(r["creator_fee"]) - math.ceil(ia * cr_rate / 1e6)) <= FEE_TOL
            self.fee_checked += 1
            self.fee_bad += 0 if ok else 1
        if self.fee_checked == 0:
            return "fee_unverified"
        if self.fee_bad:
            return "fee_mismatch"
        return None

    @staticmethod
    def _derive(r: dict[str, Any], bf: tuple[int, int], buy: bool) -> tuple[int, int]:
        """State after a swap from its own amounts (last swap, or a non-swap instruction follows). Declared approximation: protocol and fund shares of the trade fee are not in the event."""
        qb, rb = bf
        ia, oa = int(r["input_amount"]), int(r["output_amount"])
        itf, otf = int(r.get("input_transfer_fee") or 0), int(r.get("output_transfer_fee") or 0)
        cf = int(r.get("creator_fee") or 0)
        on_in = bool(r.get("creator_fee_on_input"))
        in_after_delta = ia - itf - (cf if on_in else 0)
        out_after_delta = oa + otf + (0 if on_in else cf)
        return (qb + in_after_delta, rb - out_after_delta) if buy else (qb - out_after_delta, rb + in_after_delta)

    def state_at_slot(self, x: int) -> int:
        return bisect_right(self.slots, x) - 1


# ------------------------------------------------------------------------------------------------------------------------ pooled clock (DECLARE s4)
class Clock:
    def __init__(self, pools: list[Pool], rows_of_all: list[tuple[int, int]]) -> None:
        pr = sorted(rows_of_all)
        self.bts = [a for a, _ in pr]
        self.slots = [b for _, b in pr]
        self._c: dict[int, float] = {}

    def slot_s(self, ts: float) -> float:
        hs = int(ts) - int(ts) % 3600
        if hs in self._c:
            return self._c[hs]
        a, b = bisect_left(self.bts, hs), bisect_left(self.bts, hs + 3600)
        v = SLOT_S_FALLBACK
        if a < len(self.bts) and b < len(self.bts) and self.bts[b] - self.bts[a] >= SLOT_S_MIN_SPAN and self.slots[b] - self.slots[a] > 0:
            v = (self.bts[b] - self.bts[a]) / (self.slots[b] - self.slots[a])
        self._c[hs] = v
        return v

    def clock_slot(self, T: float) -> int:
        i = bisect_right(self.bts, T) - 1
        if i < 0:
            raise Refused("no_pooled_swap_at_or_before_clock_instant")
        return self.slots[i] + round((T - self.bts[i]) / self.slot_s(T))


def exit_lag(slot_s: float) -> int:
    return max(F.MIN_EXIT_LAG_SLOTS, math.ceil(F.EXIT_LAG_S / slot_s - F.SLOT_TOL))


# ------------------------------------------------------------------------------------------------------------------------ round trip (CPMM form of arena_stack.round_trip)
def cpmm_round_trip(stake: float, Qe: float, Be: float, spot: float, Qx: float, Bx: float, fee: float, send: float = F.SEND_FEE_DECIDING, rent: float = F.RENT_DECIDING,
                    h_in: float = F.HAIRCUT_ENTRY, h_out: float = F.HAIRCUT_SELL, guard_ratio: float = F.GUARD, rent_stranded: float = F.RENT_STRANDED,
                    k_tol: float = F.K_TOL) -> dict[str, Any]:
    """Lamports. fee = the pool's rate as a fraction (entry, TP mark and exit all use it). Same structure as arena_stack.round_trip with the V_LO checks replaced by Q > 0, B > 0:
    guard -> -send, unfilled; invalid entry or exit state, or proceeds < one send -> stranded (-stake - send - rent_stranded)."""
    fin = math.isfinite
    net = stake * (1 - fee)
    tok = Be * net / (Qe + net) if (fin(Qe) and fin(Be) and Qe + net != 0) else float("nan")
    bad = not (fin(Qe) and fin(Be) and Qe > 0 and Be > 0 and fin(spot) and spot > 0 and fin(tok) and tok > 0)
    guard = (not bad) and (net / tok) / spot > guard_ratio
    out: dict[str, Any] = {"net": net, "tokens": tok, "guarded": guard, "entry_invalid": bad, "exit_invalid": False, "exit_cause": 0, "stranded": False, "proceeds": None}
    if guard:
        return {**out, "pnl": -float(send), "filled": False}
    if bad:
        return {**out, "pnl": -stake - send - rent_stranded, "filled": True, "stranded": True}
    cause = 0
    if not (fin(Qx) and Qx > 0 and fin(Bx) and tok < Bx):
        cause = 2
    tokh = tok / (1 + h_in)
    Qa = Qx + net
    Ba = 1.0 if cause else Bx - tok
    gross = tokh * Qa / (Ba + tokh)
    if not cause and not (gross <= Qa):
        cause = 7
    if not cause and k_tol is not None and Qx * Bx < Qe * Be * (1 - k_tol):
        cause = 8
    sv = gross * (1 - fee) * (1 - h_out)
    out.update(exit_invalid=bool(cause), exit_cause=cause, proceeds=sv)
    if cause or sv < send:
        return {**out, "pnl": -stake - send - rent_stranded, "filled": True, "stranded": True}
    return {**out, "pnl": sv - stake - 2 * send - rent, "filled": True}


# ------------------------------------------------------------------------------------------------------------------------ entry gates and the per-pool simulation
def entry_gates(P: Pool, i: int) -> bool:
    """G1 sell, G3 age, E1, E2, G6, G7, G8 on the print i (reserves after it). G5 (open position, cooldown) is the caller's."""
    if P.buy[i]:
        return False
    bt = P.bts[i]
    if not AGE_LO <= bt - P.M <= AGE_HI:
        return False
    q, r = P.q[i], P.r[i]
    if q <= 0 or r <= 0:
        return False
    qf = (q * r) / P.supply
    if not (q - qf <= Q_STAR and qf >= Q_FLOOR_MIN):
        return False
    lo = bisect_left(P.hidx, P.hidx[i] - (HIST_HOURS - 1))
    if i - lo <= 0 or P.bts[i - 1] < bt - ALIVE_S:  # G9 window, G6
        return False
    j24 = bisect_left(P.bts, bt - 86400, lo, i + 1)
    if float(sum(P.wsol[j24:i + 1])) < VOL_MIN:  # G7
        return False
    j6 = bisect_right(P.bts, bt - ALIVE_S, lo, i + 1) - 1
    if j6 < lo or not P.spot[j6] > 0:
        return False
    return P.spot[i] / P.spot[j6] < R6_MAX  # G8


def simulate_pool(P: Pool, clock: Clock, stake_sol: float, lat_s: float, cut: int) -> dict[str, Any]:
    stake = stake_sol * 1e9
    fee = P.fee_rate / 1e6
    rows: list[dict[str, Any]] = []
    unfilled_after_cut = 0
    blocked_until = -math.inf
    n = len(P.slots)
    for i in range(n):
        if P.bts[i] < blocked_until or not entry_gates(P, i):
            continue
        bt = P.bts[i]
        ss = clock.slot_s(bt)
        k = F.k_slots(lat_s, ss)
        x = P.slots[i] + k
        if bt + lat_s >= cut:
            unfilled_after_cut += 1
            continue
        je = P.state_at_slot(x)
        Qe, Be = float(P.q[je]), float(P.r[je])
        spot = P.spot[i]
        net = stake * (1 - fee)
        tok = Be * net / (Qe + net) if Qe + net else float("nan")
        # ssb / nearby (DECLARE s6): buys in the landing slot, buy lamports over [x - round(2 / slot_s), x]
        w = int(round(F.NEARBY_WINDOW_S / ss))
        a = bisect_left(P.slots, x)
        ssb = sum(1 for j in range(a, bisect_right(P.slots, x)) if P.buy[j])
        lo2 = bisect_left(P.slots, x - w)
        nearby = float(sum(P.wsol[j] for j in range(lo2, bisect_right(P.slots, x)) if P.buy[j]))
        T = bt + HOLD_S + LEG_S
        mark = (Qe + net) / (Be - tok) if (math.isfinite(tok) and 0 < tok < Be) else float("nan")
        reason, dslot, dtime = "time_24h", None, T
        if math.isfinite(mark) and mark > 0:
            for j in range(bisect_left(P.slots, x), n):
                if P.bts[j] > T:
                    break
                bp = P.r[j] - tok
                if bp > 0 and (P.q[j] + net) / bp >= TP * mark:
                    reason, dslot, dtime = "tp_1.5x", P.slots[j], P.bts[j]
                    break
        open_end = dtime >= cut
        if dslot is None:
            dslot = clock.clock_slot(T)
        if open_end:
            jx = n - 1  # last known state at cut
        else:
            jx = P.state_at_slot(dslot + exit_lag(clock.slot_s(dtime)))
        rt = cpmm_round_trip(stake, Qe, Be, spot, float(P.q[jx]), float(P.r[jx]), fee)
        rows.append({"pool": P.id, "mig_day": day_of(P.M), "entry_bt": bt, "day": day_of(bt), "pnl": rt["pnl"], "filled": rt["filled"], "stranded": rt["stranded"], "guarded": rt["guarded"],
                     "ssb": float(ssb), "nearby": nearby, "reason": reason, "exit_time": dtime, "open_end": open_end, "entry_slot": P.slots[i], "landing_slot": x})
        blocked_until = dtime + COOLDOWN_S
    return {"rows": rows, "unfilled_after_cut": unfilled_after_cut}


# ------------------------------------------------------------------------------------------------------------------------ cell statistics
def _drop_top(vals: list[float], n: int) -> float | None:
    return None if len(vals) <= n else float(sum(sorted(vals)[:-n]))


def leg_stats(rows: list[dict[str, Any]], leg: np.ndarray, D: float) -> dict[str, Any]:
    """rows: the non-open rows of a cell; leg: the fail-leg value per row (lamports). Trades = filled rows."""
    idx = [i for i, r in enumerate(rows) if r["filled"]]
    v = [float(leg[i]) for i in idx]
    guard_cost = float(sum(leg[i] for i, r in enumerate(rows) if not r["filled"]))
    by_day: dict[str, float] = {}
    by_pool: dict[str, float] = {}
    by_mig: dict[str, float] = {}
    for i in idx:
        r = rows[i]
        by_day[r["day"]] = by_day.get(r["day"], 0.0) + float(leg[i])
        by_pool[r["pool"]] = by_pool.get(r["pool"], 0.0) + float(leg[i])
        by_mig[r["mig_day"]] = by_mig.get(r["mig_day"], 0.0) + float(leg[i])
    tot = float(sum(v))
    s = 1e9
    top3 = _drop_top(v, 3)
    top_pool = (tot - max(by_pool.values())) if by_pool else None
    top_mig = (tot - sum(sorted(by_mig.values())[-3:])) if by_mig else None
    bl = F.boot_lo(np.array(v)) if v else None
    return {"trades": len(v), "mean_sol": (tot / len(v) / s) if v else None, "total_sol": tot / s, "guard_cost_sol": guard_cost / s, "total_incl_guard_sol": (tot + guard_cost) / s,
            "total_ex_top3_sol": None if top3 is None else top3 / s, "total_ex_top3_incl_guard_sol": None if top3 is None else (top3 + guard_cost) / s,
            "per_day_sol": {d: x / s for d, x in sorted(by_day.items())}, "distinct_days": len(by_day), "positive_days": sum(1 for x in by_day.values() if x > 0),
            "total_ex_top_pool_sol": None if top_pool is None else top_pool / s, "total_ex_top3_mig_days_sol": None if top_mig is None else top_mig / s,
            "boot_lo_sol": None if bl is None else bl / s, "trades_per_day": len(v) / D, "trades_per_migration_day": {d: sum(1 for i in idx if rows[i]["mig_day"] == d) for d in sorted(by_mig)}}


def price_cell(pools: list[Pool], clock: Clock, stake: float, lat: float, cut: int, D: float) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    unf = 0
    triggered = 0
    for P in pools:
        res = simulate_pool(P, clock, stake, lat, cut)
        unf += res["unfilled_after_cut"]
        triggered += bool(res["rows"] or res["unfilled_after_cut"])
        rows += res["rows"]
    rows.sort(key=lambda r: (r["entry_bt"], r["pool"]))
    closed = [r for r in rows if not r["open_end"]]
    open_n = len(rows) - len(closed)
    pnl = np.array([r["pnl"] for r in closed], float)
    filled = np.array([r["filled"] for r in closed], bool)
    ssb = np.array([r["ssb"] for r in closed], float)
    nb = np.array([r["nearby"] for r in closed], float)
    out: dict[str, Any] = {"stake_sol": stake, "lat_s": lat, "rows_closed": len(closed), "open_at_run_end": open_n, "unfilled_guarded": int((~filled).sum()) if len(closed) else 0,
                           "unfilled_after_cut": unf, "pools_triggered": triggered, "stranded": sum(1 for r in closed if r["stranded"]),
                           "exit_reasons": {k: sum(1 for r in closed if r["reason"] == k) for k in ("tp_1.5x", "time_24h")}}
    if not len(closed):
        out.update({"flat": leg_stats([], np.array([]), D), "press": leg_stats([], np.array([]), D), "press_intercept": None, "filled_no_fail_mean_sol": None})
        out["fifo_k4_report_only"] = fifo_cap([])
        return out
    flat, press, c, _p = F.fail_legs(pnl, filled, ssb, nb)
    out["flat"], out["press"], out["press_intercept"] = leg_stats(closed, flat, D), leg_stats(closed, press, D), c
    nf = pnl[filled]
    out["filled_no_fail_mean_sol"] = float(nf.mean() / 1e9) if len(nf) else None  # no fail blend
    out["fifo_k4_report_only"] = fifo_cap([(int(r["entry_bt"] * 1000), int(r["exit_time"] * 1000)) for r in closed])
    return out


def kill_line(cell: dict[str, Any]) -> dict[str, Any]:
    """DECLARE s9 at the fixed cell. K3 first (a stop, not a pass), then K1, K2, K4 under BOTH legs. PASS is in-sample and is not a promotion."""
    legs = ("flat", "press")
    filled = cell["flat"]["trades"]
    k1 = all(cell[g]["mean_sol"] is not None and cell[g]["mean_sol"] > 0 for g in legs)
    k2 = cell["flat"]["trades_per_day"] >= K2_MIN_PER_DAY
    k3 = filled >= K3_MIN_FILLED
    k4 = all(cell[g]["total_ex_top3_incl_guard_sol"] is not None and cell[g]["total_ex_top3_incl_guard_sol"] > 0 for g in legs)
    verdict = "NOT_DECIDABLE" if not k3 else ("PASS_IN_SAMPLE" if (k1 and k2 and k4) else "KILLED")
    return {"cell": {"stake_sol": cell["stake_sol"], "lat_s": cell["lat_s"]}, "K1": k1, "K2": k2, "K3": k3, "K4": k4, "filled": filled, "verdict": verdict}


# ------------------------------------------------------------------------------------------------------------------------ driver
def run(fd: Path, migs: list[dict[str, Any]], run_end: int) -> dict[str, Any]:
    if run_end <= WIN_START:
        raise Refused("run_end_not_after_window_start")
    recs, cp = load_fetch(fd, migs)
    pools = [Pool(r) for r in recs]
    allsw = [(b, s) for P in pools for b, s in zip(P.bts, P.slots)]
    clock = Clock(pools, allsw)
    uni = [P for P in pools if P.reason is None]
    excl: dict[str, int] = {}
    for P in pools:
        if P.reason is not None:
            excl[P.reason] = excl.get(P.reason, 0) + 1
    D = (run_end - WIN_START) / 86400.0
    cells = {}
    for st in STAKES:
        for lat in LATENCIES:
            cells[f"{st:.2f}@{lat}"] = price_cell(uni, clock, st, lat, run_end, D)
    audit: dict[str, dict[str, Any]] = {}
    for P in pools:
        a = audit.setdefault(str(P.amm_config), {"pools": 0, "excluded_fee": 0, "fee_rate_ppm": None})
        a["pools"] += 1
        a["excluded_fee"] += P.reason in ("fee_mismatch", "fee_unverified")
        if P.reason is None:
            a["fee_rate_ppm"] = P.fee_rate
    kc = cells[f"{KILL_CELL[0]:.2f}@{KILL_CELL[1]}"]
    return {"statement": STAT_NOTE, "declare": "docs/research/floor-cpmm/DECLARE.md @9f784ac7 (blob c4719da6)", "window": {"start": WIN_START, "cut": run_end, "D_days": D},
            "pools_fetched": len(pools), "universe": len(uni), "exclusions": excl, "non_swap_ix_rows": sum(P.n_ix for P in pools),
            "rows_dropped_before_window": sum(P.dropped_before_window for P in pools), "reserve_variants": {"event_vault_before": "used", "raw_vault": "not_computed: SwapEvent vaults exclude accrued fees; PoolState not fetched"},
            "cells": cells, "kill_line": kill_line(kc), "fee_audit_by_amm_config": audit, "credits_used": cp.get("credits_used")}


def summary(res: dict[str, Any]) -> str:
    k = res["kill_line"]
    lines = [f"FLOOR-CPMM replay (in-sample, not blind; not a gate item). pools {res['pools_fetched']}, universe {res['universe']}, exclusions {res['exclusions']}, credits {res['credits_used']}"]
    for name, c in res["cells"].items():
        lines.append(f"  {name}: trades {c['flat']['trades']} guarded {c['unfilled_guarded']} open_end {c['open_at_run_end']} | mean SOL flat {c['flat']['mean_sol']} press {c['press']['mean_sol']} "
                     f"| ex-top3 flat {c['flat']['total_ex_top3_incl_guard_sol']} press {c['press']['total_ex_top3_incl_guard_sol']}")
    lines.append(f"KILL LINE at {k['cell']}: K1 {k['K1']} K2 {k['K2']} K3 {k['K3']} K4 {k['K4']} VERDICT {k['verdict']}")
    lines.append("A pass is not a promotion and does not say the book makes money.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch-dir", required=True)
    ap.add_argument("--migrations", required=True, help="the JSON list the fetch ran with ({pool, bt, ...})")
    ap.add_argument("--run-end", required=True, help="the cut, ISO UTC; must equal the fetch's --run-end")
    ap.add_argument("--out", help="write the JSON result here")
    a = ap.parse_args(argv)
    try:
        migs = C.load_window_migrations(a.migrations)
        res = run(Path(a.fetch_dir), migs, iso_ts(a.run_end))
    except (Refused, L.Refused) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    txt = json.dumps(res, indent=1, sort_keys=True, default=float)
    if a.out:
        Path(a.out).write_text(txt + "\n")
    print(txt)
    print(summary(res), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
