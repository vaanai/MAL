#!/usr/bin/env python3
"""arena_j3_explore: the W_e exploration of the J3 family (QP R9(d)). PAPER only. No key, no wallet, no model, no network.

    python -m tools.arena_j3_explore --scratch DIR --hours /data/mal/arena/tape/tape-2026-10-19T08.jsonl.zst [...]

It replays a list of CLOSED tape hours through the arena_rules host in replay mode with all 12 J3 cells (tools/arena_rule_j3.py) as books in ONE scratch registry,
decision-log root and ledger, then prints and writes the per-cell report. The spec is fixed in the MiScusi notebook entry "J3 R9(d) exploration spec FIXED before any
price"; nothing below is a parameter: the 12 cells (k in 1, 5, 20 x H in 30, 120, 300, 900 s), the stake (0.25 SOL), the stack (the host's pinned `stack_config(7)`,
rent 0 per DEC-031 Amendment 2, strand rent 2,039,280, gap rules on), the legs (1.9 s; flat 15% and pressure; strand rules total and sc), the kill rule and the bootstrap
(1,000 draws, seed 1, 5th percentile) are constants. W_e is every hour in [2026-10-19T08, 2026-10-20T08); a list that reaches outside it is refused.

WHAT IT REFUSES (before anything is read or written)
  * no file, a name that is not `tape-<YYYY-MM-DDTHH>.jsonl.zst`, a file that is missing;
  * the OPEN (uncompacted) hour: a `.jsonl` file, or a `.jsonl.zst` whose `.jsonl` still exists (compaction not finished), or a `.zst.tmp`;
  * an hour outside [2026-10-19T08, 2026-10-20T08), a duplicate hour, hours that are not consecutive (the replay is one continuous tape, and the engine's own gap rules
    would count a missing hour as a feed gap);
  * a --scratch that is inside /data/mal/arena (the tape files given are the only paths under it this tool touches, and it only reads them), that contains it, or that
    is not an empty directory. The scratch holds the scratch registry, decision logs and ledger, and the report; the given files are read in place, by path, through a state that lists no directory.
It never opens the arena ledgers, bots.json, a shakedown directory or any other arena path, and it registers its books with the test-only overrides of
arena_paper.register_bot (fake registration clock, unverified sha) inside the scratch registry only: those lines are a scratch convenience, not a registration.

REPLAY. The state is an ArenaState over the scratch tape dir from the first hour's start (the tape start the R9(b) rule uses), the clock starts there, ticks run once a
second from the tape start through the end of the last hour (one tick at the end, so the last second of rows is delivered), and the engine is flushed at the end:
a sell decided inside the window whose exit state has no print yet is priced on the derived last state (`derived_last`, counted and reported), and a position whose hold
had not run out when the window ended is NOT priced (`unsold_at_window_end`, reported). A restart never happens inside a run.

REPORT (REPORT.json and REPORT.md in the scratch dir), per cell: triggers and attempts; n (priced trades after the scorer's exclusions, 0.25 SOL), fills and trip states;
UTC days; mean, trade-level CI90 lower bound (arena_score's own bootstrap) and the DATE-CLUSTERED CI90 lower bound (resample UTC dates, 1,000 draws, seed 1); total;
ex-top-3; ex-best-day; all for the four deciding sub-cells (flat | pressure x strand rule total | sc at 1.9 s) at rent 0, which decide, and the rent-charged leg beside each
(DEC-031 Amendment 2 item 7: on trips whose leg state is `filled`, flat minus 0.85 x 2,039,280 lamports, pressure minus (1 - p_i) x 2,039,280 with p_i the cell's own refit
probability); the kill verdict; entry impact at 0.25 and 1.0 SOL; and the exclusions by reason. The statistics are arena_score's (`book_cells`, `cell_stats`) on the ledger rows;
the scorer's step 3 (late prints in a gap, missing V) is mirrored in `split_stake`. W_e figures are IN-SAMPLE for J3 and every variant (QP R9(d)); they are never evidence.
"""

from __future__ import annotations

if __package__ in (None, ""):  # `python tools/arena_j3_explore.py`
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import dataclasses
import hashlib
import json
import math
import os
import re
import sys
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, NamedTuple, Sequence

import numpy as np

from tools import arena_rule_j3 as J
from tools import arena_stack as F
from tools.arena_paper import STAKES_SOL, TEST_ENV, ArenaEngine, Ledger, canon, process_code_state, utc_day
from tools.arena_rules import ARENA_OUTPUT_ROOT, TICK_MS, Host, HostConfig, HostError, HostFatal, register_rules, stack_config
from tools.arena_score import Refused, _legs, book_cells, cell_stats
from tools.arena_state import ArenaState

