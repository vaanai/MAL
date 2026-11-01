#!/usr/bin/env python3
"""arena_tip_adapter: the fast-0 getBlock tip follower's tape as ARENA-TAPE rows (QP-RULING-1101-hv4-reads section 4.1 (B), items 2 to 4). PAPER only.
READ-ONLY: it opens files for reading through tools.r4b_tip_reader.TipReader (the guarded reader of #1155/#1158, imported and NOT changed), never writes, moves or
truncates, holds no key and sends nothing. Tests use synthetic files in tmp dirs and the REAL tools.fast_tip_follower.TipFollower with a fake RPC; no real tip file,
arena tape or mirror is opened by tests or by agents. The module is a LIBRARY: it has no CLI that reads /var/lib/mal/sealed. A host that does (the pinned arena engine
host, once QP-RULING item 1 and the SEALS-BRIEF entry exist) builds an ArenaTipSource and feeds `poll()` to ArenaEngine.on_row / ArenaState.apply.

WHY. The public arena feed (tools/arena_feed.py, logsSubscribe) logs a data_loss slot_jump about every 28 s; every ArenaEngine trade held longer than a minute or two
overlaps a gap and is excluded under DEC-031 1.4, which made the HV4 X-arm reads NOT_DECIDABLE_5PCT. The tip follower is complete by construction (coverage 100.000%
against the chain, LAB_STATE row 115), decodes with the walker's own code (EXP027_DECODER_REF blobs) and carries real tx_index and block_time.

WHAT COMES OUT (arena_tape_v1 shape plus two keys, tx_index and block_time; the schema delta is in docs/design/arena-tip-adapter.md)
  trade        prog pumpswap | pumpfun by venue; the walker row's own keys minus the tape's TRADE_DROP minus `source` and the tip-only singular `virtual_quote_reserve`;
               `virtual_quote_reserves` (event V, plural) is kept, `tx_index` and `block_time` are kept, `t_recv_ms` is the follower's receive time.
  create       prog pumpfun (mint, trader, creator, event_ts, reserves, token_raw, quote_mint, is_mayhem_mode, name[:32], symbol[:16])
  complete     prog pumpfun (trader, mint, bonding_curve, event_ts, quote_mint)
  pool_create  prog pumpswap, from the walker's `migration` row (the CompletePumpAmmMigrationEvent of the same transaction that creates the pool): pool, mint, quote_mint
  gap          prog "all" (a getBlock loss is chain-wide), kind tip_gap_<reason>, data_loss true, [from_ms, to_ms], from_slot, to_slot, missed_slots
Order. Rows come out in SLOT order (the reader's: per slot migrations, creates, trades), every trade carries tx_index, so ArenaEngine.order_key gives the
(slot, 0, tx_index, event_index, signature) form on every print of every pool (arena_paper.py:912-917); a trade without tx_index never reaches the engine (below).
A gap row is emitted before the first row of slot >= its from_slot (the feed's contract: a gap row is written BEFORE the first row after it) and no earlier than the
slots before it were released; its [from_ms, to_ms] runs from the last row emitted to the follower's own gap time (clamped between the last row and the next row, so
that "no trade row before a gap has t_recv_ms >= gap.to_ms" holds).

WHAT IS DATA LOSS (ruling 2.2: the reader reports these as gaps; here each becomes a data_loss gap row)
  gaps.jsonl rows: backlog_jump (from_slot..to_slot), unfetchable, skip_contradicted, skip_contradicted_refetch, unresolved_dropped; the reader's own tip_bad_line and
  hour_file_missing; and, new here, a row this adapter cannot give to the engine (below).
WHAT IS NOT: skipped slots (("skip", slot) items; the chain had no block). They produce no row at all and move no counter of the engine.

A row the engine cannot take is DROPPED AND MARKED, never passed on half-formed (the feed's doctrine: loss is marked, not hidden): no tx_index (the PumpSwap rows that come
back through `resolve_unresolved` carry none, tools/pump_history_backfill.py:rows_from_block; mixing the two order-key forms in one pool is forbidden), a venue that is
not pumpswap / pump_bonding, a missing or non-integer slot / t_recv_ms / side / amount / reserve, or no pool (PumpSwap) / mint (bonding). Each is a gap of kind
tip_gap_bad_row_<code> at the row's slot, counted in `stats`.

Health (QP-RULING-1031-r4b-history 2.1 items 5 and 6, carried over). ArenaTipSource checks status.json before every poll and fails closed: stale, decoder blobs not
the three EXP027_DECODER_REF blobs, not pinned, or trade_event_v off give NO rows and a `problem` code; start() refuses without nice 19 / ionice idle (when required)
and without the first history hour.
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import time
from bisect import insort
from collections import Counter
from typing import Any, Callable, Iterable, Mapping

from tools import r4b_tip_reader as tr

SCHEMA = "arena_tip_adapter_v1"
VENUE_PROG = {"pumpswap": "pumpswap", "pump_bonding": "pumpfun"}
NAME_MAX, SYMBOL_MAX = 32, 16  # tools/arena_feed.py:111 (a test asserts equality)
# keys of a walker trade row that the arena tape does not carry (tools/arena_feed.TRADE_DROP, plus the tip's own `source` and its singular V stamp)
TRADE_DROP = frozenset({"v", "type", "feed", "commitment", "quote_is_wsol", "market_cap_supply_ui", "t_recv", "sol", "token", "price_sol", "market_cap_sol", "slot",
                        "signature", "event_index", "t_recv_ms", "source", "virtual_quote_reserve"})
CREATE_KEYS = ("mint", "trader", "creator", "event_ts", "quote_reserve", "base_reserve", "real_token_reserves", "token_raw", "quote_mint", "is_mayhem_mode")
COMPLETE_KEYS = ("trader", "mint", "bonding_curve", "event_ts", "quote_mint")
INT_FIELDS = ("slot", "t_recv_ms", "event_index", "tx_index", "sol_lamports", "token_raw", "quote_reserve", "base_reserve")
GAP_PROG = "all"


class BadRow(Exception):
    """A tip row the engine cannot take. `code` is log-safe and ends up in the gap kind."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class TipUnhealthy(Exception):
    """The tip side is not safe to read (status, pins, io priority, history start). Text only."""


