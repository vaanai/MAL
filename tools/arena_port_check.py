#!/usr/bin/env python3
"""arena_port_check: does the streaming arena port of a screened rule decide the same ENTRIES as the batch kit rule? PAPER only. No key, no RPC, no wallet, no model.

    python -m tools.arena_port_check --kit-rule pkg.mod:Factory --port NAME|pkg.mod[:make_rule] --hours tape-2026-10-19T08.jsonl.zst ... --out DIR
    options: --kit-kwargs JSON  --port-kwargs JSON  --cell-map '{"port cell": "kit cell"}'  --min-decisions 1        exit code: 0 PASS, 1 FAIL, 2 refused

WHY. A screen survivor is written against the batch kit (tools/arena_screen.py: `triggers(hour, state) -> (pool, decision_slot, ExitSpec)`) and must be re-written as a
streaming rule (tools/arena_rules.py `Rule`: `on_row` / `on_tick` return Buy / Sell) before it can be a frozen arena book. QP asks for the frozen bot's decision-equivalence
replay at registration (PR #752). Nothing else checks that the port enters where the screened rule entered. This does, for ENTRY DECISIONS only.

DATA ROUTE (chosen: arena-format hours, fed unchanged to both sides)
  * The batch kit already reads the arena tape format: `arena_screen.build_hour(..., fmt="arena")` parses `tape-<hour>.jsonl[.zst]` rows (t_recv_ms order, event_ts for the
    pool-age clock, V on the row). The streaming side reads the same files through `tools.arena_state.ArenaState`. So ONE closed arena-format hour is handed to both, with no
    converter. This is the same file the bot will see, which is the point of a decision-equivalence check.
  * Where such hours live: the closed arena tape /data/mal/arena/tape/tape-<hour>.jsonl.zst (live feed, first hour 2026-10-19T08), and synthetic hours written by
    tools/arena_rules_testkit.py / tools/arena_port_check_testkit.py (the tests). The exploration blocks the survivors were SCREENED on (/data/mal/clean-view/<block>/...,
    August and September) are NOT arena format (block_time and tx_index instead of t_recv_ms, no quote_mint, V from a pool map, no pool_create). No converter exists:
    tools/arena_parity.py reads an exploration PARQUET hour (/data/mal/audit-1008/tape/trades/<hour>.parquet) and builds engine-only dicts inline (`rows_for`: synthetic
    t_recv_ms = day base + slot x 400, no type / prog / quote_mint / V), which ArenaState cannot consume, and arena_j3_explore replays closed ARENA hours only.
  * FOLLOW-UP, not in this PR: a clean-view -> arena-row adapter (t_recv_ms from block_time + slot order, quote_mint WSOL, V from the pool map, original trader strings) so
    the very hours of the screen can be replayed. The comparator below is source-agnostic: it takes hour files, so the adapter only has to write `tape-<hour>.jsonl` files.
    Until then the check runs on closed arena hours (any that the D2/D3 seal allows) and on the synthetic tape.

WHAT IS COMPARED (per hour, entry decisions only; no price, no outcome, no ledger)
  Canonical line = pool | decision_slot | cell | hold_s | tp | sl | trail | event_exit, lines sorted. Per hour: n per side, md5 per side, and the first 20 differences listed
  by (hour, pool, slot, cell) with a kind (only_kit, only_port, count_differs, fields_differ + the NAMES of the differing fields), never a value.
  KIT SIDE = the kit's own pipeline, not a re-implementation: `DecisionScreen` subclasses `arena_screen.Screen` and replaces only the pricing step (`_price_store`) with a
  recorder, so `run_block` (hour loop, state.prev / first_seen, Am.2 universe and V-band refusals, the 360 s pool-age refusal) and the exit resolver `resolve_exit` (tp / sl /
  trail can free a pool before the time exit) are the kit's. The per-(cell, pool) overlap rule (a trigger before exit decision + cell.cooldown_s is dropped) is the kit's loop
  copied line for line from `_price_store` (the only copy; the kit sha256 is recorded, and a test pins the counter it shares). Entry decisions the kit would EXCLUDE from
  pricing at a block edge are still decisions here: pricing is out of scope.
  PORT SIDE = the arena host (`arena_rules.Host`) in replay mode over the same files, sim clock, 1 s ticks from the first hour start to the end of the last, with a decision-only
  engine (`DecisionOnlyEngine`: buy and sell are accepted at once, nothing is priced). The entry rows of the decision log are the port's decisions.
PORT CONTRACT. The host knows nothing of cells or exit specs, so a port declares them in the `meta` of every Buy: `cell` (default: the rule's bot_id), `hold_s`, and where used
  `tp`, `sl`, `trail` and `event_exit` (true when the port also exits on an event the kit's ExitSpec cannot express, for example a followed wallet's sell; the kit side reads
  `getattr(spec, "event_exit", False)`). A missing `hold_s` shows as a field difference. `--cell-map` renames port cells to kit cells. The port module is loaded like the host
  loads it (`arena_rules.load_rules` for a name in RULE_MODULES, so it works for registered books), else `module[:factory]` with factory(**kwargs) returning a Rule or a list
  (default factory `make_rule`), so a port can be checked BEFORE its line is added to RULE_MODULES.
  The decision slot is the host's (highest trade slot seen when the order is decided); the kit's is the rule's number. A port that decides on a tick sees the slot of the last
  row before the tick; a kit rule that names another slot will differ here, and should.

DETERMINISM. The host replay runs TWICE on the same input into two fresh scratch trees; the md5 of the whole decision log (every kind of row) must be identical.

VERDICT. PASS only if the md5 of the entry lines is equal on every hour, the two replays are identical, the host recorded no hook error or fatal, no decision falls outside the
hours, and each side has at least --min-decisions decisions (an equivalence of two empty sets proves nothing). Otherwise FAIL, with the counts. A PASS is NOT a profit claim and
it is not a price comparison: it says only that the two programs entered on the same prints of the hours given, so it is as strong as those hours exercise the rule.

SCOPE (what a PASS does NOT cover)
  * EXITS. The kit resolves a time exit by slot arithmetic at the hour's own slot rate (and tp / sl / trail on the post-trade price of a print at or after decision + k_slow + 1);
    the bot holds in ms on a 1 s tick, so exit instants differ by construction and are not compared. Because the stub engine closes a position at the tick that decides the sell,
    a second entry on the same pool inside the 1.9 s entry leg is the one place the stub and the real engine can differ.
  * PRICES, fills, fail models, exclusions, the 1 s tick rows of the last second of the last hour (event rules see them, tick rules do not).
  * Hours not given. Hours must be consecutive UTC hours, both sides start cold at the first (state.prev None, first_seen empty; ArenaState empty), so a pool is aged from its
    first print in the given hours on both sides.
  * The kit's per-cell `event_triggered` declaration (R7) is a property of the cell, not of an entry; it is not compared.

REFUSALS (before anything is read or written; exit code 2): any input or output path (hour files, rule source files, --out) whose raw, absolute or resolved text contains
forward-, truth-, fresh-08, exp011-, exp012-forward, tip-tape, /var/lib/mal/sealed, shadow, canary, probe, walk, _shakedown or kalshi; an --out inside /data/mal/arena or
/data/mal/clean-view (a counted decision is never regenerated) or one that already holds a report or scratch tree; an hour file inside /data/mal/arena that is not a closed
`tape-<hour>.jsonl.zst` directly in /data/mal/arena/tape; an hour inside [2026-10-02T10, 2026-10-16T01) (D2/D3 seal); duplicate or non-consecutive hours; a rule source that
mentions a banned model family (DEC-031 3.1, the kit's own scan). It never opens a ledger, bots.json, runner-status.json, /data/mal/blocks or a results file.
"""

