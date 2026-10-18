"""DEC-031 rev 2 deciding stack for the arena H5 books, as plain-python arithmetic. Paper only. No numpy, no pandas, no I/O, no r4b import.

WHY A SEPARATE MODULE. The arena's cost layer is tools/arena_paper.py (PR #708). It imports tools/r4b_book.py and tools/r4b_frozen.py, it is being revised, and it
did not yet carry the E1 floor when this was written. This module is the same stack for one PumpSwap round trip (the round trip of tools/r4b_book.round_trip, the E1
charge of tools/r4b_read.price_variant, the fail legs of tools/r4b_book.fail_legs), restated scalar and self-contained so the H5 scorer needs neither. A committed parity
set (tools/fixtures/arena_h5/parity_cases.jsonl and parity_expected.jsonl, built from the 2026-09-20 exploration replay by tools/fixtures/arena_h5/build_parity.py with
r4b_book as the reference) pins it: tools/test_arena_h5_cost.py compares this module with the reference output of that file to the lamport, and with r4b_book live where
numpy and r4b_book are importable. The parity reference is r4b_book.round_trip / fail_legs / press_intercept at the sha recorded in PARITY.json.

THE STACK (DEC-031 s1.4, rev 2)
  * 505,000 lamports per send. A buy and a sell are two sends; a filled round trip also pays rent 2,039,280.
  * PRE-trade PumpSwap reserves with V in Q; the fee tier of the pool state on each leg; the own buy re-injected into the exit state.
  * The DEC-021 haircut: tokens bought / (1 + 0.002608) and proceeds x (1 - 0.0016); and the E1 floor (DEC-021 Amendment 7 option B): per filled, non-stranded trip the
    loss attributed to live-minus-sim is the LARGER of the haircut and 384,022 lamports per 0.05 SOL of stake (linear in stake: 1,920,110 at 0.25 SOL).
  * Guard: a buy whose execution price is above 1.15 x the decision-time spot reverts and pays one send (not filled).
  * Strand class (a total loss of stake + the buy send + rent, no sell send, never dropped): an invalid entry state; an invalid exit state (C2 state, C7 real-quote bound,
    C8 k-fall); or proceeds below one send fee. This is strand "dec031". The quant-proof config ruling (#708) is strand=BOTH: every leg is also priced under strand "sc"
    (no proceeds-below-a-send test, no C8) and a cell passes only if it passes under both.
  * Fail legs: a filled send fails with probability 0.15 (flat) or p (pressure) and then costs one send fee, else it keeps its P&L. p = sigmoid(c + 0.8 ln(1 + same-slot buys)
    + 0.35 ln(1 + nearby buy SOL)); c is refit PER CELL (bot, stake, latency leg) at the read so that the mean p over the cell's filled sends is 0.289 (press_intercept).
The H5 pre-registered stack (EXP-024 s6 / EXP-026: 55,000 lamports per send, no rent, no haircut, no E1 floor, no guard, no strand class) is kept as REPORT-ONLY legs:
h5_frozen_round_trip.
"""

from __future__ import annotations

import math
from typing import Any, Sequence

from tools.paper_curve_math import pumpswap_sol_fee_ppm

STACK_ID = "DEC-031-rev2"
SEND = 505_000
RENT = 2_039_280
RENT_STRANDED = 2_039_280
H_IN = 0.002608
H_OUT = 0.0016
GUARD = 1.15
V_LO = 17_500_000_000  # Q includes V and V >= V_LO for every member pool, so a smaller Q is a negative real quote reserve
K_TOL = 1e-3
E1_FIXED_PER_REF = 384_022  # DEC-021:54, faa3192, n = 8, at E1_REF_STAKE
E1_REF_STAKE = 50_000_000
FAIL_FLAT = 0.15
PRESS_SLOPE_SLOT = 0.8
PRESS_SLOPE_SOL = 0.35
PRESS_TARGET = 0.289
H5_SEND = 55_000  # report-only: the H5 pre-registered stack
H5_PRESS_INTERCEPT = {"primary": -1.9478840529201729, "binding": -1.938758504543896}  # report-only frozen intercepts: fit on the 77 pv outcomes of the 2026-09-20 replay


def fee_ppm(q: float, b: float) -> float:
    """PumpSwap fee tier on (Q incl. V, B): mcap SOL = q / (b * 1000) * 1e9 (r4b_book.fee_ppm)."""
    return float(pumpswap_sol_fee_ppm(q / (b * 1000.0) * 1e9))


def _h5_fee(q: float, b: float) -> float:
    """tools.boostfloor_score.tier_fee: a fraction, market cap q / b * 1e6 (the same tiers; the float expression is the exploration scorer's)."""
    return pumpswap_sol_fee_ppm(q / b * 1e6) / 1e6


def e1_fixed(stake: float) -> float:
    return E1_FIXED_PER_REF * float(stake) / E1_REF_STAKE


def _finite(x: float) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(x)


STRANDS = ("dec031", "sc")