def _is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


# ------------------------------------------------------------------------------------------------------------------------------------ row mapping
def _common(type_: str, prog: str, w: Mapping[str, Any]) -> dict[str, Any]:
    return {"v": 1, "type": type_, "prog": prog, "slot": w["slot"], "t_recv_ms": w["t_recv_ms"], "signature": w.get("signature"), "event_index": w.get("event_index")}


def trade_row(w: Mapping[str, Any]) -> dict[str, Any]:
    prog = VENUE_PROG.get(str(w.get("venue")))
    if prog is None:
        raise BadRow("venue")
    for k in ("slot", "t_recv_ms", "event_index", "sol_lamports", "token_raw", "quote_reserve", "base_reserve"):
        if not _is_int(w.get(k)):
            raise BadRow("fields")
    if not _is_int(w.get("tx_index")):
        raise BadRow("tx_index")
    if w.get("side") not in ("buy", "sell") or not isinstance(w.get("signature"), str) or not w.get("signature"):
        raise BadRow("fields")
    if not (w.get("pool") if prog == "pumpswap" else w.get("mint")):
        raise BadRow("fields")
    row = _common("trade", prog, w)
    row.update({k: v for k, v in w.items() if k not in TRADE_DROP})
    if row.get("event_ts") is None and _is_int(w.get("block_time")):
        row["event_ts"] = w["block_time"]  # the engine's clock reads event_ts or block_time; both are the block's second
    return row


def create_row(w: Mapping[str, Any]) -> dict[str, Any]:
    if not _is_int(w.get("slot")) or not _is_int(w.get("t_recv_ms")) or not w.get("mint"):
        raise BadRow("fields")
    row = _common("create", "pumpfun", w)
    row.update({k: w.get(k) for k in CREATE_KEYS})
    row["name"], row["symbol"] = str(w.get("name") or "")[:NAME_MAX], str(w.get("symbol") or "")[:SYMBOL_MAX]
    row["tx_index"], row["block_time"] = w.get("tx_index"), w.get("block_time")
    return row


