#!/usr/bin/env python3
"""arena_screen: a reusable, fast, IN-SAMPLE screen pricer for a new PumpSwap meme-coin rule. PAPER ONLY. No key, no RPC, no wallet, no model.

    python -m tools.arena_screen --rule tools.arena_screen:TrivialRule --block /data/mal/clean-view/explore-0814 --out DIR
    python -m tools.arena_screen --rule pkg.mod:Cls --block <block> [--block ...] --out DIR --count-only      # triggers per day per cell, no price

A screen is a cheap kill test, not evidence. Everything it prints is exploration: it never says a book makes money (the promotion gate is in CLAUDE.md).

RULE API (a small Python object)
    name: str
    cells() -> list of Cell(name, cooldown_s=60.0) or plain names
    triggers(hour: HourRows, state: RunState) -> iterable of (pool, decision_slot, ExitSpec)
  ExitSpec(hold_s, tp, sl, trail, cell): a time exit after hold_s seconds (always; it bounds the hold), plus optional take-profit multiple tp (> 1), stop multiple sl
  (0 < sl < 1) and trailing multiple trail (0 < trail < 1, against the running peak, which starts at the decision spot), each on the POST-trade price of a print at slot
  >= decision slot + k_slow + 1 (the engine may not sell before the slower entry leg lands). `cell` names the cell a trigger belongs to (optional when the rule has one cell).
  EVENT EXIT (ExitSpec.exit_fire, optional): a rule-supplied PURE predicate (view) -> bool. The KIT DRIVES IT, hour by hour: for each hour h it first advances every open exit_fire position
  through h's slots, asking the predicate at every print slot of the pool in [landing_slot, cap_slot] on a view that holds only the rows with slot <= that slot (ExitView; the arrays
  end there; upto() refuses a later slot); the first True is the fire. Only then does it call the rule's `triggers(h)` for new entries. So at slot s of hour h the rule has seen triggers()
  for hours <= h-1 only, and a "no fire" is a series of honest per-slot False answers (nothing is returned that could have been chosen with hindsight). ENTRY HOUR: a position's own entry
  hour is run after triggers() of that hour, so for the rest of that hour the rule's state has seen the whole hour (as at every entry: the hour is handed over whole). That is closed by an
  ENTRY-HOUR RE-RUN inside `causal_probe(..., exit_fire=True)`, the author's seed-1 sample of >= 200 triggers per block (0 failures for registration, the standard of the entry probe, NOT
  every trigger; the harness must hand it the state SNAPSHOTTED BEFORE triggers(h) of the entry hour): it re-runs the rule's triggers() on the entry hour CUT after the entry decision slot, with a deep copy of the state as it was at the start of that hour, on a deep copy of the RULE object taken before the full-hour run (state kept on self cannot carry the full hour over; the rule must be deep-copyable), takes the ExitSpec
  that cut run returns for the same (pool, slot, cell) (its predicate was built only from rows <= the entry slot), streams the exit with THAT predicate, and requires the identical fire
  slot (or the identical no-fire) as the full-hour spec. A mismatch is a probe FAIL, like an entry look-ahead. The kit's own priced run does the prefix probe on EVERY event exit but cannot
  do the re-run (it holds no per-hour state snapshot). `landing_slot` = the kit floor sd + k_slow + 1 (the slower entry leg has landed), `cap_slot` the time cap's decision
  slot. The fire slot is the exit DECISION slot; the sell lands by the normal exit-lag rule from it (decision slot + 7 feed slots + the exit lag). The time cap and tp / sl / trail still
  apply, the earliest decides (a tp / sl / trail ON the fire slot keeps its own code; a fire on the cap's own slot is an event exit). Admission (one open position per cell and pool, cooldown after
  the exit) runs as the data arrive, so a cell is all exit_fire or none (a mix is refused, counted refused_exit_fire_mixed_cell). Candidate slots are slots with a print of the pool.
  PREFIX PROBE on EVERY event exit: the predicate must fire AGAIN on the rows <= the fire slot and must NOT fire at the previous slot the stream actually asked (the last print slot in
  [landing, fire)) on the rows <= that slot; when the fire is the first slot asked the early check is skipped. It is the purity check (the predicate is called again, so it must be stateless). It does NOT see reflection (`.base`, frames, gc, ctypes, `__dict__`): that is a SOURCE REVIEW of the rule
  module by the manager or QP before PHASE 2 GO, and the probe's views are copies only so that a naive `.base` read fails there. Registration needs >= 200 event
  exits probed per block, 0 failures; a failure KILLS the cell; a cell whose probes are not satisfied is PROVISIONAL-BLOCKED, never a survivor. `causal_probe(..., exit_fire=True)` is the
  author's single-hour sample. Declare the cell with Cell(event_exit=True): an event-exit cell is treated like an event-triggered one for the worst-in-slot kill even when its entry is
  grid-based (a cell that carries an exit_fire without declaring it is treated the same and reported undeclared). The per-cell report carries the event-exit share (results `exits.event`,
  `event_exit.fire_share`).
  --count-only IS A TIME-ONLY BOUND when an exit_fire is present: its overlap rule holds every position for the full time cap (the callable is not run, no price is read), so the figure
  is an upper bound on the hold. FOR AN exit_fire CELL THE TIME-ONLY FIGURE IS A LOWER BOUND ON ATTEMPTS (an event exit frees the pool earlier, so the real attempts are AT LEAST that
  many), AND THE RULES STEP-2 DROP MUST NOT USE IT: a cell it flags under 20 a day may well be over 20. Count with the rule's own structure scan and report the kit's figure beside it
  (the count-only result carries `attempts_bound_is_time_only` and `time_only_figure_is: "lower_bound_on_attempts"` for such a cell; the key `attempts_upper_bound` keeps its name).
  The rule gets ONE whole block hour at a time (columns sorted by pool, slot, tx, event) and may look one hour back through state.prev. A rule MUST decide only on rows
  with slot <= its decision slot (the hour is handed over whole for speed; the kit cannot see look-ahead, `causal_probe` re-runs a rule on a truncated hour to catch it).
  AM.2 UNIVERSE (DEC-031 Amendment 2: the rent refund holds only for it): WSOL quote AND V in [17.5e9, 17.7e9] lamports at decision time. The rule enforces it (hour.in_universe /
  hour.v per row); the kit REFUSES every trigger outside it and counts it: refused_non_wsol, refused_v_out_of_band, refused_v_unknown, refused_unknown_pool.
  Only WSOL-quoted PumpSwap rows are loaded (the others are counted as rows_non_wsol; a trigger on such a pool is refused as refused_non_wsol). Unknown V = NaN = outside.
  At most one open position per (cell, pool): a trigger before the previous exit decision + cell.cooldown_s is dropped and counted (overlap_dropped).

PRICING (the engine's own functions, imported, never copied: tools.arena_stack.mint_states / round_trip / book_fail_legs via tools.arena_paper)
  Decision at 1.9 s (1.3 s reported), stake 0.25 SOL deciding (1.0 SOL priced too, entry impact net_in / Qe at both stakes), feed delay 7 slots, E1 floor, 1.15 guard.
  Fail models: flat 15% and pressure at slope 1 with the intercept refit per cell, latency and strand rule (mean p over the cell's fills = 0.289, as stats.py did).
  Strand rule total DECIDES, sc is reported. Rent 0 DECIDES (Am.2: the sell closes the ATA; a stranded trip still pays 2,039,280), the rent-charged leg (2,039,280 per
  filled trip) is reported beside it. Decision slot = the rule's slot; the guard reference is the last print before it; slot seconds per UTC hour come from the hour's own
  prints. A fill lands on the END-bound state of the first print at slot >= landing + 1. Where no such print exists within GRACE_SLOTS (10,000) the state is derived from the
  last print, as the engine does; an attempt whose derived state would need slots past the end of its block is EXCLUDED and counted (block edge).
  BOOTSTRAP: BOTH bounds are computed and BOTH must be > 0 to survive: the trade-level one (tools.arena_stack.boot_lo, exactly as arena_score.cell_stats calls it) and the UTC-date
  clustered one (resample dates; 1,000 draws, seed 1, 5th percentile of the pooled mean SOL per trade).
SCOPE (what a kit figure is NOT): exits are resolved by slot arithmetic at each hour's own slot rate, whereas the arena bot holds in ms on a 1 s tick with the engine's rolling clock, so a
  kit figure is not the bot's; the frozen bot's md5 decision-equivalence replay is still required at registration. Late-print boundary: the engine derives a state once the GLOBAL
  high-water slot passes need + GRACE, the kit when the pool's own next print is at or after need + GRACE; on a tape that prints every slot the two agree except at the boundary slot
  itself (negligible). The kit counts the late-print trips per cell (late_print_derived).
KILL (hunt23 RULES.md Amendment 1; deciding leg 1.9 s / 0.25 SOL / rent 0): any of the FOUR sub-cells {flat, pressure} x {sc, total} with mean, total ex-top-3 or total
  ex-best-day <= 0; fewer than 3 blocks positive (a block is positive when its total is > 0 under ALL FOUR sub-cells); (b) mean <= 0 on explore-0814 ALONE or on the three September
  blocks POOLED (one period) under any sub-cell; (c) the in-sample gate pooled over all blocks on all four sub-cells: CI90 lower bound <= 0, or no majority of UTC dates positive.
  WORST-IN-SLOT (QP R7 ruling on #752/#747): on the SAME trips at the deciding cell the kit also prices the worst-in-slot leg and the "no guard rescue" companion (both from the
  imported tools.arena_report_legs functions) and takes the LOWER of the two legs' mean per sub-cell. An EVENT-triggered cell (Cell.event_triggered, the default) or an EVENT-EXIT
  cell (Cell.event_exit, or any cell that carries an ExitSpec.exit_fire) is KILLED when that lower mean is <= 0 in ANY of the four sub-cells; a time- or grid-triggered cell with a
  clock / price exit reports it only. If it cannot be measured (the tape states do not reproduce the kit's END states),
  nothing is printed for it and an event cell stays PROVISIONAL-BLOCKED.
  A cell that is not killed is a SCREEN SURVIVOR only: at most one survivor per rule group is picked by the rule author's
  declared statistic, and nothing here is a profit claim. `thin` (< --min-attempts-per-day, default 20) is reported beside it, not a kill.
  SCREEN P (EXP-028, REPORT ONLY; QP ruling on PR #756 comment 6276924696 item 4): a new key `screen_p` per cell (TABLE section 5), computed after the verdict and never read by it. For each
  of the four sub-cells at the deciding cell (1.9 s, 0.25 SOL, rent 0, END leg, pooled over the four blocks) it resamples the per-attempt SOL vector stat() hands to arena_stack.boot_lo, B draws
  (default 10,000, --screen-p-draws up to 1,000,000), seed 1, a fresh default_rng(1) per sub-cell: p_pct = (1 + #{m_b <= 0}) / (1 + B), p_null = (1 + #{m_b - xbar >= xbar}) / (1 + B),
  sub-cell p = their max, cell p = the max over the four; the date-cluster p is reported beside it with the same tails around the pooled ratio. It REFUSES any declared stake other than 0.25 SOL.
  SEAL (Amendment 1, 3): a pool younger than 360 s at the decision (counted from the pool's first print in the same block; state.first_seen holds EARLIER hours only) is REFUSED and counted
  (refused_pool_age_lt_360s). It is mandatory and not a parameter.

DATA GUARDS (before anything is read): block paths only under /data/mal/clean-view/{explore-0814, oracle-insample-2026-09-22_25, fast-pool-2026-09-18T23_2026-09-22T00,
  oracle-live-2026-09-25_27}; /data/mal/arena/tape closed hours (tape-<hour>.jsonl.zst) ONLY with --count-only (structure counts, never a price after a decision); every other path
  is refused; so is any path containing probe, shadow, canary, _shakedown, forward-, fresh-08, exp011, truth-, tip-tape or sealed (raw and resolved); --out must be outside
  /data/mal/arena and /data/mal/clean-view.
"""

from __future__ import annotations

if __package__ in (None, ""):  # `python tools/arena_screen.py`
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import ast
import collections
import copy
import hashlib
import dataclasses
import importlib
import importlib.util
import json
import math
import os
import re
import subprocess
import sys
import time
import zlib
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Sequence

import numpy as np

from tools import arena_paper as P
from tools import arena_stack as F
from tools.arena_score import MAX_EXCLUDED_SHARE, MIN_DAYS, MIN_N  # the gate's own constants (n >= 100, >= 5 UTC dates, 5% exclusion cap)
from tools.arena_report_legs import PARITY_RTOL, end_minus_worst, slot_states, state_arrays, worst_index  # QP-approved on #747 (79d051e, sha256 in REPORT_LEGS_PIN): IMPORTED, never copied

SCHEMA = "arena_screen_v1"
CLEAN_VIEW = "/data/mal/clean-view"
BLOCK_ROOTS = tuple(f"{CLEAN_VIEW}/{b}" for b in ("explore-0814", "oracle-insample-2026-09-22_25", "fast-pool-2026-09-18T23_2026-09-22T00", "oracle-live-2026-09-25_27"))
ARENA_ROOT = "/data/mal/arena"
ARENA_TAPE = f"{ARENA_ROOT}/tape"
FORBIDDEN = ("probe", "shadow", "canary", "_shakedown", "forward-", "fresh-08", "exp011", "truth-", "tip-tape", "sealed")
SEALED_HOURS = (datetime(2026, 10, 2, 10, tzinfo=timezone.utc), datetime(2026, 10, 16, 1, tzinfo=timezone.utc))  # D2/D3: no hour in [10-02T10, 10-16T01)
VMAP_DIR = "/data/mal/pumpswap-virtual"
VMAP_DEFAULT = (f"{VMAP_DIR}/pool_v_0814.json", f"{VMAP_DIR}/pool_v_0909.json")  # the explore map, then the wider fallback (hunt21's choice)
WSOL = "So11111111111111111111111111111111111111112"
V_LO, V_HI = 17_500_000_000, 17_700_000_000
D = 7  # feed_delay_slots, pinned like arena_rules.stack_config(7)
LATS = tuple(F.LATENCIES_S)  # (1.3, 1.9)
LAT_DECIDING = 1.9
STAKE_SOL = 0.25
STAKE_BIG = 1.0
RENT_DECIDING, RENT_CHARGED = F.RENT_DECIDING, F.RENT_REPORT
STRAND_RULES = ("total", "sc")
FAILS = ("flat", "press")
GRACE = P.GRACE_SLOTS
RETAIN_EXTRA_S = 5400.0  # a pool's rows are kept this long past the hold: GRACE_SLOTS at up to 0.54 s per slot, plus margin
MIN_HOLD_S = 3.0
REPO_ROOT = Path(__file__).resolve().parent.parent
MIN_POOL_AGE_S = 360  # Amendment 1, 3: EXP-024 Look-2 seal on the Am.2 V-range universe
SEPTEMBER_BLOCKS = ("oracle-insample-2026-09-22_25", "fast-pool-2026-09-18T23_2026-09-22T00", "oracle-live-2026-09-25_27")
SUB_CELLS = tuple((rule, fail) for rule in STRAND_RULES for fail in FAILS)  # the four deciding sub-cells (tools/arena_score.py)
GROUPS = ("explore-0814", "september")
CANONICAL_BLOCKS = ("explore-0814",) + SEPTEMBER_BLOCKS  # the four blocks: explore-0814 counts ONCE (its w1..w7 are not blocks), the three September blocks are named


def block_group(name: str) -> str | None:
    root = name.split("/")[0]
    return "explore-0814" if root == "explore-0814" else "september" if root in SEPTEMBER_BLOCKS else None


def canonical_block(name: str) -> str | None:
    root = name.split("/")[0]
    return root if root in CANONICAL_BLOCKS else None
EXIT_REASONS = ("time", "tp", "sl", "trail", "event")
EVENT_CODE = 4  # EXIT_REASONS index of an exit_fire exit (the price-path codes are 1..3)
EXIT_PROBE_MIN = 200  # exit-causality probe: fires probed per block needed for registration (0 failures)


class Refused(Exception):
    """A guard refused: nothing was read or written."""


# ------------------------------------------------------------------------------------------------------------------------------ rule API
@dataclass(frozen=True)
class ExitSpec:
    hold_s: float
    tp: float | None = None
    sl: float | None = None
    trail: float | None = None
    cell: str | None = None
    exit_fire: Callable[..., Any] | None = None  # event-driven exit: a pure predicate (ExitView) -> bool, asked at every print slot in [landing, cap] (see ExitView)

    def problem(self) -> str | None:
        return exit_problem(self)


