"""EXP-025 P7 line 2 (fee tier), BUY side, amended (EXP-025 Amendment 7 B). Sells unchanged. Pure integer functions on ints and dicts.

The amended rule, as pinned:
  1. Comparable buys. A sampled buy of the main 1,000 is in line 2's buy denominator only if it is a comparable buy of line 1 under
     Amendment 6 item 1: no `zero_sol`, and `ix_name` exactly `buy` or `buy_v2`. Every other buy is in neither denominator, with cause
     `zero_sol`, `buy_exact_quote_in` (prefix: v1 and v2), `no_ix_name` (missing, null or empty) or `ix_not_listed` (any other name,
     `multi_hop_swap` included), tested in that order and decided from the sampled raw tape row before any fetch, by
     p7_buy_amend.p7_raw_exclusion (reused, not copied). Then, as before, a buy with sol <= 0, tok <= 0 or tok >= b is skipped.
  2. Relation. Q, b, tok and sol are integers (Q = quote_reserve_mapped + V0; b, tok, sol the adapter row's base_reserve, token_raw,
     sol_lamports). ppm is the read's own tier at (Q, b) in integer ppm (tier_ppm of exp025_read.fee(Q, b)). With
         qin = ceil(Q * tok / (b - tok)) = -((-Q * tok) // (b - tok))
     a comparable buy matches iff  abs(sol * 10**6 - qin * (10**6 + ppm)) <= 100 * qin,  i.e. the implied fee on the curve input,
     sol / qin - 1, is within 1 bp of the tier. This REPLACES 1 - tok * Q / (b - tok) / sol for buys; the old relation is not an alternative.
  3. Tolerance. 1 bp of qin (event_v_map.P7_TOLERANCE_BP, unchanged). No unit allowance.
  4. Unchanged: the sell relation and its match, the main draw and frame, the 75% / 90% bars (an empty side fails), the misses
     no_adapter_row / identity_mismatch / field_missing / degenerate, and the consequence (R14).

p7_buy_amend.py is loaded the way it loads event_v_map.py: only after its sha256 equals the pinned digest below (ImportError otherwise),
so this file's own sha256 pins the exclusion it runs. Nothing here opens a file after import, and nothing computes a P&L.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os

# sha256 of ARTIFACTS/exp025/p7_buy_amend.py as pinned in SHA256SUMS and EXP-025 Amendment 6 B (git blob 24dc5ede16125099f67907d9a2ac30cbb90ac934).
P7_BUY_AMEND_SHA256 = "ed3005f083d80bba768292a8ff6adf4b4220370e01760a540034c6d1bcace31b"


def _load_p7_buy_amend():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "p7_buy_amend.py")
    with open(path, "rb") as fh:
        got = hashlib.sha256(fh.read()).hexdigest()
    if got != P7_BUY_AMEND_SHA256:
        raise ImportError(f"{path}: sha256 {got} != pinned {P7_BUY_AMEND_SHA256}")
    spec = importlib.util.spec_from_file_location("exp025_p7_buy_amend_for_line2", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


AM = _load_p7_buy_amend()
EV = AM.EV

# reused unchanged
P7_BUY_IX_WHITELIST = AM.P7_BUY_IX_WHITELIST                     # ("buy", "buy_v2")
P7_TOLERANCE_BP = EV.P7_TOLERANCE_BP                             # 1.0
TOLERANCE_PER_QIN = 100                                          # 1 bp of qin on the 10**6 scale: 10**6 * P7_TOLERANCE_BP / 10**4
if TOLERANCE_PER_QIN * 10**4 != int(10**6 * P7_TOLERANCE_BP):    # the pinned constant and the integer form must agree (fail closed)
    raise ImportError("P7_TOLERANCE_BP is not 1 bp: the integer tolerance of line 2 would not be the pinned one")

# amended
LINE2_BUY_EXCLUSIONS = ("zero_sol", "buy_exact_quote_in", "no_ix_name", "ix_not_listed")   # p7_raw_exclusion's buy causes, in its order
LINE2_BY_NAME = ("buy_exact_quote_in", "ix_not_listed")          # causes counted per ix_name as well
LINE2_MISS_CAUSES = ("no_adapter_row", "identity_mismatch", "field_missing", "degenerate")   # unchanged: misses in the denominator
LINE2_SKIPPED = "buy_skipped"                                    # unchanged: the skip rule (not in the denominator)
# tools/paper_curve_math.PUMPSWAP_SOL_FEE_TIERS ppm column (= exp025_read TIER_P = common2.TIERS): the only values tier_ppm accepts
TIER_PPMS = (12_500, 12_000, 11_500, 11_000, 10_500, 10_000, 9_500, 9_000, 8_500, 8_000, 7_500, 7_000, 6_500, 6_000, 5_500, 5_250, 5_000,
             4_750, 4_500, 4_250, 4_000, 3_750, 3_500, 3_250, 3_000)


def _is_int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _require_ints(**kw) -> None:
    bad = [k for k, v in kw.items() if not _is_int(v)]
    if bad:
        raise TypeError(f"line 2 integer law: {', '.join(bad)} must be int (not bool, not float)")


def line2_buy_exclusion(row: dict) -> str | None:
    """Why a SAMPLED buy is not in line 2's buy denominator (one of LINE2_BUY_EXCLUSIONS), or None if it is comparable.
    Decided from the sampled raw tape row alone (side, zero_sol, ix_name), by p7_buy_amend.p7_raw_exclusion. Only buys are asked."""
    if row.get("side") != "buy":
        raise ValueError("line2_buy_exclusion is for sampled buys only: line 2's sells are unchanged")
    cause = AM.p7_raw_exclusion(row)
    if cause is not None and cause not in LINE2_BUY_EXCLUSIONS:
        raise ValueError(f"unexpected exclusion {cause!r} for a buy")
    return cause


def line2_buy_skip(sol: int, tok: int, b: int) -> bool:
    """The unchanged skip rule (tier_lines' form): sol <= 0, tok <= 0 or tok >= b leaves the buy out of the denominator."""
    return sol <= 0 or tok <= 0 or tok >= b


def tier_ppm(frac) -> int:
    """The read's tier fraction (exp025_read.fee(Q, b)) as integer ppm. Anything that is not a table tier raises (fail closed)."""
    ppm = int(round(float(frac) * 1_000_000))
    if ppm not in TIER_PPMS:
        raise ValueError(f"tier {frac!r} is not a PumpSwap table tier")
    return ppm


def buy_quote_in(Q: int, b: int, tok: int) -> int:
    """The curve input of an exact-out buy: ceil(Q * tok / (b - tok)) in integers. Needs ints and tok < b."""
    _require_ints(Q=Q, b=b, tok=tok)
    if tok >= b:
        raise ValueError("tok >= b has no curve input (the skip rule leaves such a buy out first)")
    return -((-Q * tok) // (b - tok))


def line2_buy_hit(sol: int, tok: int, Q: int, b: int, ppm: int) -> bool:
    """One comparable, non-skipped buy: abs(sol * 10**6 - qin * (10**6 + ppm)) <= 100 * qin, all in Python ints."""
    _require_ints(sol=sol, tok=tok, Q=Q, b=b, ppm=ppm)
    if line2_buy_skip(sol, tok, b):
        raise ValueError("a skipped buy has no line-2 outcome")
    qin = buy_quote_in(Q, b, tok)
    return abs(sol * 1_000_000 - qin * (1_000_000 + ppm)) <= TOLERANCE_PER_QIN * qin


def line2_tally(pairs) -> dict:
    """pairs: one (row, (side, hit, cause)) per print of the MAIN 1,000, the outcome being the driver's line2_one. Outcomes:
      (None, False, None)              neither a sell nor a buy;
      ('buy', None, <exclusion>)       an excluded buy (cause in LINE2_BUY_EXCLUSIONS): in neither denominator, counted per cause and,
                                       for LINE2_BY_NAME, per ix_name;
      ('buy', None, 'buy_skipped')     the skip rule: not in the denominator;
      (side, bool, None | <miss>)      in the side's denominator; a miss cause (LINE2_MISS_CAUSES) is a miss counted per cause.
    Keys: sell_n, sell_ok, buy_n, buy_ok (p7_pass reads these), neither, buy_skipped, miss_by, buy_by_ix_name ({'buy'|'buy_v2': {n, ok}}),
    buy_excluded, buy_excluded_by (every cause of LINE2_BUY_EXCLUSIONS), buy_excluded_by_name ({ix_name: count} within LINE2_BY_NAME)."""
    t = {"sell_n": 0, "sell_ok": 0, "buy_n": 0, "buy_ok": 0, "neither": 0, "buy_skipped": 0, "miss_by": {c: 0 for c in LINE2_MISS_CAUSES},
         "buy_by_ix_name": {n: {"n": 0, "ok": 0} for n in P7_BUY_IX_WHITELIST}, "buy_excluded": 0,
         "buy_excluded_by": {c: 0 for c in LINE2_BUY_EXCLUSIONS}, "buy_excluded_by_name": {}}
    for row, (side, hit, cause) in pairs:
        if side is None:
            t["neither"] += 1
            continue
        if hit is None:
            if side == "buy" and cause == LINE2_SKIPPED:
                t["buy_skipped"] += 1
            elif side == "buy" and cause in LINE2_BUY_EXCLUSIONS:
                t["buy_excluded"] += 1
                t["buy_excluded_by"][cause] += 1
                if cause in LINE2_BY_NAME:
                    name = row.get("ix_name")
                    t["buy_excluded_by_name"][name] = t["buy_excluded_by_name"].get(name, 0) + 1
            else:
                raise ValueError(f"no line-2 outcome {(side, hit, cause)!r}")
            continue
        t[side + "_n"] += 1
        t[side + "_ok"] += int(bool(hit))
        if side == "buy":
            by = t["buy_by_ix_name"][row.get("ix_name")]      # a counted buy passed the whitelist: KeyError otherwise (fail closed)
            by["n"] += 1
            by["ok"] += int(bool(hit))
        if cause:
            t["miss_by"][cause] += 1
    t["buy_excluded_by_name"] = dict(sorted(t["buy_excluded_by_name"].items()))
    return t
