"""P29 keeper stop-loss absorb: a SEPARATE rule module for the arena lab kit (tools/arena_screen.py). PAPER ONLY. No key, no RPC, no wallet.

Declared in docs/research/p29-keeper-absorb/PRICING-DECLARE.md (v3). This module IMPORTS the pinned kit (Cell, ExitSpec, RunState, HourRows, post_price) and copies none of it. It does
not touch the kit's guards. It decides on rows with slot <= its decision slot only.

EVENT (tagged): >= 3 DISTINCT keeper SL fill signatures with PumpSwap sell rows in ONE pool within 60 s (block_time), closing at the 3rd distinct signature; 60 s refractory per pool.
The tag comes from a `tagger(block, hour) -> {pool: {(slot, is_buy, sol_lamports, token_raw): (signature, kind)}}` (the runner reads the hour file for signatures in the keeper fill
set; HourRows carries no signature). TP, mixed-slot and other fills are never SL events (they are excluded from tagged events and from the untagged control pool).

DECISION slot = last tagged slot + ceil(T_TAG * slots_per_s) (T_TAG = 1.0 s, declared, unmeasured). The kit adds D = 7 and the 1.9 s leg. Filters (all counted): a pre-drop print must
exist (the last print at or before first-tagged-sell - 60 s); drop d = (P_pre - P_last) / P_pre >= 3% on the V-priced post-trade basis (kit post_price); the 2% impact bar at 0.25 SOL on
the decision-slot state ((post-trade vault + V) / 1e9 >= 25); the reversion multiple tp = R / P_dec > 1 with R = P_last + 0.5 (P_pre - P_last) and P_dec the V-priced post-trade price of
the last print <= the decision slot (target already met otherwise: skipped, counted). Exit: tp, sl = 0.90 (both on the decision-slot spot, the kit's convention), hold_s = 120.
The kit refuses (and counts) pool age < 360 s and a V outside the Am.2 band.

UNTAGGED BURSTS (placebo candidates): >= 3 sell rows by >= 3 distinct traders within 60 s, none tagged (any kind), same filters; recorded with features for the matcher. mode 'control' emits
triggers only for the burst keys (pool, closing slot) in `control_keys`.
"""
from __future__ import annotations

import math
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Callable

import numpy as np

from tools import arena_stack as F
from tools.arena_screen import Cell, ExitSpec, HourRows, MIN_POOL_AGE_S, RunState, V_HI, V_LO, post_price

T_TAG_S = 1.0
LAT_S = 1.9  # the kit's deciding send-to-land leg
ENTRY_PRIMARY_S = T_TAG_S + LAT_S  # 2.9 s after the last tagged slot (plus the kit's D = 7)
ENTRY_STRESS_S = 3.0 + LAT_S  # 4.9 s: report-only stress leg (T_tag = 3.0 s on the entry)
BEST_CASE_SLOTS = 2  # report-only best case: entry lands 2 slots after the last tagged slot (plus D)
ENTRY_MODES = ("primary", "stress", "best")
N_DISTINCT = 3
WINDOW_S = 60
REFRACTORY_S = 60
D_MIN = 0.03
BAR_MIN_RESERVE_SOL = 25.0  # 2 x 0.25 / R <= 2%
STOP = 0.90
HOLD_S = 120.0
CELL = "p29"
_COLS = ("sl", "ib", "so", "tk", "q", "b", "v", "bt", "tr")


def bursts(bts, ids, n: int = N_DISTINCT, win: int = WINDOW_S, refr: int = REFRACTORY_S) -> list[tuple[int, int]]:
    """bts sorted ascending. -> [(closing index k, window start lo)]: >= n distinct ids with bts in [t - win, t]; then a refractory of refr s."""
    out, last, lo = [], -10**18, 0
    for k in range(len(bts)):
        t = bts[k]
        while bts[lo] < t - win:
            lo += 1
        if t - last > refr and len(set(ids[lo:k + 1])) >= n:
            out.append((k, lo))
            last = t
    return out


def day_of(bt: int) -> str:
    return datetime.fromtimestamp(int(bt), timezone.utc).strftime("%Y-%m-%d")


