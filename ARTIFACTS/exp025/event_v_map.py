"""EXP-025 pinned event-V mapping (quant-proof R2, S2). Pure integer arithmetic; reads no file.

The pinned pass A (scripts/11_passA.py) prices a pool with a CONSTANT virtual reserve V = tokens.v0_lamports added to the tape's `quote_reserve`.
On October tape the vault holds unswept fees and the per-print event V moves, so the adapter writes the tape column as

    quote_reserve := vault + V(t) - V0

Inputs, all from the SAME print (nothing is chained from the previous print):
  vault = the decoder's `quote_reserve` of the print: the event's pool quote token reserves, the raw vault balance BEFORE the trade.
          #467's fixtures pin this: tools/test_walk2_event_v.py::test_event_quote_reserve_equals_vault_prebalance
          (row["quote_reserve"] == the vault's preTokenBalances, on five real transactions including the October ones).
  V(t)  = the same print's event `virtual_quote_reserves`, the stored V BEFORE the trade. The same fixture file's docstring (audit R5) says
          "event V is the stored V BEFORE the trade", and its boost-buy test says "event V == the buy's pre-trade V".
  V0    = V(t) at the pool's first print s0 (pending fees are 0 at a fresh pool); the adapter writes it to tokens.v0_lamports.

Why not chain the previous print's post-trade state: a chain holds across a fee sweep (vault + V is unchanged by a sweep) but breaks across an LP
deposit or withdraw, about 1% of pools, because the tape's base_reserve includes the LP change and a chained quote would not.

Pass A then prices with quote_reserve + V0 = vault + V(t), and `qreal` is the vault net of pending fees. If V(t) = V0 for every print the mapping
returns the vault, which is the September behaviour; so an exploration day cannot test the convention, and the P7 pricing check on October prints does.
"""
from __future__ import annotations

P7_SAMPLE = 1000        # canonical-pool prints, spread evenly over the look's V-covered hours (EXP-024 section 10 P7 form)
P7_SELL_MIN = 0.75      # sells whose fee tier at the mapped Q matches the observed fee within 1 bp
P7_BUY_MIN = 0.90       # buys whose implied tier fee matches within 1 bp
P7_TOLERANCE_BP = 1.0


def map_quote_reserve(vault_pre: int, v_pre: int, v0: int) -> int:
    """The pinned mapping. All three inputs are integers in lamports, taken from the same print."""
    return int(vault_pre) + int(v_pre) - int(v0)


def mapped_series(prints, v0: int):
    """prints: one pool's prints in (slot, tx_index, event_index) order as (vault_pre, v_pre) pairs from each print's OWN event.
    Returns the quote_reserve column the adapter writes."""
    return [map_quote_reserve(vl, v, v0) for vl, v in prints]


def p7_pass(sell_ok: int, sell_n: int, buy_ok: int, buy_n: int) -> bool:
    """The outcome-blind pricing check: sells >= 75% and buys >= 90% within 1 bp. An empty side fails (nothing to show)."""
    if sell_n <= 0 or buy_n <= 0:
        return False
    return sell_ok / sell_n >= P7_SELL_MIN and buy_ok / buy_n >= P7_BUY_MIN
