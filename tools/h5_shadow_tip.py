#!/usr/bin/env python3
"""h5_shadow_tip: the TIP INPUT MODE of tools/h5_shadow.py (S*' = S* plus this input only; L1 design G6, DEC-031 Amendment 24 DRAFT, book s1t). PAPER ONLY: no key, no send.

WHAT IT IS. The H5 shadow engine (tools.h5_shadow.Engine, byte-identical to S* 5c2ab7c6) is fed from the research-0 receiver copy of the fast-0 tip tape, in the ARENA layout
(`tape-<UTC hour>.jsonl[.zst]`, written by tools/arena_tip_tape_writer.py through tools/arena_tip_adapter.py and pushed byte for byte by tools/arena_tip_push.py) instead
of the public logsSubscribe sockets. NOTHING in tools/h5_shadow.py changes: this module wraps its `run_live` and swaps ONE function, `run_feed` (the notice loop), for the
tip loop below. Every decode and decision function, the fresh-forward checks, the seals (Look-2 end at SUCC_END_MS), the start record, the housekeeping task, the RPC
synthetic-class fallback and the pump.fun class side feed are S*'s own and run as at S*. A test pins tools/h5_shadow.py to the S* blob (7f859b12) and every definition of
the engine to S* by AST. The public-feed mode is the default and is not touched: `python3 tools/h5_shadow.py ...` is the same bytes as before.

THE FLAG. `python3 -m tools.h5_shadow_tip --tip-input DIR --fresh-forward ARENA ...` (every other argument is tools/h5_shadow.py's). DIR must be a strict child of
/data2/mal/arena-tip-feed or /data2/mal/scratch (tools.arena_tip_push.check_dst: never the public arena tape, /data/mal, /var/lib/mal/sealed, tip-tape-archive or any
sealed-named path). `--tip-input` is refused without `--fresh-forward ARENA`, with `--replay-tape`, with `--tip-dir` (the report-only union) and with `--h5-look2-observed`.
There are no trade sockets in this mode; `--ws-url` still feeds the pump.fun class side feed exactly as at S*.

ROW MAPPING (arena-tape row -> engine call; a pumpswap trade is the only row that can trigger)
  trade, prog pumpswap  -> Engine.on_trade(row)   (venue pumpswap, event V from `virtual_quote_reserves`; keys the tip tape cannot carry (lp_fee, pool_quote_amount,
                           protocol_fee, creator_fee) are absent: post_state() falls back to LP_FRAC exactly as in the exploration replay)
  pool_create           -> Engine.on_create_pool(pool, mint, quote_mint, slot, t_recv_ms), then request_class(mint, signature) and advance(), the order decode_notice uses
  complete (bonding)    -> SynClassifier.hint(mint, "comp", signature): the CompleteEvent tx the RPC fallback reads first. No clock move, no decision
  trade, prog pumpfun / create / hb / anything else -> counted, no engine call (the PumpSwap socket never saw them either)
  gap                   -> Engine._gap(kind, flag_pools, at_ms = to_ms): the adapter's rows: prog "all" (block-level loss) and "pumpswap" (unresolved_dropped, a mixed (pool, slot)
                           group, a bad PumpSwap row) FLAG every open pool; prog "pumpfun" (a bad bonding row) is recorded, flags nothing. A row without `data_loss: false`
                           is loss (fail closed). Skipped slots never reach the tape (the adapter writes no row for them).
  a pumpswap trade or pool_create that is not well formed is not applied: it becomes a flagging gap `tip_input_bad_row` (the adapter's own doctrine: loss is marked)

START. The source starts at the process start and the driver drops every row whose t_recv_ms is before the engine's fresh-forward start (counted `rows_before_start`),
so a pool announced before the start is never announced: it gets `rejected_unannounced` and no trigger, exactly as in the public mode (a process started at F never saw the
CreatePool notice of F - 10 s). There is no look-back. A test through the wrapper pins it (and a control: the same pool announced after F triggers).

SMOKE. `--smoke` (default off) puts S*'s H5 Look-2 seal on every pool (arm_smoke): no outcome is ever computed, whatever the pool's s0. In fresh mode `suppress_outcome` does nothing
and the seal window starts at SUCC_END_MS, so the wrapper moves the window start to 0 on the engine before the first row. Trigger and pool records are written flagged
`h5_look2_sealed`; a second wall drops and counts any outcome-type record and halts the book (exit 3). Tests pin both, and a control without `--smoke` that does price outcomes.

STAMP. `t_detect_ms` is `Engine.wall()` at the decision: the HOST wall clock in live mode (Engine's default), never the row's `t_recv_ms` (the fast-0 stamp would
under-charge the writer and transport lag). In replay/harness mode (`wall_offset_ms`) the harness gives a fixed offset, wall = t_recv_ms + offset, and the offset is
recorded in the `tip_input_start` record. A test pins both.

FAIL CLOSED. Before anything is created: the dir is checked (check_dst) and tools.arena_tip_push.push_problem(dir) must be None (receiver status fresh, connected, synced; the
relayed writer heartbeat fresh, live, decoder blobs pinned, no problem, caught up) else the process refuses to start (exit 2). While running, every poll re-checks it
(cached 1 s): a problem gives NO rows; its onset writes a `tip_input_problem` record and Engine.feed_restart (open pools flagged, like a socket outage); recovery writes
`tip_input_resumed`. A PIN problem (decoder_pin, any `pin` code, a stopped receiver) HALTS the book for good (the process stops, exit 3): a human re-pins. A reader
exception is a problem too (never a crash of the engine task), and so is a reader-level loss (a bad json line, a zst error: `reader_bad_lines`, `reader_zst_errors`): the poll
that saw it reports the problem, the driver writes `tip_input_problem` + feed_restart (open pools flagged), the next clean poll writes `tip_input_resumed`.
COUNTERS. `tip_input_stats` (every 60 s) and `tip_input_stop` carry the driver's row counters and the source's (polls, rows, reader loss, ArenaState counters).
"""