def _pool_arrays(hr: HourRows, i: int) -> dict:
    s = hr.rows(i)
    return {"sl": hr.sl[s], "ib": hr.isbuy[s], "so": hr.sol[s], "tk": hr.tok[s], "q": hr.q[s], "b": hr.b[s], "v": hr.v[s], "bt": hr.bt[s], "tr": hr.trader[s]}


class P29KeeperRule:
    name = "p29-keeper-sl-absorb"

    def __init__(self, tagger: Callable[[str, str], dict], mode: str = "tagged", control_keys: set | None = None, allowed_days: dict | None = None, entry_mode: str = "primary") -> None:
        if mode not in ("tagged", "control") or entry_mode not in ENTRY_MODES:
            raise ValueError("mode")
        self.entry_mode = entry_mode
        self.tagger, self.mode, self.control_keys = tagger, mode, set(control_keys or ())
        self.allowed_days = allowed_days  # {block: {"YYYY-MM-DD"}}; events closing on any other day (warm-up hours) are neither recorded nor emitted. None = all days.
        self.events: list[dict] = []  # tagged events with every filter outcome
        self.cands: list[dict] = []  # untagged bursts with every filter outcome
        self.counters: Counter = Counter()

    def cells(self) -> list[Cell]:
        return [Cell(CELL, cooldown_s=0.0, event_triggered=True, event_exit=False)]

    def start_block(self, state: RunState) -> None:
        state.scratch["prev_tags"] = {}

    # ---------------------------------------------------------------------------------------------------------------------------------
    def triggers(self, hour: HourRows, state: RunState) -> list[tuple[str, int, ExitSpec]]:
        tags = self.tagger(hour.block, hour.hour) or {}
        prev_tags = state.scratch.get("prev_tags", {}) if state.prev is not None else {}
        out: list[tuple[str, int, ExitSpec]] = []
        rate = hour.slots_per_s
        k_dec = rate  # slots per second of this hour; the decision slot is computed per event by decision_offset()
        for i, pool in enumerate(hour.pools.tolist()):
            cur = _pool_arrays(hour, i)
            first_bt = min(int(state.first_seen.get(pool, 10**18)), int(cur["bt"][0])) if len(cur["bt"]) else 0
            ptags = tags.get(pool, {})
            pv = None
            if state.prev is not None:
                j = state.prev.pool_index().get(pool)
                if j is not None:
                    pv = _pool_arrays(state.prev, j)
            n_prev = 0 if pv is None else len(pv["sl"])
            sells = int((~cur["ib"]).sum()) + (0 if pv is None else int((~pv["ib"]).sum()))
            old = prev_tags.get(pool, {}) if pv is not None else {}
            if sells < N_DISTINCT and not ptags and not old:
                continue
            a = cur if pv is None else {k: np.concatenate([pv[k], cur[k]]) for k in _COLS}
            n = len(a["sl"])
            tag_of: dict[int, tuple] = {}  # row -> (signature, kind); located by slot, never by a loop over all rows
            for off, ln, src in ((n_prev, n - n_prev, ptags), (0, n_prev, old)):
                for (slot, ib, so, tk), val in src.items():
                    lo = off + int(np.searchsorted(a["sl"][off:off + ln], slot, "left"))
                    r = lo
                    while r < n and a["sl"][r] == slot:
                        if bool(a["ib"][r]) == ib and int(a["so"][r]) == so and int(a["tk"][r]) == tk and r not in tag_of:
                            tag_of[r] = val
                            break
                        r += 1
            pp = None
            sl_rows = sorted(r for r, t in tag_of.items() if t[1] == "SL" and not a["ib"][r])
            if len(sl_rows) >= N_DISTINCT:
                pp = post_price(a)
                for kk, lo in bursts([int(a["bt"][r]) for r in sl_rows], [tag_of[r][0] for r in sl_rows]):
                    rc = sl_rows[kk]
                    if rc < n_prev:
                        continue  # closed in the previous hour (its own call emitted it)
                    win = sl_rows[lo:kk + 1]
                    self._event(out, "tagged", pool, a, pp, rc, win, [tag_of[r][0] for r in win], hour, k_dec, first_bt)
            sell_idx = np.flatnonzero(~a["ib"])
            if len(sell_idx) >= N_DISTINCT:
                un = sell_idx[~np.isin(sell_idx, np.fromiter(tag_of.keys(), np.int64, len(tag_of)))] if tag_of else sell_idx
                if len(un) >= N_DISTINCT and bool(np.any(a["bt"][un][2:] - a["bt"][un][:-2] <= WINDOW_S)):
                    un_rows = un.tolist()
                    for kk, lo in bursts([int(a["bt"][r]) for r in un_rows], [int(a["tr"][r]) for r in un_rows]):
                        rc = un_rows[kk]
                        if rc < n_prev:
                            continue
                        if pp is None:
                            pp = post_price(a)
                        self._event(out, "control", pool, a, pp, rc, un_rows[lo:kk + 1], [int(a["tr"][r]) for r in un_rows[lo:kk + 1]], hour, k_dec, first_bt)
        state.scratch["prev_tags"] = tags
        return out

    # ---------------------------------------------------------------------------------------------------------------------------------
    def _event(self, out: list, kind: str, pool: str, a: dict, pp: np.ndarray, rc: int, win: list, ids: list, hour: HourRows, k_dec: int, first_bt: int = 0) -> None:
        c = self.counters
        if self.allowed_days is not None and day_of(a["bt"][rc]) not in self.allowed_days.get(hour.block, ()):
            c["outside_declared_days"] += 1
            return
        c[f"{kind}_bursts_raw"] += 1
        bt_first = int(a["bt"][win[0]])
        s3 = int(a["sl"][rc])
        dec = s3 + decision_offset(k_dec, self.entry_mode)
        rec = {"kind": kind, "block": hour.block, "pool": pool, "slot": s3, "decision_slot": dec, "bt": int(a["bt"][rc]), "day": day_of(a["bt"][rc]),
               "total_sol": float(a["so"][win].sum()) / 1e9, "duration_s": int(a["bt"][rc]) - bt_first, "sellers": len(set(a["tr"][win].tolist())), "pass": False, "price_pass": False, "reason": None}
        # pre-drop print: the last print at or before first-sell - 60 s
        jp = int(np.searchsorted(a["bt"], bt_first - 60, "right")) - 1
        if jp < 0:
            rec["reason"] = "no_pre_print"
        else:
            p_pre, p_last = float(pp[jp]), float(pp[rc])
            d = (p_pre - p_last) / p_pre if p_pre > 0 else float("nan")
            rec["d"] = d
            jd = int(np.searchsorted(a["sl"], dec, "left")) - 1  # the kit's reference: the last print strictly before the decision slot
            if jd < 0:
                rec["reason"] = "no_pre_print"
                self._finish(out, kind, pool, rec, s3, dec)
                return
            qpost = a["q"][jd] + a["so"][jd] * 0.99 if a["ib"][jd] else a["q"][jd] - a["so"][jd] * 1.01
            res_sol = float(qpost + a["v"][jd]) / 1e9
            rec["reserve_sol"] = res_sol
            p_dec = float(pp[jd])
            if not math.isfinite(d) or d < D_MIN:
                rec["reason"] = "drop_lt_3pct"
            elif not (math.isfinite(res_sol) and res_sol >= BAR_MIN_RESERVE_SOL):
                rec["reason"] = "impact_bar_2pct"
            else:
                r_t = p_last + 0.5 * (p_pre - p_last)
                tp = r_t / p_dec if p_dec > 0 else float("nan")
                rec["tp"] = tp
                if not (math.isfinite(tp) and tp > 1.0):
                    rec["reason"] = "target_already_met"
                else:
                    rec["price_pass"] = True  # survives the four price filters; the kit universe (V band, pool age) is checked next, counted apart
                    v = float(a["v"][jd])
                    age = int(a["bt"][jd]) - first_bt
                    rec["v_lamports"], rec["age_s"] = v, age
                    if not math.isfinite(v):
                        rec["reason"] = "v_unknown"  # pool absent from the V map
                    elif not (V_LO <= v <= V_HI):
                        rec["reason"] = "v_out_of_band"  # a real V outside the Am.2 band (includes real 0)
                    elif age < MIN_POOL_AGE_S:
                        rec["reason"] = "pool_age_lt_360s"
                    else:
                        rec["pass"] = True
        self._finish(out, kind, pool, rec, s3, dec)

    def _finish(self, out: list, kind: str, pool: str, rec: dict, s3: int, dec: int | None = None) -> None:
        dec = rec["decision_slot"] if dec is None else dec
        self.counters[f"{kind}_{'pass' if rec['pass'] else 'skip_' + str(rec['reason'])}"] += 1
        (self.events if kind == "tagged" else self.cands).append(rec)
        if rec["pass"] and ((self.mode == "tagged" and kind == "tagged") or (self.mode == "control" and kind == "control" and (pool, s3) in self.control_keys)):
            out.append((pool, dec, ExitSpec(HOLD_S, tp=rec["tp"], sl=STOP, cell=CELL)))


