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

import bisect
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
    """Mirrors tools.arena_state.ArenaState.snapshot(key) at claude/arena-feed (PR 709, head 43d21ff): a read-only mapping. The keys this module
    reads are SNAPSHOT_KEYS (all others are ignored); candle_retention_min is optional and absent there (ArenaState keeps CANDLE_MIN = 120).

    quote_wsol is True | False | None (None = the pool's quote mint was never verified). Every SOL-denominated figure (reserves, window SOL,
    price, impact, candle volume) is in the pool's quote unit, which is SOL only when quote_wsol is True. This module therefore treats a
    pool as having NO SOL figures unless `quote_wsol is True`: filters() fails it before evaluating any SOL check, features() returns only
    age and last-print age, and code_vetoes() adds `quote_not_wsol`."""

    def get(self, key: str, default: Any = None) -> Any: ...

    def __getitem__(self, key: str) -> Any: ...


SNAPSHOT_KEYS = (  # every ArenaState.snapshot key this module reads; the contract test checks them against the real snapshot
    "id", "prog", "mint", "now_ms", "quote_wsol", "price", "reserves", "created_ms", "created_known", "age_s", "age_since_first_seen_s", "last_ms",
    "last_slot", "last_trade", "windows", "top_net", "creator", "creator_net_sol", "ath_ms", "complete_ms", "origin_create_ms", "drawdown_from_ath",
    "name", "symbol", "candles",
)
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
        return " AND ".join(f"{n} {o} " + (f"{v:.9f}".rstrip("0").rstrip(".") or "0") for n, o, v in self.terms)

    def evaluate(self, values: Mapping[str, Any]) -> bool:
        """True iff every term holds on `values` (use numeric_fields(card)). A missing, null, boolean or non-finite value makes the term false."""
        for name, op, num in self.terms:
            v = values.get(name)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                return False
            if not _OPS[op](float(v), num):
                return False
        return True


# ---------------------------------------------------------------------------------------------------------------------------------------
# small numeric helpers
# ---------------------------------------------------------------------------------------------------------------------------------------
def _num(x: Any) -> float | None:
    """A finite float, or None (bools, strings, NaN and inf are all None)."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    return float(x) if math.isfinite(x) else None


def _ge(a: float, b: float) -> bool:
    return a >= b - EPS * max(1.0, abs(b))


def _le(a: float, b: float) -> bool:
    return a <= b + EPS * max(1.0, abs(b))


def _r(x: Any, sig: int = 6) -> Any:
    """Round a card number to `sig` significant digits (ints and non-numbers pass through): fewer tokens, same facts."""
    if isinstance(x, bool) or not isinstance(x, float):
        return x
    return float(f"{x:.{sig}g}")


def _win(snap: SnapshotLike, label: str) -> Mapping[str, Any]:
    w = (snap.get("windows") or {}).get(label)
    return w if isinstance(w, Mapping) else {}


def _norm_prints(prints: Iterable[PrintLike] | None) -> list[tuple[int, int, bool, str]] | None:
    if prints is None:
        return None
    out = []
    for p in prints:
        try:
            t, sol, buy, who = p[0], p[1], p[2], p[3]
        except (TypeError, IndexError, KeyError) as exc:
            raise ValueError("print is not (t_ms, sol_lamports, is_buy, trader)") from exc
        if isinstance(t, bool) or not isinstance(t, int) or isinstance(sol, bool) or not isinstance(sol, (int, float)) or sol < 0:
            raise ValueError("print has a bad time or amount")
        out.append((t, int(sol), bool(buy), str(who)))
    return out


# ---------------------------------------------------------------------------------------------------------------------------------------
# As-of cut
# ---------------------------------------------------------------------------------------------------------------------------------------
def assert_as_of(snap: SnapshotLike, as_of_ms: int, *, prints: Iterable[PrintLike] | None = None, chain: Mapping[str, Any] | None = None,
                 as_of_slot: int | None = None) -> None:
    """Raise LeakageError if any timestamp in the inputs is later than the scan instant `as_of_ms` (or the snapshot's last slot is after
    `as_of_slot`). Checks: snapshot now_ms / last_ms / ath_ms / created_ms / complete_ms / last_trade.t_ms, every candle start, every print,
    and the chain facts' own stamps."""
    def chk(name: str, v: Any) -> None:
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > as_of_ms:
            raise LeakageError(f"{name}={v} is after as_of_ms={as_of_ms}")

    for k in ("now_ms", "last_ms", "ath_ms", "created_ms", "complete_ms", "origin_create_ms"):
        chk(k, snap.get(k))
    lt = snap.get("last_trade")
    if isinstance(lt, Mapping):
        chk("last_trade.t_ms", lt.get("t_ms"))
    for c in snap.get("candles") or ():
        if isinstance(c, Mapping):
            chk("candle.t_min_ms", c.get("t_min_ms"))
    if prints is not None:
        for p in prints:
            chk("print.t_ms", p[0])
    if chain is not None:
        chk("chain.as_of_ms", chain.get("as_of_ms"))
        chk("chain.holders_as_of_ms", chain.get("holders_as_of_ms"))
    ls = snap.get("last_slot")
    if as_of_slot is not None and isinstance(ls, int) and not isinstance(ls, bool) and ls > as_of_slot:
        raise LeakageError(f"snapshot last_slot={ls} is after as_of_slot={as_of_slot}")