from __future__ import annotations

if __package__ in (None, ""):  # `python tools/arena_port_check.py`
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import hashlib
import importlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator, Sequence

import numpy as np

from tools import arena_rules as R
from tools import arena_screen as S
from tools.arena_declog import DecisionLog
from tools.arena_paper import ArenaRefused
from tools.arena_state import ArenaState

SCHEMA = "arena_port_check_v1"
REPO_ROOT = Path(__file__).resolve().parent.parent
ARENA_ROOT = R.ARENA_OUTPUT_ROOT  # /data/mal/arena: module globals, so the tests can point them at a temp tree
ARENA_TAPE = f"{ARENA_ROOT}/tape"
CLEAN_VIEW = S.CLEAN_VIEW
FORBIDDEN = ("forward-", "truth-", "fresh-08", "exp011-", "exp012-forward", "tip-tape", "/var/lib/mal/sealed", "shadow", "canary", "probe", "walk", "_shakedown", "kalshi")
SEALED_HOURS = (datetime(2026, 10, 2, 10, tzinfo=timezone.utc), datetime(2026, 10, 16, 1, tzinfo=timezone.utc))  # D2/D3: no hour in [10-02T10, 10-16T01)
HOUR_RE = re.compile(r"^tape-(\d{4}-\d{2}-\d{2}T\d{2})\.jsonl(\.zst)?$")
HOUR_MS = 3_600_000
FIELDS = ("pool", "slot", "cell", "hold_s", "tp", "sl", "trail", "event_exit")
MAX_DIFFS = 20
FROZEN_SHA = "portcheck0000"  # the scratch host's label; nothing is registered anywhere
OUT_FILES = ("port_check.json", "scratch-a", "scratch-b")


