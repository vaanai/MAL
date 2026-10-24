"""Integer-exact DBC single-segment swap math, as measured on Swap3 tape rows (P17, 2026-10-24). Paper/research only.
Q64.64 sqrt prices S, liquidity L (config curve), fee-excluded legs:
  BUY  (quote in q, fee-excluded):  S1 = S0 + (q << 128) // L ;  base_gross = L*(S1-S0) // (S0*S1) ;  quote consumed = ceil(L*(S1-S0) / 2^128) == q
  SELL (base in b):                 S1 = ceil(L*S0 / (L + b*S0)) ;  quote_gross = L*(S0-S1) >> 128   (user receives quote_gross - fee)
Fee: BUY pays it on the quote INPUT (collect_fee_mode 0) or on the base OUTPUT (mode 1; Swap3 fee_on_base_token true); a SELL always on the quote output."""
from __future__ import annotations


def seg_index(pts: list[tuple[int, int]], S: int) -> int | None:
    for i, (up, _l) in enumerate(pts):
        if S <= up:
            return i
    return None


def buy_exact_in(S0: int, L: int, q_excl_fee: int) -> tuple[int, int, int]:
    """-> (S1, base_gross, quote_consumed) within one segment."""
    S1 = S0 + (q_excl_fee << 128) // L
    d = S1 - S0
    return S1, (L * d) // (S0 * S1), -((-L * d) >> 128)


def sell_exact_in(S0: int, L: int, base_in: int) -> tuple[int, int]:
    """-> (S1, quote_gross) within one segment."""
    S1 = -((-(L * S0)) // (L + base_in * S0))
    return S1, (L * (S0 - S1)) >> 128


def fee_total(r: dict) -> int:
    return int(r["trading_fee"]) + int(r["protocol_fee"]) + int(r["referral_fee"])


def check_row(prev_S1: int, r: dict, pts: list[tuple[int, int]]) -> dict | None:
    """Reproduce one Swap3 row from the previous row's post price and the config curve. None if the trade crosses a segment (not handled). Else dict of bool matches."""
    S1 = int(r["next_sqrt_price"])
    i0, i1 = seg_index(pts, prev_S1), seg_index(pts, S1)
    if i0 is None or i1 is None or i0 != i1:
        return None
    L = pts[i0][1]
    fee = fee_total(r)
    if r["side"] == "buy":
        q = r["amount_in"] if r["fee_on_base_token"] else r["amount_in"] - fee
        b = r["amount_out"] + fee if r["fee_on_base_token"] else r["amount_out"]
        p1, bg, qc = buy_exact_in(prev_S1, L, q)
        return {"S1": p1 == S1, "base": bg == b, "quote": qc == q}
    p1, qg = sell_exact_in(prev_S1, L, r["amount_in"])
    return {"S1": p1 == S1, "quote": qg == r["amount_out"] + fee}
