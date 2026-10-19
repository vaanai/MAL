#!/usr/bin/env python3
"""arena_rules: the rule-bot host of the forward paper arena (cohort 2). PAPER only. No wallet or trading key, no model, no network.

A rule is a small pure object (`Rule`): `on_row(ctx, row)` and `on_tick(ctx, T)` return orders (`Buy`, `Sell`). It does no I/O and never reads a clock; `ctx` gives
a read-only view of `tools.arena_state.ArenaState`, the bot's own open positions, the attempt keys it already used, and `now_ms`. This host does the rest. It is
modelled on `tools/hv4_runner.py` (that module is a frozen book's code and pulls model code, so nothing here imports it):

  * `ArenaState` over the tape dir (live: a 365-min warm-up, then tail; replay: from a given start). `state.apply` is wrapped like hv4's Runner: the state applies the
    print, then `engine.on_row(row)`, then the rules see it, so the engine holds the print before any `buy`.
  * `ArenaEngine` with `stack_config(feed_delay_slots=7)` (a default, overridable). It prices at 0.25 / 0.5 / 1.0 SOL; only the stake pinned in `bots.json` decides.
  * DECISION LOG FIRST. Per order the host appends the decision row (`kind` entry | exit; flock, fsync, hash chain, `tools.arena_declog`) and only then calls
    `engine.buy` / `engine.sell`. An `ArenaRefused` adds an `order_refused` row. If the log cannot be written the order is NOT sent and the host stops (`HostFatal`).
  * TIMING. Event rules decide inside the row callback; time rules use a 1 s tick. Rows are delivered with `state.poll(until_ms=T - 1)`, so a tick at T never has seen a
    row with t_recv_ms >= T (strict as-of). Live, rows are polled continuously but never past the next tick, and `decision_ms` is the wall clock at the write. Replay, the
    sim clock is the tape's own: `decision_ms` = the triggering row's t_recv_ms (event) or T (tick), so a replay is a pure function of the tape.
  * NO ORDER BEFORE THE CLOCK. A row or tick before `clock_start_ms` is never shown to a rule, and an order stamped before it is dropped (counted, not logged).
    Live, a row older than `stale_row_ms` (10 s) at dispatch is not shown to the rules either (a restart's catch-up over the warm-up tape): a counted decision is
    written live at its decision instant and is never generated afterwards (DEC-031 section 1.2). The first `catch_up` also runs with dispatch off.
  * UNIVERSE. A `Buy` is dropped (counted) unless its pool is a PumpSwap pool whose quote mint is verified WSOL. A pump_bonding row can never become an order, and the
    scorer would make any book with one REPORT_ONLY (`has_bonding`).
  * MEMORY. A rule keeps no memory. What it needs ("this mint was already attempted", "the wallet that opened this position") comes from the log: `ctx.attempted` is the
    set of `attempt_key`s of every entry decision (refused ones included: a refusal is still a decision), `ctx.positions` the open positions with their `meta`.
    `resume()` verifies the hash chain, replays the log into that memory, runs `engine.recover()`, and re-opens any engine position the log shows as sold but the engine
    never priced (it is then sold again and booked by the engine as excluded, `engine_restart`, as in hv4).
  * Several rules can be hosted by one process (the names on the command line). Fast-hold and slow-hold books run in SEPARATE processes, so one crash or restart does not
    exclude both.

Modes (`main`): `--mode register` appends the bots.json lines and exits; `--mode run` is live (MiScusi job, see scripts/research/arena-rules-job.sh); `--mode replay`
steps a tape slice with the sim clock (refused if any output path is inside /data/mal/arena: a counted decision is never regenerated).

FREEZE. Every rule is a new file. After a book is registered, this host and its rule file are never edited; a later cohort gets a new module, process, sha and bot_ids.
"""

from __future__ import annotations

if __package__ in (None, ""):  # `python tools/arena_rules.py`
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import hashlib
import importlib
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from tools.arena_declog import DecisionLog, LogChainError
from tools.arena_paper import ArenaEngine, ArenaRefused, LedgerCorrupt, StackConfig, canon, load_registry, process_code_state, register_bot
from tools.arena_state import WARM_MIN_DEFAULT, WSOL_MINT, ArenaState

