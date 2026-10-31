#!/usr/bin/env python3
"""r4b_feed_adapter: arena tape rows (tools/arena_tape_schema.md) -> the walker-schema rows tools/r4b_live.Engine ingests. Pure mapping, no I/O, no
network, no key. Tests use synthetic dict rows only; no tape file is read here.

Mapping (one tape row in, a list of `Mapped` out)
  trade (pumpswap, pump_bonding)  -> kind "trade": the keys of r4b_layout.TRADE_COLS plus quote_mint. block_time := event_ts, tx_index := None,
                                     everything else copied unchanged (slot, event_index, signature, trader, side, sol_lamports, token_raw,
                                     quote_reserve, base_reserve, pool, mint, virtual_quote_reserves). No re-basing of reserves.
  complete                        -> kind "migration", type "complete" (mint, slot, block_time := event_ts)
  pool_create                     -> kind "migration", type "migration" (mint, pool, quote_mint, slot); also registers pool -> mint
  create                          -> kind "create" (mint, creator, is_mayhem_mode as given or None, slot, block_time := event_ts)
  gap                             -> kind "gap": a GapRecord (kind, instant_ms = to_ms, span, data_loss, closed); `halts_buys` is True unless the
                                     row says data_loss is false (fail closed). The live loop halts buys on it; the engine never sees a gap.
  pool_info                       -> nothing out; registers pool -> mint so later trades of that pool resolve. It makes NO migration row, so a pool whose
                                     CreatePool this process did not see is never an r4-b member (the engine needs the migration row's pool).
  hb, start, stop                 -> nothing out (counted in `stats`).
  anything else, or a row with a missing / mistyped required field -> kind "reject" (Reject: reason, type, signature). A reject is a data fault and
                                     `halts_buys` is True, except the two unresolved-pool reasons below.

Unresolved PumpSwap rows (mint null until the pool is known): buffered per pool, never guessed. They are emitted, in arrival order, right after the
pool_create (or pool_info) row that names the pool. A buffered row older than `pending_s` of event_ts (against the newest event_ts seen), or over the
per-pool / pool-count bounds, is dropped and reported as Reject "unresolved_expired" / "unresolved_overflow" (halts_buys False: such a pool has no
migration row in this feed, so it cannot be a member).

Disclosed differences between this live feed and the paper (walker) rows the book was frozen on. None of these is measured here.
  * No tx_index: the adapter writes None, which the engine sorts first inside its slot (as training's coalesce -1); within-slot order is ARRIVAL order,
    not chain order. Rows of one slot can reach the engine in another order than the chain's, which can move s0 and the within-slot price path.
  * block_time is the chain event's own timestamp (event_ts, seconds), not getBlock's blockTime; they can differ by a second in some slots.
  * Feed delay: the tape row is seen after the chain event (about 7 slots on the public logsSubscribe feed, as stated in the lane brief, not re-measured
    here); the decision for mark T therefore sees fewer late rows than the walker did. Commitment is "confirmed"; a dropped transaction is a gap or nothing.
  * pool_create has no event_ts in the schema, so a migration row's block_time is event_ts when present else None; the engine does not read it.
  * quote_mint is passed through. Pools quoted in a token other than WSOL are NOT filtered here (the engine only admits WSOL pools as members), so their
    prints enter the market aggregates (m_vps_*) with quote-unit amounts. Whether the walker tape kept them in those aggregates is NOT verified here.
  * The tape's pump_bonding sol_lamports is fee-excluded (schema); whether the walker rows used the same basis (it feeds c_bond_buy / c_bond_sell /
    m_vbd_*) is NOT verified here.
  * Duplicates are not removed (the feed de-duplicates on (signature, type, event_index)); a restart gap is reported as a gap row, not repaired.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Mapping

VENUE_OF_PROG = {"pumpswap": "pumpswap", "pumpfun": "pump_bonding"}
IGNORED_TYPES = ("hb", "start", "stop")
PENDING_S = 900  # unresolved PumpSwap rows are kept this many seconds of event_ts
MAX_ROWS_PER_POOL = 500
MAX_POOLS = 5_000
SWEEP_S = 60


@dataclass(frozen=True)
class GapRecord:
    kind: str
    prog: str | None
    instant_ms: int | None  # to_ms (the gap's end in t_recv_ms), else the row's t_recv_ms
    from_ms: int | None
    to_ms: int | None
    data_loss: bool | None
    closed: bool | None

    @property
    def halts_buys(self) -> bool:
        return self.data_loss is not False  # missing or unknown: fail closed


@dataclass(frozen=True)
class Reject:
    reason: str
    type: str | None
    signature: str | None
    n: int = 1
    halts_buys: bool = True


@dataclass(frozen=True)
class Mapped:
    kind: str  # "clock" (clock only, tip-history host) | "trade" | "market" (unresolved PumpSwap row: market totals only) | "resolved" (that row once its mint is known: per-mint only) | "migration" | "create" | "gap" | "reject"
    row: Any  # dict (trade / migration / create), GapRecord, Reject

    @property
    def halts_buys(self) -> bool:
        return bool(getattr(self.row, "halts_buys", False))


def _is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _is_str(x: Any) -> bool:
    return isinstance(x, str) and x != ""


def _bad(row: Mapping[str, Any], checks: Mapping[str, Any]) -> str | None:
    """The first required field that fails its check (name), else None."""
    for name, ok in checks.items():
        if not ok(row.get(name)):
            return name
    return None


def map_trade(row: Mapping[str, Any], mint: str | None = None, quote_mint: str | None = None) -> dict | Reject:
    """One tape `trade` row as a walker trade row, or a Reject. `mint` / `quote_mint` fill a null mint / quote_mint (a resolved pool); the row's own
    non-null values are never overwritten. For PumpSwap the returned mint may still be None (unresolved): the caller buffers it."""
    sig = row.get("signature") if isinstance(row.get("signature"), str) else None
    venue = row.get("venue")
    if venue not in ("pumpswap", "pump_bonding") or VENUE_OF_PROG.get(row.get("prog")) != venue:
        return Reject("bad_venue", "trade", sig)
    checks = {"slot": _is_int, "event_ts": _is_int, "trader": _is_str, "side": lambda x: x in ("buy", "sell"), "sol_lamports": _is_int,
              "token_raw": _is_int, "quote_reserve": _is_int, "base_reserve": _is_int, "signature": _is_str}
    if venue == "pumpswap":
        checks |= {"pool": _is_str, "virtual_quote_reserves": _is_int}
    else:
        checks |= {"mint": _is_str}
    bad = _bad(row, checks)
    if bad:
        return Reject(f"bad_{bad}", "trade", sig)
    ei = row.get("event_index")
    if ei is not None and not _is_int(ei):
        return Reject("bad_event_index", "trade", sig)
    m = row.get("mint") if row.get("mint") is not None else mint
    return {"venue": venue, "mint": m, "trader": row["trader"], "side": row["side"], "sol_lamports": row["sol_lamports"], "token_raw": row["token_raw"],
            "quote_reserve": row["quote_reserve"], "base_reserve": row["base_reserve"], "pool": row.get("pool") if venue == "pumpswap" else None,
            "slot": row["slot"], "signature": row["signature"], "event_index": ei, "tx_index": None, "block_time": row["event_ts"],
            "virtual_quote_reserves": row.get("virtual_quote_reserves") if venue == "pumpswap" else None,
            "quote_mint": row.get("quote_mint") if row.get("quote_mint") is not None else quote_mint}


def map_complete(row: Mapping[str, Any]) -> dict | Reject:
    sig = row.get("signature") if isinstance(row.get("signature"), str) else None
    bad = _bad(row, {"mint": _is_str, "slot": _is_int, "event_ts": _is_int})
    if bad:
        return Reject(f"bad_{bad}", "complete", sig)
    return {"type": "complete", "mint": row["mint"], "slot": row["slot"], "block_time": row["event_ts"], "tx_index": None,
            "event_index": row.get("event_index") if _is_int(row.get("event_index")) else None, "pool": None, "quote_mint": None}


def map_pool_create(row: Mapping[str, Any]) -> dict | Reject:
    sig = row.get("signature") if isinstance(row.get("signature"), str) else None
    bad = _bad(row, {"pool": _is_str, "mint": _is_str, "quote_mint": _is_str, "slot": _is_int})
    if bad:
        return Reject(f"bad_{bad}", "pool_create", sig)
    return {"type": "migration", "mint": row["mint"], "slot": row["slot"], "block_time": row["event_ts"] if _is_int(row.get("event_ts")) else None,
            "tx_index": None, "event_index": row.get("event_index") if _is_int(row.get("event_index")) else None, "pool": row["pool"],
            "quote_mint": row["quote_mint"]}


def map_create(row: Mapping[str, Any]) -> dict | Reject:
    sig = row.get("signature") if isinstance(row.get("signature"), str) else None
    bad = _bad(row, {"mint": _is_str, "slot": _is_int, "event_ts": _is_int})
    if bad:
        return Reject(f"bad_{bad}", "create", sig)
    mm = row.get("is_mayhem_mode")
    return {"type": "create", "mint": row["mint"], "creator": row.get("creator"), "is_mayhem_mode": mm if isinstance(mm, bool) else None,
            "slot": row["slot"], "block_time": row["event_ts"]}


def map_gap(row: Mapping[str, Any]) -> GapRecord | Reject:
    sig = row.get("signature") if isinstance(row.get("signature"), str) else None
    if not _is_str(row.get("kind")):
        return Reject("bad_kind", "gap", sig)
    ms = lambda k: row.get(k) if _is_int(row.get(k)) else None  # noqa: E731
    dl, cl = row.get("data_loss"), row.get("closed")
    return GapRecord(row["kind"], row.get("prog") if isinstance(row.get("prog"), str) else None, ms("to_ms") if ms("to_ms") is not None else ms("t_recv_ms"),
                     ms("from_ms"), ms("to_ms"), dl if isinstance(dl, bool) else None, cl if isinstance(cl, bool) else None)


class FeedAdapter:
    """Stateful wrapper over the pure maps: the pool -> (mint, quote_mint) resolver and the bounded buffer of unresolved PumpSwap rows.
    feed(row) returns the Mapped records to hand to the engine in order; sweep() reports expired buffers. One thread, like the engine."""

    def __init__(self, *, pending_s: int = PENDING_S, max_rows_per_pool: int = MAX_ROWS_PER_POOL, max_pools: int = MAX_POOLS) -> None:
        self.pending_s, self.max_rows_per_pool, self.max_pools = int(pending_s), int(max_rows_per_pool), int(max_pools)
        self.pools: dict[str, tuple[str, str]] = {}
        self.unresolved: dict[str, list[tuple[int, dict]]] = {}  # pool -> [(event_ts, walker row with mint None)]
        self.stats: Counter = Counter()
        self.now_ts = -1
        self._swept = -1

    def _register(self, row: Mapping[str, Any]) -> list[Mapped] | Reject:
        """pool -> (mint, quote_mint) from a pool_create / pool_info row; the buffered rows of the pool follow."""
        pool, mint, qm = row["pool"], row["mint"], row["quote_mint"]
        known = self.pools.get(pool)
        if known is not None and known != (mint, qm):
            return Reject("pool_mint_conflict", str(row.get("type")), row.get("signature"))
        self.pools[pool] = (mint, qm)
        out = []
        for _, w in self.unresolved.pop(pool, []):  # already counted in the market totals at arrival: per-mint path only
            out.append(Mapped("resolved", {**w, "mint": mint, "quote_mint": w["quote_mint"] if w["quote_mint"] is not None else qm}))
            self.stats["unresolved_resolved"] += 1
        return out

    def sweep(self, now_ts: int | None = None) -> list[Mapped]:
        """Drop buffered rows older than pending_s of event_ts; one Reject per pool (n rows)."""
        now = self.now_ts if now_ts is None else int(now_ts)
        self._swept = now
        out = []
        for pool in list(self.unresolved):
            keep = [(t, w) for t, w in self.unresolved[pool] if t >= now - self.pending_s]
            n = len(self.unresolved[pool]) - len(keep)
            if n:
                self.stats["unresolved_expired"] += n
                out.append(Mapped("reject", Reject("unresolved_expired", "trade", None, n, halts_buys=False)))
            if keep:
                self.unresolved[pool] = keep
            else:
                del self.unresolved[pool]
        return out

    def _trade(self, row: Mapping[str, Any]) -> list[Mapped]:
        pool = row.get("pool")
        known = self.pools.get(pool) if isinstance(pool, str) else None
        if known is not None and row.get("mint") is not None and row["mint"] != known[0]:
            return [Mapped("reject", Reject("pool_mint_conflict", "trade", row.get("signature") if isinstance(row.get("signature"), str) else None))]
        w = map_trade(row, *(known or (None, None)))
        if isinstance(w, Reject):
            return [Mapped("reject", w)]
        self.now_ts = max(self.now_ts, w["block_time"])
        out = self.sweep() if self.now_ts >= self._swept + SWEEP_S else []
        if w["venue"] == "pumpswap" and w["mint"] is None:  # unresolved: buffer, never guess
            lst = self.unresolved.get(w["pool"])
            if lst is None and len(self.unresolved) >= self.max_pools:  # not buffered, but still counted in the market totals
                return out + [Mapped("market", w), Mapped("reject", Reject("unresolved_overflow", "trade", w["signature"], halts_buys=False))]
            if lst is not None and len(lst) >= self.max_rows_per_pool:
                self.stats["unresolved_overflow"] += 1
                return out + [Mapped("market", w), Mapped("reject", Reject("unresolved_overflow", "trade", w["signature"], halts_buys=False))]
            self.unresolved.setdefault(w["pool"], []).append((w["block_time"], w))
            self.stats["unresolved_buffered"] += 1
            return out + [Mapped("market", w)]  # counted in the clock and market minutes now (as the walker layout counts every PumpSwap row)
        return out + [Mapped("trade", w)]

    def feed(self, row: Mapping[str, Any]) -> list[Mapped]:
        typ = row.get("type")
        self.stats[f"rows_{typ}"] += 1
        if typ == "trade":
            out = self._trade(row)
        elif typ == "complete":
            m = map_complete(row)
            out = [Mapped("reject" if isinstance(m, Reject) else "migration", m)]
        elif typ in ("pool_create", "pool_info"):
            m = map_pool_create(row) if typ == "pool_create" else (None if _bad(row, {"pool": _is_str, "mint": _is_str, "quote_mint": _is_str})
                                                                   else row)
            if m is None:
                out = [Mapped("reject", Reject("bad_pool_info", typ, None))]
            elif isinstance(m, Reject):
                out = [Mapped("reject", m)]
            else:
                reg = self._register({"pool": m["pool"] if typ == "pool_create" else row["pool"], "mint": row["mint"], "quote_mint": row["quote_mint"],
                                      "type": typ, "signature": row.get("signature")})
                if isinstance(reg, Reject):
                    out = [Mapped("reject", reg)]
                else:
                    out = ([Mapped("migration", m)] if typ == "pool_create" else []) + reg
        elif typ == "create":
            m = map_create(row)
            out = [Mapped("reject" if isinstance(m, Reject) else "create", m)]
        elif typ == "gap":
            m = map_gap(row)
            out = [Mapped("reject" if isinstance(m, Reject) else "gap", m)]
        elif typ in IGNORED_TYPES:
            out = []
        else:
            out = [Mapped("reject", Reject("unknown_type", str(typ), row.get("signature") if isinstance(row.get("signature"), str) else None))]
        for mp in out:
            if mp.kind == "reject":
                self.stats[f"reject_{mp.row.reason}"] += mp.row.n
        return out


def apply(engine: Any, mapped: list, frn_fn: Any = None) -> list:
    """Feed adapter output into a tools.r4b_live.Engine in order: "trade" -> ingest_trade (market + per-mint), "market" -> market totals only (an
    unresolved PumpSwap row, mint None), "resolved" -> per-mint path only (the same row once its pool's mint is known), "migration" / "create" ->
    their ingests. Returns the gap records and rejects for the caller (a gap with halts_buys holds new buys)."""
    held = []
    for mp in mapped:
        k = mp.kind
        if k == "trade":
            engine.ingest_trade(mp.row, frn_fn() if frn_fn else None)
        elif k == "market":
            engine.ingest_trade(mp.row, None, market=True, per_mint=False)
        elif k == "resolved":
            engine.ingest_trade(mp.row, frn_fn() if frn_fn else None, market=False, per_mint=True)
        elif k == "migration":
            engine.ingest_migration(mp.row)
        elif k == "create":
            engine.ingest_create(mp.row)
        elif k == "clock":  # the tip-history host: sd(T) from a Helius row whose slot the tip tape owns (Engine.ingest_clock)
            engine.ingest_clock(mp.row["slot"], mp.row["block_time"])
        else:
            held.append(mp)
    return held
