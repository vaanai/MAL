"""Forward paper arena: fill and cost engine (hunt20). PAPER ONLY. No key, no transaction, no RPC.

Bots call `ArenaEngine.buy(...)` / `.sell(...)`. The engine reads feed rows (the `tools/arena_feed.py` tape shape: `type` trade | gap, `prog`, `slot`,
`t_recv_ms`, `signature`, `event_index`, plus the decoded trade fields) with `.on_row(row)`, fills each decision from the tape and writes write-once ledger rows
under `/data/mal/arena/ledger/<bot_id>/trades-<UTC day of the entry decision>.jsonl`:
  kind "open"      written by buy() at the decision instant, BEFORE any pricing (one per position; recoverable after a restart with `recover()`)
  kind "trade"     one per (position, stake) after the sell is priced
  kind "excluded"  one per (position, stake) that the feed-gap / missing-V / engine-restart rule removes (counted by the scorer, never priced)

BINDING STACK (DEC-031 revision 2, section 1.4). Nothing here re-derives the lab's cost model; it is imported:
  * `tools.r4b_book.mint_states`   entry / exit pool states, spot, same-slot buys, nearby buy SOL (the prep_l3 extraction behind sc.py's panel)
  * `tools.r4b_book.round_trip`    PumpSwap round trip: V in Q, fee tiers, own buy re-injected into the exit state, +26.08 bps / -16 bps haircut,
                                   505,000 lamports per send, rent 2,039,280 per filled trip, 1.15 guard (a guarded buy pays one send)
  * `tools.r4b_book.fail_legs`     flat 15% and pressure-fail (slope scale 1; intercept refit so mean p = 0.289) legs
  * `tools.h5_shadow.SlotClock`    the rolling slot clock (`--sps rolling`)
  * `tools.paper_curve_math`       the bonding-curve quote_buy / quote_sell / reserves_with_our_buy (the curve sim of tools/latency_curve.py)
  * STRAND CLASS: an unpriced or invalid exit (r4b_book entry_invalid / exit_invalid C2, C7, C8), or proceeds below one send, is a TOTAL loss of stake plus the
    buy send plus rent (-stake - 505,000 - 2,039,280) on every leg. It bypasses the fail blend (flat = pressure = the loss) and is never dropped.
  * E1 FLOOR: per filled trip the stack carries max(haircut, 384,022 lamports per 0.05 SOL of stake) (DEC-021:54, Amendment 7 option B), scaled linearly.
  * LATENCY starts at the DECISION INSTANT: decision_ms is mapped to the tape slot clock (the latest feed row's (slot, t_recv_ms) plus elapsed time / sps) and
    the fill lands at that slot + k, k = ceil(latency / sps - 1e-9) (`k_rounding="round"` is the EXP-027 QP value). A decision_ms or decision_slot earlier than
    the engine's high-water mark, or before the book's registered clock, is REFUSED. Exit lag = ceil(0.55 s / sps), at least 2 slots. t_recv_ms lags block time
    by the feed delay, so the mapped slot is a lower bound of the chain's slot at the decision instant (not corrected; disclosed).
  * FEED GAPS: a trade whose decision-to-exit span holds a logged data_loss gap of its program, or a needed print without V, or a restart of this engine, is
    written as an "excluded" row with the reason. A derived_last exit after a gap falls under the gap rule. The scorer counts them (> 5% of a book -> NOT_DECIDABLE).
    Contract: the feed emits a gap row before the row that revealed it.

Conventions (confirmed against sc.py / r4b_book):
  * PumpSwap rows carry PRE-trade reserves. A fill lands on END-bound state: the PRE-trade reserves of the FIRST print with slot >= landing slot + 1,
    i.e. the pool after every print in the landing slot. If no such print exists yet the state after the last print is derived (G's 1.25% constant,
    as mint_states does) once the global slot clock is `grace_slots` past it; the row records `state_src`.
  * Bonding rows carry POST-trade reserves: the fill lands on the POST-trade reserves of the LAST print with slot <= landing slot.
  * Within-slot order (decides "first print at or after landing + 1" and "last print at or before landing"): by (slot, tx_index, event_index, signature) when the
    row carries tx_index (the exploration tape), else by (slot, t_recv_ms, signature, event_index) (the live arena tape has no tx_index). Receive order
    approximates chain order within a slot; it is the best the live row shape allows and is disclosed.
  * Spot for the guard = post-trade price of the last print with slot < decision slot (mint_states).
  * Bonding fills have no portal fee (direct route); the haircut was measured on PumpSwap and is applied to them as a conservative assumption.

`StackConfig(strand_rule="sc", e1_floor_per_005=0)` is the LEGACY stack of sc.py / VERIFY (no strand class, no floor); it exists only for the bit-parity
check against sc.py. Defaults are the binding stack: k_rounding ceil, rent charged, strand_rule "total", E1 floor on. A quant-proof config ruling may change
one of them; they are config and every row records them.

Not supported in v1: partial sells (`frac` must be 1.0; refused), sells decided before the slower entry leg lands (refused).
"""

from __future__ import annotations

import bisect
import fcntl
import hashlib
import json
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import numpy as np

from tools import r4b_frozen as F
from tools.h5_shadow import SlotClock, sps_ok
from tools.paper_curve_math import market_cap_sol, quote_buy, quote_sell, reserves_with_our_buy
from tools.r4b_book import fail_legs, mint_states, round_trip

SCHEMA = "arena_trade_v1"
ARENA_ROOT = "/data/mal/arena"
LEDGER_ROOT = f"{ARENA_ROOT}/ledger"
REGISTRY_PATH = f"{ARENA_ROOT}/bots.json"

STAKES_SOL = (0.25, 0.5, 1.0)
LATS = tuple(F.LATENCIES_S)  # (1.3, 1.9)
LAT_TAG = {1.3: "1p3", 1.9: "1p9"}
LEG_KEYS = ("flat_1p3", "flat_1p9", "press_1p3", "press_1p9")
SEND_LAMPORTS = F.SEND_FEE_DECIDING  # 505,000
RENT_LAMPORTS = F.RENT_REPORT  # 2,039,280 per filled trip (the brief)
GRACE_SLOTS = 10_000  # about 1 h: the state is the tape print the sim uses; derive from the last print only past this (QP ruling on #708)
HIST_KEEP_SLOTS = 20_000
LATE_KEEP_SLOTS = 2_000  # a priced position is watched for late prints this many slots past its need slot
# Provisional pressure intercept stored in the ledger's press_* net_lamports. The AUTHORITATIVE pressure leg is refit per (bot, stake, latency) book by
# arena_score (the stack refits the intercept per cell so that mean p over the cell's filled sends is 0.289). Fit on the 300-case parity book (1.3 s).
E1_FLOOR_PER_005 = 384_022  # DEC-021:54 pre-seal n = 8 live-minus-sim lamports per 0.05 SOL of stake; scaled linearly (Amendment 7 option B)
PRESS_INTERCEPT_PROVISIONAL = -1.4197242838379789