SCHEMA = "arena_j3_explore_v1"
ARENA_ROOT = ARENA_OUTPUT_ROOT  # /data/mal/arena: a module global so the tests can point it at a temp tree
HOUR_MS = 3_600_000
W_E_START_MS = int(datetime(2026, 10, 19, 8, 5, 20, tzinfo=timezone.utc).timestamp() * 1000)  # the spec's W_e starts at the tape's first receive time
W_E_END_MS = int(datetime(2026, 10, 20, 8, tzinfo=timezone.utc).timestamp() * 1000)
FEED_DELAY_SLOTS = 7
DECIDING_STAKE = 0.25
IMPACT_STAKES = (0.25, 1.0)
LAT = "1p9"
MODELS = ("flat", "press")
STRAND_RULES = ("total", "sc")
RENT_CHARGED = F.RENT_REPORT  # 2,039,280 lamports per filled trip
BOOT_DRAWS, BOOT_SEED, BOOT_PCT = F.BOOT_DRAWS, F.BOOT_SEED, F.BOOT_PCT
HOUR_FILE = re.compile(r"^tape-(\d{4}-\d{2}-\d{2}T\d{2})\.jsonl\.zst$")
MIN_FREE_PCT_DEFAULT = 25.0


class ExploreRefused(Exception):
    """A refusal before anything was read or written."""


class ExploreError(Exception):
    """An internal inconsistency found after the replay (the report is not written)."""


class Hour(NamedTuple):
    start_ms: int
    path: Path


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _hour_start(key: str) -> int:
    return _ms(datetime.strptime(key, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc))


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def w_e_hours() -> list[int]:
    """Start (ms) of every hour file W_e needs: the hour that holds W_E_START_MS through the hour before W_E_END_MS (24 files). Read at call time, so a test may move the globals."""
    first = W_E_START_MS - W_E_START_MS % HOUR_MS
    return list(range(first, W_E_END_MS, HOUR_MS))


def _inside(path: Path, root: str | Path) -> bool:
    p, r = Path(path).resolve(), Path(root).resolve()
    return p == r or r in p.parents


# ------------------------------------------------------------------------------------------------------------------------------ refusals
def validate_inputs(files: Sequence[str | Path]) -> list[Hour]:
    """The hours of W_e to replay, oldest first, or ExploreRefused. Reads no file content."""
    if not files:
        raise ExploreRefused("no tape hour given")
    hours: dict[int, Path] = {}
    for f in files:
        p = Path(f)
        name = p.name
        if name.endswith(".zst.tmp"):
            raise ExploreRefused(f"{name}: an unfinished compaction, not a closed hour")
        if re.match(r"^tape-\d{4}-\d{2}-\d{2}T\d{2}\.jsonl$", name):
            raise ExploreRefused(f"{name}: the open (uncompacted) hour is refused; only closed hours (tape-<hour>.jsonl.zst) are replayed")
        m = HOUR_FILE.match(name)
        if m is None:
            raise ExploreRefused(f"{name}: not a closed tape hour (expected tape-<YYYY-MM-DDTHH>.jsonl.zst)")
        if not p.is_file():
            raise ExploreRefused(f"{name}: no such file")
        real = p.resolve()
        if p.is_symlink() or real.name != name:  # the state reads the resolved path: a link named for a W_e hour could point at another hour or file
            raise ExploreRefused(f"{name}: a symlinked hour path is refused (it resolves to {real.name}); give the tape file itself")
        if real.parent != (Path(ARENA_ROOT) / "tape").resolve():  # R9(d): the input is the arena tape; a copy anywhere else (a shakedown dir included) is refused
            raise ExploreRefused(f"{name}: not located directly in {Path(ARENA_ROOT) / 'tape'} (it is in {real.parent}); refused")
        if (p.parent / name[: -len(".zst")]).exists():
            raise ExploreRefused(f"{name}: its .jsonl still exists, so the hour is open or its compaction has not finished; refused")
        start = _hour_start(m.group(1))
        if start not in w_e_hours():
            raise ExploreRefused(f"{name}: outside W_e [{_iso(W_E_START_MS)}, {_iso(W_E_END_MS)}); refused")
        if start in hours:
            raise ExploreRefused(f"{name}: the hour {m.group(1)} is given twice")
        hours[start] = p
    starts = sorted(hours)
    want = w_e_hours()
    if starts != want:  # the spec fixes W_e: all of it, once; a partial list is a different exploration (and a missing hour would be read as a feed gap)
        raise ExploreRefused(f"the hour list is not exactly the {len(want)} W_e hours {_iso(want[0])} to {_iso(want[-1])}: missing {[_iso(h) for h in want if h not in hours]}")
    return [Hour(s, hours[s]) for s in starts]