# ---------------------------------------------------------------------------------------------------------------------------------------
# Lane G filters (DESIGN section 1)
# ---------------------------------------------------------------------------------------------------------------------------------------
def impact_rt(stake_sol: float, quote_eff_sol: Any) -> float | None:
    """Round-trip price impact at `stake_sol` in a constant-product pool with effective quote reserve x = real + V: each leg's average-price
    shortfall against the spot it faces is stake / (x + stake), and the two legs add: 2 * stake / (x + stake). Fees are not in it (they are
    in the fill engine). None when the reserve is unknown."""
    x = _num(quote_eff_sol)
    if x is None or x <= 0:
        return None
    return 2.0 * stake_sol / (x + stake_sol)


def max_stake_by_impact(quote_eff_sol: Any) -> float | None:
    """Largest stake in STAKES whose round-trip impact is <= 3.0%; None when even 0.25 SOL breaches (a veto)."""
    best = None
    for s in STAKES:
        i = impact_rt(s, quote_eff_sol)
        if i is not None and _le(i, IMPACT_RT_CAP):
            best = s
    return best


def sim_sell_return(stake_sol: float, quote_eff_sol: Any) -> float | None:
    """Fraction of mark a sell of `stake_sol` worth of tokens returns from the pool: x / (x + stake) (constant product, fees excluded)."""
    x = _num(quote_eff_sol)
    if x is None or x <= 0:
        return None
    return x / (x + stake_sol)


def _age(snap: SnapshotLike) -> tuple[float | None, bool]:
    """(age in seconds, known). Known: the pool's create / pool_create row was on the tape. Otherwise the age is only a lower bound (time since
    the key was first seen), still sound for the 2 h minimum and the 24 h tiers, and the 7 d maximum can only be failed by the bound."""
    a = _num(snap.get("age_s"))
    if snap.get("created_known") and a is not None:
        return a, True
    return _num(snap.get("age_since_first_seen_s")), False


@dataclass(frozen=True)
class FilterResult:
    passed: bool
    failed: tuple[str, ...]
    checks: Mapping[str, Mapping[str, Any]]
    age_tier: str | None  # "young" (below 24 h, or only bounded below by less) | "established"
    age_known: bool
    max_stake: float | None  # min(impact cap, age cap) over STAKES; None when the 0.25 SOL impact check failed


def filters(snap: SnapshotLike, *, as_of_ms: int) -> FilterResult:
    """DESIGN section 1 Lane G filters on a snapshot, as of `as_of_ms`. Every check is evaluated and logged with its value."""
    assert_as_of(snap, as_of_ms)
    checks: dict[str, dict[str, Any]] = {}

    def add(name: str, value: Any, need: str, ok: bool) -> None:
        checks[name] = {"value": value, "need": need, "ok": bool(ok)}

    add("venue_pumpswap", snap.get("prog"), "pumpswap", snap.get("prog") == "pumpswap")
    add("quote_wsol", snap.get("quote_wsol"), "True (None = unverified fails)", snap.get("quote_wsol") is True)
    wsol = snap.get("quote_wsol") is True  # before any SOL figure: a non-WSOL pool's reserves and volumes are not SOL, so they are never read
    now = _num(snap.get("now_ms"))
    lag = (as_of_ms - now) if now is not None else None
    add("state_fresh", lag, f"state clock within {STATE_LAG_MAX_MS} ms of T", lag is not None and lag <= STATE_LAG_MAX_MS)
    age, known = _age(snap)
    add("age_min", age, f">= {AGE_MIN_S} s", age is not None and _ge(age, AGE_MIN_S))
    add("age_max", age, f"<= {AGE_MAX_S} s", age is not None and _le(age, AGE_MAX_S))
    res = snap.get("reserves") if wsol and isinstance(snap.get("reserves"), Mapping) else {}
    q = _num(res.get("quote_sol"))
    add("quote_floor", q, f">= {QUOTE_FLOOR_SOL} SOL real quote", q is not None and _ge(q, QUOTE_FLOOR_SOL))
    w15, w60 = _win(snap, "15m"), (_win(snap, "60m") if wsol else {})
    n15 = ((_num(w15.get("n_buys")) or 0) + (_num(w15.get("n_sells")) or 0)) if w15 else None
    add("prints_15m", n15, f">= {PRINTS_15M_MIN}", n15 is not None and n15 >= PRINTS_15M_MIN)
    sg = _num(w15.get("unique_signers")) if w15 else None
    add("signers_15m", sg, f">= {SIGNERS_15M_MIN}", sg is not None and sg >= SIGNERS_15M_MIN)
    last = _num(snap.get("last_ms"))
    idle = (as_of_ms - last) / 1000.0 if last is not None else None
    add("last_print", idle, f"<= {LAST_PRINT_MAX_S} s ago", idle is not None and _le(idle, LAST_PRINT_MAX_S))
    v60 = ((_num(w60.get("buy_sol")) or 0.0) + (_num(w60.get("sell_sol")) or 0.0)) if w60 else None
    add("vol_60m", v60, f">= {VOL_60M_MIN_SOL} SOL", v60 is not None and _ge(v60, VOL_60M_MIN_SOL))
    qe = _num(res.get("quote_eff_sol"))
    i025 = impact_rt(STAKES[0], qe)
    add("impact_025", i025, f"<= {IMPACT_RT_CAP} round trip at {STAKES[0]} SOL (breach = veto)", i025 is not None and _le(i025, IMPACT_RT_CAP))
    failed = tuple(k for k, c in checks.items() if not c["ok"])
    tier = None
    if age is not None:
        tier = "young" if age < AGE_YOUNG_MAX_S else "established"
    cap = max_stake_by_impact(qe)
    if cap is not None and tier != "established":
        cap = min(cap, YOUNG_STAKE_CAP)  # unknown age is tiered young unless the lower bound already clears 24 h
    return FilterResult(not failed, failed, checks, tier, known, cap)