class PortCheckRefused(Exception):
    """A guard refused: nothing was read or written."""


# ------------------------------------------------------------------------------------------------------------------------------ guards
def _forbidden_word(p: str) -> str | None:
    low = p.lower()
    return next((w for w in FORBIDDEN if w in low), None)


def _under(p: str, root: str) -> bool:
    return p == root or p.startswith(root.rstrip("/") + "/")


def check_path_words(path: str | os.PathLike, what: str) -> None:
    raw = str(path)
    for p in (raw, os.path.abspath(raw), os.path.realpath(raw)):
        w = _forbidden_word(p)
        if w:
            raise PortCheckRefused(f"path_forbidden_word:{w}: {what} {path}")


def hour_of(name: str) -> datetime | None:
    m = HOUR_RE.match(name)
    return datetime.strptime(m.group(1), "%Y-%m-%dT%H").replace(tzinfo=timezone.utc) if m else None


def check_hour_files(files: Sequence[str | os.PathLike]) -> list[tuple[datetime, Path]]:
    """The hour files to compare, oldest first: consecutive UTC hours, closed, outside the seal. Raises PortCheckRefused. Reads no file content."""
    if not files:
        raise PortCheckRefused("no_hours: give at least one tape hour")
    out: list[tuple[datetime, Path]] = []
    for f in files:
        check_path_words(f, "hour file")
        p = Path(f)
        t = hour_of(p.name)
        if t is None:
            raise PortCheckRefused(f"not_an_arena_hour_file: {p.name} (expected tape-<YYYY-MM-DDTHH>.jsonl or .jsonl.zst)")
        if not p.is_file():
            raise PortCheckRefused(f"hour_file_missing: {f}")
        raw, real = os.path.abspath(str(p)), os.path.realpath(str(p))
        if _under(raw, ARENA_ROOT) or _under(real, ARENA_ROOT):
            if not (p.name.endswith(".jsonl.zst") and raw == real and os.path.dirname(real) == os.path.realpath(ARENA_TAPE)):
                raise PortCheckRefused(f"arena_path_refused: only closed tape-<hour>.jsonl.zst directly in {ARENA_TAPE}: {f}")
        if p.name.endswith(".jsonl.zst") and (p.with_suffix("").exists() or Path(str(p) + ".tmp").exists()):
            raise PortCheckRefused(f"hour_not_closed: {f}")
        if SEALED_HOURS[0] <= t < SEALED_HOURS[1]:
            raise PortCheckRefused(f"sealed_hour (D2/D3 [2026-10-02T10, 2026-10-16T01)): {f}")
        out.append((t, p))
    out.sort(key=lambda x: x[0])
    for (a, _), (b, _) in zip(out, out[1:]):
        if b == a:
            raise PortCheckRefused(f"duplicate_hour: {a:%Y-%m-%dT%H}")
        if b != a + timedelta(hours=1):
            raise PortCheckRefused(f"hours_not_consecutive: {a:%Y-%m-%dT%H} then {b:%Y-%m-%dT%H} (both sides start cold, so the hours must be consecutive)")
    return out