def exit_problem(spec: Any) -> str | None:
    """Why an exit spec is unusable, or None. Duck-typed on purpose: a rule built from another import of this module (python -m) has its own ExitSpec class."""
    ok = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)  # noqa: E731
    try:
        hold, tp, sl, trail = spec.hold_s, getattr(spec, "tp", None), getattr(spec, "sl", None), getattr(spec, "trail", None)
    except AttributeError:
        return "not an ExitSpec"
    if not ok(hold) or hold < MIN_HOLD_S:
        return f"hold_s must be a number >= {MIN_HOLD_S}"
    if tp is not None and not (ok(tp) and tp > 1.0):
        return "tp must be a multiple > 1"
    if sl is not None and not (ok(sl) and 0.0 < sl < 1.0):
        return "sl must be a multiple in (0, 1)"
    if trail is not None and not (ok(trail) and 0.0 < trail < 1.0):
        return "trail must be a multiple in (0, 1)"
    fire = getattr(spec, "exit_fire", None)
    if fire is not None and not callable(fire):
        return "exit_fire must be a callable (view) -> bool"
    return None


@dataclass(frozen=True)
class Cell:
    """`event_triggered` is DECLARED per cell in DECLARE before any price (QP on #752): True for a cell that fires on a print, a wallet or any market event; False only for a cell that
    fires on a clock or a fixed grid. The default is True, so an undeclared cell is treated as the stricter kind: an event cell may not register until its worst-in-slot mean is
    measured and > 0 on all four sub-cells (R7).

    `event_exit` is DECLARED the same way, for the EXIT: True for a cell whose exit is decided by an ExitSpec.exit_fire (a tape event), whatever its entry is. Such a cell is treated like
    an event-triggered one for the worst-in-slot kill (the sell is placed on a market event, so the lower leg <= 0 kills it) even when its entry is grid-based. The kit also applies it to a
    cell that carries an exit_fire without declaring it (the stricter reading; reported as undeclared). Default False: a time / tp / sl / trail exit is a clock or a price rule."""

    name: str
    cooldown_s: float = 60.0
    event_triggered: bool = True
    event_exit: bool = False


@dataclass
class RunState:
    """Per block. `prev` is the previous hour of the same contiguous run (None after a gap and for the first hour); `scratch` is the rule's own memory."""

    block: str
    vmap: Any = None
    prev: "HourRows | None" = None
    gap_before: bool = False
    hour_index: int = 0
    scratch: dict = field(default_factory=dict)
    # pool -> block_time (s) of its first print in EARLIER hours of this block. Updated only AFTER an hour's triggers are validated, so it never carries this hour's rows (a rule that read it
    # during the hour would learn which pools are about to print: look-ahead). The kit adds the pool's own first print of the hour when it checks the age, which is causal.
    first_seen: dict = field(default_factory=dict)


# ------------------------------------------------------------------------------------------------------------------------------ guards
def _low(p: str) -> str:
    return p.lower()


def _forbidden_word(p: str) -> str | None:
    return next((w for w in FORBIDDEN if w in _low(p)), None)


def _under(p: str, root: str) -> bool:
    return p == root or p.startswith(root + "/")


def hour_of_name(name: str) -> datetime | None:
    m = re.search(r"(\d{4}-\d{2}-\d{2}T\d{2})\.jsonl\.zst$", name)
    return datetime.strptime(m.group(1), "%Y-%m-%dT%H").replace(tzinfo=timezone.utc) if m else None


def check_block_path(path: str | os.PathLike, count_only: bool) -> str:
    """Returns 'clean' (an exploration block) or 'arena' (the closed arena tape, count-only). Raises Refused."""
    raw = os.path.abspath(str(path))
    real = os.path.realpath(raw)
    for p in (str(path), raw, real):
        w = _forbidden_word(p)
        if w:
            raise Refused(f"path_forbidden_word:{w}: {path}")
    if _under(raw, ARENA_ROOT) or _under(real, ARENA_ROOT):
        if not (count_only and _under(raw, ARENA_TAPE) and _under(real, ARENA_TAPE)):
            raise Refused(f"arena_path_refused: only {ARENA_TAPE} closed hours, and only with --count-only: {path}")
        return "arena"
    if any(_under(raw, r) for r in BLOCK_ROOTS) and any(_under(real, r) for r in BLOCK_ROOTS):
        return "clean"
    raise Refused(f"not_an_exploration_block: {path} (allowed: {', '.join(BLOCK_ROOTS)})")


def check_out_dir(out: str | os.PathLike) -> str:
    raw = os.path.abspath(str(out))
    real = os.path.realpath(raw)
    for p in (raw, real):
        if _under(p, ARENA_ROOT) or _under(p, CLEAN_VIEW):
            raise Refused(f"out_dir_inside_protected_tree: {out}")
    return real


# DEC-031 section 3.1: a rule may not import or use the banned model families or their features. The tokens are spelled in pieces so that this file passes its own scan.
_BAN_TOKENS = ("r4" + "b", "la" + "ya", "l" + "3")
_BAN_PATHS = ("02_" + "build", "models/r4" + "b", "/data/mal/r4" + "b", "/data/mal/hunt-1008/la" + "ya-opt")
_BAN_TEXT = re.compile(r"(?i)(?<![a-z0-9])(?:" + "r4" + "b|la" + "ya|l" + "3" + r")(?![0-9a-z])|02_" + "build")


def banned_name(name: str) -> bool:
    """True if a module name, file name or path names a banned model family (token match on . _ - / separators, or a banned path fragment)."""
    low = name.lower()
    return any(t in _BAN_TOKENS or t.startswith(_BAN_TOKENS[0]) or t.startswith(_BAN_TOKENS[1]) for t in re.split(r"[._\-/\s]+", low)) or any(b in low for b in _BAN_PATHS)


def _import_names(src: str) -> list[str]:
    out = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            out += [node.module] + [f"{node.module}.{a.name}" for a in node.names]
    return out


def check_rule_source(path: str | os.PathLike, _seen: set | None = None) -> str:
    """Refuse a rule file whose TEXT mentions a banned model family, or whose IMPORT GRAPH (AST, followed through repo and sibling modules; the pinned tools.arena_* are not
    entered) names one. Returns the sha256 of the rule source."""
    seen = _seen if _seen is not None else set()
    p = Path(path).resolve()
    if p in seen:
        return ""
    seen.add(p)
    if banned_name(str(p)):
        raise Refused(f"rule_module_banned (DEC-031 3.1): file name {p}")
    raw = p.read_bytes()
    src = raw.decode("utf-8", "replace")
    m = _BAN_TEXT.search(src)
    if m:
        raise Refused(f"rule_module_banned (DEC-031 3.1): {p.name} mentions {m.group(0)!r} (comments and strings count)")
    for name in _import_names(src):
        if banned_name(name):
            raise Refused(f"rule_module_banned (DEC-031 3.1): {p.name} imports {name}")
        if name.startswith("tools.arena_") or name in ("tools", "numpy") or name.split(".")[0] in sys.stdlib_module_names:
            continue
        try:
            spec = importlib.util.find_spec(name)
        except (ImportError, ValueError, AttributeError):
            continue
        origin = getattr(spec, "origin", None)
        if origin and origin.endswith(".py") and (_under(origin, str(REPO_ROOT)) or _under(origin, str(p.parent))):
            check_rule_source(origin, seen)
    return hashlib.sha256(raw).hexdigest()


def check_loaded_modules(before: set) -> None:
    """After the rule is loaded (and again after the run): no module imported since `before` may be a banned family."""
    for n in sorted(set(sys.modules) - before):
        f = getattr(sys.modules.get(n), "__file__", None) or ""
        if banned_name(n) or (f and banned_name(f)):
            raise Refused(f"rule_module_banned (DEC-031 3.1): the run imported {n} {f}")


def rule_source_path(spec: str) -> Path:
    mod = spec.rpartition(":")[0]
    if not mod:
        raise Refused("--rule must be module:Factory or file.py:Factory")
    if mod.endswith(".py"):
        return Path(mod)
    fs = importlib.util.find_spec(mod)
    if fs is None or not fs.origin or not fs.origin.endswith(".py"):
        raise Refused(f"rule module has no python source: {mod}")
    return Path(fs.origin)


def check_vmap_paths(paths: Sequence[str]) -> list[str]:
    """V maps: forbidden words refused (raw and resolved) and the file must sit under /data/mal/pumpswap-virtual. Returns the resolved paths."""
    root = os.path.realpath(VMAP_DIR)
    out = []
    for p in paths:
        real = os.path.realpath(p)
        for q in (str(p), os.path.abspath(str(p)), real):
            w = _forbidden_word(q)
            if w:
                raise Refused(f"vmap_forbidden_word:{w}: {p}")
        if not (_under(os.path.abspath(str(p)), root) and _under(real, root)):
            raise Refused(f"vmap_outside_{VMAP_DIR}: {p}")
        out.append(real)
    return out


