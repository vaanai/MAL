#!/usr/bin/env python3
"""arena_rules_v2: the rule-bot host, version 2: tools/arena_rules.Host plus a persistent host-side pool table (tools/arena_pool_table.py). PAPER only. No key, no network.

tools/arena_rules.py, tools/arena_rule_h2ps.py, tools/arena_paper.py and scripts/research/arena-rules-job.sh are NOT edited (h2ps-a1 and any registered book keep their
sha256). This module IMPORTS the v1 host and subclasses it; a book hosted here is registered at a sha that contains this file and runs from scripts/research/arena-rules-v2-job.sh.

WHAT V2 ADDS TO V1
  * `HostV2._on_row` feeds EVERY tape row to the PoolTable BEFORE the v1 callback, so the table sees rows before the clock, stale rows (a restart's catch-up) and rows with
    dispatch off. The table is history, never a decision. A rule reads it as `ctx.table` (a `CtxV2`).
  * A PoolTable is built per hosted rule from `rule.table_mode`, loaded from its checkpoint when one exists, checkpointed (atomic) every CHECKPOINT_EVERY_MS of tick time and at
    the end of `run`. On a live start with no checkpoint, or one older than the state warm-up, `rebuild_table` replays the closed tape hours up to the warm-up start into it.
  * `_attempt_sell` records the exit-decision instant in the table when the position leaves the book (the 60 s re-entry cooldown outlives the position).

RESTART (what the arena engine does; NOT changed here)
  * The decision log is hash-chained; `Host.resume()` rebuilds ctx.positions / ctx.attempted from it. The POOL TABLE survives the restart through its checkpoint (and the tape).
  * `ArenaEngine.recover()` (tools/arena_paper.py:1144) rebuilds every open ledger position with `restarted=True` because the engine's own pool history has a hole while it was
    down; such a position is booked EXCLUDED (reason engine_restart) when its sell is decided, and Host.resume() sells it again at the FIRST tick after the restart (resume_reopen
    or the logged reason). So open positions DO persist across a restart in the log and the ledger, but they CANNOT RESUME as normal positions: each is excluded from the read.
    Resuming them would need the engine to persist its pool history and clear `restarted`: an arena_paper change under QP, outside this PR. With 24 h holds and ~3-6 entries a
    day (3-6 open) any restart excludes every open position; over 5% excluded the read is NOT_DECIDABLE. Plan: NO planned restart for the whole span; a crash restart is
    recorded in LAB_STATE.
  * Position and table state after a restart differ in one way: the table keeps the prints, so the rule goes on deciding correctly (no 24 h blind period).

MODES (`main`): register | run | replay, as v1. replay refuses output inside /data/mal/arena.
"""

from __future__ import annotations

if __package__ in (None, ""):  # `python tools/arena_rules_v2.py`
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import importlib
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from tools import arena_rules as R
from tools.arena_declog import LogChainError
from tools.arena_paper import ArenaEngine, canon
from tools.arena_pool_table import PoolTable
from tools.arena_stack_v2 import check_ledger_send, stack_config_for
from tools.arena_state import WARM_MIN_DEFAULT, ArenaState

RULE_MODULES_V2 = {"l1-floor-v2": "tools.arena_rule_l1", "g4v1-a1": "tools.arena_rule_g4v1"}
CHECKPOINT_EVERY_MS = 600_000
SWEEP_EVERY_MS = 3_600_000
ENGINE_TRIM_EVERY_MS = 300_000  # ArenaEngine.trim_history() cadence (tick time), the same 5 tape minutes the HV4 runner uses


class CtxV2(R.Ctx):
    __slots__ = ("table",)

    def __init__(self, state: R.StateView, book: R.BotBook, clock_start_ms: int, table: PoolTable) -> None:
        super().__init__(state, book, clock_start_ms)
        self.table = table


def load_rules_v2(names: Sequence[str]) -> list[R.Rule]:
    out: list[R.Rule] = []
    for n in names:
        if n not in RULE_MODULES_V2:
            raise R.HostError(f"unknown rule {n!r}; known: {sorted(RULE_MODULES_V2)}")
        rule = importlib.import_module(RULE_MODULES_V2[n]).make_rule()
        if rule.bot_id != n:
            raise R.HostError(f"rule module {RULE_MODULES_V2[n]} built bot_id {rule.bot_id!r}, expected {n!r}")
        out.append(rule)
    return out


