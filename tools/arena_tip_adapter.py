#!/usr/bin/env python3
"""arena_tip_adapter: the fast-0 getBlock tip follower's tape as ARENA-TAPE rows (QP-RULING-1101-hv4-reads section 4.1 (B), items 2 to 4). PAPER only.
READ-ONLY: it opens files for reading through tools.arena_tip_reader.TipReader (the guarded reader of #1155/#1158, VENDORED byte for byte under a non-r4b name because DEC-031
section 3 item 1 bars arena processes from tools/r4b_*.py; no r4b file is changed), never writes, moves or truncates, holds no key and sends nothing. Tests use synthetic files in tmp dirs and the REAL tools.fast_tip_follower.TipFollower with a fake RPC; no real tip file,
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
  gap          prog "all" for block-level loss (a getBlock loss is chain-wide), prog "pumpswap" for unresolved_dropped, a mixed (pool, slot) group and a bad PumpSwap row (prog of the
               row's venue for any bad row); kind tip_gap_<reason> built here, never truncated; data_loss true, [from_ms, to_ms], from_slot, to_slot, missed_slots
Order. Rows come out in SLOT order (the reader's: per slot migrations, creates, trades). Every trade the walker decoder numbered carries tx_index, so ArenaEngine.order_key gives
(slot, 0, tx_index, event_index, signature) (arena_paper.py:912-917). PumpSwap prints that come back through `resolve_unresolved` carry NONE (tools/pump_history_backfill.py,
rows_from_block appends the unresolved record before tx_index is set): they PASS THROUGH without the key and get the engine's receive-order form (slot, 1, t_recv_ms, signature,
event_index), which is deterministic here because the follower stamps ONE t_recv_ms on every row of a slot (QP on #1165). Across slots the two forms coexist (slot is the first
element; 0 vs 1 decides before any mixed types meet); inside one (pool, slot) only the order of two or more TRANSACTIONS is imprecise (lexical by signature, not chain order). That is an
order imprecision, not data loss. Counted: rows_no_tx_index, pool_slots_no_tx_index, pool_slots_multi_sig, pool_slots_mixed. A (pool, slot) group that MIXES the two forms cannot come
from the decoder (the cache check is per pool); if one appears the adapter writes a data_loss gap of prog pumpswap at that slot (fail closed).
A gap row is emitted before the first row of slot >= its from_slot (the feed's contract: a gap row is written BEFORE the first row after it) and no earlier than the slots below it
were released; its [from_ms, to_ms] runs from the last row emitted to the follower's own gap time, clamped between the last row and the next row (from_ms <= to_ms <= the next row's
t_recv_ms). Tip rows of one slot share one t_recv_ms, so a gap placed inside a slot has to_ms EQUAL to the t_recv_ms of rows before it (the engine's spans are inclusive: fine;
tools/test_arena_gap_order.py's strict ">=" form of the feed's property does not hold on this source).

WHAT IS DATA LOSS (ruling 2.2: the reader reports these as gaps; here each becomes a data_loss gap row)
  gaps.jsonl rows: backlog_jump (from_slot..to_slot), unfetchable, skip_contradicted, skip_contradicted_refetch, unresolved_dropped; the reader's own tip_bad_line and
  hour_file_missing; and, new here, a row this adapter cannot give to the engine (below).
WHAT IS NOT: skipped slots (("skip", slot) items; the chain had no block). They produce no row at all and move no counter of the engine.

A row the engine cannot take is DROPPED AND MARKED, never passed on half-formed (the feed's doctrine: loss is marked, not hidden): a bonding trade without tx_index (the decoder always
numbers those), a venue that is not pumpswap / pump_bonding, a missing or non-integer slot / t_recv_ms / side / amount / reserve, or no pool (PumpSwap) / mint (bonding). Each is a gap of
kind tip_gap_bad_row_<code> at the row's slot, counted in `stats`.

SCHEMA DELTA against what tools/arena_feed.py writes (pinned by test_the_output_delta_against_the_feed_is_exactly_the_documented_one). The tip rows are the walker's SLIM rows
(observe/trade_store.stored_trade, one of the three pinned decoder blobs), so keys the feed writes from the unslimmed decoder record are NOT on the tip tape and cannot be produced here:
  PumpSwap trades   pool_quote_amount, lp_fee, protocol_fee, creator_fee, mint_source, and supply_raw on sells that carry it  (ArenaState.pumpswap_post then falls back to constant product on the base move, arena_state.py:80-92:
                    the state's price and candles differ slightly from the public tape's; ArenaEngine pricing reads reserves and V only and is unaffected)
  bonding trades    real_quote_reserve, real_base_reserve, protocol_fee_bps, protocol_fee, creator, creator_fee_bps, creator_fee
  pool_create       only from migration rows (the walker emits no CreatePool rows): a pool created outside a migration has none
  order             a `complete` or `pool_create` comes BEFORE its slot's trades (the feed writes them after the trade of the same transaction)
Added: tx_index and block_time on every data row except the PumpSwap rows above, and `src: "tip"` on gap rows.

Health (QP-RULING-1031-r4b-history 2.1 items 5 and 6, carried over). ArenaTipSource checks status.json before every poll and fails closed: stale, decoder blobs not
the three EXP027_DECODER_REF blobs, not pinned, or trade_event_v off give NO rows and a `problem` code; start() refuses without nice 19 / ionice idle (when required)
and without the first history hour.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import time
from bisect import insort
from collections import Counter
from typing import Any, Callable, Iterable, Mapping

from tools import arena_tip_reader as tr

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
# keys tools/arena_feed.py writes on a trade row that the tip tape cannot carry (the walker's slim row drops them; see the SCHEMA DELTA in the module docstring)
FEED_ONLY_KEYS = {"pumpswap": frozenset({"pool_quote_amount", "lp_fee", "protocol_fee", "creator_fee", "mint_source"}),
                  "pumpfun": frozenset({"real_quote_reserve", "real_base_reserve", "protocol_fee_bps", "protocol_fee", "creator", "creator_fee_bps", "creator_fee"})}


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
    if not _is_int(w.get("tx_index")) and (prog != "pumpswap" or w.get("tx_index") is not None):
        raise BadRow("tx_index")  # only a PumpSwap print that came back through resolve_unresolved may lack it (the key absent); anything else is a bad row
    if w.get("side") not in ("buy", "sell") or not isinstance(w.get("signature"), str) or not w.get("signature"):
        raise BadRow("fields")
    if not (w.get("pool") if prog == "pumpswap" else w.get("mint")):
        raise BadRow("fields")
    row = _common("trade", prog, w)
    row.update({k: v for k, v in w.items() if k not in TRADE_DROP and not (k == "tx_index" and v is None)})
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


def gap_kind(reason: str) -> str:
    """tip_gap_<reason>, built here and never truncated (TipGap.code cuts at 32 characters and would turn skip_contradicted_refetch into ...refetc)."""
    return "tip_gap_" + re.sub(r"[^a-z0-9_]", "_", str(reason).lower())


def gap_prog(g: tr.TipGap) -> str:
    """A getBlock loss is chain-wide (prog "all"); unresolved_dropped loses PumpSwap prints only."""
    return "pumpswap" if g.reason == "unresolved_dropped" else GAP_PROG


def gap_row(g: tr.TipGap, *, slot: int, from_ms: int, to_ms: int, prog: str | None = None, kind: str | None = None) -> dict[str, Any]:
    return {"v": 1, "type": "gap", "prog": prog or gap_prog(g), "slot": slot, "t_recv_ms": to_ms, "signature": None, "event_index": None, "kind": kind or gap_kind(g.reason),
            "data_loss": True, "from_ms": from_ms, "to_ms": to_ms, "reason": g.reason, "from_slot": g.lo, "to_slot": g.hi,
            "missed_slots": g.slots if g.slots is not None else g.hi - g.lo + 1, "src": "tip"}


# ------------------------------------------------------------------------------------------------------------------------------------ the stream
class ArenaTipAdapter:
    """Reader items -> arena rows in slot order, with the gaps placed. Pure: no I/O. See the module docstring."""

    def __init__(self) -> None:
        self.stats: Counter = Counter()
        self._pending: list[tuple[int, int, int, tr.TipGap, str]] = []  # (lo, hi, seq, gap, prog), sorted
        self._seq = 0
        self.last_t: int | None = None  # t_recv_ms of the last row emitted
        self.last_slot: int | None = None
        self._grp_slot: int | None = None
        self._grp: dict[str, list] = {}  # pool -> [rows with tx_index, rows without, {signatures of the rows without}, mixed gap written] in the current slot

    # -- gaps
    def _add_gap(self, g: tr.TipGap, prog: str | None = None) -> None:
        self._seq += 1
        insort(self._pending, (g.lo, g.hi, self._seq, g, prog or gap_prog(g)))

    def _flush(self, out: list, upto_slot: int, next_t: int | None) -> None:
        """Emit every pending gap whose first slot is <= upto_slot."""
        while self._pending and self._pending[0][0] <= upto_slot:
            g, prog = self._pending[0][3], self._pending[0][4]
            base = g.t_recv_ms if g.t_recv_ms is not None else (self.last_t if self.last_t is not None else next_t)
            if base is None:
                return  # no instant for it anywhere yet: wait for the first row (the engine needs t_recv_ms)
            lo_t = self.last_t if self.last_t is not None else base
            to_ms = max(base, lo_t)
            if next_t is not None and next_t >= lo_t:
                to_ms = min(to_ms, next_t)
            self._pending.pop(0)
            row = gap_row(g, slot=self.last_slot if self.last_slot is not None else g.lo, from_ms=lo_t, to_ms=to_ms, prog=prog)
            out.append(row)
            self.stats["gap_rows"] += 1
            self.stats[f"gap_{g.reason}"] += 1
            self.stats["gap_missed_slots"] += row["missed_slots"]

    def _emit(self, out: list, row: dict[str, Any]) -> None:
        out.append(row)
        self.stats[f"rows_{row['type']}"] += 1
        self.last_slot = row["slot"] if self.last_slot is None else max(self.last_slot, row["slot"])
        self.last_t = row["t_recv_ms"] if self.last_t is None else max(self.last_t, row["t_recv_ms"])

    # -- the PumpSwap rows without tx_index (QP on #1165): counted per (pool, slot)
    def _close_group(self) -> None:
        for n_tx, n_no, sigs, _ in self._grp.values():
            if n_no:
                self.stats["pool_slots_no_tx_index"] += 1
                self.stats["pool_slots_multi_sig"] += len(sigs) >= 2
                self.stats["pool_slots_mixed"] += n_tx > 0
        self._grp = {}

    def counts(self) -> dict[str, int]:
        """stats with the open (pool, slot) group counted too: the numbers the host's hourly counts log and every read disclose."""
        keep, grp = Counter(self.stats), self._grp
        self._close_group()
        out, self.stats, self._grp = dict(self.stats), keep, grp
        return out

    def _note_pumpswap(self, row: dict[str, Any], out: list) -> None:
        if row["slot"] != self._grp_slot:
            self._close_group()
            self._grp_slot = row["slot"]
        g = self._grp.setdefault(row["pool"], [0, 0, set(), False])
        no = row.get("tx_index") is None
        if no:
            g[1] += 1
            g[2].add(row["signature"])
            self.stats["rows_no_tx_index"] += 1
        else:
            g[0] += 1
        if g[0] and g[1] and not g[3]:
            g[3] = True  # one gap per (pool, slot)
            self._add_gap(tr.TipGap(row["slot"], row["slot"], "mixed_tx_index", row["t_recv_ms"], 1), "pumpswap")

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
                self._add_gap(tr.TipGap(pos, pos, "bad_row_slot", None, None), GAP_PROG)
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
                prog = VENUE_PROG.get(str(x.get("venue"))) if kind == "trade" else None
                self._add_gap(tr.TipGap(slot, slot, f"bad_row_{exc.code}", t, 1), prog or GAP_PROG)
                continue
            for r in rows:
                if r["type"] == "trade" and r["prog"] == "pumpswap":
                    self._note_pumpswap(r, out)
                    self._flush(out, slot, t)  # a mixed-group gap goes out before the row that revealed it
                self._emit(out, r)
        if released_D is not None:
            self._flush(out, released_D + 1, None)
        return out

    def pending_gaps(self) -> list[tr.TipGap]:
        return [p[3] for p in self._pending]


