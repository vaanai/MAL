"""Arena gate scorer (hunt20): CLAUDE.md's promotion gate read per DEC-031 section 2, on forward-paper arena ledgers. READ-ONLY on the ledger. PAPER ONLY.

    python -m tools.arena_score [--bot BOT_ID] [--json out.json] [--now-ms MS] [--record] [--worst-in-slot GAPS.json] [--ledger-root DIR] [--registry FILE] [--reads-dir DIR]

ONE READ PER bot_id, AT AN OUTCOME-BLIND INSTANT. `blind_read_instant` takes decision timestamps only and loads no fill, exit or P&L: it is the first 00:00Z
boundary B at which the book has at least 100 attempts (every entry decision: open rows and refused buys) over at least 5 UTC dates, counted from the
registered clock. The read covers the attempts decided before B. Before B the status is NOT_READY. `--record` writes the read once to the reads dir; a second
record for the same bot_id is refused (a FAIL or NOT_DECIDABLE is final). Without `--record` the output is a PREVIEW, not the read.

COUNTING (stated, per the re-review): the boundary counts ATTEMPTS (every entry decision, DEC-031 section 2), outcome-blind; the gate's n >= 100 is checked on the
PRICED TRADES at the deciding stake AFTER the exclusions (the cell n). A book that reaches the boundary on exactly 100 attempts and has any exclusion therefore
fails n >= 100: that is the arrangement of DEC-031, and the read prints attempts, priced trades and exclusions side by side.
ledger_sha256 in the read and in --daily-digest is informational (later rows are appended to an entry-day file); decision_log_sha256 is the stable commitment.

CLOCK (stated): the freeze-clock filter and the day of a trade use the engine's WRITE instant (decision_ms = max(the bot's stamp, the call clock, the latest feed
row)), not the bot's stamp. A decision stamped before the clock but written after it is therefore counted (its stamp and write lag are on the row); one written
before the clock is not. The scorer prints a WARNING when it reads a test_override registry line (possible only with MAL_ARENA_TEST=1).

ORDER OF OPERATIONS (fixed, pinned by a test): clock filter -> read boundary -> DEC-031 exclusions -> pressure refit -> statistics.

STATUS (the first that applies):
  REFUSED         no registry line with deciding_stake, model_ids and the verification record (verified_sha, real-clock registration); a changed frozen_sha; an open row whose code_head is not the registered frozen_sha or whose code_dirty is not false (the frozen code is bound to the process), or an open row whose engine start record (code state captured once at engine start) is missing, not the
                  registered frozen_sha, or dirty; a duplicated trade_id; mixed stack configs inside one bot
  NOT_READY       the boundary is not reached (or not reached yet by --now-ms)
  PENDING         an attempt before B has no close yet and is younger than the settle window (default 24 h): not a verdict, and --record refuses it
  NOT_DECIDABLE   a restart orphan (an attempt before B with no priced or excluded close that is OLDER than the settle window; DEC-014 Am.1), or excluded
                  + data-refused attempts above 5% of the book's attempts (DEC-031 section 1.4). Refused buys count toward the 5% only when the reason is a data
                  issue (no_prints, no_feed_rows, clock_not_ready); stale_decision / future_decision / feed_delay_unset are reported apart.
                  Also NOT_DECIDABLE: any INCIDENT before the read boundary (<root>/<bot>/incidents.jsonl: an append refused on a damaged day file, a lost
                  buy / sell), even if the file was repaired afterwards; and a torn or unparseable line in the bot's files or incident file (reason printed;
                  the other bots are still scored).
  REPORT_ONLY     any bonding row in the book (its costs are unmeasured; quant-proof on #708), or the book was not priced under both strand rules
  PASS / FAIL     (a book whose only misses are n < 100 prints "FAIL (n < 100)": final, but not a loss) the DECIDING CELL only: 1.9 s at the stake pinned in bots.json, flat 15% AND pressure-fail, each under BOTH strand rules ("sc" and the
                  DEC-031 strand class "total"): four sub-cells, all must pass. A sub-cell passes iff n >= 100, >= 5 UTC days with a strict majority positive,
                  CI90 lower bound of mean SOL / trade > 0 (trade-level bootstrap, 1,000 draws, seed 1, 5th percentile: tools.arena_stack.boot_lo) and total
                  ex top 3 trades > 0. The 1.3 s leg and the other stakes are report-only and never decide. "weakest_cell" is the lowest-CI cell of the
                  deciding stake (information only).
FORCED BOUNDARY (--boundary-ms): a comparator SCREEN, never the bot's one read. Its status is printed "FORCED-SCREEN-ONLY: <status>" with a "not a gate read" banner.
Compare two arms at `common_boundary(a, b)` (--common-boundary A B): the earlier of the two natural instants. A forced boundary LATER than a bot's own natural
instant is allowed only if that bot's own read is already recorded as FAIL, FAIL (n < 100), NOT_DECIDABLE or REPORT_ONLY (and then it prints the recorded status:
"comparator, past its own instant: not a gate read"); refused if it was recorded PASS (its later attempts are its confirmation window) or is not recorded yet.
A PASS whose total is not positive ex best day prints "not proposed for canary". A PASS is a screen, the best of the books printed in the header
(winner's curse), not a promotion.

PRESSURE LEG: refit per (bot, stake, latency, strand rule) on exactly the covered, non-excluded attempts so that the mean fail probability over the book's
non-guarded sends is 0.289 with slopes 0.8 / 0.35 (tools.arena_stack.fail_legs). The fitted intercept is printed per cell. Limitation: the refit fixes each book's
mean fail rate at 0.289, so a bot that trades only in congested slots is not charged more in absolute terms. A strand (total-loss class) bypasses the blend.
derived_last rows are counted at their value and printed (count and share per cell); a re-run without them is report-only.
"""

from __future__ import annotations

import argparse
import bisect
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

import hashlib
import os
from datetime import datetime, timezone

from tools.arena_paper import (
    LEDGER_ROOT,
    REGISTRY_PATH,
    TEST_ENV,
    Ledger,
    book_fail_legs,
    canon,
    load_registry,
    utc_day,
)
from tools.arena_stack import boot_lo, fail_legs