def check_scratch(scratch: str | Path) -> Path:
    """The resolved scratch dir, or ExploreRefused. Writes nothing."""
    s = Path(scratch).resolve()
    root = Path(ARENA_ROOT).resolve()
    if _inside(s, root):
        raise ExploreRefused(f"--scratch {scratch} is inside {ARENA_ROOT}: this tool writes nothing under the arena tree")
    if s in root.parents:
        raise ExploreRefused(f"--scratch {scratch} contains {ARENA_ROOT}: refused")
    if s.exists() and (not s.is_dir() or any(s.iterdir())):
        raise ExploreRefused(f"--scratch {scratch} is not an empty directory: a scratch ledger is never reused")
    return s


def prepare_scratch(scratch: Path, hours: Sequence[Hour]) -> dict[str, Path]:
    dirs = {"tape": scratch / "tape", "rules": scratch / "rules", "ledger": scratch / "ledger", "registry": scratch / "bots.json"}
    for k, p in dirs.items():
        if not _inside(p, scratch) or _inside(p, ARENA_ROOT):
            raise ExploreRefused(f"{k} path {p} is not inside the scratch dir")
    scratch.mkdir(parents=True, exist_ok=True)
    for k in ("tape", "rules", "ledger"):
        dirs[k].mkdir()  # tape/ stays empty: the state reads the given files by path (zstd ignores symlinks, and a directory listing would show other hours)
    return dirs


class GivenHoursState(ArenaState):
    """ArenaState over exactly the given closed hours, read by their own paths. No directory is listed, so no other hour (the open one included) can be read."""

    def __init__(self, tape_dir: Path, hours: Sequence[Hour], start_ms: int) -> None:
        super().__init__(tape_dir, start_ms=start_ms)
        self._given = [(h.path.name[len("tape-"):-len(".jsonl.zst")], None, h.path.resolve()) for h in hours]

    def _hour_files(self) -> list:
        return list(self._given)


@contextmanager
def _scratch_registration_env() -> Iterator[None]:
    """register_bot accepts a fake registration clock and an unverified sha only with MAL_ARENA_TEST=1. Set it for the scratch registration only."""
    old = os.environ.get(TEST_ENV)
    os.environ[TEST_ENV] = "1"
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(TEST_ENV, None)
        else:
            os.environ[TEST_ENV] = old


# ------------------------------------------------------------------------------------------------------------------------------ the recording rule
class RecordingJ3(J.J3Rule):
    """The J3 rule with a tally on the side: which children were triggers and why a trigger made no order. The decisions are J3Rule's (a test hosts both and compares the
    decision logs). The tally lives here, not in the rule file, which keeps no memory."""

    def __init__(self, k: int, hold_s: int, tape_start_ms: int | None = None) -> None:
        super().__init__(k, hold_s, tape_start_ms)
        self.triggers: set[str] = set()
        self.skips: dict[str, set[str]] = {}

    def on_row(self, ctx: Any, row: Mapping[str, Any]) -> list:
        v = self.evaluate(ctx, row)
        if v.child is not None:
            if v.trigger:
                self.triggers.add(v.child)
            if v.reason is not None:
                self.skips.setdefault(v.reason, set()).add(v.child)
        return list(v.orders)


# ------------------------------------------------------------------------------------------------------------------------------ statistics
def date_cluster_boot_lo(x: Sequence[float], days: Sequence[str], draws: int = BOOT_DRAWS, seed: int = BOOT_SEED, q: float = BOOT_PCT) -> float | None:
    """Lower CI bound of the mean per trade with UTC dates as the resampling unit: each draw picks len(dates) dates with replacement and takes the pooled mean of the trades
    in them (sum / count); the q-th percentile of `draws` such means (one rng.integers call per draw, like arena_stack.boot_lo). None with fewer than 2 dates or no trade."""
    x = np.asarray(x, float)
    if len(x) == 0:
        return None
    _, inv = np.unique(np.asarray(days), return_inverse=True)
    m = int(inv.max()) + 1
    if m < 2:
        return None
    s, c = np.bincount(inv, weights=x), np.bincount(inv).astype(float)
    rng = np.random.default_rng(seed)
    bm = np.empty(draws)
    for i in range(draws):
        idx = rng.integers(0, m, m)
        bm[i] = s[idx].sum() / c[idx].sum()
    return float(np.percentile(bm, q))


