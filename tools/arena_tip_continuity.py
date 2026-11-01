#!/usr/bin/env python3
"""arena_tip_continuity: restart continuity for books fed from the PERSISTED tip-source tape (F3 of ops/reports/2026-11-02/L4-nd-audit.md; DEC-031 Am.23 item 7).

PAPER only. No key, no wallet, no RPC, no network, no model call. Stdlib + the engine's own modules. It edits no frozen file: it only drives tools.arena_paper.ArenaEngine from the outside.

THE PROBLEM. Am.23 item 7 books every position that is open across a restart of the writer or of the engine host as `engine_restart` (excluded). That was right for an engine fed live and
non-persistently. On the tip source the derived copy of the tape is complete and persisted (tools/arena_tip_tape_writer.py, resumable; tools/arena_tip_push.py), so a restarted engine can
read every row it missed. A 14-day job cap then no longer puts an exclusion inside a counted window.

WHAT THIS MODULE DOES (all of it a pure function of the persisted tape, the ledger and the decision log):
  1. `restore_engine` rebuilds an engine after a restart: it replays the persisted hour files from a replay start (the checkpoint's warm-up start, or the oldest open position minus a margin) up
     to a cursor, in order, through the SAME driver the live run uses (`BookDriver`). The engine's pool histories, gaps and slot clock are therefore what an uninterrupted engine had.
  2. Positions that the ledger shows open (an `open` row and no `trade` / `excluded` row) are ADOPTED with restarted=False (tools.arena_paper.ArenaEngine.recover() builds them with
     restarted=True, which is what makes the later sell an `engine_restart` exclusion). Their exits are decided again from the tape after entry by the book's exit rule (`ExitRule`, a pure
     function of the pool history), at the same instants an uninterrupted run decides them, and priced by the engine from its own pool history. An exit that was decided but not yet priced when
     the engine died lived only in memory: it is re-derived, not remembered.
  3. ENTRIES ARE NEVER GENERATED. The entry decisions are the decision log's rows (`LoggedEntry`): a model-driven book (HV7's Haiku rank, an LLM arm) does not call its model on a replay. The
     restore API has no model parameter; the driver's `model` is a tripwire that raises on any use. An entry the log shows with no `open` row (a kill between the log and the engine) is counted
     and never re-sent; no late entry, ever. Entries that fall into the downtime (nobody was there to decide them) simply do not exist: the book reports `entries_blind` and the downtime.
  4. FAIL CLOSED. Anything the replay cannot prove (no checkpoint, the persisted tape shorter than the checkpoint's cursor, a missing hour between the replay start and the cursor, the replay start
     older than the oldest file, a malformed open row) raises ContinuityRefused. The caller then falls back to the legacy `recover()` (engine_restart), which stays correct for a feed with a hole.

WHAT IT DOES NOT CLAIM. Equality with an uninterrupted engine holds when the replay start is early enough that every input to the positions' pricing and to the slot clock is inside the replay
(the nearby window, V carried from earlier prints of the pool, the clock's 300 s window, gaps overlapping the span): REPLAY_MARGIN_MS_DEFAULT is a floor, not a proof. The proof is the md5
restart-equivalence of tools/arena_restart_equiv.py, run per book on exploration hours. The engine's late-print watch list (`priced`) is rebuilt only for positions priced again during the
replay; a late print for a position priced before the kill is not flagged (counted in the harness as a difference if it ever occurs).

Layout read: <tape_dir>/tape-<UTC hour>.jsonl (open hour, append-only complete lines) or .jsonl.zst (closed hour, compacted), the arena feed's layout, as the writer produces it.
"""

from __future__ import annotations

if __package__ in (None, ""):  # `python3 tools/arena_tip_continuity.py` from the repo root
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from tools.arena_paper import ArenaEngine, ArenaRefused, Position, canon
from tools.arena_zst import open_zst

