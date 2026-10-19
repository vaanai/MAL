"""hv4_read: the pre-declared read tooling of Haiku trader v4 (hunt20 arena). PAPER ONLY. No API call, no key, no network.

Serves /data/mal/hunt-1008/push-1016/hunt20/haiku-v4/DESIGN.md section 8 (gate read, "the LLM adds value", "learning works") and section 9 item 6, under
DEC/DEC-031-forward-paper-arena.md section 2. It RE-USES tools.arena_score (the per-book gate, the outcome-blind instant, the pressure refit, the exclusions)
and gates nothing itself: every cell below is an arena_score cell.

    python -m tools.hv4_read book --bot CON-L     [--ledger-root D] [--registry F] [--extra-registry PATH@SHA256 ...] [--out-dir D] [--null-dir D]
    python -m tools.hv4_read learning             [--extra-registry PATH@SHA256 ...]   (D14 only)
    python -m tools.hv4_read table [--json F]     [--extra-registry PATH@SHA256 ...]   (assembles the recorded reads; computes nothing new)

ONE STORE OF RECORDED GATE READS (manager ruling on #718): arena_score's record (`record_read`, `<arena-reads-dir>/<bot_id>.json`, default /data/mal/arena/reads).
`book` records the gate read THERE through `arena_score.record_read` (or, when `arena_score --record` already ran, verifies that the stored read equals the fresh
scoring and uses it), and the learning screen rule reads that store. The out-dir holds only comparison outputs (`<ID>.hv4.json`), `<ID>.result.json` and
`<ID>.console.json`; it never holds a record of a gate read.

READS (each at a pre-declared instant, each written ONCE, a second run is refused, an early run is refused and writes nothing):
  book      one GATE read per bot_id, at the DEC-031 outcome-blind instant B (arena_score.blind_read_instant, decision timestamps only: a corrupt or unreadable
            outcome line cannot block or move it). DEC-031 governs: DESIGN section 8's day-5/7/10/14 looks are superseded for gate purposes. Output: the
            arena_score read, the gate cells of that arm, the disclosure block, a result.v1 record and Console row drafts. For an LLM book (L or F) it also
            carries V1 and V2.
  learning  one, at T0 + 14 d + 13:00Z (D14), covering entries before T0 + 14 d. It is a SEPARATE pre-declared comparison and NOT a gate read: it prints no gate
            verdict, and its result.v1 gate ticks are false by construction. T0 is the common clock_start_ms of the 13 arms in bots.json.

GATE MULTIPLICITY, disclosed in every read: 25 cells x 1 read each = 25 checks (DEC-031 section 2), beside the arena's own book and cell counts. The "best of N arena
books" count is the UNION of every registered arena book, read from the registry files (never a constant): `--registry` (the HV4 arms; arena_paper's JSON-lines format)
and each `--extra-registry PATH@SHA256` (a verified copy of another registry, e.g. mal-fast-0's `$HOME/data/arena/bots.json` with the H5 books h5v1-boostfloor-a1 and
h5v2-boostclock-a1, which is arena_h5_score's {"bots": {...}} format). A read is REFUSED, nothing written, when a book that tools/arena_h5_score registers is not in the
union (the count would be too small). arena_score's own header counts only the registry it is given and hard-codes no number; it is pinned, so it is not changed here.

THE DECIDING CELL (DEC-031 section 2): 1.9 s at the stake pinned in bots.json (0.25 for all 13 arms; the tool refuses any other), flat 15% AND pressure-fail, each
under BOTH strand rules ("total" and "sc"): four sub-cells, all must pass. "Both legs" in V1, V2 and learning means all four sub-cells, never one. The 1.3 s
leg, the other stakes and the as-sized cell are reported beside it and never decide.

THE 25 CELLS (DESIGN section 8; counted in `gate_cells()` and pinned by a test): flat 0.25 / 0.5 / 1.0 for CON-L/F/X and BIG-L/F/X (18), flat 0.25 for BST-L/F/X (3),
as-sized for CON-L/F and BIG-L/F (4). The as-sized cell is the position's own size: the trade row whose stake_sol equals the row's bot_sol_in, scored as one
book by arena_score (pressure refit on exactly those trades). CON-P and the three R arms are reference arms: scored, never gate cells.

V1  the LLM book's primary ex-top-3 (stake 0.25, 1.9 s) is ABOVE the 95th percentile (numpy linear) of the persona's R arm over a 200-seed null, in each of the four
    sub-cells. The null is 200 replays of the R arm, seeds 1..200, each an arena-ledger file `<null-dir>/<persona>-R/seed-NNNN.jsonl`, each scored by arena_score at
    the book's own instant B (a forced-boundary comparator screen). A missing seed, or a seed whose own instant is earlier than B (arena_score refuses a boundary
    later than a bot's own instant unless that bot's own read is recorded non-PASS, and a replay seed has none), makes V1 NOT_DECIDABLE.
V2  daily (book - X) at 0.25 over the calendar days of the window (days with no trade count 0), one-sided day-block bootstrap (resample whole UTC days,
    2,000 draws, seed 1), p = (1 + #{mean <= 0}) / (1 + draws) (the repo's exp017 convention) <= 0.05/6, in each of the four sub-cells. The window ends at the
    `arena_score.common_boundary(book, X)`: the earlier of the two natural instants; both arms are scored there (at or before an arm's own instant is always allowed).
    If X has no instant yet, V2 is NOT_DECIDABLE.
LEARNING (CON; BIG and BST L - F are supporting only): L - F with p <= 0.025 and L - P with p <= 0.025 (same bootstrap) in each sub-cell, and the mean daily
    L - F over D8..D14 at least the mean over D1..D7. NOT_DECIDABLE is the expected outcome; it is declared when an arm is not decidable (orphans, incidents,
    excluded share above 5%, a bonding row, a strand rule missing, no outcome-blind instant yet), an arm has fewer than 100 priced trades at 0.25, or the window has
    fewer than 14 days. Each arm is scored by `arena_score.score_bot(boundary_ms=D14)`, a comparator screen ("FORCED-SCREEN-ONLY"), with its incidents and ledger problems.
  SCREEN RULE (quant-proof's ruling on forced boundaries, #716). D14 is later than an arm's own outcome-blind instant B_own whenever the arm reached 100 attempts over
    5 dates earlier. Attempts after B_own are the arm's CONFIRMATION window if its screen PASSED, so the learning read never peeks at them. arena_score enforces
    this (`check_late_force`); hv4_read checks first, from decision timestamps and arena_score's store (`<arena-reads-dir>/<arm>.json`), so that a passed arm's
    rows are not even loaded:
      * B_own is None or not before D14: the cut is not later than the arm's own instant; the arm is read (no instant at all: arena_score cannot force, the arm is
        NOT_DECIDABLE, which is also what fewer than 100 attempts means).
      * B_own before D14 and the arm's stored read is FAIL (including "FAIL (n < 100)"), NOT_DECIDABLE or REPORT_ONLY: a later boundary is allowed; the arm is read to D14.
      * stored as PASS: the arm is NOT read and the learning read is NOT_DECIDABLE. For a supporting arm (BIG, BST) only that supporting entry is NOT_DECIDABLE.
      * not stored yet: a CON arm makes the learning read REFUSED, nothing written (record the gate reads first; a final NOT_DECIDABLE caused by a missing record
        would be an operations error, not data); a supporting arm is NOT_DECIDABLE for its entry.

BOUNDARY (manager rulings on #718, now on #716's arena_score): no monkeypatch. `score_at` calls `arena_score.score_bot(..., boundary_ms=...)` and `v2_read` uses
`arena_score.common_boundary`. A forced read is a comparator screen and is never the bot's one read (arena_score refuses to record it).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from tools import arena_score as AS
from tools.arena_paper import LEDGER_ROOT, REGISTRY_PATH, STAKES_SOL, TEST_ENV, Ledger, canon, load_registry, utc_day
from tools.hv4_arms import ARMS

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA = "hv4_read_v1"
OUT_DIR = "/data/mal/arena/hv4-reads"
ARENA_READS_DIR = AS.READS_DIR  # THE store of recorded gate reads (arena_score.record_read); hv4_read keeps no record of its own
DAY_MS = 86_400_000
WINDOW_DAYS = 14
LEARN_LOOK_OFFSET_MS = WINDOW_DAYS * DAY_MS + 13 * 3_600_000  # T0 + 14 d + 13:00Z
NULL_SEEDS = 200
NULL_PCT = 95
BOOT_SEED = 1
V2_DRAWS = 2000
V2_FAMILY = 6  # CON-L/F, BIG-L/F, BST-L/F (DESIGN section 8)
V2_ALPHA = 0.05 / V2_FAMILY
LEARN_ALPHA = 0.025
LEARN_MIN_N = AS.MIN_N  # the priced-trade floor for a learning arm (this tool's choice, stated in the PR)
GATE_READS_PER_BOT = 1  # DEC-031 section 2; DESIGN section 8's four looks are superseded for gate purposes
PRIMARY_STAKE = 0.25
PRIMARY_KEY = str(PRIMARY_STAKE)
AS_SIZED = "as_sized"
AS_SIZED_KEY = 0.0  # stake_sol sentinel of the as-sized pseudo book
SUBCELLS = ("flat_1p9_total", "flat_1p9_sc", "press_1p9_total", "press_1p9_sc")
DECIDABLE = ("PASS", "FAIL")  # arena_score statuses (under the FORCED-SCREEN-ONLY prefix too) whose cells are valid
SLIM_KEYS = ("n", "days", "days_pos", "mean_sol", "total_sol", "ci90_lo_sol", "ex_top3_sol", "ex_best_day_sol", "pass", "misses", "intercept")
REFERENCE_LABEL = "reference arm, not promotable"  # CON-P and the three R arms (DESIGN section 5): controls, scored and shown, never candidates
FILES_PINNED = ("tools/arena_feed.py", "tools/arena_paper.py", "tools/arena_score.py", "tools/hv4_read.py")


class Refused(Exception):
    pass


# ------------------------------------------------------------------------------------------------------------------------------ the 25 cells
def is_reference(bot_id: str | None) -> bool:
    """CON-P and the three R arms: reference arms of DESIGN section 5 (ArmSpec.promotable is False). A read of one is labelled so everywhere and has no canary path."""
    return bot_id in ARMS and not ARMS[bot_id].promotable


def gate_cells() -> tuple[tuple[str, Any], ...]:
    """(arm, stake) of the DESIGN's gate cells: stake 0.25 / 0.5 / 1.0 or AS_SIZED. 25 of them, all on promotable arms (L, F, X)."""
    out: list[tuple[str, Any]] = []
    for name, a in ARMS.items():
        if not a.promotable:
            continue
        if a.persona == "BST":
            out.append((name, PRIMARY_STAKE))
            continue
        out += [(name, s) for s in STAKES_SOL]
        if a.role in ("L", "F"):
            out.append((name, AS_SIZED))
    return tuple(out)