RULES_ROOT = "/data/mal/arena/rules"
TAPE_DIR = "/data/mal/arena/tape"
LEDGER_ROOT = "/data/mal/arena/ledger"
REGISTRY_PATH = "/data/mal/arena/bots.json"
ARENA_OUTPUT_ROOT = "/data/mal/arena"  # replay refuses any output path inside it
DEFAULT_FEED_DELAY_SLOTS = 7
MIN_MS = 60_000
TICK_MS = 1_000
TICK_LAG_MS = 100  # live: step(T) runs once the wall clock is T + 100 ms (rows of the second before T have landed)
POLL_SLEEP_S = 0.02
STALE_ROW_MS = 10_000
STATUS_EVERY_TICKS = 600
SELL_EARLY = "slower entry leg"  # the engine's refusal text for a sell decided before the slower entry leg lands (arena_paper.sell)
SELL_ENGINE_DROPS = ("no open position", "already has a sell")  # the engine does not hold the position (or already holds its sell): the book lets it go
SELL_RETRY_MAX = 5  # refusals that are not "the entry has not landed" before the retries slow down (and an alert row is logged)
SELL_EARLY_CAP_MS = 60_000
SELL_SLOW_RETRY_MS = 10_000
RULE_MODULES = {"h2ps-a1": "tools.arena_rule_h2ps"}  # rule name -> module with make_rule(); every new rule is a new file and a new line here, in a new host version


class HostFatal(RuntimeError):
    """The decision log (or the ledger) cannot take a row: no more orders are sent and the process must stop."""


class HostError(RuntimeError):
    """A start-up refusal: unregistered or mismatching bot, dirty or wrong-sha checkout, unknown rule, replay into the live output tree."""


# ------------------------------------------------------------------------------------------------------------------------------ rule interface
@dataclass(frozen=True)
class Buy:
    """Open a position on `pool` (a pool address for PumpSwap). `attempt_key` is remembered (log-backed) once the entry is decided; a second Buy with the same key is
    dropped by the host. `meta` is recorded in the decision row and in the engine's `open` row."""

    pool: str
    meta: Mapping[str, Any] = field(default_factory=dict)
    attempt_key: str | None = None


@dataclass(frozen=True)
class Sell:
    """Close a position (all of it). The first Sell for a position wins; refusals are retried by the host, not by the rule."""

    position_id: str
    reason: str


@dataclass(frozen=True)
class PositionView:
    position_id: str
    pool: str
    meta: Mapping[str, Any]
    decision_ms: int  # the stamp of the entry decision (the decision row's ms)
    decision_slot: int
    exiting: bool  # a Sell has been decided and is being sent / retried


class StateView:
    """Read-only window on ArenaState: queries only. No poll, no apply, no callbacks, so a rule cannot move the clock or read ahead of the host. The Pool objects it
    returns are the state's own (not copies): a rule must not mutate them."""

    __slots__ = ("_s",)

    def __init__(self, state: ArenaState) -> None:
        self._s = state

    @property
    def now_ms(self) -> int:
        return self._s.now_ms

    def pool(self, key: str):
        return self._s.pool(key)

    def pools(self, predicate: Callable | None = None):
        return self._s.pools(predicate)

    def pools_wsol(self, predicate: Callable | None = None):
        return self._s.pools_wsol(predicate)

    def pools_for_mint(self, mint: str):
        return self._s.pools_for_mint(mint)

    def snapshot(self, key: str, *, candles: bool = True):
        return self._s.snapshot(key, candles=candles)

    def windows(self, p):
        return self._s.windows(p)

    def top_net(self, p, n: int = 5):
        return self._s.top_net(p, n)

    def candles(self, p, n: int = 360, fill: bool = False):
        return self._s.candles(p, n, fill)

    def creator_net_sol(self, p):
        return self._s.creator_net_sol(p)

    def gap_spans(self, t0: int | None = None, t1: int | None = None, prog: str | None = None):
        return self._s.gap_spans(t0, t1, prog)

    def has_gap(self, t0: int, t1: int, prog: str | None = None) -> bool:
        return self._s.has_gap(t0, t1, prog)


@dataclass
class OpenPos:
    """The host's record of one open position of a bot (rebuilt from the decision log on a restart)."""

    position_id: str
    pool: str
    meta: dict
    decision_ms: int
    decision_slot: int
    exit_reason: str | None = None
    attempts: int = 0  # exit decisions written
    refused: int = 0  # sell refusals that are not "the entry leg has not landed"
    first_try_ms: int | None = None
    next_try_ms: int = 0
    alerted: bool = False
    sent: bool = False  # the log shows a sell the engine accepted (replay only)