def _trip(stake: float, qe: float, be: float, spot: float, qx: float, bx: float, send: float, rent: float, h_in: float, h_out: float, strand: str = "dec031") -> dict[str, Any]:
    """tools.r4b_book.round_trip for one trip: {pnl, state, cause}. strand "dec031" = auto_stranded on (proceeds below one send) and C8 (k-fall) on, the DEC-031 strand
    class; "sc" = both off (arena_paper's strand_rule "sc": only an invalid entry and the C2 / C7 exit causes strand). A cell passes only under both (quant-proof, #708)."""
    stake = float(stake)
    bad = True
    tok = net = 0.0
    if _finite(qe) and _finite(be) and qe >= V_LO and be > 0 and _finite(spot) and spot > 0:
        net = stake * (1 - fee_ppm(qe, be) / 1e6)
        tok = be * net / (qe + net)
        bad = not (math.isfinite(tok) and tok > 0)
    if bad:
        return {"pnl": -stake - send - RENT_STRANDED, "state": "entry_invalid", "cause": None}
    if (net / tok) / spot > GUARD:
        return {"pnl": -float(send), "state": "guarded", "cause": None}
    c2 = not (_finite(qx) and qx >= V_LO and _finite(bx) and tok < bx)
    cause = None
    sv = None
    if c2:
        cause = 2
    else:
        tokh = tok / (1 + h_in)
        qa = qx + net
        ba = bx - tok
        gross = tokh * qa / (ba + tokh)
        if not gross <= qa - V_LO:
            cause = 7
        elif strand == "dec031" and qx * bx < qe * be * (1 - K_TOL):
            cause = 8
        else:
            sv = gross * (1 - fee_ppm(qa, ba) / 1e6) * (1 - h_out)
    if cause is not None:
        return {"pnl": -stake - send - RENT_STRANDED, "state": "exit_invalid", "cause": cause}
    if strand == "dec031" and sv < send:
        return {"pnl": -stake - send - RENT_STRANDED, "state": "stranded", "cause": None}
    return {"pnl": sv - stake - 2 * send - rent, "state": "filled", "cause": None}


def deciding_round_trip(stake: float, qe: float, be: float, spot: float, qx: float, bx: float, *, send: float = SEND, rent: float = RENT, h_in: float = H_IN,
                        h_out: float = H_OUT, e1: bool = True, strand: str = "dec031") -> dict[str, Any]:
    """One trip on the DEC-031 stack. (qe, be): the pool state the buy lands on (PRE-trade, Q incl. V); spot: the decision-time spot (lamports per raw token, Q/B);
    (qx, bx): the state the sell lands on. Returns {pnl, state, cause, e1_extra} with state one of filled, guarded, stranded, entry_invalid, exit_invalid. `pnl` is the
    NO-FAIL P&L in lamports after the E1 floor."""
    if strand not in STRANDS:
        raise ValueError(f"strand {strand!r}")
    t = _trip(stake, qe, be, spot, qx, bx, send, rent, h_in, h_out, strand)
    extra = 0.0
    if e1 and t["state"] == "filled":
        t0 = _trip(stake, qe, be, spot, qx, bx, send, rent, 0.0, 0.0, strand)
        hair = t0["pnl"] - t["pnl"]
        extra = max(0.0, e1_fixed(stake) - hair)
        t["pnl"] = t["pnl"] - extra
    t["e1_extra"] = extra
    return t


def is_filled(state: str) -> bool:
    """r4b_book's `filled` = not guarded (a stranded trip is filled and strand-priced)."""
    return state != "guarded"


def flat_leg(pnl: float, state: str, send: float = SEND) -> float:
    return (1 - FAIL_FLAT) * pnl - FAIL_FLAT * send if is_filled(state) else pnl


def _sig(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z)) if z >= 0 else math.exp(z) / (1.0 + math.exp(z))


def _zb(ssb: float, nb_lamports: float) -> float:
    return PRESS_SLOPE_SLOT * math.log1p(float(ssb)) + PRESS_SLOPE_SOL * math.log1p(float(nb_lamports) / 1e9)


def press_p(intercept: float, ssb: float, nb_lamports: float) -> float:
    return _sig(float(intercept) + _zb(ssb, nb_lamports))


def press_leg(pnl: float, state: str, p: float, send: float = SEND) -> float:
    return (1 - p) * pnl - p * send if is_filled(state) else pnl


def press_intercept(ssb: Sequence[float], nb_lamports: Sequence[float], filled: Sequence[bool], target: float = PRESS_TARGET) -> float:
    """Intercept c with mean sigmoid(c + zb) over the FILLED rows = target (80 bisections on [-40, 40]; r4b_book.press_intercept). No filled row: the bisection's 0 mean
    moves c to the upper end, as in the reference."""
    zb = [_zb(a, b) for a, b in zip(ssb, nb_lamports)]
    sel = [z for z, f in zip(zb, filled) if f]
    lo, hi = -40.0, 40.0
    for _ in range(80):
        mid = (lo + hi) / 2
        mp = sum(_sig(mid + z) for z in sel) / len(sel) if sel else 0.0
        if mp > target:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def h5_frozen_round_trip(stake: float, qe: float, be: float, qx: float, bx: float) -> float:
    """REPORT-ONLY: the H5 pre-registered round trip (tools.boostfloor_score.fill_round_trip, bit for bit): tier fee on each leg, own impact, 55,000 lamports on each send,
    no rent, no haircut, no E1 floor, no guard, no strand class. Returns the no-fail P&L in lamports."""
    net = stake * (1 - _h5_fee(qe, be))
    tk = be * net / (qe + net)
    q2 = qx + net
    b2 = bx - tk
    proceeds = tk * q2 / (b2 + tk) * (1 - _h5_fee(q2, b2 + tk))
    return proceeds - stake - 2 * H5_SEND