SCHEMA_CKPT = "arena_tip_engine_checkpoint_v1"
CKPT_NAME = "engine-checkpoint.json"
H_MS = 3_600_000
SPS_WINDOW_MS = 300_000  # tools/h5_shadow.SPS_WINDOW_S: the slot clock's rolling window
REPLAY_MARGIN_MS_DEFAULT = 2 * SPS_WINDOW_MS  # before the oldest open position: the clock window twice over (the nearby window is 2 s, V is per print)
_HOUR_RE = re.compile(r"^tape-(\d{4}-\d{2}-\d{2}T\d{2})\.jsonl(\.zst)?$")
CLOSED_KINDS = ("trade", "excluded")


class ContinuityRefused(Exception):
    """The replay cannot be proven complete: the caller falls back to ArenaEngine.recover() (open positions become engine_restart). Carries a short `reason`, never file content."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


class ModelCalledOnReplay(AssertionError):
    """A model call on the replay path. There is none by construction; this exists so that a future change that adds one fails loudly."""


class ReplayModel:
    """The driver's `model`: every use raises. A replay takes its decisions from the log."""

    def __call__(self, *a: Any, **k: Any) -> Any:
        raise ModelCalledOnReplay("a replay never calls the model: decisions come from the decision log")

    def __getattr__(self, name: str) -> Any:
        raise ModelCalledOnReplay(f"a replay never calls the model (attribute {name!r})")