from __future__ import annotations

if __package__ in (None, ""):  # `python3 tools/h5_shadow_tip.py` from the repo root
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import asyncio
import collections
import logging
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterable, Sequence

from tools import arena_tip_push as atp
from tools import h5_shadow as h5
from tools.arena_state import ArenaState

SCHEMA = "h5_shadow_tip_v1"
POLL_S = 0.25  # idle poll interval
ROWS_PER_STEP = 2_000  # rows applied per step; a full step is followed at once by the next one (after yielding to the event loop)
PROBLEM_CACHE_MS = 1_000  # push_problem reads three small json files; at most once a second
EXIT_REFUSED, EXIT_HALTED = 2, 3
GAP_FLAG_PROGS = ("all", "pumpswap")  # a gap on these feeds can have cost a PumpSwap pool prints (arena_tip_adapter.gap_prog)
LOSS_COUNTERS = ("bad_lines", "zst_errors", "apply_errors")  # ArenaState drops a bad json line / skips the rest of an hour after a zst error / skips a row whose apply raised: loss
SMOKE_BLOCKED_TYPES = ("outcome", "outcome_bx10", "strip")  # records that price a Q50-rule outcome: a smoke book writes none (the Look-2 seal prevents them; this guard is the second wall)
STATS_EVERY_S = 60.0  # tip_input_stats record interval (hb's cadence)
GAP_KEEP = ("from_slot", "to_slot", "missed_slots", "from_ms", "to_ms", "reason", "prog", "src")


class TipInputUnhealthy(Exception):
    """The tip input is not safe to read. Text only (a short problem code)."""


def _is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def is_permanent(problem: str | None) -> bool:
    """A problem a human must fix (a decoder pin, a receiver code pin, a stopped or diverged receiver): the book halts instead of waiting for it to clear."""
    p = str(problem or "")
    return "pin" in p or p.startswith("state_stopped") or p == "stopped_mismatch"


def check_tip_input_dir(path: str | os.PathLike, roots: Sequence[str] | None = None) -> Path:
    """The receiver copy only: a strict child of one of `roots` (default: tools.arena_tip_push.DST_ROOTS), judged as a string and by realpath. `roots` exists for tests
    (tmp dirs); the CLI never passes it."""
    p = atp.check_dst(path, roots)
    if not p.is_dir():
        raise ValueError("tip_input_not_a_dir")
    return p