def check_out_dir(out: str | os.PathLike) -> Path:
    check_path_words(out, "--out")
    raw, real = os.path.abspath(str(out)), os.path.realpath(str(out))
    for p in (raw, real):
        if _under(p, ARENA_ROOT) or _under(p, CLEAN_VIEW):
            raise PortCheckRefused(f"out_dir_inside_protected_tree: {out} (a counted decision is never regenerated)")
    d = Path(real)
    if d.exists() and not d.is_dir():
        raise PortCheckRefused(f"out_not_a_directory: {out}")
    for n in OUT_FILES:
        if (d / n).exists():
            raise PortCheckRefused(f"out_not_empty: {d / n} exists (a fresh --out per run)")
    return d


def file_sha256(path: str | os.PathLike) -> str:
    return S.file_sha256(path)


def git_state(repo: Path = REPO_ROOT) -> dict[str, Any]:
    """HEAD and whether tools/ has tracked changes or untracked files (the tool and the rule files live there)."""
    try:
        head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=20, check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=normal", "--", "tools"], capture_output=True, text=True, timeout=20,
                                    check=True).stdout.strip())
        return {"git_head": head, "git_dirty": dirty}
    except (OSError, subprocess.SubprocessError):
        return {"git_head": None, "git_dirty": None}


# ------------------------------------------------------------------------------------------------------------------------------ canonical lines
def _num(x: Any) -> str:
    if x is None:
        return "-"
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return f"!{x!r}"  # a junk value stays visible and never equals a number
    return format(float(x), ".12g")


def make_line(pool: Any, slot: Any, cell: Any, hold: Any, tp: Any, sl: Any, trail: Any, event_exit: Any) -> tuple:
    try:
        s = int(slot)
    except (TypeError, ValueError):
        s = -1
    return (str(pool), s, str(cell), _num(hold), _num(tp), _num(sl), _num(trail), "1" if event_exit else "0")


def render(t: tuple) -> str:
    return "|".join(str(x) for x in t)


def md5_lines(lines: Sequence[tuple]) -> str:
    return hashlib.md5("".join(render(t) + "\n" for t in sorted(lines)).encode()).hexdigest()


def compare_lines(kit: Sequence[tuple], port: Sequence[tuple], hour: str = "") -> list[dict]:
    """The differences of two hours' line lists, keyed by (pool, slot, cell). Names of differing fields, never values."""
    kb: dict[tuple, list] = defaultdict(list)
    pb: dict[tuple, list] = defaultdict(list)
    for t in kit:
        kb[t[:3]].append(t)
    for t in port:
        pb[t[:3]].append(t)
    out = []
    for key in sorted(set(kb) | set(pb)):
        a, b = sorted(kb.get(key, [])), sorted(pb.get(key, []))
        if a == b:
            continue
        if not a:
            kind, names = "only_port", []
        elif not b:
            kind, names = "only_kit", []
        elif len(a) != len(b):
            kind, names = "count_differs", []
        else:
            kind = "fields_differ"
            names = [FIELDS[i] for i in range(3, len(FIELDS)) if any(x[i] != y[i] for x, y in zip(a, b))]
        out.append({"hour": hour, "pool": key[0], "slot": key[1], "cell": key[2], "kind": kind, "n_kit": len(a), "n_port": len(b), "fields": names})
    return out


# ------------------------------------------------------------------------------------------------------------------------------ kit side
def hour_lines(path: Path) -> Iterator[bytes]:
    """The pumpswap lines of one hour file (the kit's own reader for a .zst: zstd | grep)."""
    if path.name.endswith(".zst"):
        yield from S._hour_lines(str(path))
        return
    with open(path, "rb") as fh:
        for ln in fh:
            if b"pumpswap" in ln:
                yield ln


