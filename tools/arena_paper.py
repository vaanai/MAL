"""Forward paper arena: fill and cost engine (hunt20). PAPER ONLY. No key, no transaction, no RPC.

Bots call `ArenaEngine.buy(...)` / `.sell(...)`. The engine reads decoded trade rows (`observe.trade_decode` dicts plus `t_recv_ms`, `prog`) with
`.on_row(row)`, fills each decision from the tape and writes one write-once ledger row per (trade, stake) under
`/data/mal/arena/ledger/<bot_id>/trades-<UTC day of the entry decision>.jsonl`.

Nothing here re-derives the lab's cost model. It is imported:
  * `tools.r4b_book.mint_states`   entry / exit pool states, spot, same-slot buys, nearby buy SOL (the prep_l3 extraction behind sc.py's panel)
  * `tools.r4b_book.round_trip`    PumpSwap round trip: V in Q, fee tiers, own buy re-injected into the exit state, +26.08 bps / -16 bps haircut,
                                   505,000 lamports per send, rent 2,039,280 per filled trip, 1.15 guard (a guarded buy pays one send)
  * `tools.r4b_book.fail_legs`     flat 15% and pressure-fail (slope scale 1; intercept refit so mean p = 0.289) legs
  * `tools.h5_shadow.SlotClock`    the rolling slot clock (`--sps rolling`)
  * `tools.paper_curve_math`       the bonding-curve quote_buy / quote_sell / reserves_with_our_buy (the curve sim of tools/latency_curve.py)
Constants come from `tools.r4b_frozen`. The arena stack is the brief's: rent 2,039,280 per filled trip (EXP-027's QP-ruled stack refunds rent;
pass `rent=0` for that variant).

Conventions (confirmed against sc.py / r4b_book, not the looser wording in the brief):
  * PumpSwap rows carry PRE-trade reserves. A fill lands on END-bound state: the PRE-trade reserves of the FIRST print with slot >= landing slot + 1,
    i.e. the pool after every print in the landing slot. If no such print exists yet the state after the last print is derived (G's 1.25% constant,
    as mint_states does) once the global slot clock is `grace_slots` past it; the row records `state_src`.
  * Bonding rows carry POST-trade reserves. A fill lands on the POST-trade reserves of the LAST print with slot <= landing slot (the same END bound).
  * Landing slot = decision slot + k, k = ceil(latency / seconds-per-slot - 1e-9) by default (the brief; `k_rounding="round"` is the EXP-027 QP value),
    seconds-per-slot from the rolling slot clock at the decision. Exit fill = state after the slot decision + ceil(0.55 s / sps) (>= 2 slots).
  * Spot for the guard = post-trade price of the last print with slot < decision slot (mint_states).
  * Bonding fills have no portal fee (direct route, like the PumpSwap stack); the 26.08 / -16 bps haircut is applied to them too (it was measured on
    PumpSwap, so this is a conservative assumption, not a measurement).

Not supported in v1: partial sells (`frac` must be 1.0; refused otherwise), sells decided before the slower entry leg lands (refused).
"""

from __future__ import annotations

import bisect
import fcntl
import hashlib
import json
import math
import os
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
GRACE_SLOTS = 25
HIST_KEEP_SLOTS = 20_000
# Provisional pressure intercept stored in the ledger's press_* net_lamports. The AUTHORITATIVE pressure leg is refit per (bot, stake, latency) book by
# arena_score (the stack refits the intercept per cell so that mean p over the cell's filled sends is 0.289). Fit on the 300-case parity book (1.3 s).
PRESS_INTERCEPT_PROVISIONAL = -2.5

VENUE_ALIASES = {"pumpswap": "pumpswap", "pump_amm": "pumpswap", "pump_bonding": "pump_bonding", "pump": "pump_bonding", "bonding": "pump_bonding"}
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