def kill_check(s: Mapping[str, Any]) -> dict[str, Any]:
    """The spec's kill rule on one sub-cell's statistics: dead if mean <= 0, ex-top-3 <= 0 or ex-best-day <= 0 (a missing figure is dead too)."""
    if not s.get("n"):
        return {"killed": True, "why": ["no priced trades"]}
    why = []
    for key, label in (("mean_sol", "mean <= 0"), ("ex_top3_sol", "ex-top-3 <= 0"), ("ex_best_day_sol", "ex-best-day <= 0")):
        v = s.get(key)
        if v is None or not v > 0:
            why.append(label)
    return {"killed": bool(why), "why": why}


def split_stake(rows: Sequence[Mapping[str, Any]], stake: float) -> tuple[list[Mapping[str, Any]], list[tuple[Mapping[str, Any], str]]]:
    """(scored trade rows, [(excluded row, reason)]) at one stake: arena_score._score_bot step 3, line for line (a late print inside a gap, a print without V, and the
    engine's own `excluded` rows)."""
    late_gap = {r["position_id"] for r in rows if r.get("kind") == "late_print" and r.get("gap_overlap")}
    trades: list[Mapping[str, Any]] = []
    excluded: list[tuple[Mapping[str, Any], str]] = []
    for r in rows:
        if r.get("stake_sol") != stake:
            continue
        if r["kind"] == "trade":
            vs = r.get("v_src") or {}
            if r["position_id"] in late_gap:
                excluded.append((r, "late_print_gap"))
            elif "none" in (vs.get("entry"), vs.get("exit")) or "none" in (vs.get("span") or {}):
                excluded.append((r, "missing_v"))
            else:
                trades.append(r)
        elif r["kind"] == "excluded":
            excluded.append((r, str(r["exclusion_reason"])))
    return trades, excluded


def entry_impact_bps(row: Mapping[str, Any]) -> float | None:
    """Price impact of our own entry at the 1.9 s landing state, in bps: the buy moves the constant-product pool from Qe to Qe + net, so its average price is
    (1 + net / Qe) times the pool's spot there, and the impact is 1e4 x net / Qe with net = stake x (1 - fee tier). The fee itself, the haircut and the send are not in it."""
    c = row["calc"][LAT]
    qe, be = float(c["Qe"]), float(c["Be"])
    if not (math.isfinite(qe) and math.isfinite(be) and qe > 0 and be > 0):
        return None
    net = float(row["stake_sol"]) * 1e9 * (1 - float(F.fee_ppm(qe, be)) / 1e6)
    return 1e4 * net / qe


def _summary(vals: Sequence[float]) -> dict[str, Any]:
    if not len(vals):
        return {"n": 0, "mean_bps": None, "median_bps": None, "p90_bps": None, "max_bps": None}
    a = np.asarray(vals, float)
    return {"n": int(len(a)), "mean_bps": float(a.mean()), "median_bps": float(np.median(a)), "p90_bps": float(np.percentile(a, 90)), "max_bps": float(a.max())}


def _leg_arrays(trades: Sequence[Mapping[str, Any]], rule: str) -> dict[str, np.ndarray]:
    """Per-trade deciding-leg arrays at 1.9 s for one strand rule, built exactly as arena_score.book_cells builds them (same refit of the pressure intercept)."""
    g = lambda key: np.array([r["calc"][LAT]["by_rule"][rule][key] for r in trades])  # noqa: E731
    pnl, fil, strand = g("pnl_nofail").astype(float), g("filled").astype(bool), g("strand").astype(bool)
    ssb = np.array([r["calc"][LAT]["ssb"] for r in trades], float)
    near = np.array([r["calc"][LAT]["nearby_lamports"] for r in trades], float)
    flat, press, c, p = _legs(rule, pnl, fil, strand, ssb, near, float(trades[0]["stack"]["send"]))
    return {"flat": flat, "press": press, "p": p, "intercept": np.array([c]), "filled_state": np.array([r["calc"][LAT]["by_rule"][rule]["state"] == "filled" for r in trades], bool),
            "state": np.array([r["calc"][LAT]["by_rule"][rule]["state"] for r in trades]), "filled": fil}


def _stats(x: np.ndarray, days: Sequence[str]) -> dict[str, Any]:
    s = cell_stats(x, days)
    s["ci90_lo_date_cluster_sol"] = date_cluster_boot_lo(x / 1e9, days)
    s["date_clusters"] = len(set(days))
    return s


_KEEP = ("n", "days", "days_pos", "mean_sol", "ci90_lo_sol", "ci90_lo_date_cluster_sol", "date_clusters", "total_sol", "ex_top3_sol", "ex_best_day_sol", "daily")


