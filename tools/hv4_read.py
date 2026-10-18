"""hv4_read: the pre-declared read tooling of Haiku trader v4 (hunt20 arena). PAPER ONLY. No API call, no key, no network.

Serves /data/mal/hunt-1008/push-1016/hunt20/haiku-v4/DESIGN.md section 8 (gate read, "the LLM adds value", "learning works") and section 9 item 6, under
DEC/DEC-031-forward-paper-arena.md section 2. It RE-USES tools.arena_score (the per-book gate, the outcome-blind instant, the pressure refit, the exclusions)
and gates nothing itself: every cell below is an arena_score cell.

    python -m tools.hv4_read book --bot CON-L     [--ledger-root D] [--registry F] [--out-dir D] [--null-dir D]
    python -m tools.hv4_read learning                                 (D14 only)
    python -m tools.hv4_read table [--json F]                         (assembles the recorded reads; computes nothing new)

READS (each at a pre-declared instant, each written ONCE, a second run is refused, an early run is refused and writes nothing):
  book      one per bot_id, at the DEC-031 outcome-blind instant B (arena_score.blind_read_instant, decision timestamps only: a corrupt or unreadable
            outcome line cannot block or move it). Output: the arena_score read, the gate cells of that arm, the disclosure block, a result.v1 record and
            Console row drafts. For an LLM book (L or F) it also carries V1 and V2.
  learning  one, at T0 + 14 d + 13:00Z (the D14 look, DESIGN section 8), covering entries before T0 + 14 d. T0 is the common clock_start_ms of the 13 arms in bots.json.

THE DECIDING CELL (DEC-031 section 2): 1.9 s at the stake pinned in bots.json (0.25 for all 13 arms; the tool refuses any other), flat 15% AND pressure-fail, each
under BOTH strand rules ("total" and "sc"): four sub-cells, all must pass. "Both legs" in V1, V2 and learning means all four sub-cells, never one. The 1.3 s
leg, the other stakes and the as-sized cell are reported beside it and never decide.

THE 25 CELLS (DESIGN section 8; counted in `gate_cells()` and pinned by a test): flat 0.25 / 0.5 / 1.0 for CON-L/F/X and BIG-L/F/X (18), flat 0.25 for BST-L/F/X (3),
as-sized for CON-L/F and BIG-L/F (4). The as-sized cell is the position's own size: the trade row whose stake_sol equals the row's bot_sol_in, scored as one
book by arena_score (pressure refit on exactly those trades). CON-P and the three R arms are reference arms: scored, never gate cells.

V1  the LLM book's primary ex-top-3 (stake 0.25, 1.9 s) is ABOVE the 95th percentile (numpy linear) of the persona's R arm over a 200-seed null, in each of the four
    sub-cells. The null is 200 replays of the R arm, seeds 1..200, each an arena-ledger file `<null-dir>/<persona>-R/seed-NNNN.jsonl`, each scored by arena_score at
    the book's own instant B. A missing or unusable seed makes V1 NOT_DECIDABLE.
V2  daily (book - X) at 0.25 over the calendar days of the read window (days with no trade count 0), one-sided day-block bootstrap (resample whole UTC days,
    2,000 draws, seed 1), p = (1 + #{mean <= 0}) / (1 + draws) (the repo's exp017 convention) <= 0.05/6, in each of the four sub-cells. X is scored at B.
LEARNING (CON; BIG and BST L - F are supporting only): L - F with p <= 0.025 and L - P with p <= 0.025 (same bootstrap) in each sub-cell, and the mean daily
    L - F over D8..D14 at least the mean over D1..D7. NOT_DECIDABLE is the expected outcome; it is declared when an arm is not decidable under arena_score, an
    arm has fewer than 100 priced trades at 0.25, or the window has fewer than 14 days.

The pre-declared instant of a book is NOT the DESIGN's four looks (T0+5/+7/+10/+14). DEC-031 reads each bot_id once; the disclosure states the 25 x 4 = 100 checks
of the DESIGN beside it. Windows: arena_score is run with its read boundary forced (`forced_boundary`) for X, R and learning arms, so that a paired comparison covers
the same attempts. The patch is local, restored on exit, and a test pins that it reproduces arena_score's own read when the forced instant is the natural one.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
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
HV4_LOOKS = 4
PRIMARY_STAKE = 0.25
PRIMARY_KEY = str(PRIMARY_STAKE)
AS_SIZED = "as_sized"
AS_SIZED_KEY = 0.0  # stake_sol sentinel of the as-sized pseudo book
SUBCELLS = ("flat_1p9_total", "flat_1p9_sc", "press_1p9_total", "press_1p9_sc")
DECIDABLE = ("PASS", "FAIL")  # arena_score statuses whose cells are valid
SLIM_KEYS = ("n", "days", "days_pos", "mean_sol", "total_sol", "ci90_lo_sol", "ex_top3_sol", "ex_best_day_sol", "pass", "misses", "intercept")
FILES_PINNED = ("tools/arena_feed.py", "tools/arena_paper.py", "tools/arena_score.py", "tools/hv4_read.py")


class Refused(Exception):
    pass


# ------------------------------------------------------------------------------------------------------------------------------ the 25 cells
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
@contextlib.contextmanager
def forced_boundary(b_ms: int):
    """Run arena_score with its read boundary forced to b_ms (restored on exit). Everything else in arena_score runs as written."""
    orig = AS.blind_read_instant
    AS.blind_read_instant = lambda entry_ms, clock_start_ms: int(b_ms)  # noqa: E731
    try:
        yield
    finally:
        AS.blind_read_instant = orig


def score_at(bot_id: str, reg: Mapping[str, Mapping[str, Any]], rows: Sequence[Mapping[str, Any]], boundary_ms: int, now_ms: int,
             settle_ms: int = AS.SETTLE_MS) -> dict[str, Any]:
    with forced_boundary(boundary_ms):
        return AS.score_bot(bot_id, reg, rows, now_ms, None, settle_ms)


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


def score_as_sized(bot_id: str, reg: Mapping[str, Mapping[str, Any]], rows: Sequence[Mapping[str, Any]], boundary_ms: int, now_ms: int,
                   settle_ms: int = AS.SETTLE_MS) -> dict[str, Any]:
    kept, unmatched = as_sized_rows(rows)
    reg2 = dict(reg)
    reg2[bot_id] = dict(reg[bot_id], deciding_stake=AS_SIZED_KEY)
    res = score_at(bot_id, reg2, kept, boundary_ms, now_ms, settle_ms)
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
    bk = (res.get("books") or {}).get(key)
    cells = (bk or {}).get("cells") or {}
    rec["subcells"] = {k: _slim(cells[k]) for k in SUBCELLS if k in cells}
    rec["report_1p3"] = {k: _slim(c) for k, c in cells.items() if "_1p3_" in k}
    if res["status"] not in DECIDABLE:
        rec["status"] = res["status"]
        return rec
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
    return res is not None and res["status"] in DECIDABLE and len((res.get("books") or {}).get(PRIMARY_KEY, {}).get("cells") or {}) > 0


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
        if res["status"] not in DECIDABLE or any(cells.get(s, {}).get("ex_top3_sol") is None for s in SUBCELLS):
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


# ------------------------------------------------------------------------------------------------------------------------------ disclosure
def screen_text(existed: int, cells_existed: int, books_read: int, cells_read: int) -> str:
    return (f"This is a SCREEN, not a promotion. It is the best of {existed} arena books in bots.json with a clock before the read (stopped ones included; "
            f"{cells_existed} cells existed); {books_read} stake books and {cells_read} cells were read here. In-sample for selection: winner's curse applies. "
            "A pass allows only quant-proof's check on the exact files, the owner's OK, and a live canary at its DEC's small limits. A stake above the canary's limits needs a "
            "confirmation: the same bot_id, unchanged, on at least 100 attempts decided after this read's instant over at least 5 further UTC dates, with a one-sided bootstrap p "
            "(10,000 draws, seed 1; the larger p of the two fail models) at most 0.05/c, c = the arena books that have entered a confirmation by that read, failed and "
            "NOT_DECIDABLE ones included.")


def disclosure(reg: Mapping[str, Mapping[str, Any]], now_ms: int, books_read: int, cells_read: int, ex_best_day: Mapping[str, Any]) -> dict[str, Any]:
    existed = sum(1 for e in reg.values() if e["clock_start_ms"] < now_ms)  # a clock before the read, stopped ones included
    n_gate = len(gate_cells())
    cells_existed = existed * len(STAKES_SOL) * len(AS.MODELS) * len(AS.LATS)  # arena_score's own formula
    return {"books_in_bots_json": len(reg), "books_with_clock_before_read": existed, "books_stopped": sum(1 for e in reg.values() if e.get("stopped_at_ms")),
            "arena_cells_existed": cells_existed, "hv4_arms": len(ARMS), "hv4_gate_cells": n_gate, "hv4_looks_in_design": HV4_LOOKS, "hv4_checks_disclosed": n_gate * HV4_LOOKS,
            "books_read": books_read, "cells_read": cells_read, "screen_not_promotion": screen_text(existed, cells_existed, books_read, cells_read),
            "ex_best_day_sol": dict(ex_best_day),
            "v2_alpha": f"0.05/{V2_FAMILY}", "learning_alpha": LEARN_ALPHA,
            "note": f"One read per bot_id (DEC-031 section 2) at its outcome-blind instant; the DESIGN's {HV4_LOOKS} looks are disclosed as {n_gate} x {HV4_LOOKS} = {n_gate * HV4_LOOKS} checks."}


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
    deciding = next((c for c in read["cells"] if c["decides"]), None)
    res_cells = read["_deciding_cells"]
    span = int(read["window_days"])
    gf, gp = _gate_leg(res_cells, "flat"), _gate_leg(res_cells, "press")
    st = read["status"]
    rec = {"schema_version": "result.v1", "tool": "tools/hv4_read.py", "git_sha": git_sha, "command": command,
           "config": {"read": read["read"], "bot_id": read.get("bot_id"), "instant_ms": read["instant_ms"], "deciding": "1.9 s, stake 0.25, flat and pressure, strand rules total and sc",
                      "displayed_strand_rule": "total", "status": st, "v1": (read.get("v1") or {}).get("status"), "v2": (read.get("v2") or {}).get("status"),
                      "learning": (read.get("learning") or {}).get("status"), "screen": "a screen, not a promotion"},
           "role": "exploration", "data_blocks": [{"start_hour": _hour(read["clock_start_ms"]), "end_hour_exclusive": _hour(read["instant_ms"]), "host": "arena tape /data/mal/arena (public RPC)",
                                                    "ledger_owner": "hunt20 arena (DEC-031)"}],
           "stage": "failed" if st.startswith("FAIL") else "forward-paper",
           "metrics": {"flat": _leg_metrics(res_cells, "flat_1p9_total", span), "pressure_s1": _leg_metrics(res_cells, "press_1p9_total", span)},
           "gate": {"flat": gf, "pressure_s1": gp, "pass_both": bool(gf["pass"] and gp["pass"])},
           "tries": {"data_key": hashlib.sha256(canon(["hv4", read.get("bot_id"), read["clock_start_ms"]]).encode()).hexdigest()[:16], "variant_n": int(read["tries"]["variant_n"]),
                     "of_m": int(read["tries"]["of_m"])},
           "runtime_s": None, "peak_rss_mb": None, "created_utc": created_utc,
           "notes": read["disclosure"]["screen_not_promotion"] + f" Status {st}." + (" Not proposed for canary: ex-best-day is not positive." if read.get("not_proposed_for_canary") else "")}
    errs = validate_result(rec)
    if errs:
        raise ValueError("result.v1 record invalid:\n" + "\n".join(errs))
    return rec


# ------------------------------------------------------------------------------------------------------------------------------ Console row drafts
def _f(x: Any) -> str:
    return "n/a" if x is None else f"{x:+.6f}"


def console_rows(read: Mapping[str, Any], created_utc: str) -> dict[str, Any]:
    """A JSON fragment {stream, ladder, events} for the manager to merge into data/console.json. Numbers are copied at 6 decimals, never rounded up. No sentence says a book made money."""
    who = read.get("bot_id") or "CON learning"
    day = created_utc[:10].replace("-", "")
    tag = f"hv4-{who.lower().replace(' ', '-')}"
    dec = next((c for c in read["cells"] if c["decides"]), None)
    parts = [f"Haiku trader v4 {read['read']} read, {who}, instant {read['instant_ms']} ({_hour(read['instant_ms'])}Z): {read['status']}."]
    if dec and dec["subcells"]:
        s = dec["subcells"].get("flat_1p9_total") or next(iter(dec["subcells"].values()))
        parts.append(f"Deciding cell (1.9 s, stake 0.25, flat and pressure, both strand rules; flat and total rule shown): n {s['n']}, total {_f(s['total_sol'])} SOL, "
                     f"ex-top-3 {_f(s['ex_top3_sol'])}, ex-best-day {_f(s['ex_best_day_sol'])}; weakest ex-best-day over the four sub-cells {_f(dec['ex_best_day_min_sol'])}.")
    if read.get("not_proposed_for_canary"):
        parts.append("NOT PROPOSED FOR CANARY: the total is not positive ex best day (DEC-031 section 2).")
    for k in ("v1", "v2"):
        if read.get(k):
            parts.append(f"{k.upper()} {read[k]['status']}.")
    if read.get("learning"):
        lr = read["learning"]
        ps = "; ".join(f"{k} L-F p {_f(v['p'])}, L-P p {_f(lr['L_minus_P']['subcells'].get(k, {}).get('p'))}" for k, v in lr["L_minus_F"]["subcells"].items())
        parts.append(f"Learning (CON, D14) {lr['status']}: {lr['statement']}." + (f" {ps}." if ps else ""))
    parts.append(read["disclosure"]["screen_not_promotion"])
    stage = "failed" if read["status"].startswith("FAIL") else "forward-paper"
    return {"stream": [{"id": f"s-{day}-{tag}", "from": "claude", "kind": "status", "atUtc": created_utc, "text": " ".join(parts)}],
            "ladder": [{"id": tag, "title": f"HV4 {who}: {read['read']} read {read['status']}", "stage": stage,
                        "note": f"A screen, not a promotion; best of {read['disclosure']['books_with_clock_before_read']} arena books. No book has cleared the promotion gate."}],
            "events": [{"id": f"e-{day}-{tag}", "kind": "record", "title": f"HV4 {who} {read['read']} read", "detail": parts[0], "atUtc": created_utc}]}


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
    for suffix in (".json", ".result.json", ".console.json"):
        if (out_dir / "reads" / f"{name}{suffix}").exists():
            raise Refused(f"{name}: already recorded at {out_dir / 'reads' / (name + suffix)}: a second run is refused")


def _inputs(registry: str, ledger_root: str) -> dict[str, Any]:
    return {"registry_sha256": _sha(Path(registry)), "ledger_root": str(ledger_root), "files_sha256": {f: _sha(REPO_ROOT / f) for f in FILES_PINNED},
            "note": "the pinned files' sha256 at the read (DEC-031 section 1.4); a difference from the records PR is disclosed, not hidden"}


# ------------------------------------------------------------------------------------------------------------------------------ the book read
def read_book(bot_id: str, *, ledger_root: str = LEDGER_ROOT, registry: str = REGISTRY_PATH, out_dir: str = OUT_DIR, null_dir: str | None = None,
              now_ms: int | None = None, settle_ms: int = AS.SETTLE_MS, n_seeds: int = NULL_SEEDS, command: str = "") -> dict[str, Any]:
    now = _now(now_ms)
    reg = load_registry(registry)
    if bot_id not in ARMS:
        raise Refused(f"{bot_id}: not one of the 13 HV4 arms")
    t0 = window_clock(reg)
    od = Path(out_dir)
    _guard_unwritten(od, bot_id)
    # 1. the pre-declared instant, from decision timestamps only (no fill, exit or P&L is parsed before this passes)
    b = AS.blind_read_instant(AS.load_decisions(ledger_root, bot_id), reg[bot_id]["clock_start_ms"])
    if b is None or now < b:
        raise Refused(f"{bot_id}: not at its pre-declared instant (outcome-blind read boundary {b}, now {now}): refused, nothing written")
    arm = ARMS[bot_id]
    led = Ledger(ledger_root)
    rows = led.read_bot(bot_id)
    main = AS.score_bot(bot_id, reg, rows, now, None, settle_ms)
    if main["status"] in ("PENDING", "NOT_READY") or main.get("pending"):
        raise Refused(f"{bot_id}: {main['status']} ({main.get('pending')} covered attempt(s) inside the settle window): not recordable, nothing written")
    assert main["read_boundary_ms"] == b
    asz = score_as_sized(bot_id, reg, rows, b, now, settle_ms) if arm.promotable and arm.persona != "BST" and arm.role in ("L", "F") else None
    cells = []
    if arm.promotable:
        for a, stake in gate_cells():
            if a == bot_id:
                cells.append(cell_record(a, stake, asz if stake == AS_SIZED else main, decides=(stake == PRIMARY_STAKE)))
    else:
        cells.append(cell_record(bot_id, PRIMARY_STAKE, main, decides=True, gate_cell=False))
    # 2. V1 and V2 (LLM books only)
    v1 = v2 = None
    adds = v12 = None
    if arm.llm and arm.promotable:
        v1 = v1_read(main, arm.persona, reg, null_dir, b, now, n_seeds, settle_ms)
        x = f"{arm.persona}-X"
        xres = score_at(x, reg, led.read_bot(x), b, now, settle_ms)
        days = calendar_days(reg[bot_id]["clock_start_ms"], b)
        v2 = paired(main, xres, days, V2_ALPHA)
        v2["comparator"] = x
        v2["comparator_status_at_this_instant"] = status_of(xres)
        v2["note"] = ("V2 fails and the X book's cells pass at this instant: X goes to the canary first (DESIGN section 8), subject to X's own read" if v2["status"] == "FAIL" and
                      xres["status"] == "PASS" else None)
        v12 = bool(v1["status"] == "PASS" and v2["status"] == "PASS")
        adds = bool(main["status"] == "PASS" and v12)  # "adds value" is claimed only for a book that also clears the arena gate
    # 3. disclosure, record
    n_books = len(main.get("books") or {}) + (1 if asz and asz.get("books") else 0)
    cells_read = n_books * len(AS.MODELS) * len(AS.LATS)
    ebd = {f"{c['arm']}@{c['stake']}": c["ex_best_day_min_sol"] for c in cells}
    order = sorted(reg, key=lambda k: (reg[k]["clock_start_ms"], k))
    read = {"schema": SCHEMA, "read": "book", "bot_id": bot_id, "instant_ms": b, "now_ms": now, "created_utc": _created(now), "clock_start_ms": reg[bot_id]["clock_start_ms"],
            "t0_ms": t0, "window_days": max(1, len(calendar_days(reg[bot_id]["clock_start_ms"], b))), "status": status_of(main), "arena_status": main["status"],
            "cells": cells, "cell_count_in_design": len(gate_cells()), "v1": v1, "v2": v2, "v1_and_v2": v12, "adds_value": adds,
            "not_proposed_for_canary": bool(main["status"] == "PASS" and (main.get("deciding") or {}).get("not_proposed_for_canary")),
            "disclosure": disclosure(reg, now, n_books, cells_read, ebd), "tries": {"variant_n": order.index(bot_id) + 1, "of_m": len(order)},
            "inputs": _inputs(registry, ledger_root), "arena_score_read": main, "arena_score_as_sized": asz}
    dec_books = main.get("books", {}).get(PRIMARY_KEY, {}).get("cells", {})
    read["_deciding_cells"] = dec_books
    rv1 = result_v1(read, command=command or f"python -m tools.hv4_read book --bot {bot_id}", created_utc=read["created_utc"], git_sha=_git_sha())
    con = console_rows(read, read["created_utc"])
    del read["_deciding_cells"]
    _write_once(od / "reads" / f"{bot_id}.json", read)
    _write_once(od / "reads" / f"{bot_id}.result.json", rv1, pretty=True)
    _write_once(od / "reads" / f"{bot_id}.console.json", con, pretty=True)
    return read


# ------------------------------------------------------------------------------------------------------------------------------ the learning read (D14)
def read_learning(*, ledger_root: str = LEDGER_ROOT, registry: str = REGISTRY_PATH, out_dir: str = OUT_DIR, now_ms: int | None = None, settle_ms: int = AS.SETTLE_MS,
                  command: str = "") -> dict[str, Any]:
    now = _now(now_ms)
    reg = load_registry(registry)
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
    res = {a: score_at(a, reg, led.read_bot(a), cut, now, settle_ms) for a in ARMS if ARMS[a].role in ("L", "F", "P")}
    pend = [a for a, r in res.items() if r["status"] == "PENDING"]
    if pend:
        raise Refused(f"learning: {pend} have covered attempts inside the settle window: not recordable, nothing written")
    out: dict[str, Any] = {"persona": "CON", "reasons": []}
    arms = ("CON-L", "CON-F", "CON-P")
    for a in arms:
        r = res[a]
        n = ((r.get("books") or {}).get(PRIMARY_KEY) or {}).get("n_trades", 0)
        if r["status"] not in DECIDABLE:
            out["reasons"].append(f"{a}: arena_score status {r['status']}")
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
    cons = [cell_record("CON-L", PRIMARY_STAKE, res["CON-L"], decides=False)]
    ebd = {"CON-L@0.25": cons[0]["ex_best_day_min_sol"]}
    n_books = sum(len((r.get("books") or {})) for r in res.values())
    read = {"schema": SCHEMA, "read": "learning", "bot_id": None, "instant_ms": cut, "look_ms": look, "now_ms": now, "created_utc": _created(now), "clock_start_ms": t0, "t0_ms": t0,
            "window_days": WINDOW_DAYS, "status": out["status"], "cells": cons, "learning": out, "v1": None, "v2": None,
            "disclosure": disclosure(reg, now, n_books, n_books * len(AS.MODELS) * len(AS.LATS), ebd), "tries": {"variant_n": 1, "of_m": len(reg)},
            "inputs": _inputs(registry, ledger_root), "arena_score_reads": {a: {k: v for k, v in r.items() if k != "books"} for a, r in res.items()}}
    read["_deciding_cells"] = (res["CON-L"].get("books") or {}).get(PRIMARY_KEY, {}).get("cells", {})
    rv1 = result_v1(read, command=command or "python -m tools.hv4_read learning", created_utc=read["created_utc"], git_sha=_git_sha())
    con = console_rows(read, read["created_utc"])
    del read["_deciding_cells"]
    _write_once(od / "reads" / "LEARNING.json", read)
    _write_once(od / "reads" / "LEARNING.result.json", rv1, pretty=True)
    _write_once(od / "reads" / "LEARNING.console.json", con, pretty=True)
    return read


# ------------------------------------------------------------------------------------------------------------------------------ the table (assembles recorded reads only)
def build_table(out_dir: str = OUT_DIR, registry: str = REGISTRY_PATH, now_ms: int | None = None) -> dict[str, Any]:
    now = _now(now_ms)
    reg = load_registry(registry)
    rd = Path(out_dir) / "reads"
    recs: dict[str, Any] = {}
    for a in ARMS:
        p = rd / f"{a}.json"
        if p.exists():
            recs[a] = json.loads(p.read_text())
    rows = []
    for arm, stake in gate_cells():
        c = next((x for x in (recs.get(arm) or {}).get("cells", []) if x["arm"] == arm and x["stake"] == stake), None)
        rows.append({"arm": arm, "stake": stake, "status": c["status"] if c else "NOT_READ_YET", "decides": stake == PRIMARY_STAKE,
                     "ex_best_day_min_sol": c["ex_best_day_min_sol"] if c else None, "instant_ms": recs[arm]["instant_ms"] if arm in recs else None})
    cells_read = sum(1 for r in rows if r["status"] != "NOT_READ_YET")
    ebd = {f"{r['arm']}@{r['stake']}": r["ex_best_day_min_sol"] for r in rows if r["status"] != "NOT_READ_YET"}
    learn = (rd / "LEARNING.json")
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
    ap.add_argument("what", choices=("book", "learning", "table"))
    ap.add_argument("--bot")
    ap.add_argument("--ledger-root", default=LEDGER_ROOT)
    ap.add_argument("--registry", default=REGISTRY_PATH)
    ap.add_argument("--out-dir", default=OUT_DIR)
    ap.add_argument("--null-dir", help="directory with <persona>-R/seed-0001.jsonl .. seed-0200.jsonl (the 200-seed R null replays)")
    ap.add_argument("--now-ms", type=int, help="TEST ONLY (needs MAL_ARENA_TEST=1)")
    ap.add_argument("--settle-hours", type=float, default=AS.SETTLE_MS / 3_600_000)
    ap.add_argument("--json", help="table only: write the table here (never overwrites)")
    a = ap.parse_args(argv)
    cmd = "python -m tools.hv4_read " + " ".join(argv if argv is not None else sys.argv[1:])
    settle = int(a.settle_hours * 3_600_000)
    try:
        if a.what == "table":
            t = build_table(a.out_dir, a.registry, a.now_ms)
            print(render_table(t))
            if a.json:
                _write_once(Path(a.json), t)
            return 0
        if a.what == "book":
            if not a.bot:
                print("book needs --bot", file=sys.stderr)
                return 2
            r = read_book(a.bot, ledger_root=a.ledger_root, registry=a.registry, out_dir=a.out_dir, null_dir=a.null_dir, now_ms=a.now_ms, settle_ms=settle, command=cmd)
        else:
            r = read_learning(ledger_root=a.ledger_root, registry=a.registry, out_dir=a.out_dir, now_ms=a.now_ms, settle_ms=settle, command=cmd)
    except (Refused, AS.Refused) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3
    print(f"recorded {a.what} read {r.get('bot_id') or 'LEARNING'}: {r['status']} (instant {r['instant_ms']})")
    print(r["disclosure"]["screen_not_promotion"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