class BotBook:
    """Open positions and attempt keys of one bot. Everything here is a function of the decision log."""

    def __init__(self, bot_id: str) -> None:
        self.bot_id = bot_id
        self.open: dict[str, OpenPos] = {}
        self.attempted: set[str] = set()
        self._views: tuple[PositionView, ...] | None = None
        self._att: frozenset[str] | None = None

    def touch(self) -> None:
        self._views = None
        self._att = None

    def views(self) -> tuple[PositionView, ...]:
        if self._views is None:
            self._views = tuple(PositionView(p.position_id, p.pool, dict(p.meta), p.decision_ms, p.decision_slot, p.exit_reason is not None)
                                for _, p in sorted(self.open.items()))
        return self._views

    def attempted_view(self) -> frozenset[str]:
        if self._att is None:
            self._att = frozenset(self.attempted)
        return self._att

    def add(self, pos: OpenPos) -> None:
        self.open[pos.position_id] = pos
        self.touch()

    def drop(self, pid: str) -> None:
        self.open.pop(pid, None)
        self.touch()

    def apply_log_row(self, r: Mapping[str, Any]) -> None:
        """Replay one decision-log row in order, with the same transitions the live code applied."""
        kind = r.get("kind")
        pid = r.get("order_id")
        if kind == "entry":
            self.open[pid] = OpenPos(pid, r["pool"], dict(r.get("meta") or {}), int(r["ms"]), int(r.get("decision_slot") or 0))
            if r.get("attempt_key") is not None:
                self.attempted.add(r["attempt_key"])
        elif kind == "exit":
            p = self.open.get(pid)
            if p is not None:
                p.exit_reason, p.sent, p.attempts = r.get("reason"), True, max(p.attempts, int(r.get("attempt") or 1))
                p.first_try_ms = int(r["ms"]) if p.first_try_ms is None else p.first_try_ms
        elif kind == "order_refused":
            p = self.open.get(pid)
            if p is None:
                return
            if r.get("side") == "buy":
                del self.open[pid]  # the engine never held it
            else:
                msg = str(r.get("detail") or r.get("reason") or "")
                p.sent = False
                if any(k in msg for k in SELL_ENGINE_DROPS):
                    del self.open[pid]
                elif SELL_EARLY not in msg:
                    p.refused += 1
        elif kind == "sell_alert":
            p = self.open.get(pid)
            if p is not None:
                p.alerted = True
        self.touch()

    def finish_replay(self) -> None:
        """A sell the log shows as sent and not refused is out of the book (the engine took it); the rest stay open."""
        for pid in [k for k, p in self.open.items() if p.sent]:
            del self.open[pid]
        self.touch()


class Ctx:
    """What a rule is shown. Treat every attribute as read-only; the host updates `now_ms` between calls."""

    __slots__ = ("state", "_book", "now_ms", "clock_start_ms")

    def __init__(self, state: StateView, book: BotBook, clock_start_ms: int) -> None:
        self.state, self._book, self.now_ms, self.clock_start_ms = state, book, 0, clock_start_ms

    @property
    def positions(self) -> tuple[PositionView, ...]:
        return self._book.views()

    @property
    def attempted(self) -> frozenset[str]:
        return self._book.attempted_view()


class Rule:
    """Base class of a rule bot. Subclasses are pure: no I/O in on_row / on_tick, no wall clock, no mutable memory (construction may read its pinned config file)."""

    bot_id: str = ""
    deciding_stake: float = 0.25
    model_ids: tuple[str, ...] = ()
    uses_ticks: bool = False
    universe: str = "pumpswap_wsol"

    def on_row(self, ctx: Ctx, row: Mapping[str, Any]) -> list:
        return []

    def on_tick(self, ctx: Ctx, T: int) -> list:
        return []


def position_id(bot: str, pool: str, decision_ms: int, decision_slot: int) -> str:
    """The engine's deterministic position id (arena_paper.ArenaEngine.buy), so the order id can be logged before the order is sent. Same formula as
    hv4_runner.position_id (a test pins it to the engine's)."""
    return hashlib.sha256(f"{bot}|{pool}|{int(decision_ms)}|{int(decision_slot)}".encode()).hexdigest()[:16]


def in_universe(universe: str, pool: Any) -> bool:
    """True when `pool` (an ArenaState Pool or None) is inside the rule's universe. Only 'pumpswap_wsol' exists: a PumpSwap pool whose quote mint is VERIFIED WSOL."""
    return universe == "pumpswap_wsol" and pool is not None and pool.prog == "pumpswap" and pool.quote_mint == WSOL_MINT