# ------------------------------------------------------------------------------------------------------------------------------------ reading
class _RowTap(ArenaState):
    """tools.arena_state.ArenaState's file reader (the .jsonl by byte offset, a closed hour's .jsonl.zst by streaming decompress, whole lines only) with apply() turned into
    a collector: no pool state is kept, so memory does not grow with the tape."""

    def __init__(self, tape_dir: str | Path, start_ms: int) -> None:
        super().__init__(tape_dir, start_ms)
        self.out: list[dict] = []

    def apply(self, row: dict) -> None:  # noqa: D401
        self.out.append(row)


class TipRowSource:
    """Rows of the receiver copy in file order, gated by the transport/writer status. One thread (the host's). poll() never writes."""

    def __init__(self, tip_dir: str | os.PathLike, *, start_ms: int, roots: Sequence[str] | None = None, problem_fn: Callable[[], str | None] | None = None,
                 now_ms: Callable[[], int] = h5.now_ms, cache_ms: int | None = None) -> None:
        self.dir = check_tip_input_dir(tip_dir, roots)
        self._tap = _RowTap(self.dir, int(start_ms))
        self._problem_fn = problem_fn or (lambda: atp.push_problem(self.dir))
        self.now_ms, self.cache_ms = now_ms, PROBLEM_CACHE_MS if cache_ms is None else cache_ms
        self.problem: str | None = None
        self.halted = False
        self._checked_ms: int | None = None
        self._why: str | None = None
        self.started = False
        self.stats: collections.Counter = collections.Counter()

    def check(self) -> str | None:
        """push_problem, cached. Any exception is a problem (fail closed)."""
        now = self.now_ms()
        if self._checked_ms is None or now - self._checked_ms >= self.cache_ms or now < self._checked_ms:
            try:
                why = self._problem_fn()
            except Exception as exc:  # noqa: BLE001 - fail closed
                why = "status_check_" + type(exc).__name__.lower()[:24]
            self._checked_ms, self._why = now, why
        return self._why

    def start(self) -> None:
        """Refuse (TipInputUnhealthy) on any problem; nothing is read before this passes."""
        self._checked_ms = None
        why = self.check()
        if why:
            raise TipInputUnhealthy(why)
        self.started = True

    def poll(self, limit: int = ROWS_PER_STEP) -> list[dict]:
        if not self.started:
            raise TipInputUnhealthy("start() was not called")
        if self.halted:
            return []
        why = self.check()
        self.problem = why
        if why:
            self.stats["polls_unhealthy"] += 1
            self.halted = is_permanent(why)
            return []
        self._tap.out = []
        before = {k: self._tap.counters.get(k, 0) for k in LOSS_COUNTERS}
        try:
            self._tap.poll(max_rows=limit)
        except Exception as exc:  # noqa: BLE001 - a reader failure is a problem, not a crash
            self.problem = "reader_" + type(exc).__name__.lower()[:24]
            self.stats["reader_errors"] += 1
            return []
        rows, self._tap.out = self._tap.out, []
        lost = [k for k in LOSS_COUNTERS if self._tap.counters.get(k, 0) > before[k]]
        if lost:  # rows were dropped by the reader (S1 of the review): mark it like any outage, so the driver flags the open pools (feed_restart); the rows that did come are still applied
            self.problem = "reader_" + lost[0]
            self.stats["reader_loss_events"] += 1
            for k in lost:
                self.stats[f"reader_loss_{k}"] += self._tap.counters[k] - before[k]
        self.stats["polls"] += 1
        self.stats["rows"] += len(rows)
        return rows

    def counters(self) -> dict[str, int]:
        return {**dict(self.stats), **{f"tap_{k}": v for k, v in self._tap.counters.items()}}