def window_clock(reg: Mapping[str, Mapping[str, Any]]) -> int:
    """T0 of the window: the common clock of the 13 arms. Refuses a missing arm, different clocks or a deciding stake other than 0.25."""
    missing = [a for a in ARMS if a not in reg]
    if missing:
        raise Refused(f"arms not registered in bots.json: {missing}")
    clocks = {int(reg[a]["clock_start_ms"]) for a in ARMS}
    if len(clocks) != 1:
        raise Refused(f"the 13 arms do not share one clock_start_ms: {sorted(clocks)}")
    bad = [a for a in ARMS if reg[a].get("deciding_stake") != PRIMARY_STAKE]
    if bad:
        raise Refused(f"deciding_stake must be {PRIMARY_STAKE} for all arms (DESIGN section 8, flat 0.25 is the primary): {bad}")
    return clocks.pop()


# ------------------------------------------------------------------------------------------------------------------------------ scoring through arena_score
def base_status(res: Mapping[str, Any]) -> str:
    """The status without arena_score's "FORCED-SCREEN-ONLY: " prefix (a forced-boundary read is a comparator screen; the logic here needs the status under it)."""
    st = str(res["status"])
    return st[len(AS.FORCED_PREFIX):] if st.startswith(AS.FORCED_PREFIX) else st