LATS = ("1p3", "1p9")
MODELS = ("flat", "press")
RULES = ("total", "sc")
DAY_MS = 86_400_000
MIN_N, MIN_DAYS = 100, 5
MAX_EXCLUDED_SHARE = 0.05
READS_DIR = "/data/mal/arena/reads"
SETTLE_MS = 24 * 3_600_000  # an unclosed attempt younger than this is PENDING, older is a restart orphan
DATA_REFUSALS = ("no_prints", "no_feed_rows", "clock_not_ready")  # refused buys that are data issues and count toward the 5% cap
STEPS = ("clock_filter", "read_boundary", "exclusions", "pressure_refit", "statistics")
CONFIG_KEYS_IGNORED = ("sps_entry", "sps_exit")
# A forced boundary LATER than the bot's own outcome-blind instant is allowed only if the bot's own read is already RECORDED with one of these statuses
# ("FAIL (n < 100)" is status FAIL with a fail_label). A recorded PASS refuses it (its later attempts are its confirmation window), and so does an unrecorded read.
LATE_FORCE_OK = ("FAIL", "NOT_DECIDABLE", "REPORT_ONLY")
FORCED_PREFIX = "FORCED-SCREEN-ONLY: "
BANNER_FORCED = "not a gate read"
BANNER_PAST = "comparator, past its own instant: not a gate read"


class Refused(Exception):
    pass


# ------------------------------------------------------------------------------------------------------------------------------ outcome-blind instant
def load_decisions(ledger_root: str | Path, bot_id: str) -> list[int]:
    """Entry-decision timestamps of a bot's attempts (open rows and refused buys). Lines of any other kind (fills, exits, P&L) are never parsed."""
    out: list[int] = []
    d = Path(ledger_root) / bot_id
    for p in sorted(d.glob("trades-*.jsonl")) if d.exists() else []:
        for i, line in enumerate(p.read_text().splitlines(), 1):
            if line.startswith('{"kind":"open"') or line.startswith('{"kind":"refused"'):
                try:
                    out.append(int(json.loads(line)["decision_ms"]["entry"]))
                except (ValueError, KeyError, TypeError):
                    raise Refused(f"{bot_id}: {p.name} line {i} is a torn or unparseable decision line: the outcome-blind instant cannot be computed") from None
    return out