class DecisionScreen(S.Screen):
    """The kit's Screen with its pricing step replaced by a recorder of the entries that survive the kit's own refusals and overlap rule. Nothing is priced."""

    def __init__(self, rule: Any, labels: dict[int, str]) -> None:
        super().__init__(rule, count_only=False, screen_p=False)
        self.labels = labels
        self.kept: list[tuple[str, str, str, int, Any]] = []  # (hour, cell, pool, decision slot, spec)

    def _price_store(self, s: Any, pool: str, bi: int, seg_end: int, clk: dict) -> None:
        a = {k: np.concatenate([c[k] for c in s.chunks]) for k in s.chunks[0]}
        a["ib"] = a["ib"].astype(bool)
        pp = S.post_price(a)
        rate = clk["rate"]
        for k in [k for k in self._nxt if k[1] == pool]:  # as the kit: the store is gone after this call
            del self._nxt[k]
        for ci in sorted({t[0] for t in s.trigs}):
            nxt = -1
            for _, slot, hi, spec, pos in sorted((t for t in s.trigs if t[0] == ci), key=lambda t: t[1]):  # the kit's overlap loop (arena_screen.Screen._price_store); trigs are 5-tuples
                if pos is not None:  # exit_fire position: already admitted (overlap rule applied in Screen._admit); its entry decision is kept, nothing is run
                    self.kept.append((self.labels[hi], self.cells[ci].name, pool, int(slot), spec))
                    self._open.pop((ci, pool), None)  # as the kit's _resolve: the flushed store's position is gone, or the next hour's _advance_open raises KeyError and _admit drops the pool
                    continue
                if slot < nxt:
                    self.counters["overlap_dropped"] += 1
                    continue
                xs, _why, _clamped = S.resolve_exit(a, pp, slot, spec, rate[hi], 1.0 / rate[hi])
                nxt = xs + int(np.rint(self.cells[ci].cooldown_s * rate[hi]))
                self.kept.append((self.labels[hi], self.cells[ci].name, pool, int(slot), spec))


def kit_decisions(rule: Any, files: Sequence[tuple[datetime, Path]]) -> tuple[dict[str, list[tuple]], dict[str, int]]:
    labels = {i: f"{t:%Y-%m-%dT%H}" for i, (t, _) in enumerate(files)}
    scr = DecisionScreen(rule, labels)
    hours = (S.build_hour(hour_lines(p), block="port-check", T=int(t.timestamp()), idx=i, fmt="arena", vmap=None) for i, (t, p) in enumerate(files))
    scr.run_block(0, "port-check", hours)
    by: dict[str, list[tuple]] = {h: [] for h in labels.values()}
    for hour, cell, pool, slot, spec in scr.kept:
        by[hour].append(make_line(pool, slot, cell, spec.hold_s, getattr(spec, "tp", None), getattr(spec, "sl", None), getattr(spec, "trail", None),
                                  bool(getattr(spec, "event_exit", False))))
    return by, dict(scr.counters)


# ------------------------------------------------------------------------------------------------------------------------------ port side
class GivenFilesState(ArenaState):
    """ArenaState over exactly the given hour files, read by their own paths: no directory is listed, so no other hour (the open one included) can be read."""

    def __init__(self, tape_dir: Path, files: Sequence[tuple[datetime, Path]]) -> None:
        super().__init__(tape_dir, start_ms=0)
        self._given = [(f"{t:%Y-%m-%dT%H}", None if p.name.endswith(".zst") else p, p if p.name.endswith(".zst") else None) for t, p in files]

    def _hour_files(self) -> list:
        return list(self._given)