def decision_offset(rate: float, entry_mode: str = "primary") -> int:
    """Slots from the last tagged slot to the decision slot such that the kit's entry (decision + D + ceil(1.9 / sps)) lands EXACTLY at the declared slot:
    primary: last tagged slot + D + ceil(2.9 / sps); stress: ... + ceil(4.9 / sps); best (report only): last tagged slot + D + 2. sps = 1 / rate."""
    sps = 1.0 / rate
    k = int(F.k_slots_vec(LAT_S, sps, "ceil"))
    if entry_mode == "primary":
        return int(F.k_slots_vec(ENTRY_PRIMARY_S, sps, "ceil")) - k
    if entry_mode == "stress":
        return int(F.k_slots_vec(ENTRY_STRESS_S, sps, "ceil")) - k
    if entry_mode == "best":
        return BEST_CASE_SLOTS - k
    raise ValueError(entry_mode)


# ------------------------------------------------------------------------------------------------------------------------------------- placebo matcher
def decile_edges(values: list[float]) -> np.ndarray:
    return np.quantile(np.asarray(values, float), np.linspace(0.1, 0.9, 9)) if values else np.zeros(9)


def decile(x: float, edges: np.ndarray) -> int:
    return int(np.searchsorted(edges, x, "right"))


D_TOL = 0.05  # matcher v2: drop within +-5 pts (v1 was +-2 pts and left 96% of OOS events unmatched; v2 is the ONE recount, there is no v3)
DUR_TOL_S = 15
TOT_TOL = 0.25
MIN_SELLERS = 3
REUSE_MAX = 3
OWN_POOL_EXCL_S = 600
SMD_MAX = 0.25
BALANCE_VARS = ("total_sol", "duration_s", "sellers", "d", "reserve_decile")


