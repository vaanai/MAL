#!/usr/bin/env python3
"""arena_tip_continuity: restart continuity for books fed from the PERSISTED tip-source tape (F3 of ops/reports/2026-11-02/L4-nd-audit.md; DEC-031 Am.23 item 7).

PAPER only. No key, no wallet, no RPC, no network, no model call. Stdlib + the engine's own modules. It edits no frozen file: it only drives tools.arena_paper.ArenaEngine from the outside.

THE PROBLEM. Am.23 item 7 books every position that is open across a restart of the writer or of the engine host as `engine_restart` (excluded). That was right for an engine fed live and
non-persistently. On the tip source the derived copy of the tape is complete and persisted (tools/arena_tip_tape_writer.py, resumable; tools/arena_tip_push.py), so a restarted engine can
read every row it missed. A 14-day job cap then no longer puts an exclusion inside a counted window.

WHAT THIS MODULE DOES (all of it a pure function of the persisted tape, the ledger, the decision log and the sell journal), after QP's CHANGES on #1183 (B1 to B3):
  1. `restore_engine` rebuilds an engine after a restart: it replays the persisted hour files from a replay start (the checkpoint's warm-up start, or the oldest open position minus a margin)
     up to a cursor, in order, STATE ONLY: no exit rule is evaluated and no sell is made for any row of the replay (B1). Nothing is decided for a decision instant in the downtime, because no
     process was running then; the entries of the downtime do not exist either (`entries_blind`).
  2. Positions that the ledger shows open (an `open` row and no `trade` / `excluded` row) are ADOPTED with restarted=False (ArenaEngine.recover() builds them restarted=True, which is what
     makes the later sell an `engine_restart` exclusion). Their exits start at the restarted runner's first decision after the restore, at live instants, from the complete history (B1): a stop that
     was crossed in the downtime and recovered since is not sold at once.
  3. A sell the decision log shows and the engine had not priced at the kill is used AS LOGGED, never re-derived (B2). The engine keeps a sold position's exit parameters (exit instant, effective exit
     slot `xs`, slot clock) in memory only, so `SellJournal` records them right after each accepted sell (attach_sell_journal). On restore the journal row is matched to the logged sell (instant,
     slot, reason) and re-applied, and the engine prices it from the replayed tape. A logged sell with no matching journal row is booked `engine_restart` (fail closed).
  4. A state check on EVERY restart (B3): `CheckpointRecorder` stores, at every change of the open set or the priced list (and every N rows), the fingerprint of the open positions' pools
     (prints from the since-slot on, V carry, high-water marks, gaps, the slot clock's sps), the id of the last trade row, and the engine's late-print watch list (`priced`). `restore_engine` recomputes
     the fingerprint at that row of the replay: a mismatch, a checkpoint row that never comes back, or a position priced after the checkpoint (its late-print entry would be missing) raises
     ContinuityRefused. Then the `priced` list is restored.
  5. ENTRIES ARE NEVER GENERATED. The entry decisions are the decision log's rows (`LoggedEntry`): a model-driven book (HV7's Haiku rank, an LLM arm) does not call its model on a replay. The
     restore API has no model parameter; the driver's `model` is a tripwire that raises on any use. An entry the log shows with no `open` row (a kill between the log and the engine) is counted
     and never re-sent; no late entry, ever.
  6. FAIL CLOSED. Anything the replay cannot prove (no checkpoint, the persisted tape shorter than the checkpoint's cursor, a missing hour between the replay start and the cursor, the replay start
     older than the oldest file, a malformed open row, a state mismatch) raises ContinuityRefused. The caller then falls back to the legacy `recover()` (engine_restart), which stays correct for a
     feed with a hole.

THE RULE FOR EXITS ACROSS A RESTART (the choice QP asked to be explicit; it is a rule change from DEC-031 Am.23 item 7's literal reading and needs its own amendment before any book relies on it):
exits are decided only by a running process. A sell decided before the kill is used as logged; no sell is decided in the downtime; the first exit decision after a restart is at the first print the
restarted runner evaluates, at that print's instant, so a position whose stop or target was crossed in the downtime is priced from that first live decision, not at the crossing. Where a logged sell
cannot be matched the position is `engine_restart`.

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

import bisect
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
FP_SLOT_MARGIN = 100  # slots (40 s) before the oldest open position's entry slot: the fingerprint's lower bound (its pricing reads a 2 s window)
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




# ------------------------------------------------------------------------------------------------------------------------------------ logged sells, the sell journal, adoption
@dataclass(frozen=True)
class LoggedSell:
    """A sell the decision log shows as decided and not refused (HV7: kind "sell", pid; arena_rules: kind "exit", order_id). `ms` is the decision instant the book handed to engine.sell."""

    bot_id: str
    pid: str
    ms: int
    slot: int
    reason: str


def sells_from_log(rows: Sequence[Mapping[str, Any]], bot_of: Any = None) -> list[LoggedSell]:
    """The outstanding sells of a decision log: the last `sell` / `exit` row of a position, unless an `order_refused` row with side "sell" for it follows (the book logs the decision first, then
    the engine's refusal). Rows may come from several arms' logs; the order is (ms, seq)."""
    pick = bot_of or (lambda r: r["arm"])
    last: dict[str, LoggedSell] = {}
    for r in sorted(rows, key=lambda r: (int(r["ms"]), int(r.get("seq", 0)))):
        pid = r.get("pid") or r.get("order_id")
        if not pid:
            continue
        if r.get("kind") in ("sell", "exit"):
            last[str(pid)] = LoggedSell(str(pick(r)), str(pid), int(r["ms"]), int(r.get("decision_slot") or 0), str(r.get("reason") or ""))
        elif r.get("kind") == "order_refused" and r.get("side") == "sell":
            last.pop(str(pid), None)
    return sorted(last.values(), key=lambda s: (s.ms, s.pid))


class SellJournal:
    """Append-only record of what ArenaEngine.sell computed (exit instant, effective exit slot xs, slot clock, stamps), written right after each ACCEPTED sell. The engine keeps these in
    memory only, so without the journal a sell decided but not yet priced at a kill cannot be re-issued as it was decided (B2). A kill between the engine's memory write and the journal's fsync
    leaves a logged sell without a journal row: that position is booked `engine_restart` (fail closed)."""

    FIELDS = ("exit_ms", "exit_slot", "xs", "sps_exit", "reason", "exit_stamp_ms", "exit_call_ms")

    def __init__(self, path: str | os.PathLike) -> None:
        self.path = Path(path)

    def append(self, pos: Position) -> None:
        row = {"schema": "arena_sell_journal_v1", "position_id": pos.position_id, "bot_id": pos.bot_id, **{k: getattr(pos, k) for k in self.FIELDS}}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "ab") as f:
            f.write(canon(row).encode() + b"\n")
            f.flush()
            os.fsync(f.fileno())

    def rows(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        if not self.path.exists():
            return out
        for ln in self.path.read_bytes().split(b"\n")[:-1]:  # a torn last line (no newline) is not a row
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if isinstance(r, dict) and r.get("position_id"):
                out[str(r["position_id"])] = r
        return out


def attach_sell_journal(engine: ArenaEngine, journal: SellJournal) -> None:
    """Wrap engine.sell so that every accepted, still unpriced sell of an engine position is journalled before the caller goes on. (A sell priced inside the call has its ledger rows; a restarted
    position is excluded at once; neither needs a journal row.)"""
    orig = engine.sell

    def sell(bot_id: str, position_id: str, frac: float, decision_ms: int, decision_slot: int, reason: str = "") -> None:
        orig(bot_id, position_id, frac, decision_ms, decision_slot, reason)
        pos = engine.positions.get(position_id)
        if pos is not None and pos.exit_slot is not None and not pos.restarted:
            journal.append(pos)

    engine.sell = sell  # type: ignore[method-assign]


def position_from_open_row(bot_id: str, pid: str, r: Mapping[str, Any]) -> Position:
    try:
        return Position(pid, bot_id, r["frozen_sha"], r["pool"], r["mint"], r["prog"], r["decision_ms"]["entry"], r["decision_slot"]["entry"], r["effective_slot"]["entry"], r["sps_entry"],
                        r["bot_sol_in"], r["meta"], restarted=False, stamp_ms=(r.get("stamp_ms") or {}).get("entry", r["decision_ms"]["entry"]),
                        call_ms=(r.get("call_ms") or {}).get("entry", 0), call_feed_slot=r.get("call_feed_slot"))
    except (KeyError, TypeError) as exc:
        raise ContinuityRefused("open_row_malformed", f"{pid}: {type(exc).__name__}") from None


def adopt_position(engine: ArenaEngine, bot_id: str, pid: str, open_row: Mapping[str, Any]) -> Position:
    """Put a ledger-open position into the engine with restarted=False. An empty history is created for its pool if the replay has not reached it yet (a pending sell is tried on every row)."""
    from tools.arena_paper import PoolHist

    pos = position_from_open_row(bot_id, pid, open_row)
    engine.positions[pid] = pos
    engine.pools.setdefault(pos.pool, PoolHist(pos.pool, pos.mint, pos.prog))
    engine.start_record(bot_id)  # this engine's code state goes on the ledger, as after any start
    return pos


def apply_logged_sell(engine: ArenaEngine, pos: Position, sell: LoggedSell, journal_rows: Mapping[str, Mapping[str, Any]]) -> str:
    """Use the sell AS LOGGED (B2): the journal row of the same position, with the same instant, slot and reason, gives the exit fields; the position is queued for pricing exactly as
    ArenaEngine.sell queues it. No match: the position is booked `engine_restart` (fail closed). Never re-derived."""
    jr = journal_rows.get(pos.position_id)
    if (jr is not None and jr.get("bot_id") == pos.bot_id and int(jr["exit_stamp_ms"]) == sell.ms and int(jr["exit_slot"]) == sell.slot and str(jr["reason"]) == sell.reason
            and jr.get("xs") is not None):
        pos.exit_ms, pos.exit_slot, pos.xs, pos.sps_exit, pos.reason = int(jr["exit_ms"]), int(jr["exit_slot"]), int(jr["xs"]), float(jr["sps_exit"]), str(jr["reason"])
        pos.exit_stamp_ms, pos.exit_call_ms = int(jr["exit_stamp_ms"]), int(jr["exit_call_ms"])
        engine.pending.setdefault(pos.pool, []).append(pos.position_id)
        return "sell_reapplied"
    pos.restarted = True
    pos.exit_ms, pos.exit_slot, pos.reason, pos.exit_stamp_ms = sell.ms, sell.slot, sell.reason, sell.ms
    engine._write_excluded(pos, "engine_restart", None)  # the engine's own exclusion row, as ArenaEngine.sell writes for a recovered position
    engine.positions.pop(pos.position_id, None)
    return "sell_unmatched"


def ledger_state(engine: ArenaEngine, bots: Sequence[str]) -> dict[tuple[str, str], dict[str, Any]]:
    """Per (bot, position id): the `open` row, whether a `trade` / `excluded` row exists, whether a `refused` row exists."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for bot in sorted(bots):
        for r in engine.ledger.read_bot(bot):
            pid, k = r.get("position_id"), r.get("kind", "trade")
            if k == "open":
                out.setdefault((bot, pid), {})["open"] = r
            elif k in CLOSED_KINDS:
                out.setdefault((bot, pid), {})["closed"] = True
            elif k == "refused":
                out.setdefault((bot, pid), {})["refused"] = True
    return out


def recover_with_continuity(engine: ArenaEngine, logged_sells: Sequence[LoggedSell], journal: SellJournal | None) -> dict[str, int]:
    """The replacement for ArenaEngine.recover() when the engine's state is rebuilt by replaying the persisted tape (a runner such as HV7's calls its own engine.recover() in resume()):
    every ledger-open position is adopted with restarted=False; a position with a logged, unpriced sell gets it back as logged, or `engine_restart` when the journal cannot match it."""
    from tools.arena_paper import load_registry

    sells = {s.pid: s for s in logged_sells}
    jrows = journal.rows() if journal is not None else {}
    n = {"adopted": 0, "sell_reapplied": 0, "sell_unmatched": 0}
    for (bot, pid), st in sorted(ledger_state(engine, list(load_registry(engine.registry_path))).items()):
        if st.get("open") is None or st.get("closed") or pid in engine.positions:
            continue
        pos = adopt_position(engine, bot, pid, st["open"])
        n["adopted"] += 1
        if pid in sells:
            n[apply_logged_sell(engine, pos, sells[pid], jrows)] += 1
    return n


# ------------------------------------------------------------------------------------------------------------------------------------ the state check (B3)
def row_id(row: Mapping[str, Any]) -> list[Any] | None:
    if row.get("slot") is None or not isinstance(row.get("t_recv_ms"), int):
        return None
    return [int(row["slot"]), row.get("signature"), row.get("event_index"), int(row["t_recv_ms"])]


def engine_fingerprint(engine: ArenaEngine, pools: Sequence[str] | None = None, from_slot: int | None = None) -> str:
    """md5 of the state a restart must reproduce: for each pool (default: those with an open position) the prints from `from_slot` on (keys, slots, reserves, V and its source) and the pool's
    V carry, the engine's high-water marks, its gaps (those ending at or after from_slot), and the slot clock's sps. `from_slot` is the oldest slot an open position can still read (its
    entry slot minus a margin): older prints may differ between a live engine and a replay without changing any price (HV7's housekeeping trims idle pools before they get a position; the replay
    holds them from the start). Names, counts and md5s only leave this function."""
    names = sorted(pools if pools is not None else {p.pool for p in engine.positions.values()})
    body: dict[str, Any] = {}
    for n in names:
        h = engine.pools.get(n)
        if h is None:
            continue
        i0 = bisect.bisect_left(h.slot, from_slot) if from_slot is not None else 0
        body[n] = [[list(k) for k in h.keys[i0:]], h.slot[i0:], h.q[i0:], h.b[i0:], h.v[i0:], h.vsrc[i0:], h.last_v_row]
    gaps = [[g.prog, g.t_lo, g.t_hi, g.s_lo, g.s_hi, g.kind] for g in engine.gaps if from_slot is None or g.s_hi >= from_slot]
    sps = engine.sps()
    return hashlib.md5(canon({"pools": body, "hw_slot": engine.hw_slot, "hw_ms": engine.hw_ms, "gaps": gaps, "sps": None if sps is None else round(sps, 9)}).encode()).hexdigest()


def priced_to_json(priced: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    return {p: [dict(e, trigger=None if e["trigger"] is None else list(e["trigger"]), span=list(e["span"])) for e in lst] for p, lst in priced.items() if lst}


def priced_from_json(obj: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    return {p: [dict(e, trigger=None if e["trigger"] is None else tuple(e["trigger"]), span=tuple(e["span"])) for e in lst] for p, lst in obj.items()}


class CheckpointRecorder:
    """Wraps engine.on_row. After a trade row, whenever the open set or the engine's priced list changed (and every `every_rows` rows), it records a checkpoint: the id of that row, the fingerprint
    of the open positions' pools, the late-print watch list, the replay warm-up start. Taken only right after a trade row, so a replay can compare at exactly that row. `history` keeps the last
    checkpoints; `on_save(ck)` persists one (save_checkpoint)."""

    def __init__(self, engine: ArenaEngine, *, every_rows: int = 5000, margin_ms: int = REPLAY_MARGIN_MS_DEFAULT, cursor_fn: Any = None, on_save: Any = None, history_max: int = 256) -> None:
        self.engine, self.every, self.margin_ms, self.cursor_fn, self.on_save, self.history_max = engine, every_rows, margin_ms, cursor_fn, on_save, history_max
        self.history: list[dict[str, Any]] = []
        self.hour_min_slot: dict[str, int] = {}
        self._rows = 0
        self._sig: tuple[int, int] | None = None
        self._orig = engine.on_row
        engine.on_row = self._on_row  # type: ignore[method-assign]

    def uninstall(self) -> None:
        self.engine.on_row = self._orig  # type: ignore[method-assign]

    def _n_priced(self) -> int:
        return sum(len(v) for v in self.engine.priced.values())

    def _on_row(self, row: Mapping[str, Any]) -> None:
        self._orig(row)
        t, rid = row.get("t_recv_ms"), row_id(row)
        if row.get("type", "trade") != "trade" or rid is None or not isinstance(t, int):
            return
        h, s = hour_key(t), int(row["slot"])
        if h not in self.hour_min_slot or s < self.hour_min_slot[h]:
            self.hour_min_slot[h] = s
        self._rows += 1
        sig = (len(self.engine.positions), self._n_priced())
        if sig != self._sig or self._rows % self.every == 0:
            self._sig = sig
            self.save(rid)

    def save(self, rid: list[Any]) -> dict[str, Any]:
        eng = self.engine
        opens = [p.decision_ms for p in eng.positions.values() if not p.restarted]
        warm = min(opens + [eng.hw_ms if eng.hw_ms is not None else rid[3]]) - self.margin_ms
        since = self.hour_min_slot.get(hour_key(warm))
        pools = sorted({p.pool for p in eng.positions.values()})
        sds = [p.sd for p in eng.positions.values() if not p.restarted]
        fp_from = None if since is None else max(since, min(sds) - FP_SLOT_MARGIN) if sds else since
        cur = self.cursor_fn() if self.cursor_fn else None
        ck = {"schema": SCHEMA_CKPT, "last_row_id": rid, "cursor": cur.as_dict() if cur is not None else None, "now_ms": int(eng.now_fn()), "warm_from_ms": warm, "since_slot": since,
              "fp_from_slot": fp_from, "pools": pools, "fp": None if fp_from is None else engine_fingerprint(eng, pools, fp_from), "priced": priced_to_json(eng.priced), "n_open": len(opens), "engine_id": eng.engine_id}
        self.history.append(ck)
        del self.history[: -self.history_max]
        if self.on_save is not None:
            self.on_save(ck)
        return ck


class StateCheck:
    """Wraps the FRESH engine's on_row during a replay. At the checkpoint's row it recomputes the fingerprint; a mismatch is recorded (not raised: a runner's row hook swallows exceptions) and
    `raise_if_failed` / `require_done` raise ContinuityRefused. On a match it restores the late-print watch list."""

    def __init__(self, engine: ArenaEngine, ck: Mapping[str, Any]) -> None:
        self.engine, self.ck = engine, ck
        self.done, self.failed = False, None
        self._orig = engine.on_row
        engine.on_row = self._on_row  # type: ignore[method-assign]

    def uninstall(self) -> None:
        self.engine.on_row = self._orig  # type: ignore[method-assign]

    def _on_row(self, row: Mapping[str, Any]) -> None:
        self._orig(row)
        if self.done or self.failed or row_id(row) != self.ck["last_row_id"] or row.get("type", "trade") != "trade":
            return
        got = engine_fingerprint(self.engine, self.ck["pools"], self.ck["fp_from_slot"])
        if got != self.ck["fp"]:
            self.failed = ("state_mismatch", "the replayed state at the checkpoint row differs from the checkpoint's")
            return
        merged = priced_from_json(self.ck["priced"])
        for p, lst in self.engine.priced.items():
            merged.setdefault(p, []).extend(lst)
        self.engine.priced = merged
        self.done = True

    def raise_if_failed(self) -> None:
        if self.failed:
            raise ContinuityRefused(*self.failed)

    def require_done(self) -> None:
        self.raise_if_failed()
        if not self.done:
            raise ContinuityRefused("checkpoint_row_not_replayed", "the checkpoint's last row never came back in the replay")


def check_nothing_priced_after(engine: ArenaEngine, ck: Mapping[str, Any]) -> None:
    """A position priced after the checkpoint has no late-print entry in it and is not adopted (it is closed): the watch list cannot be restored. Fail closed."""
    from tools.arena_paper import load_registry

    for bot in load_registry(engine.registry_path):
        for r in engine.ledger.read_bot(bot):
            if r.get("kind", "trade") in CLOSED_KINDS and int(r.get("written_ms") or 0) > int(ck["now_ms"]):
                raise ContinuityRefused("priced_after_checkpoint", "a position was priced after the checkpoint: its late-print watch entry is not in it")


# ------------------------------------------------------------------------------------------------------------------------------------ driver
class BookDriver:
    """Feeds persisted rows to an engine and issues the book's decisions the way a live run does, on a SIM clock (the tape's own), so that a restart is replayable.

    mode "live":    a logged entry is executed with engine.buy at its instant (the live book already wrote the decision; this stands in for the engine call). Exits come from `rule` after
                    each print of the position's pool and go through engine.sell, except for rows inside `blind_rows` (a downtime: no process, no decision).
    mode "restore": STATE ONLY. A logged entry is ADOPTED from the ledger (its `open` row) or skipped (already priced / refused / never opened); a logged sell is applied as logged
                    (journal) or the position is `engine_restart`. No exit rule is evaluated, nothing is bought, nothing is sold that the log does not show.
    An entry with ms <= t is issued BEFORE the first row with t_recv_ms >= its ms is applied (strict as-of, the Host's `poll(until_ms=T-1)` rule)."""

    def __init__(self, engine: ArenaEngine, rule: ExitRule, entries: Sequence[LoggedEntry], *, mode: str = "live", blind: tuple[int, int] | None = None,
                 blind_rows: tuple[Cursor, Cursor] | None = None, logged_sells: Sequence[LoggedSell] = (), journal_rows: Mapping[str, Mapping[str, Any]] | None = None) -> None:
        self.engine, self.rule = engine, rule
        self.entries = sorted(entries, key=lambda e: (e.ms, e.bot_id, e.pool))
        self.i = 0
        self.mode = mode
        self.blind = blind  # (lo, hi]: no entry decision exists in this interval (the engine host was down)
        self.blind_rows = blind_rows  # (kill cursor, restart cursor]: no exit decision exists for these rows
        self.sells = sorted(logged_sells, key=lambda s: (s.ms, s.pid))
        self.si = 0
        self.journal_rows = dict(journal_rows or {})
        self.sim_now = 0
        self.counters: dict[str, int] = {}
        self.cursor: Cursor | None = None
        self.decision_log: list[dict[str, Any]] = []  # what a live book writes before engine.sell: sell rows and the engine's refusals (HV7's row shape)
        self.model = ReplayModel()
        self.bots = {e.bot_id for e in entries} | {s.bot_id for s in logged_sells}
        self._ledger: dict[tuple[str, str], dict[str, Any]] = {}
        engine.now_fn = lambda: self.sim_now

    def _count(self, k: str, n: int = 1) -> None:
        self.counters[k] = self.counters.get(k, 0) + n

    def feed(self, cursor: Cursor, row: Mapping[str, Any]) -> None:
        t = row.get("t_recv_ms")
        t = t if isinstance(t, int) and not isinstance(t, bool) else None
        if t is not None:
            self._due(t)
            self.sim_now = max(self.sim_now, t)
        self.cursor = cursor
        self.engine.on_row(dict(row))
        self._count("rows")
        if self.mode == "live" and t is not None and row.get("type", "trade") == "trade":
            if self.blind_rows is not None and (self.blind_rows[0].hour, self.blind_rows[0].lines) < (cursor.hour, cursor.lines) <= (self.blind_rows[1].hour, self.blind_rows[1].lines):
                self._count("exits_blind_rows")
            else:
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
        while self.si < len(self.sells) and self.sells[self.si].ms < t:
            self._apply_sell(self.sells[self.si])
            self.si += 1

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
        self._ledger = ledger_state(self.engine, sorted(self.bots))

    def _adopt(self, e: LoggedEntry) -> None:
        pid = position_id(e.bot_id, e.pool, e.ms, e.slot)
        if e.order_id is not None and e.order_id != pid:
            raise ContinuityRefused("order_id_mismatch", f"{e.order_id} != {pid}")
        st = self._ledger.get((e.bot_id, pid), {})
        if st.get("closed"):
            self._count("entries_already_priced")
        elif st.get("open") is not None:
            adopt_position(self.engine, e.bot_id, pid, st["open"])
            self._count("adopted")
        elif st.get("refused"):
            self._count("entries_refused_before")
        else:
            self._count("entry_without_open_row")  # killed between the decision log and the engine: never re-sent

    def _apply_sell(self, s: LoggedSell) -> None:
        pos = self.engine.positions.get(s.pid)
        if pos is None or pos.bot_id != s.bot_id:
            self._count("logged_sell_not_open")  # priced before the kill, or never adopted
            return
        self._count(apply_logged_sell(self.engine, pos, s, self.journal_rows))

    def finish_restore(self) -> None:
        """The sells decided after the last replayed row but before the kill."""
        while self.si < len(self.sells):
            self._apply_sell(self.sells[self.si])
            self.si += 1

    def _log(self, body: dict[str, Any]) -> None:
        self.decision_log.append({"seq": len(self.decision_log), **body})

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
            self._log({"kind": "sell", "arm": pos.bot_id, "pid": pid, "ms": t, "decision_slot": slot, "reason": why})  # the decision row first, as the books do
            try:
                self.engine.sell(pos.bot_id, pid, 1.0, t, slot, why)
                self._count("exits")
            except ArenaRefused:
                self._log({"kind": "order_refused", "arm": pos.bot_id, "side": "sell", "pid": pid, "ms": t})
                self._count("sell_refused")  # the entry leg has not landed yet: the rule is re-evaluated on the next print


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
    if not isinstance(ck, dict) or ck.get("schema") != SCHEMA_CKPT or not isinstance(ck.get("last_row_id"), list):
        raise ContinuityRefused("checkpoint_malformed")
    return ck


# ------------------------------------------------------------------------------------------------------------------------------------ restore
def restore_engine(engine: ArenaEngine, tape_dir: str | os.PathLike, entries: Sequence[LoggedEntry], rule: ExitRule, checkpoint: str | os.PathLike | Mapping[str, Any], *,
                   logged_sells: Sequence[LoggedSell] = (), journal: SellJournal | None = None, until: Cursor | None = None, margin_ms: int = REPLAY_MARGIN_MS_DEFAULT,
                   blind: tuple[int, int] | None = None) -> BookDriver:
    """Rebuild `engine` (fresh, empty) after a restart and return the driver in mode "live", positioned at `until` (default: the end of the persisted tape), ready to be fed the next rows.

    STATE ONLY (B1): no exit is decided for any replayed row. LOGGED SELLS are used as logged (B2). The replayed state is CHECKED against the checkpoint's fingerprint (B3). There is no model
    parameter: the entries are the decision log's. ContinuityRefused when the replay cannot be proven complete and equal; the engine is then to be discarded and the caller uses
    ArenaEngine.recover() on a fresh one."""
    ck = dict(checkpoint) if isinstance(checkpoint, Mapping) else load_checkpoint(checkpoint)
    if ck.get("schema") != SCHEMA_CKPT or not isinstance(ck.get("last_row_id"), list):
        raise ContinuityRefused("checkpoint_malformed")
    if ck.get("fp") is None or ck.get("since_slot") is None or ck.get("fp_from_slot") is None:
        raise ContinuityRefused("checkpoint_without_state_fingerprint", "the recorder had not seen the warm-up hour")
    hours = tape_hours(tape_dir)
    cur = None
    if isinstance(ck.get("cursor"), dict):
        cur = Cursor(str(ck["cursor"]["hour"]), int(ck["cursor"]["lines"]))
        if cur.hour not in hours:
            raise ContinuityRefused("cursor_hour_missing", cur.hour)
        have = count_lines(hours[cur.hour])
        if have < cur.lines:
            raise ContinuityRefused("tape_shorter_than_checkpoint", f"{cur.hour}: {have} < {cur.lines}")
    end = until if until is not None else tape_end(tape_dir)
    if cur is not None and (end.hour, end.lines) < (cur.hour, cur.lines):
        raise ContinuityRefused("restore_end_before_checkpoint")
    check_nothing_priced_after(engine, ck)
    drv = BookDriver(engine, rule, entries, mode="restore", blind=blind, logged_sells=logged_sells, journal_rows=journal.rows() if journal is not None else {})
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
    check = StateCheck(engine, ck)
    try:
        for c, row in iter_tape(tape_dir, first, end):
            drv.feed(c, row)
            check.raise_if_failed()
        drv.finish_restore()
        check.require_done()
    finally:
        check.uninstall()
    drv.mode = "live"
    drv._count("restored_rows", drv.counters.get("rows", 0))
    return drv


__all__ = ["BookDriver", "CheckpointRecorder", "ContinuityRefused", "Cursor", "ExitRule", "LoggedEntry", "LoggedSell", "ModelCalledOnReplay", "ReplayModel", "SellJournal", "StateCheck",
           "StopTargetTimeRule", "adopt_position", "apply_logged_sell", "attach_sell_journal", "engine_fingerprint", "entries_from_log", "iter_tape", "load_checkpoint", "position_id",
           "recover_with_continuity", "restore_engine", "save_checkpoint", "sells_from_log", "tape_end", "tape_hours"]