# ------------------------------------------------------------------------------------------------------------------------------------ tape
def hour_key(t_ms: int) -> str:
    return datetime.fromtimestamp(t_ms // 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H")


def hour_start_ms(h: str) -> int:
    return int(datetime.strptime(h, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp() * 1000)


def tape_hours(tape_dir: str | os.PathLike) -> dict[str, Path]:
    """hour -> the file that holds it (the open .jsonl wins over a .zst of the same hour: it is complete while a compaction is unfinished)."""
    out: dict[str, Path] = {}
    for p in sorted(Path(tape_dir).iterdir()):
        m = _HOUR_RE.match(p.name)
        if m and (m.group(1) not in out or not m.group(2)):
            out[m.group(1)] = p
    return out


def _lines(path: Path) -> Iterator[bytes]:
    """Complete lines of a tape file (a torn last line of an open hour is not a line). A .zst is streamed through the zstd CLI."""
    f = open_zst(path) if path.name.endswith(".zst") else open(path, "rb")
    buf = b""
    try:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            buf += chunk
            *whole, buf = buf.split(b"\n")
            for ln in whole:
                if ln.strip():
                    yield ln
    finally:
        f.close()


def count_lines(path: Path) -> int:
    return sum(1 for _ in _lines(path))


@dataclass(frozen=True)
class Cursor:
    """The engine has consumed the first `lines` non-empty complete lines of the hour file `hour` and every line of the earlier hours."""

    hour: str
    lines: int

    def as_dict(self) -> dict[str, Any]:
        return {"hour": self.hour, "lines": self.lines}


def iter_tape(tape_dir: str | os.PathLike, from_hour: str, until: Cursor | None = None) -> Iterator[tuple[Cursor, dict]]:
    """(cursor AFTER the row, row) for every row from the start of hour `from_hour` in file order, up to and including the cursor `until`. Raises ContinuityRefused when an hour between the start
    and the end is missing (the persisted tape is not complete there)."""
    hours = tape_hours(tape_dir)
    if from_hour not in hours:
        raise ContinuityRefused("replay_start_missing", from_hour)
    ordered = sorted(h for h in hours if h >= from_hour)
    last = until.hour if until is not None else ordered[-1]
    if last not in hours:
        raise ContinuityRefused("cursor_hour_missing", last)
    h = from_hour
    while h <= last:
        if h not in hours:
            raise ContinuityRefused("hour_missing", h)
        n = 0
        if until is not None and h == until.hour and until.lines == 0:
            return
        for ln in _lines(hours[h]):
            n += 1
            try:
                row = json.loads(ln)
            except ValueError:
                row = {"type": "bad_line"}  # the engine ignores a row it cannot use; the line still counts toward the cursor
            yield Cursor(h, n), row
            if until is not None and h == until.hour and n >= until.lines:
                return
        if until is not None and h == until.hour:
            raise ContinuityRefused("tape_shorter_than_cursor", f"{h} has {n} lines, the cursor says {until.lines}")
        h = hour_key(hour_start_ms(h) + H_MS)


def tape_end(tape_dir: str | os.PathLike) -> Cursor:
    hours = tape_hours(tape_dir)
    if not hours:
        raise ContinuityRefused("empty_tape")
    h = max(hours)
    return Cursor(h, count_lines(hours[h]))


# ------------------------------------------------------------------------------------------------------------------------------------ decisions
@dataclass(frozen=True)
class LoggedEntry:
    """One entry decision as the book's decision log holds it. `ms` is the decision instant, `slot` the decision slot; `order_id` is the engine's position id (sha of bot|pool|ms|slot)."""

    bot_id: str
    pool: str
    ms: int
    slot: int
    sol_in: float | None
    meta: Mapping[str, Any]
    order_id: str | None = None


def position_id(bot_id: str, pool: str, ms: int, slot: int) -> str:
    return hashlib.sha256(f"{bot_id}|{pool}|{int(ms)}|{int(slot)}".encode()).hexdigest()[:16]  # as ArenaEngine.buy


def entries_from_log(rows: Sequence[Mapping[str, Any]], bot_of: Any = None) -> list[LoggedEntry]:
    """Entry decisions out of decision-log rows (tools/arena_declog.DecisionLog rows: kind == "entry"; ms, order_id, pool, decision_slot, meta, stake_sol | size). `bot_of(row)` names the
    engine bot (default: the row's `arm`, which is the log name). Order: (ms, seq)."""
    pick = bot_of or (lambda r: r["arm"])
    out = []
    for r in sorted((r for r in rows if r.get("kind") == "entry"), key=lambda r: (int(r["ms"]), int(r.get("seq", 0)))):
        stake = r.get("stake_sol", r.get("size"))
        out.append(LoggedEntry(str(pick(r)), str(r["pool"]), int(r["ms"]), int(r.get("decision_slot") or 0), None if stake is None else float(stake), dict(r.get("meta") or {}),
                               r.get("order_id")))
    return out


# ------------------------------------------------------------------------------------------------------------------------------------ exits
class ExitRule:
    """A book's exit as a PURE function of the engine's pool history after the entry. `check` is called after each print of a position's pool, in tape order; it returns a reason when the
    book would sell now, else None. It may read pos, the history and (t_ms, slot) of the print; it keeps no memory of its own (a restart cannot restore a memory)."""

    name = "exit"

    def check(self, pos: Position, hist: Any, t_ms: int, slot: int) -> str | None:  # pragma: no cover - interface
        raise NotImplementedError


class StopTargetTimeRule(ExitRule):
    """Reference rule (stop / target / max hold), used by the fixtures and the harness self-test. The entry mark is the first print at or after pos.sd + settle_slots; the mark is the
    print's pool price (q + v) / b. NOT any registered book's rule: HV7 wires its own exit function through the same interface."""

    name = "stop_target_time"

    def __init__(self, stop_pct: float = -0.08, target_pct: float = 0.20, max_hold_ms: int = 3_600_000, settle_slots: int = 6) -> None:
        self.stop_pct, self.target_pct, self.max_hold_ms, self.settle_slots = stop_pct, target_pct, max_hold_ms, settle_slots

    def check(self, pos: Position, hist: Any, t_ms: int, slot: int) -> str | None:
        a = hist.arrays()
        sl = a["sl"]
        i0 = int(sl.searchsorted(pos.sd + self.settle_slots, "left"))
        if i0 >= len(sl) or slot < pos.sd + self.settle_slots:
            return None
        p0 = (a["q"][i0] + a["v"][i0]) / a["b"][i0]
        p = (a["q"][-1] + a["v"][-1]) / a["b"][-1]
        ret = p / p0 - 1.0
        if ret <= self.stop_pct:
            return "stop"
        if ret >= self.target_pct:
            return "target"
        if t_ms - pos.decision_ms >= self.max_hold_ms:
            return "time"
        return None


# ------------------------------------------------------------------------------------------------------------------------------------ driver
class BookDriver:
    """Feeds persisted rows to an engine and issues the book's decisions the way a live run does, on a SIM clock (the tape's own), so that a restart is replayable.

    mode "live":    a logged entry is executed with engine.buy at its instant (the live book already wrote the decision; this stands in for the engine call).
    mode "restore": a logged entry is ADOPTED from the ledger (its `open` row) or skipped (already priced / refused / never opened); nothing is bought.
    In both modes exits come from `rule` after each print of the position's pool and go through engine.sell.
    An entry with ms <= t is issued BEFORE the first row with t_recv_ms >= its ms is applied (strict as-of, the Host's `poll(until_ms=T-1)` rule)."""

    def __init__(self, engine: ArenaEngine, rule: ExitRule, entries: Sequence[LoggedEntry], *, mode: str = "live", blind: tuple[int, int] | None = None) -> None:
        self.engine, self.rule = engine, rule
        self.entries = sorted(entries, key=lambda e: (e.ms, e.bot_id, e.pool))
        self.i = 0
        self.mode = mode
        self.blind = blind  # (lo, hi]: no entry decision exists in this interval (the engine host was down)
        self.sim_now = 0
        self.counters: dict[str, int] = {}
        self.cursor: Cursor | None = None
        self.model = ReplayModel()
        self.bots = {e.bot_id for e in entries}
        self._ledger: dict[str, dict[str, Any]] = {}
        engine.now_fn = lambda: self.sim_now

    def _count(self, k: str, n: int = 1) -> None:
        self.counters[k] = self.counters.get(k, 0) + n

    def feed(self, cursor: Cursor, row: Mapping[str, Any]) -> None:
        t = row.get("t_recv_ms")
        t = t if isinstance(t, int) and not isinstance(t, bool) else None
        if t is not None:
            self._due(t)
            self.sim_now = max(self.sim_now, t)
        self.engine.on_row(dict(row))
        self.cursor = cursor
        self._count("rows")
        if t is not None and row.get("type", "trade") == "trade":
            self._exits(row, t)

    def finish_entries(self, until_ms: int) -> None:
        """Issue the entries due by `until_ms` that no later row will trigger (the end of the tape)."""
        self._due(until_ms)

    def _due(self, t: int) -> None:
        while self.i < len(self.entries) and self.entries[self.i].ms <= t:
            e = self.entries[self.i]
            self.i += 1
            self.sim_now = max(self.sim_now, e.ms)
            if self.blind is not None and self.blind[0] < e.ms <= self.blind[1]:
                self._count("entries_blind")
            elif self.mode == "restore":
                self._adopt(e)
            else:
                self._enter(e)

    def _enter(self, e: LoggedEntry) -> None:
        try:
            pid = self.engine.buy(e.bot_id, e.pool, e.sol_in, e.ms, e.slot, dict(e.meta))
        except ArenaRefused:
            self._count("entries_refused")
            return
        if e.order_id is not None and pid != e.order_id:
            raise ContinuityRefused("order_id_mismatch", f"{e.order_id} != {pid}")
        self._count("entries")

    def load_ledger(self) -> None:
        """What the ledger holds per position: open row, closed (trade / excluded), refused."""
        for bot in sorted(self.bots):
            st: dict[str, Any] = {}
            for r in self.engine.ledger.read_bot(bot):
                pid, k = r.get("position_id"), r.get("kind", "trade")
                if k == "open":
                    st.setdefault(pid, {})["open"] = r
                elif k in CLOSED_KINDS:
                    st.setdefault(pid, {})["closed"] = True
                elif k == "refused":
                    st.setdefault(pid, {})["refused"] = True
            self._ledger.update({(bot, p): v for p, v in st.items()})

    def _adopt(self, e: LoggedEntry) -> None:
        pid = position_id(e.bot_id, e.pool, e.ms, e.slot)
        if e.order_id is not None and e.order_id != pid:
            raise ContinuityRefused("order_id_mismatch", f"{e.order_id} != {pid}")
        st = self._ledger.get((e.bot_id, pid), {})
        if st.get("closed"):
            self._count("entries_already_priced")
        elif st.get("open") is not None:
            r = st["open"]
            try:
                self.engine.positions[pid] = Position(pid, e.bot_id, r["frozen_sha"], r["pool"], r["mint"], r["prog"], r["decision_ms"]["entry"], r["decision_slot"]["entry"],
                                                      r["effective_slot"]["entry"], r["sps_entry"], r["bot_sol_in"], r["meta"], restarted=False,
                                                      stamp_ms=(r.get("stamp_ms") or {}).get("entry", r["decision_ms"]["entry"]), call_ms=(r.get("call_ms") or {}).get("entry", 0),
                                                      call_feed_slot=r.get("call_feed_slot"))
            except (KeyError, TypeError) as exc:
                raise ContinuityRefused("open_row_malformed", f"{pid}: {type(exc).__name__}") from None
            self.engine.start_record(e.bot_id)  # this engine's code state goes on the ledger, as after any start
            self._count("adopted")
        elif st.get("refused"):
            self._count("entries_refused_before")
        else:
            self._count("entry_without_open_row")  # killed between the decision log and the engine: never re-sent

    def _exits(self, row: Mapping[str, Any], t: int) -> None:
        pool = row.get("pool") or row.get("mint")
        h = self.engine.pools.get(pool) if pool else None
        if h is None:
            return
        slot = int(row["slot"])
        for pid in sorted(p for p, pos in self.engine.positions.items() if pos.pool == pool and pos.exit_slot is None and not pos.restarted and pos.bot_id in self.bots):
            pos = self.engine.positions[pid]
            why = self.rule.check(pos, h, t, slot)
            if not why:
                continue
            try:
                self.engine.sell(pos.bot_id, pid, 1.0, t, slot, why)
                self._count("exits")
            except ArenaRefused:
                self._count("sell_refused")  # the entry leg has not landed yet: the rule is re-evaluated on the next print

    # -- checkpoint
    def checkpoint(self, margin_ms: int = REPLAY_MARGIN_MS_DEFAULT) -> dict[str, Any]:
        opens = [p.decision_ms for p in self.engine.positions.values() if not p.restarted]
        warm = min(opens + [self.sim_now]) - margin_ms
        return {"schema": SCHEMA_CKPT, "cursor": self.cursor.as_dict() if self.cursor else None, "sim_now": self.sim_now, "hw_ms": self.engine.hw_ms, "hw_slot": self.engine.hw_slot,
                "warm_from_ms": warm, "n_open": len(opens), "engine_id": self.engine.engine_id}


def save_checkpoint(path: str | os.PathLike, ckpt: Mapping[str, Any]) -> None:
    p = Path(path)
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(canon(dict(ckpt)).encode() + b"\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)


def load_checkpoint(path: str | os.PathLike) -> dict[str, Any]:
    try:
        ck = json.loads(Path(path).read_bytes())
    except (OSError, ValueError):
        raise ContinuityRefused("no_checkpoint", str(Path(path).name)) from None
    cur = ck.get("cursor") if isinstance(ck, dict) else None
    if not isinstance(ck, dict) or ck.get("schema") != SCHEMA_CKPT or not isinstance(cur, dict) or not isinstance(cur.get("hour"), str) or not isinstance(cur.get("lines"), int):
        raise ContinuityRefused("checkpoint_malformed")
    return ck


# ------------------------------------------------------------------------------------------------------------------------------------ restore
def restore_engine(engine: ArenaEngine, tape_dir: str | os.PathLike, entries: Sequence[LoggedEntry], rule: ExitRule, checkpoint: str | os.PathLike | Mapping[str, Any], *,
                   until: Cursor | None = None, margin_ms: int = REPLAY_MARGIN_MS_DEFAULT, blind: tuple[int, int] | None = None) -> BookDriver:
    """Rebuild `engine` (fresh, empty) after a restart and return the driver in mode "live", positioned at `until` (default: the end of the persisted tape), ready to be fed the next rows.

    There is no model parameter: the entries are the decision log's. ContinuityRefused when the replay cannot be proven complete (see the module docstring); the engine is then untouched
    except for the rows already replayed, and the caller discards it and uses ArenaEngine.recover() on a fresh one."""
    ck = dict(checkpoint) if isinstance(checkpoint, Mapping) else load_checkpoint(checkpoint)
    if ck.get("schema") != SCHEMA_CKPT or not isinstance(ck.get("cursor"), dict):
        raise ContinuityRefused("checkpoint_malformed")
    cur = Cursor(str(ck["cursor"]["hour"]), int(ck["cursor"]["lines"]))
    hours = tape_hours(tape_dir)
    if cur.hour not in hours:
        raise ContinuityRefused("cursor_hour_missing", cur.hour)
    have = count_lines(hours[cur.hour])
    if have < cur.lines:
        raise ContinuityRefused("tape_shorter_than_checkpoint", f"{cur.hour}: {have} < {cur.lines}")
    end = until if until is not None else tape_end(tape_dir)
    if (end.hour, end.lines) < (cur.hour, cur.lines):
        raise ContinuityRefused("restore_end_before_checkpoint")
    drv = BookDriver(engine, rule, entries, mode="restore", blind=blind)
    drv.load_ledger()
    opens = [int(v["open"]["decision_ms"]["entry"]) for v in drv._ledger.values() if v.get("open") is not None and not v.get("closed")]
    cands = [m - margin_ms for m in opens]
    if isinstance(ck.get("warm_from_ms"), int) and not isinstance(ck.get("warm_from_ms"), bool):
        cands.append(int(ck["warm_from_ms"]))
    if not cands:
        raise ContinuityRefused("no_replay_start")
    start_ms = min(cands)
    first = hour_key(max(start_ms, 0))
    drv.entries = [e for e in drv.entries if e.ms >= start_ms]  # an entry older than the replay start is closed or was never opened: not adoptable, not needed
    for c, row in iter_tape(tape_dir, first, end):
        drv.feed(c, row)
    drv.mode = "live"
    drv._count("restored_rows", drv.counters.get("rows", 0))
    return drv


def engine_fingerprint(engine: ArenaEngine, pools: Sequence[str] | None = None) -> str:
    """md5 of the pool histories (keys, slots, reserves, V and its source) of `pools` (default: every pool with an open position), the high-water marks and the gap list. The evidence that a
    restored engine holds what the uninterrupted one held at the same cursor."""
    names = sorted(pools if pools is not None else {p.pool for p in engine.positions.values()})
    body = {n: [[list(k) for k in engine.pools[n].keys], engine.pools[n].slot, engine.pools[n].q, engine.pools[n].b, engine.pools[n].v, engine.pools[n].vsrc] for n in names if n in engine.pools}
    gaps = [[g.prog, g.t_lo, g.t_hi, g.s_lo, g.s_hi, g.kind] for g in engine.gaps]
    return hashlib.md5(canon({"pools": body, "hw_slot": engine.hw_slot, "hw_ms": engine.hw_ms, "gaps": gaps}).encode()).hexdigest()


__all__ = ["BookDriver", "ContinuityRefused", "Cursor", "ExitRule", "LoggedEntry", "ModelCalledOnReplay", "ReplayModel", "StopTargetTimeRule", "entries_from_log", "engine_fingerprint",
           "iter_tape", "load_checkpoint", "position_id", "restore_engine", "save_checkpoint", "tape_end", "tape_hours"]