def match_controls(tagged_all: list[dict], cands: list[dict]) -> tuple[list[tuple[dict, dict]], list[dict]]:
    """Matcher v2 (PRICING-DECLARE s6), cross-pool, deterministic, greedy in (bt, pool) order. `tagged_all` = EVERY tagged event of the run (filtered or not): the own-pool exclusion applies to
    any tagged event of the candidate's pool. Events matched are the passing ones. Constraints: same UTC day, a DIFFERENT pool, candidate >= 600 s from every tagged event of its own pool,
    total sold SOL within +-25%, duration within +-15 s, candidate distinct traders >= 3, drop d within +-5 pts, same reserve decile (deciles over the pooled passing events + candidates), each
    control used at most 3 times. Score = |tot/etot - 1|/0.25 + |dur diff|/15 + |seller diff| + |dd|/0.05; ties: earliest bt, then smallest pool id, then smallest slot.
    Returns (pairs [(event, control)], unmatched events). Each event record gets `reserve_decile`."""
    ev = sorted([e for e in tagged_all if e["pass"]], key=lambda e: (e["bt"], e["pool"], e["slot"]))
    cs = [c for c in cands if c["pass"]]
    edges = decile_edges([r["reserve_sol"] for r in ev + cs])
    for r in ev + cs:
        r["reserve_decile"] = decile(r["reserve_sol"], edges)
    own_t: dict[str, list[int]] = {}
    for e in tagged_all:  # filtered or not
        own_t.setdefault(e["pool"], []).append(e["bt"])
    by_day: dict[str, list[dict]] = {}
    for c in cs:
        if c["sellers"] < MIN_SELLERS or any(abs(c["bt"] - t) < OWN_POOL_EXCL_S for t in own_t.get(c["pool"], ())):
            continue
        by_day.setdefault(c["day"], []).append(c)
    used: Counter = Counter()
    pairs, un = [], []
    for e in ev:
        best, bkey = None, None
        for c in by_day.get(e["day"], ()):
            ck = (c["pool"], c["slot"])
            if used[ck] >= REUSE_MAX or c["pool"] == e["pool"] or c["reserve_decile"] != e["reserve_decile"]:
                continue
            if e["total_sol"] <= 0 or abs(c["total_sol"] / e["total_sol"] - 1) > TOT_TOL or abs(c["duration_s"] - e["duration_s"]) > DUR_TOL_S or abs(c["d"] - e["d"]) > D_TOL:
                continue
            score = abs(c["total_sol"] / e["total_sol"] - 1) / TOT_TOL + abs(c["duration_s"] - e["duration_s"]) / DUR_TOL_S + abs(c["sellers"] - e["sellers"]) + abs(c["d"] - e["d"]) / D_TOL
            key = (score, c["bt"], c["pool"], c["slot"])
            if bkey is None or key < bkey:
                best, bkey = c, key
        if best is None:
            un.append(e)
        else:
            used[(best["pool"], best["slot"])] += 1
            pairs.append((e, best))
    return pairs, un