def scan(led: Ledger, bot_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """A bot's rows, incident lines and ledger problems, loaded as arena_score.score_all loads them (an incident or a torn line makes the bot NOT_DECIDABLE there)."""
    rows, problems = led.scan_bot(bot_id)
    incidents, inc_problems = led.read_incidents(bot_id)
    return rows, incidents, list(problems) + list(inc_problems)


def score_at(bot_id: str, reg: Mapping[str, Mapping[str, Any]], rows: Sequence[Mapping[str, Any]], boundary_ms: int, now_ms: int, settle_ms: int = AS.SETTLE_MS, *,
             incidents: Sequence[Mapping[str, Any]] = (), problems: Sequence[str] = (), comparator_read: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """arena_score.score_bot with the read boundary given (a comparator SCREEN: the status carries arena_score's "FORCED-SCREEN-ONLY: " prefix). At or before the
    bot's own instant it is always allowed; later only with `comparator_read` = its recorded non-PASS read; arena_score decides and raises arena_score.Refused."""
    return AS.score_bot(bot_id, reg, rows, now_ms, None, settle_ms, int(boundary_ms), incidents=incidents, ledger_problems=problems, comparator_read=comparator_read)


def score_ledger_at(led: Ledger, bot_id: str, reg: Mapping[str, Mapping[str, Any]], boundary_ms: int, now_ms: int, settle_ms: int, reads_dir: str | Path) -> dict[str, Any]:
    """score_at on a bot of the ledger, with its incidents and problems and, for a boundary past its own instant, its recorded read from arena_score's store."""
    try:
        rows, incidents, problems = scan(led, bot_id)
        return score_at(bot_id, reg, rows, boundary_ms, now_ms, settle_ms, incidents=incidents, problems=problems, comparator_read=AS.load_recorded_read(reads_dir, bot_id))
    except (KeyError, TypeError, ValueError, IndexError) as exc:  # a well-formed JSON line the scorer cannot read: refused for this bot, as in arena_score.score_all
        raise Refused(f"{bot_id}: a ledger row is malformed ({type(exc).__name__}: {str(exc)[:80]}): refused") from None


def as_sized_rows(rows: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """The as-sized book: keep, per position, the trade / excluded row whose stake_sol is the row's own bot_sol_in, relabelled to the sentinel stake. Returns
    (rows, positions that have no row at their own size: they make the cell NOT_DECIDABLE)."""
    kept: list[dict[str, Any]] = []
    have: set[str] = set()
    every: set[str] = set()
    for r in rows:
        if r["kind"] in ("trade", "excluded"):
            every.add(r["position_id"])
            want = r.get("bot_sol_in")
            if want is not None and abs(float(r["stake_sol"]) - float(want)) < 1e-9:
                kept.append(dict(r, stake_sol=AS_SIZED_KEY))
                have.add(r["position_id"])
        else:
            kept.append(dict(r))
    return kept, sorted(every - have)


def score_as_sized(bot_id: str, reg: Mapping[str, Mapping[str, Any]], rows: Sequence[Mapping[str, Any]], now_ms: int, settle_ms: int = AS.SETTLE_MS, *,
                   incidents: Sequence[Mapping[str, Any]] = (), problems: Sequence[str] = ()) -> dict[str, Any]:
    """The as-sized pseudo book through arena_score's NATURAL read (its open and refused rows are the book's own, so its outcome-blind instant is the book's)."""
    kept, unmatched = as_sized_rows(rows)
    reg2 = dict(reg)
    reg2[bot_id] = dict(reg[bot_id], deciding_stake=AS_SIZED_KEY)
    res = AS.score_bot(bot_id, reg2, kept, now_ms, None, settle_ms, incidents=incidents, ledger_problems=problems)
    res["as_sized_unmatched"] = unmatched
    if unmatched and res["status"] not in ("NOT_READY", "PENDING"):
        res["status"] = "NOT_DECIDABLE"
        res["reasons"].append(f"{len(unmatched)} position(s) have no priced row at their own bot_sol_in size: the as-sized cell cannot be built")
    return res


def _slim(c: Mapping[str, Any]) -> dict[str, Any]:
    return {k: c.get(k) for k in SLIM_KEYS}


def status_of(res: Mapping[str, Any]) -> str:
    return str(res.get("fail_label") or res["status"])


def cell_record(arm: str, stake: Any, res: Mapping[str, Any] | None, decides: bool, gate_cell: bool = True) -> dict[str, Any]:
    """One cell: the four deciding sub-cells (1.9 s, flat/pressure x total/sc), the 1.3 s sub-cells beside them, ex-best-day."""
    key = str(AS_SIZED_KEY if stake == AS_SIZED else float(stake))
    rec: dict[str, Any] = {"arm": arm, "stake": stake, "decides": bool(decides), "gate_cell": bool(gate_cell), "arena_status": None, "status": "NOT_READ",
                           "subcells": {}, "report_1p3": {}, "ex_best_day_min_sol": None, "ex_best_day_positive": None}
    if res is None:
        return rec
    rec["arena_status"] = status_of(res)
    if base_status(res) not in DECIDABLE:  # NOT_DECIDABLE / REPORT_ONLY / PENDING / NOT_READY: final or unripe, it claims nothing, so no cell number is carried (DEC-031 section 2)
        rec["status"] = base_status(res)
        return rec
    bk = (res.get("books") or {}).get(key)
    cells = (bk or {}).get("cells") or {}
    rec["subcells"] = {k: _slim(cells[k]) for k in SUBCELLS if k in cells}
    rec["report_1p3"] = {k: _slim(c) for k, c in cells.items() if "_1p3_" in k}
    full = len(rec["subcells"]) == len(SUBCELLS)
    rec["status"] = "PASS" if full and all(c["pass"] for c in rec["subcells"].values()) else "FAIL"
    ebd = [c["ex_best_day_sol"] for c in rec["subcells"].values() if c["ex_best_day_sol"] is not None]
    if full and ebd:
        rec["ex_best_day_min_sol"] = min(ebd)
        rec["ex_best_day_positive"] = bool(min(ebd) > 0)
    return rec


# ------------------------------------------------------------------------------------------------------------------------------ statistics on the daily series
def calendar_days(clock_ms: int, end_ms: int) -> list[str]:
    d0 = int(clock_ms) // DAY_MS * DAY_MS
    return [utc_day(d) for d in range(d0, int(end_ms), DAY_MS)]


def daily_vec(res: Mapping[str, Any], stake_key: str, sub: str, days: Sequence[str]) -> np.ndarray | None:
    c = (((res.get("books") or {}).get(stake_key) or {}).get("cells") or {}).get(sub)
    if c is None:
        return None
    d = c["daily"]
    extra = set(d) - set(days)
    if extra:
        raise Refused(f"trades on days outside the read window: {sorted(extra)}")
    return np.array([d[x]["sol"] if x in d else 0.0 for x in days], float)


def day_block_p(diffs: Sequence[float], draws: int = V2_DRAWS, seed: int = BOOT_SEED) -> float | None:
    """One-sided day-block bootstrap p for H0: mean daily difference <= 0. Resamples whole UTC days with replacement; p = (1 + #{draw mean <= 0}) / (1 + draws)."""
    x = np.asarray(diffs, float)
    if len(x) == 0:
        return None
    rng = np.random.default_rng(seed)
    m = x[rng.integers(0, len(x), size=(draws, len(x)))].mean(axis=1)
    return float((1 + int((m <= 0).sum())) / (1 + draws))


def _decidable(res: Mapping[str, Any] | None) -> bool:
    return res is not None and base_status(res) in DECIDABLE and len((res.get("books") or {}).get(PRIMARY_KEY, {}).get("cells") or {}) > 0


def paired(a: Mapping[str, Any] | None, b: Mapping[str, Any] | None, days: Sequence[str], alpha: float, weeks: bool = False) -> dict[str, Any]:
    """a - b daily at stake 0.25 in each of the four sub-cells. status PASS iff every sub-cell has p <= alpha (and, with weeks, week 2 >= week 1)."""
    out: dict[str, Any] = {"alpha": alpha, "draws": V2_DRAWS, "seed": BOOT_SEED, "days": len(days), "subcells": {}, "reasons": []}
    if not (_decidable(a) and _decidable(b)):
        out["status"] = "NOT_DECIDABLE"
        out["reasons"].append("an arm is not decidable under arena_score (status " + ", ".join(str(x["status"]) if x else "missing" for x in (a, b)) + ")")
        return out
    ok = True
    for sub in SUBCELLS:
        va, vb = daily_vec(a, PRIMARY_KEY, sub, days), daily_vec(b, PRIMARY_KEY, sub, days)
        if va is None or vb is None:
            out["status"] = "NOT_DECIDABLE"
            out["reasons"].append(f"sub-cell {sub} missing")
            return out
        d = va - vb
        p = day_block_p(d)
        rec: dict[str, Any] = {"mean_daily_diff_sol": float(d.mean()), "total_diff_sol": float(d.sum()), "p": p, "p_ok": bool(p is not None and p <= alpha)}
        if weeks:
            w1, w2 = float(d[:7].mean()), float(d[7:14].mean())
            rec.update(week1_mean_sol=w1, week2_mean_sol=w2, week2_ge_week1=bool(w2 >= w1), day1_diff_sol=float(d[0]))
        rec["ok"] = bool(rec["p_ok"] and (rec["week2_ge_week1"] if weeks else True))
        ok = ok and rec["ok"]
        out["subcells"][sub] = rec
    out["status"] = "PASS" if ok else "FAIL"
    return out


# ------------------------------------------------------------------------------------------------------------------------------ the R null (V1)
def load_null_rows(null_dir: str | Path, persona: str, seed: int) -> list[dict[str, Any]]:
    p = Path(null_dir) / f"{persona}-R" / f"seed-{seed:04d}.jsonl"
    if not p.exists():
        raise Refused(f"null seed file missing: {p}")
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def r_null(persona: str, reg: Mapping[str, Mapping[str, Any]], null_dir: str | Path | None, boundary_ms: int, now_ms: int, n_seeds: int = NULL_SEEDS,
           settle_ms: int = AS.SETTLE_MS) -> dict[str, Any]:
    """Primary ex-top-3 of the persona's R arm over seeds 1..n_seeds, per sub-cell, with its 95th percentile. Any missing or unusable seed -> NOT_DECIDABLE."""
    rid = f"{persona}-R"
    out: dict[str, Any] = {"arm": rid, "n_seeds": n_seeds, "seeds_used": 0, "pct": NULL_PCT, "p95": {}, "values": {s: [] for s in SUBCELLS}, "unusable": {}, "reasons": []}
    if null_dir is None:
        out["status"] = "NOT_DECIDABLE"
        out["reasons"].append("no --null-dir: the 200-seed R null is not available")
        return out
    for seed in range(1, n_seeds + 1):
        try:
            res = score_at(rid, reg, load_null_rows(null_dir, persona, seed), boundary_ms, now_ms, settle_ms)
        except (Refused, AS.Refused) as exc:
            out["unusable"][str(seed)] = str(exc)[:200]
            continue
        cells = ((res.get("books") or {}).get(PRIMARY_KEY) or {}).get("cells") or {}
        if base_status(res) not in DECIDABLE or any(cells.get(s, {}).get("ex_top3_sol") is None for s in SUBCELLS):
            out["unusable"][str(seed)] = f"status {res['status']}"
            continue
        for s in SUBCELLS:
            out["values"][s].append(float(cells[s]["ex_top3_sol"]))
        out["seeds_used"] += 1
    if out["unusable"]:
        out["status"] = "NOT_DECIDABLE"
        out["reasons"].append(f"{len(out['unusable'])} of {n_seeds} null seeds missing or unusable")
        return out
    out["p95"] = {s: float(np.percentile(out["values"][s], NULL_PCT)) for s in SUBCELLS}
    out["status"] = "OK"
    return out


def v1_read(book_res: Mapping[str, Any], persona: str, reg, null_dir, boundary_ms: int, now_ms: int, n_seeds: int = NULL_SEEDS, settle_ms: int = AS.SETTLE_MS) -> dict[str, Any]:
    out: dict[str, Any] = {"rule": f"primary ex-top-3 above the p{NULL_PCT} of the {persona}-R {n_seeds}-seed null, in each of the four sub-cells", "subcells": {}, "reasons": []}
    if not _decidable(book_res):
        out["status"] = "NOT_DECIDABLE"
        out["reasons"].append(f"the book is not decidable under arena_score (status {book_res['status']})")
        return out
    null = r_null(persona, reg, null_dir, boundary_ms, now_ms, n_seeds, settle_ms)
    out["null"] = {k: v for k, v in null.items() if k != "values"}
    out["null_sha256"] = hashlib.sha256(canon(null["values"]).encode()).hexdigest()
    if null["status"] != "OK":
        out["status"] = "NOT_DECIDABLE"
        out["reasons"] += null["reasons"]
        return out
    cells = book_res["books"][PRIMARY_KEY]["cells"]
    for s in SUBCELLS:
        ex3 = cells[s]["ex_top3_sol"]
        out["subcells"][s] = {"book_ex_top3_sol": ex3, "null_p95_sol": null["p95"][s], "above": bool(ex3 is not None and ex3 > null["p95"][s])}
    out["status"] = "PASS" if all(v["above"] for v in out["subcells"].values()) else "FAIL"
    return out


def v2_read(bot_id: str, persona: str, main: Mapping[str, Any], b: int, reg: Mapping[str, Mapping[str, Any]], led: Ledger, ledger_root: str, registry: str,
            reads_dir: str, now_ms: int, settle_ms: int, bv: int | None = None) -> dict[str, Any]:
    """V2 over the window ending at arena_score.common_boundary(book, X): the earlier of the two natural instants. Both arms are scored there (at or before an arm's
    own instant is always allowed); the book keeps its natural read when its instant is the earlier one. Both instants come from decision timestamps only.
    `bv` = the declared V1/V2 instant: if X has no outcome-blind instant by then, V2 is NOT_DECIDABLE by rule (so the answer does not depend on when the read is run)."""
    x = f"{persona}-X"
    out: dict[str, Any] = {"comparator": x, "book_instant_ms": b, "comparator_instant_ms": None, "window_end_ms": None, "note": None, "alpha": V2_ALPHA, "reasons": [], "subcells": {}}
    try:
        out["comparator_instant_ms"] = AS.natural_instant(ledger_root, registry, x)
        if bv is not None and out["comparator_instant_ms"] is not None and out["comparator_instant_ms"] > bv:
            raise Refused(f"{x} reached its outcome-blind instant {out['comparator_instant_ms']} only after the declared V1/V2 instant {bv}: V2 is NOT_DECIDABLE by rule")
        b2 = AS.common_boundary(bot_id, x, ledger_root, registry)
        out["window_end_ms"] = b2
        bres = main if b2 == b else score_ledger_at(led, bot_id, reg, b2, now_ms, settle_ms, reads_dir)
        xres = score_ledger_at(led, x, reg, b2, now_ms, settle_ms, reads_dir)
    except (AS.Refused, Refused) as exc:
        out["status"] = "NOT_DECIDABLE"
        out["reasons"].append(f"no common window: {str(exc)[:240]}")
        return out
    v2 = paired(bres, xres, calendar_days(reg[bot_id]["clock_start_ms"], b2), V2_ALPHA)
    v2.update(comparator=x, book_instant_ms=b, comparator_instant_ms=out["comparator_instant_ms"], window_end_ms=b2, boundary_source=xres.get("boundary_source"),
              comparator_status_at_window_end=status_of(xres))
    v2["note"] = ("V2 fails and the X book's cells pass at this window end (a comparator screen, not X's own read): X goes to the canary first (DESIGN section 8), "
                  "subject to X's own read" if v2["status"] == "FAIL" and base_status(xres) == "PASS" else None)
    return v2


# ------------------------------------------------------------------------------------------------------------------------------ the arena's registered books (multiplicity)
def load_registry_any(path: str | Path) -> dict[str, dict[str, Any]]:
    """The books of one registry file, normalised to arena_paper's entry shape (bot_id, frozen_sha, clock_start_ms, deciding_stake, stopped_at_ms). Two formats exist
    under the name bots.json: arena_paper's append-only JSON lines (the HV4 arms and every engine book) and arena_h5_score's {"bots": {...}} object (the H5 books)."""
    text = Path(path).read_text()
    try:
        obj = json.loads(text)
    except ValueError:
        obj = None
    if isinstance(obj, dict) and isinstance(obj.get("bots"), dict):
        return {bid: {"bot_id": bid, "frozen_sha": e.get("frozen_sha"), "clock_start_ms": int(e["clock_start_ms"]),
                      "deciding_stake": float(e.get("deciding_stake_sol", e.get("deciding_stake"))), "stopped_at_ms": e.get("stopped_ms")} for bid, e in obj["bots"].items()}
    return load_registry(path)


def other_registered_books() -> tuple[str, ...]:
    """The arena books that live in another registry than the HV4 arms' (the H5 books of tools/arena_h5_score, registered on mal-fast-0): the union must hold them."""
    from tools.arena_h5_score import BOTS as H5_BOTS

    return tuple(sorted(H5_BOTS))


def arena_books(registry: str | Path, extra: Sequence[str] = ()) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """EVERY registered arena book, for the multiplicity count (DEC-031 section 2: "the number of arena books in bots.json with a clock before the read, stopped ones
    included"). The HV4 arms' registry is `registry`; the H5 books are registered in another bots.json on another host, so a count over `registry` alone would say
    "best of 13" where at least 15 books have a clock. `extra` = "PATH@SHA256" copies of the other registries (the records PR gives each path and sha256); the sha256 is
    verified. The result is the UNION by bot_id; a bot_id in two files must have the same sha, clock and stake. Refused when a book that tools/arena_h5_score registers
    is missing from the union (the count would be too small). Returns (books, sources); nothing is hard-coded about how many there are."""
    sources: list[dict[str, Any]] = [{"path": str(registry), "sha256": _sha(Path(registry)), "pinned": False}]
    books: dict[str, dict[str, Any]] = {k: dict(v) for k, v in load_registry_any(registry).items()}
    for spec in extra:
        path, sep, pin = str(spec).rpartition("@")
        if not sep or not path or not re.fullmatch(r"[0-9a-f]{64}", pin):
            raise Refused(f"--extra-registry must be PATH@SHA256 (64 lower-case hex, the sha256 of that copy as given in the records PR): got {spec!r}")
        have = _sha(Path(path))
        if have is None:
            raise Refused(f"--extra-registry {path}: no such file")
        if have != pin:
            raise Refused(f"--extra-registry {path}: its sha256 is {have}, not the pinned {pin}: refused")
        for bid, e in load_registry_any(path).items():
            old = books.get(bid)
            if old is None:
                books[bid] = dict(e)
                continue
            if any(str(old.get(k)) != str(e.get(k)) for k in ("frozen_sha", "clock_start_ms", "deciding_stake")):
                raise Refused(f"{bid} is registered differently in {registry} and {path} (sha, clock or stake): refused")
            if old.get("stopped_at_ms") is None:
                old["stopped_at_ms"] = e.get("stopped_at_ms")
        sources.append({"path": path, "sha256": have, "pinned": True})
    missing = [b for b in other_registered_books() if b not in books]
    if missing:
        raise Refused(f"the arena books {missing} (registered by tools/arena_h5_score on mal-fast-0, $HOME/data/arena/bots.json) are not in the registries given: the multiplicity count "
                      "would be too small. Pass that registry's copy with --extra-registry PATH@SHA256 (ARTIFACTS/lab/arena-records-2026-10-18.md section 5)")
    return books, sources


# ------------------------------------------------------------------------------------------------------------------------------ disclosure
def screen_text(existed: int, cells_existed: int, books_read: int, cells_read: int) -> str:
    return (f"This is a SCREEN, not a promotion. It is the best of {existed} arena books with a clock before the read, counted over every registry listed in the read's inputs (stopped ones included; "
            f"{cells_existed} cells existed); {books_read} stake books and {cells_read} cells were read here. In-sample for selection: winner's curse applies. "
            "A pass allows only quant-proof's check on the exact files, the owner's OK, and a live canary at its DEC's small limits. A stake above the canary's limits needs a "
            "confirmation: the same bot_id, unchanged, on at least 100 attempts decided after this read's instant over at least 5 further UTC dates, with a one-sided bootstrap p "
            "(10,000 draws, seed 1; the larger p of the two fail models) at most 0.05/c, c = the arena books that have entered a confirmation by that read, failed and "
            "NOT_DECIDABLE ones included.")


def reference_text() -> str:
    return (f"{REFERENCE_LABEL.capitalize()} (DESIGN section 5): a control, not a candidate. This read is not one of the 25 gate cells, it is no screen of a book, "
            "it has no canary path and no confirmation, and nothing in it says a book made money. Its cells are shown only so that the LLM arms can be compared with it.")


def disclosure(reg: Mapping[str, Mapping[str, Any]], now_ms: int, books_read: int, cells_read: int, ex_best_day: Mapping[str, Any], reference: bool = False) -> dict[str, Any]:
    """`reg` = EVERY registered arena book (arena_books: the union of the registries), never one registry's file."""
    existed = sum(1 for e in reg.values() if e["clock_start_ms"] < now_ms)  # a clock before the read, stopped ones included
    n_gate = len(gate_cells())
    cells_existed = existed * len(STAKES_SOL) * len(AS.MODELS) * len(AS.LATS)  # arena_score's own formula
    return {"arena_books_registered": len(reg), "books_with_clock_before_read": existed, "books_stopped": sum(1 for e in reg.values() if e.get("stopped_at_ms")),
            "arena_cells_existed": cells_existed, "hv4_arms": len(ARMS), "hv4_gate_cells": n_gate, "hv4_reads_per_bot_id": GATE_READS_PER_BOT, "hv4_gate_checks": n_gate * GATE_READS_PER_BOT,
            "books_read": books_read, "cells_read": cells_read, "reference_arm": bool(reference), "reference_label": REFERENCE_LABEL if reference else None,
            "screen_not_promotion": reference_text() if reference else screen_text(existed, cells_existed, books_read, cells_read),
            "ex_best_day_sol": dict(ex_best_day),
            "v2_alpha": f"0.05/{V2_FAMILY}", "learning_alpha": LEARN_ALPHA,
            "gate_multiplicity": (f"{n_gate} cells x {GATE_READS_PER_BOT} read each = {n_gate * GATE_READS_PER_BOT} gate checks. DEC-031 section 2 governs: one gate read per bot_id at its "
                                  "outcome-blind instant; DESIGN section 8's day-5/7/10/14 looks are superseded for gate purposes. The D14 learning read is a separate pre-declared "
                                  "comparison, not a gate read.")}


# ------------------------------------------------------------------------------------------------------------------------------ result.v1 (hand-built: tools.mal_result imports tools.laya_v0, which the arena may not)
def _validate(x: Any, schema: Mapping[str, Any], root: Mapping[str, Any], path: str, errs: list[str]) -> None:
    if "$ref" in schema:
        schema = root["$defs"][schema["$ref"].split("/")[-1]]
    if "const" in schema and x != schema["const"]:
        errs.append(f"{path}: not {schema['const']!r}")
    if "enum" in schema and x not in schema["enum"]:
        errs.append(f"{path}: {x!r} not in enum")
    t = schema.get("type")
    if t is not None:
        names = t if isinstance(t, list) else [t]
        py = {"string": str, "object": dict, "array": list, "boolean": bool, "null": type(None), "integer": int, "number": (int, float)}
        good = any(isinstance(x, py[n]) and not (n in ("integer", "number") and isinstance(x, bool)) for n in names)
        if not good:
            errs.append(f"{path}: {type(x).__name__} is not {names}")
            return
    if isinstance(x, (int, float)) and not isinstance(x, bool) and "minimum" in schema and x < schema["minimum"]:
        errs.append(f"{path}: below minimum")
    if isinstance(x, dict):
        for k in schema.get("required", []):
            if k not in x:
                errs.append(f"{path}.{k}: missing")
        for k, sub in (schema.get("properties") or {}).items():
            if k in x:
                _validate(x[k], sub, root, f"{path}.{k}", errs)
        ap = schema.get("additionalProperties")
        if isinstance(ap, dict):
            for k, v in x.items():
                if k not in (schema.get("properties") or {}):
                    _validate(v, ap, root, f"{path}.{k}", errs)
    if isinstance(x, list) and "items" in schema:
        for i, v in enumerate(x):
            _validate(v, schema["items"], root, f"{path}[{i}]", errs)


def validate_result(rec: Mapping[str, Any]) -> list[str]:
    """schemas/result.v1.schema.json, checked with the subset of JSON Schema it uses (type, required, enum, const, minimum, items, properties, $ref)."""
    schema = json.loads((REPO_ROOT / "schemas" / "result.v1.schema.json").read_text())
    errs: list[str] = []
    _validate(rec, schema, schema, "$", errs)
    return errs


def _hour(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, timezone.utc).strftime("%Y-%m-%dT%H")


def _leg_metrics(cells: Mapping[str, Mapping[str, Any]], sub: str, span_days: int) -> dict[str, Any]:
    c = cells.get(sub)
    base = {"n_trades": 0, "selected_fraction": None, "trades_per_day": None, "sol_per_day": None, "mean_pct": None, "median_pct": None, "mean_sol": None, "median_sol": None,
            "ci90_lo_sol": None, "total_sol": None, "ex_top3_sol": None, "days": 0, "days_positive": 0, "fill_rate": None, "fill_cond_mean_pct": None, "top5_profit_share": None,
            "by_segment": {}}
    if not c:
        return base
    base.update(n_trades=int(c["n"]), mean_sol=c["mean_sol"], ci90_lo_sol=c["ci90_lo_sol"], total_sol=c["total_sol"], ex_top3_sol=c["ex_top3_sol"], days=int(c["days"]),
                days_positive=int(c["days_pos"]), trades_per_day=(c["n"] / span_days) if span_days else None, sol_per_day=(c["total_sol"] / span_days) if span_days else None)
    return base


def _gate_leg(cells: Mapping[str, Mapping[str, Any]], model: str) -> dict[str, bool]:
    """The gate ticks of one fail model: true only if true under BOTH strand rules."""
    subs = [cells.get(f"{model}_1p9_{r}") for r in AS.RULES]
    if any(s is None for s in subs):
        return {k: False for k in ("n_ge_100", "days_ge_5", "majority_days_positive", "ci90_lo_gt_0", "ex_top3_gt_0", "pass")}
    return {"n_ge_100": all(s["n"] >= AS.MIN_N for s in subs), "days_ge_5": all(s["days"] >= AS.MIN_DAYS for s in subs),
            "majority_days_positive": all(s["days"] > 0 and s["days_pos"] * 2 > s["days"] for s in subs),
            "ci90_lo_gt_0": all(s["ci90_lo_sol"] is not None and s["ci90_lo_sol"] > 0 for s in subs),
            "ex_top3_gt_0": all(s["ex_top3_sol"] is not None and s["ex_top3_sol"] > 0 for s in subs), "pass": all(s["pass"] for s in subs)}


def _git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=10).stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def result_v1(read: Mapping[str, Any], *, command: str, created_utc: str, git_sha: str) -> dict[str, Any]:
    """A result.v1 record of the deciding cell. metrics.flat / pressure_s1 show the strand rule "total"; each gate tick is true only under BOTH strand rules.
    stage is never "passed": a pass here is a screen."""
    ref = bool(read.get("reference_arm"))  # a reference arm carries no gate tick: it is not a candidate (DESIGN section 5)
    gate_read = read["read"] == "book" and not ref
    decidable = bool(read["decidable"])  # PASS or FAIL: the only statuses whose cells are valid (a NOT_DECIDABLE / REPORT_ONLY read is final and claims nothing)
    res_cells = read["_deciding_cells"] if decidable else {}  # no cell number, no tick, for a read that is not decidable
    span = int(read["window_days"])
    none = {k: False for k in ("n_ge_100", "days_ge_5", "majority_days_positive", "ci90_lo_gt_0", "ex_top3_gt_0", "pass")}
    gf, gp = (_gate_leg(res_cells, "flat"), _gate_leg(res_cells, "press")) if gate_read and decidable else (dict(none), dict(none))
    st = read["status"]
    rec = {"schema_version": "result.v1", "tool": "tools/hv4_read.py", "git_sha": git_sha, "command": command,
           "config": {"read": read["read"], "bot_id": read.get("bot_id"), "instant_ms": read["instant_ms"], "deciding": "1.9 s, stake 0.25, flat and pressure, strand rules total and sc",
                      "displayed_strand_rule": "total", "gate_evaluated": gate_read, "reference_arm": ref, "label": REFERENCE_LABEL if ref else None, "status": st, "v1": (read.get("v1") or {}).get("status"), "v2": (read.get("v2") or {}).get("status"),
                      "learning": (read.get("learning") or {}).get("status"), "screen": REFERENCE_LABEL if ref else "a screen, not a promotion"},
           "role": "exploration", "data_blocks": [{"start_hour": _hour(read["clock_start_ms"]), "end_hour_exclusive": _hour(read["instant_ms"]), "host": "arena tape /data/mal/arena (public RPC)",
                                                    "ledger_owner": "hunt20 arena (DEC-031)"}],
           "stage": "failed" if gate_read and st.startswith("FAIL") else "forward-paper",
           "metrics": {"flat": _leg_metrics(res_cells, "flat_1p9_total", span), "pressure_s1": _leg_metrics(res_cells, "press_1p9_total", span)},
           "gate": {"flat": gf, "pressure_s1": gp, "pass_both": bool(gf["pass"] and gp["pass"])},
           "tries": {"data_key": hashlib.sha256(canon(["hv4", read.get("bot_id"), read["clock_start_ms"]]).encode()).hexdigest()[:16], "variant_n": int(read["tries"]["variant_n"]),
                     "of_m": int(read["tries"]["of_m"])},
           "runtime_s": None, "peak_rss_mb": None, "created_utc": created_utc,
           "notes": read["disclosure"]["screen_not_promotion"] + f" Status {st}." + ("" if decidable else " Not decidable: no cell number and no gate tick is reported (the read is final and claims nothing).") + (" The gate ticks are false by construction: " + REFERENCE_LABEL + "." if ref else "" if gate_read else " NOT A GATE READ: the D14 learning comparison; the gate ticks are false by construction, not evaluated.") + (" Not proposed for canary: ex-best-day is not positive." if read.get("not_proposed_for_canary") else "")}
    errs = validate_result(rec)
    if errs:
        raise ValueError("result.v1 record invalid:\n" + "\n".join(errs))
    return rec


# ------------------------------------------------------------------------------------------------------------------------------ Console row drafts
def _f(x: Any) -> str:
    return "n/a" if x is None else f"{x:+.6f}"


SUBLABEL = {"flat_1p9_total": "flat 15% fail, strand rule total", "flat_1p9_sc": "flat 15% fail, strand rule sc",
            "press_1p9_total": "pressure fail (slope scale 1), strand rule total", "press_1p9_sc": "pressure fail (slope scale 1), strand rule sc"}


def console_rows(read: Mapping[str, Any], created_utc: str) -> dict[str, Any]:
    """A JSON fragment {stream, ladder, events} for the manager to merge into data/console.json. Numbers are copied at 6 decimals, never rounded up. No sentence says a book made money."""
    who = read.get("bot_id") or "CON learning"
    day = created_utc[:10].replace("-", "")
    tag = f"hv4-{who.lower().replace(' ', '-')}"
    dec = next((c for c in read["cells"] if c["decides"]), None)
    gate = read["read"] == "book"
    decidable = bool(read["decidable"])
    ref = bool(read.get("reference_arm"))
    kind = f"reference read ({REFERENCE_LABEL}; DESIGN section 5)" if ref else "gate read" if gate else "D14 learning comparison (NOT a gate read)"
    parts = [f"Haiku trader v4 {kind}, {who}, instant {read['instant_ms']} ({_hour(read['instant_ms'])}Z): {read['status']}."]
    if not decidable:
        parts.append(f"Not decidable ({read['status']}): the read is final and claims nothing; no cell number and no gate tick is reported here.")
    elif dec and dec["subcells"]:
        legs = "; ".join(f"{SUBLABEL[k]}: n {c['n']}, total {_f(c['total_sol'])} SOL, ex-top-3 {_f(c['ex_top3_sol'])}, CI90 lo {_f(c['ci90_lo_sol'])}, "
                         + ("clears" if c["pass"] else "misses " + "; ".join(str(m) for m in c["misses"])) for k, c in ((k, dec["subcells"][k]) for k in SUBCELLS if k in dec["subcells"]))
        worst = min((c["total_sol"] for c in dec["subcells"].values() if c["total_sol"] is not None), default=None)
        parts.append(f"Deciding cell (1.9 s, stake 0.25; the 1.3 s leg and the other stakes never decide; it decides only if all four sub-cells clear): {legs}. "
                     f"Smallest total over the four sub-cells {_f(worst)} SOL; weakest ex-best-day {_f(dec['ex_best_day_min_sol'])}.")
    if read.get("not_proposed_for_canary"):
        parts.append("NOT PROPOSED FOR CANARY: the total is not positive ex best day (DEC-031 section 2).")
    if read.get("v1") or read.get("v2"):
        if read["status"] != "PASS":  # V1 and V2 are comparisons that can only add to a book that cleared the gate: a book that did not is not given a V verdict here
            parts.append("V1 and V2 are comparison only for a book that did not clear the gate: no V verdict is reported here (see the hv4 json).")
        elif (read.get("v1") or {}).get("status") == "PENDING":
            parts.append(f"V1 and V2 pending until {_created(read['v12']['instant_ms'])}: they are decided once, at the book's window end plus the "
                         f"{read['v12']['settle_ms'] / 3_600_000:g} h settle time, and recorded in the arena store.")
        else:
            parts.append(" ".join(f"{k.upper()} {read[k]['status']}." for k in ("v1", "v2") if read.get(k))
                         + f" (decided once at {_created(read['v12']['instant_ms'])} and recorded in the arena store; they do not change the gate read).")
    if read.get("learning"):
        lr = read["learning"]
        ps = "; ".join(f"{k} L-F p {_f(v['p'])}, L-P p {_f(lr['L_minus_P']['subcells'].get(k, {}).get('p'))}" for k, v in lr["L_minus_F"]["subcells"].items())
        parts.append(f"Learning (CON, D14) {lr['status']}: {lr['statement']}." + (f" {ps}." if ps else ""))
    if not ref:
        parts.append(read["disclosure"]["gate_multiplicity"])
    parts.append(read["disclosure"]["screen_not_promotion"])
    stage = "failed" if gate and not ref and read["status"].startswith("FAIL") else "forward-paper"
    return {"stream": [{"id": f"s-{day}-{tag}", "from": "claude", "kind": "status", "atUtc": created_utc, "text": " ".join(parts)}],
            "ladder": [{"id": tag, "title": f"HV4 {who}: {'reference read, ' + REFERENCE_LABEL if ref else 'gate read' if gate else 'D14 learning comparison, not a gate read'} {read['status']}", "stage": stage,
                        "note": (f"{REFERENCE_LABEL.capitalize()}; no canary path. No book has cleared the promotion gate." if ref else
                                 f"A screen, not a promotion; best of {read['disclosure']['books_with_clock_before_read']} arena books. No book has cleared the promotion gate.")}],
            "events": [{"id": f"e-{day}-{tag}", "kind": "record", "title": f"HV4 {who} {'reference read' if ref else 'gate read' if gate else 'D14 learning comparison'}", "detail": parts[0], "atUtc": created_utc}]}


# ------------------------------------------------------------------------------------------------------------------------------ V1 / V2: decided once, in the arena store
V12_SCHEMA = "hv4_v12_v1"


def v12_instant(b: int, settle_ms: int) -> int:
    """The ONE declared instant of a book's V1 and V2 (quant-proof B1 on #718): its window end B (the outcome-blind gate instant) plus the declared settle time. It is a
    function of decision timestamps and a constant; the operator's choice of when to run changes nothing."""
    return int(b) + int(settle_ms)


def v12_path(reads_dir: str | Path, bot_id: str) -> Path:
    return Path(reads_dir) / f"{bot_id}.v12.json"


def load_v12(reads_dir: str | Path, bot_id: str) -> dict[str, Any] | None:
    """The bot's recorded V1/V2 decision in the same store as its gate read (keyed by bot_id), or None. An unreadable file counts as a record (it blocks a re-run)."""
    p = v12_path(reads_dir, bot_id)
    if not p.exists():
        return None
    try:
        r = json.loads(p.read_text())
        return r if isinstance(r, dict) else {"status": "UNREADABLE"}
    except (OSError, ValueError):
        return {"status": "UNREADABLE"}


def record_v12(reads_dir: str | Path, rec: Mapping[str, Any]) -> dict[str, Any]:
    p = v12_path(reads_dir, rec["bot_id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(p, "x") as f:
            f.write(canon(rec) + "\n")
    except FileExistsError:
        raise Refused(f"{rec['bot_id']}: V1 and V2 are already decided and recorded at {p}: one decision per bot_id, from any out dir; a second run is refused") from None
    return {"path": str(p), "sha256": _sha(p)}


def pending_v(name: str, bv: int, settle_ms: int) -> dict[str, Any]:
    return {"status": "PENDING", "pending_until_ms": int(bv), "subcells": {},
            "reasons": [f"{name} is decided once, at {_created(bv)} (the book's window end + the {settle_ms / 3_600_000:g} h settle time), and recorded in the arena store; "
                        "run `python -m tools.hv4_read v12 --bot <ID>` at or after it"]}


# ------------------------------------------------------------------------------------------------------------------------------ write-once
def _now(now_ms: int | None) -> int:
    if now_ms is not None:
        if os.environ.get(TEST_ENV) != "1":
            raise Refused("--now-ms is a test-only override (it would move the pre-declared instant): refused")
        return int(now_ms)
    return int(time.time() * 1000)


def _created(now_ms: int) -> str:
    return datetime.fromtimestamp(now_ms / 1000.0, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha(p: Path) -> str | None:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


def _write_once(path: Path, obj: Any, pretty: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "x") as f:
            f.write((json.dumps(obj, sort_keys=True, indent=1, allow_nan=False) if pretty else canon(obj)) + "\n")
    except FileExistsError:
        raise Refused(f"already recorded at {path}: one read per output, a second run is refused") from None


def _guard_unwritten(out_dir: Path, name: str) -> None:
    for suffix in (".hv4.json", ".result.json", ".console.json"):
        if (out_dir / "reads" / f"{name}{suffix}").exists():
            raise Refused(f"{name}: already recorded at {out_dir / 'reads' / (name + suffix)}: a second run is refused")


def _inputs(registry: str, ledger_root: str, reads_dir: str, sources: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    return {"registry_sha256": _sha(Path(registry)), "registries": [dict(x) for x in sources], "ledger_root": str(ledger_root), "arena_reads_dir": str(reads_dir), "files_sha256": {f: _sha(REPO_ROOT / f) for f in FILES_PINNED},
            "note": "the pinned files' sha256 at the read (DEC-031 section 1.4); a difference from the records PR is disclosed, not hidden"}


# ------------------------------------------------------------------------------------------------------------------------------ the book read
GATE_KEYS = ("status", "fail_label", "read_boundary_ms", "attempts", "books")


def record_gate_read(reads_dir: str | Path, res: Mapping[str, Any], create: bool = True) -> dict[str, Any]:
    """Put the gate read in arena_score's store: `arena_score.record_read` (write-once). If `arena_score --record` already stored this bot_id, the stored read must equal
    the fresh scoring on the gate keys (status, label, boundary, attempts, every book and cell), else the read is refused; the stored file is never touched."""
    p = Path(reads_dir) / f"{res['bot_id']}.json"
    source = "recorded"
    if p.exists():
        old = json.loads(p.read_text())
        diff = [k for k in GATE_KEYS if canon(old.get(k)) != canon(res.get(k))]
        if diff:
            raise Refused(f"{res['bot_id']}: the read already stored at {p} differs from the fresh scoring on {diff}: refused (the ledger changed since it was recorded?)")
        source = "existing"
    elif not create:
        raise Refused(f"{res['bot_id']}: its gate read is not in the arena store yet ({p}): run `book` first; the V1/V2 step never records a gate read")
    else:
        AS.record_read(reads_dir, res)
    return {"path": str(p), "sha256": _sha(p), "source": source}


def read_book(bot_id: str, *, ledger_root: str = LEDGER_ROOT, registry: str = REGISTRY_PATH, out_dir: str = OUT_DIR, reads_dir: str = ARENA_READS_DIR,
              null_dir: str | None = None, now_ms: int | None = None, settle_ms: int = AS.SETTLE_MS, n_seeds: int = NULL_SEEDS, command: str = "",
              extra_registries: Sequence[str] = (), no_null: bool = False, v12_only: bool = False) -> dict[str, Any]:
    """The book read. V1 and V2 of an L or F book are decided ONCE, at `v12_instant` (B + settle time), and recorded in the arena store next to the gate read
    (`<reads_dir>/<bot_id>.v12.json`, write-once, any out dir): before that instant this read says "pending until <instant>"; at or after it (here, or later with
    `v12_only`, the `v12` command) they are decided and recorded; a second decision is refused. `null_dir=None` needs `no_null=True` (an explicit opt-out) when it decides."""
    now = _now(now_ms)
    reg = load_registry(registry)
    books, sources = arena_books(registry, extra_registries)  # the multiplicity count: every registered arena book (refused when the other registry is missing)
    if bot_id not in ARMS:
        raise Refused(f"{bot_id}: not one of the 13 HV4 arms")
    arm = ARMS[bot_id]
    llm_book = bool(arm.llm and arm.promotable)
    t0 = window_clock(reg)
    od = Path(out_dir)
    name = f"{bot_id}.v12" if v12_only else bot_id
    _guard_unwritten(od, name)
    if llm_book and load_v12(reads_dir, bot_id) is not None:
        raise Refused(f"{bot_id}: V1 and V2 are already decided and recorded at {v12_path(reads_dir, bot_id)}: one decision per bot_id, from any out dir; a second run is refused")
    if v12_only and not llm_book:
        raise Refused(f"{bot_id}: V1 and V2 exist for the LLM books L and F only")
    # 1. the pre-declared instant, from decision timestamps only (no fill, exit or P&L is parsed before this passes)
    b = AS.blind_read_instant(AS.load_decisions(ledger_root, bot_id), reg[bot_id]["clock_start_ms"])
    if b is None or now < b:
        raise Refused(f"{bot_id}: not at its pre-declared instant (outcome-blind read boundary {b}, now {now}): refused, nothing written")
    bv = v12_instant(b, settle_ms)
    if v12_only and now < bv:
        raise Refused(f"{bot_id}: V1 and V2 are decided once, at {bv} ({_created(bv)}) = the window end + the settle time, not before (now {now}): refused, nothing written")
    decide = llm_book and now >= bv
    if decide and null_dir is None and not no_null:
        raise Refused(f"{bot_id}: V1 and V2 are decided now and would be recorded NOT_DECIDABLE for good without the R null: pass --null-dir (or --no-null to record that "
                      "on purpose); refused, nothing written")
    led = Ledger(ledger_root)
    all_ = AS.score_all(ledger_root, registry, bot_id, now, settle_ms, None, reads_dir)  # exactly what `arena_score --bot X --record` would store
    if bot_id in all_["bots_refused"]:
        raise Refused(all_["bots_refused"][bot_id])
    main = all_["bots"][bot_id]
    rows, incidents, problems = scan(led, bot_id)
    if main["status"] in ("PENDING", "NOT_READY") or main.get("pending"):
        raise Refused(f"{bot_id}: {main['status']} ({main.get('pending')} covered attempt(s) inside the settle window): not recordable, nothing written")
    if main["read_boundary_ms"] != b:
        raise Refused(f"{bot_id}: the scored read boundary {main['read_boundary_ms']} is not the pre-declared instant {b}: refused, nothing written")
    asz = score_as_sized(bot_id, reg, rows, now, settle_ms, incidents=incidents, problems=problems) if arm.promotable and arm.persona != "BST" and arm.role in ("L", "F") else None
    cells = []
    if arm.promotable:
        for a, stake in gate_cells():
            if a == bot_id:
                cells.append(cell_record(a, stake, asz if stake == AS_SIZED else main, decides=(stake == PRIMARY_STAKE)))
    else:
        cells.append(cell_record(bot_id, PRIMARY_STAKE, main, decides=True, gate_cell=False))
    # 2. V1 and V2 (LLM books only): decided once at the declared instant, else pending
    v1 = v2 = None
    adds = v12 = None
    if decide:
        v1 = v1_read(main, arm.persona, reg, null_dir, b, now, n_seeds, settle_ms)
        v2 = v2_read(bot_id, arm.persona, main, b, reg, led, ledger_root, registry, reads_dir, now, settle_ms, bv)
        v12 = bool(v1["status"] == "PASS" and v2["status"] == "PASS")
        adds = bool(main["status"] == "PASS" and v12)  # "adds value" is claimed only for a book that also clears the arena gate
    elif llm_book:
        v1, v2 = pending_v("V1", bv, settle_ms), pending_v("V2", bv, settle_ms)
    # 3. disclosure, record
    n_books = len(main.get("books") or {}) + (1 if asz and asz.get("books") else 0)
    cells_read = n_books * len(AS.MODELS) * len(AS.LATS)
    ebd = {f"{c['arm']}@{c['stake']}": c["ex_best_day_min_sol"] for c in cells}
    order = sorted(books, key=lambda k: (books[k]["clock_start_ms"], k))
    read = {"schema": SCHEMA, "read": "book", "bot_id": bot_id, "instant_ms": b, "now_ms": now, "created_utc": _created(now), "clock_start_ms": reg[bot_id]["clock_start_ms"],
            "t0_ms": t0, "window_days": max(1, len(calendar_days(reg[bot_id]["clock_start_ms"], b))), "status": status_of(main), "arena_status": main["status"],
            "decidable": base_status(main) in DECIDABLE,
            "cells": cells, "cell_count_in_design": len(gate_cells()), "v1": v1, "v2": v2, "v1_and_v2": v12, "adds_value": adds,
            "v12": ({"instant_ms": bv, "settle_ms": settle_ms, "state": "decided" if decide else "pending", "record": None} if llm_book else None),
            "reference_arm": not arm.promotable,
            "not_proposed_for_canary": bool(arm.promotable and main["status"] == "PASS" and (main.get("deciding") or {}).get("not_proposed_for_canary")),
            "disclosure": disclosure(books, now, n_books, cells_read, ebd, reference=not arm.promotable), "tries": {"variant_n": order.index(bot_id) + 1, "of_m": len(order)},
            "inputs": _inputs(registry, ledger_root, reads_dir, sources), "arena_score_read": main, "arena_score_as_sized": asz}
    dec_books = main.get("books", {}).get(PRIMARY_KEY, {}).get("cells", {})
    read["_deciding_cells"] = dec_books
    rv1 = result_v1(read, command=command or f"python -m tools.hv4_read {'v12' if v12_only else 'book'} --bot {bot_id}", created_utc=read["created_utc"], git_sha=_git_sha())
    con = console_rows(read, read["created_utc"])
    del read["_deciding_cells"]
    read["arena_record"] = record_gate_read(reads_dir, main, create=not v12_only)  # the ONE store of gate reads; everything above is computed first, so a refusal leaves nothing behind
    if decide:
        read["v12"]["record"] = record_v12(reads_dir, {"schema": V12_SCHEMA, "bot_id": bot_id, "book_instant_ms": b, "v12_instant_ms": bv, "settle_ms": settle_ms, "decided_at_ms": now,
                                                       "gate_status": status_of(main), "gate_read_sha256": read["arena_record"]["sha256"], "v1": v1, "v2": v2, "v1_and_v2": v12,
                                                       "adds_value": adds, "inputs": read["inputs"]})
    _write_once(od / "reads" / f"{name}.hv4.json", {k: v for k, v in read.items() if k != "arena_score_read"})  # a comparison output, not the record
    _write_once(od / "reads" / f"{name}.result.json", rv1, pretty=True)
    _write_once(od / "reads" / f"{name}.console.json", con, pretty=True)
    return read


# ------------------------------------------------------------------------------------------------------------------------------ the learning read (D14)
def recorded_status(reads_dir: str | Path, arm: str) -> str | None:
    """`status` of the arm's gate read in arena_score's store (None if it has none). The out-dir is never consulted."""
    rec = AS.load_recorded_read(reads_dir, arm)
    return None if rec is None else rec.get("status")


def arm_screen(arm: str, reg: Mapping[str, Mapping[str, Any]], ledger_root: str, reads_dir: str | Path, cut_ms: int) -> dict[str, Any]:
    """The SCREEN RULE for one learning arm (module docstring), from decision timestamps and arena_score's store only. arena_score enforces the same rule when the arm
    is scored at the cut (`check_late_force`); this runs first so that an arm that passed its screen has its rows never loaded."""
    own = AS.blind_read_instant(AS.load_decisions(ledger_root, arm), reg[arm]["clock_start_ms"])
    st = recorded_status(reads_dir, arm)
    out: dict[str, Any] = {"own_instant_ms": own, "cut_ms": cut_ms, "recorded_status": st, "rule": "not_later", "reason": None}
    if own is None or own >= cut_ms:
        return out
    if st is None:
        out["rule"], out["reason"] = "unrecorded", f"its own instant {own} is before D14 and its gate read is not in arena_score's store: a later boundary is refused"
    elif st == "PASS":
        out["rule"], out["reason"] = "passed", f"it PASSED its screen at {own}: its attempts after that are its confirmation window and are not read"
    elif st not in AS.LATE_FORCE_OK:
        out["rule"], out["reason"] = "blocked", f"its stored read has status {st!r}: a later boundary is refused"
    else:
        out["rule"] = "later_allowed"
    return out


def nd_arm(arm: str, reason: str) -> dict[str, Any]:
    return {"bot_id": arm, "status": "NOT_DECIDABLE", "books": {}, "pending": 0, "attempts": None, "restart_orphans": 0, "reasons": [reason]}


def read_learning(*, ledger_root: str = LEDGER_ROOT, registry: str = REGISTRY_PATH, out_dir: str = OUT_DIR, reads_dir: str = ARENA_READS_DIR, now_ms: int | None = None, settle_ms: int = AS.SETTLE_MS,
                  command: str = "", extra_registries: Sequence[str] = ()) -> dict[str, Any]:
    now = _now(now_ms)
    reg = load_registry(registry)
    books, sources = arena_books(registry, extra_registries)
    t0 = window_clock(reg)
    if t0 % DAY_MS:
        raise Refused(f"T0 {t0} is not a 00:00Z boundary: the D1..D14 split needs one")
    od = Path(out_dir)
    _guard_unwritten(od, "LEARNING")
    look, cut = t0 + LEARN_LOOK_OFFSET_MS, t0 + WINDOW_DAYS * DAY_MS
    if now < look:
        raise Refused(f"learning is read at D14 only: not before {look} (T0 + 14 d + 13:00Z), now {now}: refused, nothing written")
    led = Ledger(ledger_root)
    days = calendar_days(t0, cut)
    learn_arms = [a for a in ARMS if ARMS[a].role in ("L", "F", "P")]
    screens = {a: arm_screen(a, reg, ledger_root, reads_dir, cut) for a in learn_arms}
    missing = [a for a, sc in screens.items() if sc["rule"] == "unrecorded" and a.startswith("CON-")]
    if missing:
        raise Refused(f"learning: the gate read of {missing} is not in arena_score's store ({reads_dir}), and each reached its own instant before D14: record it first (SCREEN RULE); refused, nothing written")
    res = {}
    for a in learn_arms:
        sc = screens[a]
        if sc["rule"] in ("passed", "unrecorded", "blocked"):  # never loaded: a passed arm's later attempts are its confirmation window
            res[a] = nd_arm(a, sc["reason"])
            continue
        try:
            res[a] = score_ledger_at(led, a, reg, cut, now, settle_ms, reads_dir)  # forced to D14: a comparator screen, arena_score applies the same rule again
        except (AS.Refused, Refused) as exc:
            res[a] = nd_arm(a, str(exc)[:240])
    pend = [a for a, r in res.items() if base_status(r) == "PENDING"]
    if pend:
        raise Refused(f"learning: {pend} have covered attempts inside the settle window: not recordable, nothing written")
    out: dict[str, Any] = {"persona": "CON", "reasons": [], "arm_screens": screens}
    arms = ("CON-L", "CON-F", "CON-P")
    for a in arms:
        r = res[a]
        n = ((r.get("books") or {}).get(PRIMARY_KEY) or {}).get("n_trades", 0)
        if base_status(r) not in DECIDABLE:
            out["reasons"].append(f"{a}: not decidable ({'; '.join(r['reasons']) or r['status']})")
        elif n < LEARN_MIN_N:
            out["reasons"].append(f"{a}: {n} priced trades at 0.25 < {LEARN_MIN_N}")
    lf = paired(res["CON-L"], res["CON-F"], days, LEARN_ALPHA, weeks=True)
    lp = paired(res["CON-L"], res["CON-P"], days, LEARN_ALPHA)
    out["L_minus_F"], out["L_minus_P"] = lf, lp
    out["supporting_only"] = {p: paired(res[f"{p}-L"], res[f"{p}-F"], days, LEARN_ALPHA, weeks=True) for p in ("BIG", "BST")}
    if len(days) < WINDOW_DAYS:
        out["reasons"].append(f"{len(days)} days in the window, {WINDOW_DAYS} needed")
    if out["reasons"] or "NOT_DECIDABLE" in (lf["status"], lp["status"]):
        out["status"] = "NOT_DECIDABLE"
    else:
        out["status"] = "PASS" if lf["status"] == "PASS" and lp["status"] == "PASS" else "FAIL"
    out["statement"] = ("learning shown" if out["status"] == "PASS" else "learning not shown: the reviewer stays off next window" if out["status"] == "FAIL"
                        else "NOT_DECIDABLE (the expected outcome): nothing is claimed")
    cons = [dict(cell_record("CON-L", PRIMARY_STAKE, res["CON-L"], decides=False), status="NOT_A_GATE_READ")]
    ebd = {"CON-L@0.25": cons[0]["ex_best_day_min_sol"]}
    n_books = sum(len((r.get("books") or {})) for r in res.values())
    read = {"schema": SCHEMA, "read": "learning", "bot_id": None, "instant_ms": cut, "look_ms": look, "now_ms": now, "created_utc": _created(now), "clock_start_ms": t0, "t0_ms": t0,
            "window_days": WINDOW_DAYS, "status": out["status"], "decidable": out["status"] in DECIDABLE, "cells": cons, "learning": out, "v1": None, "v2": None,
            "disclosure": disclosure(books, now, n_books, n_books * len(AS.MODELS) * len(AS.LATS), ebd), "tries": {"variant_n": 1, "of_m": len(books)},
            "inputs": _inputs(registry, ledger_root, reads_dir, sources), "learning_arms": {a: {k: v for k, v in r.items() if k != "books"} for a, r in res.items()}}
    read["_deciding_cells"] = (res["CON-L"].get("books") or {}).get(PRIMARY_KEY, {}).get("cells", {})
    rv1 = result_v1(read, command=command or "python -m tools.hv4_read learning", created_utc=read["created_utc"], git_sha=_git_sha())
    con = console_rows(read, read["created_utc"])
    del read["_deciding_cells"]
    _write_once(od / "reads" / "LEARNING.hv4.json", read)
    _write_once(od / "reads" / "LEARNING.result.json", rv1, pretty=True)
    _write_once(od / "reads" / "LEARNING.console.json", con, pretty=True)
    return read


# ------------------------------------------------------------------------------------------------------------------------------ the table (assembles recorded reads only)
def build_table(out_dir: str = OUT_DIR, registry: str = REGISTRY_PATH, now_ms: int | None = None, reads_dir: str = ARENA_READS_DIR, extra_registries: Sequence[str] = ()) -> dict[str, Any]:
    now = _now(now_ms)
    reg, _ = arena_books(registry, extra_registries)
    rd = Path(out_dir) / "reads"
    recs: dict[str, Any] = {}
    for a in ARMS:
        p = rd / f"{a}.hv4.json"
        if p.exists():
            recs[a] = json.loads(p.read_text())
    store = {a: recorded_status(reads_dir, a) for a in ARMS}  # the gate status of an arm is arena_score's stored one; a disagreement is flagged, not smoothed over
    rows = []
    for arm, stake in gate_cells():
        c = next((x for x in (recs.get(arm) or {}).get("cells", []) if x["arm"] == arm and x["stake"] == stake), None)
        bad = arm in recs and store[arm] != recs[arm]["arena_status"]
        rows.append({"arm": arm, "stake": stake, "status": "STORE_MISMATCH" if bad else (c["status"] if c else "NOT_READ_YET"), "decides": stake == PRIMARY_STAKE,
                     "store_status": store[arm], "ex_best_day_min_sol": c["ex_best_day_min_sol"] if c else None, "instant_ms": recs[arm]["instant_ms"] if arm in recs else None})
    cells_read = sum(1 for r in rows if r["status"] != "NOT_READ_YET")
    ebd = {f"{r['arm']}@{r['stake']}": r["ex_best_day_min_sol"] for r in rows if r["status"] != "NOT_READ_YET"}
    learn = (rd / "LEARNING.hv4.json")
    return {"cells": rows, "cell_count": len(rows), "arms_read": sorted(recs), "arms_not_read": [a for a in ARMS if a not in recs],
            "learning": json.loads(learn.read_text())["learning"]["status"] if learn.exists() else "NOT_READ_YET",
            "disclosure": disclosure(reg, now, len(recs), cells_read, ebd)}


def render_table(t: Mapping[str, Any]) -> str:
    L = [f"HV4 READ TABLE: {t['cell_count']} gate cells, {t['disclosure']['books_read']} arms read, learning {t['learning']}", t["disclosure"]["screen_not_promotion"]]
    for r in t["cells"]:
        L.append(f"  {r['arm']:7s} stake {str(r['stake']):8s} {r['status']:14s} {'DECIDES' if r['decides'] else 'report-only'}  ex-best-day {_f(r['ex_best_day_min_sol'])}")
    return "\n".join(L)


# ------------------------------------------------------------------------------------------------------------------------------ CLI
def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("what", choices=("book", "v12", "learning", "table"))
    ap.add_argument("--bot")
    ap.add_argument("--ledger-root", default=LEDGER_ROOT)
    ap.add_argument("--registry", default=REGISTRY_PATH)
    ap.add_argument("--out-dir", default=OUT_DIR, help="comparison outputs, result.v1 and Console fragments only (no gate record)")
    ap.add_argument("--arena-reads-dir", default=ARENA_READS_DIR, help="arena_score's store of recorded gate reads (the only one)")
    ap.add_argument("--extra-registry", action="append", default=[], metavar="PATH@SHA256",
                    help="another arena registry (the H5 books' bots.json from mal-fast-0), for the multiplicity count; repeatable; the sha256 is verified")
    ap.add_argument("--null-dir", help="directory with <persona>-R/seed-0001.jsonl .. seed-0200.jsonl (the 200-seed R null replays)")
    ap.add_argument("--no-null", action="store_true", help="record V1 as NOT_DECIDABLE on purpose (no R null); otherwise --null-dir is required when V1/V2 are decided")
    ap.add_argument("--now-ms", type=int, help="TEST ONLY (needs MAL_ARENA_TEST=1)")
    ap.add_argument("--settle-hours", type=float, default=AS.SETTLE_MS / 3_600_000)
    ap.add_argument("--json", help="table only: write the table here (never overwrites)")
    a = ap.parse_args(argv)
    cmd = "python -m tools.hv4_read " + " ".join(argv if argv is not None else sys.argv[1:])
    settle = int(a.settle_hours * 3_600_000)
    try:
        if a.what == "table":
            t = build_table(a.out_dir, a.registry, a.now_ms, a.arena_reads_dir, a.extra_registry)
            print(render_table(t))
            if a.json:
                _write_once(Path(a.json), t)
            return 0
        if a.what in ("book", "v12"):
            if not a.bot:
                print(f"{a.what} needs --bot", file=sys.stderr)
                return 2
            r = read_book(a.bot, no_null=a.no_null, v12_only=(a.what == "v12"), ledger_root=a.ledger_root, registry=a.registry, out_dir=a.out_dir, reads_dir=a.arena_reads_dir, null_dir=a.null_dir, now_ms=a.now_ms, settle_ms=settle, command=cmd, extra_registries=a.extra_registry)
        else:
            r = read_learning(ledger_root=a.ledger_root, registry=a.registry, out_dir=a.out_dir, reads_dir=a.arena_reads_dir, now_ms=a.now_ms, settle_ms=settle, command=cmd, extra_registries=a.extra_registry)
    except (Refused, AS.Refused) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3
    print(f"{a.what} read {r.get('bot_id') or 'LEARNING'}: {r['status']} (instant {r['instant_ms']})" + (f"; gate read {r['arena_record']['source']} at {r['arena_record']['path']}" if r.get("arena_record") else ""))
    print(r["disclosure"]["screen_not_promotion"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