def stack_config(feed_delay_slots: int = DEFAULT_FEED_DELAY_SLOTS, **kw: Any) -> StackConfig:
    """arena_paper.StackConfig with the whole-slot feed delay d passed explicitly (StackConfig refuses to price without it). Default 7 (the manager pins it)."""
    if isinstance(feed_delay_slots, bool) or not isinstance(feed_delay_slots, int) or feed_delay_slots < 0:
        raise ValueError("feed_delay_slots must be a whole number of slots >= 0")
    return StackConfig(feed_delay_slots=feed_delay_slots, **kw)


def head_binding(repo: str | os.PathLike | None = None) -> dict[str, Any]:
    """The running code's identity: {"git_sha": HEAD, "dirty": bool} (None, None from git becomes git_sha None / dirty None, which is refused)."""
    head, dirty = process_code_state(repo)
    return {"git_sha": head, "dirty": dirty}


def require_frozen_checkout(binding: Mapping[str, Any], frozen_sha: str) -> None:
    """A live book runs the code it registered: a clean checkout at its frozen sha, or it does not start."""
    if binding.get("dirty") is not False:
        raise HostError("the checkout is dirty (or unknown): the host runs from a clean checkout at its frozen sha")
    sha = str(binding.get("git_sha") or "")
    if len(frozen_sha) < 7 or not sha or not (sha.startswith(frozen_sha) or frozen_sha.startswith(sha)):
        raise HostError("the checkout is not at the frozen sha")


def load_rules(names: Sequence[str]) -> list[Rule]:
    """Instantiate the named rules (each module's `make_rule()`), in command-line order."""
    out: list[Rule] = []
    for n in names:
        if n not in RULE_MODULES:
            raise HostError(f"unknown rule {n!r}; known: {sorted(RULE_MODULES)}")
        rule = importlib.import_module(RULE_MODULES[n]).make_rule()
        if rule.bot_id != n:
            raise HostError(f"rule module {RULE_MODULES[n]} built bot_id {rule.bot_id!r}, expected {n!r}")
        out.append(rule)
    return out


@dataclass
class HostConfig:
    root: str = RULES_ROOT
    registry: str = REGISTRY_PATH
    frozen_sha: str = ""
    clock_start_ms: int = 0
    live: bool = False
    stale_row_ms: int = STALE_ROW_MS
    verbose: bool = False


def register_rules(rules: Sequence[Rule], registry: str, frozen_sha: str, clock_start_ms: int, *, now_ms: int | None = None, verify_sha: bool = True) -> dict[str, dict]:
    """Append the bots.json lines (before the clock). deciding_stake and model_ids come from the rule. now_ms / verify_sha=False are test-only (arena_paper refuses
    them unless MAL_ARENA_TEST=1)."""
    return {r.bot_id: register_bot(r.bot_id, frozen_sha, clock_start_ms, registry, deciding_stake=r.deciding_stake, model_ids=list(r.model_ids), now_ms=now_ms,
                                   verify_sha=verify_sha) for r in rules}


