"""arena_rule_h2ps: bot `h2ps-a1`, H2 Book A (copy a followed wallet's buy) restricted to PumpSwap pools quoted in WSOL. PAPER only; hosted by tools/arena_rules.py.

Source rule: /data/mal/hunt-1008/h2-wallets/RULE.md (frozen 2026-10-08), wallet list `frozen_wallets.txt` sha256 345d3903..., copied to data/arena/h2ps_wallets.txt and
pinned below (the rule refuses to load a list with any other sha256). The rule is a pure object: on_row / on_tick return orders, no I/O, no clock, no memory.

WHAT IS COPIED FROM RULE.md
  * Signal: a buy print by a followed wallet on a mint not yet attempted by the book. One attempt per mint, book-wide (not per wallet): the first such buy. The host
    remembers the attempt key (the mint) from the decision log, and a refused buy still counts as the mint's attempt (a refusal is a decision, DEC-031 section 2).
  * Exit: the earliest of (a) the followed wallet's first sell of the mint after its signal buy, (b) 600 s after landing.
  * Universe filter on the print: PumpSwap venue, V in [17.5e9, 17.7e9] lamports (the canonical-pool band) on the signal print.
  * Guard (buy executes only if net / tokens <= 1.15 x the post-trade spot of the signal print), venue fee tiers, own-buy re-injection, V in Q, END bound: these are the
    arena engine's (tools/arena_paper.py), not this file's.

WHERE THIS IS NOT H2 BOOK A (read before quoting any H2 number for this bot)
  1. PUMPSWAP ONLY. Book A was frozen as a bonding + PumpSwap book. Here a pump_bonding print is never a signal and never consumes a mint's attempt, so a mint the frozen
     book would have entered on the curve is entered here only if a followed wallet buys it on PumpSwap, later. Pools whose quote mint is not WSOL (27% of resolved pools in
     the arena smoke) or is not yet verified are skipped before any order: their lamport fields are not SOL. Q = quote_reserve + virtual_quote_reserves. The H2 evidence (JUDGE
     2026-10-08: flat +2.59%, pressure +2.35% at 1.3 s on Sept data) was for the mixed universe; there is no evidence for this subset, so this is a NEW bot_id with no inherited
     result. The exit signal is a followed wallet's sell of the same mint on a PumpSwap WSOL pool (a curve sell cannot follow a PumpSwap entry).
  2. COSTS are the arena's, not RULE.md's: 505,000 lamports per send (RULE.md: 55,000), rent refunded on a filled trip but lost (2,039,280) on a stranded one (the host's
     explicit stack, DEC-031 Amendment 2), the E1 floor, +26.08 / -16 bps, flat 15% and pressure legs at
     1.3 s and 1.9 s from the DECISION instant (the instant the decision row is written), never from the signal print. Only the 1.9 s leg at 0.25 SOL decides.
  3. FOLLOW-SELL LATENCY IS NOT RULE.md'S. RULE.md fills a follow-sell at max(ceil(0.55 s / sps), round(L / sps)) slots after the trigger (their sell is an observed signal: the
     same latency as the entry, 5 slots at L = 1.9 s). The arena engine charges every exit its exit lag, ceil(0.55 s / sps) and at least 2 slots, from the instant the sell
     decision is written. So the arena's follow-sell exits are up to about 1.2 s faster than RULE.md's: an optimistic bias on the (a) exits, disclosed, not modelled here.
  4. TIME CAP is evaluated on the host's 1 s tick. It is 600 s + 1.9 s after the ENTRY DECISION instant, which matches RULE.md's "deadline = landing slot + 600 s" at the 1.9 s
     leg (the engine's feed-delay slots d cancel between entry and exit). It is not exact for the 1.3 s leg.
  5. The followed wallet's sell is matched on its MINT among PumpSwap WSOL prints (RULE.md: its first sell after the signal buy). A second PumpSwap pool of the same mint is
     rare and is not special-cased.
  6. Live only, from the host: a print more than 10 s old at dispatch (a restart's catch-up over the warm-up tape) is not shown to any rule.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping

from tools.arena_rules import Buy, Ctx, Rule, Sell
from tools.arena_state import WSOL_MINT

BOT_ID = "h2ps-a1"
WALLETS_SHA256 = "345d390311579d1e61b5b622417e02d583244895c5909f19055eab42e9335499"
N_WALLETS = 50
WALLETS_PATH = Path(__file__).resolve().parent.parent / "data" / "arena" / "h2ps_wallets.txt"
STAKE_SOL = 0.25
V_LO, V_HI = 17_500_000_000, 17_700_000_000  # RULE.md: canonical pools, V in [17.5e9, 17.7e9] lamports
LEG_MS = 1_900  # the deciding latency leg
HOLD_MS = 600_000 + LEG_MS  # exit at entry decision + 1.9 s + 600 s (see module docstring, item 4)
REASON_WALLET_SELL = "wallet_sell"
REASON_TIME_CAP = "time_cap_600s"


class WalletListError(RuntimeError):
    """The wallet list is not the frozen one."""


def load_wallets(path: str | Path = WALLETS_PATH) -> frozenset[str]:
    data = Path(path).read_bytes()
    if hashlib.sha256(data).hexdigest() != WALLETS_SHA256:
        raise WalletListError(f"{path}: sha256 is not the pinned {WALLETS_SHA256[:8]}")
    wallets = frozenset(ln.strip() for ln in data.decode().splitlines() if ln.strip())
    if len(wallets) != N_WALLETS:
        raise WalletListError(f"{path}: {len(wallets)} wallets, expected {N_WALLETS}")
    return wallets


class H2PSRule(Rule):
    bot_id = BOT_ID
    deciding_stake = STAKE_SOL
    model_ids: tuple[str, ...] = ()
    uses_ticks = True  # the 600 s cap
    universe = "pumpswap_wsol"

    def __init__(self, wallets: frozenset[str]) -> None:
        self.wallets = frozenset(wallets)

    def on_row(self, ctx: Ctx, row: Mapping[str, Any]) -> list:
        # Skip everything that is not a PumpSwap trade of a followed wallet BEFORE anything else: a pump_bonding print can never become an order.
        if row.get("type") != "trade" or row.get("prog") != "pumpswap" or row.get("venue", "pumpswap") != "pumpswap":
            return []
        trader = row.get("trader")
        if trader not in self.wallets:
            return []
        pool_id = row.get("pool")
        if not pool_id:
            return []
        p = ctx.state.pool(pool_id)
        quote = row.get("quote_mint") or (p.quote_mint if p is not None else None)
        if quote != WSOL_MINT or (p is not None and p.quote_mint not in (None, WSOL_MINT)):
            return []  # not WSOL, or not verified: its lamport fields are not SOL
        mint = row.get("mint") or (p.mint if p is not None else None)
        if not mint:
            return []
        side = row.get("side")
        if side == "buy":
            if mint in ctx.attempted:
                return []
            v = row.get("virtual_quote_reserves")
            if v is None and p is not None:
                v = p.v
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not V_LO <= v <= V_HI:
                return []
            meta = {"rule": BOT_ID, "wallet": trader, "mint": mint, "signal_slot": row.get("slot"), "signal_sig": row.get("signature"),
                    "signal_t_recv_ms": row.get("t_recv_ms"), "v": v}
            return [Buy(pool_id, meta, attempt_key=mint)]
        if side == "sell":
            return [Sell(pos.position_id, REASON_WALLET_SELL) for pos in ctx.positions
                    if not pos.exiting and pos.meta.get("wallet") == trader and pos.meta.get("mint") == mint]
        return []

    def on_tick(self, ctx: Ctx, T: int) -> list:
        return [Sell(pos.position_id, REASON_TIME_CAP) for pos in ctx.positions if not pos.exiting and T >= pos.decision_ms + HOLD_MS]


def make_rule(wallets_path: str | Path | None = None) -> H2PSRule:
    """The rule with the pinned wallet list (refuses a list whose sha256 is not the frozen one)."""
    return H2PSRule(load_wallets(wallets_path or WALLETS_PATH))