def load_registry(path: str | os.PathLike = REGISTRY_PATH) -> dict[str, dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return {}
    doc = json.loads(p.read_text())
    return dict(doc.get("bots", {}))


def register_bot(bot_id: str, frozen_sha: str, clock_start_ms: int, path: str | os.PathLike = REGISTRY_PATH) -> dict[str, Any]:
    """Register a bot once. The same (bot_id, frozen_sha, clock) again is a no-op; a different sha or clock for an existing bot_id is REFUSED
    (a new version is a new bot_id)."""
    if not bot_id or "/" in bot_id or bot_id.startswith("."):
        raise ArenaRefused(f"bad bot_id {bot_id!r}")
    if not frozen_sha or not all(c in "0123456789abcdef" for c in frozen_sha.lower()) or len(frozen_sha) < 7:
        raise ArenaRefused("frozen_sha must be a git hex sha (7+ chars)")
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    lock = p.with_suffix(p.suffix + ".lock")
    with open(lock, "a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        bots = load_registry(p)
        entry = {"frozen_sha": frozen_sha, "clock_start_ms": int(clock_start_ms)}
        if bot_id in bots:
            old = {k: bots[bot_id].get(k) for k in entry}
            if old != entry:
                raise ArenaRefused(f"bot {bot_id} is registered with {old}; a changed sha or clock is a new bot_id, not an edit")
            return bots[bot_id]
        bots[bot_id] = entry
        tmp = p.with_suffix(p.suffix + ".tmp")
        with open(tmp, "w") as f:
            json.dump({"bots": bots}, f, sort_keys=True, indent=1, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
        _fsync_dir(p.parent)
        return entry


# ------------------------------------------------------------------------------------------------------------------------------ ledger
def canon(obj: Any) -> str:
    """Strict, canonical JSON (no NaN / Infinity, sorted keys)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False)


def utc_day(ms: int) -> str:
    return datetime.fromtimestamp(int(ms) / 1000.0, timezone.utc).strftime("%Y-%m-%d")


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
    # "sc": the stack sc.py / VERIFY priced (bit parity; a sell worth less than one send is a normal, tiny-proceeds sell).
    # "r4b": tools.r4b_book defaults (EXP-027 QP rulings): auto_stranded (proceeds < one send -> stranded price) and the C8 k-fall check. Harsher by
    # (send - proceeds + rent differences) on dust sells; 3 of 300 parity cases (one rugged pool) differ by 412,340 lamports each.
    strand_rule: str = "sc"

    def as_dict(self) -> dict[str, Any]:
        return {"send": self.send, "rent": self.rent, "h_in": self.h_in, "h_out": self.h_out, "k_rounding": self.k_rounding,
                "exit_lag_s": F.EXIT_LAG_S, "guard": F.GUARD, "portal_ppm": self.portal_ppm, "press_intercept_provisional": self.press_intercept, "strand_rule": self.strand_rule}


# ------------------------------------------------------------------------------------------------------------------------------ bonding curve
def curve_trip(stake: float, qe: float, be: float, ref: float | None, qx: float, bx: float, *, send: float, rent: float, h_in: float, h_out: float,
               portal_ppm: int = 0, rent_stranded: float = F.RENT_STRANDED, auto_stranded: bool = False) -> tuple[float, str, bool]:
    """One pre-graduation bonding-curve round trip in lamports -> (pnl, state, filled).

    (qe, be): POST-trade reserves the buy lands on. ref: decision-time price (SOL per token) for the 15% slippage guard, as latency_curve._try_buy.
    (qx, bx): POST-trade reserves the sell lands on; our buy is re-injected (reserves_with_our_buy) and the curve cannot pay more real SOL than it holds
    (quote_sell). A buy that cannot fill (completed curve, guard) is a miss and pays one send. A sell that cannot land strands the position
    (-stake - send - rent_stranded, the deciding stack's stranded price)."""
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


# ------------------------------------------------------------------------------------------------------------------------------ pool history
class PoolHist:
    """Ordered prints of one pool. PumpSwap reserves are PRE-trade, bonding POST-trade; V is per print."""

    __slots__ = ("pool", "mint", "venue", "keys", "seen", "slot", "isbuy", "sol", "tok", "q", "b", "v", "last_v", "_np", "_np_n")

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
        self.last_v: float | None = None
        self._np: dict[str, np.ndarray] | None = None
        self._np_n = -1

    def add(self, key: tuple, slot: int, isbuy: bool, sol: float, tok: float, q: float, b: float, v: float) -> bool:
        if key in self.seen:
            return False
        self.seen.add(key)
        i = len(self.keys) if (not self.keys or key >= self.keys[-1]) else bisect.bisect_right(self.keys, key)
        for lst, val in ((self.keys, key), (self.slot, slot), (self.isbuy, isbuy), (self.sol, sol), (self.tok, tok), (self.q, q), (self.b, b), (self.v, v)):
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
                        "v": np.asarray(self.v, dtype=float)}
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


def price_leg(prog: str, stake_lamports: float, st: Mapping[str, Any], lat: float, cfg: StackConfig) -> dict[str, Any]:
    """One (stake, latency) leg from extracted states `st` = {ref, Qe[lat], Be[lat], ssb[lat], nearby[lat], Qx, Bx}. Returns the no-fail pnl, the fill state
    and the flat / provisional-pressure legs (lamports)."""
    if prog == "pumpswap":
        pnl, filled, strand, bad, xbad = round_trip(np.array([stake_lamports]), st["Qe"][lat], st["Be"][lat], st["ref"], st["Qx"], st["Bx"], cfg.send,
                                                   cfg.rent, cfg.h_in, cfg.h_out, return_stranded=True, return_invalid=True, return_exit_invalid=True,
                                                   auto_stranded=cfg.strand_rule == "r4b", k_tol=F.K_TOL if cfg.strand_rule == "r4b" else None)
        pnl, filled = float(pnl[0]), bool(filled[0])
        state = "guarded" if not filled else "entry_invalid" if bad[0] else "exit_invalid" if xbad[0] else "stranded" if strand[0] else "filled"
    else:
        pnl, state, filled = curve_trip(stake_lamports, st["Qe"][lat], st["Be"][lat], st["ref"], st["Qx"], st["Bx"], send=cfg.send, rent=cfg.rent,
                                        h_in=cfg.h_in, h_out=cfg.h_out, portal_ppm=cfg.portal_ppm, auto_stranded=cfg.strand_rule == "r4b")
    flat, press, _, p = fail_legs(np.array([pnl]), np.array([filled]), np.array([st["ssb"][lat]]), np.array([st["nearby"][lat]]), cfg.send,
                                  intercept=cfg.press_intercept)
    return {"pnl": pnl, "filled": filled, "state": state, "flat": float(flat[0]), "press": float(press[0]), "p": float(p[0])}


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
    decision_slot: int
    sps_entry: float
    bot_sol_in: float | None
    meta: dict[str, Any]
    exit_ms: int | None = None
    exit_slot: int | None = None
    sps_exit: float | None = None
    reason: str | None = None
    written: bool = False


class ArenaEngine:
    def __init__(self, ledger_root: str | os.PathLike = LEDGER_ROOT, registry_path: str | os.PathLike = REGISTRY_PATH, cfg: StackConfig | None = None,
                 vmap: Mapping[str, Any] | None = None, clock: SlotClock | None = None, sps_fn: Callable[[], float | None] | None = None) -> None:
        self.ledger = Ledger(ledger_root)
        self.registry_path = registry_path
        self.cfg = cfg or StackConfig()
        self.vmap = vmap or {}
        self.clock = clock or SlotClock()
        self.sps_fn = sps_fn
        self.pools: dict[str, PoolHist] = {}
        self.positions: dict[str, Position] = {}
        self.pending: dict[str, list[str]] = {}  # pool -> position ids with a sell decided and not yet priced
        self.hw_slot = -1
        self.counters: dict[str, int] = {}
        self.last_error: dict[str, str] = {}

    # --- feed
    def _count(self, k: str) -> None:
        self.counters[k] = self.counters.get(k, 0) + 1

    def _v_of(self, h: PoolHist, row: Mapping[str, Any], venue: str) -> float:
        if venue != "pumpswap":
            return 0.0
        v = row.get("virtual_quote_reserves")
        if v is None:
            v = self.vmap.get(h.pool)
        if v is None:
            v = h.last_v if h.last_v is not None else 0
            self._count("v_missing")
        v = float(v)
        if not (0.0 <= v <= V_BAND_MAX):  # README rule: a V outside [0, 30 SOL] is not added
            v = 0.0
            self._count("v_out_of_band")
        h.last_v = v
        return v

    def on_row(self, row: Mapping[str, Any]) -> None:
        venue = VENUE_ALIASES.get(str(row.get("prog") or row.get("venue")))
        if venue is None or row.get("type", "trade") != "trade":
            return
        pool = row.get("pool") or row.get("mint")
        if not pool or row.get("slot") is None or row.get("side") not in ("buy", "sell"):
            self._count("rows_skipped")
            return
        slot = int(row["slot"])
        h = self.pools.get(pool)
        if h is None:
            h = self.pools[pool] = PoolHist(pool, row.get("mint"), venue)
        sig = row.get("signature")
        key = (slot, int(row["tx_index"]) if row.get("tx_index") is not None else -1, int(row["event_index"]) if row.get("event_index") is not None else 0,
               sig or "")
        v = self._v_of(h, row, venue)
        ok = h.add(key, slot, row["side"] == "buy", float(row["sol_lamports"]), float(row["token_raw"]), float(row["quote_reserve"]),
                   float(row["base_reserve"]), v)
        if not ok:
            self._count("rows_dup")
            return
        ts = row.get("event_ts") or row.get("block_time")
        self.clock.observe(slot, int(ts) if ts else None)
        if slot > self.hw_slot:
            self.hw_slot = slot
            for p in list(self.pending):
                self._try_pending(p, final=False)
        else:
            self._try_pending(pool, final=False)

    def sps(self) -> float | None:
        s = self.sps_fn() if self.sps_fn else self.clock.sps()
        return s if sps_ok(s) else None

    # --- bot API
    def _bot(self, bot_id: str) -> dict[str, Any]:
        reg = load_registry(self.registry_path)
        if bot_id not in reg:
            raise ArenaRefused(f"bot {bot_id} is not registered (register_bot first: the frozen sha is stamped from the registry)")
        return reg[bot_id]

    def buy(self, bot_id: str, pool: str, sol_in: float | None, decision_ms: int, decision_slot: int, meta: Mapping[str, Any] | None = None) -> str:
        """Open a paper position decided at (decision_ms, decision_slot). Priced at every configured stake (0.25 / 0.5 / 1.0 SOL), so `sol_in` is the
        bot's own nominal size and is only recorded. Returns the position_id (deterministic in bot, pool, decision)."""
        reg = self._bot(bot_id)
        h = self.pools.get(pool)
        if h is None or not h.slot:
            raise ArenaRefused(f"no prints seen for pool {pool}")
        sps = self.sps()
        if sps is None:
            raise ArenaRefused("slot clock not ready (sps unknown)")
        pid = hashlib.sha256(f"{bot_id}|{pool}|{int(decision_ms)}|{int(decision_slot)}".encode()).hexdigest()[:16]
        if pid in self.positions:
            raise ArenaRefused(f"position {pid} already open for this decision")
        self.positions[pid] = Position(pid, bot_id, reg["frozen_sha"], pool, h.mint, h.venue, int(decision_ms), int(decision_slot), float(sps),
                                       None if sol_in is None else float(sol_in), dict(meta or {}))
        return pid

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
        sps = self.sps()
        if sps is None:
            raise ArenaRefused("slot clock not ready (sps unknown)")
        k_slow = max(self._k(lat, pos.sps_entry) for lat in LATS)
        if int(decision_slot) < pos.decision_slot + k_slow + 1:
            raise ArenaRefused("sell decided before the slower entry leg lands")
        try:
            lag = F.exit_lag_slots(sps)
        except AssertionError as exc:
            raise ArenaRefused(str(exc)) from None
        del lag
        pos.exit_ms, pos.exit_slot, pos.sps_exit, pos.reason = int(decision_ms), int(decision_slot), float(sps), reason
        self.pending.setdefault(pos.pool, []).append(position_id)
        self._try_pending(pos.pool, final=False)

    # --- pricing
    def _needed_slot(self, pos: Position) -> int:
        x_max = pos.decision_slot + max(self._k(lat, pos.sps_entry) for lat in LATS)
        x_exit = pos.exit_slot + F.exit_lag_slots(pos.sps_exit)  # type: ignore[operator,arg-type]
        return max(x_max, x_exit) + 1

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
                self._price_and_write(pos, h, src)
            except Exception as exc:  # a pricing failure must not drop the position silently
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
            ms = mint_states(a["sl"], a["ib"], a["so"], a["tk"], a["q"], a["b"], a["v"], np.array([pos.decision_slot]), pos.sps_entry,
                             t_marks=np.array([0]), slot_s_marks=np.array([pos.sps_exit]), clock_slot_marks=np.array([pos.exit_slot]), lats=LATS,
                             rounding=cfg.k_rounding)
            return {"ref": float(ms["spot"][0]), "Qe": {l: float(ms["Qe"][l][0]) for l in LATS}, "Be": {l: float(ms["Be"][l][0]) for l in LATS},
                    "ssb": {l: float(ms["ssb"][l][0]) for l in LATS}, "nearby": {l: float(ms["nearby"][l][0]) for l in LATS},
                    "Qx": float(ms["Qx"][0]), "Bx": float(ms["Bx"][0]), "land": {l: pos.decision_slot + k[l] for l in LATS}}
        x_land = {l: pos.decision_slot + k[l] for l in LATS}
        bs = bonding_states(h, pos.decision_slot, x_land, pos.exit_slot + lag, int(round(F.NEARBY_WINDOW_S / pos.sps_entry)))  # type: ignore[operator]
        bs["land"] = x_land
        return bs

    def price_stake(self, prog: str, stake_lamports: float, st: Mapping[str, Any], lat: float) -> dict[str, Any]:
        return price_leg(prog, stake_lamports, st, lat, self.cfg)

    def build_rows(self, pos: Position, h: PoolHist, src: str) -> list[dict[str, Any]]:
        cfg = self.cfg
        st = self.leg_inputs(pos, h)
        rows = []
        for s_sol in cfg.stakes_sol:
            stake = float(round(s_sol * 1e9))
            legs: dict[str, Any] = {}
            calc: dict[str, Any] = {}
            for lat in LATS:
                tag = LAT_TAG[lat]
                r = self.price_stake(pos.prog, stake, st, lat)
                legs[f"flat_{tag}"] = {"state": r["state"], "net_lamports": r["flat"]}
                legs[f"press_{tag}"] = {"state": r["state"], "net_lamports": r["press"]}
                calc[tag] = {"landing_slot": int(st["land"][lat]), "Qe": st["Qe"][lat], "Be": st["Be"][lat], "spot": st["ref"], "ssb": st["ssb"][lat],
                             "nearby_lamports": st["nearby"][lat], "Qx": st["Qx"], "Bx": st["Bx"], "pnl_nofail": r["pnl"], "filled": r["filled"],
                             "state": r["state"], "p_press_provisional": r["p"]}
            tid = f"{pos.position_id}-s{int(round(s_sol * 1000))}"
            rows.append({"schema": SCHEMA, "trade_id": tid, "position_id": pos.position_id, "bot_id": pos.bot_id, "frozen_sha": pos.frozen_sha,
                         "decision_ms": {"entry": pos.decision_ms, "exit": pos.exit_ms}, "decision_slot": {"entry": pos.decision_slot, "exit": pos.exit_slot},
                         "pool": pos.pool, "mint": pos.mint, "prog": pos.prog, "stake_sol": s_sol, "bot_sol_in": pos.bot_sol_in, "exit_reason": pos.reason,
                         "meta": pos.meta, "state_src": src, "stack": dict(cfg.as_dict(), sps_entry=pos.sps_entry, sps_exit=pos.sps_exit),
                         "legs": legs, "calc": calc})
        return rows

    def _price_and_write(self, pos: Position, h: PoolHist, src: str) -> None:
        for row in self.build_rows(pos, h, src):
            self.ledger.append(row)


def row_digest(rows: list[Mapping[str, Any]]) -> str:
    return hashlib.md5("\n".join(canon(r) for r in rows).encode()).hexdigest()


__all__ = ["ArenaEngine", "ArenaRefused", "Ledger", "LedgerConflict", "StackConfig", "register_bot", "load_registry", "curve_trip", "price_leg", "LEG_KEYS",
           "STAKES_SOL", "LATS", "SCHEMA", "canon", "utc_day"]