# ------------------------------------------------------------------------------------------------------------------------------------ the source (reader + health)
def lower_io_priority(pid: int | None = None) -> bool:
    """nice 19 and ionice -c3 (idle) for `pid` (default: this process). False when either could not be applied. Both are PER THREAD on Linux (setpriority(PRIO_PROCESS, tid)
    and ioprio of a tid): the host must call ArenaTipSource.start() on the thread that polls, before it spawns other threads (they inherit the lowered priority only if created after)."""
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


def feed_delay_slots(samples: Iterable[Mapping[str, Any]], sps: float, *, ingest_lag_ms: float = 0.0, min_samples: int = 100) -> dict[str, Any]:
    """The whole-slot feed delay d for StackConfig.feed_delay_slots, from samples of read_lag_sample. Same ruling as tools/arena_feed_delay.py: the 90th percentile, not
    the median (under-correcting favours P&L). Per-sample value = the sample's `block_lag_ms_p90` (the follower's own window, status.json: the block lag is NOT in
    checkpoint.json). `ingest_lag_ms` = the p90 of the host's own ingest lag (wall clock at on_row minus the row's t_recv_ms; checkpoint-then-drain polling adds delay before a bot sees a
    row; QP on #1165): d = ceil((p90 block lag + ingest lag) / seconds per slot). Refuses fewer than `min_samples` usable samples. block_time has a resolution of one second: the lag carries up to +1 s of quantisation, like the arena measurement."""
    lags = [float(s["block_lag_ms_p90"]) for s in samples if isinstance(s.get("block_lag_ms_p90"), (int, float)) and not isinstance(s.get("block_lag_ms_p90"), bool)]
    if len(lags) < min_samples or not (0.05 < sps < 2.0):
        raise ValueError(f"need >= {min_samples} samples and a sane sps; got {len(lags)} samples, sps {sps}")
    p90, p50 = _pct(lags, 90), _pct(lags, 50)
    return {"n": len(lags), "p50_ms": p50, "p90_ms": p90, "max_ms": max(lags), "sps": sps, "ingest_lag_ms": ingest_lag_ms,
            "d": math.ceil((p90 + ingest_lag_ms) / (sps * 1000) - 1e-9), "d_median": math.ceil((p50 + ingest_lag_ms) / (sps * 1000) - 1e-9)}
