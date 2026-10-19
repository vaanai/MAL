#!/usr/bin/env python3
"""Kalshi LIP forward paper simulator (hunt18 / kalshi-lip).  Paper only; public data only.

Implements, and nothing else, the frozen rule

    DECLARE-FORWARD.md            sha256 977fa41c78517cad11359121030c81555d9700aa0f66204afc7bc9333f0af796
                                  (frozen 2026-10-19T03:33:16Z)
    DECLARE-FORWARD-CONDITIONS.md sha256 35ee7e652d470d03ae5ea0de7274c2da0caf8ab518baa4afa4dadeafc3dee63b
                                  (binding; where it differs from the DECLARE it wins)

as laid out in SIM-SPEC.md (hunt18/kalshi-lip).  Where the DECLARE/CONDITIONS and SIM-SPEC differ the
DECLARE/CONDITIONS win; every resolution is listed in ``SPEC_NOTES`` and is written to gate.json.

Nothing here places an order, signs a request or needs a key.  It does no network I/O.

Inputs (all read through the repo's own seal / manifest checks, see the section "readers"):
  --books     obrec dir(s)  orderbook + orderbook_snaps, chain-verified, SEALED parts only
  --programs  lipsnap dir   incentive_programs + incentive_polls (ok polls only), chain-verified
  --meta      dir of raw GET /markets?series_ticker=S&status=open listings (JSON), earliest capture wins
  --trades    kalshi root   trades parquet + _checkpoints (per-ticker completeness) + manifest.jsonl
  --markets   kalshi root   settled markets (result, settlement_value_dollars)
  --series    kalshi root   series table (fee_type, fee_multiplier, frequency)

Outputs in --out: units.parquet, fills.parquet, days.parquet, gate.json, table.txt (+ snaps.parquet with
--debug-snaps).  ``--dry-run`` prints structure counts only (no reward, fill or P&L is computed, and neither
the trades parquet nor any result is opened).

Time is held as integer microseconds since the epoch and prices as integer basis points of a dollar
(10,000 = $1.00, one cent = 100), so every boundary comparison is exact.
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import os
import re
import resource
import subprocess
import sys
from collections import OrderedDict, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

try:  # run as ``python -m tools.kalshi_lip_sim`` (repo root) or as a script / from tools/ in tests
    from tools import kalshi_fees
    from tools import kalshi_public as kp
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import kalshi_fees
    import kalshi_public as kp

# ------------------------------------------------------------------------------------------ the rule
FREEZE_ISO = "2026-10-19T03:33:16Z"
DECLARE_SHA256 = "977fa41c78517cad11359121030c81555d9700aa0f66204afc7bc9333f0af796"
CONDITIONS_SHA256 = "35ee7e652d470d03ae5ea0de7274c2da0caf8ab518baa4afa4dadeafc3dee63b"

US = 1_000_000
BP = 10_000                      # basis points of a dollar; one cent = 100 bp
GAS_SERIES = tuple(f"KXAAAGASD{s}" for s in ("NY", "FL", "IL", "PA", "OH", "GA", "MI", "NC"))
OTHER_SERIES = ("KXRT", "KXHORMUZWEEKLY", "KXSUEZWEEKLY", "KXPANAMAWEEKLY", "KXBABELMANDEBWEEKLY",
                "KXEOWEEK", "KXNEXTPRESSECPERM")
SERIES_ADMITTED = GAS_SERIES + OTHER_SERIES            # DECLARE 3.1: the 15 recorded series
L_US = 2 * US                    # order delay L
CAP_US = 450 * US                # CAP (2 x the median per-market gap at the freeze)
MAX_PERIOD_S = 86_760            # DECLARE 3.2: E - S0 <= 24.1 h
FIRST_POLL_MAX_LAG_S = 3_600     # DECLARE 3.3
DAILY_SLACK_S = 86_400           # DECLARE 3.4: X <= E + 24 h
BAND_BP = 1_000                  # Q3: b_y >= 0.10 and b_n >= 0.10 (inclusive)
FULL_UNIT_S = 3_600              # DECLARE 3.6
MIN_PAYOUT = 1.00                # P1 (deciding)
U15_P = 0.15
DRAWS, SEED = 1_000, 1
UNRESOLVED_AFTER_S = 48 * 3_600
POLL_REWARD_TAIL_S = 900         # CONDITIONS 10: reward stops at the last listing poll + 900 s
READ_FROM_ISO = "2026-10-24T00:00:00Z"
NOT_DECIDABLE_ISO = "2026-11-02T00:00:00Z"
EXCL_SHARE_LIMIT = 0.10          # CONDITIONS 17
COVERAGE_MIN = 0.70              # DECLARE 10
BASELINE_GAP_S = {"obrec-1019c": 222.7, "lipsnap": 900.0}   # CONDITIONS 14
PRIMARY_RUN = "obrec-1019c"      # ties go here (DECLARE 2.1)
EPS = 1e-9
CELLS = ("U0", "U15", "BQ")      # CONDITIONS 5: deciding cells
HORIZON_S = 3_600 + 450          # markouts look this far past E_eff
MARKOUT_H_S = (300, 3_600)

SPEC_NOTES = [
    "unit_id is the program id (incentive_programs.id); the U15 hash uses side labels 'yes'/'no' (lower case, the data vocabulary).",
    "Prices are integer basis points; sizes and counts are floats as recorded.  Reward is floored to the cent with a 1e-7-cent tolerance so 16.00 does not become 15.99 through float noise.",
    "Live interval of snapshot k is (ts_k+L, min(E_eff, ts_k+L+min(gap,CAP))] (DECLARE 4 and CONDITIONS 7); it ends at E_eff, not E_eff+L.  Fills and reward use the same interval.",
    "Reward accrues on the remaining resting size r(t) after fills (CONDITIONS 6); pieces are cut at each fill's created_time.",
    "Q5 inventory at decision k counts only fills with created_time <= ts_k; a fill of the previous order inside (ts_k, ts_k+L] is booked afterwards, so net inventory can exceed I_max by at most one side's size.",
    "Q2 also requires b_y + b_n < 1 (a crossed recorded book is not quoted).",
    "Validity (side total >= T) and Q4 (rival depth >= T - s) use a 1e-9 tolerance on float sizes.",
    "quoted_s (the 'full unit' test, DECLARE 3.6) is the sum of live-interval lengths of snapshots that passed Q1-Q5.",
    "Lower sensitivity: snapshot k counts only if k+1 is quoted and valid at its start; upper: k's own value, or w_k x v_(k+1) if k has none and k+1 is valid.",
    "G3 counts distinct unit-days among FULL units; 'positive' uses the sum of net of ALL opened units on those days (stubs' losses are not hidden).",
    "G6 uses worst_u = R_u - fees_u + min(settlement P&L under yes, under no) (CONDITIONS 3), per cell; total + 3 x min(min_u worst_u, 0).  A positive minimum is not credited (stricter than the literal formula only in that degenerate case).",
    "G4b clusters every gas unit (the eight KXAAAGASD* series) by date(E); other units stay in their event cluster (CONDITIONS 4).  The gas share has about one independent draw per day.",
    "Programs whose id first appears in a complete poll before the freeze are excluded (CONDITIONS 9: pre-freeze polls are read for ids only).",
    "Program parameter changes: decisions (Q4) use the first-seen parameter set; the reward is the minimum, per variant, over the parameter sets seen (CONDITIONS 10).  Reward stops at the last listing poll + 900 s when the id drops out of a later complete poll before E; quoting and fills continue.",
    "Metadata: the earliest capture of a ticker is usable only if fetched at or before the first book snapshot at/after quote_start; otherwise E_eff = E and the unit-day is date(E) (CONDITIONS 8).  The settled markets close_time is never used for E_eff, close or unit-days.",
    "Event cluster: the settled-markets event_ticker if present, else the ticker without its last '-' segment.",
    "Trades completeness: the union of [since, until] windows of full (non-tail) trades checkpoints listing the ticker as done must cover [quote_start, E_eff]; fills cannot occur before quote_start.",
    "Population is fixed by what was downloaded before the read instant R: trades files, settled-market rows and metadata captures written/fetched after R are not read (CONDITIONS 16).",
    "Source run per unit: the run with the smaller median ts gap inside the unit window, ties to obrec-1019c; a non-primary run is eligible only with no gap > CAP in the window, otherwise the unit falls back to the primary (CONDITIONS 14).",
    "BQ cell: rival depth at our level at ts_k is ahead of us; at-price prints fill us only beyond the remaining depth ahead, through-prints fill fully and clear the level; reward counts us last at our level (CONDITIONS 5).",
    "Day-floor stress cell: the $1.00 minimum is applied to the sum of central rewards of all opened units per unit-day.",
    "Capacity $/day = total net / distinct unit-days of the population; collateral per day = sum of unit collateral by unit-day (units are held to settlement, so collateral_peak = sum price x qty).",
    "Kill checks: boundaries are each 00:00Z with >= 3 distinct unit-days among units whose settlement_ts is at or before it; Book A, cell U0.",
    "Coverage: expected rounds/day = 86400 / 222.7 for obrec-1019c, polls/day = 86400 / 900 for lipsnap-1019; day coverage is the effective rounds (sum of n_snaps_ok / n_open) over the expected, scaled to the part of the day inside [freeze, R).  Two consecutive days < 70% make the read NOT_DECIDABLE.",
    "Excluded-share rule (CONDITIONS 17): excl_no_trades + excl_no_meta units that have a structurally quotable book (Q1-Q4 at zero inventory, E_eff = E) over the units that would otherwise be opened; > 10% makes the read NOT_DECIDABLE.",
]


# ---------------------------------------------------------------------------------------- time helpers
def iso_to_us(s) -> int:
    d = kp.parse_dt(s)
    return int(round(d.timestamp() * US))


def us_to_iso(us) -> str:
    return datetime.fromtimestamp(us / US, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def us_to_date(us) -> str:
    return datetime.fromtimestamp(us // US, tz=timezone.utc).strftime("%Y-%m-%d")


def ts_col_us(col) -> np.ndarray:
    """Arrow timestamp column -> int64 microseconds (nulls become 0; callers filter on a mask)."""
    a = col.cast(pa.timestamp("us", tz="UTC")).cast(pa.int64())
    return a.fill_null(0).to_numpy(zero_copy_only=False)


def bp_of(price) -> int | None:
    return None if price is None or (isinstance(price, float) and math.isnan(price)) else int(round(price * BP))


FREEZE_US = iso_to_us(FREEZE_ISO)


# --------------------------------------------------------------------------------------------- books
class Snap:
    """One recorded order-book snapshot: bids only, best (highest) price first, prices in bp."""
    __slots__ = ("ts", "yes", "no", "depth_y", "depth_n")

    def __init__(self, ts_us, yes, no):
        self.ts = ts_us
        self.yes = yes
        self.no = no
        self.depth_y = sum(q for _, q in yes)
        self.depth_n = sum(q for _, q in no)


def make_snap(ts_s, yes=(), no=()) -> Snap:
    """Test / debug helper: ts in seconds, levels as (price_dollars, size)."""
    f = lambda lv: sorted(((int(round(p * BP)), float(q)) for p, q in lv if q > 0), key=lambda x: -x[0])
    return Snap(int(round(ts_s * US)), f(yes), f(no))


class Unit:
    """One market-period (one program row on one market)."""
    def __init__(self, **kw):
        self.status = "pending"
        self.drift = False
        self.psets = []          # [(P_dollars, T, DF)], first-seen first
        self.rew_end_us = None   # reward stops here (CONDITIONS 10)
        self.__dict__.update(kw)

    @property
    def pset0(self):
        return self.psets[0]


# ------------------------------------------------------------------------------------ reward formula
def _ticks(ref_bp: int, p_bp: int) -> int:
    return int(math.floor((ref_bp - p_bp) / 100.0 + 0.5))


def side_phi(levels, T, df, our_bp, our_size, *, rival_mult=1.0, mode="prorata", capped=True):
    """DECLARE 5 steps 1-6 for ONE side.  ``levels``: recorded rival bids ``[(price_bp, size)]``, best first.
    Our order of ``our_size`` is inserted at ``our_bp`` (added to that level).  Returns (valid, phi, info).

    mode "prorata": our share inside a counted level is pro rata (deciding for U0/U15).
    mode "back":    rivals at our level are ahead of us, we count last (BQ).
    capped False:   no Target cap on the counted sizes (uncapped stress cell).
    """
    ents = [[p, q * rival_mult, 0.0] for p, q in levels]
    if our_size > 0:
        for e in ents:
            if e[0] == our_bp:
                e[2] = our_size
                break
        else:
            i = 0
            while i < len(ents) and ents[i][0] > our_bp:
                i += 1
            ents.insert(i, [our_bp, 0.0, our_size])
    total = sum(e[1] + e[2] for e in ents)
    if T <= 0 or total + EPS < T:
        return False, 0.0, {"total": total}
    thr, cum, ref = T / 5.0, 0.0, None
    for p, rq, oq in ents:
        cum += rq + oq
        if cum + EPS >= thr:
            ref = p
            break
    raw_total = our_raw = cum_before = 0.0
    for p, rq, oq in ents:
        q = rq + oq
        m = 1.0 if p >= ref else df ** _ticks(ref, p)
        if capped:
            room = max(0.0, T - cum_before)
            if mode == "prorata" or oq == 0:
                c = min(q, room)
                c_our = c * oq / q if oq > 0 else 0.0
                c_riv = c - c_our
            else:
                c_riv = min(rq, room)
                c_our = max(0.0, min(oq, room - rq))
        else:
            c_riv, c_our = rq, oq
        raw_total += (c_riv + c_our) * m
        our_raw += c_our * m
        cum_before += q
    phi = our_raw / raw_total if raw_total > 0 else 0.0
    return True, phi, {"total": total, "ref_bp": ref, "raw_total": raw_total, "our_raw": our_raw}


VARIANTS = OrderedDict([("c", {}), ("r2", {"rival_mult": 2.0}), ("r4", {"rival_mult": 4.0}),
                        ("unc", {"capped": False})])


def snapshot_value(snap, by, bn, ry, rn, T, df, **kw):
    """(valid, v): v = phi_YES + phi_NO when BOTH sides are valid, else 0 (DECLARE 5.6)."""
    vy, py, _ = side_phi(snap.yes, T, df, by, ry, **kw)
    vn, pn, _ = side_phi(snap.no, T, df, bn, rn, **kw)
    if vy and vn:
        return True, py + pn
    return False, 0.0


def floor_cent(x: float) -> float:
    return math.floor(x * 100.0 + 1e-7) / 100.0


def apply_min_payout(r: float) -> float:
    return r if r >= MIN_PAYOUT - 1e-9 else 0.0


# ------------------------------------------------------------------------------------ quote decision
class Quote:
    __slots__ = ("by", "bn", "sy", "sn", "dy", "dn")

    def __init__(self, by, bn, sy, sn, dy, dn):
        self.by, self.bn, self.sy, self.sn, self.dy, self.dn = by, bn, sy, sn, dy, dn


def decide_quote(unit, snap, Y, N, S, imax):
    """Q1..Q5 on ONE snapshot and the unit's own fills so far.  Returns (Quote | None, reason).
    Reads nothing else: not ``result``, not a trade after ``snap.ts``, not a later snapshot."""
    ts = snap.ts
    if not (unit.quote_start_us <= ts < unit.e_eff_us):
        return None, "Q1"
    if not snap.yes or not snap.no:
        return None, "Q2"
    by, bn = snap.yes[0][0], snap.no[0][0]
    if by + bn >= BP:
        return None, "Q2"
    if by < BAND_BP or bn < BAND_BP:
        return None, "Q3"
    sy = min(S, max(0.0, imax - (Y - N)))
    sn = min(S, max(0.0, imax + (Y - N)))
    T = unit.pset0[1]
    if snap.depth_y + EPS < T - sy or snap.depth_n + EPS < T - sn:
        return None, "Q4"
    return Quote(by, bn, sy, sn, snap.yes[0][1], snap.no[0][1]), "ok"


# ------------------------------------------------------------------------------------------- trades
class TradeList:
    """Prints of one ticker sorted by (created_us, trade_id); deduplicated on trade_id."""
    __slots__ = ("ts", "tid", "yes_bp", "no_bp", "qty", "taker", "block")

    def __init__(self, rows=()):
        seen, out = set(), []
        for r in rows:
            tid = r[1]
            if tid is None or tid in seen:
                continue
            seen.add(tid)
            out.append(r)
        out.sort(key=lambda r: (r[0], r[1]))
        self.ts = [r[0] for r in out]
        self.tid = [r[1] for r in out]
        self.yes_bp = [r[2] for r in out]
        self.no_bp = [r[3] for r in out]
        self.qty = [r[4] for r in out]
        self.taker = [(r[5] or "").lower() for r in out]
        self.block = [bool(r[6]) for r in out]

    def __len__(self):
        return len(self.ts)

    def span(self, lo_us, hi_us):
        """Indices of prints with lo < created <= hi."""
        return bisect.bisect_right(self.ts, lo_us), bisect.bisect_right(self.ts, hi_us)


def make_trade(ts_s, tid, yes, count, taker, block=False, no=None):
    """Test helper: a print row as ``TradeList`` takes it (prices in dollars)."""
    no = round(1 - yes, 6) if no is None else no
    return (int(round(ts_s * US)), tid, bp_of(yes), bp_of(no), float(count), taker, block)


def yes_bid_fills(taker_side, yes_bp, b_bp) -> bool:
    """DECLARE 6: a YES bid at b is hit by a taker_side 'no' print at yes_price <= b."""
    return taker_side == "no" and yes_bp is not None and yes_bp <= b_bp


def no_bid_fills(taker_side, no_bp, b_bp) -> bool:
    return taker_side == "yes" and no_bp is not None and no_bp <= b_bp


def fill_price(b_bp, print_bp) -> int:
    """Fills execute at OUR price, never the print price (DECLARE 6)."""
    return b_bp


def u15_u(unit_id, side, trade_id) -> float:
    return int(hashlib.sha256(f"1|{unit_id}|{side}|{trade_id}".encode()).hexdigest()[:8], 16) / 2 ** 32


# ------------------------------------------------------------------------------------- the unit pass
class UnitRun:
    """Result of one pass (one cell) over one unit; no outcome field is involved."""
    __slots__ = ("cell", "fills", "n_snaps_window", "n_snaps_quoted", "quoted_us", "dropped", "acc",
                 "lower", "upper", "w_quoted_us", "reasons", "decisions", "blind_gaps", "blind_us")


def run_unit(unit, snaps, trades, cell, S, imax, *, stress=True, keep_decisions=False):
    """One full pass of the rule over a unit's snapshots for one cell ('U0' | 'U15' | 'BQ').

    ``snaps``: usable snapshots of the chosen source, ts ascending (any extra ones outside
    [quote_start, E_eff) are ignored).  ``trades``: TradeList of the unit's ticker.  Inventory paths differ
    by cell, hence one pass per cell.  Never reads ``unit.result``.
    """
    assert cell in CELLS
    mode = "back" if cell == "BQ" else "prorata"
    names = list(VARIANTS) if stress else ["c"]
    kws = {n: dict(VARIANTS[n], mode=mode) for n in names}
    psets = unit.psets
    rew_end = unit.rew_end_us if unit.rew_end_us is not None else unit.e_eff_us
    fills, Ytot, Ntot, kptr, Yk, Nk = [], 0.0, 0.0, 0, 0.0, 0.0
    # per quoted snapshot: (w_clip_us, [per pset: (V_c, v0_valid, v0_c)]) ; acc[pi][name] = sum of (len_s * v)
    acc = [dict.fromkeys(names, 0.0) for _ in psets]
    per_snap = []   # index-aligned with the window list
    dropped = reasons_n = 0
    reasons = defaultdict(int)
    decisions = []
    quoted_us = n_quoted = n_win = 0
    w_quoted = []
    blind_gaps = blind_us = 0
    win = [s for s in snaps if s.ts >= unit.quote_start_us and s.ts < unit.e_eff_us]
    n_win = len(win)
    for k, sn in enumerate(win):
        ts = sn.ts
        nxt = win[k + 1].ts if k + 1 < len(win) else None
        gap = (nxt - ts) if nxt is not None else CAP_US
        if gap > CAP_US:
            blind_gaps += 1
            blind_us += gap - CAP_US
        while kptr < len(fills) and fills[kptr][4] <= ts:   # fills known at decision time
            f = fills[kptr]
            if f[1] == "yes":
                Yk += f[3]
            else:
                Nk += f[3]
            kptr += 1
        q, why = decide_quote(unit, sn, Yk, Nk, S, imax)
        reasons[why] += 1
        if keep_decisions:
            decisions.append((ts, why, None if q is None else (q.by, q.bn, q.sy, q.sn)))
        if q is None:
            per_snap.append(None)
            continue
        n_quoted += 1
        a = ts + L_US
        end = min(unit.e_eff_us, ts + L_US + min(gap, CAP_US))
        if end <= a:
            per_snap.append(None)
            continue
        quoted_us += end - a
        w_quoted.append(end - a)
        # ---- prints in (a, end]: fills, and the resting-size pieces for the reward
        ry, rn = q.sy, q.sn
        ahead_y, ahead_n = q.dy, q.dn
        pieces, cur = [], a
        i0, i1 = trades.span(a, end)
        for i in range(i0, i1):
            if trades.block[i]:
                continue
            tk, t_us = trades.taker[i], trades.ts[i]
            side = None
            if ry > EPS and yes_bid_fills(tk, trades.yes_bp[i], q.by):
                side, pb, rest = "yes", trades.yes_bp[i], ry
                at = pb == q.by
            elif rn > EPS and no_bid_fills(tk, trades.no_bp[i], q.bn):
                side, pb, rest = "no", trades.no_bp[i], rn
                at = pb == q.bn
            if side is None:
                continue
            cnt = trades.qty[i]
            if cell == "BQ":
                if side == "yes":
                    ahead = ahead_y
                else:
                    ahead = ahead_n
                if at:
                    take = max(0.0, cnt - ahead)       # only beyond the depth ahead of us
                    ahead = max(0.0, ahead - cnt)
                else:
                    take = cnt                          # through-print: fills in full, level cleared
                    ahead = 0.0
                if side == "yes":
                    ahead_y = ahead
                else:
                    ahead_n = ahead
                if take <= EPS:
                    continue
                cnt = take
            elif cell == "U15" and u15_u(unit.unit_id, side, trades.tid[i]) < U15_P:
                dropped += 1
                continue
            qty = min(rest, cnt)
            if qty <= EPS:
                continue
            pieces.append((cur, t_us, ry, rn))
            cur = t_us
            if side == "yes":
                ry -= qty
                Ytot += qty
            else:
                rn -= qty
                Ntot += qty
            fills.append((trades.tid[i], side, fill_price(q.by if side == "yes" else q.bn, pb), qty, t_us))
        pieces.append((cur, end, ry, rn))
        # ---- reward pieces (clipped at the reward end) under every parameter set and variant
        per_p, cache = [], {}
        for pi, (P, T, df) in enumerate(psets):
            tot = dict.fromkeys(names, 0.0)
            v0 = (False, 0.0)
            for pj, (lo, hi, py_, pn_) in enumerate(pieces):
                ln = max(0, min(hi, rew_end) - lo)
                if ln <= 0 and pj > 0:
                    continue
                for n in names:
                    key = (pi, n, py_, pn_)
                    if key not in cache:
                        cache[key] = snapshot_value(sn, q.by, q.bn, py_, pn_, T, df, **kws[n])
                    valid, v = cache[key]
                    tot[n] += (ln / US) * v
                    if n == "c" and pj == 0:
                        v0 = (valid, v)
            for n in names:
                acc[pi][n] += tot[n]
            per_p.append((tot["c"], v0[0], v0[1], max(0, min(end, rew_end) - a)))
        per_snap.append(per_p)
    # ---- sensitivity band (lower / upper) on the central variant, per parameter set
    lower, upper = [], []
    for pi in range(len(psets)):
        lo_s = up_s = 0.0
        for k, rec in enumerate(per_snap):
            if rec is None:
                continue
            Vk, _vv, _v0, wclip = rec[pi]
            nxt = per_snap[k + 1] if k + 1 < len(per_snap) else None
            nvalid = nxt is not None and nxt[pi][1]
            if nvalid:
                lo_s += Vk
            up_s += Vk if Vk > 0 else ((wclip / US) * nxt[pi][2] if nvalid else 0.0)
        lower.append(lo_s)
        upper.append(up_s)
    r = UnitRun()
    r.cell, r.fills, r.n_snaps_window, r.n_snaps_quoted = cell, fills, n_win, n_quoted
    r.quoted_us, r.dropped, r.acc, r.lower, r.upper = quoted_us, dropped, acc, lower, upper
    r.w_quoted_us, r.reasons, r.decisions, r.blind_gaps, r.blind_us = w_quoted, dict(reasons), decisions, blind_gaps, blind_us
    return r


def finalize_rewards(run, unit, names=None):
    """Seconds-weighted sums -> dollars.  R = floor_cent(P x sum / (2 N)); the reward of a unit under
    parameter changes is the minimum over the sets seen (CONDITIONS 10).  Returns {variant: raw R}."""
    N_s = (unit.e_us - unit.s0_us) / US
    out = {}
    for n in run.acc[0]:
        out[n] = min(floor_cent(P * run.acc[pi][n] / (2.0 * N_s)) for pi, (P, _T, _df) in enumerate(unit.psets))
    out["lower"] = min(floor_cent(P * run.lower[pi] / (2.0 * N_s)) for pi, (P, _T, _df) in enumerate(unit.psets))
    out["upper"] = min(floor_cent(P * run.upper[pi] / (2.0 * N_s)) for pi, (P, _T, _df) in enumerate(unit.psets))
    return out


# ------------------------------------------------------------------------------------ fees and P&L
def fee_for_fill(qty, price_bp, fee_type, fee_mult, rounding="cent"):
    """Maker fee of ONE fill via tools/kalshi_fees.  Returns (fee, assumed).  'quadratic' -> 0;
    'quadratic_with_maker_fees' -> 0.25 x the taker rate; flat / unknown / missing -> 0.25 x the taker
    rate and ``assumed`` = True (DECLARE 2.6)."""
    mult = 1.0 if fee_mult is None else fee_mult
    price = price_bp / BP
    if fee_type is not None:
        try:
            return kalshi_fees.maker_fee(qty, price, fee_type, mult, rounding), False
        except (NotImplementedError, ValueError):
            pass
    return kalshi_fees.maker_fee(qty, price, "quadratic_with_maker_fees", mult, rounding), True


def unit_fees(fills, fee_type, fee_mult):
    cent = micro = 0.0
    assumed = False
    for (_tid, _side, pbp, qty, _t) in fills:
        c, a1 = fee_for_fill(qty, pbp, fee_type, fee_mult, "cent")
        m, _a2 = fee_for_fill(qty, pbp, fee_type, fee_mult, "micro")
        cent += c
        micro += m
        assumed = assumed or a1
    return round(cent, 6), round(micro, 6), assumed


def settle_pnl(fills, yes_value: float) -> float:
    """YES bid fill q at b: q x (v - b); NO bid fill: q x ((1 - v) - b).  v = 1/0 for yes/no, the
    settlement value for scalar results."""
    t = 0.0
    for (_tid, side, pbp, qty, _t) in fills:
        b = pbp / BP
        t += qty * ((yes_value - b) if side == "yes" else ((1.0 - yes_value) - b))
    return t


def settle_worst_structural(fills) -> float:
    """min over the two binary outcomes of the settlement P&L (CONDITIONS 3)."""
    return min(settle_pnl(fills, 1.0), settle_pnl(fills, 0.0))


def settle_worst_case(fills) -> float:
    """Unresolved 48 h after max(E, X): every filled contract loses its price (DECLARE 8)."""
    return -sum(qty * pbp / BP for (_tid, _s, pbp, qty, _t) in fills)


def collateral(fills) -> float:
    return sum(qty * pbp / BP for (_tid, _s, pbp, qty, _t) in fills)


# -------------------------------------------------------------------------------------------- gate
def cluster_bootstrap(cluster_sum, cluster_n, draws=DRAWS, seed=SEED):
    """Event-clustered bootstrap of mean net per unit: resample clusters with replacement,
    statistic = sum(net) / count(units) of the resample; ``RandomState(seed)``, one draw matrix."""
    s, n = np.asarray(cluster_sum, float), np.asarray(cluster_n, float)
    m = len(s)
    if m == 0:
        return {"p05": None, "p95": None, "mean": None, "clusters": 0}
    idx = np.random.RandomState(seed).randint(0, m, size=(draws, m))
    stat = s[idx].sum(axis=1) / n[idx].sum(axis=1)
    return {"p05": float(np.percentile(stat, 5)), "p95": float(np.percentile(stat, 95)),
            "mean": float(stat.mean()), "clusters": m}


def _cluster_arrays(rows, key):
    groups = defaultdict(lambda: [0.0, 0])
    for r in rows:
        g = groups[key(r)]
        g[0] += r["net"]
        g[1] += 1
    ks = sorted(groups)
    return [groups[k][0] for k in ks], [groups[k][1] for k in ks]


def key_event(r):
    return r["event"]


def key_gas_day(r):
    """G4b: every gas unit sharing date(E) is ONE cluster; other units keep their event."""
    return f"GAS|{r['day_e']}" if r["gas"] else r["event"]


def key_day(r):
    return r["day"]


def evaluate_gate(rows, *, boot=True):
    """G1..G7 (+G4b) for ONE book and ONE cell.  ``rows`` = the population: opened units (settled or
    worst-case), each a dict with unit_id, event, day, day_e, gas, full, net, net_lower, worst_u."""
    n = len(rows)
    full = [r for r in rows if r["full"]]
    nets = sorted((r["net"] for r in rows), reverse=True)
    total = float(sum(nets))
    g = {"n_units": n, "n_full": len(full), "total_net": round(total, 6),
         "mean_net": round(total / n, 6) if n else None}
    g["G1"] = {"pass": len(full) >= 100, "full_units": len(full), "need": 100}
    ev_full = {r["event"] for r in full}
    g["G2"] = {"pass": len(ev_full) >= 30, "events_full": len(ev_full), "need": 30}
    days_full = sorted({r["day"] for r in full})
    day_sum = defaultdict(float)
    for r in rows:
        day_sum[r["day"]] += r["net"]
    pos = [d for d in days_full if day_sum[d] > 0]
    g["G3"] = {"pass": len(days_full) >= 5 and 2 * len(pos) > len(days_full), "days_full": len(days_full),
               "positive_days": len(pos), "need_days": 5, "day_sums": {d: round(day_sum[d], 6) for d in sorted(day_sum)}}
    if boot and n:
        b4 = cluster_bootstrap(*_cluster_arrays(rows, key_event))
        b4b = cluster_bootstrap(*_cluster_arrays(rows, key_gas_day))
        bd = cluster_bootstrap(*_cluster_arrays(rows, key_day))
    else:
        b4 = b4b = bd = {"p05": None, "p95": None, "mean": None, "clusters": 0}
    g["G4"] = dict(b4, **{"pass": b4["p05"] is not None and b4["p05"] > 0})
    g["G4b"] = dict(b4b, **{"pass": b4b["p05"] is not None and b4b["p05"] > 0})
    g["day_clustered_info"] = bd
    top3 = sum(nets[:3])
    g["G5"] = {"pass": n > 0 and (total - top3) > 0, "total_ex_top3": round(total - top3, 6), "top3": round(top3, 6),
               "total_ex_best_day": round(total - (max(day_sum.values()) if day_sum else 0.0), 6),
               "best_day": (max(day_sum, key=lambda d: (day_sum[d], d)) if day_sum else None)}
    worst = min((r["worst_u"] for r in rows), default=0.0)
    g6 = total + 3.0 * min(worst, 0.0)
    g["G6"] = {"pass": n > 0 and g6 > 0, "value": round(g6, 6), "min_worst_u": round(worst, 6)}
    lower = [r["net_lower"] for r in rows]
    g["G7"] = {"pass": n > 0 and float(np.mean(lower)) > 0, "mean_net_lower": round(float(np.mean(lower)), 6) if n else None}
    g["counts_met"] = bool(g["G1"]["pass"] and g["G2"]["pass"] and g["G3"]["days_full"] >= 5)
    g["all_pass"] = all(g[k]["pass"] for k in ("G1", "G2", "G3", "G4", "G4b", "G5", "G6", "G7"))
    return g


def kill_checks(rows, S, boundaries):
    """DECLARE 10, Book A, cell U0.  ``rows``: opened units with ``resolved_us`` (settlement known at)
    and net/event/day.  Evaluated at each boundary (a 00:00Z instant) with >= 3 distinct unit-days among
    units resolved at or before it.  K1: cumulative net <= -8 S.  K2: >= 60 opened units and the
    event-clustered bootstrap 95th percentile of the mean < 0."""
    out = []
    for b in boundaries:
        sub = [r for r in rows if r["resolved_us"] is not None and r["resolved_us"] <= b]
        if len({r["day"] for r in sub}) < 3:
            continue
        tot = sum(r["net"] for r in sub)
        bs = cluster_bootstrap(*_cluster_arrays(sub, key_event)) if len(sub) >= 60 else None
        k1 = tot <= -8.0 * S
        k2 = bs is not None and bs["p95"] < 0
        out.append({"boundary": us_to_iso(b), "n_units": len(sub), "cum_net": round(tot, 6), "K1": bool(k1),
                    "K2": bool(k2), "p95": None if bs is None else bs["p95"]})
    fired = [o for o in out if o["K1"] or o["K2"]]
    return {"checks": out, "dead_as_frozen": bool(fired), "first_fired": fired[0]["boundary"] if fired else None}