def file_sha256(path: str | os.PathLike) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def kit_provenance() -> dict:
    kit = Path(__file__).resolve()
    out: dict[str, Any] = {"path": str(kit), "sha256": file_sha256(kit), "git_head": None, "git_dirty": None,
                           "report_legs_sha256": file_sha256(kit.parent / "arena_report_legs.py")}  # the imported worst-in-slot functions (R7); REPORT_LEGS_PIN is the QP-approved one
    try:
        out["git_head"] = subprocess.run(["git", "-C", str(kit.parent), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=20, check=True).stdout.strip()
        out["git_dirty"] = bool(subprocess.run(["git", "-C", str(kit.parent), "status", "--porcelain", "--untracked-files=no"], capture_output=True, text=True, timeout=20, check=True).stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return out


def discover(path: str | os.PathLike, kind: str, hours: Sequence[str] | None = None) -> list[tuple[datetime, str]]:
    """The hour files of one block path (a block root, a worker dir, a trades dir, or for the arena tape a dir or one closed-hour file), sorted by hour."""
    p = Path(path)
    bp = os.path.realpath(p)
    root = next((os.path.realpath(r) for r in BLOCK_ROOTS if _under(bp, os.path.realpath(r))), None)
    if kind == "arena":
        fs = [p] if p.is_file() else sorted(p.glob("tape-*.jsonl.zst"))
        out = []
        for f in fs:
            if re.fullmatch(r"tape-\d{4}-\d{2}-\d{2}T\d{2}\.jsonl\.zst", f.name) is None or f.parent.resolve() != Path(ARENA_TAPE) or os.path.realpath(f) != f"{ARENA_TAPE}/{f.name}":
                raise Refused(f"arena_file_refused: {f} (only tape-<hour>.jsonl.zst directly in {ARENA_TAPE})")
            if f.with_suffix("").exists() or Path(str(f) + ".tmp").exists():
                raise Refused(f"arena_hour_not_closed: {f}")
            out.append((hour_of_name(f.name), str(f)))
    else:
        fs = [p] if p.is_file() else sorted(set(p.glob("trades/trades-*.jsonl.zst")) | set(p.glob("w*/trades/trades-*.jsonl.zst")) | set(p.glob("trades-*.jsonl.zst")))
        out = []
        for f in fs:
            if hour_of_name(f.name) is None:
                raise Refused(f"unexpected_file_name: {f}")
            if _forbidden_word(str(f)) or _forbidden_word(os.path.realpath(f)):
                raise Refused(f"path_forbidden_word: {f}")
            if not (root and _under(os.path.realpath(f), root) and _under(os.path.realpath(f), bp)):
                raise Refused(f"hour_file_outside_block_root: {f} resolves to {os.path.realpath(f)} (block root {root})")
            out.append((hour_of_name(f.name), str(f)))
    for t, f in out:
        if SEALED_HOURS[0] <= t < SEALED_HOURS[1]:
            raise Refused(f"sealed_hour (D2/D3 [2026-10-02T10, 2026-10-16T01)): {f}")
    if hours:
        want = set(hours)
        out = [(t, f) for t, f in out if t.strftime("%Y-%m-%dT%H") in want]
    out.sort(key=lambda x: x[0])
    seen = [t for t, _ in out]
    if len(set(seen)) != len(seen):
        raise Refused(f"duplicate_hour_in_block: {path}")
    if not out:
        raise Refused(f"no_hour_files: {path}")
    return out


# ------------------------------------------------------------------------------------------------------------------------------ V map and hours
class VMap:
    """pool -> V lamports (the trade rows carry none): the first map that has a non-null value wins. Maps are dicts or JSON files {'v': {pool: V|null}}."""

    def __init__(self, maps: Sequence[Any]) -> None:
        self.maps = [json.load(open(m))["v"] if isinstance(m, (str, os.PathLike)) else m for m in maps]

    def get(self, pool: str) -> float | None:
        for m in self.maps:
            v = m.get(pool)
            if v is not None:
                return float(v)
        return None


@dataclass
class HourRows:
    """One block hour, rows sorted by (pool, slot, tx, event). CSR: pool i owns rows ptr[i]:ptr[i+1]. WSOL-quoted PumpSwap rows only (others are counted)."""

    block: str
    hour: str
    T: int  # hour start, epoch s
    idx: int
    pools: np.ndarray
    mints: np.ndarray
    ptr: np.ndarray
    sl: np.ndarray
    tx: np.ndarray
    ev: np.ndarray
    isbuy: np.ndarray
    sol: np.ndarray
    tok: np.ndarray
    q: np.ndarray  # quote_reserve, PRE-trade, V not included
    b: np.ndarray
    bt: np.ndarray
    trader: np.ndarray  # crc32 of the trader
    v: np.ndarray  # V per row (lamports; NaN = unknown)
    slots_per_s: float
    n_pumpswap: int = 0
    n_nonwsol: int = 0
    nonwsol_pools: frozenset = frozenset()
    n_no_ts: int = 0  # rows refused for a missing timestamp
    _index: dict | None = None

    @property
    def n(self) -> int:
        return len(self.sl)

    @property
    def min_slot(self) -> int:
        return int(self.sl.min()) if self.n else -1

    @property
    def max_slot(self) -> int:
        return int(self.sl.max()) if self.n else -1

    def pool_index(self) -> dict:
        if self._index is None:
            self._index = {nm: i for i, nm in enumerate(self.pools.tolist())}
        return self._index

    @property
    def in_universe(self) -> np.ndarray:
        """Per pool: V of the pool's LAST row in this hour is in [17.5e9, 17.7e9] (WSOL is given: other quotes are not loaded). A rule needing it at an earlier print uses self.v."""
        last = np.maximum(self.ptr[1:] - 1, 0)
        v = self.v[last] if self.n else np.zeros(0)
        return (v >= V_LO) & (v <= V_HI)

    def rows(self, i: int) -> slice:
        return slice(int(self.ptr[i]), int(self.ptr[i + 1]))


def _hour_lines(path: str) -> Iterator[bytes]:
    z = subprocess.Popen(["zstd", "-dc", "-q", path], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
    g = subprocess.Popen(["grep", "-F", "-e", "pumpswap"], stdin=z.stdout, stdout=subprocess.PIPE)
    assert z.stdout is not None and g.stdout is not None
    z.stdout.close()
    try:
        yield from g.stdout
    finally:
        g.stdout.close()
        grc, zrc = g.wait(), z.wait()
    if zrc != 0 or grc not in (0, 1):
        raise RuntimeError(f"zstd/grep failed (zstd {zrc}, grep {grc}) on {path}")


def build_hour(lines: Iterable[bytes | str], *, block: str, T: int, idx: int, fmt: str, vmap: VMap | None) -> HourRows:
    """Parse trade rows into columns. fmt 'backfill' (exploration views: tx_index, block_time, V from the map) or 'arena' (the live tape: t_recv_ms order, V on the row)."""
    loads = json.loads
    pid_of: dict[str, int] = {}
    names: list[str] = []
    mints: list[str] = []
    vpool: dict[str, float] = {}
    nonwsol: set = set()
    c = {k: [] for k in ("pid", "sl", "tx", "ev", "ib", "so", "tk", "q", "b", "bt", "tr", "v")}
    n_ps = n_non = n_no_ts = 0
    arena = fmt == "arena"
    for line in lines:
        if not arena and b'"quote_is_wsol":false' in line and b'"venue":"pumpswap"' in line:  # the 58% non-WSOL rows: counted and their pool noted, never parsed
            k = line.find(b'"pool":"')
            if k >= 0:
                n_ps += 1
                n_non += 1
                nonwsol.add(line[k + 8:line.find(b'"', k + 8)].decode())
                continue
        r = loads(line)
        if arena and r.get("type", "trade") != "trade":
            continue
        if (r.get("venue") or r.get("prog")) != "pumpswap":
            continue
        pool = r.get("pool")
        if not pool or r.get("slot") is None or r.get("side") not in ("buy", "sell"):
            continue
        n_ps += 1
        wsol = (r.get("quote_mint") == WSOL) if arena else bool(r.get("quote_is_wsol"))
        if not wsol:
            n_non += 1
            nonwsol.add(pool)
            continue
        bt_ = r.get("event_ts") if arena else (r.get("block_time") or r.get("event_ts"))
        if not isinstance(bt_, (int, float)) or bt_ <= 0 or (arena and not r.get("t_recv_ms")):  # a missing timestamp REFUSES the row (counted); it is never stored as 0
            n_no_ts += 1
            continue
        i = pid_of.get(pool)
        if i is None:
            i = pid_of[pool] = len(names)
            names.append(pool)
            mints.append(r.get("mint") or "")
            if not arena:
                v0 = vmap.get(pool) if vmap is not None else None
                vpool[pool] = float("nan") if v0 is None else v0
        c["pid"].append(i)
        c["sl"].append(r["slot"])
        if arena:
            c["tx"].append(int(r["t_recv_ms"]) % 2_000_000_000)
            vv = r.get("virtual_quote_reserves")
            c["v"].append(float("nan") if vv is None else float(vv))
            c["bt"].append(int(bt_))
        else:
            c["tx"].append(r.get("tx_index") or 0)
            c["v"].append(vpool[pool])
            c["bt"].append(int(bt_))
        c["ev"].append(r.get("event_index") or 0)
        c["ib"].append(r["side"] == "buy")
        c["so"].append(r["sol_lamports"])
        c["tk"].append(r["token_raw"])
        c["q"].append(r["quote_reserve"])
        c["b"].append(r["base_reserve"])
        c["tr"].append(zlib.crc32((r.get("trader") or "").encode()))
    pid = np.array(c["pid"], np.int32)
    sl = np.array(c["sl"], np.int64)
    tx = np.array(c["tx"], np.int64)
    ev = np.array(c["ev"], np.int64)
    o = np.lexsort((ev, tx, sl, pid))
    bt = np.array(c["bt"], np.int64)[o]
    npool = len(names)
    ptr = np.searchsorted(pid[o], np.arange(npool + 1), "left").astype(np.int64)
    sl = sl[o]
    sps = float((sl.max() - sl.min()) / max(1, int(bt.max()) - int(bt.min()))) if len(sl) else 0.0
    ib = np.array(c["ib"], bool)[o]
    return HourRows(block, datetime.fromtimestamp(T, tz=timezone.utc).strftime("%Y-%m-%dT%H"), T, idx, np.array(names, dtype=str), np.array(mints, dtype=str), ptr, sl, tx[o], ev[o],
                    ib, np.array(c["so"], np.float64)[o], np.array(c["tk"], np.float64)[o], np.array(c["q"], np.float64)[o], np.array(c["b"], np.float64)[o], bt,
                    np.array(c["tr"], np.int64)[o], np.array(c["v"], np.float64)[o], sps, n_ps, n_non, frozenset(nonwsol), n_no_ts)


_WORKER: dict[str, Any] = {}


def _load_job(job: tuple) -> HourRows:
    path, block, T, idx, fmt = job
    return build_hour(_hour_lines(path), block=block, T=T, idx=idx, fmt=fmt, vmap=_WORKER.get("vmap"))


def iter_hours(files: Sequence[tuple[datetime, str]], block: str, fmt: str, vmap: VMap | None, workers: int = 1) -> Iterator[HourRows]:
    """Hours in order. workers > 1 loads the next hours in forked processes (a bounded window); memory is ~100 MB per hour in flight."""
    _WORKER["vmap"] = vmap
    jobs = [(f, block, int(t.timestamp()), i, fmt) for i, (t, f) in enumerate(files)]
    if workers <= 1:
        for j in jobs:
            yield _load_job(j)
        return
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs: collections.deque = collections.deque()
        it = iter(jobs)
        for j in it:
            futs.append(ex.submit(_load_job, j))
            if len(futs) > workers:
                break
        while futs:
            yield futs.popleft().result()
            nxt = next(it, None)
            if nxt is not None:
                futs.append(ex.submit(_load_job, nxt))


# ------------------------------------------------------------------------------------------------------------------------------ pricing
def screen_cfg(rent: int = RENT_DECIDING, send: int = 505_000) -> P.StackConfig:
    """The deciding stack: arena_rules.stack_config(7) with `rent` (0 deciding, 2,039,280 for the rent-charged leg). Pinned equal in the tests.
    `send` defaults to 505,000 (every registered book); 160_000 is the DEC-031 Amendment 8 stack (arena_rules.stack_config_v2) and must be passed explicitly."""
    return P.StackConfig(send=send, rent=rent, h_in=0.002608, h_out=0.0016, k_rounding="ceil", stakes_sol=(0.25, 0.5, 1.0), grace_slots=10_000, portal_ppm=0,
                         press_intercept=-1.4197242838379789, strand_rule="both", e1_floor_per_005=384_022, feed_delay_slots=D, gap_slots=150, gap_silence_ms=60_000, max_future_ms=5_000)


def legs(st: dict, stake: float, lat: float, rule: str, cfg: P.StackConfig, intercept: float | None = None) -> dict:
    """Vectorised price_leg over trips. st: ref, Qe[lat], Be[lat], ssb[lat], nearby[lat], Qx, Bx arrays. Equal to arena_paper.price_leg trip by trip (tested to 0 lamports).
    intercept None = refit on these trips (the screen); a number = the provisional one the engine writes into its ledger rows."""
    n = len(st["ref"])
    sk = np.full(n, float(stake))
    total = rule == "total"
    kw = dict(return_stranded=True, return_invalid=True, return_exit_invalid=True, auto_stranded=total, k_tol=F.K_TOL if total else None)
    args = (sk, st["Qe"][lat], st["Be"][lat], st["ref"], st["Qx"], st["Bx"], cfg.send, cfg.rent)
    pnl, filled, strand, bad, xbad = F.round_trip(*args, cfg.h_in, cfg.h_out, **kw)
    pnl0 = F.round_trip(*args, 0.0, 0.0, **kw)[0]
    isfilled = filled & ~bad & ~xbad & ~strand
    extra = np.where(isfilled, np.maximum(0.0, cfg.e1_floor_per_005 * sk / 50_000_000.0 - (pnl0 - pnl)), 0.0)
    pnl = pnl - extra
    strandf = total & filled & (bad | xbad | strand)
    flat, press, c, p = P.book_fail_legs(pnl, filled, strandf, st["ssb"][lat], st["nearby"][lat], cfg.send, intercept=intercept)
    return {"pnl": pnl, "flat": flat, "press": press, "filled": filled, "strand": strandf, "c": c}


def post_price(a: dict) -> np.ndarray:
    """Post-trade price of every print (mint_states' own convention; tested equal to its spot)."""
    qp = np.where(a["ib"], a["q"] + a["so"] * 0.99, a["q"] - a["so"] * 1.01) + a["v"]
    bp = np.where(a["ib"], a["b"] - a["tk"], a["b"] + a["tk"])
    return qp / np.where(bp > 0, bp, a["b"])


def _exit_clock(spec: ExitSpec, sd: int, rate: float, sps: float) -> tuple[int, int, bool]:
    """(floor_slot, cap_slot, clamped): the earliest slot an exit may be DECIDED (the slower entry leg has landed: sd + k_slow + 1) and the time cap's decision slot (never before the floor)."""
    floor_slot = sd + int(F.k_slots_vec(LAT_DECIDING, sps, "ceil")) + 1
    xt = sd + int(np.rint(spec.hold_s * rate))
    return floor_slot, max(xt, floor_slot), xt < floor_slot


def _price_exit(a: dict, pp: np.ndarray, sd: int, spec: ExitSpec, rate: float, sps: float) -> tuple[int, int, bool]:
    """The time cap and the tp / sl / trail path rules (the kit's original resolver, unchanged): (exit decision slot, reason code 0..3, clamped)."""
    sl = a["sl"]
    floor_slot, xs, clamped = _exit_clock(spec, sd, rate, sps)
    why = 0
    if spec.tp is None and spec.sl is None and spec.trail is None:
        return xs, why, clamped
    jd = int(np.searchsorted(sl, sd, "left"))
    j0, j1 = int(np.searchsorted(sl, floor_slot, "left")), int(np.searchsorted(sl, xs, "right"))
    if j1 <= j0:
        return xs, why, clamped
    spot0 = pp[max(jd - 1, 0)]
    seg = pp[j0:j1]
    best = None
    for code, hit in ((1, None if spec.tp is None else seg >= spec.tp * spot0), (2, None if spec.sl is None else seg <= spec.sl * spot0),
                      (3, None if spec.trail is None else seg <= spec.trail * np.maximum(spot0, np.maximum.accumulate(pp[jd:j1]))[j0 - jd:])):
        if hit is None:
            continue
        m = np.flatnonzero(hit)
        if m.size and (best is None or m[0] < best[0]):
            best = (int(m[0]), code)
    if best is not None:
        xs, why = int(sl[j0 + best[0]]), best[1]
    return xs, why, clamped


class ExitLookAhead(Refused):
    """An exit_fire asked for rows after the slot it is being asked about. The run is refused; nothing it priced is a result."""


class ExitFireError(Refused):
    """An exit_fire returned something that is not a bool."""


_VIEW_COLS = ("sl", "ib", "so", "tk", "q", "b", "v")


def _cut_rows(a: dict, pp: np.ndarray, n: int, copy: bool = False) -> dict:
    """The first n rows of the pool arrays as READ-ONLY slices (the view's whole world: nothing after row n is in the dict). copy=True gives COPIES (`.base` is None): the prefix probe's views."""
    out = {k: (np.array(a[k][:n]) if copy else a[k][:n]) for k in _VIEW_COLS}
    out["pp"] = np.array(pp[:n]) if copy else pp[:n]
    for v in out.values():
        v.flags.writeable = False
    return out


class ExitView:
    """What an event exit's predicate `exit_fire(view) -> bool` sees: ONE pool, rows with slot <= `slot` and nothing else. The kit calls the predicate once per print slot of the pool in
    [landing_slot, cap_slot], in order, on a view cut at that slot; the first True is the fire (the exit DECISION slot). Hour by hour the kit advances every open exit_fire position through
    the hour's slots BEFORE it calls the rule's `triggers` for that hour, so when the predicate runs at slot s of hour h the rule has seen `triggers()` for hours <= h-1 only (for a position's
    own entry hour: see the module doc).
      view.slot          the slot being asked about (a slot with at least one print of the pool)
      view.rows()        dict of read-only arrays (sl, ib, so, tk, q, b, v, pp) of every print with slot <= view.slot, history before the entry included
      view.upto(slot)    the same for a slot <= view.slot; a later slot raises ExitLookAhead
      view.sd, view.landing_slot, view.cap_slot, view.rate, view.sps    the entry decision slot, the kit floor sd + k_slow + 1, the time cap's decision slot, slots per second, seconds per slot
    The arrays are slices that end at `slot`. REFLECTION IS NOT DETECTABLE BY ANY PROBE: `.base` of a slice (the whole hour's array, at a flush the whole store), frame inspection
    (`inspect`, `sys._getframe`), `gc`, `ctypes`, `__dict__` / `__slots__` access to the view, or any attribute that reaches the full arrays reads rows after the slot, and the prefix probe,
    the entry-hour re-run and the byte checks all stream the same function over the same arrays. The prefix probe builds its views from COPIES, so a naive `.base` read gets None there and
    raises (a failure), but a reader with a fallback is not caught. THE CONTROL IS A SOURCE REVIEW of the rule module by the manager or QP before PHASE 2 GO (grep `.base`, `inspect`,
    `_getframe`, `gc`, `ctypes`, `__dict__`, `_a`, `__slots__`, `globals`, `sys.modules` in the rule and in everything it imports).
    A predicate must be a PURE function of the view (and constants fixed at the entry): the probe calls it again."""

    __slots__ = ("_a", "slot", "sd", "landing_slot", "cap_slot", "rate", "sps")

    def __init__(self, rows: dict, slot: int, sd: int, landing_slot: int, cap_slot: int, rate: float, sps: float) -> None:
        self._a = rows
        self.slot, self.sd, self.landing_slot, self.cap_slot, self.rate, self.sps = int(slot), int(sd), int(landing_slot), int(cap_slot), float(rate), float(sps)

    def rows(self) -> dict:
        return dict(self._a)

    def upto(self, slot: int) -> dict:
        slot = int(slot)
        if slot > self.slot:
            raise ExitLookAhead(f"exit_fire asked for rows up to slot {slot}, after the slot it is being asked about ({self.slot})")
        m = int(np.searchsorted(self._a["sl"], slot, "right"))
        return {k: v[:m] for k, v in self._a.items()}


def _fire_bool(r: Any) -> bool:
    if isinstance(r, (bool, np.bool_)):
        return bool(r)
    raise ExitFireError(f"exit_fire must return a bool (fire now or not yet), not {r!r}")


class _Pos:
    """One exit_fire position: the kit drives its predicate through the pool's print slots, as the data arrive. `done` = the last slot the predicate was asked about."""

    def __init__(self, spec: Any, sd: int, rate: float, sps: float, ci: int = -1, pool: str = "", hi: int = -1) -> None:
        self.spec, self.fire, self.sd, self.rate, self.sps, self.ci, self.pool, self.hi = spec, spec.exit_fire, int(sd), float(rate), float(sps), ci, pool, hi
        self.landing, self.cap, self.clamped = _exit_clock(spec, self.sd, rate, sps)
        self.done, self.xs, self.why, self.asks = -1, None, 0, 0  # asks = how many times the predicate was called (the timing figure)

    def view(self, rows: dict, slot: int) -> ExitView:
        return ExitView(rows, slot, self.sd, self.landing, self.cap, self.rate, self.sps)

    def run(self, a: dict, pp: np.ndarray, covered_to: int, cut: dict | None = None) -> bool:
        """Ask the predicate at every print slot of `a` (rows up to the data so far; `covered_to` = the last slot the data cover) after `done`, from the landing floor to the cap. The
        price rules (tp / sl / trail) are resolved on the data so far and stop the stream, as does the cap. True once the exit is decided (xs, why set)."""
        spec, sl = self.spec, a["sl"]
        xs_p, why_p = self.cap, 0
        if spec.tp is not None or spec.sl is not None or spec.trail is not None:
            xs_p, why_p, _ = _price_exit(a, pp, self.sd, spec, self.rate, self.sps)
        last = min(xs_p - 1, self.cap) if why_p else self.cap  # a price exit on the very slot of a fire keeps its own code, so the fire is asked about strictly before it
        j0, j1 = int(np.searchsorted(sl, max(self.landing, self.done + 1), "left")), int(np.searchsorted(sl, last, "right"))
        fired = None
        if j1 > j0:
            slots = np.unique(sl[j0:j1])
            ns = np.searchsorted(sl, slots, "right")
            for s, n in zip(slots.tolist(), ns.tolist()):
                rows = cut.get(n) if cut is not None else None
                if rows is None:
                    rows = _cut_rows(a, pp, n)
                    if cut is not None:
                        cut[n] = rows
                self.done = s
                self.asks += 1
                if _fire_bool(self.fire(self.view(rows, s))):
                    fired = s
                    break
        if fired is not None:
            self.xs, self.why = fired, EVENT_CODE
        elif why_p:
            self.xs, self.why = xs_p, why_p
        elif self.cap <= covered_to:
            self.xs, self.why = self.cap, 0
        return self.xs is not None


def exit_fire_probe(pos: _Pos, a: dict, pp: np.ndarray, fire_slot: int) -> bool:
    """Prefix probe on one event exit (QP on #760, as corrected at 7f5f3d6): the predicate must fire AGAIN on the rows <= the fire slot (a pure function of the view), and must NOT fire at
    the PREVIOUS SLOT THE STREAM ACTUALLY ASKED (the last print slot in [landing, fire)) on the rows <= that slot. When the fire is the first slot the stream asked there is no such slot and
    the early check is skipped: a condition that already holds at the first print slot (a buy seen since entry, a truncated window already full) is an honest fire, not a defect. For a
    pure predicate the early check is a re-ask, which is the purity check; it does not detect foresight written as a function of the slot (`view.slot >= P`). A raise is a failure. The
    views are built from COPIES of the rows (a naive `.base` read gets None and raises; a reader with a fallback is not caught: see the ExitView doc)."""
    sl = a["sl"]
    f = int(fire_slot)
    asked = sl[(sl >= pos.landing) & (sl < f)]
    try:
        again = _fire_bool(pos.fire(pos.view(_cut_rows(a, pp, int(np.searchsorted(sl, f, "right")), copy=True), f)))
        early = False
        if asked.size:
            prev = int(asked[-1])
            early = _fire_bool(pos.fire(pos.view(_cut_rows(a, pp, int(np.searchsorted(sl, prev, "right")), copy=True), prev)))
    except Exception:  # noqa: BLE001
        return False
    return again and not early


def resolve_exit(a: dict, pp: np.ndarray, sd: int, spec: ExitSpec, rate: float, sps: float) -> tuple[int, int, bool]:
    """(exit decision slot, reason code, clamped). Time exit at sd + round(hold_s x slots/s); tp / sl / trail on the first eligible print with slot in
    [sd + k_slow + 1, time exit], whichever comes first. With spec.exit_fire the predicate is asked at every print slot in [landing, cap] on a view cut at that slot (the batch form of the
    streaming the screen does hour by hour; the two are tested equal); the earliest of the price-path exit, the first fire and the time cap decides. Ties: a tp / sl / trail ON the fire
    slot keeps its own code (same slot, same price); a fire on the time cap's own slot is an event exit. The sell then lands by the normal exit-lag rule from the exit DECISION slot."""
    if getattr(spec, "exit_fire", None) is None:
        return _price_exit(a, pp, sd, spec, rate, sps)
    pos = _Pos(spec, sd, rate, sps)
    pos.run(a, pp, 2**62)
    return int(pos.xs), pos.why, pos.clamped


def pool_states(a: dict, sd: np.ndarray, sps_d: np.ndarray, xs: np.ndarray, sps_x: np.ndarray) -> dict:
    args = (a["sl"], a["ib"], a["so"], a["tk"], a["q"], a["b"], a["v"])
    ms = F.mint_states(*args, sd + D, sps_d, t_marks=np.zeros(len(sd)), slot_s_marks=sps_x, clock_slot_marks=xs + D, lats=LATS, rounding="ceil")
    ref = F.mint_states(*args, sd, sps_d, lats=(LATS[0],), rounding="ceil")["spot"]
    return {"ref": ref, "Qe": ms["Qe"], "Be": ms["Be"], "ssb": ms["ssb"], "nearby": ms["nearby"], "Qx": ms["Qx"], "Bx": ms["Bx"]}


def _take(st: dict, m) -> dict:
    return {k: ({l: x[m] for l, x in v.items()} if isinstance(v, dict) else v[m]) for k, v in st.items()}


def _cat(parts: list[dict]) -> dict:
    return {k: ({l: np.concatenate([p[k][l] for p in parts]) for l in parts[0][k]} if isinstance(parts[0][k], dict) else np.concatenate([p[k] for p in parts])) for k in parts[0]}


# ------------------------------------------------------------------------------------------------------------------------------ statistics
def boot_lo(dsum: np.ndarray, dn: np.ndarray, draws: int = F.BOOT_DRAWS, seed: int = F.BOOT_SEED) -> float:
    """Date-clustered: resample UTC days with replacement; statistic = pooled mean SOL per trade; 5th percentile (stats.py of hunt21, same rng stream)."""
    k = len(dsum)
    if k == 0:
        return float("nan")
    rng = np.random.default_rng(seed)
    r = np.empty(draws)
    for i in range(draws):
        ix = rng.integers(0, k, k)
        r[i] = dsum[ix].sum() / max(1, dn[ix].sum())
    return float(np.percentile(r, F.BOOT_PCT))


def stat(lam: np.ndarray, day: np.ndarray, blk: np.ndarray, blocks: Sequence[str], stake_sol: float = STAKE_SOL) -> dict | None:
    x = np.asarray(lam, float) / 1e9
    if len(x) == 0:
        return None
    days, inv = np.unique(day, return_inverse=True)
    dsum, dn = np.bincount(inv, weights=x), np.bincount(inv).astype(float)
    tot, best = float(x.sum()), float(dsum.max())
    top3 = float(np.sort(x)[::-1][:3].sum())
    lo = boot_lo(dsum, dn)
    lo_t = F.boot_lo(x)  # the gate's own bound: trade-level bootstrap exactly as arena_score.cell_stats calls it (x in SOL)
    pb = {}
    for bi, nm in enumerate(blocks):
        m = blk == bi
        pb[nm] = {"n": int(m.sum()), "total": float(x[m].sum()), "mean_pct": float(x[m].mean() / stake_sol * 100) if m.any() else None}
    return {"n": int(len(x)), "days": int(len(days)), "pos_days": int((dsum > 0).sum()), "mean": float(x.mean()), "mean_pct": float(x.mean() / stake_sol * 100), "ci_lo": lo,
            "ci_lo_pct": lo / stake_sol * 100, "ci_lo_trade": lo_t, "ci_lo_trade_pct": lo_t / stake_sol * 100, "total": tot, "ex_top3": tot - top3, "ex_best_day": tot - best, "top_day_share": (best / tot if tot > 0 else None), "per_block": pb}


def gate_row(s: dict | None) -> dict | None:
    """The pooled in-sample gate columns of one sub-cell: n, UTC dates, majority of dates, BOTH CI90 lower bounds (trade-level = arena_score's; UTC-date clustered = the stricter
    screen of hunt21), ex-top-3, ex-best-day. `trade_gate_pass` is arena_score.cell_verdict's own test (n >= 100, >= 5 dates, majority, trade CI90 > 0, ex-top-3 > 0)."""
    if s is None:
        return None
    maj = bool(2 * s["pos_days"] > s["days"])
    return {"n": s["n"], "mean_pct": s["mean_pct"], "ci_lo_pct": s["ci_lo_pct"], "ci_lo_gt0": bool(s["ci_lo"] > 0), "ci_lo_trade_pct": s["ci_lo_trade_pct"], "ci_lo_trade_gt0": bool(s["ci_lo_trade"] > 0),
            "days": s["days"], "pos_days": s["pos_days"], "majority_dates": maj, "ex_top3": s["ex_top3"], "ex_best_day": s["ex_best_day"],
            "n_ge_min": bool(s["n"] >= MIN_N), "days_ge_min": bool(s["days"] >= MIN_DAYS),
            "trade_gate_pass": bool(s["n"] >= MIN_N and s["days"] >= MIN_DAYS and maj and s["ci_lo_trade"] > 0 and s["ex_top3"] > 0)}


def kill_verdict(subs: dict, blocks: Sequence[str], groups: dict | None = None, *, excl_share: float | None = None, explore_att_per_day: float | None = None,
                 min_att_per_day: float = 0.0, min_blocks_positive: int = 3) -> tuple[bool, list[str]]:
    """Amendment 1 plus the gate's own tests (QP on #752). subs: {(strand rule, fail): stat} at 1.9 s / 0.25 SOL / rent 0, pooled over all blocks; groups: {(group, rule, fail):
    stat | None} for explore-0814 alone and the September blocks pooled. Any of these kills: a sub-cell with no trips; mean, ex-top-3 or ex-best-day <= 0; n < 100 or < 5 UTC dates
    (arena_score MIN_N, MIN_DAYS); the trade-level OR the date-clustered CI90 lower bound <= 0; no majority of dates positive; fewer than 3 of the FOUR canonical blocks positive in
    all four sub-cells; a group mean <= 0 or a group with no trips; the cell's block-edge exclusions above 5% of its attempts (not decidable in-sample); explore-0814 attempts per
    day below the RULES step-2 count. Returns (kill, reasons)."""
    why = []
    for key in SUB_CELLS:
        s, tag = subs.get(key), "/".join(key)
        if s is None:
            why.append(f"{tag}: no trips")
            continue
        for k in ("mean", "ex_top3", "ex_best_day"):
            if not s[k] > 0:
                why.append(f"{tag}.{k}<=0")
        g = gate_row(s)
        if not g["n_ge_min"]:
            why.append(f"{tag}.gate.n {s['n']} < {MIN_N}")
        if not g["days_ge_min"]:
            why.append(f"{tag}.gate.days {s['days']} < {MIN_DAYS}")
        if not g["ci_lo_trade_gt0"]:
            why.append(f"{tag}.gate.ci_lo_trade<=0")
        if not g["ci_lo_gt0"]:
            why.append(f"{tag}.gate.ci_lo_date_clustered<=0")
        if not g["majority_dates"]:
            why.append(f"{tag}.gate.no_majority_of_dates_positive")
    npos = 0
    for cb in CANONICAL_BLOCKS:
        ok = True
        for k in SUB_CELLS:
            if subs.get(k) is None:
                ok = False
                break
            n_ = sum(v["n"] for nm, v in subs[k]["per_block"].items() if canonical_block(nm) == cb)
            t_ = sum(v["total"] for nm, v in subs[k]["per_block"].items() if canonical_block(nm) == cb)
            ok = ok and n_ > 0 and t_ > 0
        npos += bool(ok)
    if npos < min_blocks_positive:
        why.append(f"blocks_positive {npos}/{len(CANONICAL_BLOCKS)} < {min_blocks_positive}")
    for grp in GROUPS:
        for key in SUB_CELLS:
            g = (groups or {}).get((grp, *key))
            if g is None:
                why.append(f"{grp}: not measured (no trips in this group)")
                break
            if not g["mean"] > 0:
                why.append(f"{grp}.{'/'.join(key)}.mean<=0")
    if excl_share is not None and excl_share > MAX_EXCLUDED_SHARE:
        why.append(f"excluded share {excl_share:.1%} > {MAX_EXCLUDED_SHARE:.0%}: not decidable in-sample")
    if min_att_per_day and explore_att_per_day is not None and explore_att_per_day < min_att_per_day:
        why.append(f"explore-0814 attempts/day {explore_att_per_day:.1f} < {min_att_per_day:g} (RULES step 2)")
    return bool(why), why


# ------------------------------------------------------------------------------------------------------------------------------ worst-in-slot (R7)
# The worst-in-slot leg is built from the functions QP approved on #747 (tools/arena_report_legs.py, merged 79d051e): state_arrays, slot_states, worst_index and end_minus_worst are
# IMPORTED above and nothing of them is copied here. The kit hands them its own pool arrays and its own landing / exit slots (the engine's: x = decision + 7 + k, exit = exit decision + 7
# + lag), computes the states per trip at pricing time (where the pool's prints are still in hand), and re-prices the SAME trips with its own vectorised legs() (equal to
# arena_paper.price_leg to 0 lamports, and pinned equal to arena_report_legs on a fixture). Selection is by spot price, never by outcome.
WORST_IN_SLOT_AVAILABLE = True
REPORT_LEGS_PIN = "65f4d471e3ad19308a038c4d6563f22322134013f09c0afe2d64c648964a0d8b"  # sha256 of tools/arena_report_legs.py as QP approved it on #747 (the test fails if the file changes)


class _ArrHist:
    """A pool's kit arrays in the shape arena_report_legs.state_arrays reads from a PoolHist (`.pool`, `.arrays()`); `vok` = the print carries a finite V."""

    def __init__(self, pool: str, a: dict) -> None:
        self.pool = pool
        self._a = {**a, "vok": np.isfinite(a["v"])}

    def arrays(self) -> dict:
        return self._a


def _same(a: float, b: float) -> bool:
    return bool(np.isclose(a, b, rtol=PARITY_RTOL, atol=0.0, equal_nan=True))


def _no_v(sa: dict, idx: np.ndarray) -> bool:
    """True if a PRINT among the state indices has no finite V (the derived END index n is not a print)."""
    pr = idx[idx < len(sa["vok"])]
    return bool(pr.size and not sa["vok"][pr].all())


def worst_in_slot_states(pool: str, a: dict, sd: np.ndarray, sps_d: np.ndarray, xs: np.ndarray, sps_x: np.ndarray, need: np.ndarray, late: np.ndarray, skip: np.ndarray, st: dict) -> dict:
    """The worst-in-slot state columns of one pool's trips (R7): the entry state of the highest price among the states of the landing slot, per latency, and the exit state of the lowest
    price among the states of the exit slot. A trip whose END state the engine derived from the rows before `need` (the late-print case) uses that cut history, as arena_report_legs
    does for a derived_last trip. `st` is the kit's own END pricing of the same trips: the END member of every slot's states must reproduce it (w_mismatch), and a slot state without
    a finite V (w_vbad) is flagged; either makes the cell's worst-in-slot "not measured". `skip` trips (block-edge exclusions) are left NaN."""
    n = len(sd)
    out: dict[str, Any] = {"wQe": {l: np.full(n, np.nan) for l in LATS}, "wBe": {l: np.full(n, np.nan) for l in LATS}, "wQx": np.full(n, np.nan), "wBx": np.full(n, np.nan),
                           "w_vbad": np.zeros(n, bool), "w_mismatch": np.zeros(n, bool)}
    h = _ArrHist(pool, a)
    full = state_arrays(h)
    land = {l: sd + D + F.k_slots_vec(l, sps_d, "ceil") for l in LATS}
    xl = xs + D + F.exit_lag_vec(sps_x)
    for i in np.flatnonzero(~skip):
        sa = state_arrays(h, int(need[i])) if late[i] else full
        idx_x, je_x = slot_states(sa, int(xl[i]))
        wx = worst_index(sa, idx_x, "sell")
        out["wQx"][i], out["wBx"][i] = sa["QX"][wx], sa["BX"][wx]
        bad_v = _no_v(sa, idx_x)
        ok = _same(sa["QX"][je_x], st["Qx"][i]) and _same(sa["BX"][je_x], st["Bx"][i])
        for l in LATS:
            idx_e, je_e = slot_states(sa, int(land[l][i]))
            we = worst_index(sa, idx_e, "buy")
            out["wQe"][l][i], out["wBe"][l][i] = sa["QX"][we], sa["BX"][we]
            bad_v = bad_v or _no_v(sa, idx_e)
            ok = ok and _same(sa["QX"][je_e], st["Qe"][l][i]) and _same(sa["BX"][je_e], st["Be"][l][i])
        out["w_vbad"][i], out["w_mismatch"][i] = bad_v, not ok
    return out


def _leg_stat(lam: np.ndarray) -> dict:
    x = np.asarray(lam, float) / 1e9
    return {"n": int(len(x)), "mean_sol": float(x.mean()), "total_sol": float(x.sum()), "ci90_lo_sol": F.boot_lo(x)}  # F.boot_lo exactly as arena_score.cell_stats calls it


def worst_in_slot_legs(st: dict, cfg: P.StackConfig) -> dict:
    """The worst-in-slot leg and its "no guard rescue" companion at the deciding cell (1.9 s, 0.25 SOL, cfg.rent) for the four sub-cells {sc, total} x {flat, press}, on the SAME trips as
    the screen. st carries the END states (Qe, Be, Qx, Bx) and the worst-in-slot ones (wQe, wBe, wQx, wBx) from worst_in_slot_states. The pressure intercept is refit on each leg's own
    filled set (the stack's per-cell refit, as arena_report_legs). The companion keeps the END-priced row of a trip that is filled at END and guarded at its worst entry state, so the
    1.15 guard cannot revert it (QP on #747: with the guard the worst-in-slot leg alone is not a bound). `lower` = the lower of the two legs' means (R7 canary condition), per sub-cell.
    Returns {"measured": False, "reason": ...} with NO figure when the tape states do not reproduce the kit's END states or a slot state has no V."""
    n_mis, n_vbad = int(st["w_mismatch"].sum()), int(st["w_vbad"].sum())
    if n_mis or n_vbad:
        return {"measured": False, "reason": f"worst-in-slot not measured: END consistency failed on {n_mis} trips; a slot state has no V on {n_vbad} trips", "sub_cells": None,
                "diag": {"trips": int(len(st["ref"])), "end_mismatch_trips": n_mis, "no_v_trips": n_vbad}}
    stake, lat = STAKE_SOL * 1e9, LAT_DECIDING
    stw = dict(st, Qe=st["wQe"], Be=st["wBe"], Qx=st["wQx"], Bx=st["wBx"])
    end_c: dict[str, dict] = {}
    wor_c: dict[str, dict] = {}
    ng_c: dict[str, dict] = {}
    flips: dict[str, int] = {}
    icpt: dict[str, dict] = {}
    for rule in STRAND_RULES:
        E, W = legs(st, stake, lat, rule, cfg), legs(stw, stake, lat, rule, cfg)
        rescue = E["filled"] & ~W["filled"]  # the guard turned a trip that was filled at END away at its worst entry state
        flips[rule] = int(rescue.sum())
        flat, press, c, _ = P.book_fail_legs(np.where(rescue, E["pnl"], W["pnl"]), np.where(rescue, E["filled"], W["filled"]), np.where(rescue, E["strand"], W["strand"]),
                                             st["ssb"][lat], st["nearby"][lat], cfg.send)
        NG = {"flat": flat, "press": press}
        icpt[rule] = {"end": E["c"], "worst_in_slot": W["c"], "no_guard_rescue": c}
        for fail in FAILS:
            key = f"{rule}|{fail}"
            end_c[key], wor_c[key], ng_c[key] = _leg_stat(E[fail]), _leg_stat(W[fail]), _leg_stat(NG[fail])
    gap, gap_ng = end_minus_worst(end_c, wor_c), end_minus_worst(end_c, ng_c)
    sub = {}
    for key in end_c:
        w, g = wor_c[key], ng_c[key]
        sub[key] = {"end": end_c[key], "worst_in_slot": w, "no_guard_rescue": g,
                    "lower": {"mean_sol": min(w["mean_sol"], g["mean_sol"]), "ci90_lo_sol": min(w["ci90_lo_sol"], g["ci90_lo_sol"]),
                              "leg_of_mean": "no_guard_rescue" if g["mean_sol"] < w["mean_sol"] else "worst_in_slot"},
                    "end_minus_worst": gap[key], "end_minus_no_guard_rescue": gap_ng[key]}
    with np.errstate(all="ignore"):
        inval = lambda Q, B: ~np.isfinite(Q / B) | (B <= 0)  # noqa: E731
        moved = lambda a_, b_: int(((a_ != b_) & ~(np.isnan(a_) & np.isnan(b_))).sum())  # noqa: E731
        diag = {"trips": int(len(st["ref"])), "guard_flips": flips, "pressure_intercept": icpt,
                "invalid_pick": int((inval(st["wQe"][lat], st["wBe"][lat]) | inval(st["wQx"], st["wBx"])).sum()),
                "entry_moved": moved(st["wQe"][lat], st["Qe"][lat]) + moved(st["wBe"][lat], st["Be"][lat]), "exit_moved": moved(st["wQx"], st["Qx"]) + moved(st["wBx"], st["Bx"])}
    return {"measured": True, "reason": None, "sub_cells": sub, "diag": diag}


def registration_status(event_triggered: bool, killed: bool, wis_measured: bool) -> str:
    """What a cell that was screened may do next (QP R7 ruling on #752)."""
    if killed:
        return "KILLED"
    if wis_measured:
        return "SCREEN-SURVIVOR (worst-in-slot measured > 0 on all four sub-cells)" if event_triggered else "SCREEN-SURVIVOR (worst-in-slot measured)"
    if event_triggered:
        return "PROVISIONAL-BLOCKED (event-triggered: worst-in-slot not measured; no registration until it is, and > 0 on all four sub-cells)"
    return "PROVISIONAL (grid-triggered: may register with 'worst-in-slot not measured' stated; needed before any canary)"


AUTHOR_NOTES = (
    "**Author's job, not the kit's.** (1) Drop any cell that matches the C1-NF decision grid and exit (EXP-025 section 4, DEC-031 section 3.7) BEFORE pricing. (2) Drop any cell under 20 "
    "attempts/day on explore-0814 WITHOUT pricing (RULES step 2); the kit's explore-0814 attempts/day kill is a backstop, not a substitute. (3) Before ANY registration run `causal_probe` "
    "(on a copy of the state) on a seed-1 sample of at least 200 triggers PER BLOCK with 0 failures, and state the count in REPORT; the kit hands a rule the whole hour and cannot see "
    "look-ahead. (4) Declare per cell, before pricing, whether it is event-triggered (the default) or grid-triggered; an event cell is KILLED when the lower of its worst-in-slot and no-guard-rescue legs has a mean <= 0 in any of the four sub-cells (R7), and stays PROVISIONAL-BLOCKED if "
    "worst-in-slot could not be measured; a grid cell reports it only and needs it measured before any canary. (5) The kit's figures are not the bot's: it resolves exits by slot arithmetic at the hour's slot rate, "
    "the arena bot holds in ms on a 1 s tick with the engine's rolling clock, so the frozen bot's md5 decision-equivalence replay is still required at registration. (6) Selection: "
    "every figure is in-sample on blocks that were mined before; a survivor is the best of its declared cells, and the chance of at least one false screen pass is up to 1 - 0.95^N "
    "(QP ruling item 1). A survivor is a candidate for a forward slot and nothing more.",
)


# ------------------------------------------------------------------------------------------------------------------------------ screen p (EXP-028, REPORT-ONLY)
# QP ruling on PR #756 (comment 6276924696, item 4), amended by the coordinator for B (QP on #754, comment 6276929107, item 1). It is IN the kit because the p must be computed on exactly
# the per-attempt vectors the kill used, which exist only in memory here. It adds keys only: it never kills a cell and never rescues one (it is computed after the verdict is final and
# nothing reads it), and every pre-existing results.json and TABLE field is byte-identical with and without it (tested). It is a Monte Carlo p of an in-sample screen, not evidence.
SCREEN_P_B = 10_000  # default number of draws
SCREEN_P_B_MIN, SCREEN_P_B_MAX = F.BOOT_DRAWS, 1_000_000  # >= 1,000 (so the first 1,000 draws can be checked against arena_stack.boot_lo), <= 1,000,000 (a Holm bar below 1/10001)
SCREEN_P_SEED = F.BOOT_SEED  # 1, and a FRESH numpy.random.default_rng(seed) per sub-cell and per date-cluster resampler
SCREEN_P_SOURCE = "QP ruling, vaanai/MAL PR #756 comment 6276924696 item 4 (B parameter: PR #754 comment 6276929107 item 1)"
SCREEN_P_DEFINITION = (
    "H0: mean SOL per attempt <= 0, one-sided. x_s = the per-attempt SOL vector stat() passes to arena_stack.boot_lo (deciding cell 1.9 s / 0.25 SOL / rent 0 / END leg, pooled over the "
    "four canonical blocks after block-edge exclusions, guarded reverts included), xbar = mean(x_s). Draws b = 1..B: m_b = x_s[rng.integers(0, n, n)].mean(), rng = default_rng(seed) fresh per "
    "sub-cell. p_pct = (1 + #{m_b <= 0}) / (1 + B); p_null = (1 + #{m_b - xbar >= xbar}) / (1 + B); sub-cell p = max(p_pct, p_null); cell p = max over the four sub-cells "
    "{total, sc} x {flat, press}. Date-cluster p: the kit's date resampler (boot_lo(dsum, dn): resample UTC dates, r_b = dsum[ix].sum() / max(1, dn[ix].sum())), same B and seed, "
    "the same two tails around the pooled ratio rhat, same max rules. A cell with n = 0, a non-finite attempt, or fewer than 2 attempts (2 dates for the cluster) has p None, which is not a pass.")


def screen_p_check(stake_sol: float, draws: int) -> int:
    """Refuse (Refused) a stake other than 0.25 SOL (the kit decides at STAKE_SOL; EXP-028 item 2 says the stake in DECLARE) and a B outside [1,000, 1,000,000]. Returns B."""
    if isinstance(stake_sol, bool) or stake_sol != STAKE_SOL:
        raise Refused(f"screen_p_stake_refused: screen p is defined at {STAKE_SOL} SOL only (the kit's deciding stake), not {stake_sol!r}")
    if isinstance(draws, bool) or not isinstance(draws, (int, np.integer)) or not SCREEN_P_B_MIN <= draws <= SCREEN_P_B_MAX:
        raise Refused(f"screen_p_draws_refused: B must be an integer in [{SCREEN_P_B_MIN}, {SCREEN_P_B_MAX}], not {draws!r}")
    return int(draws)


def _boot_means(x: np.ndarray, B: int, seed: int = SCREEN_P_SEED) -> np.ndarray:
    """m_b = x[rng.integers(0, n, n)].mean() for b = 1..B from a fresh default_rng(seed): arena_stack.boot_lo's own loop, so m[:1000] reproduces its bound bit for bit."""
    rng = np.random.default_rng(seed)
    n = len(x)
    m = np.empty(B)
    for b in range(B):
        m[b] = x[rng.integers(0, n, n)].mean()
    return m


def _cluster_ratios(dsum: np.ndarray, dn: np.ndarray, B: int, seed: int = SCREEN_P_SEED) -> np.ndarray:
    """r_b of the kit's own date-cluster resampler (boot_lo(dsum, dn)'s loop) for b = 1..B: resample the k UTC dates, pooled mean SOL per trade."""
    k = len(dsum)
    rng = np.random.default_rng(seed)
    r = np.empty(B)
    for b in range(B):
        ix = rng.integers(0, k, k)
        r[b] = dsum[ix].sum() / max(1, dn[ix].sum())
    return r


def _tails(draws: np.ndarray, centre: float, B: int) -> dict:
    """The two one-sided tails around `centre` (xbar, or the pooled ratio rhat): the percentile share #{d <= 0} and the recentred null-imposed share #{d - centre >= centre}, each with the
    +1 Monte Carlo correction (never 0), and their max. Counts and p are reported unrounded."""
    c_pct, c_null = int((draws <= 0).sum()), int(((draws - centre) >= centre).sum())
    p_pct, p_null = (1 + c_pct) / (1 + B), (1 + c_null) / (1 + B)
    return {"tail_count_pct": c_pct, "tail_count_null": c_null, "p_pct": p_pct, "p_null": p_null, "p": max(p_pct, p_null)}


def screen_p_sub(lam: np.ndarray, day: np.ndarray, *, B: int = SCREEN_P_B, seed: int = SCREEN_P_SEED, stake_sol: float = STAKE_SOL, ci_lo_trade: float | None = None,
                 ci_lo_date: float | None = None) -> dict:
    """Screen p of ONE sub-cell. lam: the per-attempt pnl in lamports exactly as stat() receives it; day: its UTC day. x = lam / 1e9 and (dsum, dn) are built exactly as stat() builds the
    vectors it hands to arena_stack.boot_lo and to boot_lo(dsum, dn). ci_lo_trade / ci_lo_date (stat's own bounds) are optional: with them the result records that the first 1,000
    draws reproduce the bounds the kill used."""
    B = screen_p_check(stake_sol, B)
    x = np.asarray(lam, float) / 1e9
    out: dict[str, Any] = {"n": int(len(x)), "B": B, "seed": int(seed), "xbar": None, "tail_count_pct": None, "tail_count_null": None, "p_pct": None, "p_null": None, "p": None,
                           "ci90_lo_first1000": None, "ci90_lo_first1000_equals_stat": None, "none_reason": None, "date_cluster": None}
    dc: dict[str, Any] = {"k_dates": 0, "B": B, "seed": int(seed), "rhat": None, "tail_count_pct": None, "tail_count_null": None, "p_pct": None, "p_null": None, "p": None,
                          "ci90_lo_first1000": None, "ci90_lo_first1000_equals_stat": None, "none_reason": None}
    out["date_cluster"] = dc
    if len(x) == 0:
        out["none_reason"] = dc["none_reason"] = "n = 0: no p (not a pass)"
        return out
    if not np.isfinite(x).all():
        out["none_reason"] = dc["none_reason"] = f"{int((~np.isfinite(x)).sum())} non-finite attempt(s): no p (not a pass)"
        return out
    out["xbar"] = xbar = float(x.mean())
    if len(x) < 2:
        out["none_reason"] = "n < 2: a resample is constant, no p (not a pass)"
    else:
        m = _boot_means(x, B, seed)
        out.update(_tails(m, xbar, B))
        out["ci90_lo_first1000"] = lo = float(np.percentile(m[:SCREEN_P_B_MIN], F.BOOT_PCT))
        out["ci90_lo_first1000_equals_stat"] = None if ci_lo_trade is None else bool(lo == ci_lo_trade)
    days, inv = np.unique(np.asarray(day), return_inverse=True)
    dsum, dn = np.bincount(inv, weights=x), np.bincount(inv).astype(float)
    dc["k_dates"] = int(len(days))
    dc["rhat"] = rhat = float(dsum.sum() / max(1, dn.sum()))
    if len(days) < 2:
        dc["none_reason"] = "fewer than 2 UTC dates: a date resample is constant, no p (not a pass)"
        return out
    r = _cluster_ratios(dsum, dn, B, seed)
    dc.update(_tails(r, rhat, B))
    dc["ci90_lo_first1000"] = lo = float(np.percentile(r[:SCREEN_P_B_MIN], F.BOOT_PCT))
    dc["ci90_lo_first1000_equals_stat"] = None if ci_lo_date is None else bool(lo == ci_lo_date)
    return out


def _max_p(ps: Sequence[float | None]) -> float | None:
    return None if (not ps or any(p is None for p in ps)) else max(ps)


def screen_p_cell(st: dict | None, cfg: P.StackConfig, stats: dict | None = None, *, B: int = SCREEN_P_B, seed: int = SCREEN_P_SEED, stake_sol: float = STAKE_SOL) -> dict:
    """Screen p of a cell: the four sub-cells at the deciding cell (1.9 s, 0.25 SOL, cfg.rent = 0, END leg) on the pooled trips `st`, re-priced by the kit's own legs() (the same call, the
    same trips and the same per-cell pressure refit that stat() was fed). stats: the cell's stat() dict, to record that the first 1,000 draws reproduce its bounds. cell p = the max over
    the four sub-cells (an intersection-union test), None unless all four have a p; the same for the date-cluster p. REPORT ONLY: nothing reads these numbers."""
    B = screen_p_check(stake_sol, B)
    subs: dict[str, dict] = {}
    n = 0 if st is None else len(st["ref"])
    day = st["T"] // 86400 if n else np.empty(0, np.int64)
    for rule in STRAND_RULES:
        L = legs(st, STAKE_SOL * 1e9, LAT_DECIDING, rule, cfg) if n else None
        for fail in FAILS:
            s_ = (stats or {}).get(f"{LAT_DECIDING}|{rule}|rent0|{fail}")
            subs[f"{rule}|{fail}"] = screen_p_sub(L[fail] if n else np.empty(0), day, B=B, seed=seed, stake_sol=stake_sol,
                                                  ci_lo_trade=None if s_ is None else s_["ci_lo_trade"], ci_lo_date=None if s_ is None else s_["ci_lo"])
    return {"report_only": True, "source": SCREEN_P_SOURCE, "B": B, "seed": int(seed), "stake_sol": STAKE_SOL, "leg": "1.9 s / 0.25 SOL / rent 0 / END, pooled over the four blocks",
            "sub_cells": subs, "cell_p": _max_p([v["p"] for v in subs.values()]), "cell_p_date_cluster": _max_p([v["date_cluster"]["p"] for v in subs.values()])}


def screen_p_md(res: dict) -> list[str]:
    """TABLE section 5 (appended; nothing above it changes): the screen p per cell, unrounded."""
    head = " | ".join(f"{rule}/{fail} trade-level: n, xbar, tails pct/null, p_pct, p_null, p | {rule}/{fail} date-cluster: k, rhat, tails pct/null, p" for rule, fail in SUB_CELLS)
    L = ["", "## 5. Screen p (EXP-028, REPORT ONLY: it never kills or rescues a cell)", "", SCREEN_P_DEFINITION, "",
         "Source: " + SCREEN_P_SOURCE + ". Unrounded. `None` = no p (n = 0, a non-finite attempt, or too few attempts / dates) and is not a pass. In-sample, no profit claim.", "",
         f"| cell | B | seed | cell p | cell p date-cluster | {head} |", "|---|---|---|---|---|" + "---|" * (2 * len(SUB_CELLS))]
    for nm, r in res.items():
        sp = r.get("screen_p")
        if sp is None:
            continue
        cols = []
        for rule, fail in SUB_CELLS:
            c = sp["sub_cells"][f"{rule}|{fail}"]
            d = c["date_cluster"]
            cols += [f"{c['n']}, {c['xbar']!r}, {c['tail_count_pct']}/{c['tail_count_null']}, {c['p_pct']!r}, {c['p_null']!r}, **{c['p']!r}**",
                     f"{d['k_dates']}, {d['rhat']!r}, {d['tail_count_pct']}/{d['tail_count_null']}, **{d['p']!r}**"]
        L.append(f"| {nm} | {sp['B']} | {sp['seed']} | **{sp['cell_p']!r}** | {sp['cell_p_date_cluster']!r} | " + " | ".join(cols) + " |")
    return L


# ------------------------------------------------------------------------------------------------------------------------------ the screen
@dataclass
class _Store:
    chunks: list = field(default_factory=list)
    trigs: list = field(default_factory=list)  # (cell index, decision slot, hour idx, spec)
    until: int = 0


class Screen:
    def __init__(self, rule: Any, *, count_only: bool = False, min_attempts_per_day: float = 20.0, screen_p: bool = True, screen_p_draws: int = SCREEN_P_B,
                 declared_stake_sol: float = STAKE_SOL) -> None:
        self.rule, self.count_only, self.min_per_day = rule, count_only, float(min_attempts_per_day)
        self.screen_p, self.screen_p_B = bool(screen_p), (screen_p_check(declared_stake_sol, screen_p_draws) if screen_p else int(screen_p_draws))  # Refused before anything is read
        raw = list(rule.cells())
        self.cells = [Cell(str(c)) if isinstance(c, str) else Cell(str(c.name), float(getattr(c, "cooldown_s", 60.0)), bool(getattr(c, "event_triggered", True)),
                                                                   bool(getattr(c, "event_exit", False))) for c in raw]
        if not self.cells or len({c.name for c in self.cells}) != len(self.cells):
            raise ValueError("a rule needs at least one cell and unique cell names")
        self.cell_ix = {c.name: i for i, c in enumerate(self.cells)}
        self.counters: collections.Counter = collections.Counter()
        self.blocks: list[str] = []
        self.days: set[int] = set()
        self.cnt = [collections.Counter() for _ in self.cells]  # day -> validated triggers
        self.cnt_ub = [collections.Counter() for _ in self.cells]  # day -> after the time-only overlap rule (an upper bound of attempts)
        self.acc: list[list[dict]] = [[] for _ in self.cells]
        self.cfg = {RENT_DECIDING: screen_cfg(RENT_DECIDING), RENT_CHARGED: screen_cfg(RENT_CHARGED)}
        self.rows = self.hours = 0
        self.t_load = self.t_rule = self.t_price = 0.0
        n = len(self.cells)
        self.flag_hours: list[str] = []
        self.excl_cell, self.late_cell, self.derived_cell = collections.Counter(), collections.Counter(), collections.Counter()  # per cell index
        self.gdays: dict[str, set] = {}  # block group -> UTC days run
        self.cnt_ub_expl = [0] * n  # count-only: attempts upper bound inside explore-0814
        self.exit_used: collections.Counter = collections.Counter()  # per cell index: attempts (priced) or triggers (count-only) whose spec carried an exit_fire
        self.exit_probe: dict[tuple[int, int], list[int]] = {}  # (cell index, block index) -> [event exits probed, probe failures]
        self.cell_fire: dict[int, bool] = {}  # cell index -> its specs carry an exit_fire (a cell is all exit_fire or none)
        self._open: dict[tuple, _Pos] = {}  # (cell index, pool) -> the unresolved exit_fire position
        self._nxt: dict[tuple, int] = {}  # (cell index, pool) -> first slot a new position may be admitted (exit + cooldown)
        self._hc: dict[str, dict] = {}  # pool -> this hour's concatenated arrays (cleared every hour)

    # --- trigger validation
    def _validate(self, hr: HourRows, tr: Any, state: RunState) -> tuple[int, str, int, ExitSpec] | None:
        c = self.counters
        pool, slot, spec = tr
        c["triggers_raw"] += 1
        i = hr.pool_index().get(pool)
        if i is None:
            c["refused_non_wsol" if pool in hr.nonwsol_pools else "refused_unknown_pool"] += 1
            return None
        if exit_problem(spec):
            c["refused_bad_exit"] += 1
            return None
        slot = int(slot)
        a, b = int(hr.ptr[i]), int(hr.ptr[i + 1])
        j = int(np.searchsorted(hr.sl[a:b], slot, "right")) - 1
        if j < 0:
            c["refused_no_print_at_or_before_decision"] += 1
            return None
        first_prev, first_hr, now = state.first_seen.get(pool), int(hr.bt[a]), int(hr.bt[a + j])
        if first_hr <= 0 or now <= 0 or (first_prev is not None and first_prev <= 0):
            c["refused_pool_age_unknown"] += 1
            return None
        first = first_hr if first_prev is None else min(first_prev, first_hr)  # the pool's first print: an earlier hour's, else this hour's own first row (<= the decision print)
        if now - first < MIN_POOL_AGE_S:
            c["refused_pool_age_lt_360s"] += 1
            return None
        v = float(hr.v[a + j])
        if not math.isfinite(v):
            c["refused_v_unknown"] += 1
            return None
        if not (V_LO <= v <= V_HI):
            c["refused_v_out_of_band"] += 1
            return None
        name = getattr(spec, "cell", None) or (self.cells[0].name if len(self.cells) == 1 else None)
        if name is None or name not in self.cell_ix:
            c["refused_no_such_cell"] += 1
            return None
        return self.cell_ix[name], pool, slot, spec

    # --- one block
    def run_block(self, bi: int, name: str, hours: Iterable[HourRows]) -> None:
        self.blocks.append(name)
        rule, state = self.rule, RunState(block=name)
        if hasattr(rule, "start_block"):
            rule.start_block(state)
        stores: dict[str, _Store] = {}
        clk: dict[str, dict[int, Any]] = {"rate": {}, "min": {}, "T": {}}  # per hour index: slots per second, first slot, hour start
        seg_end = -1
        prev: HourRows | None = None
        nextok: dict[tuple, int] = {}
        self._open, self._nxt = {}, {}
        t0 = time.time()
        for hr in hours:
            self.t_load += time.time() - t0
            self.hours += 1
            self.rows += hr.n
            self.counters["rows_pumpswap"] += hr.n_pumpswap
            self.counters["rows_non_wsol"] += hr.n_nonwsol
            self.counters["rows_wsol_kept"] += hr.n
            if hr.n_no_ts:
                self.counters["rows_no_timestamp"] += hr.n_no_ts
                self.counters["hours_with_missing_timestamp"] += 1
                self.flag_hours.append(hr.hour)
            if hr.n == 0 or (prev is not None and hr.T != prev.T + 3600):
                self.counters["empty_hours" if hr.n == 0 else "hour_gaps"] += 1
                self._flush(stores, bi, seg_end, clk)
                prev, seg_end = None, -1
                state.gap_before = True
                if hr.n == 0:
                    t0 = time.time()
                    continue
            self._hc = {}
            if self._open:  # exit_fire positions first: through this hour's slots, BEFORE the rule sees the hour (the rule has seen triggers() for hours <= h-1 only)
                self._advance_open(hr, stores, bi)
            state.prev, state.hour_index = prev, hr.idx
            t1 = time.time()
            raw = list(rule.triggers(hr, state))
            self.t_rule += time.time() - t1
            state.gap_before = False
            good = [v for v in (self._validate(hr, t, state) for t in raw) if v is not None]
            for i, nm in enumerate(hr.pools.tolist()):  # only now: the rule has already run on this hour
                if nm not in state.first_seen:
                    state.first_seen[nm] = int(hr.bt[hr.ptr[i]])
            good.sort(key=lambda g: (g[0], g[1], g[2]))
            day = hr.T // 86400
            self.days.add(day)
            grp = block_group(name)
            if grp:
                self.gdays.setdefault(grp, set()).add(day)
            clk["rate"][hr.idx], clk["min"][hr.idx], clk["T"][hr.idx] = hr.slots_per_s, hr.min_slot, hr.T
            seg_end = max(seg_end, hr.max_slot)
            for ci, pool, slot, spec in good:
                has_fire = getattr(spec, "exit_fire", None) is not None
                if not self.count_only and self.cell_fire.setdefault(ci, has_fire) != has_fire:
                    self.counters["refused_exit_fire_mixed_cell"] += 1  # a cell is all exit_fire or none: admission needs every exit of the cell decided as the data arrive
                    continue
                self.cnt[ci][day] += 1
                if self.count_only:
                    # time-only overlap rule: an upper bound on the hold, since a take-profit / stop exit can only free the pool earlier; it ignores an event exit (see the module doc)
                    self.exit_used[ci] += getattr(spec, "exit_fire", None) is not None
                    k = (ci, pool)
                    if slot >= nextok.get(k, -1):
                        self.cnt_ub[ci][day] += 1
                        self.cnt_ub_expl[ci] += grp == "explore-0814"
                        nextok[k] = slot + int(np.rint((spec.hold_s + self.cells[ci].cooldown_s) * hr.slots_per_s))
                    continue
                s = stores.get(pool)
                if s is None:
                    s = stores[pool] = _Store()
                    if prev is not None and pool in prev.pool_index():
                        s.chunks.append(self._chunk(prev, prev.pool_index()[pool]))
                if has_fire:
                    self._admit(ci, pool, slot, spec, hr, s, bi)
                    continue
                s.trigs.append((ci, slot, hr.idx, spec, None))
                s.until = max(s.until, hr.idx + int(math.ceil((spec.hold_s + RETAIN_EXTRA_S) / 3600.0)))
            if not self.count_only:
                for pool, s in stores.items():
                    i = hr.pool_index().get(pool)
                    if i is not None:
                        s.chunks.append(self._chunk(hr, i))
                for pool in [p for p, s in stores.items() if s.until <= hr.idx]:
                    self._price_store(stores.pop(pool), pool, bi, seg_end, clk)
            prev = hr
            t0 = time.time()
        if not self.count_only:
            self._flush(stores, bi, seg_end, clk)

    @staticmethod
    def _chunk(hr: HourRows, i: int) -> dict:
        s = hr.rows(i)
        return {"sl": hr.sl[s].copy(), "ib": hr.isbuy[s].copy(), "so": hr.sol[s].copy(), "tk": hr.tok[s].copy(), "q": hr.q[s].copy(), "b": hr.b[s].copy(), "v": hr.v[s].copy()}

    def _entry(self, pool: str, s: _Store, hr: HourRows) -> dict:
        """The pool's rows through THIS hour (the store's chunks plus the hour's own), read-only, with the post-trade price; cached for the hour."""
        e = self._hc.get(pool)
        if e is None:
            chunks = list(s.chunks)
            i = hr.pool_index().get(pool)
            if i is not None:
                chunks.append(self._chunk(hr, i))
            a = {k: np.concatenate([c[k] for c in chunks]) for k in chunks[0]}
            a["ib"] = a["ib"].astype(bool)
            pp = post_price(a)
            for v in (*a.values(), pp):
                v.flags.writeable = False
            e = self._hc[pool] = {"a": a, "pp": pp, "cut": {}}
        return e

    def _advance_open(self, hr: HourRows, stores: dict, bi: int) -> None:
        for pos in list(self._open.values()):
            e = self._entry(pos.pool, stores[pos.pool], hr)
            if pos.run(e["a"], e["pp"], hr.max_slot, e["cut"]):
                self._resolve(pos, e["a"], e["pp"], bi)

    def _admit(self, ci: int, pool: str, slot: int, spec: Any, hr: HourRows, s: _Store, bi: int) -> None:
        """Admission of an exit_fire trigger AS THE DATA ARRIVE: dropped while the cell's position in this pool is open or inside exit + cooldown (the same rule as the priced path), else a
        position that is run through the rest of this hour at once (its exit may be decided before the next trigger of the hour is looked at)."""
        c, key = self.counters, (ci, pool)
        if key in self._open or slot < self._nxt.get(key, -1):
            c["overlap_dropped"] += 1
            return
        rate = hr.slots_per_s
        pos = _Pos(spec, slot, rate, 1.0 / rate, ci, pool, hr.idx)
        self.exit_used[ci] += 1
        c["exit_fire_positions"] += 1
        s.trigs.append((ci, slot, hr.idx, spec, pos))
        s.until = max(s.until, hr.idx + int(math.ceil((spec.hold_s + RETAIN_EXTRA_S) / 3600.0)))
        e = self._entry(pool, s, hr)
        if pos.run(e["a"], e["pp"], hr.max_slot, e["cut"]):
            self._resolve(pos, e["a"], e["pp"], bi)
        else:
            self._open[key] = pos

    def _resolve(self, pos: "_Pos", a: dict, pp: np.ndarray, bi: int, set_nxt: bool = True) -> None:
        """A position's exit is decided: free the pool after the cooldown, and probe an event exit (prefix probe: fires again on rows <= the fire slot, does NOT fire at the previous slot asked)."""
        self._open.pop((pos.ci, pos.pool), None)
        self.counters["exit_fire_asks"] += pos.asks
        if set_nxt:
            self._nxt[(pos.ci, pos.pool)] = pos.xs + int(np.rint(self.cells[pos.ci].cooldown_s * pos.rate))
        if pos.why == EVENT_CODE:
            ok = exit_fire_probe(pos, a, pp, pos.xs)
            rec = self.exit_probe.setdefault((pos.ci, bi), [0, 0])
            rec[0] += 1
            rec[1] += not ok
            self.counters["exit_fire_events"] += 1
            self.counters["exit_probe_failures"] += not ok

    def _flush(self, stores: dict, bi: int, seg_end: int, clk: dict) -> None:
        for pool in list(stores):
            self._price_store(stores.pop(pool), pool, bi, seg_end, clk)

    def _price_store(self, s: _Store, pool: str, bi: int, seg_end: int, clk: dict) -> None:
        rate, minslot = clk["rate"], clk["min"]
        t0 = time.time()
        for k in [k for k in self._nxt if k[1] == pool]:  # the store is gone after this: the next trigger of the pool starts afresh, as on the priced path
            del self._nxt[k]
        a = {k: np.concatenate([c[k] for c in s.chunks]) for k in s.chunks[0]}
        a["ib"] = a["ib"].astype(bool)
        pp = post_price(a)
        hours = np.array(sorted(minslot))
        mins = np.array([minslot[h] for h in hours])
        sps_of = lambda h: 1.0 / rate[int(h)]  # noqa: E731
        c = self.counters
        rows: list[tuple] = []
        for ci in sorted({t[0] for t in s.trigs}):
            nxt = -1
            for _, slot, hi, spec, pos in sorted((t for t in s.trigs if t[0] == ci), key=lambda t: t[1]):
                if pos is not None:  # an exit_fire position: admitted and (but for a gap / the block's end) decided as the data arrived
                    if pos.xs is None:
                        pos.run(a, pp, 2**62)
                        self._resolve(pos, a, pp, bi, set_nxt=False)
                    c["hold_clamped"] += pos.clamped
                    rows.append((ci, slot, hi, pos.xs, pos.why))
                    continue
                if slot < nxt:
                    c["overlap_dropped"] += 1
                    continue
                xs, why, clamped = resolve_exit(a, pp, slot, spec, rate[hi], sps_of(hi))
                c["hold_clamped"] += clamped
                nxt = xs + int(np.rint(self.cells[ci].cooldown_s * rate[hi]))
                rows.append((ci, slot, hi, xs, why))
        if not rows:
            return
        R = np.array(rows, np.int64)
        ci, sd, hi, xs, why = R.T
        hx = np.clip(np.searchsorted(mins, xs, "right") - 1, 0, len(mins) - 1)
        sps_d = np.array([sps_of(h) for h in hi])
        sps_x = np.array([sps_of(hours[h]) for h in hx])
        st = pool_states(a, sd, sps_d, xs, sps_x)
        need = np.maximum(xs + D + F.exit_lag_vec(sps_x) + 1, sd + D + F.k_slots_vec(LAT_DECIDING, sps_d, "ceil") + 1)
        n = len(a["sl"])
        jx = np.searchsorted(a["sl"], need, "left")
        has = jx < n
        late = has & (a["sl"][np.minimum(jx, n - 1)] >= need + GRACE)
        nop = ~has
        excl = nop & (need + GRACE > seg_end)
        for i in np.flatnonzero(late):  # the engine derived these from the rows before `need` (the clock passed need + GRACE with no print): redo on the truncated history
            sub = {k: v[: jx[i]] for k, v in a.items()}
            one = pool_states(sub, sd[i:i + 1], sps_d[i:i + 1], xs[i:i + 1], sps_x[i:i + 1])
            for k, v in one.items():
                if isinstance(v, dict):
                    for l in v:
                        st[k][l][i] = v[l][0]
                else:
                    st[k][i] = v[0]
        st.update(worst_in_slot_states(pool, a, sd, sps_d, xs, sps_x, need, late, excl, st))  # R7: from the same pool arrays, before the block-edge exclusions are taken out
        keep = ~excl
        for cnt, mask in ((self.excl_cell, excl), (self.late_cell, late & keep), (self.derived_cell, nop & keep)):
            for k, m in zip(*np.unique(ci[mask], return_counts=True)):
                cnt[int(k)] += int(m)
        c["excluded_block_edge"] += int(excl.sum())
        c["derived_last_state"] += int((nop & keep).sum())
        c["late_print_derived"] += int(late.sum())
        st = _take(st, keep)
        st.update({"cell": ci[keep], "blk": np.full(int(keep.sum()), bi, np.int64), "T": np.array([clk["T"][int(h)] for h in hi[keep]], np.int64), "xs": xs[keep], "sd": sd[keep],
                   "why": why[keep], "pool": np.full(int(keep.sum()), zlib.crc32(pool.encode()), np.int64)})
        for k in np.unique(st["cell"]):
            self.acc[int(k)].append(_take(st, st["cell"] == k))
        self.t_price += time.time() - t0

    # --- results
    def results(self) -> dict:
        names = self.blocks
        out_cells = {}
        ndays = max(1, len(self.days))
        for ci, cell in enumerate(self.cells):
            if self.count_only:
                out_cells[cell.name] = {"triggers": int(sum(self.cnt[ci].values())), "attempts_upper_bound": int(sum(self.cnt_ub[ci].values())),
                                        "per_day": {self._d(d): int(self.cnt[ci][d]) for d in sorted(self.days)}, "per_day_after_overlap": {self._d(d): int(self.cnt_ub[ci][d]) for d in sorted(self.days)},
                                        "days_with_trigger": int(sum(1 for d in self.days if self.cnt[ci][d])),
                                        "explore_0814": self._explore_count(ci)}
                if self.exit_used[ci]:  # new keys only when an exit_fire is present: a run without one is byte-identical to before
                    out_cells[cell.name].update(exit_fire_triggers=int(self.exit_used[ci]), attempts_bound_is_time_only=True,
                                                time_only_figure_is="lower_bound_on_attempts")
                continue
            out_cells[cell.name] = self._cell_result(ci, names, ndays)
        return out_cells

    def _explore_count(self, ci: int) -> dict:
        gd = len(self.gdays.get("explore-0814", ()))
        per = self.cnt_ub_expl[ci] / gd if gd else None
        below = (per < self.min_per_day) if (per is not None and self.min_per_day) else None
        if self.exit_used[ci]:
            below = None  # an exit_fire cell: the time-only figure is a LOWER bound on attempts, so it cannot say "under 20" (undetermined; the RULES step-2 drop must not use it)
        return {"days": gd, "attempts_upper_bound": int(self.cnt_ub_expl[ci]), "per_day_upper_bound": per, "below_step2_count": below}

    @staticmethod
    def _d(day: int) -> str:
        return datetime.fromtimestamp(day * 86400, tz=timezone.utc).strftime("%Y-%m-%d")

    def _cell_result(self, ci: int, names: list[str], ndays: int) -> dict:
        parts = self.acc[ci]
        if not parts:
            r0 = {"attempts": 0, "kill": True, "kill_reasons": ["no attempts"], "thin": None, "excluded_block_edge": int(self.excl_cell[ci]), "event_triggered": self.cells[ci].event_triggered,
                  "registration": "KILLED"}
            if self._event_exit(ci):
                r0["event_exit"] = self._event_exit_info(ci, names, 0, 0)
            if self.screen_p:
                r0["screen_p"] = screen_p_cell(None, self.cfg[RENT_DECIDING], None, B=self.screen_p_B)  # n = 0: p None, not a pass
            return r0
        st = _cat(parts)
        n = len(st["ref"])
        day = st["T"] // 86400
        r: dict[str, Any] = {"attempts": n, "pools": int(len(np.unique(st["pool"]))), "exits": {EXIT_REASONS[k]: int((st["why"] == k).sum()) for k in range(4)},
                             "attempts_per_day": n / ndays, "days_in_run": ndays}
        ee = self._event_exit(ci)  # the exit is decided by a tape event: new keys only for such a cell (a run without an exit_fire is byte-identical to before)
        if ee:
            n_event = int((st["why"] == EVENT_CODE).sum())
            r["exits"][EXIT_REASONS[EVENT_CODE]] = n_event
            r["event_exit"] = self._event_exit_info(ci, names, n_event, n)
        stats, legsum, press_c, groups = {}, {}, {}, {}
        gmask = {g: np.array([block_group(names[b]) == g for b in range(len(names))])[st["blk"]] for g in GROUPS}
        gd = len(self.gdays.get("explore-0814", ()))
        r["explore_attempts_per_day"] = int(gmask["explore-0814"].sum()) / gd if gd else None  # RULES step 2 counts on explore-0814 ALONE
        r["thin"] = bool(r["explore_attempts_per_day"] < self.min_per_day) if (self.min_per_day and gd) else None
        ex = int(self.excl_cell[ci])
        r.update(excluded_block_edge=ex, excluded_share=ex / (n + ex), late_print_derived=int(self.late_cell[ci]), derived_last_state=int(self.derived_cell[ci]))
        for lat in LATS:
            for rule in STRAND_RULES:
                for rent, tag in ((RENT_DECIDING, "rent0"), (RENT_CHARGED, "rentC")):
                    L = legs(st, STAKE_SOL * 1e9, lat, rule, self.cfg[rent])
                    for fail in FAILS:
                        stats[f"{lat}|{rule}|{tag}|{fail}"] = stat(L[fail], day, st["blk"], names)
                        if lat == LAT_DECIDING and tag == "rent0":
                            for g, m in gmask.items():
                                groups[f"{g}|{rule}|{fail}"] = stat(L[fail][m], day[m], st["blk"][m], names) if m.any() else None
                    if tag == "rent0":
                        legsum[f"{lat}|{rule}"] = {"fills": int(L["filled"].sum()), "strands": int(L["strand"].sum()), "nofail_mean_pct": float(L["pnl"].mean() / (STAKE_SOL * 1e9) * 100)}
                        press_c[f"{lat}|{rule}"] = L["c"]
        big = {}
        for fail in FAILS:
            Lb = legs(st, STAKE_BIG * 1e9, LAT_DECIDING, "total", self.cfg[RENT_DECIDING])
            big[fail] = stat(Lb[fail], day, st["blk"], names, STAKE_BIG)
        imp = {}
        for lat in LATS:
            fl = legs(st, STAKE_SOL * 1e9, lat, "total", self.cfg[RENT_DECIDING])["filled"]
            fee = F.fee_ppm(st["Qe"][lat], st["Be"][lat])
            imp[str(lat)] = {str(s): (float(np.mean(s * 1e9 * (1 - fee[fl] / 1e6) / st["Qe"][lat][fl])) if fl.any() else None) for s in (STAKE_SOL, STAKE_BIG)}
        r.update(stats=stats, legs=legsum, press_intercept=press_c, stake_1_0_total_rent0_1p9=big, impact=imp, groups=groups)
        subs = {(rule, fail): stats[f"{LAT_DECIDING}|{rule}|rent0|{fail}"] for rule, fail in SUB_CELLS}
        r["gate"] = {f"{rule}|{fail}": gate_row(v) for (rule, fail), v in subs.items()}
        r["kill"], r["kill_reasons"] = kill_verdict(subs, names, {(g.split("|")[0], *g.split("|")[1:]): v for g, v in groups.items()}, excl_share=r["excluded_share"],
                                                    explore_att_per_day=r["explore_attempts_per_day"], min_att_per_day=self.min_per_day)
        cell = self.cells[ci]
        wis: dict[str, Any] = {"measured": False, "reason": "worst-in-slot not computed (WORST_IN_SLOT_AVAILABLE is False)", "sub_cells": None}
        if WORST_IN_SLOT_AVAILABLE:
            wis = worst_in_slot_legs(st, self.cfg[RENT_DECIDING])
            if wis["measured"]:
                bad = [k for k in SUB_CELLS if not wis["sub_cells"]["|".join(k)]["lower"]["mean_sol"] > 0]
                if (cell.event_triggered or ee) and bad:  # an event EXIT is treated like an event entry (the sell is placed on a market event), whatever the entry is
                    r["kill"] = True
                    r["kill_reasons"].append("worst-in-slot (lower of the worst-in-slot and no-guard-rescue legs) mean <= 0 in " + ", ".join("/".join(k) for k in bad))
        r["event_triggered"], r["worst_in_slot"] = cell.event_triggered, wis
        if ee and r["event_exit"]["probe_failures"]:
            r["kill"] = True
            r["kill_reasons"].append(f"exit-causality probe failed on {r['event_exit']['probe_failures']} of the event exits: the exit_fire reads rows after its own fire slot")
        r["registration"] = registration_status(cell.event_triggered or ee, r["kill"], wis["measured"])
        if ee and not r["kill"] and not r["event_exit"]["probe_ok"]:  # never labelled a survivor before its probes have passed (QP on #760)
            r["registration"] = f"PROVISIONAL-BLOCKED (event exit: exit-causality probe not satisfied; needs >= {EXIT_PROBE_MIN} event exits probed per block with 0 failures)"
        if self.screen_p:  # REPORT ONLY, after the verdict and the registration status are final: a new key, nothing reads it
            r["screen_p"] = screen_p_cell(st, self.cfg[RENT_DECIDING], stats, B=self.screen_p_B)
        return r

    def _event_exit(self, ci: int) -> bool:
        """True if the cell's exit is event-driven: declared (Cell.event_exit) or in fact carrying an exit_fire (the stricter reading wins)."""
        return bool(self.cells[ci].event_exit or self.exit_used[ci])

    def _event_exit_info(self, ci: int, names: list[str], n_event: int, n: int) -> dict:
        """Per-cell report of an event exit: the share of attempts that exited on the event, and the exit-causality probe per block (every event exit is probed)."""
        per = {nm: {"event_exits_probed": self.exit_probe.get((ci, bi), [0, 0])[0], "failures": self.exit_probe.get((ci, bi), [0, 0])[1]} for bi, nm in enumerate(names)}
        fails = sum(v["failures"] for v in per.values())
        return {"declared": self.cells[ci].event_exit, "undeclared_use": bool(not self.cells[ci].event_exit and self.exit_used[ci]), "attempts_with_exit_fire": int(self.exit_used[ci]),
                "event_exits": n_event, "fire_share": (n_event / n if n else None), "probe_per_block": per, "probe_failures": fails, "probe_min_per_block": EXIT_PROBE_MIN,
                "probe_ok": bool(per and fails == 0 and all(v["event_exits_probed"] >= EXIT_PROBE_MIN for v in per.values())), "worst_in_slot_kill_applies": True}


# ------------------------------------------------------------------------------------------------------------------------------ rules
class TrivialRule:
    """Loader / pipeline proof, NOT a strategy: every `every`-th buy print of an in-universe pool (per hour), one time exit of `hold_s`. No edge claim of any kind."""

    name = "trivial"

    def __init__(self, every: int = 200, hold_s: float = 300.0, tp: float | None = None, sl: float | None = None, trail: float | None = None) -> None:
        self.every, self.spec = int(every), ExitSpec(hold_s, tp, sl, trail)

    def cells(self) -> list[Cell]:
        return [Cell("trivial")]

    def triggers(self, hour: HourRows, state: RunState) -> list[tuple[str, int, ExitSpec]]:
        out = []
        ok = hour.in_universe
        for i in np.flatnonzero(ok):
            s = hour.rows(int(i))
            idx = np.flatnonzero(hour.isbuy[s])
            for j in idx[self.every - 1::self.every]:
                out.append((str(hour.pools[i]), int(hour.sl[s][j]), self.spec))
        return out


def load_rule(spec: str, kwargs_json: str | None = None) -> Any:
    """'pkg.module:Factory' or '/path/to/file.py:Factory'; Factory(**kwargs) returns the rule."""
    mod, _, fac = spec.rpartition(":")
    if not mod or not fac:
        raise Refused("--rule must be module:Factory or file.py:Factory")
    if mod.endswith(".py"):
        sp = importlib.util.spec_from_file_location("screen_rule_" + Path(mod).stem, mod)
        m = importlib.util.module_from_spec(sp)  # type: ignore[arg-type]
        sp.loader.exec_module(m)  # type: ignore[union-attr]
    else:
        m = importlib.import_module(mod)
    return getattr(m, fac)(**(json.loads(kwargs_json) if kwargs_json else {}))


def causal_probe(rule: Any, hour: HourRows, state: RunState, pool: str, slot: int, exit_fire: bool = False) -> bool:
    """True if `rule` still fires (pool, slot) when the hour is cut after `slot` (no look-ahead past the decision slot). With exit_fire=True the EXIT is probed too: for every
    spec the rule hands out for (pool, slot) on the whole hour that carries an exit_fire, the predicate is asked at every print slot of the pool's rows (previous hour + this hour, up to
    the hour's end); on a fire the prefix probe runs (fires again on rows <= the fire slot, does NOT fire at the previous slot asked: exit_fire_probe). THE ENTRY-HOUR RE-RUN (the manager's decision on #760,
    QP's option b): the rule's triggers() is re-run on the hour CUT after `slot`, with a deep copy of `state` (the state as it was at the start of the hour, as for the entry check); the
    ExitSpec that cut run returns for the same (pool, slot, cell) is streamed over the same full rows, and its (exit slot, reason) must equal the full-hour spec's, fire or no fire. A
    rule that keeps the hour's later rows in its state or closure and gates its fire on the exact slot passes the prefix probe and FAILS here. Scope: the sampled triggers, the standard of the
    entry probe. The priced screen streams and prefix-probes every event exit itself (Screen.exit_probe)."""
    state = dataclasses.replace(state, scratch=copy.deepcopy(state.scratch), first_seen=dict(state.first_seen))  # the probe must not change a rule that writes to scratch
    keep = hour.sl <= slot
    pid = np.repeat(np.arange(len(hour.pools)), np.diff(hour.ptr))[keep]
    ptr = np.searchsorted(pid, np.arange(len(hour.pools) + 1), "left").astype(np.int64)
    cut = dataclasses.replace(hour, ptr=ptr, _index=None, **{k: getattr(hour, k)[keep] for k in ("sl", "tx", "ev", "isbuy", "sol", "tok", "q", "b", "bt", "trader", "v")})
    if not exit_fire:
        return any(p == pool and int(s) == int(slot) for p, s, _ in rule.triggers(cut, state))
    entry_seen, res = exit_entry_hour_rerun(rule, hour, cut, state, pool, int(slot))
    return entry_seen and all(r["ok"] for r in res)


def _stream_outcome(spec: Any, a: dict, pp: np.ndarray, slot: int, rate: float, covered_to: int) -> tuple:
    """Stream one spec's exit over the rows in `a`: ((exit slot, reason) or (None, 0) if undecided in the data, prefix probe ok)."""
    pos = _Pos(spec, slot, rate, 1.0 / rate)
    pos.run(a, pp, covered_to)
    return (pos.xs, pos.why), (True if pos.why != EVENT_CODE else exit_fire_probe(pos, a, pp, pos.xs))


def exit_entry_hour_rerun(rule: Any, hour: HourRows, cut: HourRows, state: RunState, pool: str, slot: int) -> tuple[bool, list[dict]]:
    """The entry-hour re-run (see causal_probe). `cut` = `hour` cut after `slot`, `state` = the state at the start of the hour (this function deep-copies it for each run). Returns
    (the cut run returned the entry, one dict per exit_fire spec the FULL-hour run hands out for (pool, slot): {"cell", "full": (xs, why), "cut": (xs, why) | None, "prefix_ok", "same", "ok"}). A cut run that returns no exit_fire for that
    cell gives cut None (not ok). The cut run is made on `copy.deepcopy(rule)` taken BEFORE the full-hour run (which uses the caller's rule), so state the rule keeps on itself cannot carry
    the full hour into the cut run, and the full-hour specs are streamed before the cut run starts. The rule must be deep-copyable. `state` must be the state as it was at the START of
    the hour (before triggers(h)): a post-triggers state shows both runs the same leaked scratch."""
    cp = lambda: dataclasses.replace(state, scratch=copy.deepcopy(state.scratch), first_seen=dict(state.first_seen))  # noqa: E731
    rule_cut = copy.deepcopy(rule)  # BEFORE the full-hour run: state the rule keeps on itself (an attribute, a memo keyed by hour.idx, first write wins) cannot carry the full hour into the cut run
    i = hour.pool_index().get(pool)
    if i is None or not hour.n:
        return False, [{"cell": None, "full": None, "cut": None, "prefix_ok": False, "same": False, "ok": False}]
    chunks = [Screen._chunk(hour, i)]
    prev = state.prev
    if prev is not None and pool in prev.pool_index():
        chunks.insert(0, Screen._chunk(prev, prev.pool_index()[pool]))
    a = {k: np.concatenate([c[k] for c in chunks]) for k in chunks[0]}
    a["ib"] = a["ib"].astype(bool)
    pp = post_price(a)
    rate = hour.slots_per_s
    out: dict[Any, dict] = {}
    for p, s_, spec in list(rule.triggers(hour, cp())):
        if p != pool or int(s_) != slot or getattr(spec, "exit_fire", None) is None:
            continue
        cell = getattr(spec, "cell", None)
        try:
            full, prefix_ok = _stream_outcome(spec, a, pp, slot, rate, hour.max_slot)
        except Refused:
            full, prefix_ok = None, False
        out[cell] = {"cell": cell, "full": full, "cut": None, "prefix_ok": prefix_ok, "same": False, "ok": False}
    cut_trigs = list(rule_cut.triggers(cut, cp()))
    entry_seen = any(p == pool and int(s_) == slot for p, s_, _ in cut_trigs)
    for p, s_, spec in cut_trigs:
        cell = getattr(spec, "cell", None)
        if p != pool or int(s_) != slot or cell not in out or getattr(spec, "exit_fire", None) is None:
            continue
        try:
            out[cell]["cut"], _ = _stream_outcome(spec, a, pp, slot, rate, hour.max_slot)
        except Refused:
            out[cell]["cut"] = None
    for r in out.values():
        r["same"] = r["cut"] is not None and r["full"] == r["cut"]
        r["ok"] = bool(r["same"] and r["prefix_ok"])
    if not out:  # no exit_fire spec on the full-hour run: nothing to re-run; the entry check alone decides
        return entry_seen, [{"cell": None, "full": None, "cut": None, "prefix_ok": True, "same": True, "ok": True}]
    return entry_seen, list(out.values())


# ------------------------------------------------------------------------------------------------------------------------------ output
def _f(x: Any, p: int = 2) -> str:
    return "na" if x is None else f"{x:.{p}f}"


def table_md(res: dict, blocks: list[str], meta: dict) -> str:
    L = [f"# arena_screen TABLE ({meta['rule']}): IN-SAMPLE exploration screen, not evidence, no profit claim", "",
         "Deciding leg: 1.9 s, 0.25 SOL, rent 0 (DEC-031 Am.2 universe enforced), pool age >= 360 s enforced, feed delay 7. KILL (hunt23 Amendment 1) is evaluated on all four sub-cells "
         "{flat, pressure} x {sc, total}. Blocks: " + "; ".join(f"B{i + 1}={b}" for i, b in enumerate(blocks)) + ".", "",
         *AUTHOR_NOTES, "",
         "## 1. Strand rule total (deciding), pooled over all blocks", "",
         "| cell | att | fills | days(+) | flat mean% | flat CIlo% | flat tot | ex3 | exbest | press mean% | press CIlo% | press tot | ex3 | exbest | explore-0814 alone flat/press mean% | September pooled flat/press mean% | block mean% flat/press | impact .25/1.0 % | 1.0 SOL flat/press mean% | flags |",
         "|" + "---|" * 20]
    gm = lambda r, g, rule, fail: _f((r["groups"].get(f"{g}|{rule}|{fail}") or {}).get("mean_pct"))  # noqa: E731
    for nm, r in res.items():
        if r["attempts"] == 0:
            L.append(f"| {nm} | 0 |" + " |" * 17 + " KILL (no attempts) |")
            continue
        a, b = r["stats"]["1.9|total|rent0|flat"], r["stats"]["1.9|total|rent0|press"]
        pb = " ".join(f"B{i + 1}:{_f(a['per_block'][bn]['mean_pct'])}/{_f(b['per_block'][bn]['mean_pct'])}" for i, bn in enumerate(blocks))
        big = r["stake_1_0_total_rent0_1p9"]
        im = r["impact"]["1.9"]
        flags = ("KILL " if r["kill"] else r["registration"] + " ") + ("THIN " if r["thin"] else "") + ("EVENT " if r["event_triggered"] else "GRID ") + ("EVENT-EXIT " if r.get("event_exit") else "")
        L.append(f"| {nm} | {r['attempts']} | {r['legs']['1.9|total']['fills']} | {a['days']}({a['pos_days']}) | {_f(a['mean_pct'])} | {_f(a['ci_lo_pct'])} | {_f(a['total'], 3)} | {_f(a['ex_top3'], 3)} | "
                 f"{_f(a['ex_best_day'], 3)} | {_f(b['mean_pct'])} | {_f(b['ci_lo_pct'])} | {_f(b['total'], 3)} | {_f(b['ex_top3'], 3)} | {_f(b['ex_best_day'], 3)} | "
                 f"{gm(r, 'explore-0814', 'total', 'flat')}/{gm(r, 'explore-0814', 'total', 'press')} | {gm(r, 'september', 'total', 'flat')}/{gm(r, 'september', 'total', 'press')} | {pb} | "
                 f"{_f(100 * im['0.25'], 3)}/{_f(100 * im['1.0'], 3)} | {_f(big['flat']['mean_pct'])}/{_f(big['press']['mean_pct'])} | {flags.strip()} |")
    L += ["", "## 2. The four deciding sub-cells with the pooled in-sample gate columns", "",
          "Each entry: mean% / CI90 LB% / dates(+) majority? / ex-top-3 SOL / ex-best-day SOL. Explore-alone and September-pooled entries: mean%.", "",
          "| cell | " + " | ".join(f"{rule}/{fail}" for rule, fail in SUB_CELLS) + " |", "|---|" + "---|" * len(SUB_CELLS)]
    for nm, r in res.items():
        if r["attempts"] == 0:
            continue
        cols = []
        for rule, fail in SUB_CELLS:
            g = r["gate"][f"{rule}|{fail}"]
            cols.append(f"{_f(g['mean_pct'])} / {_f(g['ci_lo_pct'])} / {g['pos_days']}/{g['days']} {'maj' if g['majority_dates'] else 'no-maj'} / {_f(g['ex_top3'], 3)} / {_f(g['ex_best_day'], 3)} "
                        f"[expl {gm(r, 'explore-0814', rule, fail)}, sept {gm(r, 'september', rule, fail)}]")
        L.append(f"| {nm} | " + " | ".join(cols) + " |")
    L += ["", "## 3. Reported legs (not deciding): mean% (total SOL), flat / press", "",
          "| cell | 1.3 s total rent0 | 1.9 s total rent-charged | 1.3 s sc rent0 | strands 1.9 total | KILL reasons |", "|---|---|---|---|---|---|"]
    for nm, r in res.items():
        if r["attempts"] == 0:
            continue
        g = lambda k: f"{_f(r['stats'][k]['mean_pct'])} ({_f(r['stats'][k]['total'], 3)})"  # noqa: E731
        L.append(f"| {nm} | {g('1.3|total|rent0|flat')} / {g('1.3|total|rent0|press')} | {g('1.9|total|rentC|flat')} / "
                 f"{g('1.9|total|rentC|press')} | {g('1.3|sc|rent0|flat')} / {g('1.3|sc|rent0|press')} | {r['legs']['1.9|total']['strands']} | {'; '.join(r['kill_reasons']) or '-'} |")
    L += ["", "## 4. Worst-in-slot (R7): mean SOL per trade at the deciding cell (1.9 s, 0.25 SOL, rent 0), END / worst-in-slot / no guard rescue / **LOWER of the two**", "",
          "An EVENT cell is KILLED when the LOWER mean is <= 0 in any sub-cell; a GRID cell reports it only. `not measured` = the tape states did not reproduce the kit's END states "
          "(no figure is printed and an event cell stays PROVISIONAL-BLOCKED). CIlo = the lower of the two legs' trade-level CI90 bounds (arena_score's bootstrap). In-sample, no profit claim.", "",
          "| cell | kind | " + " | ".join(f"{rule}/{fail}" for rule, fail in SUB_CELLS) + " | sub-cells with lower mean <= 0 | guard flips sc/total |", "|---|---|" + "---|" * (len(SUB_CELLS) + 2)]
    for nm, r in res.items():
        w = r.get("worst_in_slot")
        if r["attempts"] == 0 or w is None:
            continue
        kind = "EVENT" if r["event_triggered"] else ("EVENT-EXIT" if r.get("event_exit") else "GRID")
        if not w["measured"]:
            L.append(f"| {nm} | {kind} | " + " | ".join("not measured" for _ in SUB_CELLS) + " | - | - |")
            continue
        cols = []
        for rule, fail in SUB_CELLS:
            c = w["sub_cells"][f"{rule}|{fail}"]
            cols.append(f"{_f(c['end']['mean_sol'], 4)} / {_f(c['worst_in_slot']['mean_sol'], 4)} / {_f(c['no_guard_rescue']['mean_sol'], 4)} / **{_f(c['lower']['mean_sol'], 4)}** "
                        f"(CIlo {_f(c['lower']['ci90_lo_sol'], 4)}, {c['lower']['leg_of_mean']})")
        nonpos = [f"{rule}/{fail}" for rule, fail in SUB_CELLS if not w["sub_cells"][f"{rule}|{fail}"]["lower"]["mean_sol"] > 0]
        fl = w["diag"]["guard_flips"]
        L.append(f"| {nm} | {kind} | " + " | ".join(cols) + f" | {', '.join(nonpos) or '-'} | {fl['sc']}/{fl['total']} |")
    if any("screen_p" in r for r in res.values()):  # appended only: every line above is the same with and without screen p
        L += screen_p_md(res)
    ev = {nm: r["event_exit"] for nm, r in res.items() if r.get("event_exit")}
    if ev:
        L += ["", "## 6. Event exits (ExitSpec.exit_fire): share of attempts that exited on the event, and the exit-causality probe", "",
              "An event exit is treated like an event entry for the worst-in-slot kill (section 4: EVENT-EXIT; section 5 is the screen p, which an event-exit cell gets like any cell), whatever the entry. The kit asks the predicate "
              f"at every print slot on a view cut there, hour by hour before the rule sees the hour. The prefix probe, on EVERY event exit, needs it to fire again on the rows <= the fire slot and NOT at the previous slot asked (skipped for the first slot asked): registration needs >= {EXIT_PROBE_MIN} event exits probed per block and 0 failures; a cell whose probes are not satisfied is PROVISIONAL-BLOCKED, never a survivor. Reflection (`.base`, frames, gc) is not detectable by any probe: source review of the rule module before PHASE 2 GO. Ties: a tp / sl / trail on the fire "
              "slot keeps its own code. Not a price claim; in-sample.", "",
              "| cell | attempts | event exits | fire share | probe failures | probed per block | probe ok | declared |", "|---|---|---|---|---|---|---|---|"]
        for nm, e in ev.items():
            pb = " ".join(f"B{i + 1}:{v['event_exits_probed']}" for i, v in enumerate(e["probe_per_block"].values()))
            L.append(f"| {nm} | {res[nm]['attempts']} | {e['event_exits']} | {_f(e['fire_share'], 3)} | {e['probe_failures']} | {pb} | {'yes' if e['probe_ok'] else 'NO'} | "
                     f"{'yes' if e['declared'] else 'NO (carries an exit_fire undeclared: treated as declared)'} |")
    return "\n".join(L) + "\n"


def counts_md(res: dict, meta: dict) -> str:
    days = sorted({d for r in res.values() for d in r["per_day"]})
    L = [f"# COUNTS ({meta['rule']}): count-only, no price read", "",
         *(["**SUBSET: --hours " + ", ".join(meta["provenance"]["hours_subset_requested"]) + ". Not a full count; do not use it for the RULES step-2 count.**", ""] if meta.get("hours_subset") else []),
         "Triggers after the universe and pool-age refusals; `after overlap` applies the time-only one-open-position rule (an upper bound of attempts). RULES step 2 counts on explore-0814 ALONE "
         "(a cell under 20 attempts/day there is dropped WITHOUT pricing; the author drops it, the kit only flags it, as does the C1-NF drop).", "",
         "| cell | triggers | after overlap | days>=1 | explore-0814 att/day (upper bound) | step 2 | " + " | ".join(d[5:] for d in days) + " |", "|---|---|---|---|---|---|" + "---|" * len(days)]
    for nm, r in res.items():
        e = r["explore_0814"]
        per = "na" if e["per_day_upper_bound"] is None else f"{e['per_day_upper_bound']:.1f}"
        flag = "na" if e["below_step2_count"] is None else ("DROP (<20)" if e["below_step2_count"] else "ok")
        flag += " (time-only: exit_fire present; LOWER bound on attempts, not for the step-2 drop)" if r.get("attempts_bound_is_time_only") else ""
        L.append(f"| {nm} | {r['triggers']} | {r['attempts_upper_bound']} | {r['days_with_trigger']} | {per} | {flag} | " + " | ".join(str(r["per_day_after_overlap"].get(d, 0)) for d in days) + " |")
    if any(r.get("attempts_bound_is_time_only") for r in res.values()):
        L += ["", "**exit_fire present (cells marked `time-only`):** the overlap rule above holds every position for its full time cap and cannot run the event exit, so the figure is a TIME-ONLY "
              "bound on the hold. **For an exit_fire cell it is a LOWER bound on attempts** (an event exit frees the pool earlier, so the real attempts are at least this many, never fewer), "
              "**so the RULES step-2 drop must not use it.** Count such a cell with the rule's own structure scan and report the kit's figure beside it."]
    return "\n".join(L) + "\n"


def run(args: argparse.Namespace) -> dict:
    if not getattr(args, "no_screen_p", False):  # the DECLARE stake and B are checked before anything is listed, read or written
        screen_p_check(getattr(args, "declared_stake_sol", STAKE_SOL), getattr(args, "screen_p_draws", SCREEN_P_B))
    out = check_out_dir(args.out)
    if args.hours and not args.count_only:
        raise Refused("hours_subset_refused_when_pricing: --hours is for --count-only; a priced screen runs every hour of the four blocks (a subset is not a screen of record)")
    specs, reals = [], []
    for b in args.block:
        kind = check_block_path(b, args.count_only)
        real = os.path.realpath(b)
        if any(_under(real, o) or _under(o, real) for o in reals):
            raise Refused(f"repeated_or_nested_block: {b} (each block once; explore-0814 is ONE block, not its workers)")
        reals.append(real)
        specs.append((b, kind, discover(b, kind, args.hours)))
    if not args.count_only and sorted(reals) != sorted(os.path.realpath(r) for r in BLOCK_ROOTS):
        raise Refused("pricing_needs_the_four_blocks: pass each of " + ", ".join(BLOCK_ROOTS) + " once, whole (blocks positive are counted out of these four)")
    kinds = {k for _, k, _ in specs}
    rule_path = rule_source_path(args.rule)
    rule_sha = check_rule_source(rule_path)
    vpaths = check_vmap_paths(args.vmap or VMAP_DEFAULT) if "clean" in kinds else []
    before = set(sys.modules)
    rule = load_rule(args.rule, args.rule_kwargs)
    check_loaded_modules(before)
    vmap = VMap(vpaths) if vpaths else None
    Path(out).mkdir(parents=True, exist_ok=True)
    scr = Screen(rule, count_only=args.count_only, min_attempts_per_day=args.min_attempts_per_day, screen_p=not getattr(args, "no_screen_p", False),
                 screen_p_draws=getattr(args, "screen_p_draws", SCREEN_P_B), declared_stake_sol=getattr(args, "declared_stake_sol", STAKE_SOL))
    t0 = time.time()
    names = []
    for bi, (b, kind, files) in enumerate(specs):
        name = os.path.relpath(os.path.realpath(b), CLEAN_VIEW) if kind == "clean" else "arena-tape"
        names.append(name)
        scr.run_block(bi, name, iter_hours(files, name, "arena" if kind == "arena" else "backfill", vmap, args.workers))
    check_loaded_modules(before)
    wall = time.time() - t0
    cells = scr.results()
    prov = {"kit": kit_provenance(), "rule": {"spec": args.rule, "source_path": str(rule_path.resolve()), "source_sha256": rule_sha, "kwargs": args.rule_kwargs},
            "vmap": [{"path": v, "sha256": file_sha256(v)} for v in vpaths], "hours_subset_requested": list(args.hours) if args.hours else None,
            "hours": {n: [t.strftime("%Y-%m-%dT%H") for t, _ in sp[2]] for n, sp in zip(names, specs)}}
    meta = {"schema": SCHEMA, "rule": getattr(rule, "name", args.rule), "count_only": args.count_only, "blocks": scr.blocks, "hours": scr.hours, "rows_kept": scr.rows,
            "hours_subset": bool(args.hours), "full_screen_of_record": bool(not args.count_only and not args.hours), "hours_with_missing_timestamp": scr.flag_hours, "provenance": prov,
            "counters": dict(scr.counters), "wall_s": wall, "rows_pumpswap_per_s": scr.counters["rows_pumpswap"] / wall if wall else None, "rows_kept_per_s": scr.rows / wall if wall else None,
            "seconds": {"load_wait": scr.t_load, "rule": scr.t_rule, "price": scr.t_price}, "stack": scr.cfg[RENT_DECIDING].as_dict(), "workers": args.workers,
            "written_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    meta["screen_p"] = {"enabled": bool(scr.screen_p and not args.count_only), "report_only": True, "B": scr.screen_p_B, "seed": SCREEN_P_SEED, "stake_sol": STAKE_SOL, "source": SCREEN_P_SOURCE}
    doc = {"meta": meta, "cells": cells}
    name = "counts" if args.count_only else "results"
    Path(out, f"{name}.json").write_text(json.dumps(doc, indent=1, default=float))
    Path(out, "COUNTS.md" if args.count_only else "TABLE.md").write_text(counts_md(cells, meta) if args.count_only else table_md(cells, scr.blocks, meta))
    return doc


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rule", required=True, help="module:Factory or file.py:Factory")
    ap.add_argument("--rule-kwargs", help="JSON kwargs for the factory")
    ap.add_argument("--block", action="append", required=True, help="a block path (repeat for several blocks)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--hours", nargs="*", help="restrict to these hours, YYYY-MM-DDTHH")
    ap.add_argument("--count-only", action="store_true")
    ap.add_argument("--workers", type=int, default=1, help="hour loader processes (<= 2 on mal-fast-0)")
    ap.add_argument("--min-attempts-per-day", type=float, default=20.0)
    ap.add_argument("--vmap", nargs="*", help="V map JSON files, first non-null wins (default pool_v_0814, pool_v_0909)")
    ap.add_argument("--screen-p-draws", type=int, default=SCREEN_P_B, help=f"B for the EXP-028 screen p (report only), {SCREEN_P_B_MIN}..{SCREEN_P_B_MAX}; seed 1, a fresh rng per sub-cell")
    ap.add_argument("--declared-stake-sol", type=float, default=STAKE_SOL, help="the stake in the DECLARE; the screen p is defined at 0.25 SOL only and a priced run REFUSES any other")
    ap.add_argument("--no-screen-p", action="store_true", help="do not compute the screen p (the results are otherwise byte-identical)")
    args = ap.parse_args(argv)
    try:
        doc = run(args)
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    m = doc["meta"]
    print(json.dumps({k: m[k] for k in ("rule", "count_only", "hours", "rows_pumpswap_per_s", "rows_kept_per_s", "wall_s", "seconds", "counters")}, indent=1, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