VENUE_ALIASES = {"pumpswap": "pumpswap", "pump_amm": "pumpswap", "pump_bonding": "pump_bonding", "pump": "pump_bonding", "pumpfun": "pump_bonding", "bonding": "pump_bonding"}
V_BAND_MAX = 30_000_000_000


class ArenaRefused(Exception):
    """A request the arena will not price (unregistered bot, no slot clock, unknown pool, partial sell, ...)."""


class LedgerConflict(Exception):
    """A trade_id already in the ledger with different content."""


# ------------------------------------------------------------------------------------------------------------------------------ registry
def _fsync_dir(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def canon(obj: Any) -> str:
    """Strict, canonical JSON (no NaN / Infinity, sorted keys)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False)


def utc_day(ms: int) -> str:
    return datetime.fromtimestamp(int(ms) / 1000.0, timezone.utc).strftime("%Y-%m-%d")


def _append_line(path: Path, obj: Mapping[str, Any]) -> None:
    """Append one strict-JSON line (flock, O_APPEND, fsync of the file and, on creation, of the directory)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    created = not path.exists()
    fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, canon(obj).encode() + b"\n")
        os.fsync(fd)
        if created:
            _fsync_dir(path.parent)
    finally:
        os.close(fd)


def load_registry(path: str | os.PathLike = REGISTRY_PATH) -> dict[str, dict[str, Any]]:
    """bots.json is APPEND-ONLY JSON lines: {"kind":"register", bot_id, frozen_sha, clock_start_ms, registered_at_ms, deciding_stake, model_ids} and
    {"kind":"stop", bot_id, stopped_at_ms}. No line is ever removed; a stopped book stays listed (stopped_at_ms) and counts in every multiplicity."""
    p = Path(path)
    out: dict[str, dict[str, Any]] = {}
    if not p.exists():
        return out
    for line in p.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r["kind"] == "register":
            out[r["bot_id"]] = {k: v for k, v in r.items() if k != "kind"} | {"stopped_at_ms": None}
        elif r["kind"] == "stop" and r["bot_id"] in out:
            out[r["bot_id"]]["stopped_at_ms"] = r["stopped_at_ms"]
    return out


