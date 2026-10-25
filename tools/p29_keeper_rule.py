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

from tools.arena_screen import Cell, ExitSpec, HourRows, MIN_POOL_AGE_S, RunState, V_HI, V_LO, post_price

T_TAG_S = 1.0
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

    def __init__(self, tagger: Callable[[str, str], dict], mode: str = "tagged", control_keys: set | None = None, allowed_days: dict | None = None) -> None:
        if mode not in ("tagged", "control"):
            raise ValueError("mode")
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
        k_dec = int(math.ceil(T_TAG_S * rate)) if rate > 0 else 0
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
        dec = s3 + k_dec
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
            jd = int(np.searchsorted(a["sl"], dec, "right")) - 1
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
        c[f"{kind}_{'pass' if rec['pass'] else 'skip_' + str(rec['reason'])}"] += 1
        (self.events if kind == "tagged" else self.cands).append(rec)
        if rec["pass"] and ((self.mode == "tagged" and kind == "tagged") or (self.mode == "control" and kind == "control" and (pool, s3) in self.control_keys)):
            out.append((pool, dec, ExitSpec(HOLD_S, tp=rec["tp"], sl=STOP, cell=CELL)))


# ------------------------------------------------------------------------------------------------------------------------------------- placebo matcher
def decile_edges(values: list[float]) -> np.ndarray:
    return np.quantile(np.asarray(values, float), np.linspace(0.1, 0.9, 9)) if values else np.zeros(9)


def decile(x: float, edges: np.ndarray) -> int:
    return int(np.searchsorted(edges, x, "right"))


def match_controls(events: list[dict], cands: list[dict]) -> tuple[list[tuple[dict, dict]], list[dict]]:
    """Type (ii) cross-pool control, deterministic. events/cands: passing records (pass True). One control per event, no reuse. Constraints: same UTC day, a DIFFERENT pool, candidate
    not within 600 s of any tagged event of ITS OWN pool, total sold SOL within +-25%, duration within +-15 s, distinct sellers within +-1, d within +-2 pts, same reserve decile (deciles
    over the pooled events + candidates). Score = |tot/etot - 1|/0.25 + |dur diff|/15 + |seller diff| + |dd|/0.02; ties: earliest bt, then smallest pool id. Events are taken in (bt, pool) order.
    Returns (pairs, unmatched events)."""
    ev = sorted([e for e in events if e["pass"]], key=lambda e: (e["bt"], e["pool"]))
    cs = [c for c in cands if c["pass"]]
    edges = decile_edges([r["reserve_sol"] for r in ev + cs])
    own_t: dict[str, list[int]] = {}
    for e in ev:
        own_t.setdefault(e["pool"], []).append(e["bt"])
    by_day: dict[str, list[dict]] = {}
    for c in cs:
        if any(abs(c["bt"] - t) < 600 for t in own_t.get(c["pool"], ())):
            continue
        by_day.setdefault(c["day"], []).append(c)
    used: set = set()
    pairs, un = [], []
    for e in ev:
        best, bkey = None, None
        ed = decile(e["reserve_sol"], edges)
        for c in by_day.get(e["day"], ()):
            ck = (c["pool"], c["slot"])
            if ck in used or c["pool"] == e["pool"] or decile(c["reserve_sol"], edges) != ed:
                continue
            if e["total_sol"] <= 0 or abs(c["total_sol"] / e["total_sol"] - 1) > 0.25 or abs(c["duration_s"] - e["duration_s"]) > 15 or abs(c["sellers"] - e["sellers"]) > 1 or abs(c["d"] - e["d"]) > 0.02:
                continue
            score = abs(c["total_sol"] / e["total_sol"] - 1) / 0.25 + abs(c["duration_s"] - e["duration_s"]) / 15 + abs(c["sellers"] - e["sellers"]) + abs(c["d"] - e["d"]) / 0.02
            key = (score, c["bt"], c["pool"])
            if bkey is None or key < bkey:
                best, bkey = c, key
        if best is None:
            un.append(e)
        else:
            used.add((best["pool"], best["slot"]))
            pairs.append((e, best))
    return pairs, un


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