def blind_read_instant(entry_ms: Sequence[int], clock_start_ms: int) -> int | None:
    """The first 00:00Z boundary B with >= 100 attempts over >= 5 UTC dates among the attempts decided in [clock_start, B). Decision timestamps only."""
    ms = sorted(int(m) for m in entry_ms if int(m) >= clock_start_ms)
    if len(ms) < MIN_N:
        return None
    b = (ms[0] // DAY_MS + 1) * DAY_MS
    last = (ms[-1] // DAY_MS + 1) * DAY_MS
    while b <= last:
        k = bisect.bisect_left(ms, b)
        if k >= MIN_N and len({m // DAY_MS for m in ms[:k]}) >= MIN_DAYS:
            return b
        b += DAY_MS
    return None


def natural_instant(ledger_root: str | Path, registry: str, bot_id: str) -> int | None:
    """A bot's own outcome-blind instant from decision timestamps only (the same number score_bot computes)."""
    reg = load_registry(registry)
    if bot_id not in reg:
        raise Refused(f"{bot_id}: not in the registry")
    return blind_read_instant(load_decisions(ledger_root, bot_id), reg[bot_id]["clock_start_ms"])


def common_boundary(bot_a: str, bot_b: str, ledger_root: str | Path = LEDGER_ROOT, registry: str = REGISTRY_PATH) -> int:
    """The earlier of the two bots' natural outcome-blind instants: the normal way to compare two arms. Scoring both at it (`--boundary-ms`) puts each at or
    before its own instant (always allowed) and on the same attempt boundary. Refused if either bot has no natural instant yet (an unnamed default would silently
    score the arms on different boundaries)."""
    ns = {b: natural_instant(ledger_root, registry, b) for b in (bot_a, bot_b)}
    missing = sorted(b for b, n in ns.items() if n is None)
    if missing:
        raise Refused(f"no common boundary yet: {', '.join(missing)} has not reached 100 attempts over 5 UTC dates (no outcome-blind instant)")
    return min(ns.values())  # type: ignore[type-var]


def load_recorded_read(reads_dir: str | Path, bot_id: str) -> dict[str, Any] | None:
    """The bot's recorded read, or None when there is none. An unreadable file is returned as {"status": "UNREADABLE"}: it is a recorded read of unknown status."""
    p = Path(reads_dir) / f"{bot_id}.json"
    if not p.exists():
        return None
    try:
        r = json.loads(p.read_text())
        return r if isinstance(r, dict) else {"status": "UNREADABLE"}
    except (OSError, ValueError):
        return {"status": "UNREADABLE"}


def check_late_force(bot_id: str, boundary_ms: int, natural: int, recorded: Mapping[str, Any] | None) -> str:
    """A forced boundary later than the bot's own natural instant (quant-proof ruling on #716). Allowed only when the bot's own read is already recorded as FAIL,
    FAIL (n < 100), NOT_DECIDABLE or REPORT_ONLY. Refused when it is recorded PASS (the attempts after its instant are its confirmation window, DEC-031 section 2
    step 4: a read past it shows them before its one confirmation read; 'never recorded' does not cure that, the look is the breach) and when it is not recorded
    (the status is unknown, so a PASS cannot be excluded). Returns the recorded status label."""
    head = f"{bot_id}: the forced boundary {boundary_ms} is later than the bot's own outcome-blind instant {natural}"
    if recorded is None:
        raise Refused(f"{head} and its own read is not recorded yet: its status is unknown, so a PASS cannot be excluded "
                      "(its attempts after that instant are its confirmation window); compare the arms at common_boundary instead")
    st = recorded.get("status")
    if st == "PASS":
        raise Refused(f"{head} and its own read is recorded PASS: the attempts decided after its instant are its confirmation window (DEC-031 section 2 step 4)")
    if st not in LATE_FORCE_OK or recorded.get("boundary_source") == "forced":
        raise Refused(f"{head} and its recorded read has status {st!r}: only FAIL, FAIL (n < 100), NOT_DECIDABLE or REPORT_ONLY allow it")
    if recorded.get("bot_id") != bot_id or recorded.get("read_boundary_ms") != natural:
        raise Refused(f"{head}, but the recorded read is not this bot's read at that instant (recorded bot {recorded.get('bot_id')}, boundary "
                      f"{recorded.get('read_boundary_ms')}): refused")
    return str(recorded.get("fail_label") or st)


# ------------------------------------------------------------------------------------------------------------------------------ incidents
def incident_instant(i: Mapping[str, Any]) -> int:
    """The instant an incident concerns: the earlier of when it was written (at_ms) and the decision instant of the attempt it lost (row_decision_ms /
    decision_ms), so that an incident that lost an attempt decided before the boundary counts even if it was recorded after it."""
    ts = [int(i["at_ms"])] + [int(i[k]) for k in ("row_decision_ms", "decision_ms") if isinstance(i.get(k), int) and not isinstance(i.get(k), bool)]
    return min(ts)


def incident_summary(incidents: Sequence[Mapping[str, Any]], b: int | None) -> dict[str, Any]:
    """What the incident file says, for the read. before_boundary = incidents whose instant is before the read boundary b (None while there is no boundary)."""
    before = [i for i in incidents if b is not None and incident_instant(i) < b]
    return {"n": len(incidents), "kinds": dict(Counter(i["kind"] for i in incidents)), "before_boundary": None if b is None else len(before),
            "first_at_ms": min((int(i["at_ms"]) for i in incidents), default=None), "lost_buys": sum(1 for i in incidents if i["kind"] == "buy_refused"),
            "files": sorted({str(i.get("day_file")) for i in incidents if i.get("day_file")})}


# ------------------------------------------------------------------------------------------------------------------------------ statistics
def cell_stats(x_lamports: Sequence[float], days: Sequence[str]) -> dict[str, Any]:
    """Gate statistics of one cell. x in lamports per trade, days = UTC date of each trade's entry decision."""
    x = np.asarray(x_lamports, float)
    n = len(x)
    if n == 0:
        return {"n": 0, "days": 0, "days_pos": 0, "mean_sol": None, "total_sol": 0.0, "ci90_lo_sol": None, "ex_top3_sol": None, "ex_best_day_sol": None, "daily": {}}
    ud, inv = np.unique(np.asarray(days), return_inverse=True)
    dsum = np.bincount(inv, weights=x) / 1e9
    dcnt = np.bincount(inv)
    srt = np.sort(x)[::-1]
    return {"n": int(n), "days": int(len(ud)), "days_pos": int((dsum > 0).sum()), "mean_sol": float(x.mean() / 1e9), "total_sol": float(x.sum() / 1e9),
            "ci90_lo_sol": boot_lo(x / 1e9), "ex_top3_sol": float(srt[3:].sum() / 1e9), "ex_best_day_sol": float(x.sum() / 1e9 - dsum.max()),
            "daily": {str(d): {"n": int(c), "sol": float(s)} for d, c, s in zip(ud, dcnt, dsum)}}


def cell_verdict(s: Mapping[str, Any]) -> dict[str, Any]:
    miss = []
    if s["n"] < MIN_N:
        miss.append(f"n {s['n']} < {MIN_N}")
    if s["days"] < MIN_DAYS:
        miss.append(f"days {s['days']} < {MIN_DAYS}")
    if s["days"] and s["days_pos"] * 2 <= s["days"]:
        miss.append(f"days positive {s['days_pos']}/{s['days']} is not a majority")
    if s["ci90_lo_sol"] is None or not s["ci90_lo_sol"] > 0:
        miss.append(f"CI90 lo {s['ci90_lo_sol']} <= 0")
    if s["ex_top3_sol"] is None or not s["ex_top3_sol"] > 0:
        miss.append(f"ex-top-3 {s['ex_top3_sol']} <= 0")
    return {"pass": not miss, "misses": miss}


def _legs(rule: str, pnl, fil, strand, ssb, near, send, intercept=None):
    if rule == "total":
        return book_fail_legs(pnl, fil, strand, ssb, near, send, intercept=intercept)
    return fail_legs(pnl, fil, ssb, near, send, intercept=intercept)


def book_cells(trades: Sequence[Mapping[str, Any]], rules: Sequence[str]) -> dict[str, Any]:
    """All cells of one (bot, stake) book: model x latency x strand rule. The pressure intercept is refit here, on exactly these trades."""
    cells: dict[str, Any] = {}
    days = [utc_day(r["decision_ms"]["entry"]) for r in trades]
    derived = np.array([r.get("state_src") == "derived_last" for r in trades], bool)
    for rule in rules:
        for lat in LATS:
            pnl = np.array([r["calc"][lat]["by_rule"][rule]["pnl_nofail"] for r in trades], float)
            fil = np.array([r["calc"][lat]["by_rule"][rule]["filled"] for r in trades], bool)
            strand = np.array([r["calc"][lat]["by_rule"][rule]["strand"] for r in trades], bool)
            ssb = np.array([r["calc"][lat]["ssb"] for r in trades], float)
            near = np.array([r["calc"][lat]["nearby_lamports"] for r in trades], float)
            send = float(trades[0]["stack"]["send"])
            flat, press, c, _ = _legs(rule, pnl, fil, strand, ssb, near, send)
            c0 = float(trades[0]["stack"]["press_intercept_provisional"])
            _, press_fx, _, p_fx = _legs(rule, pnl, fil, strand, ssb, near, send, intercept=c0)
            for i, r in enumerate(trades):  # the stored flat leg must equal the recomputed one (it has no book-level parameter)
                if r["legs"][rule][f"flat_{lat}"]["net_lamports"] != float(flat[i]):
                    raise Refused(f"trade {r['trade_id']}: stored flat leg differs from the recomputed flat leg ({rule}, {lat})")
            for model, x in (("flat", flat), ("press", press)):
                s = cell_stats(x, days)
                fx = None
                if model == "press":  # the fixed-intercept pressure leg, REPORT-ONLY beside the refit: it never decides
                    sf = cell_stats(press_fx, days)
                    fx = {"report_only": True, "intercept": c0, "mean_p": float(p_fx[fil].mean()) if fil.any() else None, "mean_sol": sf["mean_sol"],
                          "total_sol": sf["total_sol"], "ci90_lo_sol": sf["ci90_lo_sol"], "ex_top3_sol": sf["ex_top3_sol"]}
                cells[f"{model}_{lat}_{rule}"] = dict(s, **cell_verdict(s), intercept=float(c) if model == "press" else None, fixed_intercept=fx,
                                                      derived_last_n=int(derived.sum()),
                                                      derived_last_share=float(derived.mean()) if len(trades) else None,
                                                      without_derived_last=dict(cell_stats(x[~derived], [d for d, k in zip(days, derived) if not k]),
                                                                                report_only=True))
    return cells


def load_worst_gaps(path: str | Path) -> dict[str, float]:
    """The worst-in-slot input (DEC-032 section 1 item 2, QP ruling R7): a JSON object {trade_id: gap_lamports}, the cost of the worst-in-slot fills over the
    engine's END-bound fills (>= 0), computed from ledger and tape by a separate tape tool. This scorer reads no tape."""
    try:
        g = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise Refused(f"worst-in-slot file {path} is unreadable: {exc}") from None
    ok = isinstance(g, dict) and all(isinstance(k, str) and isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 and v != float("inf") for k, v in g.items())
    if not ok:
        raise Refused(f"worst-in-slot file {path}: expected an object {{trade_id: gap_lamports >= 0}}")
    return {k: float(v) for k, v in g.items()}


def worst_in_slot_cells(trades: Sequence[Mapping[str, Any]], gaps: Mapping[str, float], rules: Sequence[str]) -> dict[str, Any]:
    """REPORT-ONLY worst-in-slot leg of the deciding book at 1.9 s (QP ruling R7): each trade's no-fail P&L minus its END-minus-worst gap, re-run through the same
    fail legs as the book's cells. Never decides. A trade without a gap makes the figure 'not measured' (no partial coverage). Per sub-cell: mean and CI90 lower
    bound (trade-level bootstrap, 1,000 draws, seed 1, 5th percentile); the headline is the lowest of the sub-cells."""
    missing = sorted(r["trade_id"] for r in trades if r["trade_id"] not in gaps)
    if missing:
        return {"measured": False, "reason": f"worst-in-slot not measured: {len(missing)} of {len(trades)} deciding trade(s) have no gap (first: {missing[0]})"}
    days = [utc_day(r["decision_ms"]["entry"]) for r in trades]
    gap = np.array([gaps[r["trade_id"]] for r in trades], float)
    cells: dict[str, Any] = {}
    lat = "1p9"
    for rule in rules:
        pnl = np.array([r["calc"][lat]["by_rule"][rule]["pnl_nofail"] for r in trades], float)
        fil = np.array([r["calc"][lat]["by_rule"][rule]["filled"] for r in trades], bool)
        strand = np.array([r["calc"][lat]["by_rule"][rule]["strand"] for r in trades], bool)
        ssb = np.array([r["calc"][lat]["ssb"] for r in trades], float)
        near = np.array([r["calc"][lat]["nearby_lamports"] for r in trades], float)
        send = float(trades[0]["stack"]["send"])
        live = fil & ~strand  # an unfilled trade or a total-loss strand has no slot fill to worsen
        pnl_w = np.where(live, pnl - gap, pnl)
        flat, press, _, _ = _legs(rule, pnl_w, fil, strand, ssb, near, send)
        for model, x in (("flat", flat), ("press", press)):
            s = cell_stats(x, days)
            cells[f"{model}_{lat}_{rule}"] = {"n": s["n"], "mean_sol": s["mean_sol"], "total_sol": s["total_sol"], "ci90_lo_sol": s["ci90_lo_sol"],
                                              "mean_gap_sol": float(gap[live].mean() / 1e9) if live.any() else None}
    means = [c["mean_sol"] for c in cells.values() if c["mean_sol"] is not None]
    los = [c["ci90_lo_sol"] for c in cells.values() if c["ci90_lo_sol"] is not None]
    return {"measured": True, "report_only": True, "latency": lat, "mean_sol": min(means) if means else None, "ci90_lo_sol": min(los) if los else None,
            "worst_cell": min(cells, key=lambda k: cells[k]["mean_sol"]) if cells else None, "mean_positive_all_cells": bool(means) and min(means) > 0, "cells": cells}


# ------------------------------------------------------------------------------------------------------------------------------ one bot
def _entry(r: Mapping[str, Any]) -> int:
    return int(r["decision_ms"]["entry"])


def check_bot_rows(bot_id: str, reg: Mapping[str, Mapping[str, Any]], rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Refusals. Returns the common stack config (without the per-trade slot clock values)."""
    if bot_id not in reg:
        raise Refused(f"{bot_id}: ledger rows but no registry entry")
    e = reg[bot_id]
    if e.get("deciding_stake") is None or e.get("model_ids") is None:
        raise Refused(f"{bot_id}: the registry line lacks deciding_stake / model_ids (DEC-031 section 1.2)")
    if os.environ.get(TEST_ENV) != "1" and (e.get("time_source") != "wall_clock" or not e.get("verified_sha")):
        raise Refused(f"{bot_id}: the registry line lacks the verification record (verified_sha, wall-clock registration): refused")
    shas = sorted({r["frozen_sha"] for r in rows if "frozen_sha" in r})
    if shas and shas != [e["frozen_sha"]]:
        raise Refused(f"{bot_id}: frozen_sha in the ledger {shas} != registered {e['frozen_sha']} (a new version is a new bot_id)")
    bad = [r for r in rows if r.get("kind") == "open" and _entry(r) >= e["clock_start_ms"] and (r.get("code_head") != e["frozen_sha"] or r.get("code_dirty") is not False)]
    if bad:
        raise Refused(f"{bot_id}: {len(bad)} open row(s) were written by a process whose code is not the registered frozen_sha or whose tree was dirty "
                      f"(first: code_head {bad[0].get('code_head')}, code_dirty {bad[0].get('code_dirty')}, registered {e['frozen_sha']}): the frozen code is bound to the process")
    starts = {r["engine_id"]: r for r in rows if r.get("kind") == "start" and r.get("engine_id")}
    bad_start = [r for r in rows if r.get("kind") == "open" and _entry(r) >= e["clock_start_ms"]
                 and not ((st := starts.get(r.get("engine_id"))) and st.get("code_head") == e["frozen_sha"] and st.get("code_dirty") is False)]
    if bad_start:
        raise Refused(f"{bot_id}: {len(bad_start)} open row(s) point at an engine start record that is missing or does not match (the engine must have started clean "
                      f"on the registered frozen_sha {e['frozen_sha']}; first engine_id {bad_start[0].get('engine_id')})")
    ids = [r["trade_id"] for r in rows]
    if len(ids) != len(set(ids)):
        raise Refused(f"{bot_id}: duplicated trade_id in the ledger")
    cfgs = {canon({k: v for k, v in r["stack"].items() if k not in CONFIG_KEYS_IGNORED}) for r in rows if r.get("stack")}
    if len(cfgs) > 1:
        raise Refused(f"{bot_id}: mixed stack configs inside one bot ({len(cfgs)} distinct): refused")
    return json.loads(next(iter(cfgs))) if cfgs else {}


def score_bot(bot_id: str, reg: Mapping[str, Mapping[str, Any]], rows: Sequence[Mapping[str, Any]], now_ms: int, trace: list[str] | None = None,
              settle_ms: int = SETTLE_MS, boundary_ms: int | None = None, *, incidents: Sequence[Mapping[str, Any]] = (),
              ledger_problems: Sequence[str] = (), comparator_read: Mapping[str, Any] | None = None,
              worst_gaps: Mapping[str, float] | None = None) -> dict[str, Any]:
    """Score one bot. `boundary_ms` (optional) FORCES the read boundary so that comparator arms can be scored over the SAME attempt boundary as the book being read
    (the normal way: `common_boundary(a, b)`, the earlier of the two natural instants). It must be a 00:00Z instant. At or before this bot's own outcome-blind
    instant it is always allowed. LATER than it, it is allowed only if `comparator_read` (the bot's own RECORDED read, from the reads dir) has status FAIL, FAIL (n < 100),
    NOT_DECIDABLE or REPORT_ONLY: refused when that read is PASS (its later attempts are its confirmation window) or not recorded yet; the output then says
    past_own_instant and prints the recorded status. The output says boundary_source "forced" (and keeps natural_boundary_ms) or "natural". A forced read is a
    comparator SCREEN, never the bot's one read: its status is prefixed "FORCED-SCREEN-ONLY: " with a "not a gate read" banner (so it cannot be quoted as a verdict)
    and record_read refuses it. Default behaviour is unchanged.

    `incidents` are the lines of the bot's incidents.jsonl (a refused append on a damaged day file, a lost buy): any incident before the read boundary makes the read
    NOT_DECIDABLE, even after the day file was repaired. `ledger_problems` are the torn / unparseable lines found in the bot's files (and incident file): they make
    THIS bot NOT_DECIDABLE with those reasons (the caller scored the lines that did parse)."""
    try:
        out = _score_bot(bot_id, reg, rows, now_ms, trace, settle_ms, boundary_ms, incidents, comparator_read, worst_gaps)
    except Refused as exc:
        if ledger_problems:
            raise Refused(f"{exc} [also: {len(ledger_problems)} ledger problem(s), first: {ledger_problems[0]}]") from None
        raise
    if out.get("pending") and out["status"] not in ("NOT_DECIDABLE", "NOT_READY"):
        out["preview_status"] = out["status"]
        if "fail_label" in out:
            out["preview_fail_label"] = out.pop("fail_label")  # a pending book prints PENDING, not a provisional FAIL label
        out["status"] = "PENDING"
        out["reasons"].append(f"{out['pending']} attempt(s) before the boundary are still open and inside the settle window: not a verdict, not recordable")
    out["ledger_problems"] = list(ledger_problems)
    if ledger_problems:  # a damaged file: this bot's attempts cannot be accounted for, whatever the lines that parsed say
        out["status"] = "NOT_DECIDABLE"
        out.pop("fail_label", None)
        out["reasons"].append(f"{len(ledger_problems)} torn or unparseable ledger line(s) in this bot's files: attempts may be lost (DEC-014 Amendment 1); "
                              f"first: {ledger_problems[0]}")
    out["gate_read"] = out["boundary_source"] != "forced"
    if not out["gate_read"]:  # a forced boundary is a comparator screen: no status of it can be quoted as a verdict
        out["banner"] = BANNER_PAST if out["past_own_instant"] else BANNER_FORCED
        if out["past_own_instant"]:
            out["banner"] += f" (the comparator's own recorded read: {out['comparator_recorded_status']})"
        out["status"] = FORCED_PREFIX + out["status"]
        for k in ("fail_label", "preview_status", "preview_fail_label"):
            if k in out:
                out[k] = FORCED_PREFIX + out[k]
    return out


def _score_bot(bot_id: str, reg: Mapping[str, Mapping[str, Any]], rows: Sequence[Mapping[str, Any]], now_ms: int, trace: list[str] | None, settle_ms: int,
               boundary_ms: int | None = None, incidents: Sequence[Mapping[str, Any]] = (), comparator_read: Mapping[str, Any] | None = None,
               worst_gaps: Mapping[str, float] | None = None) -> dict[str, Any]:
    tr = trace if trace is not None else []
    config = check_bot_rows(bot_id, reg, rows)
    e = reg[bot_id]
    out: dict[str, Any] = {"bot_id": bot_id, "frozen_sha": e["frozen_sha"], "clock_start_ms": e["clock_start_ms"], "deciding_stake": e["deciding_stake"],
                           "model_ids": e["model_ids"], "stopped_at_ms": e.get("stopped_at_ms"), "config": config, "rows_total": len(rows), "reasons": [], "warnings": []}
    if e.get("time_source") != "wall_clock" or not e.get("verified_sha"):
        out["warnings"].append("the registry line is a test_override (fake clock / unverified sha): read only because MAL_ARENA_TEST=1 is set; this is not a production read")
    # 1. clock filter
    tr.append("clock_filter")
    counted = [r for r in rows if _entry(r) >= e["clock_start_ms"]]
    out["rows_before_clock"] = len(rows) - len(counted)
    # 2. read boundary (outcome-blind: decision timestamps only)
    tr.append("read_boundary")
    natural = blind_read_instant([_entry(r) for r in counted if r["kind"] in ("open", "refused")], e["clock_start_ms"])
    b = natural
    out["boundary_source"], out["natural_boundary_ms"], out["past_own_instant"] = "natural", natural, False
    if boundary_ms is not None:
        if int(boundary_ms) % DAY_MS:
            raise Refused(f"{bot_id}: a forced boundary must be a 00:00Z instant, got {boundary_ms}")
        if natural is None:
            raise Refused(f"{bot_id}: cannot force a boundary: the bot has no outcome-blind instant yet (fewer than 100 attempts over 5 UTC dates)")
        if int(boundary_ms) > natural:  # past the bot's own instant: only if its own read is already recorded and was not a PASS
            out["comparator_recorded_status"] = check_late_force(bot_id, int(boundary_ms), natural, comparator_read)
            out["past_own_instant"] = True
        b, out["boundary_source"] = int(boundary_ms), "forced"
    out["read_boundary_ms"] = b
    out["incidents"] = incident_summary(incidents, b)
    if b is None or now_ms < b:
        out["status"] = "NOT_READY"
        out["reasons"].append("the book has not yet reached 100 attempts over 5 UTC dates at a 00:00Z boundary" if b is None else "now is before the read boundary")
        if incidents:
            out["reasons"].append(f"{len(incidents)} ledger incident(s) on record ({out['incidents']['kinds']}): an incident before the boundary makes the read NOT_DECIDABLE")
        return out
    covered = [r for r in counted if _entry(r) < b]
    opens = {r["position_id"]: r for r in covered if r["kind"] == "open"}
    refused = [r for r in covered if r["kind"] == "refused"]
    closed = {r["position_id"] for r in covered if r["kind"] in ("trade", "excluded")}
    unclosed = sorted(set(opens) - closed)
    orphans = [pid for pid in unclosed if now_ms - _entry(opens[pid]) >= settle_ms]
    pending = [pid for pid in unclosed if pid not in orphans]
    data_refused = [r for r in refused if r["reason"] in DATA_REFUSALS]
    late = [r for r in covered if r["kind"] == "late_print"]
    out["attempts"] = len(opens) + len(refused)
    out["refused_buys"] = dict(Counter(r["reason"] for r in refused))
    out["restart_orphans"] = len(orphans)
    out["pending"] = len(pending)
    out["settle_ms"] = settle_ms
    out["refused_data_issues"] = len(data_refused)
    out["refused_other"] = dict(Counter(r["reason"] for r in refused if r["reason"] not in DATA_REFUSALS))  # tolerance / config: reported, outside the 5%
    out["late_prints"] = len(late)
    # 3. DEC-031 exclusions
    tr.append("exclusions")
    late_gap = {r["position_id"] for r in late if r.get("gap_overlap")}
    by_stake: dict[float, dict[str, list]] = defaultdict(lambda: {"trades": [], "excluded": []})
    for r in covered:
        if r["kind"] == "trade":
            vs = r.get("v_src") or {}
            if r["position_id"] in late_gap:
                by_stake[r["stake_sol"]]["excluded"].append(dict(r, exclusion_reason="late_print_gap"))
            elif "none" in (vs.get("entry"), vs.get("exit")) or "none" in (vs.get("span") or {}):
                by_stake[r["stake_sol"]]["excluded"].append(dict(r, exclusion_reason="missing_v"))
            else:
                by_stake[r["stake_sol"]]["trades"].append(r)
        elif r["kind"] == "excluded":
            by_stake[r["stake_sol"]]["excluded"].append(r)
    books: dict[str, Any] = {}
    n_attempts = len(opens) + len(refused)
    for st in sorted(by_stake):
        ex = Counter(x["exclusion_reason"] for x in by_stake[st]["excluded"])
        books[str(st)] = {"stake": st, "n_trades": len(by_stake[st]["trades"]), "excluded": dict(ex), "refused": len(data_refused),
                          "excluded_share": (sum(ex.values()) + len(data_refused)) / n_attempts if n_attempts else None}
    out["books"] = books
    # 4. pressure refit, 5. statistics (per book, on the post-exclusion trades)
    tr.append("pressure_refit")
    tr.append("statistics")
    has_bonding = False
    for st, bk in books.items():
        trades = by_stake[float(st)]["trades"]
        has_bonding = has_bonding or any(r["prog"] == "pump_bonding" for r in trades + by_stake[float(st)]["excluded"])
        rules_present = [ru for ru in RULES if trades and ru in trades[0]["legs"]]
        bk["rules"] = rules_present
        bk["cells"] = book_cells(trades, rules_present) if trades else {}
    out["has_bonding"] = has_bonding
    # status
    dk = str(e["deciding_stake"])
    why = out["reasons"]
    if orphans:
        why.append(f"{len(orphans)} restart orphan(s): attempts before the boundary never closed within the settle window (DEC-014 Amendment 1)")
    over = {st: bk["excluded_share"] for st, bk in books.items() if bk["excluded_share"] is not None and bk["excluded_share"] > MAX_EXCLUDED_SHARE}
    if over:
        why.append(f"excluded + refused share above {MAX_EXCLUDED_SHARE:.0%}: " + ", ".join(f"stake {k}: {v:.1%}" for k, v in over.items()))
    inc_before = out["incidents"]["before_boundary"]
    if inc_before:
        why.append(f"{inc_before} ledger incident(s) before the boundary ({out['incidents']['kinds']}; {out['incidents']['lost_buys']} lost buy(s)): "
                   "a refused append on a damaged day file or a lost attempt, on record even if the file was repaired afterwards (DEC-014 Amendment 1)")
    if orphans or over or inc_before:
        out["status"] = "NOT_DECIDABLE"
        return out
    dec = books.get(dk)
    if dec is None or not dec["cells"]:
        out["status"] = "FAIL"
        out["fail_label"] = "FAIL (n < 100)"
        why.append(f"no priced trades at the deciding stake {dk}")
        return out
    subs = {k: v for k, v in dec["cells"].items() if "_1p9_" in k}
    out["deciding"] = {"stake": dk, "latency": "1p9", "subcells": sorted(subs), "pass": all(v["pass"] for v in subs.values()) and len(subs) == 4,
                       "weakest_cell": min((k for k in dec["cells"] if dec["cells"][k]["ci90_lo_sol"] is not None), key=lambda k: dec["cells"][k]["ci90_lo_sol"], default=None),
                       "misses": {k: v["misses"] for k, v in subs.items() if v["misses"]},
                       "not_proposed_for_canary": any((v["ex_best_day_sol"] is None or v["ex_best_day_sol"] <= 0) for v in subs.values())}
    if worst_gaps is not None:  # opt-in and report-only: the key is absent unless the flag is given, so every existing output is unchanged
        out["worst_in_slot"] = worst_in_slot_cells(by_stake[float(dk)]["trades"], worst_gaps, dec["rules"])
    if has_bonding:
        out["status"] = "REPORT_ONLY"
        why.append("a bonding row is in the book: its costs are unmeasured (no send / haircut DEC), so no deciding verdict")
    elif len(subs) != 4:
        out["status"] = "REPORT_ONLY"
        why.append(f"the deciding cell was not priced under both strand rules (rules present: {dec['rules']})")
    else:
        out["status"] = "PASS" if out["deciding"]["pass"] else "FAIL"
        misses = [m for ms in out["deciding"]["misses"].values() for m in ms]
        if out["status"] == "FAIL" and misses and all(m.startswith("n ") for m in misses):
            out["fail_label"] = "FAIL (n < 100)"  # fewer than 100 priced trades after the exclusions: final, but not a loss
    return out


def record_read(reads_dir: str | Path, res: Mapping[str, Any]) -> Path:
    """Write the one read of a bot_id. A second record for the same bot_id is refused."""
    d = Path(reads_dir)
    p = d / f"{res['bot_id']}.json"
    if res.get("boundary_source") == "forced":
        raise Refused(f"{res['bot_id']}: a forced-boundary read is a comparator score, not the bot's one read: refusing to record it")
    if res["status"] == "NOT_READY":
        raise Refused(f"{res['bot_id']}: not ready, nothing to record")
    if res.get("read_boundary_ms") is None or "attempts" not in res:  # no boundary, or not reached (a damaged-file preview prints NOT_DECIDABLE before it)
        raise Refused(f"{res['bot_id']}: the read boundary is not reached (status {res['status']}): the one read is made at the outcome-blind instant, nothing to record")
    if res.get("pending") or res["status"] == "PENDING":
        raise Refused(f"{res['bot_id']}: {res.get('pending')} covered attempt(s) still pending (inside the settle window): refusing to record a read that may change")
    d.mkdir(parents=True, exist_ok=True)
    try:
        with open(p, "x") as f:
            f.write(canon(res) + "\n")
    except FileExistsError:
        raise Refused(f"{res['bot_id']}: its one read is already recorded at {p}") from None
    return p


# ------------------------------------------------------------------------------------------------------------------------------ all bots
def score_all(ledger_root: str = LEDGER_ROOT, registry: str = REGISTRY_PATH, only: str | None = None, now_ms: int | None = None,
              settle_ms: int = SETTLE_MS, boundary_ms: int | None = None, reads_dir: str = READS_DIR, worst_gaps: Mapping[str, float] | None = None) -> dict[str, Any]:
    import time

    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    reg = load_registry(registry)
    led = Ledger(ledger_root)
    bots: dict[str, Any] = {}
    refused: dict[str, str] = {}
    for b in sorted(reg):
        if only and b != only:
            continue
        try:
            rows, problems = led.scan_bot(b)  # a damaged file makes THIS bot NOT_DECIDABLE (with the reason); it never stops the others
            incidents, inc_problems = led.read_incidents(b)
            recorded = load_recorded_read(reads_dir, b) if boundary_ms is not None else None  # only a forced boundary can need the bot's own recorded read
            bots[b] = score_bot(b, reg, rows, now, None, settle_ms, boundary_ms, incidents=incidents, ledger_problems=problems + inc_problems, comparator_read=recorded, worst_gaps=worst_gaps)
            bd = bots[b].get("read_boundary_ms")
            if bd is not None and not bots[b]["status"].endswith("NOT_READY"):  # informational file hashes of the covered entry days
                d0 = reg[b]["clock_start_ms"] // DAY_MS * DAY_MS
                bots[b]["ledger_files"] = {utc_day(d): digest_day(ledger_root, registry, utc_day(d), b)["bots"][b] for d in range(d0, bd, DAY_MS)}
                bots[b]["ledger_files_note"] = "ledger_sha256 is informational (rows are appended to an entry-day file later); decision_log_sha256 is the stable commitment"
        except Refused as exc:
            refused[b] = str(exc)
        except (KeyError, TypeError, ValueError, IndexError) as exc:  # a well-formed JSON line the scorer cannot read: REFUSED for this bot only (never recordable)
            refused[b] = f"{b}: a ledger row is malformed ({type(exc).__name__}: {str(exc)[:80]}): refused; the other bots are still scored"
    existed_books = sum(1 for e in reg.values() if e["clock_start_ms"] < now)  # every book with a clock before the read, stopped ones included
    read_books = sum(len(v.get("books", {})) for v in bots.values())
    n_stakes = 3
    return {"now_ms": now, "arena_books_with_clock_before_read": existed_books, "arena_books_registered": len(reg), "arena_books_stopped": sum(1 for e in reg.values() if e.get("stopped_at_ms")),
            "cells_existed": existed_books * n_stakes * len(MODELS) * len(LATS), "books_read": read_books, "cells_read": read_books * len(MODELS) * len(LATS),
            "bots_refused": refused, "bots": bots}


KINDS = ("open", "refused", "trade", "excluded", "late_print", "start")


def digest_day(ledger_root: str | Path, registry: str, day: str, only: str | None = None) -> dict[str, Any]:
    """Per bot_id: sha256 of the day's ledger file, sha256 of its decision log (the open and refused lines, in file order, byte for byte) and line counts by
    kind. Counts only: no row is parsed, so no fill, exit or P&L is read. The manager posts this to the notebook (DEC-031 section 1.2)."""
    datetime.strptime(day, "%Y-%m-%d")
    out: dict[str, Any] = {"day": day, "bots": {}}
    for b in sorted(load_registry(registry)):
        if only and b != only:
            continue
        p = Path(ledger_root) / b / f"trades-{day}.jsonl"
        if not p.exists():
            out["bots"][b] = {"ledger_sha256": None, "decision_log_sha256": None, "lines": 0, "counts": {}}
            continue
        data = p.read_bytes()
        dec, counts, n = hashlib.sha256(), Counter(), 0
        for line in data.split(b"\n"):
            if not line:
                continue
            n += 1
            for k in KINDS:
                if line.startswith(b'{"kind":"' + k.encode() + b'"'):
                    counts[k] += 1
                    if k in ("open", "refused"):
                        dec.update(line + b"\n")
                    break
        out["bots"][b] = {"ledger_sha256": hashlib.sha256(data).hexdigest(), "decision_log_sha256": dec.hexdigest(), "lines": n, "counts": dict(counts)}
    return out


def render_digest(dg: Mapping[str, Any]) -> str:
    L = [f"ARENA DAILY DIGEST {dg['day']} (sha256 of the day's files and counts only; no P&L)"]
    for b, v in dg["bots"].items():
        L.append(f"{b}  ledger_sha256 {v['ledger_sha256']}  decision_log_sha256 {v['decision_log_sha256']}  lines {v['lines']}  counts {canon(v['counts'])}")
    return "\n".join(L)


def render(res: Mapping[str, Any]) -> str:
    L = [f"ARENA GATE READ (a PREVIEW unless recorded with --record)  now {res['now_ms']}",
         f"multiplicity: {res['arena_books_with_clock_before_read']} books in bots.json with a clock before the read ({res['arena_books_registered']} registered, "
         f"{res['arena_books_stopped']} stopped, stopped ones counted); cells that existed {res['cells_existed']} (books x 3 stakes x 4); "
         f"books read {res['books_read']} (stake books), cells read {res['cells_read']}.",
         "A PASS is a SCREEN: the best of the books above, in-sample for selection (winner's curse), not a promotion. The deciding cell is 1.9 s at the pinned stake."]
    for b, why in res["bots_refused"].items():
        L.append(f"REFUSED {b}: {why}")
    f = lambda z: "None" if z is None else f"{z:+.6f}"  # noqa: E731
    for b, v in res["bots"].items():
        L.append(f"\nBOT {b}  STATUS {v.get('fail_label') or v['status']}  sha {v['frozen_sha']}  clock {v['clock_start_ms']}  deciding stake {v['deciding_stake']}  models {v['model_ids']}")
        if v.get("banner"):
            L.append(f"  *** {v['banner']} *** a forced-boundary comparator screen: it is never the bot's one read and cannot be recorded or quoted as a verdict")
        for w in v.get("warnings", []):
            L.append(f"  WARNING: {w}")
        L.append(f"  config {canon(v['config'])}")
        for r in v["reasons"]:
            L.append(f"  reason: {r}")
        for q in v.get("ledger_problems", [])[:8]:
            L.append(f"  LEDGER PROBLEM: {q}")
        if len(v.get("ledger_problems", [])) > 8:
            L.append(f"  LEDGER PROBLEM: ... and {len(v['ledger_problems']) - 8} more")
        inc = v.get("incidents")
        if inc and inc["n"]:
            L.append(f"  INCIDENTS: {inc['n']} on record {inc['kinds']}  before the boundary {inc['before_boundary']}  lost buys {inc['lost_buys']}  files {inc['files']}")
        if v["status"].endswith("NOT_READY") or "attempts" not in v:
            continue
        L.append(f"  read boundary {v['read_boundary_ms']} ({v['boundary_source']}{'; the bot' + chr(39) + 's own is ' + str(v['natural_boundary_ms']) if v['boundary_source'] == 'forced' else ''})  attempts {v['attempts']}  refused buys {v['refused_buys']}  restart orphans {v['restart_orphans']}  "
                 f"late prints {v['late_prints']}  rows before clock (not counted) {v['rows_before_clock']}")
        L.append(f"  pending {v['pending']} (settle window {v['settle_ms'] // 3_600_000} h)  refused: data issues {v['refused_data_issues']}, other {v['refused_other']}  "
                 "counting: the boundary counts attempts; the gate's n is priced trades after exclusions (per stake below)")
        d = v.get("deciding")
        if d:
            L.append(f"  DECIDING CELL: 1.9 s, stake {d['stake']}, flat + pressure, strand rules total + sc -> {'pass' if d['pass'] else 'fail'}  weakest cell {d['weakest_cell']}")
            for k, m in d["misses"].items():
                L.append(f"    {k}: {'; '.join(m)}")
            if d["pass"] and d["not_proposed_for_canary"]:
                L.append("  NOT PROPOSED FOR CANARY: total is not positive ex best day (or ex-best-day is missing) in a deciding sub-cell")
        w = v.get("worst_in_slot")
        if w is not None:
            if not w["measured"]:
                L.append(f"  WORST-IN-SLOT (report-only): {w['reason']}")
            else:
                L.append(f"  WORST-IN-SLOT (report-only, never decides; 1.9 s, lowest of the 4 sub-cells): mean {f(w['mean_sol'])} ci90lo {f(w['ci90_lo_sol'])} "
                         f"worst cell {w['worst_cell']}  mean > 0 in every sub-cell: {w['mean_positive_all_cells']}")
                for k, c in w["cells"].items():
                    L.append(f"    wis {k:16s} n {c['n']:4d} mean {f(c['mean_sol'])} total {f(c['total_sol'])} ci90lo {f(c['ci90_lo_sol'])} mean gap {f(c['mean_gap_sol'])}")
        for st, bk in v["books"].items():
            L.append(f"  stake {st}{' (deciding)' if st == str(v['deciding_stake']) else ' (report-only)'}  trades {bk['n_trades']}  excluded {bk['excluded']}  "
                     f"refused {bk['refused']}  excluded+refused share {f(bk['excluded_share'])}")
            for k, c in bk.get("cells", {}).items():
                icpt = "" if c["intercept"] is None else f" icpt {c['intercept']:+.4f}"
                fx = c.get("fixed_intercept")
                L.append(f"    {k:16s} n {c['n']:4d} days {c['days']:2d} pos {c['days_pos']:2d} mean {f(c['mean_sol'])} total {f(c['total_sol'])} ci90lo {f(c['ci90_lo_sol'])} "
                         f"ex-top3 {f(c['ex_top3_sol'])} ex-best-day {f(c['ex_best_day_sol'])} derived_last {c['derived_last_n']} ({f(c['derived_last_share'])}){icpt} "
                         f"{'pass' if c['pass'] else 'fail'}")
                if fx:
                    L.append(f"      fixed icpt {fx['intercept']:+.4f} (REPORT-ONLY, never decides): mean p {f(fx['mean_p'])} mean {f(fx['mean_sol'])} total {f(fx['total_sol'])} "
                             f"ci90lo {f(fx['ci90_lo_sol'])} ex-top3 {f(fx['ex_top3_sol'])}")
            first = next(iter(bk.get("cells", {}).values()), None)
            if first:
                L.append("    daily (first cell): " + ", ".join(f"{dd} n={x['n']} {x['sol']:+.4f}" for dd, x in first["daily"].items()))
    return "\n".join(L)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ledger-root", default=LEDGER_ROOT)
    ap.add_argument("--registry", default=REGISTRY_PATH)
    ap.add_argument("--reads-dir", default=READS_DIR)
    ap.add_argument("--bot")
    ap.add_argument("--json")
    ap.add_argument("--now-ms", type=int)
    ap.add_argument("--daily-digest", action="store_true", help="print, per bot, the sha256 of --day's decision log and ledger file plus counts (no P&L)")
    ap.add_argument("--day", help="UTC day YYYY-MM-DD for --daily-digest")
    ap.add_argument("--boundary-ms", type=int, help="force the read boundary (a 00:00Z instant): a comparator screen, never recordable. Later than a bot's own outcome-blind "
                    "instant it needs that bot's own read recorded (not PASS) in --reads-dir; compare two arms at --common-boundary instead")
    ap.add_argument("--common-boundary", nargs=2, metavar=("BOT_A", "BOT_B"), help="print the earlier of the two bots' natural outcome-blind instants (the boundary to compare them at) and exit")
    ap.add_argument("--settle-hours", type=float, default=SETTLE_MS / 3_600_000, help="an unclosed attempt younger than this is PENDING, older a restart orphan")
    ap.add_argument("--worst-in-slot", metavar="GAPS_JSON", help="report-only: JSON {trade_id: END-minus-worst gap in lamports}; adds the worst-in-slot mean and CI90 lower bound of the "
                    "deciding book (QP ruling R7, DEC-032 section 1). Off: every output is unchanged")
    ap.add_argument("--record", action="store_true", help="write the one read of --bot (refused if already recorded or not ready)")
    a = ap.parse_args(argv)
    if a.daily_digest:
        if not a.day:
            print("--daily-digest needs --day YYYY-MM-DD", file=sys.stderr)
            return 2
        dg = digest_day(a.ledger_root, a.registry, a.day, a.bot)
        print(render_digest(dg))
        if a.json:
            with open(a.json, "w") as fh:
                json.dump(dg, fh, indent=1, sort_keys=True, allow_nan=False)
        return 0
    if a.common_boundary:
        try:
            cb = common_boundary(a.common_boundary[0], a.common_boundary[1], a.ledger_root, a.registry)
        except Refused as exc:
            print(f"NOT AVAILABLE: {exc}", file=sys.stderr)
            return 3
        print(f"common boundary {cb} ({datetime.fromtimestamp(cb / 1000, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}): the earlier of the two natural instants; "
              f"score both with --boundary-ms {cb}")
        return 0
    try:
        gaps = load_worst_gaps(a.worst_in_slot) if a.worst_in_slot else None
    except Refused as exc:
        print(f"NOT AVAILABLE: {exc}", file=sys.stderr)
        return 3
    res = score_all(a.ledger_root, a.registry, a.bot, a.now_ms, int(a.settle_hours * 3_600_000), a.boundary_ms, a.reads_dir, gaps)
    print(render(res))
    if a.json:
        with open(a.json, "w") as fh:
            json.dump(res, fh, indent=1, sort_keys=True, allow_nan=False)
    if a.record:
        if not a.bot or a.bot not in res["bots"]:
            print("--record needs a scored --bot", file=sys.stderr)
            return 2
        try:
            print("recorded:", record_read(a.reads_dir, res["bots"][a.bot]))
        except Refused as exc:
            print(f"NOT RECORDED: {exc}", file=sys.stderr)
            return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
