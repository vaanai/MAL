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
    now = _num(snap.get("now_ms"))
    lag = (as_of_ms - now) if now is not None else None
    add("state_fresh", lag, f"state clock within {STATE_LAG_MAX_MS} ms of T", lag is not None and lag <= STATE_LAG_MAX_MS)
    age, known = _age(snap)
    add("age_min", age, f">= {AGE_MIN_S} s", age is not None and _ge(age, AGE_MIN_S))
    add("age_max", age, f"<= {AGE_MAX_S} s", age is not None and _le(age, AGE_MAX_S))
    res = snap.get("reserves") if isinstance(snap.get("reserves"), Mapping) else {}
    q = _num(res.get("quote_sol"))
    add("quote_floor", q, f">= {QUOTE_FLOOR_SOL} SOL real quote", q is not None and _ge(q, QUOTE_FLOOR_SOL))
    w15, w60 = _win(snap, "15m"), _win(snap, "60m")
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


# ---- PART 3 ----