def complete_row(w: Mapping[str, Any]) -> dict[str, Any]:
    if not _is_int(w.get("slot")) or not _is_int(w.get("t_recv_ms")) or not w.get("mint"):
        raise BadRow("fields")
    row = _common("complete", "pumpfun", w)
    row.update({k: w.get(k) for k in COMPLETE_KEYS})
    row["tx_index"], row["block_time"] = w.get("tx_index"), w.get("block_time")
    return row


def pool_create_row(w: Mapping[str, Any]) -> dict[str, Any]:
    if not _is_int(w.get("slot")) or not _is_int(w.get("t_recv_ms")) or not w.get("pool") or not w.get("mint"):
        raise BadRow("fields")
    row = _common("pool_create", "pumpswap", w)
    row.update({"pool": w["pool"], "mint": w["mint"], "quote_mint": w.get("quote_mint")})
    row["tx_index"], row["block_time"] = w.get("tx_index"), w.get("block_time")
    return row


def migration_rows(w: Mapping[str, Any]) -> list[dict[str, Any]]:
    """A walker `type: complete` row is the tape's `complete`; a `type: migration` row is the tape's `pool_create`."""
    t = w.get("type")
    if t == "complete":
        return [complete_row(w)]
    if t == "migration":
        return [pool_create_row(w)]
    raise BadRow("migration_type")


def gap_row(g: tr.TipGap, *, slot: int, from_ms: int, to_ms: int) -> dict[str, Any]:
    return {"v": 1, "type": "gap", "prog": GAP_PROG, "slot": slot, "t_recv_ms": to_ms, "signature": None, "event_index": None, "kind": g.code, "data_loss": True,
            "from_ms": from_ms, "to_ms": to_ms, "reason": g.reason, "from_slot": g.lo, "to_slot": g.hi, "missed_slots": g.slots if g.slots is not None else g.hi - g.lo + 1,
            "src": "tip"}


# ------------------------------------------------------------------------------------------------------------------------------------ the stream
class ArenaTipAdapter:
    """Reader items -> arena rows in slot order, with the gaps placed. Pure: no I/O. See the module docstring."""

    def __init__(self) -> None:
        self.stats: Counter = Counter()
        self._pending: list[tuple[int, int, int, tr.TipGap]] = []  # (lo, hi, seq, gap), sorted
        self._seq = 0
        self.last_t: int | None = None  # t_recv_ms of the last row emitted
        self.last_slot: int | None = None

    # -- gaps
    def _add_gap(self, g: tr.TipGap) -> None:
        self._seq += 1
        insort(self._pending, (g.lo, g.hi, self._seq, g))

    def _flush(self, out: list, upto_slot: int, next_t: int | None) -> None:
        """Emit every pending gap whose first slot is <= upto_slot."""
        while self._pending and self._pending[0][0] <= upto_slot:
            g = self._pending[0][3]
            base = g.t_recv_ms if g.t_recv_ms is not None else (self.last_t if self.last_t is not None else next_t)
            if base is None:
                return  # no instant for it anywhere yet: wait for the first row (the engine needs t_recv_ms)
            lo_t = self.last_t if self.last_t is not None else base
            to_ms = max(base, lo_t)
            if next_t is not None and next_t >= lo_t:
                to_ms = min(to_ms, next_t)
            self._pending.pop(0)
            row = gap_row(g, slot=self.last_slot if self.last_slot is not None else g.lo, from_ms=lo_t, to_ms=to_ms)
            out.append(row)
            self.stats["gap_rows"] += 1
            self.stats[f"gap_{g.reason}"] += 1
            self.stats["gap_missed_slots"] += row["missed_slots"]

    def _emit(self, out: list, row: dict[str, Any]) -> None:
        out.append(row)
        self.stats[f"rows_{row['type']}"] += 1
        self.last_slot = row["slot"] if self.last_slot is None else max(self.last_slot, row["slot"])
        self.last_t = row["t_recv_ms"] if self.last_t is None else max(self.last_t, row["t_recv_ms"])

    # -- items
    def feed(self, items: Iterable[tuple], released_D: int | None = None) -> list[dict[str, Any]]:
        """Arena rows for the reader items of one poll (in the reader's order), plus the gaps they release. `released_D` (TipReader.released_D): after the items, every
        pending gap that starts at or below released_D + 1 is emitted too (a gap is known before the checkpoint passes it)."""
        out: list[dict[str, Any]] = []
        for kind, x in items:
            if kind == "gap":
                self.stats["gaps_seen"] += 1
                self._add_gap(x)
                continue
            slot = x if kind == "skip" else (x.get("slot") if isinstance(x, Mapping) else None)
            if not _is_int(slot):
                self.stats["rows_bad_slot"] += 1
                pos = self.last_slot if self.last_slot is not None else 0
                self._add_gap(tr.TipGap(pos, pos, "bad_row_slot", None, None))
                continue
            t = x.get("t_recv_ms") if isinstance(x, Mapping) and _is_int(x.get("t_recv_ms")) else None
            self._flush(out, slot, t)
            if kind == "skip":
                self.stats["skips"] += 1  # a skipped slot is not data loss: no row, no gap
                continue
            try:
                rows = [trade_row(x)] if kind == "trade" else [create_row(x)] if kind == "create" else migration_rows(x) if kind == "migration" else None
                if rows is None:
                    raise BadRow("kind")
            except BadRow as exc:
                self.stats[f"rows_dropped_{exc.code}"] += 1
                self._add_gap(tr.TipGap(slot, slot, f"bad_row_{exc.code}", t, 1))
                continue
            for r in rows:
                self._emit(out, r)
        if released_D is not None:
            self._flush(out, released_D + 1, None)
        return out

    def pending_gaps(self) -> list[tr.TipGap]:
        return [p[3] for p in self._pending]