def subcells(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The four deciding sub-cells (flat | pressure x strand rule total | sc, 1.9 s, 0.25 SOL) at rent 0, each with its rent-charged leg beside it."""
    if not trades:
        return {}
    days = [utc_day(r["decision_ms"]["entry"]) for r in trades]
    scored = book_cells(trades, list(STRAND_RULES))  # arena_score's own cells (also refuses a stored flat leg that differs from the recomputed one)
    out: dict[str, Any] = {}
    for rule in STRAND_RULES:
        a = _leg_arrays(trades, rule)
        rc_x = {"flat": a["flat"] - (1 - F.FAIL_FLAT) * RENT_CHARGED * a["filled_state"], "press": a["press"] - (1 - a["p"]) * RENT_CHARGED * a["filled_state"]}
        for model in MODELS:
            name = f"{model}_{LAT}_{rule}"
            x = a[model]
            mine = _stats(x, days)
            theirs = scored[name]
            for key in ("n", "mean_sol", "total_sol", "ci90_lo_sol", "ex_top3_sol", "ex_best_day_sol"):
                if mine[key] != theirs[key] and not (isinstance(mine[key], float) and math.isclose(mine[key], theirs[key], rel_tol=1e-12, abs_tol=1e-15)):
                    raise ExploreError(f"{name}: {key} recomputed {mine[key]} != arena_score {theirs[key]}")
            sc = {k: mine[k] for k in _KEEP}
            sc.update({"fills": int(a["filled"].sum()), "states": dict(Counter(map(str, a["state"]))), "strand_rule": rule, "fail_model": "flat 15%" if model == "flat" else "pressure",
                       "intercept": float(a["intercept"][0]) if model == "press" else None, "derived_last_n": theirs["derived_last_n"], "derived_last_share": theirs["derived_last_share"],
                       "without_derived_last": {k: theirs["without_derived_last"][k] for k in ("n", "mean_sol", "total_sol")}, "kill": kill_check(mine)})
            rc = _stats(rc_x[model], days)
            sc["rent_charged"] = {**{k: rc[k] for k in _KEEP}, "trips_transformed": int(a["filled_state"].sum()), "kill": kill_check(rc),
                                  "note": "report-only: rent 0 decides (DEC-031 Amendment 2); transform of the same rows on trips whose leg state is 'filled'"}
            out[name] = sc
    return out


def cell_report(rule: RecordingJ3, host: Host, ledger_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    bot = rule.bot_id
    dec = host.log.rows(bot)
    entries = [r for r in dec if r["kind"] == "entry"]
    opens = [r for r in ledger_rows if r["kind"] == "open"]
    refused = [r for r in ledger_rows if r["kind"] == "refused"]
    closed = {r["position_id"] for r in ledger_rows if r["kind"] in ("trade", "excluded")}
    unsold = [o for o in opens if o["position_id"] not in closed]
    trades, excluded = split_stake(ledger_rows, DECIDING_STAKE)
    ids = {r["position_id"] for r in trades}
    impact = {}
    for st in IMPACT_STAKES:
        t_st, _ = split_stake(ledger_rows, st)
        vals = [v for r in t_st if r["position_id"] in ids for v in [entry_impact_bps(r)] if v is not None]
        impact[str(st)] = _summary(vals)
    subs = subcells(trades)
    killed = {k: v["kill"]["why"] for k, v in subs.items() if v["kill"]["killed"]}
    if not subs:
        killed = {"all": ["no priced trades"]}
    return {
        "bot_id": bot, "k": rule.k, "hold_s": rule.hold_s, "triggers": len(rule.triggers), "attempts": len(entries), "opened": len(opens), "refused_buys": len(refused),
        "attempts_reconcile": len(entries) == len(opens) + len(refused),
        "n_priced": len(trades), "n_excluded": len(excluded), "utc_days": sorted({utc_day(r["decision_ms"]["entry"]) for r in trades}),
        "subcells": subs, "entry_impact": impact,
        "exclusions": {
            "no_attempt_by_rule": {k: len(v) for k, v in sorted(rule.skips.items())},  # distinct child pools per reason; the trigger made no entry decision
            "refused_buys": dict(sorted(Counter(r["reason"] for r in refused).items())),  # still an attempt
            "excluded_trips": dict(sorted(Counter(reason for _, reason in excluded).items())),
            "unsold_at_window_end": len(unsold),
            "late_prints": sum(1 for r in ledger_rows if r["kind"] == "late_print"),
        },
        "kill": {"killed": bool(killed), "sub_cells_failing": killed, "rule": "dead if, under either fail model and either strand rule, mean <= 0, ex-top-3 <= 0 or ex-best-day <= 0"},
    }


# ------------------------------------------------------------------------------------------------------------------------------ the run
def _plain(o: Any) -> Any:
    if isinstance(o, dict):
        return {str(k): _plain(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_plain(v) for v in o]
    if isinstance(o, np.generic):
        o = o.item()
    if isinstance(o, float) and not math.isfinite(o):
        return None
    return o


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


HOST_ZERO_COUNTERS = ("blocked_universe", "order_before_clock", "order_bad_type")  # a non-zero count means the rule and the host disagree about the universe, the clock or the order type


def integrity_clean(i: Mapping[str, Any]) -> bool:
    """True when nothing in the run was damaged or disagreed: no unreadable or bad row, no hook or pricing error, no ledger incident, no halted book, and none of the host's
    blocked_universe / order_before_clock / order_bad_type counters (the rule never asks for any of them)."""
    return not (i["zst_errors"] or i["bad_lines"] or i["apply_errors"] or i["hook_errors"] or i["price_errors"] or i["ledger_incidents"] or i["halted_books"]
                or any(i[k] for k in HOST_ZERO_COUNTERS))


def run_exploration(files: Sequence[str | Path], scratch: str | Path, *, sha: str | None = None, min_free_pct: float = MIN_FREE_PCT_DEFAULT, hash_inputs: bool = True) -> dict[str, Any]:
    """Validate, replay, report. Returns the report dict (also what REPORT.json holds)."""
    hours = validate_inputs(files)
    root = check_scratch(scratch)
    dirs = prepare_scratch(root, hours)
    t0, t1 = W_E_START_MS, hours[-1].start_ms + HOUR_MS  # the state start, the clock start and the rule's tape start are all W_e's start, not the first hour's
    git_head, git_dirty = process_code_state()
    head = sha or git_head or "0" * 40
    rules: list[RecordingJ3] = J.make_rules(t0, rule_cls=RecordingJ3)  # type: ignore[assignment]
    with _scratch_registration_env():
        register_rules(rules, str(dirs["registry"]), head, t0, now_ms=t0 - 1, verify_sha=False)
    state = GivenHoursState(dirs["tape"], hours, t0)
    engine = ArenaEngine(dirs["ledger"], dirs["registry"], cfg=stack_config(FEED_DELAY_SLOTS), min_free_pct=min_free_pct)
    host = Host(HostConfig(root=str(dirs["rules"]), registry=str(dirs["registry"]), frozen_sha=head, clock_start_ms=t0, live=False), rules, state, engine)
    host.resume()
    host.catch_up(t0)
    T = t0
    while T <= t1:  # one tick at t1 itself: it polls the rows up to t1 - 1, the last second of the window
        host.step(T)
        T += TICK_MS
    engine.flush()
    led = Ledger(dirs["ledger"], min_free_pct=min_free_pct)
    rows = {r.bot_id: led.read_bot(r.bot_id) for r in rules}
    incidents = sum(len(led.read_incidents(r.bot_id)[0]) for r in rules)
    integrity = {"zst_errors": int(state.counters["zst_errors"]), "bad_lines": int(state.counters["bad_lines"]), "apply_errors": int(state.counters["apply_errors"]),
                 "hook_errors": list(host.hook_errors[:20]), "price_errors": int(engine.counters.get("price_errors", 0)), "ledger_incidents": incidents,
                 "halted_books": sorted(engine.halted), "rows_before_start": int(state.counters["rows_before_start"]), "engine_last_errors": dict(sorted(engine.last_error.items())[:5]),
                 **{k: int(host.counters.get(k, 0)) for k in HOST_ZERO_COUNTERS}}
    integrity["clean"] = integrity_clean(integrity)
    cells = {r.bot_id: cell_report(r, host, rows[r.bot_id]) for r in rules}
    alive = [b for b, c in cells.items() if not c["kill"]["killed"]]
    scrub = ("engine_id", "pid", "frozen_sha", "code_head", "code_dirty")  # the digest is a function of the tape and the rules, not of the checkout that ran them
    ledger_digest = hashlib.md5("\n".join(canon({k: v for k, v in row.items() if k not in scrub}) for b in sorted(rows) for row in rows[b] if row["kind"] != "start").encode()).hexdigest()  # a start record holds the process's wall clock and engine id
    report = {
        "schema": SCHEMA, "valid": bool(integrity["clean"]), "provenance": {"git_head": git_head, "git_dirty": git_dirty, "scratch_sha": head},
        "labels": ["PAPER ONLY", "IN-SAMPLE: W_e figures are in-sample for J3 and every variant (QP R9(d)); never cite them as evidence", "NOT A PROFIT CLAIM; no cell has cleared the gate"],
        "spec": {
            "notebook_entry": "J3 R9(d) exploration spec FIXED before any price", "universe": "PumpSwap only; the PARENT's canonical pool is the only priced pool (children are trigger events)",
            "child": "quote_mint neither WSOL nor null; pool_create on the tape; the k-th BUY print counted from the state's event deque (complete history only)",
            "parent": "the child's quote_mint with a canonical PumpSwap pool: quote_mint == WSOL (first) and V in [17.5e9, 17.7e9] on the deciding row",
            "r9b": "parent s0 >= 360 s before the decision and last print before the decision >= s0 + 360 s (s0 = tape start when the pool_create is not on the tape)",
            "order": "buy the parent's canonical pool at 0.25 SOL; one open position per parent; one attempt per (parent, child)",
            "cells": [{"bot_id": J.bot_id(k, h), "k": k, "hold_s": h} for k, h in J.cells()], "hold": "H from the landing of the 1.9 s leg, exit on the 1 s tick at or after",
            "deciding": {"latency": "1.9 s", "stake_sol": DECIDING_STAKE, "fail_models": ["flat 15%", "pressure"], "strand_rules": list(STRAND_RULES), "rent_lamports_filled": 0,
                         "rent_charged_leg_lamports": RENT_CHARGED},
            "ci": {"trade_level": "arena_stack.boot_lo (1,000 draws, seed 1, 5th percentile)", "date_clustered": "resample UTC dates, 1,000 draws, seed 1, 5th percentile, pooled mean"},
            "kill": "a cell dies if, under either fail model (and either strand rule), mean <= 0, ex-top-3 <= 0 or ex-best-day <= 0",
        },
        "window": {"start_ms": t0, "end_ms": t1, "start_utc": _iso(t0), "end_utc": _iso(t1), "w_e": [_iso(W_E_START_MS), _iso(W_E_END_MS)], "hours": [_iso(h.start_ms) for h in hours]},
        "inputs": [{"name": h.path.name, "bytes": h.path.stat().st_size, "sha256": _sha256_file(h.path) if hash_inputs else None} for h in hours],
        "stack": {"config": stack_config(FEED_DELAY_SLOTS).as_dict(), "fields": dataclasses.asdict(stack_config(FEED_DELAY_SLOTS)), "strand_rent_lamports": F.RENT_STRANDED,
                  "stakes_sol": list(STAKES_SOL), "latency_legs_s": list(F.LATENCIES_S)},
        "multiplicity": {"cells_declared": len(cells), "cells_in_report": len(cells), "note": "all 12 cells are in this report, killed ones included (QP R9(d))"},
        "integrity": integrity,
        "host": {"counters": dict(sorted(host.counters.items())), "status": host.status()},
        "engine": {"counters": dict(sorted(engine.counters.items()))},
        "state": {"rows_applied": int(state.counters["rows"]), "evicted": int(state.counters["evicted"])},
        "digests": {"decision_log_md5": host.log.md5(), "ledger_md5": ledger_digest},
        "kill_summary": {"alive": alive, "killed": [b for b in cells if b not in alive]},
        "cells": cells,
    }
    return _plain(report)


# ------------------------------------------------------------------------------------------------------------------------------ rendering
def _f(x: Any) -> str:
    return "None" if x is None else f"{x:+.6f}"


def _row(cell: str, s: Mapping[str, Any]) -> str:
    k = s["kill"]
    return (f"| {cell} | {s['n']} | {s.get('fills', '')} | {s['days']} | {_f(s['mean_sol'])} | {_f(s['ci90_lo_sol'])} | {_f(s['ci90_lo_date_cluster_sol'])} | {_f(s['total_sol'])} | "
            f"{_f(s['ex_top3_sol'])} | {_f(s['ex_best_day_sol'])} | {'KILLED: ' + ', '.join(k['why']) if k['killed'] else 'alive'} |")


def render_md(rep: Mapping[str, Any]) -> str:
    L = ["# J3 W_e exploration (QP R9(d))", "", *[f"- {x}" for x in rep["labels"]], "",
         f"Window {rep['window']['start_utc']} to {rep['window']['end_utc']} ({len(rep['window']['hours'])} hours); schema {rep['schema']}; valid {rep['valid']}.", ""]
    if not rep["integrity"]["clean"]:
        L += ["**INTEGRITY NOT CLEAN: do not read these figures.** " + json.dumps(rep["integrity"], sort_keys=True), ""]
    L += [f"Stack: `{canon(rep['stack']['config'])}`; strand rent {rep['stack']['strand_rent_lamports']}; gap rules gap_slots {rep['stack']['fields']['gap_slots']}, "
          f"gap_silence_ms {rep['stack']['fields']['gap_silence_ms']}.",
          f"Digests: decision logs md5 {rep['digests']['decision_log_md5']}, ledger md5 {rep['digests']['ledger_md5']}.",
          f"Multiplicity: {rep['multiplicity']['cells_declared']} cells declared, all in this report. Alive: {rep['kill_summary']['alive'] or 'none'}.", "",
          "## Per cell: counts", "", "| cell | triggers | attempts | opened | refused | n priced | excluded | unsold at end | utc days |", "|---|---|---|---|---|---|---|---|---|"]
    for b, c in rep["cells"].items():
        L.append(f"| {b} | {c['triggers']} | {c['attempts']} | {c['opened']} | {c['refused_buys']} | {c['n_priced']} | {c['n_excluded']} | {c['exclusions']['unsold_at_window_end']} | "
                 f"{len(c['utc_days'])} |")
    head = ["| cell | n | fills | days | mean | CI90 lo (trade) | CI90 lo (date-cluster) | total | ex-top-3 | ex-best-day | kill |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    names = [f"{m}_{LAT}_{r}" for r in STRAND_RULES for m in MODELS]
    for name in names:
        L += ["", f"## Deciding sub-cell {name} (rent 0, 1.9 s, 0.25 SOL; SOL per trade)", "", *head]
        L += [_row(b, c["subcells"][name]) for b, c in rep["cells"].items() if name in c["subcells"]]
    for name in names:
        L += ["", f"## Rent-charged leg beside {name} (report-only; DEC-031 Amendment 2 item 7)", "", *head]
        L += [_row(b, {**c["subcells"][name]["rent_charged"], "fills": c["subcells"][name]["fills"]}) for b, c in rep["cells"].items() if name in c["subcells"]]
    L += ["", "## Entry impact (bps of price, own buy at the 1.9 s landing state)", "", "| cell | 0.25 SOL n | mean | median | p90 | max | 1.0 SOL n | mean | median | p90 | max |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    g = lambda v: "None" if v is None else f"{v:.1f}"  # noqa: E731
    for b, c in rep["cells"].items():
        a, d = c["entry_impact"]["0.25"], c["entry_impact"]["1.0"]
        L.append(f"| {b} | {a['n']} | {g(a['mean_bps'])} | {g(a['median_bps'])} | {g(a['p90_bps'])} | {g(a['max_bps'])} | {d['n']} | {g(d['mean_bps'])} | {g(d['median_bps'])} | "
                 f"{g(d['p90_bps'])} | {g(d['max_bps'])} |")
    L += ["", "## Exclusions by reason", "", "| cell | no attempt (rule) | refused buys | excluded trips | unsold at end | late prints |", "|---|---|---|---|---|---|"]
    for b, c in rep["cells"].items():
        e = c["exclusions"]
        L.append(f"| {b} | {canon(e['no_attempt_by_rule'])} | {canon(e['refused_buys'])} | {canon(e['excluded_trips'])} | {e['unsold_at_window_end']} | {e['late_prints']} |")
    L += ["", f"Host counters: `{canon(rep['host']['counters'])}`", f"Engine counters: `{canon(rep['engine']['counters'])}`", ""]
    return "\n".join(L)


def write_report(scratch: Path, rep: Mapping[str, Any]) -> tuple[Path, Path]:
    j, m = scratch / "REPORT.json", scratch / "REPORT.md"
    j.write_text(json.dumps(rep, indent=1, sort_keys=True, allow_nan=False) + "\n")
    m.write_text(render_md(rep))
    return j, m


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="J3 W_e exploration (QP R9(d)): paper only. Reads the given closed tape hours and nothing else under /data/mal/arena.")
    ap.add_argument("--hours", nargs="+", required=True, help="closed tape hour files (tape-<hour>.jsonl.zst) inside [2026-10-19T08, 2026-10-20T08), consecutive")
    ap.add_argument("--scratch", required=True, help="an empty directory outside /data/mal/arena: scratch registry, decision logs, ledger and the report")
    ap.add_argument("--min-free-pct", type=float, default=MIN_FREE_PCT_DEFAULT, help="the ledger's free-space floor of the scratch filesystem (arena default 25, never below 20)")
    ap.add_argument("--no-hash", action="store_true", help="do not sha256 the input files")
    a = ap.parse_args(argv)
    try:
        rep = run_exploration(a.hours, a.scratch, min_free_pct=a.min_free_pct, hash_inputs=not a.no_hash)
        j, m = write_report(Path(a.scratch).resolve(), rep)
    except (ExploreRefused, HostError, HostFatal, Refused, ValueError) as e:
        print(f"arena_j3_explore: {type(e).__name__}: {str(e)[:400]}", file=sys.stderr)
        return 2
    print(f"valid={rep['valid']} cells={len(rep['cells'])} alive={rep['kill_summary']['alive']} decision_log_md5={rep['digests']['decision_log_md5']}")
    print(f"wrote {j} and {m}")
    return 0 if rep["valid"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