class DecisionOnlyEngine:
    """The engine surface the host uses, with no pricing: a buy opens a position, a sell closes it at once. The entry decision is in the decision log before buy() is called."""

    def __init__(self) -> None:
        self.positions: dict[str, Any] = {}
        self.now_fn = None
        self.rows_seen = 0

    def on_row(self, row: Any) -> None:
        self.rows_seen += 1

    def recover(self) -> int:
        return 0

    def start_record(self, bot_id: str) -> bool:
        return False

    def buy(self, bot_id: str, pool: str, sol_in: Any, decision_ms: int, decision_slot: int, meta: Any = None) -> str:
        pid = R.position_id(bot_id, pool, decision_ms, decision_slot)
        self.positions[pid] = SimpleNamespace(bot_id=bot_id, pool=pool, meta=dict(meta or {}), decision_ms=decision_ms, stamp_ms=decision_ms, decision_slot=decision_slot)
        return pid

    def sell(self, bot_id: str, pid: str, frac: float, decision_ms: int, decision_slot: int, reason: str = "") -> None:
        if pid not in self.positions:
            raise ArenaRefused(f"no open position {pid} for bot {bot_id}")
        del self.positions[pid]


def load_port(spec: str, kwargs_json: str | None = None) -> tuple[list[Any], list[Path]]:
    """(rules, their source files). A name in RULE_MODULES goes through the host's own `load_rules`; else `module[:factory]` (default `make_rule`) with factory(**kwargs)."""
    name, _, fac = spec.partition(":")
    kwargs = json.loads(kwargs_json) if kwargs_json else {}
    if spec in R.RULE_MODULES:
        mod, rules = R.RULE_MODULES[spec], R.load_rules([spec])
    else:
        mod = name
        try:
            made = getattr(importlib.import_module(mod), fac or "make_rule")(**kwargs)
        except (ImportError, AttributeError) as exc:
            raise PortCheckRefused(f"port_not_loadable: {spec}: {exc}") from exc
        rules = list(made) if isinstance(made, (list, tuple)) else [made]
    ids = [getattr(r, "bot_id", "") for r in rules]
    if not rules or not all(callable(getattr(r, "on_row", None)) and callable(getattr(r, "on_tick", None)) for r in rules) or not all(ids) or len(set(ids)) != len(ids):
        raise PortCheckRefused(f"port_not_a_rule: {spec} must give Rule objects with unique non-empty bot_ids")
    fs = importlib.util.find_spec(mod)
    if fs is None or not fs.origin or not fs.origin.endswith(".py"):
        raise PortCheckRefused(f"port_module_has_no_python_source: {mod}")
    return rules, [Path(fs.origin)]


def port_replay(rules: list[Any], files: Sequence[tuple[datetime, Path]], scratch: Path) -> dict[str, Any]:
    """One host replay of the given hours into a fresh scratch tree. Returns the entry rows, the whole-log md5 and the host's counters."""
    root = scratch / "rules"
    first, last_end = int(files[0][0].timestamp() * 1000), int(files[-1][0].timestamp() * 1000) + HOUR_MS
    state = GivenFilesState(scratch / "tape-unused", files)
    cfg = R.HostConfig(root=str(root), registry=str(scratch / "bots.json"), frozen_sha=FROZEN_SHA, clock_start_ms=first, live=False)
    host = R.Host(cfg, rules, state, DecisionOnlyEngine())
    host.run(first, last_end)
    host.state.poll(until_ms=last_end - 1)  # the last second: event rules see it (the tick at last_end - 1 s delivered up to last_end - 1002)
    entries = [(r.bot_id, row) for r in rules for row in host.log.rows(r.bot_id) if row.get("kind") == "entry"]
    for r in rules:
        host.log.verify(r.bot_id)
    return {"entries": entries, "md5": DecisionLog(root).md5(), "counters": dict(host.counters), "hook_errors": list(host.hook_errors), "fatal": repr(host.fatal) if host.fatal else None,
            "state_counters": {k: int(v) for k, v in state.counters.items()}}


def port_lines(entries: list[tuple[str, dict]], cell_map: dict[str, str], hours: Sequence[str]) -> tuple[dict[str, list[tuple]], int]:
    by: dict[str, list[tuple]] = {h: [] for h in hours}
    outside = 0
    for bot, row in entries:
        meta = row.get("meta") or {}
        cell = meta.get("cell") or bot
        h = datetime.fromtimestamp(int(row["ms"]) / 1000, timezone.utc).strftime("%Y-%m-%dT%H")
        if h not in by:
            outside += 1
            continue
        by[h].append(make_line(row["pool"], row.get("decision_slot"), cell_map.get(cell, cell), meta.get("hold_s"), meta.get("tp"), meta.get("sl"), meta.get("trail"),
                               bool(meta.get("event_exit", False))))
    return by, outside