# ---------------------------------------------------------------------------------------------------------------------------------------
# Wash: V* (DESIGN section 2; t-volume-campaign RULE.md "Features" and "Frozen book 2")
# ---------------------------------------------------------------------------------------------------------------------------------------
def vstar(prints: Iterable[PrintLike], te_ms: int) -> dict[str, float]:
    """V* = s2_15(te): the share of 15-minute gross volume that came from round-trip wallets, as the lab measured it.

    te is a UTC minute boundary. The 15 minutes are three 5-minute windows [te-5m, te), [te-10m, te-5m), [te-15m, te-10m), prints with
    t < te only. Inside each window a wallet is a round-trip wallet iff it has >= 1 buy and >= 1 sell and |buy SOL - sell SOL| < 5% of
    (buy SOL + sell SOL) (strict). V* = sum of round-trip wallets' gross / sum of all gross over the three windows; a window with no prints
    counts as 0, and no volume at all is 0.0 (no veto), as in the lab. The veto fires at V* >= 0.20. Amounts are lamports (ints) so the 5%
    test is exact: |net| * 20 < gross."""
    per: list[dict[str, list[int]]] = [{} for _ in range(VSTAR_WINDOWS)]
    for t, sol, buy, who in _norm_prints(prints) or []:
        if t >= te_ms or t < te_ms - VSTAR_WINDOWS * VSTAR_WINDOW_MS:
            continue
        w = per[(te_ms - t - 1) // VSTAR_WINDOW_MS].setdefault(who, [0, 0, 0, 0])
        w[0 if buy else 1] += sol
        w[2 if buy else 3] += 1
    gross = wash = 0
    for win in per:
        for b, s, nb, ns in win.values():
            g = b + s
            gross += g
            if nb >= 1 and ns >= 1 and abs(b - s) * WASH_NET_DEN < g * WASH_NET_NUM:
                wash += g
    return {"vstar": (wash / gross) if gross > 0 else 0.0, "wash_gross_sol": wash / LAMPORTS, "gross_sol": gross / LAMPORTS}


# ---------------------------------------------------------------------------------------------------------------------------------------
# Features: the flat numeric fields shared by setups, vetoes and the card (every window as of `as_of_ms`)
# ---------------------------------------------------------------------------------------------------------------------------------------
FEATURE_FIELDS = (
    "age_s", "quote_sol", "quote_eff_sol", "price", "n_prints_15m", "signers_15m", "last_print_s",
    "impact_rt_025", "impact_rt_050", "impact_rt_100", "max_stake_impact",
    "ret_1m", "ret_5m", "ret_15m", "ret_60m", "ret_240m", "dd_ath", "rebound", "hi_6h", "lo_6h", "close_vs_hi_6h", "vol_6h", "avg_vol_15m_6h",
    "vol_5m", "vol_15m", "vol_60m", "net_flow_5m", "net_flow_15m", "net_flow_60m",
    "n_buys_5m", "n_sells_5m", "n_buys_15m", "n_sells_15m", "n_buys_60m", "n_sells_60m",
    "unique_buyers_15m", "unique_sellers_15m", "new_wallet_buy_share_15m", "top_buyer_net_sol", "top_seller_net_sol",
    "vstar", "wash_gross_15m_sol", "top3_signer_share_60m", "creator_net_sol_60m", "creator_net_sol_life", "net_sell_25slots_sol",
    "noncreator_sellers_60m",
)
RISK_NUMERIC_FIELDS = (  # from the chain facts (tools/hv4_chain.py), passed through as finite numbers or null
    "holder_count", "top1_frac", "top10_frac", "creator_sold_frac", "same_slot_supply", "sniper_share", "sniper_still_holding",
    "holders_age_s", "quote_chg_15m", "quote_dd_peak",
)
CARD_NUMERIC_FIELDS = frozenset(FEATURE_FIELDS) | frozenset(RISK_NUMERIC_FIELDS) | {"max_stake"}
assert len(CARD_NUMERIC_FIELDS) == len(FEATURE_FIELDS) + len(RISK_NUMERIC_FIELDS) + 1  # no name used twice


def _series(snap: SnapshotLike) -> list[tuple[int, float, float, float, float, float]]:
    """The snapshot's 1-min candles as (minute, o, h, l, c, vol_sol), sorted; a malformed candle is skipped."""
    rows = []
    for c in snap.get("candles") or ():
        if not isinstance(c, Mapping):
            continue
        t = c.get("t_min_ms")
        vals = [_num(c.get(k)) for k in ("o", "h", "l", "c", "vol_sol")]
        if isinstance(t, int) and not isinstance(t, bool) and None not in vals:
            rows.append((t // MIN_MS, *vals))
    rows.sort(key=lambda r: r[0])
    return rows


def _covered(snap: SnapshotLike, window_min: int) -> bool:
    """Is a `window_min`-minute candle window fully observed? Needs the snapshot to retain that many minutes (candle_retention_min, default
    120) and, if the pool's start was not seen, the pool to have been watched that long; a pool whose whole life is retained is covered."""
    ret = _num(snap.get("candle_retention_min"))
    ret = DEFAULT_CANDLE_RETENTION_MIN if ret is None else ret
    age, known = _age(snap)
    if age is None:
        return False
    span_min = age / 60.0
    return ret >= min(window_min, span_min) if known else (ret >= window_min and span_min >= window_min)


def features(snap: SnapshotLike, prints: Iterable[PrintLike] | None, *, as_of_ms: int) -> dict[str, Any]:
    """All FEATURE_FIELDS as of `as_of_ms`. A field that cannot be computed from the inputs is None, never a guess: the 240-min return and the
    6 h fields need candle history the snapshot may not hold (see DEFAULT_CANDLE_RETENTION_MIN); the tape fields need `prints`."""
    pr = _norm_prints(prints)
    assert_as_of(snap, as_of_ms, prints=pr)
    f: dict[str, Any] = {k: None for k in FEATURE_FIELDS}
    f["age_s"] = _age(snap)[0]
    last = _num(snap.get("last_ms"))
    f["last_print_s"] = (as_of_ms - last) / 1000.0 if last is not None else None
    if snap.get("quote_wsol") is not True:
        return f  # no SOL figure of a non-WSOL (or unverified) pool is read: every other field stays None
    res = snap.get("reserves") if isinstance(snap.get("reserves"), Mapping) else {}
    qe = _num(res.get("quote_eff_sol"))
    f["quote_sol"], f["quote_eff_sol"], f["price"] = _num(res.get("quote_sol")), qe, _num(snap.get("price"))
    for tag, s in (("025", 0.25), ("050", 0.5), ("100", 1.0)):
        f[f"impact_rt_{tag}"] = impact_rt(s, qe)
    f["max_stake_impact"] = max_stake_by_impact(qe)
    for label in ("5m", "15m", "60m"):
        w = _win(snap, label)
        if w:
            f[f"vol_{label}"] = (_num(w.get("buy_sol")) or 0.0) + (_num(w.get("sell_sol")) or 0.0)
            f[f"net_flow_{label}"] = _num(w.get("net_flow_sol"))
            f[f"n_buys_{label}"], f[f"n_sells_{label}"] = _num(w.get("n_buys")), _num(w.get("n_sells"))
    if _win(snap, "15m"):
        f["n_prints_15m"] = (f["n_buys_15m"] or 0) + (f["n_sells_15m"] or 0)
        f["signers_15m"] = _num(_win(snap, "15m").get("unique_signers"))
    f["dd_ath"] = _num(snap.get("drawdown_from_ath"))
    tn = snap.get("top_net") if isinstance(snap.get("top_net"), Mapping) else {}
    for key, name in (("buyers", "top_buyer_net_sol"), ("sellers", "top_seller_net_sol")):
        lst = tn.get(key) or []
        f[name] = _num(lst[0].get("net_sol")) if lst and isinstance(lst[0], Mapping) else None
    f["creator_net_sol_life"] = _num(snap.get("creator_net_sol"))

    # candles: returns, 6 h high / low / volume
    rows = _series(snap)
    mins = [r[0] for r in rows]
    price = f["price"]
    ret_cap = _num(snap.get("candle_retention_min"))
    ret_cap = DEFAULT_CANDLE_RETENTION_MIN if ret_cap is None else ret_cap
    for n in (1, 5, 15, 60, 240):
        if price is None or n > ret_cap:
            continue
        i = bisect.bisect_right(mins, (as_of_ms - n * MIN_MS) // MIN_MS) - 1
        if i >= 0 and rows[i][4] > 0:
            f[f"ret_{n}m"] = price / rows[i][4] - 1.0
    if _covered(snap, 360):
        cur_m = as_of_ms // MIN_MS
        win = [r for r in rows if r[0] > cur_m - 360]
        if win:
            hi, lo, vol = max(r[2] for r in win), min(r[3] for r in win), sum(r[5] for r in win)
            f["hi_6h"], f["lo_6h"], f["vol_6h"], f["avg_vol_15m_6h"] = hi, lo, vol, vol / 24.0
            if price is not None and hi > 0:
                f["close_vs_hi_6h"] = price / hi
            if price is not None and lo > 0:
                f["rebound"] = price / lo - 1.0

    # tape-derived: V*, wash concentration, creator flow, net selling, buyer / seller counts
    if pr is not None:
        creator = snap.get("creator") if isinstance(snap.get("creator"), str) else None
        lo15, lo60 = as_of_ms - 15 * MIN_MS, as_of_ms - 60 * MIN_MS
        p60 = sorted((p for p in pr if p[0] >= lo60), key=lambda p: p[0])
        first: dict[str, int] = {}
        gross: dict[str, int] = {}
        for t, sol, buy, who in p60:
            first.setdefault(who, t)
            gross[who] = gross.get(who, 0) + sol
        f["unique_buyers_15m"] = len({w for t, _s, b, w in p60 if b and t >= lo15})
        f["unique_sellers_15m"] = len({w for t, _s, b, w in p60 if not b and t >= lo15})
        buy15 = sum(s for t, s, b, _w in p60 if b and t >= lo15)
        new15 = sum(s for t, s, b, w in p60 if b and t >= lo15 and first[w] >= lo15)
        f["new_wallet_buy_share_15m"] = (new15 / buy15) if buy15 > 0 else None  # "new" = not seen in the 60 min of tape before this print
        tot = sum(gross.values())
        f["top3_signer_share_60m"] = (sum(sorted(gross.values(), reverse=True)[:3]) / tot) if tot > 0 else None
        if creator:
            f["creator_net_sol_60m"] = sum((s if not b else -s) for _t, s, b, w in p60 if w == creator) / LAMPORTS
        cut = as_of_ms - NET_SELL_WINDOW_MS
        f["net_sell_25slots_sol"] = sum((-s if b else s) for t, s, b, _w in p60 if t >= cut) / LAMPORTS
        f["noncreator_sellers_60m"] = len({w for _t, _s, b, w in p60 if not b and w != creator})
        v = vstar(p60, (as_of_ms // MIN_MS) * MIN_MS)
        f["vstar"], f["wash_gross_15m_sol"] = v["vstar"], v["wash_gross_sol"]
    return f


# ---------------------------------------------------------------------------------------------------------------------------------------
# Setups (DESIGN section 1): at most one per pool, per persona, per 30 min; ranked by the z-score of vol_15m within the setup type
# ---------------------------------------------------------------------------------------------------------------------------------------
SETUP_PERSONA = {"PULLBACK": "CON", "BREAKOUT": "BIG", "SECOND_WAVE": "BIG"}


@dataclass(frozen=True)
class SetupTag:
    pool_id: str
    type: str
    persona: str
    vol_15m: float
    z: float | None = None


@dataclass
class SetupLimiter:
    """The 30-minute cooldown per (pool, persona). Callers `record` a tag when it is actually passed on, so a setup that was ranked out does
    not burn the cooldown. (The lab's count used 30-minute aligned buckets; this is a sliding cooldown, so counts differ slightly.)"""

    cooldown_ms: int = SETUP_COOLDOWN_MS
    _last: dict = field(default_factory=dict)

    def allowed(self, pool_id: str, persona: str, now_ms: int) -> bool:
        last = self._last.get((pool_id, persona))
        return last is None or now_ms - last >= self.cooldown_ms

    def record(self, pool_id: str, persona: str, now_ms: int) -> None:
        self._last[(pool_id, persona)] = now_ms


def _pullback(f: Mapping[str, Any]) -> bool:
    r60, r15, nf = f.get("ret_60m"), f.get("ret_15m"), f.get("net_flow_60m")
    return (r60 is not None and r15 is not None and nf is not None and _ge(r60, PULLBACK_RET60_MIN)
            and _ge(r15, PULLBACK_RET15_LO) and _le(r15, PULLBACK_RET15_HI) and _ge(nf, PULLBACK_NET60_MIN_SOL))


def _breakout(f: Mapping[str, Any]) -> bool:
    p, hi, v15, avg, age = f.get("price"), f.get("hi_6h"), f.get("vol_15m"), f.get("avg_vol_15m_6h"), f.get("age_s")
    return (None not in (p, hi, v15, avg, age) and _ge(p, BREAKOUT_HI_FRAC * hi) and _ge(v15, BREAKOUT_VOL_MULT * avg)
            and _ge(age, BREAKOUT_AGE_MIN_S))


def _second_wave(f: Mapping[str, Any]) -> bool:
    p, hi, lo, r15 = f.get("price"), f.get("hi_6h"), f.get("lo_6h"), f.get("ret_15m")
    return (None not in (p, hi, lo, r15) and _le(lo, SECONDWAVE_LO_OVER_HI_MAX * hi) and _ge(p, SECONDWAVE_CLOSE_OVER_LO_MIN * lo)
            and _ge(r15, SECONDWAVE_RET15_MIN))


_SETUP_TESTS = (("PULLBACK", _pullback), ("BREAKOUT", _breakout), ("SECOND_WAVE", _second_wave))


def setups(feats: Mapping[str, Any], *, pool_id: str, as_of_ms: int, limiter: SetupLimiter | None = None) -> list[SetupTag]:
    """Setup tags that fire on `feats` (from features()). At most one per persona: CON gets PULLBACK; BIG gets BREAKOUT, else SECOND_WAVE.
    A null input makes a condition false. This does not re-check the Lane G filters (the scanner runs filters() first) and does not mutate
    `limiter`. The returned tags have z=None; call rank_setups on the batch."""
    out: list[SetupTag] = []
    seen: set[str] = set()
    v15 = _num(feats.get("vol_15m"))
    for typ, test in _SETUP_TESTS:
        persona = SETUP_PERSONA[typ]
        if persona in seen or v15 is None or not test(feats):
            continue
        if limiter is not None and not limiter.allowed(pool_id, persona, as_of_ms):
            continue
        seen.add(persona)
        out.append(SetupTag(pool_id, typ, persona, v15))
    return out


def rank_setups(tags: Sequence[SetupTag]) -> list[SetupTag]:
    """Fill z = (vol_15m - mean) / population std within each setup type (0.0 when the type has one tag or zero spread), then sort by z
    descending, ties by pool id."""
    by_type: dict[str, list[SetupTag]] = {}
    for t in tags:
        by_type.setdefault(t.type, []).append(t)
    out = []
    for group in by_type.values():
        vals = [t.vol_15m for t in group]
        mean = sum(vals) / len(vals)
        sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / len(vals))
        for t in group:
            out.append(SetupTag(t.pool_id, t.type, t.persona, t.vol_15m, ((t.vol_15m - mean) / sd) if sd > 0 else 0.0))
    return sorted(out, key=lambda t: (-(t.z or 0.0), t.pool_id))


# ---------------------------------------------------------------------------------------------------------------------------------------
# Hard vetoes (DESIGN section 2), in code, fail closed
# ---------------------------------------------------------------------------------------------------------------------------------------
def _name_key(x: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(x).lower())


def _token_check(chain: Mapping[str, Any]) -> tuple[list[str], dict[str, Any]]:
    """Vetoes from the mint's facts (veto 1 is separate). Chain keys: token_program, extensions (names), transfer_fee_bps,
    default_account_state ("initialized" | "frozen")."""
    bad: list[str] = []
    prog = chain.get("token_program")
    exts = chain.get("extensions")
    detail: dict[str, Any] = {"token_program": prog, "extensions": exts}
    if prog not in (SPL_TOKEN_PROGRAM, TOKEN_2022_PROGRAM):
        bad.append("token_program_unknown")
    if not isinstance(exts, (list, tuple)):
        bad.append("chain_missing:extensions")
        exts = []
    names = {_name_key(e) for e in exts}
    if prog == SPL_TOKEN_PROGRAM and names:
        bad.append("token_ext_on_classic_program")
    if names & BANNED_EXTENSIONS:
        bad.append("token_ext_banned")
    if names & {"transferfeeconfig", "transferfee"}:
        fee = _num(chain.get("transfer_fee_bps"))
        if fee is None:
            bad.append("transfer_fee_unknown")
        elif fee > 0:
            bad.append("transfer_fee")
    if "defaultaccountstate" in names:
        st = chain.get("default_account_state")
        if not isinstance(st, str):
            bad.append("default_account_state_unknown")
        elif st.strip().lower() != "initialized":
            bad.append("default_account_frozen")
    return bad, detail


def sell_route(snap: SnapshotLike, prints: Iterable[PrintLike] | None, chain: Mapping[str, Any], *, as_of_ms: int,
               stake: float = STAKES[0]) -> dict[str, Any]:
    """Paper simulation of the live sell-route check, run before the buy (a veto) and again at exit (logged as sell_route_flag, fill still
    happens): (a) the token program and extensions allow a transfer; (b) >= 3 distinct non-creator sellers in the last 60 min (all sellers
    when the creator is unknown); (c) a constant-product sell of `stake` returns >= 90% of mark. Check (a) is the mint-fact half only; the
    on-chain simulation is tools/hv4_chain.py's job."""
    bad, _ = _token_check(chain)
    pr = _norm_prints(prints)
    creator = snap.get("creator") if isinstance(snap.get("creator"), str) else None
    sellers = None if pr is None else len({w for t, _s, b, w in pr if not b and w != creator and t >= as_of_ms - 60 * MIN_MS})
    res = snap.get("reserves") if isinstance(snap.get("reserves"), Mapping) else {}
    ret = sim_sell_return(stake, res.get("quote_eff_sol"))
    ok = (not bad) and sellers is not None and sellers >= SELL_ROUTE_MIN_SELLERS and ret is not None and _ge(ret, SELL_ROUTE_MIN_RETURN)
    return {"ok": bool(ok), "transfer_ok": not bad, "noncreator_sellers_60m": sellers, "sim_sell_return": ret, "stake": stake}


@dataclass(frozen=True)
class VetoResult:
    vetoed: bool
    reasons: tuple[str, ...]
    checks: Mapping[str, Any]  # every check with its value, for the decision log


def code_vetoes(snap: SnapshotLike, prints: Iterable[PrintLike] | None, chain: Mapping[str, Any], *, as_of_ms: int,
                stake: float = STAKES[0], feats: Mapping[str, Any] | None = None) -> VetoResult:
    """DESIGN section 2 hard vetoes 1-7. Every check is evaluated; a missing or malformed fact is a veto, never a pass.

    Chain facts (a Mapping from tools/hv4_chain.py; key present with None means "null on chain"; key absent means "not fetched"):
      as_of_ms, as_of_slot, holders_as_of_ms; mint_authority, freeze_authority (None = renounced); token_program, extensions,
      transfer_fee_bps, default_account_state; mayhem (True | False | None=unknown); quote_sol_15m_ago; quote_sol_peak;
      holder_count, holder_count_cover, top1_frac, top10_frac, creator_sold_frac, same_slot_supply, sniper_share, sniper_still_holding."""
    pr = _norm_prints(prints)
    assert_as_of(snap, as_of_ms, prints=pr, chain=chain)
    f = dict(feats) if feats is not None else features(snap, pr, as_of_ms=as_of_ms)
    reasons: list[str] = []
    checks: dict[str, Any] = {}

    def missing(*keys: str) -> bool:
        gone = [k for k in keys if k not in chain]
        reasons.extend(f"chain_missing:{k}" for k in gone)
        return bool(gone)

    missing("as_of_ms", "as_of_slot")
    checks["quote_wsol"] = snap.get("quote_wsol")
    if snap.get("quote_wsol") is not True:
        reasons.append("quote_not_wsol")
    # 1. mint or freeze authority non-null
    for k in ("mint_authority", "freeze_authority"):
        if not missing(k):
            checks[k] = chain[k]
            if chain[k] is not None:
                reasons.append(f"{k}_set")
    # 2. Token-2022 extensions that block or tax a sell
    bad, detail = _token_check(chain)
    checks["token"] = detail
    reasons.extend(bad)
    # 3. Mayhem with age < 24 h (unknown flag = Mayhem; age is the lower bound when the start was not seen)
    age = f.get("age_s")
    mayhem = chain.get("mayhem") if "mayhem" in chain else None
    checks["mayhem"] = {"mayhem": mayhem, "age_s": age}
    if mayhem is not False and (age is None or age < MAYHEM_MIN_AGE_S):
        reasons.append("mayhem_young" if mayhem is True else "mayhem_unknown_young")
    # 4. quote below the floor, or the impact cap breached at 0.25 SOL
    q, i025 = f.get("quote_sol"), f.get("impact_rt_025")
    checks["quote_floor"] = {"quote_sol": q, "impact_rt_025": i025}
    if q is None or not _ge(q, QUOTE_FLOOR_SOL):
        reasons.append("quote_below_floor")
    if i025 is None or not _le(i025, IMPACT_RT_CAP):
        reasons.append("impact_cap_breached")
    # 5. V* >= 0.20
    vs = f.get("vstar")
    checks["vstar"] = {"vstar": vs, "wash_gross_15m_sol": f.get("wash_gross_15m_sol")}
    if vs is None:
        reasons.append("vstar_unknown")
    elif _ge(vs, VSTAR_THRESHOLD):
        reasons.append("vstar_wash")
    # 6. sell route
    sr = sell_route(snap, pr, chain, as_of_ms=as_of_ms, stake=stake)
    checks["sell_route"] = sr
    if not sr["ok"]:
        reasons.append("sell_route")
    # 7. real quote down >= 30% in 15 min
    ago = _num(chain.get("quote_sol_15m_ago")) if "quote_sol_15m_ago" in chain else None
    checks["quote_15m"] = {"quote_sol": q, "quote_sol_15m_ago": ago}
    if ago is None or ago <= 0 or q is None:
        reasons.append("quote_15m_unknown")
    elif _ge((ago - q) / ago, QUOTE_DROP_VETO):
        reasons.append("quote_drop_15m")
    return VetoResult(bool(reasons), tuple(reasons), checks)


# ---------------------------------------------------------------------------------------------------------------------------------------
# Card (DESIGN section 2 "Card")
# ---------------------------------------------------------------------------------------------------------------------------------------
def _filled(rows: Sequence[tuple[int, float, float, float, float, float]], cur_m: int, n: int) -> list[tuple[float, float, float, float, float] | None]:
    """The last n minutes (ending at minute cur_m) as (o, h, l, c, vol); an empty minute carries the last close with volume 0; None before the
    first known close."""
    mins = [r[0] for r in rows]
    out: list[tuple[float, float, float, float, float] | None] = []
    for m in range(cur_m - n + 1, cur_m + 1):
        i = bisect.bisect_right(mins, m) - 1
        if i < 0:
            out.append(None)
        elif mins[i] == m:
            out.append((rows[i][1], rows[i][2], rows[i][3], rows[i][4], rows[i][5]))
        else:
            c = rows[i][4]
            out.append((c, c, c, c, 0.0))
    return out


def _candles_for_card(snap: SnapshotLike, as_of_ms: int, price: float | None) -> tuple[list[list[float]], list[list[float]]]:
    """20 one-minute and 12 five-minute candles, prices as multiples of the current price (o, h, l, c) and volume in SOL."""
    if price is None or price <= 0:
        return [], []
    rows = _series(snap)
    cur_m = as_of_ms // MIN_MS
    one = _filled(rows, cur_m, 60)

    def enc(c: tuple[float, float, float, float, float]) -> list[float]:
        return [round(c[0] / price, 4), round(c[1] / price, 4), round(c[2] / price, 4), round(c[3] / price, 4), round(c[4], 3)]

    c1 = [enc(c) for c in one[-20:] if c is not None]
    c5 = []
    for j in range(12):
        blk = [c for c in one[5 * j:5 * j + 5] if c is not None]
        if blk:
            c5.append(enc((blk[0][0], max(c[1] for c in blk), min(c[2] for c in blk), blk[-1][3], sum(c[4] for c in blk))))
    return c1, c5


def card_json(card: Mapping[str, Any]) -> str:
    """Canonical JSON of a card (sorted keys, no spaces, no NaN): the bytes that are hashed and sent."""
    return json.dumps(card, sort_keys=True, separators=(",", ":"), allow_nan=False, ensure_ascii=True)


def card_sha256(card: Mapping[str, Any]) -> str:
    return hashlib.sha256(card_json(card).encode("ascii")).hexdigest()


def numeric_fields(card: Mapping[str, Any]) -> dict[str, float]:
    """The predicate namespace of a card: its finite non-boolean numbers in `f` and `risk`."""
    out: dict[str, float] = {}
    for sec in ("f", "risk"):
        for k, v in (card.get(sec) or {}).items():
            n = _num(v)
            if n is not None and k in CARD_NUMERIC_FIELDS:
                out[k] = n
    return out


def build_card(snap: SnapshotLike, prints: Iterable[PrintLike] | None, chain: Mapping[str, Any], *, as_of_ms: int, as_of_slot: int | None = None,
               setup: SetupTag | None = None, stake: float = STAKES[0]) -> dict[str, Any]:
    """The card for one Lane G pool as of `as_of_ms`, or CardRefused (a Lane G filter missed, or a hard veto fired, or a stamp is missing).
    LeakageError if any input is later than the scan instant. Every value on the card is as of `as_of_slot` / `as_of_ms`; the chain facts keep
    their own stamps under `risk`. Name and symbol are sanitized and live only under `untrusted`; the id and wallet handles must look like
    base58 addresses or the card is refused. `x` is null until the Grok feature drop is wired in at a window's registration."""
    pr = _norm_prints(prints)
    assert_as_of(snap, as_of_ms, prints=pr, chain=chain, as_of_slot=as_of_slot)
    fr = filters(snap, as_of_ms=as_of_ms)
    feats = features(snap, pr, as_of_ms=as_of_ms)
    vr = code_vetoes(snap, pr, chain, as_of_ms=as_of_ms, stake=stake, feats=feats)
    slot = as_of_slot if as_of_slot is not None else snap.get("last_slot")
    reasons = list(fr.failed) + list(vr.reasons)
    if not (isinstance(slot, int) and not isinstance(slot, bool)):
        reasons.append("as_of_slot_missing")
    if not is_safe_id(snap.get("id")):
        reasons.append("bad_pool_id")
    if setup is not None and setup.pool_id != snap.get("id"):
        raise ValueError("setup tag belongs to another pool")
    if reasons:
        raise CardRefused(reasons)

    f = {k: _r(v) for k, v in feats.items()}
    max_stake = fr.max_stake
    f["max_stake"] = max_stake
    f["age_tier"] = fr.age_tier
    f["age_known"] = fr.age_known
    risk: dict[str, Any] = {"as_of_slot": chain.get("as_of_slot"), "as_of_ms": chain.get("as_of_ms"), "holders_as_of_ms": chain.get("holders_as_of_ms"),
                            "mint_authority_null": True, "freeze_authority_null": True,
                            "token_program": "token_2022" if chain.get("token_program") == TOKEN_2022_PROGRAM else "spl_token",
                            "extensions": [sanitize_name(e)[:NAME_MAX] for e in list(chain.get("extensions") or [])[:12]],
                            "mayhem": chain.get("mayhem"), "holder_count_cover": chain.get("holder_count_cover"),
                            "sell_route_ok": vr.checks["sell_route"]["ok"]}
    for k in RISK_NUMERIC_FIELDS:
        risk[k] = _r(_num(chain.get(k)))
    hs = _num(chain.get("holders_as_of_ms"))
    risk["holders_age_s"] = _r((as_of_ms - hs) / 1000.0) if hs is not None else None
    ago, q, peak = _num(chain.get("quote_sol_15m_ago")), feats["quote_sol"], _num(chain.get("quote_sol_peak"))
    risk["quote_chg_15m"] = _r(q / ago - 1.0) if ago and q is not None else None
    risk["quote_dd_peak"] = _r(1.0 - q / peak) if peak and peak > 0 and q is not None else None
    c1, c5 = _candles_for_card(snap, as_of_ms, feats["price"])
    tn = snap.get("top_net") if isinstance(snap.get("top_net"), Mapping) else {}

    def top(key: str) -> str | None:
        lst = tn.get(key) or []
        return short_addr(lst[0].get("trader")) if lst and isinstance(lst[0], Mapping) else None

    card = {
        "v": CARD_VERSION, "id": snap["id"], "lane": "G", "as_of_slot": slot, "as_of_ms": as_of_ms,
        "setup": None if setup is None else {"type": setup.type, "persona": setup.persona, "z": _r(setup.z)},
        "f": f, "risk": risk, "tops": {"buyer": top("buyers"), "seller": top("sellers")},
        "c1": c1, "c5": c5,
        "untrusted": {"name": sanitize_name(snap.get("name")), "symbol": sanitize_name(snap.get("symbol"))},
        "x": None,
    }
    card_json(card)  # raises on a non-finite number
    return card