def register_bot(bot_id: str, frozen_sha: str, clock_start_ms: int, path: str | os.PathLike = REGISTRY_PATH, *, deciding_stake: float = 0.25,
                 model_ids: tuple[str, ...] | list[str] = (), now_ms: int | None = None) -> dict[str, Any]:
    """Register a book once, before its clock. Records registered_at_ms and REFUSES a clock_start_ms earlier than the registration instant. The same line again
    is a no-op; a different sha, clock, deciding stake or model ids for an existing bot_id is REFUSED (a new version is a new bot_id)."""
    if not bot_id or "/" in bot_id or bot_id.startswith("."):
        raise ArenaRefused(f"bad bot_id {bot_id!r}")
    if not frozen_sha or not all(c in "0123456789abcdef" for c in frozen_sha.lower()) or len(frozen_sha) < 7:
        raise ArenaRefused("frozen_sha must be a git hex sha (7+ chars)")
    if deciding_stake not in STAKES_SOL:
        raise ArenaRefused(f"deciding_stake must be one of {STAKES_SOL}")
    reg_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    if int(clock_start_ms) < reg_ms:
        raise ArenaRefused(f"clock_start_ms {clock_start_ms} is earlier than the registration instant {reg_ms}: a clock cannot be backdated")
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    entry = {"bot_id": bot_id, "frozen_sha": frozen_sha, "clock_start_ms": int(clock_start_ms), "deciding_stake": float(deciding_stake),
             "model_ids": list(model_ids)}
    with open(p.with_suffix(p.suffix + ".lock"), "a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        bots = load_registry(p)
        if bot_id in bots:
            old = {k: bots[bot_id].get(k) for k in entry}
            if old != entry:
                raise ArenaRefused(f"bot {bot_id} is registered with {old}; a changed sha, clock, stake or model is a new bot_id, not an edit")
            return bots[bot_id]
        _append_line(p, dict(entry, kind="register", registered_at_ms=reg_ms))
    return load_registry(p)[bot_id]


def stop_bot(bot_id: str, stopped_at_ms: int, path: str | os.PathLike = REGISTRY_PATH) -> None:
    """Append a stop line. The book stays listed and counts in every multiplicity disclosure."""
    p = Path(path)
    with open(p.with_suffix(p.suffix + ".lock"), "a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        bots = load_registry(p)
        if bot_id not in bots:
            raise ArenaRefused(f"unknown bot {bot_id}")
        if bots[bot_id]["stopped_at_ms"] is not None:
            raise ArenaRefused(f"bot {bot_id} is already stopped")
        _append_line(p, {"kind": "stop", "bot_id": bot_id, "stopped_at_ms": int(stopped_at_ms)})


# ------------------------------------------------------------------------------------------------------------------------------ ledger
class Ledger:
    """Write-once JSONL ledger: one file per (bot, UTC day of the entry decision). A trade_id is written once; the same bytes again is a no-op,
    different bytes raise LedgerConflict. Every append is flock'ed, written with O_APPEND and fsync'ed (and the directory on file creation)."""

    def __init__(self, root: str | os.PathLike = LEDGER_ROOT) -> None:
        self.root = Path(root)

    def path_for(self, bot_id: str, day: str) -> Path:
        return self.root / bot_id / f"trades-{day}.jsonl"

    @staticmethod
    def _scan(fd: int) -> dict[str, str]:
        os.lseek(fd, 0, os.SEEK_SET)
        data = b""
        while True:
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                break
            data += chunk
        ids: dict[str, str] = {}
        for line in data.split(b"\n"):
            if not line:
                continue
            ids[json.loads(line)["trade_id"]] = hashlib.sha256(line).hexdigest()
        return ids

    def append(self, row: Mapping[str, Any]) -> bool:
        """True if written, False if the identical row is already there."""
        line = canon(row).encode()
        day = utc_day(row["decision_ms"]["entry"])
        p = self.path_for(row["bot_id"], day)
        new_dir = not p.parent.exists()
        p.parent.mkdir(parents=True, exist_ok=True)
        created = not p.exists()
        fd = os.open(str(p), os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            ids = self._scan(fd)
            tid = row["trade_id"]
            if tid in ids:
                if ids[tid] == hashlib.sha256(line).hexdigest():
                    return False
                raise LedgerConflict(f"trade_id {tid} already written with different content")
            os.write(fd, line + b"\n")
            os.fsync(fd)
            if created:
                _fsync_dir(p.parent)
            if new_dir:
                _fsync_dir(p.parent.parent)
            return True
        finally:
            os.close(fd)  # releases the flock

    def read_bot(self, bot_id: str) -> list[dict[str, Any]]:
        d = self.root / bot_id
        out: list[dict[str, Any]] = []
        if not d.exists():
            return out
        for p in sorted(d.glob("trades-*.jsonl")):
            for line in p.read_text().splitlines():
                if line.strip():
                    out.append(json.loads(line))
        return out


# ------------------------------------------------------------------------------------------------------------------------------ config
STRAND_STATES = ("stranded", "entry_invalid", "exit_invalid")


@dataclass(frozen=True)
class StackConfig:
    send: int = SEND_LAMPORTS
    rent: int = RENT_LAMPORTS
    h_in: float = F.HAIRCUT_ENTRY
    h_out: float = F.HAIRCUT_SELL
    k_rounding: str = "ceil"
    stakes_sol: tuple[float, ...] = STAKES_SOL
    grace_slots: int = GRACE_SLOTS
    portal_ppm: int = 0  # bonding route fee; 0 = direct, like the PumpSwap stack
    press_intercept: float = PRESS_INTERCEPT_PROVISIONAL
    # "both" (the deciding default, quant-proof on #708): every trade is priced under BOTH rules and a cell passes only if it passes under both.
    # "total": the DEC-031 strand class (r4b_book detection: invalid entry / exit, C2, C7, C8, proceeds < one send) priced as a TOTAL loss, no fail blend.
    # "sc": no strand class (sc.py / VERIFY): a sell worth less than one send is a normal sell and a strand-like exit is blended like any fill.
    # Neither is lower on every trade: at the arena's rent "total" is the more lenient on dust sells (+412,340 lamports on the one rugged parity pool).
    strand_rule: str = "both"
    e1_floor_per_005: int = E1_FLOOR_PER_005  # lamports per 0.05 SOL of stake; 0 = off (legacy parity only)
    slot_tolerance: int = 0  # a decision_slot below the engine's high-water slot by more than this is refused
    max_future_ms: int = 5_000
    max_past_ms: int | None = 60_000  # a decision_ms older than this at the write instant is stale (the caller supplies decision_ms)

    def as_dict(self) -> dict[str, Any]:
        return {"send": self.send, "rent": self.rent, "h_in": self.h_in, "h_out": self.h_out, "k_rounding": self.k_rounding, "exit_lag_s": F.EXIT_LAG_S,
                "guard": F.GUARD, "portal_ppm": self.portal_ppm, "press_intercept_provisional": self.press_intercept, "strand_rule": self.strand_rule,
                "e1_floor_per_005": self.e1_floor_per_005}


def rules_of(cfg: StackConfig) -> tuple[str, ...]:
    return ("total", "sc") if cfg.strand_rule == "both" else (cfg.strand_rule,)


def book_fail_legs(pnl, filled, strand, ssb, nearby, send, intercept=None):
    """Flat and pressure legs of a book. A strand is a total loss and BYPASSES the fail blend; it stays in the set the pressure intercept is fit on
    (mean p over the cell's non-guarded sends, as the stack fits it). Returns (flat, press, intercept, p)."""
    pnl = np.asarray(pnl, float)
    flat, press, c, p = fail_legs(pnl, filled, ssb, nearby, send, intercept=intercept)
    strand = np.asarray(strand, bool)
    return np.where(strand, pnl, flat), np.where(strand, pnl, press), c, p


# ------------------------------------------------------------------------------------------------------------------------------ bonding curve
def curve_trip(stake: float, qe: float, be: float, ref: float | None, qx: float, bx: float, *, send: float, rent: float, h_in: float, h_out: float,
               portal_ppm: int = 0, rent_stranded: float = F.RENT_STRANDED, auto_stranded: bool = True) -> tuple[float, str, bool]:
    """One pre-graduation bonding-curve round trip in lamports -> (pnl, state, filled).

    (qe, be): POST-trade reserves the buy lands on. ref: decision-time price (SOL per token) for the 15% slippage guard, as latency_curve._try_buy.
    (qx, bx): POST-trade reserves the sell lands on; our buy is re-injected (reserves_with_our_buy) and the curve cannot pay more real SOL than it holds
    (quote_sell). A buy that cannot fill (completed curve, guard) is a miss and pays one send. A sell that cannot land (or, with auto_stranded, pays less
    than one send) strands the position: -stake - send - rent_stranded."""
    if not (math.isfinite(qe) and math.isfinite(be) and qe > 0 and be > 0):
        return -float(send), "guarded", False
    buy = quote_buy(venue="pump_bonding", size_lamports=int(stake), quote_lamports=int(qe), base_raw=int(be), market_cap=market_cap_sol(int(qe), int(be)),
                    portal_fee_ppm=portal_ppm)
    if buy is None:
        return -float(send), "guarded", False
    if ref and ref > 0:
        spot = int(qe) / (int(be) * 1000)
        executable = (buy.net_in_lamports / 1e9) / (buy.tokens_raw / 1e6)
        if spot > ref * F.GUARD or executable > ref * F.GUARD:
            return -float(send), "guarded", False
    stranded = -float(stake) - send - rent_stranded
    if not (math.isfinite(qx) and math.isfinite(bx)):
        return stranded, "stranded", True
    book = reserves_with_our_buy(quote_lamports=int(qx), base_raw=int(bx), net_in_lamports=buy.net_in_lamports, tokens_raw=buy.tokens_raw, same_venue=True)
    if book is None:
        return stranded, "stranded", True
    q2, b2 = book
    tokh = int(buy.tokens_raw / (1 + h_in))
    out = quote_sell(venue="pump_bonding", tokens_raw=tokh, quote_lamports=q2, base_raw=b2, market_cap=market_cap_sol(q2, b2),
                     payable_quote_lamports=int(qx), portal_fee_ppm=portal_ppm)
    if out is None:
        return stranded, "stranded", True
    proceeds = out * (1 - h_out)
    if auto_stranded and proceeds < send:
        return stranded, "stranded", True
    return proceeds - stake - 2 * send - rent, "filled", True


def _trip(prog: str, stake: float, st: Mapping[str, Any], lat: float, cfg: StackConfig, h_in: float, h_out: float, rule: str) -> tuple[float, str, bool]:
    total = rule == "total"
    if prog == "pumpswap":
        pnl, filled, strand, bad, xbad = round_trip(np.array([stake]), st["Qe"][lat], st["Be"][lat], st["ref"], st["Qx"], st["Bx"], cfg.send, cfg.rent, h_in,
                                                   h_out, return_stranded=True, return_invalid=True, return_exit_invalid=True, auto_stranded=total,
                                                   k_tol=F.K_TOL if total else None)
        state = "guarded" if not filled[0] else "entry_invalid" if bad[0] else "exit_invalid" if xbad[0] else "stranded" if strand[0] else "filled"
        return float(pnl[0]), state, bool(filled[0])
    return curve_trip(stake, st["Qe"][lat], st["Be"][lat], st["ref"], st["Qx"], st["Bx"], send=cfg.send, rent=cfg.rent, h_in=h_in, h_out=h_out,
                      portal_ppm=cfg.portal_ppm, auto_stranded=total)


def price_leg(prog: str, stake_lamports: float, st: Mapping[str, Any], lat: float, cfg: StackConfig, rule: str | None = None) -> dict[str, Any]:
    """One (stake, latency) leg from extracted states `st` = {ref, Qe[lat], Be[lat], ssb[lat], nearby[lat], Qx, Bx}. Returns the no-fail pnl (after the E1
    floor), the fill state, the strand flag and the flat / provisional-pressure legs (lamports). A strand is a total loss on every leg."""
    rule = rule or rules_of(cfg)[0]
    pnl, state, filled = _trip(prog, stake_lamports, st, lat, cfg, cfg.h_in, cfg.h_out, rule)
    floor_extra = 0.0
    if cfg.e1_floor_per_005 and state == "filled":
        pnl0, _, _ = _trip(prog, stake_lamports, st, lat, cfg, 0.0, 0.0, rule)
        haircut = pnl0 - pnl
        floor_extra = max(0.0, cfg.e1_floor_per_005 * stake_lamports / 50_000_000.0 - haircut)
        pnl -= floor_extra
    strand = rule == "total" and state in STRAND_STATES
    flat, press, _, p = book_fail_legs(np.array([pnl]), np.array([filled]), np.array([strand]), np.array([st["ssb"][lat]]), np.array([st["nearby"][lat]]),
                                       cfg.send, intercept=cfg.press_intercept)
    return {"pnl": pnl, "filled": filled, "state": state, "strand": bool(strand), "e1_extra": floor_extra, "flat": float(flat[0]), "press": float(press[0]),
            "p": float(p[0])}


# ------------------------------------------------------------------------------------------------------------------------------ pool history
class PoolHist:
    """Ordered prints of one pool. PumpSwap reserves are PRE-trade, bonding POST-trade; V is per print with its source (a print whose source is "none" is
    never priced as V = 0: the trades that need it are excluded and counted)."""

    __slots__ = ("pool", "mint", "venue", "keys", "seen", "slot", "isbuy", "sol", "tok", "q", "b", "v", "vsrc", "last_v_row", "_np")

    def __init__(self, pool: str, mint: str | None, venue: str) -> None:
        self.pool, self.mint, self.venue = pool, mint, venue
        self.keys: list[tuple] = []
        self.seen: set[tuple] = set()
        self.slot: list[int] = []
        self.isbuy: list[bool] = []
        self.sol: list[float] = []
        self.tok: list[float] = []
        self.q: list[float] = []
        self.b: list[float] = []
        self.v: list[float] = []
        self.vsrc: list[str] = []  # where each print's V came from: row | pool_prev | vmap | none | na (bonding)
        self.last_v_row: float | None = None
        self._np: dict[str, np.ndarray] | None = None

    def add(self, key: tuple, dedupe: tuple, slot: int, isbuy: bool, sol: float, tok: float, q: float, b: float, v: float, vsrc: str) -> bool:
        if dedupe in self.seen:
            return False
        self.seen.add(dedupe)
        i = len(self.keys) if (not self.keys or key >= self.keys[-1]) else bisect.bisect_right(self.keys, key)
        for lst, val in ((self.keys, key), (self.slot, slot), (self.isbuy, isbuy), (self.sol, sol), (self.tok, tok), (self.q, q), (self.b, b), (self.v, v),
                         (self.vsrc, vsrc)):
            lst.insert(i, val)
        self._np = None
        return True

    @property
    def last_slot(self) -> int:
        return self.slot[-1] if self.slot else -1

    def arrays(self) -> dict[str, np.ndarray]:
        if self._np is None:
            self._np = {"sl": np.asarray(self.slot, dtype=np.int64), "ib": np.asarray(self.isbuy, dtype=bool), "so": np.asarray(self.sol, dtype=float),
                        "tk": np.asarray(self.tok, dtype=float), "q": np.asarray(self.q, dtype=float), "b": np.asarray(self.b, dtype=float),
                        "v": np.asarray(self.v, dtype=float), "vok": np.asarray([x != "none" for x in self.vsrc], dtype=bool)}
        return self._np


def bonding_states(h: PoolHist, sd: int, x_land: dict[float, int], x_exit: int, nearby_slots: int) -> dict[str, Any]:
    """Bonding fills: POST-trade reserves of the last print with slot <= x (the END bound). Pressure = buys in slot x / buy lamports over [x - nearby, x]."""
    a = h.arrays()
    sl, ib, so, q, b = a["sl"], a["ib"], a["so"], a["q"], a["b"]
    cib = np.concatenate([[0], np.cumsum(ib)])
    cbv = np.concatenate([[0], np.cumsum(np.where(ib, so, 0.0))])
    j = int(np.searchsorted(sl, sd, "left"))
    ref = float(q[j - 1] / (b[j - 1] * 1000.0)) if j > 0 else None
    out: dict[str, Any] = {"ref": ref, "Qe": {}, "Be": {}, "ssb": {}, "nearby": {}}
    for lat, x in x_land.items():
        je = int(np.searchsorted(sl, x + 1, "left"))  # prints with slot <= x are [0, je)
        ix = int(np.searchsorted(sl, x, "left"))
        lo2 = int(np.searchsorted(sl, x - nearby_slots, "left"))
        out["Qe"][lat] = float(q[je - 1]) if je > 0 else float("nan")
        out["Be"][lat] = float(b[je - 1]) if je > 0 else float("nan")
        out["ssb"][lat] = float(cib[je] - cib[ix])
        out["nearby"][lat] = float(cbv[je] - cbv[lo2])
    je = int(np.searchsorted(sl, x_exit + 1, "left"))
    out["Qx"] = float(q[je - 1]) if je > 0 else float("nan")
    out["Bx"] = float(b[je - 1]) if je > 0 else float("nan")
    return out


# ------------------------------------------------------------------------------------------------------------------------------ engine
@dataclass
class Position:
    position_id: str
    bot_id: str
    frozen_sha: str
    pool: str
    mint: str | None
    prog: str
    decision_ms: int
    decision_slot: int  # as given by the bot
    sd: int  # effective entry decision slot = max(decision_slot, slot mapped from decision_ms)
    sps_entry: float
    bot_sol_in: float | None
    meta: dict[str, Any]
    exit_ms: int | None = None
    exit_slot: int | None = None
    xs: int | None = None  # effective exit decision slot
    sps_exit: float | None = None
    reason: str | None = None
    restarted: bool = False  # rebuilt from the ledger after an engine restart: its pool history has a hole


@dataclass
class Gap:
    prog: str | None
    t_lo: int
    t_hi: int
    s_lo: int
    s_hi: int
    kind: str


class ArenaEngine:
    def __init__(self, ledger_root: str | os.PathLike = LEDGER_ROOT, registry_path: str | os.PathLike = REGISTRY_PATH, cfg: StackConfig | None = None,
                 vmap: Mapping[str, Any] | None = None, clock: SlotClock | None = None, sps_fn: Callable[[], float | None] | None = None,
                 now_fn: Callable[[], int] | None = None) -> None:
        self.ledger = Ledger(ledger_root)
        self.registry_path = registry_path
        self.cfg = cfg or StackConfig()
        self.vmap = vmap or {}
        self.clock = clock or SlotClock()
        self.sps_fn = sps_fn
        self.now_fn = now_fn or (lambda: time.time_ns() // 1_000_000)
        self.pools: dict[str, PoolHist] = {}
        self.positions: dict[str, Position] = {}
        self.pending: dict[str, list[str]] = {}  # pool -> position ids with a sell decided and not yet priced
        self.hw_slot = -1
        self.hw_ms: int | None = None  # latest t_recv_ms of a trade row
        self.hw_ms_slot = -1  # slot of the row that set hw_ms: the (slot, ms) anchor of the slot clock
        self.gaps: list[Gap] = []
        self.priced: dict[str, list[dict[str, Any]]] = {}  # pool -> positions already written (for late-print detection)
        self.counters: dict[str, int] = {}
        self.last_error: dict[str, str] = {}

    # --- feed
    def _count(self, k: str) -> None:
        self.counters[k] = self.counters.get(k, 0) + 1

    def _v_of(self, h: PoolHist, row: Mapping[str, Any], venue: str) -> tuple[float, str]:
        """(V, source). Source order: this row's event V, an earlier row of the same pool, the static map, none. V outside [0, 30 SOL] is not used."""
        if venue != "pumpswap":
            return 0.0, "na"
        for src, v in (("row", row.get("virtual_quote_reserves")), ("pool_prev", h.last_v_row), ("vmap", self.vmap.get(h.pool))):
            if v is None:
                continue
            v = float(v)
            if 0.0 <= v <= V_BAND_MAX:
                if src == "row":
                    h.last_v_row = v
                return v, src
            self._count("v_out_of_band")
        self._count("v_missing")
        return 0.0, "none"

    @staticmethod
    def order_key(row: Mapping[str, Any], slot: int, t_ms: int) -> tuple:
        """Within-slot order. With tx_index (exploration tape): (slot, tx_index, event_index, signature). Without it (the live arena tape): receive order,
        (slot, t_recv_ms, signature, event_index). The two forms never meet in one pool."""
        sig = str(row.get("signature") or "")
        ev = int(row["event_index"]) if row.get("event_index") is not None else 0
        if row.get("tx_index") is not None:
            return (slot, 0, int(row["tx_index"]), ev, sig)
        return (slot, 1, t_ms, sig, ev)

    def on_gap(self, row: Mapping[str, Any]) -> None:
        """A feed gap row (type "gap"). Only data_loss gaps exclude trades; the others are counted. The lost interval is from the engine's last row before
        the gap (and silent_ms / the slot jump when the row gives them) to the gap row's time."""
        at = int(row["t_recv_ms"])
        silent = int(row.get("silent_ms") or 0)
        t_lo = min(self.hw_ms if self.hw_ms is not None else at, at - silent)
        s_hi = max(int(row.get("to_slot") or row.get("slot") or 0), self.hw_slot)
        if not row.get("data_loss"):
            self._count("gap_soft")
            return
        self._count("gap_data_loss")
        self.gaps.append(Gap(row.get("prog"), t_lo, at, max(self.hw_slot, 0), s_hi, str(row.get("kind"))))

    def on_row(self, row: Mapping[str, Any]) -> None:
        typ = row.get("type", "trade")
        if typ == "gap":
            self.on_gap(row)
            return
        venue = VENUE_ALIASES.get(str(row.get("prog") or row.get("venue")))
        if venue is None or typ != "trade":
            return
        pool = row.get("pool") or row.get("mint")
        if not pool or row.get("slot") is None or row.get("side") not in ("buy", "sell") or row.get("t_recv_ms") is None:
            self._count("rows_skipped")
            return
        slot, t_ms = int(row["slot"]), int(row["t_recv_ms"])
        h = self.pools.get(pool)
        if h is None:
            h = self.pools[pool] = PoolHist(pool, row.get("mint"), venue)
        key = self.order_key(row, slot, t_ms)
        dedupe = (slot, str(row.get("signature") or ""), int(row["event_index"]) if row.get("event_index") is not None else 0,
                  int(row["tx_index"]) if row.get("tx_index") is not None else -1)
        v, vsrc = self._v_of(h, row, venue)
        if not h.add(key, dedupe, slot, row["side"] == "buy", float(row["sol_lamports"]), float(row["token_raw"]), float(row["quote_reserve"]),
                     float(row["base_reserve"]), v, vsrc):
            self._count("rows_dup")
            return
        self._late_check(pool, slot, key, row)
        if self.hw_ms is None or t_ms > self.hw_ms:
            self.hw_ms, self.hw_ms_slot = t_ms, slot
        elif t_ms == self.hw_ms:
            self.hw_ms_slot = max(self.hw_ms_slot, slot)
        ts = row.get("event_ts") or row.get("block_time")
        self.clock.observe(slot, int(ts) if ts else None)
        if slot > self.hw_slot:
            self.hw_slot = slot
            for p in list(self.pending):
                self._try_pending(p, final=False)
        else:
            self._try_pending(pool, final=False)

    def _late_check(self, pool: str, slot: int, key: tuple, row: Mapping[str, Any]) -> None:
        """A print that arrives after a position on its pool was priced, and sorts before the print that priced it (slot < need, or slot == need and an earlier
        key; slot <= need when the state was derived), is a LATE print. It is written to the ledger (counted per book by the scorer); if a logged gap overlaps
        the position's span the row says so and the scorer excludes that trade under the gap rule. The priced row itself is write-once and not changed."""
        lst = self.priced.get(pool)
        if not lst:
            return
        keep = []
        for e in lst:
            if self.hw_slot > e["need"] + LATE_KEEP_SLOTS:
                continue
            keep.append(e)
            if slot < e["need"] or (slot == e["need"] and (e["trigger"] is None or key < e["trigger"])):
                lid = hashlib.sha256(f"{e['pid']}|{slot}|{row.get('signature')}|{row.get('event_index')}".encode()).hexdigest()[:16]
                overlap = self._gap_hit(e["prog"], e["span"]) is not None
                self.ledger.append({"schema": SCHEMA, "kind": "late_print", "trade_id": f"{e['pid']}-late-{lid}", "position_id": e["pid"], "bot_id": e["bot_id"],
                                    "decision_ms": {"entry": e["entry_ms"], "exit": None}, "slot": slot, "signature": row.get("signature"),
                                    "t_recv_ms": row.get("t_recv_ms"), "gap_overlap": overlap, "written_ms": self.now_fn()})
                self._count("late_prints")
        self.priced[pool] = keep

    def sps(self) -> float | None:
        s = self.sps_fn() if self.sps_fn else self.clock.sps()
        return s if sps_ok(s) else None

    def slot_at(self, ms: int, sps: float) -> int:
        """The tape slot at wall-clock `ms`: the latest feed row's (slot, t_recv_ms) plus the elapsed slots. A lower bound of the chain's slot (feed delay)."""
        assert self.hw_ms is not None
        return self.hw_ms_slot + int(math.floor((int(ms) - self.hw_ms) / (sps * 1000.0) + 1e-9))

    def _check_instant(self, decision_ms: int, decision_slot: int) -> None:
        if self.hw_ms is None:
            raise ArenaRefused("no_feed_rows: no feed rows seen yet")
        if int(decision_ms) < self.hw_ms:
            raise ArenaRefused(f"stale_decision: decision_ms {decision_ms} is earlier than the engine's high-water mark {self.hw_ms} (a hindsight decision)")
        if int(decision_slot) < self.hw_slot - self.cfg.slot_tolerance:
            raise ArenaRefused(f"stale_decision: decision_slot {decision_slot} is earlier than the engine's high-water slot {self.hw_slot}")
        now = self.now_fn()
        if int(decision_ms) > now + self.cfg.max_future_ms:
            raise ArenaRefused("future_decision: decision_ms is in the future")
        if self.cfg.max_past_ms is not None and int(decision_ms) < now - self.cfg.max_past_ms:
            raise ArenaRefused("stale_decision: decision_ms is older than max_past_ms at the write instant")

    # --- bot API
    def _bot(self, bot_id: str) -> dict[str, Any]:
        reg = load_registry(self.registry_path)
        if bot_id not in reg:
            raise ArenaRefused(f"bot {bot_id} is not registered (register_bot first: the frozen sha is stamped from the registry)")
        return reg[bot_id]

    def buy(self, bot_id: str, pool: str, sol_in: float | None, decision_ms: int, decision_slot: int, meta: Mapping[str, Any] | None = None) -> str:
        """Open a paper position decided at the instant decision_ms (the slot the bot saw is decision_slot). Priced at every configured stake (0.25 / 0.5 /
        1.0 SOL), so `sol_in` is the bot's own nominal size and is only recorded. An "open" row is written to the ledger BEFORE anything is priced.
        Returns the position_id (deterministic in bot, pool, decision)."""
        reg = self._bot(bot_id)
        if int(decision_ms) < reg["clock_start_ms"]:
            raise ArenaRefused(f"before_clock: decision_ms {decision_ms} is before bot {bot_id}'s clock {reg['clock_start_ms']}")
        if reg["stopped_at_ms"] is not None and int(decision_ms) >= reg["stopped_at_ms"]:
            raise ArenaRefused(f"stopped: bot {bot_id} is stopped")
        pid = hashlib.sha256(f"{bot_id}|{pool}|{int(decision_ms)}|{int(decision_slot)}".encode()).hexdigest()[:16]
        try:
            h = self.pools.get(pool)
            if h is None or not h.slot:
                raise ArenaRefused(f"no_prints: no prints seen for pool {pool}")
            self._check_instant(decision_ms, decision_slot)
            sps = self.sps()
            if sps is None:
                raise ArenaRefused("clock_not_ready: slot clock not ready (sps unknown)")
        except ArenaRefused as exc:  # a refused buy is still a decision: it goes in the decision log and counts toward the excluded share
            self.ledger.append({"schema": SCHEMA, "kind": "refused", "trade_id": f"{pid}-refused", "position_id": pid, "bot_id": bot_id,
                                "frozen_sha": reg["frozen_sha"], "decision_ms": {"entry": int(decision_ms), "exit": None},
                                "decision_slot": {"entry": int(decision_slot), "exit": None}, "pool": pool, "reason": str(exc).split(":")[0],
                                "detail": str(exc)[:200], "bot_sol_in": sol_in, "meta": dict(meta or {}), "stack": self.cfg.as_dict(), "written_ms": self.now_fn()})
            self._count("refused_" + str(exc).split(":")[0])
            raise
        if pid in self.positions:
            raise ArenaRefused(f"duplicate: position {pid} already open for this decision")
        sd = max(int(decision_slot), self.slot_at(decision_ms, sps))
        pos = Position(pid, bot_id, reg["frozen_sha"], pool, h.mint, h.venue, int(decision_ms), int(decision_slot), sd, float(sps),
                       None if sol_in is None else float(sol_in), dict(meta or {}))
        self.ledger.append({"schema": SCHEMA, "kind": "open", "trade_id": f"{pid}-open", "position_id": pid, "bot_id": bot_id, "frozen_sha": reg["frozen_sha"],
                            "decision_ms": {"entry": pos.decision_ms, "exit": None}, "decision_slot": {"entry": pos.decision_slot, "exit": None},
                            "effective_slot": {"entry": sd, "exit": None}, "pool": pool, "mint": h.mint, "prog": h.venue, "sps_entry": pos.sps_entry,
                            "bot_sol_in": pos.bot_sol_in, "meta": pos.meta, "stack": self.cfg.as_dict(), "written_ms": self.now_fn()})
        self.positions[pid] = pos
        return pid

    def recover(self) -> int:
        """Rebuild open positions from the ledger after a restart: every "open" row with no "trade" / "excluded" row. The pool history of a recovered position
        has a hole (the engine was down), so it is closed as an excluded trade (reason engine_restart) when its sell is decided; one never sold stays open and
        the scorer counts it as a restart orphan (the read is NOT_DECIDABLE)."""
        n = 0
        for bot_id in load_registry(self.registry_path):
            rows = self.ledger.read_bot(bot_id)
            closed = {r["position_id"] for r in rows if r.get("kind", "trade") in ("trade", "excluded")}
            for r in rows:
                if r.get("kind") == "open" and r["position_id"] not in closed and r["position_id"] not in self.positions:
                    self.positions[r["position_id"]] = Position(r["position_id"], bot_id, r["frozen_sha"], r["pool"], r["mint"], r["prog"], r["decision_ms"]["entry"],
                                                                r["decision_slot"]["entry"], r["effective_slot"]["entry"], r["sps_entry"], r["bot_sol_in"],
                                                                r["meta"], restarted=True)
                    n += 1
        return n

    def _k(self, lat: float, sps: float) -> int:
        return int(F.k_slots(lat, sps, self.cfg.k_rounding))

    def sell(self, bot_id: str, position_id: str, frac: float, decision_ms: int, decision_slot: int, reason: str = "") -> None:
        pos = self.positions.get(position_id)
        if pos is None or pos.bot_id != bot_id:
            raise ArenaRefused(f"no open position {position_id} for bot {bot_id}")
        if pos.exit_slot is not None:
            raise ArenaRefused(f"position {position_id} already has a sell")
        if frac != 1.0:
            raise ArenaRefused("partial sells are not supported in v1 (frac must be 1.0)")
        self._check_instant(decision_ms, decision_slot)
        sps = self.sps()
        if sps is None:
            raise ArenaRefused("slot clock not ready (sps unknown)")
        xs = max(int(decision_slot), self.slot_at(decision_ms, sps))
        k_slow = max(self._k(lat, pos.sps_entry) for lat in LATS)
        if xs < pos.sd + k_slow + 1:
            raise ArenaRefused("sell decided before the slower entry leg lands")
        try:
            F.exit_lag_slots(sps)
        except AssertionError as exc:
            raise ArenaRefused(str(exc)) from None
        pos.exit_ms, pos.exit_slot, pos.xs, pos.sps_exit, pos.reason = int(decision_ms), int(decision_slot), xs, float(sps), reason
        if pos.restarted:
            self._write_excluded(pos, "engine_restart", None)
            del self.positions[position_id]
            return
        self.pending.setdefault(pos.pool, []).append(position_id)
        self._try_pending(pos.pool, final=False)

    # --- pricing
    def _needed_slot(self, pos: Position) -> int:
        x_max = pos.sd + max(self._k(lat, pos.sps_entry) for lat in LATS)
        x_exit = pos.xs + F.exit_lag_slots(pos.sps_exit)  # type: ignore[operator,arg-type]
        return max(x_max, x_exit) + 1

    def _span(self, pos: Position, need: int, src: str) -> tuple[int, int, int, int]:
        """(t_lo, t_hi, s_lo, s_hi) of decision-to-exit. A derived_last exit extends the span to the resolution instant (the prints it stands in for may sit in
        a gap). Being derived is NOT an exclusion by itself."""
        t_hi = pos.exit_ms + int((F.exit_lag_slots(pos.sps_exit) + 1) * pos.sps_exit * 1000)  # type: ignore[operator,arg-type]
        s_hi = need
        if src == "derived_last":
            t_hi, s_hi = max(t_hi, self.hw_ms or t_hi), max(s_hi, self.hw_slot)
        return pos.decision_ms, t_hi, pos.sd, s_hi

    def _gap_hit(self, prog: str, span: tuple[int, int, int, int]) -> Gap | None:
        t_lo, t_hi, s_lo, s_hi = span
        for g in self.gaps:
            if g.prog not in (None, prog, "all") and not (g.prog == "pumpfun" and prog == "pump_bonding"):
                continue
            if (g.t_lo <= t_hi and g.t_hi >= t_lo) or (g.s_lo <= s_hi and g.s_hi >= s_lo):
                return g
        return None

    def _exclusion(self, pos: Position, h: PoolHist, src: str, need: int) -> str | None:
        """DEC-031 section 1.4: a logged data_loss gap in the decision-to-exit span, or a needed print without V, excludes the trade (counted, never priced)."""
        if self._gap_hit(pos.prog, self._span(pos, need, src)) is not None:
            return "feed_gap"
        if pos.prog == "pumpswap":
            a = h.arrays()
            sel = (a["sl"] >= pos.sd) & (a["sl"] <= need)
            if (~a["vok"][sel]).any():
                return "missing_v"
        return None

    def _v_sources(self, pos: Position, h: PoolHist) -> dict[str, Any]:
        """Where V came from for the print that defines the entry state (1.3 s leg), the print that defines the exit state, and every print in the span."""
        if pos.prog != "pumpswap":
            return {"entry": "na", "exit": "na", "span": {}}
        a = h.arrays()
        sl = a["sl"]
        n = len(sl)
        k13 = self._k(LATS[0], pos.sps_entry)
        je = min(int(np.searchsorted(sl, pos.sd + k13 + 1, "left")), n - 1)
        jx = min(int(np.searchsorted(sl, pos.xs + F.exit_lag_slots(pos.sps_exit) + 1, "left")), n - 1)  # type: ignore[operator,arg-type]
        lo, hi = int(np.searchsorted(sl, pos.sd, "left")), min(int(np.searchsorted(sl, pos.xs + 1, "right")), n)
        span: dict[str, int] = {}
        for x in h.vsrc[lo:hi]:
            span[x] = span.get(x, 0) + 1
        return {"entry": h.vsrc[je], "exit": h.vsrc[jx], "span": span}

    def _try_pending(self, pool: str, final: bool) -> None:
        ids = self.pending.get(pool)
        if not ids:
            return
        h = self.pools[pool]
        keep = []
        for pid in ids:
            pos = self.positions[pid]
            need = self._needed_slot(pos)
            ready = h.last_slot >= need or self.hw_slot >= need + self.cfg.grace_slots or final
            if not ready:
                keep.append(pid)
                continue
            src = "tape" if h.last_slot >= need else "derived_last"
            try:
                why = self._exclusion(pos, h, src, need)
                if why:
                    self._write_excluded(pos, why, src)
                else:
                    self._price_and_write(pos, h, src)
                trig = h.keys[int(np.searchsorted(h.arrays()["sl"], need, "left"))] if src == "tape" else None
                self.priced.setdefault(pool, []).append({"pid": pid, "bot_id": pos.bot_id, "prog": pos.prog, "entry_ms": pos.decision_ms, "need": need,
                                                         "trigger": trig, "span": self._span(pos, need, src)})
            except Exception as exc:  # a pricing failure must not drop the position silently: it stays open (an orphan in the read)
                self.last_error[pid] = f"{type(exc).__name__}: {exc}"
                self._count("price_errors")
                keep.append(pid)
                continue
            del self.positions[pid]
        if keep:
            self.pending[pool] = keep
        else:
            self.pending.pop(pool, None)

    def flush(self) -> None:
        """End of a finite tape: price every pending sell on the derived state after the last print (stack behaviour)."""
        for p in list(self.pending):
            self._try_pending(p, final=True)

    def prune(self) -> int:
        """Drop the history of pools with no open position that have been quiet for HIST_KEEP_SLOTS."""
        busy = {p.pool for p in self.positions.values()}
        drop = [k for k, h in self.pools.items() if k not in busy and h.last_slot < self.hw_slot - HIST_KEEP_SLOTS]
        for k in drop:
            del self.pools[k]
        return len(drop)

    def leg_inputs(self, pos: Position, h: PoolHist) -> dict[str, Any]:
        """Entry / exit states for both latencies (no stake yet)."""
        cfg = self.cfg
        a = h.arrays()
        k = {lat: self._k(lat, pos.sps_entry) for lat in LATS}
        lag = F.exit_lag_slots(pos.sps_exit)  # type: ignore[arg-type]
        if pos.prog == "pumpswap":
            ms = mint_states(a["sl"], a["ib"], a["so"], a["tk"], a["q"], a["b"], a["v"], np.array([pos.sd]), pos.sps_entry, t_marks=np.array([0]),
                             slot_s_marks=np.array([pos.sps_exit]), clock_slot_marks=np.array([pos.xs]), lats=LATS, rounding=cfg.k_rounding)
            return {"ref": float(ms["spot"][0]), "Qe": {l: float(ms["Qe"][l][0]) for l in LATS}, "Be": {l: float(ms["Be"][l][0]) for l in LATS},
                    "ssb": {l: float(ms["ssb"][l][0]) for l in LATS}, "nearby": {l: float(ms["nearby"][l][0]) for l in LATS},
                    "Qx": float(ms["Qx"][0]), "Bx": float(ms["Bx"][0]), "land": {l: pos.sd + k[l] for l in LATS}}
        x_land = {l: pos.sd + k[l] for l in LATS}
        bs = bonding_states(h, pos.sd, x_land, pos.xs + lag, int(round(F.NEARBY_WINDOW_S / pos.sps_entry)))  # type: ignore[operator]
        bs["land"] = x_land
        return bs

    def _base_row(self, pos: Position, kind: str, s_sol: float, src: str | None) -> dict[str, Any]:
        return {"schema": SCHEMA, "kind": kind, "trade_id": f"{pos.position_id}-s{int(round(s_sol * 1000))}", "position_id": pos.position_id,
                "bot_id": pos.bot_id, "frozen_sha": pos.frozen_sha, "decision_ms": {"entry": pos.decision_ms, "exit": pos.exit_ms},
                "decision_slot": {"entry": pos.decision_slot, "exit": pos.exit_slot}, "effective_slot": {"entry": pos.sd, "exit": pos.xs},
                "pool": pos.pool, "mint": pos.mint, "prog": pos.prog, "stake_sol": s_sol, "bot_sol_in": pos.bot_sol_in, "exit_reason": pos.reason,
                "meta": pos.meta, "state_src": src, "stack": dict(self.cfg.as_dict(), sps_entry=pos.sps_entry, sps_exit=pos.sps_exit),
                "written_ms": self.now_fn()}

    def _write_excluded(self, pos: Position, reason: str, src: str | None) -> None:
        for s_sol in self.cfg.stakes_sol:
            self.ledger.append(dict(self._base_row(pos, "excluded", s_sol, src), exclusion_reason=reason, legs=None, calc=None))
        self._count(f"excluded_{reason}")

    def build_rows(self, pos: Position, h: PoolHist, src: str) -> list[dict[str, Any]]:
        cfg = self.cfg
        st = self.leg_inputs(pos, h)
        vs = self._v_sources(pos, h)
        rules = rules_of(cfg)
        rows = []
        for s_sol in cfg.stakes_sol:
            stake = float(round(s_sol * 1e9))
            legs: dict[str, Any] = {r: {} for r in rules}
            calc: dict[str, Any] = {}
            for lat in LATS:
                tag = LAT_TAG[lat]
                calc[tag] = {"landing_slot": int(st["land"][lat]), "Qe": st["Qe"][lat], "Be": st["Be"][lat], "spot": st["ref"], "ssb": st["ssb"][lat],
                             "nearby_lamports": st["nearby"][lat], "Qx": st["Qx"], "Bx": st["Bx"], "by_rule": {}}
                for rule in rules:
                    r = price_leg(pos.prog, stake, st, lat, cfg, rule)
                    legs[rule][f"flat_{tag}"] = {"state": r["state"], "net_lamports": r["flat"]}
                    legs[rule][f"press_{tag}"] = {"state": r["state"], "net_lamports": r["press"]}
                    calc[tag]["by_rule"][rule] = {"pnl_nofail": r["pnl"], "filled": r["filled"], "state": r["state"], "strand": r["strand"],
                                                  "e1_extra": r["e1_extra"], "p_press_provisional": r["p"]}
            rows.append(dict(self._base_row(pos, "trade", s_sol, src), legs=legs, calc=calc, v_src=vs))
        return rows

    def _price_and_write(self, pos: Position, h: PoolHist, src: str) -> None:
        for row in self.build_rows(pos, h, src):
            self.ledger.append(row)


def row_digest(rows: list[Mapping[str, Any]]) -> str:
    return hashlib.md5("\n".join(canon(r) for r in rows).encode()).hexdigest()


__all__ = ["ArenaEngine", "ArenaRefused", "Ledger", "LedgerConflict", "StackConfig", "register_bot", "stop_bot", "load_registry", "curve_trip", "price_leg",
           "book_fail_legs", "LEG_KEYS", "STAKES_SOL", "LATS", "SCHEMA", "STRAND_STATES", "canon", "utc_day"]