# ------------------------------------------------------------------------------------------------------------------------------ the check
def run_check(kit_spec: str, port_spec: str, hour_files: Sequence[str | os.PathLike], out: str | os.PathLike, *, kit_kwargs: str | None = None, port_kwargs: str | None = None,
              cell_map: dict[str, str] | None = None, min_decisions: int = 1) -> dict[str, Any]:
    files = check_hour_files(hour_files)
    out_dir = check_out_dir(out)
    for spec in (kit_spec, port_spec):
        check_path_words(spec, "rule spec")
    kit_src = S.rule_source_path(kit_spec) if kit_spec.rpartition(":")[0] else None
    if kit_src is None:
        raise PortCheckRefused("--kit-rule must be module:Factory or file.py:Factory")
    port_mod = R.RULE_MODULES.get(port_spec, port_spec.partition(":")[0])
    port_fs = importlib.util.find_spec(port_mod)
    if port_fs is None or not port_fs.origin or not port_fs.origin.endswith(".py"):
        raise PortCheckRefused(f"port_module_has_no_python_source: {port_mod}")
    port_src = Path(port_fs.origin)
    for p in (kit_src, port_src):
        check_path_words(p, "rule source")
    try:
        kit_sha, port_sha = S.check_rule_source(kit_src), S.check_rule_source(port_src)  # DEC-031 3.1 scan, the kit's own
    except S.Refused as exc:
        raise PortCheckRefused(str(exc)) from exc
    before = set(sys.modules)
    try:
        kit_rule = S.load_rule(kit_spec, kit_kwargs)
        rules_probe, _ = load_port(port_spec, port_kwargs)
        S.check_loaded_modules(before)
    except S.Refused as exc:
        raise PortCheckRefused(str(exc)) from exc
    del rules_probe
    hours = [f"{t:%Y-%m-%dT%H}" for t, _ in files]
    cmap = dict(cell_map or {})
    out_dir.mkdir(parents=True, exist_ok=True)

    kit_by, kit_counters = kit_decisions(kit_rule, files)
    runs = []
    for tag in ("a", "b"):  # the same input twice, fresh rules and a fresh scratch tree each time
        rules, _ = load_port(port_spec, port_kwargs)
        scratch = out_dir / f"scratch-{tag}"
        scratch.mkdir()
        runs.append(port_replay(rules, files, scratch))
    port_by, outside = port_lines(runs[0]["entries"], cmap, hours)

    per_hour, diffs = {}, []
    for h in hours:
        k, p = sorted(kit_by[h]), sorted(port_by[h])
        d = compare_lines(k, p, h)
        diffs += d
        kinds = Counter(x["kind"] for x in d)
        per_hour[h] = {"n_kit": len(k), "n_port": len(p), "md5_kit": md5_lines(k), "md5_port": md5_lines(p), "match": md5_lines(k) == md5_lines(p),
                       "diff_keys": len(d), "only_kit": kinds["only_kit"], "only_port": kinds["only_port"], "count_differs": kinds["count_differs"], "fields_differ": kinds["fields_differ"]}
    tot = {"hours": len(hours), "hours_matching": sum(v["match"] for v in per_hour.values()), "n_kit": sum(v["n_kit"] for v in per_hour.values()),
           "n_port": sum(v["n_port"] for v in per_hour.values()), "diff_keys": len(diffs), **{k: sum(v[k] for v in per_hour.values()) for k in ("only_kit", "only_port", "count_differs", "fields_differ")},
           "port_decisions_outside_hours": outside}
    det = runs[0]["md5"] == runs[1]["md5"]
    hook = sum(len(r["hook_errors"]) for r in runs)
    reasons = []
    if tot["hours_matching"] != tot["hours"]:
        reasons.append(f"entry decisions differ on {tot['hours'] - tot['hours_matching']} of {tot['hours']} hours: only_kit {tot['only_kit']}, only_port {tot['only_port']}, "
                       f"count_differs {tot['count_differs']}, fields_differ {tot['fields_differ']} (by pool, slot, cell)")
    if not det:
        reasons.append("host replay not deterministic: the decision-log md5 differs between two identical replays")
    if hook or any(r["fatal"] for r in runs):
        reasons.append(f"host recorded {hook} hook error(s) / fatal: {runs[0]['hook_errors'][:3]} {runs[0]['fatal']}")
    if outside:
        reasons.append(f"{outside} port decision(s) fall outside the given hours")
    if min(tot["n_kit"], tot["n_port"]) < int(min_decisions):
        reasons.append(f"vacuous: kit {tot['n_kit']} / port {tot['n_port']} decisions, fewer than --min-decisions {min_decisions} on a side (an empty equivalence proves nothing)")
    doc = {"schema": SCHEMA, "verdict": "FAIL" if reasons else "PASS", "reasons": reasons,
           "inputs": {"kit_rule": kit_spec, "kit_kwargs": kit_kwargs, "port": port_spec, "port_kwargs": port_kwargs, "cell_map": cmap, "min_decisions": int(min_decisions), "hours": hours,
                      "hour_files": [{"hour": f"{t:%Y-%m-%dT%H}", "path": str(p), "sha256": file_sha256(p)} for t, p in files]},
           "provenance": {**git_state(), "tool_sha256": file_sha256(Path(__file__).resolve()), "arena_screen_sha256": file_sha256(Path(S.__file__).resolve()),
                          "arena_rules_sha256": file_sha256(Path(R.__file__).resolve()), "kit_rule_source": str(kit_src), "kit_rule_sha256": kit_sha,
                          "port_rule_source": str(port_src), "port_rule_sha256": port_sha, "python": sys.version.split()[0]},
           "per_hour": per_hour, "totals": tot, "differences_first_20": diffs[:MAX_DIFFS], "differences_truncated": max(0, len(diffs) - MAX_DIFFS),
           "determinism": {"replays": 2, "decision_log_md5_a": runs[0]["md5"], "decision_log_md5_b": runs[1]["md5"], "identical": det},
           "kit_counters": kit_counters, "port_host_counters": runs[0]["counters"], "port_state_counters": runs[0]["state_counters"], "port_hook_errors": hook,
           "scope": "entry decisions only (pool, decision slot, cell, hold_s, tp, sl, trail, event_exit); exits, prices, fills and exclusions are not compared; not a profit claim"}
    (out_dir / "port_check.json").write_text(json.dumps(doc, indent=1, sort_keys=True))
    return doc


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--kit-rule", required=True, help="the screened rule, module:Factory or file.py:Factory (as arena_screen.load_rule takes it)")
    ap.add_argument("--kit-kwargs", help="JSON kwargs for the kit factory")
    ap.add_argument("--port", required=True, help="the arena port: a name in arena_rules.RULE_MODULES, or module[:factory] (default factory make_rule)")
    ap.add_argument("--port-kwargs", help="JSON kwargs for the port factory")
    ap.add_argument("--cell-map", help='JSON {"port cell": "kit cell"}')
    ap.add_argument("--hours", nargs="+", required=True, help="tape-<YYYY-MM-DDTHH>.jsonl[.zst] files, consecutive hours")
    ap.add_argument("--out", required=True, help="a fresh directory: port_check.json, scratch-a/, scratch-b/")
    ap.add_argument("--min-decisions", type=int, default=1)
    a = ap.parse_args(argv)
    try:
        doc = run_check(a.kit_rule, a.port, a.hours, a.out, kit_kwargs=a.kit_kwargs, port_kwargs=a.port_kwargs, cell_map=json.loads(a.cell_map) if a.cell_map else None,
                        min_decisions=a.min_decisions)
    except PortCheckRefused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"verdict": doc["verdict"], "reasons": doc["reasons"], "totals": doc["totals"], "determinism": doc["determinism"],
                      "per_hour": {h: {k: v[k] for k in ("n_kit", "n_port", "md5_kit", "md5_port", "match")} for h, v in doc["per_hour"].items()}}, indent=1))
    return 0 if doc["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
