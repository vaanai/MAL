"""Arena H5 DECIDING read (hunt20): the gate verdict of the two H5 arena books, h5v1-boostfloor-a1 and h5v2-boostclock-a1. PAPER ONLY. READ-ONLY on the ledger; writes nothing except an
optional --json file. This is the tool quant-proof's PR #753 comment (6276830297, item 8) owed: "the H5 deciding-cell command or tool is not in the repo". It is NOT a profit claim: a PASS is
a screen (DEC-031 section 2), the best of N, in-sample for selection (winner's curse).

    python -m tools.arena_h5_read --ledger-root <arena>/ledger --read-instant MS --i-am-the-read --n-books N --c C [--bot BOT ...] [--cells-total K]
                                  [--prior-read BOT=VERDICT ...] [--other-p BOT=P ...] [--json OUT] [--now-ms MS]

WHY A NEW TOOL. tools/arena_score.py cannot read an H5 ledger (its rows have no `calc` / `by_rule` / `stack`, tools/arena_h5_legs.py doc) and tools/arena_h5_legs.py prints the same cells as
REPORT-ONLY lines with no verdict. tools/arena_h5_score.py prints counts only and, at the read, writes the refit (`refit`); its own docstring says "the gate itself is tools/arena_score.py's". So
the verdict path is: this file, calling the gate's own functions.

WHAT DECIDES (DEC-031 section 2 and Amendments 1 to 3, unchanged here). For each book, the DECIDING CELL: the 1.9 s leg at the pinned 0.30 SOL stake, flat 15% AND pressure-fail, each under BOTH
strand rules ("total", H5's "dec031" strand class, and "sc"): four sub-cells, all must pass, RENT-CHARGED (2,039,280 lamports per filled trip: DEC-031 Amendment 2 item 10 keeps the H5 books
rent-charged). A sub-cell passes iff tools.arena_score.cell_verdict says so: n >= 100 priced trades, >= 5 UTC days with a strict majority positive, CI90 lower bound of mean SOL per trade > 0
(trade-level bootstrap, 1,000 draws, seed 1, 5th percentile: cell_stats -> tools.arena_stack.boot_lo) and total ex top 3 trades > 0. Every statistic is arena_score.cell_stats, every pass
test is arena_score.cell_verdict, every leg is arena_h5_cost through arena_h5_legs.cell_values (this file copies none of them). The 1.3 s leg and the 0.10 / 0.25 stakes are never read here.

THE PRESSURE INTERCEPT is the REFIT AT THE READ: arena_h5_score.refit_cells on the book's counted rows before the read instant (mean p over the cell's filled sends = 0.289), per stake and latency
leg. The ledger's `press_*` values carry the FROZEN exploration intercept (cost.H5_PRESS_INTERCEPT, provisional); this tool never reads their `net_lamports`, it re-prices the pressure leg from
`nofail_lamports`, the state, ssb and nb at the refit intercept. If `refit-<day>.json` (written by `arena_h5_score refit`) exists it must equal the recomputed intercepts (else REFUSED); if it is
absent the output says `refit_file: absent` and the intercepts are the ones recomputed from the rows.

STATUS (the first that applies; the order of arena_score._score_bot):
  REFUSED         (exit 3, nothing opened or nothing recorded) the seal below; a torn / unparseable / inconsistent ledger; a recomputed read instant that differs from the record; bad parameters.
  NOT_DECIDABLE   excluded attempts above 5% of the book's NON-REFUSED attempts (DEC-031 section 1.4 and Amendment 1: "every other exclusion stays under the 5% cap, computed over non-refused
                  attempts"; attempts = counted + excluded, because the seal refuses while any attempt before the instant is open), or a counted trade at the deciding stake without its deciding-leg
                  P&L (DEC-031 section 2 / DEC-014 Amendment 1). FINAL for the bot_id. A NOT_DECIDABLE read prints COUNTS ONLY: no cell figure is computed or printed (tools/arena_h5_legs.py can still
                  print the report-only legs on its own).
  PASS / FAIL     the four sub-cells. "FAIL (n < 100)" when every miss is the count (final, not a loss). A PASS also prints "not proposed for canary" when a sub-cell's total ex best day is not
                  positive (arena_score's rule), and `passes_only_at_rent_0` is printed when a FAIL would have passed at rent 0: that book has FAILED its read (Amendment 2 item 10).
REFUSALS (DEC-031 Amendment 1) are not attempts. They are printed beside every count, by reason, with the record's own attempt-definition sentence, and checked against the record (count and
reasons) before anything is priced.

REPORT-ONLY beside the deciding figures, never deciding: the rent-0 leg of Amendment 2 items 9 and 10 (arena_h5_legs.cell_values(rent0=True), labelled "report-only, added after the clock, never
deciding"), ex-best-day, derived_last counts, the bootstrap p, a parity line against arena_h5_legs.cells_of (same functions: a drift guard, not an independent check; the independent restatement is
in tools/test_arena_h5_read.py), and the sha256 of this file, the modules it imports, read-instant.json and the refit file. The worst-in-slot legs come from tools/arena_h5_legs.py, not from here.

MULTIPLICITY BLOCK (DEC-031 Amendment 3 item 3 and QP R8). N and c are PARAMETERS supplied by the manager (--n-books, --c, and --cells-total, --prior-read BOT=VERDICT, --other-p BOT=P): the
tool counts nothing for them and refuses to run without --n-books and --c. It computes: the plain line 1 - 0.95^N, 0.05/c, p as EXP-028 section 3 item 3 pins it (B = 10,000, a fresh
default_rng(1) per sub-cell and the loop of tools.arena_stack.boot_lo; p_pct = (1 + #{m_b <= 0}) / (1 + B), p_null = (1 + #{m_b - xbar >= xbar}) / (1 + B), sub-cell p = max of the two, book p =
the max over the four sub-cells, so it is never below 1 / 10,001; every per-sub-cell figure is reported unrounded) and the Holm rank of this run's p among the
--other-p values (report-only: neither decides on a screen read). Fields only the manager has (agent, DECLARE sha256 and notebook id, cells declared / killed / surviving, blocks, October hours)
are printed as "manager states" lines. The taint list of item 8 is stated for what the tool knows ((e) siblings forward).

THE SEAL is tools/arena_h5_legs.py's, IMPORTED and never copied (`arena_h5_legs.gate`, `check_path`, `load_book_rows`, `check_ledger_rows`): --i-am-the-read, --read-instant at or after the book's
RECORDED read instant (<ledger>/<bot>/read-instant.json, counts only), --now-ms (default now) not before it, no attempt decided before it still open, the ledger holds every attempt and refusal
the record counts, and no probe / canary / _shakedown / sealed / live-shadow name in the bot_id or any path. All checks run before the first ledger row is opened. One hardening that
arena_h5_legs lacks (QP #753 Q3 (a)): --now-ms is clamped to min(now_ms, wall clock), so the flag can only tighten the seal. There is no default for any path. The trade set is the recorded
instant's, not --read-instant's. Before 2026-11-14T00:00Z no arena H5 record may carry an outcome for a synthetic pool or split an outcome by class (EXP-024 Amendment 9 (d)): this tool reads no
pool class and splits nothing.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from tools import arena_h5_books as B
from tools import arena_h5_cost as cost
from tools import arena_h5_legs as L  # the read-instant seal, the ledger loader and the cell helpers: imported, never copied
from tools import arena_h5_score as h5s
from tools import arena_score as gate_mod
from tools.arena_score import MAX_EXCLUDED_SHARE, MIN_N, Refused, cell_stats, cell_verdict

DECIDING_STAKE = h5s.DECIDING_STAKE  # "0.30", pinned in bots.json before the clock
DECIDING_SFX = "1p9"  # the 1.9 s leg
MODELS = ("flat", "press")
RULES = L.RULES  # ("total", "sc")
SUBCELLS = tuple(f"{m}_{DECIDING_SFX}_{r}" for r in RULES for m in MODELS)
P_DRAWS, P_SEED, ALPHA = 10_000, 1, 0.05
P_NOTE = ("EXP-028 section 3 item 3: sub-cell p = max(p_pct, p_null), p_pct = (1 + #{m_b <= 0}) / (1 + B), p_null = (1 + #{m_b - xbar >= xbar}) / (1 + B), B = 10,000, a fresh default_rng(1) "
          "per sub-cell; book p = the max over the four sub-cells; report-only")
CANARY_NOT_MEASURED = ("worst-in-slot: not measured by this tool (tools/arena_h5_legs.py --shadow-dir; otherwise no canary, R7)",
                       "mint fetch: not run (report-only for H5, required before any canary, Am.3 item 6)")
STATS_KEYS = ("n", "days", "days_pos", "mean_sol", "total_sol", "ci90_lo_sol", "ex_top3_sol", "ex_best_day_sol")
SCREEN_LINE = ("A PASS is a SCREEN: the best of N arena books, in-sample for selection (winner's curse), not a promotion and not a profit claim. It allows only DEC-031 section 2 steps 1 to 3 "
               "(quant-proof's check on the exact files, the owner's OK, a live canary at its DEC's small limits). A stake above the canary's limits needs the confirmation of step 4.")
RENT_LINE = ("Rent (DEC-031 Amendment 2 items 9 and 10; QP R1 (h)): the H5 books decide RENT-CHARGED (2,039,280 lamports per filled trip). The rent-0 leg is "
             f"{L.LABEL_RENT0}. A book that passes only at rent 0 has FAILED its read.")
TAINT_LINE = ("Taints touched (Amendment 3 item 8): (e) siblings forward: h5v1-boostfloor-a1 and h5v2-boostclock-a1 share the trigger and the entry legs and differ in the exit, so the best of them "
              "at the read is in-sample. Not touched by this tool's rule: (a) it decides on all four sub-cells. The manager states any other taint.")
MANAGER_FIELDS = ("agent", "DECLARE sha256 and notebook id", "cells declared / killed / surviving", "blocks", "October hours used")


def wall_ms() -> int:
    return int(time.time() * 1000)


def sha256_file(path: str | os.PathLike) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def provenance() -> dict[str, str]:
    """sha256 of this file and of every module whose code produces a figure here (QP #753 item 6)."""
    mods = {"tools/arena_h5_read.py": __file__, "tools/arena_h5_legs.py": L.__file__, "tools/arena_h5_score.py": h5s.__file__, "tools/arena_h5_cost.py": cost.__file__,
            "tools/arena_score.py": gate_mod.__file__, "tools/arena_report_legs.py": L.RL.__file__}
    return {k: sha256_file(v) for k, v in mods.items()}


# ------------------------------------------------------------------------------------------------------------------------------ statistics
def boot_means(x_sol: Sequence[float], draws: int = P_DRAWS, seed: int = P_SEED) -> np.ndarray:
    """The bootstrap means of tools.arena_stack.boot_lo, `draws` of them: a fresh default_rng(seed), one rng.integers call per draw. The first 1,000 of a longer run are the 1,000 of boot_lo."""
    x = np.asarray(x_sol, float)
    rng = np.random.default_rng(seed)
    return np.array([x[rng.integers(0, len(x), len(x))].mean() for _ in range(draws)])


def boot_p(x_sol: Sequence[float], draws: int = P_DRAWS, seed: int = P_SEED) -> dict[str, Any] | None:
    """The lab's cell p of ONE sub-cell, as EXP-028 section 3 item 3 pins it (x_sol = the per-trade SOL vector cell_stats passes to boot_lo). H0: mean <= 0, one-sided.
    p_pct = (1 + #{m_b <= 0}) / (1 + B); p_null = (1 + #{m_b - xbar >= xbar}) / (1 + B); the sub-cell p is max(p_pct, p_null). Everything is returned unrounded, with the 5th percentile
    of the first 1,000 draws (it must equal the cell's ci90_lo_sol). Report-only on a screen read."""
    x = np.asarray(x_sol, float)
    if len(x) == 0:
        return None
    m = boot_means(x, draws, seed)
    xbar = float(x.mean())
    k_pct, k_null = int((m <= 0).sum()), int((m - xbar >= xbar).sum())
    p_pct, p_null = (1 + k_pct) / (1 + draws), (1 + k_null) / (1 + draws)
    return {"n": int(len(x)), "xbar": xbar, "B": int(draws), "seed": int(seed), "tail_pct": k_pct, "tail_null": k_null, "p_pct": p_pct, "p_null": p_null, "p": max(p_pct, p_null),
            "decides": "p_null" if p_null > p_pct else "p_pct", "p5_first_1000_draws": float(np.percentile(m[:1000], 5)) if draws >= 1000 else None}


def holm_rank(p_by_book: Mapping[str, float]) -> dict[str, dict[str, Any]]:
    """Rank of each p among all arena reads given (ascending, ties by bot_id) and the Holm step-down level alpha / (m - rank + 1) of that rank. REPORT-ONLY: rejection by Holm also needs
    every smaller rank to reject, and nothing here decides."""
    m = len(p_by_book)
    order = sorted(p_by_book, key=lambda b: (p_by_book[b], b))
    return {b: {"p": p_by_book[b], "rank": i + 1, "m": m, "holm_level": ALPHA / (m - i)} for i, b in enumerate(order)}


# ------------------------------------------------------------------------------------------------------------------------------ the ledger
def exclusion_pools(bot_dir: Path, ri: int, refused: set[str]) -> dict[str, dict[str, Any]]:
    """The book's exclusion rows decided before the read instant, by pool, refused pools left out (the scorer's `ledger_attempts_before` counts the same set). Fields: id, pool, reason,
    decision_ms, frozen_sha: decision-time only."""
    out: dict[str, dict[str, Any]] = {}
    for f in sorted(bot_dir.glob("exclusions-????-??-??.jsonl")):
        for line in f.read_text().splitlines():
            if line.strip():
                e = json.loads(line)
                if int(e["decision_ms"]) < ri and e["pool"] not in refused:
                    out.setdefault(e["pool"], e)
    return out


def refit_file_status(bot_dir: Path, ri: int, refit: Mapping[str, Mapping[str, Any]]) -> tuple[str, str | None]:
    """("matched", sha256) when arena_h5_score's refit file for the read day equals the intercepts recomputed from the rows, ("absent", None) when there is none; REFUSED on a difference."""
    f = bot_dir / f"refit-{h5s.utc_day(ri)}.json"
    if not f.is_file():
        return "absent", None
    rec = json.loads(f.read_text())["cells"]
    for k, v in refit.items():
        if k not in rec or abs(rec[k]["intercept"] - v["intercept"]) > 1e-12:
            raise Refused(f"cell {k}: the recorded refit {f.name} differs from the intercept recomputed from the ledger rows")
    return "matched", sha256_file(f)


def counts_and_checks(bot_id: str, ledger_root: str | os.PathLike, doc: Mapping[str, Any]) -> dict[str, Any]:
    """Counts of the book at the recorded read instant, with every reconciliation that can fail BEFORE a figure is priced. Raises Refused on a damaged or inconsistent ledger."""
    bot_dir, ri = Path(ledger_root) / bot_id, int(doc["read_instant_ms"])
    try:
        rows = L.load_book_rows(bot_id, ledger_root, doc)
        L.check_ledger_rows(rows)
        led = h5s.ledger_attempts_before(bot_dir, ri)
        refused = set(led["refusals"])
        excl = exclusion_pools(bot_dir, ri, refused)
    except (ValueError, KeyError, TypeError) as exc:  # a torn line or a row the scorer did not write: the manager rules on it, the tool does not make a damaged ledger final
        raise Refused(f"{bot_id}: the ledger cannot be read ({type(exc).__name__}: {str(exc)[:120]}): REFUSED, not a verdict; a damaged ledger needs the manager's ruling (DEC-014 Amendment 1)") from None
    pools = {r["pool"] for r in rows}
    if pools & set(excl):
        raise Refused(f"{bot_id}: {len(pools & set(excl))} pool(s) are both a trade row and an exclusion row: the ledger is inconsistent")
    if len(pools) + len(excl) != led["attempts"]:
        raise Refused(f"{bot_id}: counted pools {len(pools)} + excluded pools {len(excl)} != the ledger's attempts {led['attempts']}")
    by_reason = dict(sorted(collections.Counter(led["refusals"].values()).items()))
    if by_reason != dict(doc.get("refused_by_reason_before") or {}):
        raise Refused(f"{bot_id}: the ledger's refusals by reason {by_reason} differ from the record's {doc.get('refused_by_reason_before')}")
    counted_dec = [min(int(r["decision_ms"]) for r in rows if r["pool"] == p) for p in sorted(pools)]
    why_ri = B.check_recorded_instant(bot_id, doc, counted_dec, counted_dec + [int(e["decision_ms"]) for e in excl.values()], ri)  # THE instant rule of the book, one function
    if why_ri is not None:
        raise Refused(f"{bot_id}: the read instant recomputed from the ledger's decision timestamps differs from the recorded {ri}" + ("" if why_ri == "differs" else f" ({why_ri})"))
    dec_rows = [r for r in rows if f"{r['stake_sol']:.2f}" == DECIDING_STAKE]
    if len({r["pool"] for r in dec_rows}) != len(dec_rows) or {r["pool"] for r in dec_rows} != pools:
        raise Refused(f"{bot_id}: not every counted pool has exactly one row at the deciding stake {DECIDING_STAKE}")
    unpriced = sum(1 for r in dec_rows if r["legs"][f"flat_{DECIDING_SFX}"].get("net_lamports") is None or r["legs"][f"press_{DECIDING_SFX}"].get("net_lamports") is None)
    n_counted, n_excl = len(pools), len(excl)
    decided = n_counted + n_excl  # == attempts: the seal refuses while any attempt before the instant is open
    return {"rows": rows, "dec_rows": dec_rows, "pools": pools | set(excl), "n_attempts": decided, "n_counted": n_counted, "n_excluded": n_excl, "excluded_share": n_excl / decided if decided else 0.0,
            "excluded_by_reason": dict(sorted(collections.Counter(e["reason"] for e in excl.values()).items())), "n_refused": len(led["refusals"]), "refused_by_reason": by_reason,
            "n_unpriced_deciding_leg": unpriced, "attempt_definition": doc.get("attempt_definition")}


# ------------------------------------------------------------------------------------------------------------------------------ one book
def ended_book(bot_id: str, exit_rule: str, doc: Mapping[str, Any], fname: str, rec_sha: str) -> dict[str, Any]:
    """DEC-031 Amendment 11 item 2: the Q50 book has no read instant by the end-seal. NOT_DECIDABLE, final, COUNTS ONLY from the write-once record; no ledger row is opened."""
    n_att, n_exc = int(doc["attempts_before"]), int(doc["excluded_before"])
    return {"bot_id": bot_id, "exit_rule": exit_rule, "read_instant_ms": None, "deciding_stake": DECIDING_STAKE, "deciding_leg": DECIDING_SFX, "deciding_rent_lamports": int(cost.RENT),
            "rent_line": RENT_LINE, "screen_line": SCREEN_LINE,
            "counts": {"n_attempts": n_att, "n_counted": int(doc["counted_before"]), "n_excluded": n_exc, "excluded_share": n_exc / n_att if n_att else 0.0,
                       "excluded_by_reason": "not in the record (counts only)", "n_refused": int(doc["refused_before"]), "refused_by_reason": dict(doc.get("refused_by_reason_before") or {}),
                       "n_unpriced_deciding_leg": None},
            "attempt_definition": doc.get("attempt_definition"), "excluded_cap": MAX_EXCLUDED_SHARE, "provenance": dict(provenance(), **{fname: rec_sha}),
            "reasons": [f"no read instant by the end-seal {doc['end_seal_ms']} (DEC-031 Amendment 11 item 2): {doc['counted_before']} counted trades on {doc['counted_dates']} dates"],
            "status": "NOT_DECIDABLE", "final": True, "figures": "none: a NOT_DECIDABLE read prints counts only"}


def decide_book(bot_id: str, ledger_root: str | os.PathLike, *, read_instant_ms: int | None, i_am_the_read: bool, now_ms: int | None = None,
                books: Mapping[str, str] | None = None) -> dict[str, Any]:
    """The deciding read of one H5 book. Raises Refused (before any ledger row is opened, for the seal's checks). Returns the verdict document. `books`
    (tools/arena_h5_books.load_books): the registered book list, Q50 included; the seal then keys on each book's own instant file (Q50: DEC-031 Amendment 11's)."""
    now = wall_ms() if now_ms is None else min(int(now_ms), wall_ms())  # the flag can only tighten the seal
    fname = h5s.READ_INSTANT_FILE if books is None else B.instant_file(bot_id)
    try:
        doc = L.gate(bot_id, ledger_root, read_instant_ms, i_am_the_read, now, books)  # THE SEAL, arena_h5_legs' own
    except L.Ended as e:
        return ended_book(bot_id, books[bot_id], e.doc, fname, sha256_file(Path(ledger_root) / bot_id / fname))
    ri = int(doc["read_instant_ms"])
    bot_dir = Path(ledger_root) / bot_id
    c = counts_and_checks(bot_id, ledger_root, doc)
    why: list[str] = []
    out: dict[str, Any] = {"bot_id": bot_id, "exit_rule": (h5s.BOTS if books is None else books)[bot_id], "read_instant_ms": ri, "deciding_stake": DECIDING_STAKE, "deciding_leg": DECIDING_SFX,
                           "deciding_rent_lamports": int(cost.RENT), "rent_line": RENT_LINE, "screen_line": SCREEN_LINE,
                           "counts": {k: c[k] for k in ("n_attempts", "n_counted", "n_excluded", "excluded_share", "excluded_by_reason", "n_refused", "refused_by_reason", "n_unpriced_deciding_leg")},
                           "attempt_definition": c["attempt_definition"], "excluded_cap": MAX_EXCLUDED_SHARE,
                           "provenance": dict(provenance(), **{fname: sha256_file(bot_dir / fname)})}
    if c["excluded_share"] > MAX_EXCLUDED_SHARE:  # 5% of NON-REFUSED attempts (Amendment 1); refusals are not in the denominator
        why.append(f"excluded {c['n_excluded']} of {c['n_attempts']} non-refused attempts = {c['excluded_share']:.2%} is above {MAX_EXCLUDED_SHARE:.0%} (DEC-031 section 1.4, Amendment 1): "
                   f"{c['excluded_by_reason']}")
    if c["n_unpriced_deciding_leg"]:
        why.append(f"{c['n_unpriced_deciding_leg']} counted trade(s) at the deciding stake without a deciding-leg (1.9 s flat / pressure) P&L (DEC-031 section 2, DEC-014 Amendment 1)")
    out["reasons"] = why
    if why:
        out["status"] = "NOT_DECIDABLE"
        out["figures"] = "none: a NOT_DECIDABLE read prints counts only"
        return out
    refit = h5s.refit_cells(c["rows"])  # THE DECIDING INTERCEPT: refit at the read, per stake and latency leg
    key = f"{DECIDING_STAKE}|{DECIDING_SFX}"
    if key not in refit:
        raise Refused(f"{bot_id}: no refit cell {key}")
    ic = refit[key]["intercept"]
    rf, rf_sha = refit_file_status(bot_dir, ri, refit)
    out["refit"] = {"cell": key, "intercept": ic, "n": refit[key]["n"], "n_filled": refit[key]["n_filled"], "frozen_exploration_intercept_not_used": cost.H5_PRESS_INTERCEPT["binding"],
                    "refit_file": rf}
    if rf_sha:
        out["provenance"]["refit file"] = rf_sha
    items = L.items_from_rows(c["dec_rows"], DECIDING_SFX)
    days = [it["day"] for it in items]
    sub: dict[str, Any] = {}
    for rule in RULES:
        v, v0 = L.cell_values(items, rule, ic), L.cell_values(items, rule, ic, rent0=True)
        for model in MODELS:
            s = cell_stats(v[model], days)
            gv = cell_verdict(s)
            s0 = cell_stats(v0[model], days)
            gv0 = cell_verdict(s0)
            pd = boot_p(np.asarray(v[model]) / 1e9)
            if pd["p5_first_1000_draws"] != s["ci90_lo_sol"]:  # the same stream as boot_lo: a drift guard
                raise Refused(f"{bot_id}: the 5th percentile of the first 1,000 bootstrap draws {pd['p5_first_1000_draws']!r} != the cell's ci90_lo_sol {s['ci90_lo_sol']!r}")
            sub[f"{model}_{DECIDING_SFX}_{rule}"] = dict(s, passes=gv["pass"], misses=gv["misses"], intercept=ic if model == "press" else None, p=pd["p"], p_detail=pd,
                                                         rent_0={"report_only": True, "label": L.LABEL_RENT0, "n_credited": int(v0["credited"].sum()), "criteria_met": gv0["pass"],
                                                                 "misses": gv0["misses"], **{k: s0[k] for k in STATS_KEYS}})
    legs = L.cells_of({DECIDING_SFX: items}, {DECIDING_SFX: ic})  # drift guard against arena_h5_legs' as-scored lines (same functions)
    for k, cell in sub.items():
        if any(abs((cell[f] or 0.0) - (legs[k][f] or 0.0)) > 1e-12 for f in STATS_KEYS):
            raise Refused(f"{bot_id}: {k} differs from arena_h5_legs' as-scored cell: the two tools drifted apart")
    out["parity_with_arena_h5_legs"] = "equal on " + ", ".join(STATS_KEYS) + " (same functions: a drift guard, not an independent check)"
    out["subcells"] = sub
    out["derived_last"] = {"n": sum(1 for it in items if it["derived_last"]), "share": (sum(1 for it in items if it["derived_last"]) / len(items)) if items else None}
    ci = {k: v["ci90_lo_sol"] for k, v in sub.items() if v["ci90_lo_sol"] is not None}
    ps = [v["p_detail"]["p"] for v in sub.values()]
    out["weakest_cell"] = min(ci, key=ci.get) if ci else None  # information only
    out["p"] = max(ps) if len(ps) == len(sub) else None
    out["p_note"] = P_NOTE
    ok = len(sub) == 4 and all(v["passes"] for v in sub.values())
    out["status"] = "PASS" if ok else "FAIL"
    misses = [m for v in sub.values() for m in v["misses"]]
    if not ok and misses and all(m.startswith("n ") for m in misses):
        out["fail_label"] = f"FAIL (n < {MIN_N})"  # fewer than 100 priced trades after the exclusions: final, but not a loss
    out["not_proposed_for_canary"] = any(v["ex_best_day_sol"] is None or v["ex_best_day_sol"] <= 0 for v in sub.values())
    if ok:
        out["not_measured_here"] = list(CANARY_NOT_MEASURED)
    out["passes_only_at_rent_0"] = (not ok) and all(v["rent_0"]["criteria_met"] for v in sub.values())
    return out


# ------------------------------------------------------------------------------------------------------------------------------ multiplicity (DEC-031 Amendment 3 item 3, QP R8)
def parse_kv(items: Sequence[str], cast, what: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for it in items:
        k, sep, v = it.partition("=")
        if not sep or not k or not v:
            raise Refused(f"{what}: {it!r} is not BOT=VALUE")
        if k in out:
            raise Refused(f"{what}: {k} given twice")
        try:
            out[k] = cast(v)
        except ValueError:
            raise Refused(f"{what}: {it!r} has a value that does not parse") from None
    return out


def check_params(n_books: int | None, c: int | None, cells_total: int | None, prior: Mapping[str, str], other_p: Mapping[str, float]) -> None:
    if n_books is None or c is None:
        raise Refused("--n-books (N) and --c are required: the manager supplies the multiplicity count; this tool counts nothing for them")
    if n_books < 1 or c < 1 or (cells_total is not None and cells_total < 1):
        raise Refused("--n-books, --c and --cells-total must be positive integers")
    for b, p in other_p.items():
        if not 0.0 <= p <= 1.0:
            raise Refused(f"--other-p {b}={p}: a p-value is in [0, 1]")


def multiplicity_block(results: Mapping[str, Mapping[str, Any]], *, n_books: int, c: int, cells_total: int | None, prior: Mapping[str, str], other_p: Mapping[str, float],
                       taint_line: str = TAINT_LINE) -> dict[str, Any]:
    """The Amendment 3 item 3 block for this run's books, from the manager's parameters and the tool's own figures."""
    dup = sorted(set(prior) & set(results)) + sorted(set(other_p) & set(results))
    if dup:
        raise Refused(f"--prior-read / --other-p name a book that is in this run: {sorted(set(dup))}")
    read = {b: r.get("fail_label") or r["status"] for b, r in results.items()}
    read.update(prior)
    if n_books < len(read):
        raise Refused(f"--n-books {n_books} is smaller than the {len(read)} books read ({sorted(read)}): N counts every book with a clock before the read")
    p_run = {b: r["p"] for b, r in results.items() if r.get("p") is not None}
    holm = holm_rank(dict(other_p, **p_run)) if p_run else {}
    return {"N": n_books, "N_source": "supplied by the manager (--n-books)", "books_read_with_verdicts": dict(sorted(read.items())), "n_books_read": len(read),
            "cells_in_total": cells_total if cells_total is not None else "NOT SUPPLIED (the manager states the total across hunt23, J3 and cohort 2)",
            "cells_per_book": "1 deciding decision = a conjunction of 4 sub-cells (1.9 s x 0.30 SOL x flat/pressure x total/sc); 24 cells priced per book (3 stakes x 2 latencies x 4), the other 20 report-only (R8 item 2)",
            "per_book_manager_states": list(MANAGER_FIELDS), "p_by_book": {b: p_run.get(b) for b in sorted(results)}, "p_note": P_NOTE,
            "plain_line": f"If no book has an edge, the chance of at least one screen pass is up to 1 - 0.95^N = {math.ceil(100 * (1 - 0.95 ** n_books) * 100) / 100:.2f}% (rounded up) at N = {n_books}.",
            "c": c, "c_source": "supplied by the manager (--c); a running count: each confirmation read states its c", "confirmation_threshold_0.05_over_c": ALPHA / c,
            "screen_read_line": {b: holm[b] for b in sorted(holm) if b in results}, "holm_over_reads": sorted(holm), "holm_note": "report-only; Holm also needs every smaller rank to reject; neither decides on a screen read",
            "screen_line": SCREEN_LINE, "rent_line": RENT_LINE, "taint_line": taint_line,
            "refusals_am1": {b: {"n_refused": r["counts"]["n_refused"], "by_reason": r["counts"]["refused_by_reason"], "sentence": r["attempt_definition"]} for b, r in sorted(results.items())},
            "synthetic_class": "no outcome is split by class and no pool class is read (EXP-024 Amendment 9 (d))"}


# ------------------------------------------------------------------------------------------------------------------------------ render
def _f(z: Any) -> str:
    return "None" if z is None else f"{z:+.6f}"


def render_book(r: Mapping[str, Any]) -> str:
    c = r["counts"]
    L_ = [f"\nBOT {r['bot_id']}  STATUS {r.get('fail_label') or r['status']}{' (screen; not a promotion)' if r['status'] == 'PASS' else ''}  exit rule {r['exit_rule']}  read instant {r['read_instant_ms']}  deciding: 1.9 s leg ({r['deciding_leg']}) at {r['deciding_stake']} SOL, RENT-CHARGED",
          f"  attempts {c['n_attempts']} = counted {c['n_counted']} + excluded {c['n_excluded']} ({c['excluded_share']:.2%} of non-refused attempts; cap {r['excluded_cap']:.0%})  excluded by reason {c['excluded_by_reason']}",
          f"  refused (not attempts, DEC-031 Amendment 1) {c['n_refused']}  by reason {c['refused_by_reason']}", f"  {r['attempt_definition']}"]
    L_ += [f"  reason: {x}" for x in r["reasons"]]
    if "subcells" not in r:
        return "\n".join(L_ + [f"  {r['figures']}"])
    rf = r["refit"]
    L_.append(f"  pressure intercept REFIT at the read {rf['intercept']:+.6f} (cell {rf['cell']}, n {rf['n']}, filled {rf['n_filled']}; refit file {rf['refit_file']}); the frozen exploration intercept "
              f"{rf['frozen_exploration_intercept_not_used']:+.6f} is NOT used")
    L_.append(f"  DECIDING CELL (RENT-CHARGED), four sub-cells, all must pass -> {'pass' if r['status'] == 'PASS' else 'fail'}   weakest cell {r['weakest_cell']}")
    for k, v in r["subcells"].items():
        L_.append(f"    {k:16s} n {v['n']:4d} days {v['days']:2d} pos {v['days_pos']:2d} mean {_f(v['mean_sol'])} total {_f(v['total_sol'])} ci90lo {_f(v['ci90_lo_sol'])} "
                  f"ex-top3 {_f(v['ex_top3_sol'])} ex-best-day {_f(v['ex_best_day_sol'])} p {v['p']:.7f} {'pass' if v['passes'] else 'fail'}")
        d = v["p_detail"]
        L_.append(f"      p detail (unrounded; {d['decides']} decides): n {d['n']} xbar {d['xbar']!r} B {d['B']} seed {d['seed']} tail_pct {d['tail_pct']} tail_null {d['tail_null']} "
                  f"p_pct {d['p_pct']!r} p_null {d['p_null']!r} p {d['p']!r}")
        L_ += [f"      miss: {m}" for m in v["misses"]]
        z = v["rent_0"]
        L_.append(f"      rent-0 ({z['label']}): n {z['n']:4d} days {z['days']:2d} pos {z['days_pos']:2d} mean {_f(z['mean_sol'])} total {_f(z['total_sol'])} ci90lo {_f(z['ci90_lo_sol'])} "
                  f"ex-top3 {_f(z['ex_top3_sol'])} ex-best-day {_f(z['ex_best_day_sol'])} n_credited {z['n_credited']}")
    d = r["derived_last"]
    L_.append(f"  derived_last trips {d['n']} (share {_f(d['share'])}); parity with arena_h5_legs: {r['parity_with_arena_h5_legs']}")
    if r["status"] == "PASS":
        L_ += [f"  {x}" for x in r["not_measured_here"]]
    if r["status"] == "PASS" and r["not_proposed_for_canary"]:
        L_.append("  NOT PROPOSED FOR CANARY: total is not positive ex best day (or ex-best-day is missing) in a deciding sub-cell")
    if r["passes_only_at_rent_0"]:
        L_.append("  This book has FAILED its read: it passes only at rent 0 (report-only, never deciding; Amendment 2 item 10)")
    L_.append(f"  daily (first sub-cell): " + ", ".join(f"{dd} n={x['n']} {x['sol']:+.4f}" for dd, x in next(iter(r["subcells"].values()))["daily"].items()))
    return "\n".join(L_)


def render(rep: Mapping[str, Any]) -> str:
    L_ = ["ARENA H5 DECIDING READ  (tools/arena_h5_read.py; PAPER ONLY; a PASS is a screen, not a profit claim)", f"*** {SCREEN_LINE} ***", f"*** {RENT_LINE} ***"]
    for b, why in rep["refused"].items():
        L_.append(f"REFUSED {b}: {why}")
    L_ += [render_book(r) for r in rep["books"].values()]
    m = rep.get("multiplicity")
    if m:
        L_.append("\nMULTIPLICITY BLOCK (DEC-031 Amendment 3 item 3; QP R8)")
        L_.append(f"  N = {m['N']} ({m['N_source']}); books read here and before, with verdicts: {m['books_read_with_verdicts']}")
        L_.append(f"  cells in total: {m['cells_in_total']}")
        L_.append(f"  cells per book: {m['cells_per_book']}")
        L_.append(f"  per book, the manager states: {', '.join(m['per_book_manager_states'])}")
        L_.append(f"  p ({m['p_note']}): {', '.join(f'{b} {p!r}' for b, p in m['p_by_book'].items())}")
        L_.append(f"  {m['plain_line']}")
        L_.append(f"  c = {m['c']} ({m['c_source']}); the confirmation threshold 0.05/c = {m['confirmation_threshold_0.05_over_c']:.6f}")
        for b, h in m["screen_read_line"].items():
            L_.append(f"  screen-read line {b}: p {_f(h['p'])}, Holm rank {h['rank']} of {h['m']} arena reads, level 0.05/(m - rank + 1) = {h['holm_level']:.6f} ({m['holm_note']})")
        for b, a in m["refusals_am1"].items():
            L_.append(f"  Amendment 1 refusals {b}: {a['n_refused']} by reason {a['by_reason']}")
        L_ += [f"  {m['taint_line']}", f"  {m['synthetic_class']}"]
    return "\n".join(L_)


# ------------------------------------------------------------------------------------------------------------------------------ cli
def books_taint_line(books: Sequence[str]) -> str:
    return (f"Taints touched (Amendment 3 item 8): (e) siblings forward: {', '.join(books)} share the trigger family and the entry legs (DEC-031 Amendment 5 item 4.3), so the best of them at "
            "the read is in-sample; a Q50 screen pass while v1-a2 fails is selection on a correlated sibling, not new evidence. Not touched by this tool's rule: (a) it decides on all four "
            "sub-cells. The manager states any other taint.")


def build_report(bots: Sequence[str], ledger_root: str | os.PathLike, *, read_instant_ms: int | None, i_am_the_read: bool, now_ms: int | None, n_books: int | None, c: int | None,
                 cells_total: int | None = None, prior: Mapping[str, str] | None = None, other_p: Mapping[str, float] | None = None,
                 books: Mapping[str, str] | None = None) -> dict[str, Any]:
    prior, other_p = dict(prior or {}), dict(other_p or {})
    check_params(n_books, c, cells_total, prior, other_p)  # before any book is opened
    rep: dict[str, Any] = {"books": {}, "refused": {}}
    kw = {} if books is None else {"books": books}
    for b in bots:
        try:
            rep["books"][b] = decide_book(b, ledger_root, read_instant_ms=read_instant_ms, i_am_the_read=i_am_the_read, now_ms=now_ms, **kw)
        except Refused as exc:
            rep["refused"][b] = str(exc)
    if rep["books"]:
        tl = {} if books is None else {"taint_line": books_taint_line(list(bots))}
        rep["multiplicity"] = multiplicity_block(rep["books"], n_books=int(n_books), c=int(c), cells_total=cells_total, prior=prior, other_p=other_p, **tl)
    if books is not None:
        rep["books_file"] = {"path": getattr(books, "path", None), "sha256": getattr(books, "sha256", None), "ids": list(books)}
    return rep


def main(argv: Sequence[str] | None = None) -> int:
    pre, _ = L.BOOKS_AP.parse_known_args(argv)
    books = None
    if pre.books_file:
        try:
            books = B.load_books(L.check_path(pre.books_file, "books file"), pre.books_sha256)
        except (Refused, OSError) as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 3
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ledger-root", required=True)
    if books is None:
        ap.add_argument("--bot", action="append", choices=sorted(h5s.BOTS), help="default: both H5 books")
    else:
        ap.add_argument("--bot", action="append", choices=sorted(books), help="default: the books file's new_rule_bot_ids")
        L._add_books_args(ap)
    ap.add_argument("--read-instant", type=int, help="ms; must be at or after each book's recorded read instant")
    ap.add_argument("--i-am-the-read", action="store_true", help="only the read job sets this: the H5 outputs are sealed until then")
    ap.add_argument("--now-ms", type=int, help="clamped to the wall clock: it can only tighten the seal")
    ap.add_argument("--n-books", type=int, help="N: every arena book with a clock before the read, stopped and NOT_DECIDABLE included (supplied by the manager; required)")
    ap.add_argument("--c", type=int, help="c: confirmation tests entered by this read (supplied by the manager; required)")
    ap.add_argument("--cells-total", type=int, help="cells in total across hunt23, J3 and cohort 2 (supplied by the manager)")
    ap.add_argument("--prior-read", action="append", default=[], metavar="BOT=VERDICT", help="a book already read, with its verdict")
    ap.add_argument("--other-p", action="append", default=[], metavar="BOT=P", help="the p of another arena read, for the Holm rank")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    try:
        if a.json:
            L.check_path(a.json, "json output")
        kw = {} if books is None else {"books": books}
        rep = build_report(a.bot or (sorted(h5s.BOTS) if books is None else books.new_rule), a.ledger_root, read_instant_ms=a.read_instant, i_am_the_read=a.i_am_the_read, now_ms=a.now_ms,
                           n_books=a.n_books, c=a.c, cells_total=a.cells_total, prior=parse_kv(a.prior_read, str, "--prior-read"), other_p=parse_kv(a.other_p, float, "--other-p"), **kw)
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3
    if not rep["books"]:  # nothing was readable: say why on stderr, print no verdict
        for bot, why in rep["refused"].items():
            print(f"REFUSED {bot}: {why}", file=sys.stderr)
        return 3
    print(render(rep))
    if a.json:
        h5s._atomic_write_json(Path(a.json), rep)
    return 3 if rep["refused"] else 0


if __name__ == "__main__":
    sys.exit(main())
