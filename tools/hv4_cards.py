#!/usr/bin/env python3
"""hv4_cards: the code half of Haiku trader v4 (HV4): scanner filters, setup tags, hard vetoes, the card, the predicate grammar, the name sanitizer.

Serves /data/mal/hunt-1008/push-1016/hunt20/haiku-v4/DESIGN.md sections 1, 2 and 3 (WATCH predicates), build item 1. PAPER research only: pure python,
no network, no keys, no model call, no file read. Nothing here trades. Nothing here says a book makes money.

    snap = state.snapshot(pool_id)                      # tools.arena_state.ArenaState.snapshot (any Mapping with the same keys works)
    prints = state.pool(pool_id).events                 # (t_ms, sol_lamports, is_buy, trader) tuples, the last 60 min
    fr = filters(snap, as_of_ms=T)                      # section 1 Lane G filters (snapshot only; cheap)
    feats = features(snap, prints, as_of_ms=T)          # flat numeric fields shared by setups, vetoes and the card
    tags = setups(feats, pool_id=..., as_of_ms=T, limiter=lim)
    vr = code_vetoes(snap, prints, chain, as_of_ms=T)   # section 2 hard vetoes, fail closed
    card = build_card(snap, prints, chain, as_of_ms=T, setup=tag)   # raises CardRefused on a filter miss or a veto
    p = Predicate.parse("ret_15m <= -0.03 AND net_flow_60m >= 3")   # WATCH predicates: a closed grammar, never eval

No import of tools.arena_state (or any other repo module): `SnapshotLike` is a structural protocol, so this file imports with the standard
library only and a test fixture can stand in for the state.

As-of discipline. Every function that reads a snapshot, a print list or chain facts takes `as_of_ms` (the scan instant T, as of the last slot
before it) and raises LeakageError if any timestamp in its inputs is later than T. There is no silent filtering: a future print is a bug in the
caller, and the card is refused. Rolling windows come from the snapshot (anchored at the state clock `now_ms`); a state clock more than
STATE_LAG_MAX_MS behind T fails the `state_fresh` filter, so a stalled feed cannot be stamped as T.

Fail closed. A missing or malformed input never passes: a missing chain fact is a veto, a null return is a null field, a null field makes a
setup condition false and a predicate term false.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Protocol, Sequence, runtime_checkable

# ---------------------------------------------------------------------------------------------------------------------------------------
# Constants: every number below is a DESIGN.md number; the section is in the comment.
# ---------------------------------------------------------------------------------------------------------------------------------------
MIN_MS = 60_000
LAMPORTS = 1_000_000_000
CARD_VERSION = "hv4-card-1"

AGE_MIN_S = 2 * 3600  # s1: age since first PumpSwap print, 2 h to 7 d (both ends inclusive, as in counts/setups.py)
AGE_MAX_S = 7 * 86400
AGE_YOUNG_MAX_S = 24 * 3600  # s1: 2-24 h is the young tier (stake cap 0.25, at most half an arm's slots)
YOUNG_STAKE_CAP = 0.25
QUOTE_FLOOR_SOL = 30.0  # s1: real quote reserve
PRINTS_15M_MIN = 20  # s1: prints in the last 15 min
SIGNERS_15M_MIN = 8
LAST_PRINT_MAX_S = 120.0
VOL_60M_MIN_SOL = 10.0  # s1: gross SOL volume over 60 min
IMPACT_RT_CAP = 0.03  # s1: round-trip impact at the intended stake
STAKES = (0.25, 0.5, 1.0)  # s3: size menu
STATE_LAG_MAX_MS = 10_000  # not in DESIGN: a state clock further behind T than this is a stalled feed (see module docstring)

SETUP_COOLDOWN_MS = 30 * MIN_MS  # s1: at most one setup per pool, per persona, per 30 min
PULLBACK_RET60_MIN = 0.10  # s1 CON PULLBACK
PULLBACK_RET15_LO, PULLBACK_RET15_HI = -0.15, -0.03
PULLBACK_NET60_MIN_SOL = 3.0
BREAKOUT_HI_FRAC = 0.98  # s1 BIG BREAKOUT
BREAKOUT_VOL_MULT = 3.0
BREAKOUT_AGE_MIN_S = 6 * 3600
SECONDWAVE_LO_OVER_HI_MAX = 0.4  # s1 BIG SECOND_WAVE
SECONDWAVE_CLOSE_OVER_LO_MIN = 1.4
SECONDWAVE_RET15_MIN = 0.10

VSTAR_THRESHOLD = 0.20  # s2 V*: t-volume-campaign REPORT.md line 64, RULE.md "VETO V*", JUDGE-4 row 5a (NOT the 0.7-over-5-min of the hunt20 summary)
VSTAR_WINDOWS = 3  # three aligned 5-min windows ending at the minute boundary te
VSTAR_WINDOW_MS = 5 * MIN_MS
WASH_NET_NUM, WASH_NET_DEN = 1, 20  # a round-trip wallet has |buy - sell| < 5% of (buy + sell): |net| * 20 < gross, in integers
QUOTE_DROP_VETO = 0.30  # s2 veto 7: real quote down >= 30% in 15 min
MAYHEM_MIN_AGE_S = 24 * 3600  # s2 veto 3
SELL_ROUTE_MIN_SELLERS = 3  # s2 sell route: non-creator sellers in the last 60 min
SELL_ROUTE_MIN_RETURN = 0.90  # s2 sell route: a simulated sell of the stake returns >= 90% of mark
NET_SELL_WINDOW_MS = 10_000  # s2 net_sell_25slots: 25 slots * 0.4 s; the print tuples carry no slot, so the window is time

SPL_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
# s2 veto 2, matched on the extension name with case and punctuation removed. TransferFee and DefaultAccountState are value-checked below.
BANNED_EXTENSIONS = frozenset({"transferhook", "permanentdelegate", "nontransferable", "pausable", "pausableconfig"})

DEFAULT_CANDLE_RETENTION_MIN = 120  # tools.arena_state.CANDLE_MIN today; 240-min returns and the 6 h fields stay null until it is raised
NAME_MAX = 24
EPS = 1e-9


class LeakageError(ValueError):
    """An input carries a timestamp later than the scan instant."""


class PredicateError(ValueError):
    """A WATCH predicate is outside the grammar."""


class CardRefused(Exception):
    """No card: a Lane G filter missed or a hard veto fired. `.reasons` is the sorted-by-check-order list of check names."""

    def __init__(self, reasons: Sequence[str]) -> None:
        super().__init__("card refused: " + ",".join(reasons))
        self.reasons = list(reasons)


@runtime_checkable
class SnapshotLike(Protocol):
    """Mirrors tools.arena_state.ArenaState.snapshot(key): a read-only mapping. Keys this module reads (all others are ignored):
    id, prog ("pumpswap"), mint, now_ms, quote_wsol (True | False | None), price, reserves{quote_sol, quote_eff_sol, v_sol, base_tokens},
    created_known, age_s, age_since_first_seen_s, last_ms, last_slot, last_trade{t_ms}, n_trades, windows{"1m","5m","15m","60m"}{buy_sol,
    sell_sol, n_buys, n_sells, unique_signers, net_flow_sol}, top_net{buyers[], sellers[]}{trader, net_sol}, creator, creator_net_sol, ath_ms,
    drawdown_from_ath, name, symbol, candles[{t_min_ms, o, h, l, c, vol_sol}], and optionally candle_retention_min (default 120)."""

    def get(self, key: str, default: Any = None) -> Any: ...

    def __getitem__(self, key: str) -> Any: ...


PrintLike = Sequence[Any]  # (t_ms: int, sol_lamports: int, is_buy: bool, trader: str); tools.arena_state.Pool.events items


# ---------------------------------------------------------------------------------------------------------------------------------------
# Text hygiene: names, symbols, ids
# ---------------------------------------------------------------------------------------------------------------------------------------
_NAME_KEEP = re.compile(r"[^A-Za-z0-9 ]+", re.ASCII)
_SPACES = re.compile(r" +")
_BASE58_ID = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}", re.ASCII)


def sanitize_name(raw: Any) -> str:
    """A token name or symbol as the model may see it: ASCII letters, digits and single spaces only, at most 24 characters.

    Everything else is dropped (not replaced), so quotes, braces, newlines, backticks, angle brackets, unicode look-alikes and zero-width
    characters cannot reach the prompt. A non-string is the empty string. The result always matches [A-Za-z0-9 ]{0,24}. It is still
    free text from an attacker: the card marks it untrusted and the prompt (never this module) tells the model not to follow it."""
    if not isinstance(raw, str):
        return ""
    s = _SPACES.sub(" ", _NAME_KEEP.sub("", raw)).strip()
    return s[:NAME_MAX].rstrip()


def is_safe_id(x: Any) -> bool:
    """A pool or wallet address as base58 text of 32 to 44 characters (what the model may be shown and may echo back)."""
    return isinstance(x, str) and _BASE58_ID.fullmatch(x) is not None


def short_addr(x: Any) -> str | None:
    """First 6 characters of a base58 address, else None (an address-shaped injection is dropped, not shown)."""
    return x[:6] if is_safe_id(x) else None


# ---------------------------------------------------------------------------------------------------------------------------------------
# Predicate: `field op number`, joined by AND, at most 4 terms, over numeric card fields. A regex grammar; no eval, no ast, no compile.
# ---------------------------------------------------------------------------------------------------------------------------------------
PRED_MAX_TERMS = 4
PRED_MAX_LEN = 200
_P_FIELD = r"[a-z][a-z0-9_]{0,39}"
_P_NUM = r"-?(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,9})?"
_P_TERM = re.compile(rf"({_P_FIELD}) *(<=|>=|<|>) *({_P_NUM})", re.ASCII)
_P_AND = re.compile(r" +(?:AND|and) +", re.ASCII)
_OPS = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b, ">=": lambda a, b: a >= b}


@dataclass(frozen=True)
class Predicate:
    """A parsed WATCH predicate. Build with Predicate.parse; `terms` is a tuple of (field, op, number)."""

    terms: tuple[tuple[str, str, float], ...]

    @classmethod
    def parse(cls, text: Any, allowed_fields: Iterable[str] | None = None) -> "Predicate":
        """Parse or raise PredicateError. `allowed_fields` defaults to CARD_NUMERIC_FIELDS (a name outside it is rejected at parse time)."""
        allowed = CARD_NUMERIC_FIELDS if allowed_fields is None else frozenset(allowed_fields)
        if not isinstance(text, str):
            raise PredicateError("predicate is not a string")
        if len(text) > PRED_MAX_LEN or not text.isascii():
            raise PredicateError("predicate too long or not ASCII")
        body = text.strip(" ")
        if not body:
            raise PredicateError("empty predicate")
        parts = _P_AND.split(body)
        if len(parts) > PRED_MAX_TERMS:
            raise PredicateError(f"more than {PRED_MAX_TERMS} terms")
        terms: list[tuple[str, str, float]] = []
        for part in parts:
            m = _P_TERM.fullmatch(part)
            if m is None:
                raise PredicateError("term is not `field op number`")
            name, op, num = m.group(1), m.group(2), float(m.group(3))
            if name not in allowed:
                raise PredicateError(f"unknown card field {name!r}")
            terms.append((name, op, num))
        return cls(tuple(terms))

    @property
    def fields(self) -> frozenset[str]:
        return frozenset(t[0] for t in self.terms)

    def canonical(self) -> str:
        return " AND ".join(f"{n} {o} {v:g}" if v == int(v) and abs(v) < 1e15 else f"{n} {o} {v!r}" for n, o, v in self.terms)

    def evaluate(self, values: Mapping[str, Any]) -> bool:
        """True iff every term holds on `values` (use numeric_fields(card)). A missing, null, boolean or non-finite value makes the term false."""
        for name, op, num in self.terms:
            v = values.get(name)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                return False
            if not _OPS[op](float(v), num):
                return False
        return True


# ---- PART 2 ----