# ------------------------------------------------------------------------------------------------------------------------------------ the source (reader + health)
def lower_io_priority(pid: int | None = None) -> bool:
    """nice 19 and ionice -c3 (idle) for `pid` (default: this process). False when either could not be applied."""
    pid = os.getpid() if pid is None else pid
    ok = True
    try:
        os.setpriority(os.PRIO_PROCESS, pid, 19)
    except OSError:
        ok = False
    exe = shutil.which("ionice")
    if exe is None:
        return False
    try:
        ok &= subprocess.run([exe, "-c3", "-p", str(pid)], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
    return ok


class ArenaTipSource:
    """TipReader + status gate + ArenaTipAdapter. One thread (the host's). poll() never blocks and never writes."""

    def __init__(self, reader: Any, state_dir: str | os.PathLike, *, now_ms: Callable[[], int] | None = None, io_hook: Callable[[], bool] | None = None,
                 require_io_idle: bool = True, status_max_age_ms: int = tr.STATUS_MAX_AGE_MS) -> None:
        self.reader, self.state_dir = reader, os.fspath(state_dir)
        self.now_ms = now_ms or (lambda: time.time_ns() // 1_000_000)
        self.io_hook = io_hook or lower_io_priority
        self.require_io_idle, self.status_max_age_ms = require_io_idle, status_max_age_ms
        self.adapter = ArenaTipAdapter()
        self.stats: Counter = Counter()
        self.problem: str | None = None
        self.started = False

    @classmethod
    def open(cls, tip_dir: str | os.PathLike, state_dir: str | os.PathLike, *, start_ms: int, catch_up_hours: int = 52, **kw) -> "ArenaTipSource":
        """Production constructor: the guarded reader (hour floor, archive and path refusals) with the production floor; every kwarg goes to ArenaTipSource."""
        return cls(tr.TipReader(tip_dir, state_dir, start_ms=start_ms, catch_up_hours=catch_up_hours), state_dir, **kw)

    def status_problem(self) -> str | None:
        return tr.status_problem(tr.read_state_json(self.state_dir, "status.json"), self.now_ms(), max_age_ms=self.status_max_age_ms)

    def start(self) -> None:
        """Refuse (TipUnhealthy) without idle io when required, an unhealthy status, or a missing first history hour. Nothing is read before this passes."""
        if self.require_io_idle and not self.io_hook():
            raise TipUnhealthy("ionice: nice 19 / ionice -c3 could not be applied")
        why = self.status_problem()
        if why:
            raise TipUnhealthy(why)
        why = self.reader.start_problem()
        if why:
            raise TipUnhealthy(why)
        self.started = True

    def poll(self, limit: int = 20_000) -> list[dict[str, Any]]:
        """Arena rows, in slot order. An unhealthy status, or a reader problem, gives NO rows (and `problem` says why): the host must treat the tip side as down."""
        if not self.started:
            raise TipUnhealthy("start() was not called")
        why = self.status_problem()
        if why:
            self.problem = f"tip:{why}"
            self.stats["polls_unhealthy"] += 1
            return []
        items = self.reader.poll(limit)
        self.problem = getattr(self.reader, "problem", None)
        rows = self.adapter.feed(items, getattr(self.reader, "released_D", None))
        self.stats["polls"] += 1
        self.stats["rows"] += len(rows)
        return rows

    @property
    def caught_up(self) -> bool:
        return bool(getattr(self.reader, "caught_up", False))

    @property
    def released_D(self) -> int | None:
        return getattr(self.reader, "released_D", None)


# ------------------------------------------------------------------------------------------------------------------------------------ feed delay (QP item 4)
def read_lag_sample(state_dir: str | os.PathLike, now_ms: int | None = None) -> dict[str, Any] | None:
    """One measurement of the tip's delay from the follower's state dir only (no tape is opened). checkpoint.json says how far the follower has committed
    (last_done, last_t_recv_ms); the block lag itself (t_recv_ms - blockTime x 1000) is in status.json (`lag_ms` of the last block, `block_lag_ms_p50/p90` over the
    follower's window, `lag_slots` behind the tip). None if either file is unreadable."""
    st, ck = tr.read_state_json(state_dir, "status.json"), tr.read_state_json(state_dir, "checkpoint.json")
    if st is None or ck is None:
        return None
    return {"ts_ms": st.get("ts_ms") if now_ms is None else now_ms, "status_ts_ms": st.get("ts_ms"), "lag_ms": st.get("lag_ms"), "block_lag_ms_p50": st.get("block_lag_ms_p50"),
            "block_lag_ms_p90": st.get("block_lag_ms_p90"), "lag_slots": st.get("lag_slots"), "last_done": ck.get("last_done"), "last_t_recv_ms": ck.get("last_t_recv_ms"),
            "gaps": ck.get("gaps"), "backlog_jumps": st.get("backlog_jumps")}


def _pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, max(0, math.ceil(p / 100 * len(xs)) - 1))]