class HostV2(R.Host):
    def resume(self) -> dict[str, int]:
        """DEC-031 Amendment 8: refuse to continue a ledger whose rows were priced at another send than this process prices at (a restart with the wrong --stack-version)."""
        root = getattr(getattr(self.engine, "ledger", None), "root", None)
        cfg = getattr(self.engine, "cfg", None)
        if root is not None and cfg is not None:
            check_ledger_send(root, [r.bot_id for r in self.rules], cfg.send)
        return super().resume()

    def __init__(self, cfg: R.HostConfig, rules: Sequence[R.Rule], state: ArenaState, engine: Any, now_fn: Callable[[], int] | None = None,
                 binding: Mapping[str, Any] | None = None, table_path: str | os.PathLike | None = None, table: PoolTable | None = None) -> None:
        modes = {getattr(r, "table_mode", None) for r in rules}
        if None in modes or len(modes) != 1:
            raise R.HostError("HostV2 hosts rules that declare ONE table_mode")
        super().__init__(cfg, rules, state, engine, now_fn, binding)
        self.table_path = Path(table_path) if table_path else Path(cfg.root) / "pooltable" / "pooltable.pkl"
        mode = modes.pop()
        tcls = {getattr(r, "table_cls", PoolTable) for r in rules}  # a rule may ask for a PoolTable subclass (g4v1-a1: pre-trade prices); default: the base table, as for l1-floor-v2
        if len(tcls) != 1:
            raise R.HostError("HostV2 hosts rules that declare ONE table class")
        tcls = tcls.pop()
        if table is None:
            table = tcls.load(self.table_path) if self.table_path.exists() else tcls(mode)
        if table.mode != mode:
            raise R.HostError(f"the pool table checkpoint is mode {table.mode!r}, the rule wants {mode!r}")
        self.table = table
        self._ctx = {r.bot_id: CtxV2(R.StateView(state), self.books[r.bot_id], cfg.clock_start_ms, self.table) for r in self.rules}
        self._last_ckpt = 0
        self.last_ckpt_report: dict[str, Any] = {}
        self._last_sweep = 0
        self._last_trim = 0

    def _on_row(self, row: dict) -> None:
        if self.fatal is None:
            try:
                self.table.update(row)
            except Exception as e:  # noqa: BLE001 - recorded, never swallowed silently
                self._hook_error("table.update", e)
        super()._on_row(row)

    def _attempt_sell(self, rule: R.Rule, book: R.BotBook, pos: R.OpenPos, dec_ms: int) -> None:
        super()._attempt_sell(rule, book, pos, dec_ms)
        if pos.position_id not in book.open:
            self.table.note_exit(pos.pool, dec_ms)

    def step(self, T: int) -> None:
        super().step(T)
        if T - self._last_trim >= ENGINE_TRIM_EVERY_MS:
            # MEMORY ONLY. The engine keeps every print of every pool for ever (about 1 KB a print, ~450 MB per arena hour: a 2-day replay was OOM-killed at 20 GB, and the
            # forward book plans 8 GB). trim_history() (PR #745, QP-reviewed, pricing-neutral: ledger byte-equal on a 9 h replay) drops only prints no later buy can read; a buy
            # that would read below a trim floor is refused instead of mispriced. Entry decisions are made from the state and the pool table, never from the engine's prints.
            self._last_trim = T
            trim = getattr(self.engine, "trim_history", None)
            if trim is not None:
                trim()
        if T - self._last_sweep >= SWEEP_EVERY_MS:
            self._last_sweep = T
            self.table.live_positions = {pid for b in self.books.values() for pid in b.open}
            self.table.sweep(T)
        if T - self._last_ckpt >= CHECKPOINT_EVERY_MS:
            self._last_ckpt = T
            self.save_table()

    def trim_counters(self) -> dict[str, Any]:
        """REPORT-ONLY: the engine's history-trim counters (PR #745). A nonzero rows_below_trim_floor or refused_history_trimmed voids the trim's
        pricing equivalence. An engine without them (FakeEngine) reads 0. Never used by a decision."""
        c = getattr(self.engine, "counters", None)
        c = c if isinstance(c, dict) else {}
        return {k: c.get(k, 0) for k in ("rows_below_trim_floor", "refused_history_trimmed", "hist_trimmed_prints", "rows_dup")}

    def status(self) -> dict[str, Any]:
        return {**super().status(), "trim_counters": self.trim_counters()}

    def save_table(self) -> None:
        t0 = time.monotonic()
        self.table.checkpoint(self.table_path)
        try:  # report-only: table sizes, checkpoint bytes and seconds (measures the memory plan; never changes a decision)
            nbytes = os.path.getsize(self.table_path)
        except OSError:
            nbytes = -1
        self.last_ckpt_report = {"ckpt_bytes": nbytes, "ckpt_s": round(time.monotonic() - t0, 3), **self.table.sizes(), "trim_counters": self.trim_counters()}
        print(canon({"table_checkpoint": self.last_ckpt_report}), file=sys.stderr, flush=True)

    def run(self, t_from: int, t_to: int, sleep: Callable[[float], None] = time.sleep) -> None:
        try:
            super().run(t_from, t_to, sleep)
        finally:
            self.save_table()