# ------------------------------------------------------------------------------------------------------------------------------------ mapping
def _bad_row(engine: h5.Engine, row: dict, c: collections.Counter, code: str) -> None:
    c["rows_bad"] += 1
    c[f"rows_bad_{code}"] += 1
    slot, t = row.get("slot"), row.get("t_recv_ms")
    engine._gap("tip_input_bad_row", flag_pools=True, at_ms=t if _is_int(t) else engine.wall(), code=code, **({"slot": slot} if _is_int(slot) else {}))


def _gap(engine: h5.Engine, row: dict, c: collections.Counter) -> None:
    prog = str(row.get("prog") or "all")
    flag = row.get("data_loss") is not False and prog in GAP_FLAG_PROGS
    to_ms = row.get("to_ms") if _is_int(row.get("to_ms")) else row.get("t_recv_ms")
    kw = {k: row[k] for k in GAP_KEEP if k in row and (_is_int(row[k]) or isinstance(row[k], str))}
    kw["prog"] = prog
    c["gap_rows"] += 1
    c["gap_rows_flagging"] += flag
    engine._gap(str(row.get("kind") or "tip_gap")[:64], flag_pools=flag, at_ms=to_ms if _is_int(to_ms) else None, **kw)


def apply_row(engine: h5.Engine, row: dict, c: collections.Counter) -> None:
    """One arena-tape row into the engine. See the module docstring. Raises nothing for a malformed row; an engine bug propagates (the driver counts it like run_feed does)."""
    typ, prog = row.get("type"), row.get("prog")
    if typ == "trade":
        if prog != "pumpswap":
            c["rows_ignored_bonding"] += 1
            return
        need_s = ("pool", "side", "signature")
        need_i = ("slot", "t_recv_ms", "sol_lamports", "token_raw", "quote_reserve", "base_reserve")
        if not all(_is_int(row.get(k)) for k in need_i) or row.get("side") not in ("buy", "sell") or not all(isinstance(row.get(k), str) and row.get(k) for k in need_s):
            return _bad_row(engine, row, c, "trade_fields")
        if row.get("venue") not in (None, "pumpswap"):
            return _bad_row(engine, row, c, "trade_venue")
        c["rows_trade_pumpswap"] += 1
        engine.on_trade(row if row.get("venue") == "pumpswap" else {**row, "venue": "pumpswap"})
    elif typ == "pool_create":
        if not (_is_int(row.get("slot")) and _is_int(row.get("t_recv_ms")) and row.get("pool") and row.get("mint")):
            return _bad_row(engine, row, c, "pool_create_fields")
        c["rows_pool_create"] += 1
        engine.on_create_pool(row["pool"], row["mint"], row.get("quote_mint"), row["slot"], row["t_recv_ms"])
        engine.request_class(row["mint"], row.get("signature") if isinstance(row.get("signature"), str) else None)  # the migrate tx (decode_notice does the same)
        engine.advance(row["slot"], row["t_recv_ms"], None)  # decode_notice: a notice without PumpSwap prints only moves the clock
    elif typ == "complete":
        c["rows_complete"] += 1
        clf = engine.classifier
        if hasattr(clf, "hint") and row.get("mint") and isinstance(row.get("signature"), str) and row.get("signature"):
            clf.hint(row["mint"], "comp", row["signature"])
    elif typ == "gap":
        _gap(engine, row, c)
    else:
        c["rows_ignored_" + (typ if isinstance(typ, str) else "untyped")] += 1