def feed_delay_slots(samples: Iterable[Mapping[str, Any]], sps: float, *, min_samples: int = 100) -> dict[str, Any]:
    """The whole-slot feed delay d for StackConfig.feed_delay_slots, from samples of read_lag_sample. Same ruling as tools/arena_feed_delay.py: the 90th percentile, not
    the median (under-correcting favours P&L). Per-sample value = the sample's `block_lag_ms_p90` (the follower's own window). Refuses fewer than `min_samples`
    usable samples. block_time has a resolution of one second: the lag carries up to +1 s of quantisation, like the arena measurement."""
    lags = [float(s["block_lag_ms_p90"]) for s in samples if isinstance(s.get("block_lag_ms_p90"), (int, float)) and not isinstance(s.get("block_lag_ms_p90"), bool)]
    if len(lags) < min_samples or not (0.05 < sps < 2.0):
        raise ValueError(f"need >= {min_samples} samples and a sane sps; got {len(lags)} samples, sps {sps}")
    p90, p50 = _pct(lags, 90), _pct(lags, 50)
    return {"n": len(lags), "p50_ms": p50, "p90_ms": p90, "max_ms": max(lags), "sps": sps, "d": math.ceil(p90 / (sps * 1000) - 1e-9), "d_median": math.ceil(p50 / (sps * 1000) - 1e-9)}