def rebuild_table(table: PoolTable, tape_dir: str, state_start_ms: int) -> int:
    """Bring the table up to the state's warm-up start from the closed tape (the host's warm-up replay feeds the rest). Not a replay of decisions."""
    return table.rebuild(tape_dir, state_start_ms)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Arena rule-bot host v2 (pool table): paper only. No key of any kind is read.")
    ap.add_argument("--rules", nargs="+", required=True, help=f"known: {sorted(RULE_MODULES_V2)}")
    ap.add_argument("--mode", choices=("run", "register", "replay"), default="run")
    ap.add_argument("--tape-dir", default=R.TAPE_DIR)
    ap.add_argument("--root", default=R.RULES_ROOT, help="decision logs in <root>/decisions/<bot_id>/, the table checkpoint in <root>/pooltable/")
    ap.add_argument("--ledger-root", default=R.LEDGER_ROOT)
    ap.add_argument("--registry", default=R.REGISTRY_PATH)
    ap.add_argument("--frozen-sha", required=True)
    ap.add_argument("--clock-start-ms", type=int, required=True)
    ap.add_argument("--feed-delay-slots", type=int, default=R.DEFAULT_FEED_DELAY_SLOTS)
    ap.add_argument("--end-ms", type=int, default=int(os.environ["ARENA_RULES_END_MS"]) if os.environ.get("ARENA_RULES_END_MS") else None)
    ap.add_argument("--stack-version", type=int, default=1, choices=(1, 2), help="1 = 505,000 per send (default; every registered book); 2 = 160,000 (DEC-031 Amendment 8 books only)")
    ap.add_argument("--warm-min", type=int, default=WARM_MIN_DEFAULT)
    ap.add_argument("--from-ms", type=int, default=None)
    ap.add_argument("--to-ms", type=int, default=None)
    a = ap.parse_args(argv)
    try:
        rules = load_rules_v2(a.rules)
        if a.mode == "register":
            regs = R.register_rules(rules, a.registry, a.frozen_sha, a.clock_start_ms)
            print(canon({"n_bots": len(regs)}))
            for k, v in regs.items():
                print(canon({"bot_id": k, **{x: v[x] for x in ("frozen_sha", "clock_start_ms", "deciding_stake", "model_ids")}}))
            return 0
        live = a.mode == "run"
        if not live:
            if a.from_ms is None or a.to_ms is None:
                raise R.HostError("replay needs --from-ms and --to-ms")
            for p in (a.root, a.ledger_root, a.registry):
                if R._inside(p, R.ARENA_OUTPUT_ROOT):
                    raise R.HostError(f"replay refuses to write inside {R.ARENA_OUTPUT_ROOT} ({p}): a counted decision is never regenerated or replayed")
        state_start = None if live else a.from_ms - a.warm_min * R.MIN_MS
        state = ArenaState(a.tape_dir, start_ms=state_start, warm_min=a.warm_min)
        engine = ArenaEngine(a.ledger_root, a.registry, cfg=stack_config_for(a.stack_version, a.feed_delay_slots))
        cfg = R.HostConfig(root=a.root, registry=a.registry, frozen_sha=a.frozen_sha, clock_start_ms=a.clock_start_ms, live=live, verbose=True)
        host = HostV2(cfg, rules, state, engine, binding=R.head_binding() if live else None)
        if live:
            warm_start = int(time.time() * 1000) - a.warm_min * R.MIN_MS
            print(canon({"table_rebuilt_rows": rebuild_table(host.table, a.tape_dir, warm_start), "table_hwm_ms": host.table.hwm_ms}), flush=True)
        print(canon({"resumed": host.resume()}), flush=True)
        if live:
            start = max(a.clock_start_ms, int(time.time() * 1000))
            host.run(start - start % R.TICK_MS, a.end_ms or 2 ** 62)
        else:
            host.run(a.from_ms, a.to_ms)
        print(canon({"done": host.status()}), flush=True)
        return 0
    except (R.HostError, R.HostFatal, LogChainError) as e:
        print(f"arena_rules_v2: {type(e).__name__}: {str(e)[:300]}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