def balance(pairs: list[tuple[dict, dict]]) -> dict:
    """Standardised mean difference of the 5 match variables over the matched pairs (control rows counted once per pair): (mean_t - mean_c) / sqrt((var_t + var_c) / 2).
    Gate: any |SMD| > 0.25 -> the placebo is NOT_DECIDABLE."""
    out = {}
    for v in BALANCE_VARS:
        t = np.array([float(p[0][v]) for p in pairs])
        c = np.array([float(p[1][v]) for p in pairs])
        if len(t) < 2:
            out[v] = None
            continue
        sd = math.sqrt((t.var(ddof=1) + c.var(ddof=1)) / 2)
        out[v] = float((t.mean() - c.mean()) / sd) if sd > 0 else 0.0
    ok = all(x is not None and abs(x) <= SMD_MAX for x in out.values())
    return {"smd": out, "max_abs_smd": max((abs(x) for x in out.values() if x is not None), default=None), "balanced": bool(ok)}


def cluster_boot_lo(diffs, groups, B: int = 1000, seed: int = 1) -> float | None:
    """5th percentile of the mean paired difference, resampling pairs that share a control (`groups`, the control id) as ONE unit. Seed 1, 1,000 draws."""
    d = np.asarray(diffs, float)
    if len(d) == 0:
        return None
    ids = {g: i for i, g in enumerate(dict.fromkeys(groups))}
    gi = np.array([ids[g] for g in groups])
    sums, cnts = np.bincount(gi, weights=d), np.bincount(gi).astype(float)
    rng = np.random.default_rng(seed)
    pick = rng.integers(0, len(sums), size=(B, len(sums)))
    means = sums[pick].sum(1) / cnts[pick].sum(1)
    return float(np.percentile(means, 5))


def landing_disagree(a: dict, landing_slot: int, tol: float = 0.005) -> bool | None:
    """Report only, no outcome: at the first print j with slot > landing_slot, do the previous print's post-trade reserves (kit post-state convention) disagree with print j's pre-trade
    reserves by more than tol (relative, vault or base)? None when there is no previous or next print."""
    j = int(np.searchsorted(a["sl"], landing_slot, "right"))
    if j <= 0 or j >= len(a["sl"]):
        return None
    i = j - 1
    qp = a["q"][i] + a["so"][i] * 0.99 if a["ib"][i] else a["q"][i] - a["so"][i] * 1.01
    bp = a["b"][i] - a["tk"][i] if a["ib"][i] else a["b"][i] + a["tk"][i]
    return bool(abs(qp - a["q"][j]) > tol * max(a["q"][j], 1.0) or abs(bp - a["b"][j]) > tol * max(a["b"][j], 1.0))