# ------------------------------------------------------------------------------------------------------------------------------ host
class Host:
    def __init__(self, cfg: HostConfig, rules: Sequence[Rule], state: ArenaState, engine: Any, now_fn: Callable[[], int] | None = None,
                 binding: Mapping[str, Any] | None = None) -> None:
        if not rules:
            raise HostError("no rule to host")
        ids = [r.bot_id for r in rules]
        if len(set(ids)) != len(ids) or not all(ids):
            raise HostError("rule bot_ids must be non-empty and unique")
        if cfg.live:
            require_frozen_checkout(binding or {}, cfg.frozen_sha)  # a live host must prove its checkout
        self.cfg, self.rules, self.state, self.engine = cfg, list(rules), state, engine
        self.binding = dict(binding) if binding is not None else None
        self.now_fn = now_fn or (lambda: time.time_ns() // 1_000_000)
        self.log = DecisionLog(cfg.root)
        self.books = {r.bot_id: BotBook(r.bot_id) for r in self.rules}
        self._ctx = {r.bot_id: Ctx(StateView(state), self.books[r.bot_id], cfg.clock_start_ms) for r in self.rules}
        self.slot = 0  # the highest trade slot seen: the decision_slot of every order
        self.sim_now = 0  # replay: the sim clock (the highest t_recv_ms or tick seen); the engine's call clock
        self.counters: dict[str, int] = {}
        self.hook_errors: list[str] = []
        self.fatal: BaseException | None = None
        self._dispatch = True
        if not cfg.live:
            engine.now_fn = lambda: self.sim_now
        orig = state.apply

        def apply(row: dict) -> None:
            orig(row)
            self._on_row(row)

        state.apply = apply  # poll() calls self.apply: the engine and the rules see exactly the rows the state sees, in order, the engine first

    # -- plumbing
    def _count(self, k: str, n: int = 1) -> None:
        self.counters[k] = self.counters.get(k, 0) + n

    def _hook_error(self, where: str, e: BaseException) -> None:
        self._count("hook_errors")
        if len(self.hook_errors) < 1000:
            self.hook_errors.append(f"{where}:{type(e).__name__}")

    def _set_fatal(self, e: BaseException) -> None:
        if self.fatal is None:
            self.fatal = e

    def _raise_fatal(self) -> None:
        if self.fatal is not None:
            raise HostFatal(f"{type(self.fatal).__name__}: {str(self.fatal)[:200]}") from self.fatal

    def _now(self, T: int) -> int:
        return self.now_fn() if self.cfg.live else T

    def status(self) -> dict[str, Any]:
        return {"counters": dict(sorted(self.counters.items())), "open": {b: len(bk.open) for b, bk in self.books.items()},
                "attempted": {b: len(bk.attempted) for b, bk in self.books.items()}, "slot": self.slot, "state_now_ms": self.state.now_ms,
                "hook_errors": self.hook_errors[:5], "state_counters": {k: v for k, v in self.state.counters.items() if k in ("bad_lines", "apply_errors")}}

    # -- registration and start-up checks
    def register_all(self, now_ms: int | None = None, verify_sha: bool = True) -> dict[str, dict]:
        return register_rules(self.rules, self.cfg.registry, self.cfg.frozen_sha, self.cfg.clock_start_ms, now_ms=now_ms, verify_sha=verify_sha)

    def verify_registered(self) -> None:
        """Every hosted rule must be registered, not stopped, with this process's frozen sha, clock and the rule's stake and (empty) model list."""
        reg = load_registry(self.cfg.registry)
        for r in self.rules:
            e = reg.get(r.bot_id)
            if e is None:
                raise HostError(f"{r.bot_id} is not registered in {self.cfg.registry}")
            sha = str(e["frozen_sha"])
            if not (sha.startswith(self.cfg.frozen_sha) or self.cfg.frozen_sha.startswith(sha)) or not self.cfg.frozen_sha:
                raise HostError(f"{r.bot_id} is registered at sha {sha}, this process is at {self.cfg.frozen_sha}")
            if int(e["clock_start_ms"]) != int(self.cfg.clock_start_ms) or float(e["deciding_stake"]) != float(r.deciding_stake) or list(e["model_ids"]) != list(r.model_ids):
                raise HostError(f"{r.bot_id}: clock, stake or model ids differ from its registered line")
            if e["stopped_at_ms"] is not None:
                raise HostError(f"{r.bot_id} is stopped")

    # -- resume
    def resume(self) -> dict[str, int]:
        """Verify every chain, rebuild each bot's memory from its log, recover the engine, reconcile the two (see the module docstring)."""
        self.verify_registered()
        n = {"rows": 0, "open": 0, "reopened": 0, "dropped": 0}
        for r in self.rules:
            self.log.verify(r.bot_id)
            for row in self.log.rows(r.bot_id):
                n["rows"] += 1
                self.books[r.bot_id].apply_log_row(row)
            self.books[r.bot_id].finish_replay()
        self.engine.recover()
        live_pids = {r.bot_id: {pid for pid, p in self.engine.positions.items() if p.bot_id == r.bot_id} for r in self.rules}
        dec_ms = self.now_fn() if self.cfg.live else self.sim_now
        for r in self.rules:
            book = self.books[r.bot_id]
            for pid in sorted(live_pids[r.bot_id] - set(book.open)):  # the engine holds it, the log shows it sold or never opened: sell it again (engine_restart)
                ep = self.engine.positions[pid]
                book.add(OpenPos(pid, ep.pool, dict(ep.meta), int(getattr(ep, "stamp_ms", ep.decision_ms)), int(ep.decision_slot)))
                self.log.append(r.bot_id, dec_ms, {"kind": "resume_reopen", "order_id": pid})
                n["reopened"] += 1
            for pid in sorted(set(book.open) - live_pids[r.bot_id]):  # logged as an entry, never reached the engine (killed between the log and the buy)
                book.drop(pid)
                self.log.append(r.bot_id, dec_ms, {"kind": "resume_dropped", "order_id": pid, "why": "not_in_engine"})
                n["dropped"] += 1
            n["open"] += len(book.open)
            start = getattr(self.engine, "start_record", None)
            if start is not None:
                start(r.bot_id)
            if self.binding is not None:
                self.log.append(r.bot_id, dec_ms, {"kind": "start", "binding": self.binding, "frozen_sha": self.cfg.frozen_sha, "rules": [x.bot_id for x in self.rules]})
        return n

    # -- the row callback (event rules)
    def _on_row(self, row: dict) -> None:
        t = row.get("t_recv_ms")
        live = self.cfg.live
        if not live and isinstance(t, int) and t > self.sim_now:
            self.sim_now = t
        if row.get("type") == "trade":
            s = row.get("slot")
            if isinstance(s, int) and not isinstance(s, bool) and s > self.slot:
                self.slot = s
        try:
            self.engine.on_row(row)
        except Exception as e:  # noqa: BLE001 - recorded, never swallowed silently
            self._hook_error("engine.on_row", e)
        if not self._dispatch or not isinstance(t, int) or t < self.cfg.clock_start_ms:
            return
        now = self.now_fn() if live else self.sim_now
        if live and now - t > self.cfg.stale_row_ms:
            self._count("stale_rows")
            return
        for rule in self.rules:
            ctx = self._ctx[rule.bot_id]
            ctx.now_ms = now
            try:
                orders = rule.on_row(ctx, row)
            except Exception as e:  # noqa: BLE001
                self._hook_error(f"{rule.bot_id}.on_row", e)
                continue
            if orders:
                self._submit(rule, orders, now)

    # -- the tick (time rules, sell retries)
    def catch_up(self, t_from: int) -> None:
        """Apply every row before t_from to the state and the engine with the rules switched off (the warm-up tape, or the tape a restarted process missed)."""
        self._dispatch = False
        try:
            self.state.poll(until_ms=t_from - 1)
        finally:
            self._dispatch = True
        self._raise_fatal()

    def step(self, T: int) -> None:
        """One tick at instant T: the state is stepped to T - 1 first (strict as-of), then sell retries, then the rules' on_tick."""
        if not self.cfg.live:
            self.sim_now = max(self.sim_now, T)
        self.state.poll(until_ms=T - 1)
        self._raise_fatal()
        dec = self._now(T)
        for rule in self.rules:
            book = self.books[rule.bot_id]
            self._retry_sells(rule, book, dec)
            if not rule.uses_ticks or T < self.cfg.clock_start_ms:
                continue
            ctx = self._ctx[rule.bot_id]
            ctx.now_ms = dec
            try:
                orders = rule.on_tick(ctx, T)
            except Exception as e:  # noqa: BLE001
                self._hook_error(f"{rule.bot_id}.on_tick", e)
                continue
            if orders:
                self._submit(rule, orders, dec)
        self._raise_fatal()

    def run(self, t_from: int, t_to: int, sleep: Callable[[float], None] = time.sleep) -> None:
        """Ticks T = t_from, t_from + 1 s, ... while T < t_to. Live: rows are delivered as they land (never past the next tick) and a tick runs at T + 100 ms."""
        self.catch_up(t_from)
        T = t_from
        ticks = 0
        while T < t_to:
            if self.cfg.live:
                while self.now_fn() < T + TICK_LAG_MS:
                    self.state.poll(until_ms=T - 1)
                    self._raise_fatal()
                    sleep(POLL_SLEEP_S)
            self.step(T)
            T += TICK_MS
            ticks += 1
            if self.cfg.verbose and ticks % STATUS_EVERY_TICKS == 0:
                print(canon({"t": T, **self.status()}), flush=True)

    # -- orders
    def _submit(self, rule: Rule, orders: Sequence[Any], dec_ms: int) -> None:
        book = self.books[rule.bot_id]
        for o in orders:
            try:
                if dec_ms < self.cfg.clock_start_ms:
                    self._count("order_before_clock")
                elif isinstance(o, Buy):
                    self._buy(rule, book, o, dec_ms)
                elif isinstance(o, Sell):
                    self._sell_order(rule, book, o, dec_ms)
                else:
                    self._count("order_bad_type")
            except (OSError, LogChainError, LedgerCorrupt) as e:
                self._set_fatal(e)
                return
            except Exception as e:  # noqa: BLE001 - a malformed order (for example meta that is not JSON): nothing was sent
                self._hook_error(f"{rule.bot_id}.order", e)

    def _refused(self, bot: str, dec_ms: int, pid: str, side: str, e: BaseException, reason: str | None = None) -> None:
        self.log.append(bot, dec_ms, {"kind": "order_refused", "order_id": pid, "side": side, "reason": reason or str(e).split(":")[0], "detail": str(e)[:160]})

    def _buy(self, rule: Rule, book: BotBook, o: Buy, dec_ms: int) -> None:
        if not in_universe(rule.universe, self.state.pool(o.pool)):
            self._count("blocked_universe")
            return
        key = o.attempt_key
        if key is not None and key in book.attempted:
            self._count("blocked_duplicate_attempt")
            return
        slot = self.slot
        pid = position_id(rule.bot_id, o.pool, dec_ms, slot)
        if pid in book.open:
            self._count("blocked_duplicate_order")
            return
        meta = dict(o.meta)
        # THE DECISION ROW FIRST (flock, fsync): before the engine hears of the order, so before any fill is priced. If this raises, nothing is sent.
        self.log.append(rule.bot_id, dec_ms, {"kind": "entry", "order_id": pid, "pool": o.pool, "meta": meta, "decision_slot": slot, "stake_sol": rule.deciding_stake,
                                              "attempt_key": key})
        book.add(OpenPos(pid, o.pool, meta, dec_ms, slot))
        if key is not None:
            book.attempted.add(key)
            book.touch()
        self._count("entries")
        try:
            self.engine.buy(rule.bot_id, o.pool, rule.deciding_stake, dec_ms, slot, meta)
        except ArenaRefused as e:  # a refused buy is still a decision (it counts as an attempt) and leaves no position
            self._refused(rule.bot_id, dec_ms, pid, "buy", e)
            book.drop(pid)
            self._count("entries_refused")
        except Exception as e:  # noqa: BLE001 - the ledger cannot take the row, or an engine bug: log it, stop
            self._refused(rule.bot_id, dec_ms, pid, "buy", e, reason="engine_error:" + type(e).__name__)
            book.drop(pid)
            self._count("entries_refused")
            self._set_fatal(e)

    def _sell_order(self, rule: Rule, book: BotBook, o: Sell, dec_ms: int) -> None:
        pos = book.open.get(o.position_id)
        if pos is None:
            self._count("sell_unknown_position")
            return
        if pos.exit_reason is not None:
            self._count("sell_already_pending")
            return
        pos.exit_reason = str(o.reason)
        book.touch()
        self._attempt_sell(rule, book, pos, dec_ms)

    def _retry_sells(self, rule: Rule, book: BotBook, dec_ms: int) -> None:
        for pid in sorted(book.open):
            pos = book.open.get(pid)
            if pos is not None and pos.exit_reason is not None and dec_ms >= pos.next_try_ms:
                try:
                    self._attempt_sell(rule, book, pos, dec_ms)
                except (OSError, LogChainError, LedgerCorrupt) as e:
                    self._set_fatal(e)
                    return

    def _attempt_sell(self, rule: Rule, book: BotBook, pos: OpenPos, dec_ms: int) -> None:
        pos.attempts += 1
        pos.first_try_ms = dec_ms if pos.first_try_ms is None else pos.first_try_ms
        slot = self.slot
        self.log.append(rule.bot_id, dec_ms, {"kind": "exit", "order_id": pos.position_id, "pool": pos.pool, "reason": pos.exit_reason, "decision_slot": slot,
                                              "attempt": pos.attempts})  # the decision row first, as for a buy
        try:
            self.engine.sell(rule.bot_id, pos.position_id, 1.0, dec_ms, slot, reason=pos.exit_reason or "")
        except ArenaRefused as e:
            self._refused(rule.bot_id, dec_ms, pos.position_id, "sell", e)
            msg = str(e)
            self._count("sell_refused")
            if any(k in msg for k in SELL_ENGINE_DROPS):
                book.drop(pos.position_id)  # an orphan the engine does not know (or already holds the sell of): closed in the book, counted
                self._count("sell_orphan")
                return
            early = SELL_EARLY in msg
            if not early:
                pos.refused += 1
            capped = (dec_ms - (pos.first_try_ms or dec_ms) >= SELL_EARLY_CAP_MS) if early else pos.refused >= SELL_RETRY_MAX
            if capped and not pos.alerted:
                pos.alerted = True
                self._count("sell_alert")
                self.log.append(rule.bot_id, dec_ms, {"kind": "sell_alert", "order_id": pos.position_id, "pool": pos.pool, "tries": pos.attempts, "detail": msg[:160]})
            pos.next_try_ms = dec_ms + (SELL_SLOW_RETRY_MS if capped else TICK_MS)
            return
        except Exception as e:  # noqa: BLE001
            self._refused(rule.bot_id, dec_ms, pos.position_id, "sell", e, reason="engine_error:" + type(e).__name__)
            self._set_fatal(e)
            return
        book.drop(pos.position_id)
        self._count("exits")


# ------------------------------------------------------------------------------------------------------------------------------ CLI
def _inside(path: str, root: str) -> bool:
    p, r = Path(path).resolve(), Path(root).resolve()
    return p == r or r in p.parents


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Arena rule-bot host: paper only. No key of any kind is read.")
    ap.add_argument("--rules", nargs="+", required=True, help=f"rule names to host in this process; known: {sorted(RULE_MODULES)}")
    ap.add_argument("--mode", choices=("run", "register", "replay"), default="run")
    ap.add_argument("--tape-dir", default=TAPE_DIR)
    ap.add_argument("--root", default=RULES_ROOT, help="decision logs go in <root>/decisions/<bot_id>/")
    ap.add_argument("--ledger-root", default=LEDGER_ROOT)
    ap.add_argument("--registry", default=REGISTRY_PATH)
    ap.add_argument("--frozen-sha", required=True)
    ap.add_argument("--clock-start-ms", type=int, required=True)
    ap.add_argument("--feed-delay-slots", type=int, default=DEFAULT_FEED_DELAY_SLOTS, help="the whole-slot feed delay the stack charges (the manager pins it)")
    ap.add_argument("--end-ms", type=int, default=int(os.environ["ARENA_RULES_END_MS"]) if os.environ.get("ARENA_RULES_END_MS") else None,
                    help="stop at this instant (default: run until stopped; env ARENA_RULES_END_MS)")
    ap.add_argument("--warm-min", type=int, default=WARM_MIN_DEFAULT, help="minutes of tape before the start that warm the state")
    ap.add_argument("--from-ms", type=int, default=None, help="replay: first tick")
    ap.add_argument("--to-ms", type=int, default=None, help="replay: stop before this tick")
    a = ap.parse_args(argv)
    try:
        rules = load_rules(a.rules)
        if a.mode == "register":
            regs = register_rules(rules, a.registry, a.frozen_sha, a.clock_start_ms)
            print(canon({"n_bots": len(regs)}))
            for k, v in regs.items():
                print(canon({"bot_id": k, **{x: v[x] for x in ("frozen_sha", "clock_start_ms", "deciding_stake", "model_ids")}}))
            return 0
        live = a.mode == "run"
        if not live:
            if a.from_ms is None or a.to_ms is None:
                raise HostError("replay needs --from-ms and --to-ms")
            for p in (a.root, a.ledger_root, a.registry):
                if _inside(p, ARENA_OUTPUT_ROOT):
                    raise HostError(f"replay refuses to write inside {ARENA_OUTPUT_ROOT} ({p}): a counted decision is never regenerated or replayed")
        state = ArenaState(a.tape_dir, start_ms=None if live else a.from_ms - a.warm_min * MIN_MS, warm_min=a.warm_min)
        engine = ArenaEngine(a.ledger_root, a.registry, cfg=stack_config(a.feed_delay_slots))
        cfg = HostConfig(root=a.root, registry=a.registry, frozen_sha=a.frozen_sha, clock_start_ms=a.clock_start_ms, live=live, verbose=True)
        host = Host(cfg, rules, state, engine, binding=head_binding() if live else None)
        print(canon({"resumed": host.resume()}), flush=True)
        if live:
            start = max(a.clock_start_ms, int(time.time() * 1000))
            host.run(start - start % TICK_MS, a.end_ms or 2 ** 62)
        else:
            host.run(a.from_ms, a.to_ms)
        print(canon({"done": host.status()}), flush=True)
        return 0
    except (HostError, HostFatal, LogChainError) as e:
        print(f"arena_rules: {type(e).__name__}: {str(e)[:300]}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