class TipDriver:
    """The synchronous core: pulls rows from a source, applies them, writes the problem/resumed records. The live loop and the harness both call step()."""

    def __init__(self, engine: h5.Engine, source: Any, *, wall_offset_ms: int | None = None, on_error: Callable[[BaseException, dict], None] | None = None) -> None:
        self.engine, self.source, self.wall_offset_ms, self.on_error = engine, source, wall_offset_ms, on_error
        self.counters: collections.Counter = collections.Counter()
        self._down = False

    def _transition(self) -> None:
        prob = self.source.problem
        if prob and not self._down:
            self._down = True
            self.counters["problems"] += 1
            self.engine.emit({"type": "tip_input_problem", "problem": prob, "permanent": is_permanent(prob), "open_pools": len(self.engine.pools)})
            self.engine.feed_restart(self.engine.wall(), "tip_input:" + prob)
        elif not prob and self._down:
            self._down = False
            self.engine.emit({"type": "tip_input_resumed"})

    def step(self, limit: int = ROWS_PER_STEP) -> int:
        rows = self.source.poll(limit)
        self._transition()
        eng = self.engine
        fs = eng.fresh_start_ms
        for row in rows:
            if fs is not None and _is_int(row.get("t_recv_ms")) and row["t_recv_ms"] < fs:
                self.counters["rows_before_start"] += 1  # S* parity: the public feed never saw a row from before the process start; an announcement from before it is unseen too
                continue
            if self.wall_offset_ms is not None and _is_int(row.get("t_recv_ms")):
                eng.wall = lambda ms=row["t_recv_ms"] + self.wall_offset_ms: ms
            try:
                apply_row(eng, row, self.counters)
            except Exception as exc:  # noqa: BLE001 - an engine bug on one row must not stop the book; counted and logged like run_feed
                eng.counters["decode_errors"] += 1
                if eng.counters["decode_errors"] <= h5.ERROR_RECORDS_MAX:
                    eng.emit({"type": "error", "where": "tip_apply", "exc": type(exc).__name__, "msg": str(exc)[:200], "slot": row.get("slot"), "signature": row.get("signature")})
                if self.on_error is not None:
                    try:
                        self.on_error(exc, {"where": "tip_apply", "slot": row.get("slot"), "signature": row.get("signature")})
                    except Exception:  # noqa: BLE001
                        pass
        return len(rows)

    @property
    def halted(self) -> bool:
        return bool(getattr(self.source, "halted", False))


# ------------------------------------------------------------------------------------------------------------------------------------ live
def start_record(source: TipRowSource, wall_offset_ms: int | None, smoke: bool = False) -> dict:
    return {"type": "tip_input_start", "smoke": smoke, "look2_seal": "every pool, no outcome priced (smoke)" if smoke else "as S*", "schema": SCHEMA, "dir": str(source.dir), "poll_s": POLL_S, "rows_per_step": ROWS_PER_STEP, "problem_cache_ms": source.cache_ms,
            "start_filter": "t_recv_ms >= engine.fresh_start_ms", "stamp": "host_wall" if wall_offset_ms is None else "fixed_offset", "wall_offset_ms": wall_offset_ms, "gap_flag_progs": list(GAP_FLAG_PROGS),
            "public_trade_feed": "off", "rpc_class_fallback": "as S*", "pump_class_side_feed": "as S*"}


def stats_record(kind: str, driver: TipDriver, source: TipRowSource, guard: dict | None = None) -> dict:
    return {"type": kind, "schema": SCHEMA, **({} if guard is None else {"smoke_outcome_blocked": guard["blocked"]}), "driver": dict(sorted(driver.counters.items())), "source": dict(sorted(source.counters().items())), "problem": source.problem,
            "halted": source.halted}


def arm_smoke(engine: h5.Engine) -> dict:
    """SMOKE MODE: S*'s H5 Look-2 seal on EVERY pool, so no Q50-rule outcome is ever priced. In fresh mode the engine's seal window starts at SUCC_END_MS (2026-12-01), so pools
    before it would be priced (`suppress_outcome` does nothing in fresh mode); here the window start is moved to 0 and the declared observation stays off: `_h5_sealed_now` is then
    True for every pool, before or after the end constant. A sealed pool writes its trigger (decision-time) and pool records flagged `h5_look2_sealed`, and no ladder, outcome,
    outcome_bx10, strip, legs or min_q: pool counts print as flags only. Second wall: an output filter that drops (and counts) any outcome-type record; the first one makes the
    book halt (exit 3). Must be called before the first row (the seal is judged once per pool)."""
    if engine.pools or engine.counters["events"]:
        raise RuntimeError("arm_smoke must run before the first row")
    engine.h5_look2_start_ms, engine.h5_look2_observed = 0, False
    guard = {"blocked": 0}
    inner = engine._emit

    def filtered(rec: dict) -> None:
        if rec.get("type") in SMOKE_BLOCKED_TYPES:
            guard["blocked"] += 1
            return
        inner(rec)

    engine._emit = filtered
    return guard


