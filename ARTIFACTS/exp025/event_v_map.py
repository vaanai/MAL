"""EXP-025 pinned event-V mapping (quant-proof R2). Pure integer arithmetic; reads no file.

The pinned pass A (scripts/11_passA.py) prices a pool with a CONSTANT virtual reserve V = tokens.v0_lamports added to the tape's `quote_reserve`.
On October tape the vault holds unswept fees and the per-print event V moves, so the adapter writes the tape column as

    quote_reserve := vault + V(t) - V0

where
  vault = the PumpSwap quote vault balance of the pool in the PRE-trade state of the print (PumpSwap tape reserves are PRE-trade),
  V(t)  = the per-print event V in the PRE-trade state of the same print (decoder `--event-v`),
  V0    = V at the pool's first print s0 (pre-trade; pending fees are 0 at a fresh pool), the value the adapter writes to tokens.v0_lamports.

Pass A then prices with quote_reserve + V0 = vault + V(t), the same total quote the on-chain curve uses, and `qreal` (the "real quote >= 20 SOL"
feature) is the vault net of pending fees. When V(t) = V0 for every print the mapping is the identity on the vault, which is the September behaviour.
"""
from __future__ import annotations


def map_quote_reserve(vault: int, v_t: int, v0: int) -> int:
    """The pinned mapping. All three inputs are integers in lamports (PRE-trade state of one print)."""
    return int(vault) + int(v_t) - int(v0)


def pre_trade_states(post_states, v0: int, vault0: int):
    """post_states: the POST-trade (vault, V) of each print of one pool in (slot, tx_index, event_index) order.
    Returns the PRE-trade (vault, V) of each print: print 0 starts at (vault0, v0) (the fresh-pool state at s0), print i starts at the POST state of print i-1.
    This is the PRE-trade convention the mapping needs."""
    out = []
    prev = (int(vault0), int(v0))
    for post in post_states:
        out.append(prev)
        prev = (int(post[0]), int(post[1]))
    return out


def mapped_series(post_states, v0: int, vault0: int):
    """quote_reserve for each print of one pool, from POST-trade states."""
    return [map_quote_reserve(vl, v, v0) for vl, v in pre_trade_states(post_states, v0, vault0)]