def make_run_feed(source: TipRowSource, state: dict, *, poll_s: float = POLL_S, smoke: bool = False) -> Callable[..., Any]:
    """The replacement for tools.h5_shadow.run_feed: same call signature (the factory is ignored: there are no sockets), same contract (return when `stop` is set)."""

    async def run_feed(source_factory: Any, engine: h5.Engine, stop: asyncio.Event, *, max_seconds: float | None = None,
                       on_decode_error: Callable[[BaseException, Any], None] | None = None, **_: Any) -> None:
        started = time.monotonic()
        guard = arm_smoke(engine) if smoke else None
        engine.emit(start_record(source, None, smoke))
        driver = TipDriver(engine, source, on_error=(lambda e, ctx: on_decode_error(e, SimpleNamespace(**ctx))) if on_decode_error else None)
        state["driver"] = driver
        last_stats = time.monotonic()
        try:
            while not stop.is_set():
                n = driver.step()
                if driver.halted:
                    state["halted"] = source.problem
                    engine.emit({"type": "tip_input_halted", "problem": source.problem})
                    stop.set()
                    break
                if guard is not None and guard["blocked"]:  # an outcome-type record was produced in smoke mode: the seal failed; nothing was written; stop for good
                    state["halted"] = "smoke_outcome_computed"
                    engine.emit({"type": "tip_input_halted", "problem": "smoke_outcome_computed", "blocked": guard["blocked"]})
                    stop.set()
                    break
                if time.monotonic() - last_stats >= STATS_EVERY_S:  # S2 of the review: the input counters (rows by type, bad rows, gaps, reader loss) are in the output, not only in memory
                    last_stats = time.monotonic()
                    engine.emit(stats_record("tip_input_stats", driver, source, guard))
                if max_seconds is not None and time.monotonic() - started > max_seconds:
                    stop.set()
                    break
                if n >= ROWS_PER_STEP:
                    await asyncio.sleep(0)
                else:
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=poll_s)
                    except asyncio.TimeoutError:
                        pass
        finally:
            engine.emit(stats_record("tip_input_stop", driver, source, guard))

    return run_feed


# ------------------------------------------------------------------------------------------------------------------------------------ CLI
def build_parser() -> argparse.ArgumentParser:
    ap = h5.build_parser()
    ap.description = "H5-BOOSTFLOOR shadow detector, TIP INPUT MODE (S*'): tools/h5_shadow.py fed from the research-0 receiver copy of the tip tape (paper only)"
    ap.add_argument("--smoke", action="store_true", help="SMOKE MODE: the Look-2 seal on every pool (no outcome, ladder, strip or min_q is ever computed; trigger and pool records "
                    "carry h5_look2_sealed). For the s1t early smoke. Default off.")
    ap.add_argument("--tip-input", required=True, metavar="DIR", help="the receiver copy (arena layout), e.g. /data2/mal/arena-tip-feed/<book>; a strict child of "
                    + " or ".join(atp.DST_ROOTS) + " only")
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    if not h5.fresh_forward_flag(args.fresh_forward):
        ap.error("--tip-input requires --fresh-forward ARENA (the tip book is an arena book)")
    if args.replay_tape or args.tip_dir:
        ap.error("--tip-input does not apply with --replay-tape or --tip-dir (the report-only union)")
    if args.h5_look2_observed is not None:
        ap.error("--h5-look2-observed does not apply with --fresh-forward (neither seal is applied)")
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        source = TipRowSource(args.tip_input, start_ms=h5.now_ms())  # no look-back: a pool announced before the start is never announced (S* parity)
        source.start()
    except (ValueError, TipInputUnhealthy) as exc:
        print(f"REFUSED: tip input: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    state: dict = {}
    saved = h5.run_feed
    h5.run_feed = make_run_feed(source, state, smoke=args.smoke)  # run_live looks run_feed up in the module globals at call time
    try:
        rc = asyncio.run(h5.run_live(args))
    finally:
        h5.run_feed = saved
    return EXIT_HALTED if state.get("halted") else rc


if __name__ == "__main__":
    sys.exit(main())
