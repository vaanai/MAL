#!/usr/bin/env python3
"""EXP-021 screen (exploration): rug signals as MODEL INPUTS to the EXP-012 selector, paired against a retrained control.

**EXPLORATION ONLY. NOT A PRE-REGISTRATION, NOT A PROMOTE, NOT GATE EVIDENCE.** Plan: EXP/EXP-021-rug-signals-in-selector-plan.md (#437), whose
section 4 fixes the design and the bars. This module only composes existing code: the tape pass, cell simulator, guards, features and strict rug
label are `tools/exp016_screen.py` and `tools/exp016_rug.py`; the learner, the purge, the bootstrap and the book statistics are `tools/exp015_screen.py`.

Arms (the ONLY difference is the feature set)
  CONTROL  EXP-012's 18 frozen features, retrained per fold (EXP-015's C2 learner: `e15.fit_cfg`, label = 1{pressure net > 0}, MISS = 0).
  RUG      the same 18 plus EXP-016 section 3 features a1-a5, b1-b5, c2, d1-d4 and e1 (16 more; c1 and e2 are not added: EXP-012 already has
           sniper_buy_share and n_buyers). f1 (creator funding) is phase 2 and is not here.
Folds    leave-one-UTC-date-out over every pool date, with EXP-015's 35-minute purge. The 27 non-P1 dates decide; the P1 dates are report-only.
Count    on each held-out date both arms keep exactly the number of rows the frozen EXP-012 selection kept on that date (the frozen model's
         out-of-fold rate, not a tuned threshold): the top-n_d scores, ties broken by mint id. No inner loop exists, so nothing is chosen per fold.
Bars     (both legs, flat and pressure; plan section 4) 1 paired mean > 0 with a one-sided date-cluster bootstrap p < 0.025 (DEC-021 family alpha);
         3 a majority of the non-P1 dates with a positive paired gain; 4 paired ex-top-3 > 0; 5 paired ex-best-date > 0; 6 no date above 20% of the
         paired gain (Amendment 1: bars 3-6 judge the paired gain RUG minus CONTROL; bar 2, the kept-book CI90 lower bound, and the kept-book ex-top-3 and
         ex-best-date are report-only). Report only: the four EXP-016 veto rules on RUG's picks, rug-label rate among picks per arm, k2.

Order of a run (as EXP-016's): guards -> ONE tape pass per source -> outcome-blind pre-`started` counts and refusals -> `started` (RUN.lock,
ops tries log AND canonical data/tries.jsonl) -> fit and report. After `started` there is no resume. `--precount` stops after the counts; `--guards-only`
stops after the guards. Memory is EXP-016's (measured about 57 GB at P2 with 4 forked workers): the tape pass is e16.process_source unchanged and
`--max-workers` (default 4, cap 8) is its fork count; the fits are serial and small.
"""

from __future__ import annotations

import argparse
import contextlib
import gc
import hashlib
import json
import math
import os
import re
import signal
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

import tools.exp011_freeze as fz
import tools.exp015_screen as e15
import tools.exp016_rug as rug
import tools.exp016_screen as e16
import tools.exp017_screen as e17
from tools.exp012_latency_sensitivity import DEFAULT_ARTIFACT_DIR, load_oof

TOOL = "tools.exp021_screen"
SCHEMA = "exp021_screen_v1"
REPO_ROOT = Path(__file__).resolve().parent.parent
CANONICAL_TRIES = e16.CANONICAL_TRIES
PLAN = REPO_ROOT / "EXP" / "EXP-021-rug-signals-in-selector-plan.md"
LAMPORTS = e16.LAMPORTS
Refused = e16.Refused

BANNER = (
    "EXPLORATION SCREEN (EXP-021 plan section 4). Not a pre-registration, not a promote, not gate evidence. A pass means 'worth f1 phase 2 and one "
    "confirmation read', never 'has an edge'. This is the 8th family on these 27 dates and the base book is best-of-many. Exit lag 2 is optimistic "
    "against the live exit leak. Leave-one-date-out trains on days before and after each test day (no drift view)."
)

# --- pinned numbers (each tied to the plan by tools/test_exp021_screen.py) -------------------------------------------------------
PRIMARY_CELL = e15.PRIMARY_CELL  # (6, 2)
LEGS = e15.LEGS
MDL = e15.MDL_DEFAULT  # 20: EXP-015's C2 learner, unchanged
FAMILY_ALPHA = 0.025  # DEC-021 per-walk family alpha; plan section 4 bar 1 (strict <)
CONCENTRATION_MAX = e15.CONCENTRATION_MAX  # 0.20
MIN_FIT_ROWS = 20
TRIES_CAP = 1
CONFIG_KEY = "exp021_rug"
STARTED_KEY = "exp021_started"
CONFIG = {"key": CONFIG_KEY, "name": "RUG", "desc": "EXP-012's 18 features + EXP-016 a1-a5,b1-b5,c2,d1-d4,e1, paired vs the retrained 18-feature control"}

FEATURES_CONTROL: list[str] = list(fz.FROZEN_FEATURE_NAMES)
assert len(FEATURES_CONTROL) == 18
RUG_EXTRA: list[str] = [
    "n_launch_buyers", "launch_supply_bought", "launch_supply_held", "n_create_slot_buyers", "max_slot_cohort_held",  # a1-a5
    "creator_launch_share", "creator_n_buys_after", "creator_n_sells", "creator_sold_frac", "creator_share",  # b1-b5
    "launch_sol_share",  # c2 (c1 sniper_buy_share is EXP-012's own)
    "serial_launch_held", "creator_prior_dumps", "prior_dumper_held", "creator_buyer_recurrence",  # d1-d4
    "top3_share",  # e1 (e2 n_buyers is EXP-012's own)
]
assert len(RUG_EXTRA) == 16 and all(n in rug.FEATURE_NAMES for n in RUG_EXTRA) and not set(RUG_EXTRA) & {"sniper_buy_share", "n_buyers"}
FEATURES_RUG: list[str] = FEATURES_CONTROL + RUG_EXTRA
assert len(FEATURES_RUG) == 34 and len(set(FEATURES_RUG)) == 34
ARMS = ("control", "rug")
VETO_RULES = ("r1", "r2", "r3", "r4")

# Pre-declared refusals of the table (outcome-blind, builder-proposed; the manager pins them before the real run).
MIN_FROZEN_NON_P1 = 100  # frozen-selected rows on the non-P1 dates (the gate's own 100-trade floor)
MIN_NON_P1_DATES_WITH_A_FROZEN_PICK = 14  # Amendment 2: fewer and the majority bar B3 (14 of 27) could never pass
MIN_ROWS_PER_NON_P1_DATE = 1  # every non-P1 date must have at least one universe row

OUT_REPORT, OUT_MD = "report.json", "report.md"
OUT_UNIVERSE, OUT_FEATURES = "universe.sha256", "feature_table.sha256"
MARKER = "tries_logged.marker"
OUTCOME_PASS = ("SCREEN PASS: EXP-021 phase 1 clears every plan section 4 bar under both legs. This means 'worth f1 phase 2 and a Part 1 pre-registration with "
                "one confirmation read', never 'has an edge'.")
OUTCOME_FAIL = ("SCREEN FAIL: EXP-021 phase 1 failed a plan section 4 bar. Rug signals as selector inputs on these dates are closed for phase 1: "
                "no new feature subset, learner parameter, count rule or label on these pools.")
OUTCOME_INCOMPLETE = "SCREEN NOT DECIDED: the run was refused or aborted after `started`. No pass is claimed; the try is spent."


# --- the table --------------------------------------------------------------------------------------------------------------------


def _finite(xs: Sequence[float]) -> bool:
    return all(isinstance(v, (int, float)) and math.isfinite(float(v)) for v in xs)


def build_table(cells: Sequence[Mapping[str, Any]], sel: Sequence[bool]) -> list[dict[str, Any]]:
    """EXP-015's row universe on EXP-016's cells: EVERY counted cell, FILLED and MISS (a censored cell, a no-pool and a no-sim mint are in no book).
    Row = frozen-selection flag, the two feature vectors, the primary cell's nets, the strict rug label (FILLED only) and the report-only cells."""
    out: list[dict[str, Any]] = []
    for c, s in zip(cells, sel):
        if not e16.in_book(c):
            continue
        nets = e15.cell_nets(c["cells"][PRIMARY_CELL])
        ef, rf = c.get("exp012_features"), c.get("features")
        filled = c["status"] == "FILLED"
        out.append({
            "mint": c["mint"], "date": c["date"], "block": c["block"], "source": c["source"], "mig_ms": int(c["mig_ms"]), "filled": filled, "frozen": bool(s),
            "ef": None if ef is None else [float(v) for v in ef],
            "rf": None if rf is None else [float(rf[n]) for n in rug.FEATURE_NAMES],
            "flat": nets["flat"], "press": nets["press"],
            "rug": bool(c["label"]["rug"]) if filled else None, "exit_kind": c.get("exit_kind"),
            "alt": {f"{k}_{lag}": e15.cell_nets(cc) for (k, lag), cc in c["cells"].items() if (k, lag) != PRIMARY_CELL and not cc.get("censored")},
        })
    return out


def label_row(r: Mapping[str, Any]) -> int:
    """EXP-015's C2 label: 1 iff a FILLED trade's pressure net (haircut, both fees) is > 0; a MISS is 0."""
    return 1 if (r["filled"] and r["press"] > 0) else 0


def arm_vector(r: Mapping[str, Any], arm: str) -> list[float]:
    rf = dict(zip(rug.FEATURE_NAMES, r["rf"]))
    return list(r["ef"]) + ([rf[n] for n in RUG_EXTRA] if arm == "rug" else [])


def universe_sha256(table: Sequence[Mapping[str, Any]]) -> str:
    """Row universe: (mint, date, status, frozen-selected), sorted. No net, no label."""
    lines = [json.dumps([r["mint"], r["date"], "FILLED" if r["filled"] else "MISS", bool(r["frozen"])]) for r in sorted(table, key=lambda r: (r["date"], r["mint"]))]
    return hashlib.sha256(("\n".join(lines) + "\n").encode("utf-8")).hexdigest()


def feature_table_sha256(table: Sequence[Mapping[str, Any]]) -> str:
    lines = [json.dumps([r["mint"], r["ef"], r["rf"]]) for r in sorted(table, key=lambda r: (r["date"], r["mint"]))]
    return hashlib.sha256(("\n".join(lines) + "\n").encode("utf-8")).hexdigest()


def table_counts(table: Sequence[Mapping[str, Any]], with_p4: bool) -> dict[str, Any]:
    """Outcome-blind: row, status and frozen-selection counts per block and per date, and the feature sanity counts. No net, no label."""
    non_dates = e15.non_p1_dates(with_p4)
    by_block: dict[str, dict[str, int]] = {}
    by_date: dict[str, dict[str, int]] = {}
    for r in table:
        for d, k in ((by_block, r["block"]), (by_date, r["date"])):
            e = d.setdefault(k, {"rows": 0, "filled": 0, "miss": 0, "frozen": 0})
            e["rows"] += 1
            e["filled" if r["filled"] else "miss"] += 1
            e["frozen"] += int(r["frozen"])
    seen: set[str] = set()
    dup = 0
    for r in table:
        dup += int(r["mint"] in seen)
        seen.add(r["mint"])
    return {
        "n_rows": len(table), "by_block": dict(sorted(by_block.items())), "by_date": dict(sorted(by_date.items())),
        "n_missing_features": sum(1 for r in table if r["ef"] is None or r["rf"] is None),
        "n_nonfinite_features": sum(1 for r in table if r["ef"] is not None and r["rf"] is not None and not (_finite(r["ef"]) and _finite(r["rf"]))),
        "n_duplicate_mints": dup, "non_p1_dates_expected": len(non_dates), "non_p1_dates_without_rows": [d for d in non_dates if by_date.get(d, {}).get("rows", 0) < MIN_ROWS_PER_NON_P1_DATE],
        "non_p1_dates_with_frozen_pick": sum(1 for d in non_dates if by_date.get(d, {}).get("frozen", 0) >= 1),
        "frozen_selected_non_p1": sum(v["frozen"] for k, v in by_block.items() if k != "P1"),
        "k2_cells_available": any(k.startswith("2_") for r in table for k in r["alt"]),
        "alt_cell_keys": sorted({k for r in table for k in r["alt"]}),
    }


def check_table_limits(counts: Mapping[str, Any], with_p4: bool) -> list[str]:
    """The pre-declared refusals of this tool (counts only). Empty = none."""
    why: list[str] = []
    if not with_p4:
        why.append("the EXP-011 block (P4) is not in the pool: the plan's 27 non-P1 dates do not exist (no smaller pool is decided on)")
    if counts["n_rows"] == 0:
        why.append("0 universe rows")
    if counts["n_missing_features"]:
        why.append(f"{counts['n_missing_features']} counted cells lack the EXP-012 or the EXP-016 feature vector")
    if counts["n_nonfinite_features"]:
        why.append(f"{counts['n_nonfinite_features']} universe rows carry a non-finite feature")
    if counts["n_duplicate_mints"]:
        why.append(f"{counts['n_duplicate_mints']} mints are in the universe twice")
    if counts["non_p1_dates_without_rows"]:
        why.append(f"{len(counts['non_p1_dates_without_rows'])} non-P1 dates have no universe row: {counts['non_p1_dates_without_rows'][:5]}")
    if counts["non_p1_dates_with_frozen_pick"] < MIN_NON_P1_DATES_WITH_A_FROZEN_PICK:
        why.append(f"only {counts['non_p1_dates_with_frozen_pick']} of {counts['non_p1_dates_expected']} non-P1 dates have a frozen pick (< {MIN_NON_P1_DATES_WITH_A_FROZEN_PICK}): bar B3 could never pass")
    if counts["frozen_selected_non_p1"] < MIN_FROZEN_NON_P1:
        why.append(f"{counts['frozen_selected_non_p1']} frozen-selected rows on the non-P1 dates (< {MIN_FROZEN_NON_P1})")
    return why


def enforce_table_limits(counts: Mapping[str, Any], with_p4: bool) -> None:
    why = check_table_limits(counts, with_p4)
    if why:
        raise Refused("pre-declared EXP-021 limit(s) exceeded: " + "; ".join(why))


# --- folds ------------------------------------------------------------------------------------------------------------------------


@contextlib.contextmanager
def feature_names(names: Sequence[str]) -> Iterator[None]:
    """`fz._fit` names the LightGBM dataset columns from `fz.FROZEN_FEATURE_NAMES` (18). The RUG arm has 34 columns, so the name list is swapped for
    the call and restored (as `e15.fit_cfg` does for min_data_in_leaf). Nothing else about the learner changes."""
    old = fz.FROZEN_FEATURE_NAMES
    fz.FROZEN_FEATURE_NAMES = list(names)
    try:
        yield
    finally:
        fz.FROZEN_FEATURE_NAMES = old


def fit_predict(train_x: Sequence[Sequence[float]], train_y: Sequence[int], test_x: Sequence[Sequence[float]], names: Sequence[str]) -> list[float] | None:
    """EXP-015's retrain on one training set: `e15.fit_cfg(x, y, 20)` (num_leaves 15, lr 0.05, 100 rounds, seed 1, scale_pos_weight). None if the set
    cannot train (under 20 rows or one class). Trees are invariant to monotone transforms, so the counts are not log1p'd (EXP-016's logistic needed it)."""
    if len(train_y) < MIN_FIT_ROWS or len({int(v) for v in train_y}) < 2:
        return None
    with feature_names(names):
        m = e15.fit_cfg(train_x, list(train_y), MDL)
    return fz._predict(m, test_x)


def top_n(scores: Mapping[str, float], n: int) -> set[str]:
    """The n highest scores, ties broken by mint id (deterministic, outcome-blind)."""
    ranked = sorted(scores, key=lambda m: (-scores[m], m))
    return set(ranked[: max(0, n)])


def run_folds(table: Sequence[Mapping[str, Any]], dates: Sequence[str] | None = None) -> dict[str, Any]:
    """Leave-one-date-out for both arms. `dates` defaults to every date in the table. Returns per-arm selections (mint -> bool), per-fold records.
    A fold that cannot train raises Refused (the paired comparison would be broken)."""
    ds = sorted({r["date"] for r in table}) if dates is None else list(dates)
    sel: dict[str, dict[str, bool]] = {a: {} for a in ARMS}
    folds: list[dict[str, Any]] = []
    y_all = [label_row(r) for r in table]
    vec = {a: [arm_vector(r, a) for r in table] for a in ARMS}
    names = {"control": FEATURES_CONTROL, "rug": FEATURES_RUG}
    for d in ds:
        start = e15.date_start_ms(d)
        end = start + e15.DAY_MS
        te = [i for i, r in enumerate(table) if r["date"] == d]
        tr = [i for i, r in enumerate(table) if r["date"] != d and not (start - e15.PURGE_MS <= r["mig_ms"] < start) and not (end <= r["mig_ms"] < end + e15.PURGE_MS)]
        n_frozen = sum(1 for i in te if table[i]["frozen"])
        rec: dict[str, Any] = {"date": d, "n_test": len(te), "n_train": len(tr), "n_frozen": n_frozen}
        for a in ARMS:
            scores = fit_predict([vec[a][i] for i in tr], [y_all[i] for i in tr], [vec[a][i] for i in te], names[a]) if te else []
            if scores is None:
                raise Refused(f"fold {d} ({a}) cannot train: {len(tr)} rows or one class")
            picked = top_n({table[i]["mint"]: s for i, s in zip(te, scores)}, n_frozen)
            for i in te:
                sel[a][table[i]["mint"]] = table[i]["mint"] in picked
            rec[f"n_{a}"] = len(picked)
        folds.append(rec)
    return {"sel": sel, "folds": folds, "dates": ds}


# --- statistics and bars --------------------------------------------------------------------------------------------------------


def _scope(table: Sequence[Mapping[str, Any]], non_p1: bool) -> list[Mapping[str, Any]]:
    return [r for r in table if r["block"] != "P1"] if non_p1 else list(table)


def book(rows: Sequence[Mapping[str, Any]], pick: Mapping[str, bool]) -> list[dict[str, Any]]:
    """The arm's picks as trades (filled and MISS), in the shape `e15.leg_stats` takes."""
    return [{"mint": r["mint"], "day": r["date"], "filled": r["filled"], "flat": r["flat"], "press": r["press"]} for r in rows if pick.get(r["mint"])]


def paired_by_date(rows: Sequence[Mapping[str, Any]], a: Mapping[str, bool], b: Mapping[str, bool], leg: str, alt: str | None = None) -> dict[str, list[float]]:
    """x_m = (s_a - s_b) * net_m over EVERY row (0 where the two agree). `alt` scores the report-only cell instead (0 if the row has no such cell)."""
    out: dict[str, list[float]] = {}
    for r in rows:
        net = r[leg] if alt is None else ((r["alt"].get(alt) or {}).get(leg, 0.0))
        out.setdefault(r["date"], []).append((int(bool(a.get(r["mint"]))) - int(bool(b.get(r["mint"])))) * net)
    return out


def paired_stats(by_date: Mapping[str, Sequence[float]], n_dates: int) -> dict[str, Any]:
    """Mean, date-cluster CI90 and one-sided p (10,000 draws, seed 1), the per-date sums, ex-top-3 and ex-best-date of the paired gain."""
    xs = sorted((v for vs in by_date.values() for v in vs), reverse=True)
    mean = (sum(xs) / len(xs) / LAMPORTS) if xs else None
    ci = e15.date_cluster_ci(by_date)
    p = e17.boot_p(by_date)
    sums = {d: sum(v) / LAMPORTS for d, v in by_date.items()}
    total = sum(sums.values())
    pos = [v for v in sums.values() if v > 0]
    share = (max(pos) / sum(pos)) if pos and sum(pos) > 0 else None  # report-only: share of the positive-date total
    net_share = (max(sums.values()) / total) if sums and total > 0 else None  # B6 (Amendment 2): share of the NET paired total
    return {"n": len(xs), "mean_x_sol": mean, "ci90_date_sol": ci, "p_one_sided": p, "total_sol": total, "dates_positive": len(pos), "of_dates": n_dates,
            "max_date_share_of_positive_total": share, "max_date_share_of_net_total": net_share, "ex_top3_sol": (sum(xs[3:]) / LAMPORTS) if len(xs) > 3 else None,
            "ex_best_date_sol": total - max(sums.values(), default=0.0), "by_date_sol": dict(sorted(sums.items()))}


def bars(table: Sequence[Mapping[str, Any]], sel: Mapping[str, Mapping[str, bool]], with_p4: bool) -> dict[str, Any]:
    """Plan section 4 as amended (Amendment 1) on the non-P1 dates, both legs. Bars 1, 3, 4, 5, 6 all judge the PAIRED gain (RUG minus CONTROL). Bar 2 and the kept-book ex-top-3 and ex-best-date are report-only (`kept_book_report_only`)."""
    non = _scope(table, True)
    n_non = len(e15.non_p1_dates(with_p4))
    out: dict[str, Any] = {"n_non_p1_dates": n_non, "n_rows": len(non)}
    for leg in LEGS:
        pr = paired_stats(paired_by_date(non, sel["rug"], sel["control"], leg), n_non)
        st = e15.leg_stats(book(non, sel["rug"]), leg)
        days = st["dates"]
        best = max((d["total_sol"] for d in days), default=None)
        b = {
            "B1_paired_mean_p": {"pass": bool(pr["mean_x_sol"] is not None and pr["mean_x_sol"] > 0 and pr["p_one_sided"] is not None and pr["p_one_sided"] < FAMILY_ALPHA),
                                 "mean_x_sol": pr["mean_x_sol"], "p_one_sided": pr["p_one_sided"], "alpha": FAMILY_ALPHA, "ci90_date_sol": pr["ci90_date_sol"]},
            "B3_majority_dates_positive": {"pass": bool(pr["dates_positive"] * 2 > n_non), "dates_positive": pr["dates_positive"], "of_dates": n_non},
            "B4_ex_top3": {"pass": bool(pr["ex_top3_sol"] is not None and pr["ex_top3_sol"] > 0), "ex_top3_sol": pr["ex_top3_sol"], "paired_total_sol": pr["total_sol"]},
            "B5_ex_best_date": {"pass": bool(pr["n"] and pr["ex_best_date_sol"] > 0), "ex_best_date_sol": pr["ex_best_date_sol"]},
            "B6_paired_concentration": {"pass": bool(pr["total_sol"] > 0 and pr["max_date_share_of_net_total"] is not None and pr["max_date_share_of_net_total"] <= CONCENTRATION_MAX),
                                        "max_date_share_of_net_total": pr["max_date_share_of_net_total"], "max_date_share_of_positive_total": pr["max_date_share_of_positive_total"], "paired_total_sol": pr["total_sol"], "limit": CONCENTRATION_MAX},
        }
        b["all"] = all(v["pass"] for v in b.values() if isinstance(v, dict))
        b["paired_report"] = pr
        # Amendment 1: REPORT-ONLY (never gating). At 0.05 SOL fixed fees dominate the kept book, so a selection gain cannot show on it.
        b["kept_book_report_only"] = {"ci90_book_stats": st["ci90_sol"], "ci90_date_cluster": st["ci90_date_sol"], "ci_lo_gt_0": bool(
            st["ci_lo"] is not None and st["ci_lo"] > 0 and st["ci_lo_date"] is not None and st["ci_lo_date"] > 0), "mean_sol": st["mean_sol"], "n": st["n"],
            "total_sol": st["total_sol"], "ex_top3_sol": st["ex_top3_sol"], "ex_best_date_sol": None if best is None else st["total_sol"] - best,
            "dates_positive": st["dates_positive"], "of_dates": n_non}
        out[leg] = b
    out["passes"] = bool(all(out[leg]["all"] for leg in LEGS))
    return out


def rug_rate(rows: Sequence[Mapping[str, Any]], pick: Mapping[str, bool]) -> dict[str, Any]:
    """Strict rug-label rate among the arm's FILLED picks (the label exists only on filled trades)."""
    f = [r for r in rows if pick.get(r["mint"]) and r["filled"]]
    n = sum(1 for r in f if r["rug"])
    return {"n_filled_picks": len(f), "n_rug": n, "rate": (n / len(f)) if f else None}


def veto_report(rows: Sequence[Mapping[str, Any]], pick: Mapping[str, bool]) -> dict[str, Any]:
    """Report-only: EXP-016's four arithmetic rules on RUG's picks (the same thresholds, `e16.rule_veto`). A vetoed FILLED pick is removed; a vetoed MISS
    keeps its fee. Outside every bar and every family; a veto goes into a trial config only after a later pre-registration."""
    out: dict[str, Any] = {}
    picks = [r for r in rows if pick.get(r["mint"])]
    for cid in VETO_RULES:
        veto = {r["mint"]: bool(r["filled"] and e16.rule_veto(cid, r["rf"])) for r in picks}
        kept = [r for r in picks if not veto[r["mint"]]]
        vf = [r for r in picks if veto[r["mint"]]]
        out[cid] = {"desc": e16.CANDIDATES[cid]["desc"], "n_picks": len(picks), "n_vetoed_filled": len(vf), "n_rug_vetoed": sum(1 for r in vf if r["rug"]),
                    **{f"kept_mean_sol_{leg}": ((sum(r[leg] for r in kept) / len(kept) / LAMPORTS) if kept else None) for leg in LEGS},
                    **{f"picks_mean_sol_{leg}": ((sum(r[leg] for r in picks) / len(picks) / LAMPORTS) if picks else None) for leg in LEGS}}
    return out


def alt_cell_report(table: Sequence[Mapping[str, Any]], sel: Mapping[str, Mapping[str, bool]]) -> dict[str, Any]:
    """Report-only: the same picks scored at the cached alternative cells. k2 is reported only if the cached cells hold one (EXP-016's do not)."""
    non = _scope(table, True)
    keys = sorted({k for r in non for k in r["alt"]})
    out: dict[str, Any] = {"cells": {}, "k2": ("available" if any(k.startswith("2_") for k in keys) else
                                              f"not available: the cached cells are {sorted(['6_2'] + keys)}; k2 would need a re-simulation (a new tape pass), so it is skipped")}
    for k in keys:
        out["cells"][k] = {leg: _mean_or_none([x for xs in paired_by_date(non, sel["rug"], sel["control"], leg, alt=k).values() for x in xs], len(non)) for leg in LEGS}
    return out


def _mean_or_none(xs: Sequence[float], n: int) -> float | None:
    return (sum(xs) / n / LAMPORTS) if n else None


def report_only(table: Sequence[Mapping[str, Any]], sel: Mapping[str, Mapping[str, bool]], with_p4: bool) -> dict[str, Any]:
    non, allr = _scope(table, True), list(table)
    p1 = [r for r in table if r["block"] == "P1"]
    frozen = {r["mint"]: r["frozen"] for r in table}
    n_non, n_all = len(e15.non_p1_dates(with_p4)), len(e15.pool_dates(with_p4))
    out: dict[str, Any] = {
        "rug_label_rate": {s: {"arm": a, "non_p1": rug_rate(non, pk), "all_dates": rug_rate(allr, pk)} for s, a, pk in (("frozen", "frozen EXP-012", frozen), ("control", "control", sel["control"]), ("rug", "rug", sel["rug"]))},
        "veto_on_rug_picks": {"non_p1": veto_report(non, sel["rug"]), "all_dates": veto_report(allr, sel["rug"])},
        "alt_cells_rug_minus_control": alt_cell_report(table, sel),
        "books": {a: {"non_p1": e15.scope_report(book(non, pk), n_non), "all_dates": e15.scope_report(book(allr, pk), n_all)} for a, pk in (("frozen", frozen), ("control", sel["control"]), ("rug", sel["rug"]))},
        "paired_vs_frozen_non_p1": {a: {leg: paired_stats(paired_by_date(non, sel[a], frozen, leg), n_non) for leg in LEGS} for a in ARMS},
        "p1_report_only_paired_rug_minus_control": {leg: paired_stats(paired_by_date(p1, sel["rug"], sel["control"], leg), len(e15.block_dates("P1"))) for leg in LEGS},
        "paired_ex_top3_and_best_date_non_p1": {leg: {k: v for k, v in paired_stats(paired_by_date(non, sel["rug"], sel["control"], leg), n_non).items() if k in ("ex_top3_sol", "ex_best_date_sol")} for leg in LEGS},
        "jaccard_rug_control_non_p1": e15.jaccard({r["mint"] for r in non if sel["rug"].get(r["mint"])}, {r["mint"] for r in non if sel["control"].get(r["mint"])}),
        "rug_events_non_p1_filled": sum(1 for r in non if r["filled"] and r["rug"]),
    }
    return out


def decide(b: Mapping[str, Any] | None) -> dict[str, Any]:
    if b is None:
        return {"passes": False, "outcome": OUTCOME_INCOMPLETE}
    return {"passes": bool(b["passes"]), "outcome": OUTCOME_PASS if b["passes"] else OUTCOME_FAIL}


def run_screen(table: Sequence[Mapping[str, Any]], with_p4: bool, dates: Sequence[str] | None = None) -> dict[str, Any]:
    """AFTER `started`: both arms' folds, the bars, the report-only items."""
    fr = run_folds(table, dates)
    b = bars(table, fr["sel"], with_p4)
    return {"folds": fr["folds"], "bars": b, "decision": decide(b), "report_only": report_only(table, fr["sel"], with_p4)}


# --- report -------------------------------------------------------------------------------------------------------------------------


def first_line(with_p4: bool) -> str:
    n = len(e15.pool_dates(with_p4))
    if with_p4:
        return f"EXP-021 screen on the {n}-UTC-date pool ({len(e15.non_p1_dates(True))} non-P1 dates decide; the P1 dates are report-only)."
    return f"EXP-021 screen on the SMALLER {n}-UTC-date pool (P4 is NOT in it): the plan's 27 non-P1 dates do not exist. Every number scales accordingly."


def _f(v: Any, d: int = 5) -> str:
    return "n/a" if v is None else f"{v:.{d}f}"


CAVEATS = (
    "Exploration. A pass is not evidence for an edge: one family, eight on these 27 dates, a best-of-many base book, a few dozen strict rug events.",
    "Both arms are retrained on the same folds by the same learner (EXP-015 C2: LightGBM, min_data_in_leaf 20); the RUG arm has 34 columns, so feature_fraction 0.9 samples a different number of columns than the control's 18. That is part of the feature-set change, not a tuned difference.",
    "The count per held-out date is the frozen model's own selection count that date (top-n by score, ties by mint id). It is not a score threshold, so a date with no frozen pick has no pick in either arm.",
    "Bars 1 and 3-6 judge the paired gain (RUG minus CONTROL) on the non-P1 dates (Amendment 1). The RUG kept book's CI90 lower bound, ex-top-3 and ex-best-date are report-only: at 0.05 SOL fixed fees dominate the kept book, so a selection gain cannot show on it; the confirmation pre-registration sets the stake and the full gate.",
    "A UTC date with a zero paired gain counts as NOT positive. The date-cluster CI resamples whole dates.",
    "Exit lag 2 is optimistic against the live exit leak. k2 was not simulated (see the alt-cell block).",
    "Purge: rows within 35 minutes before the start or after the end of the held-out date are dropped from that fold's training set.",
)


def render_md(rep: Mapping[str, Any]) -> str:
    L = [rep["first_line"], "", f"**{rep.get('p1b', P1B_EXCLUDED)}**: no P1B row is read, trained on or reported.", "", rep["banner"], "", "## Matched outcome", "", f"**{rep['decision']['outcome']}**", ""]
    pc = rep.get("pre_started") or {}
    L += [f"- Pre-`started` counts (ids and counts only): {json.dumps({k: v for k, v in pc.items() if k not in ('no_pool_mint_ids', 'censored_mints')}, default=str)}",
          f"- Table counts: {json.dumps(rep.get('table_counts'), default=str)}",
          f"- Prior tries per pool at `started`: {rep.get('prior_tries')}; universe sha256 `{rep.get('universe_sha256')}`; feature table sha256 `{rep.get('feature_table_sha256')}`.", ""]
    b = rep.get("bars")
    if b:
        L += ["## Bars (non-P1 dates, both legs)", "", "| bar | leg | pass | detail |", "| --- | --- | --- | --- |"]
        for leg in LEGS:
            for k, v in b[leg].items():
                if isinstance(v, dict) and "pass" in v:
                    L.append(f"| {k} | {leg} | {v['pass']} | {json.dumps(v, default=str)} |")
        L.append("")
        ro = rep.get("report_only") or {}
        L += ["## Report-only", "", f"- Rug-label rate among picks: {json.dumps(ro.get('rug_label_rate'), default=str)}",
              f"- EXP-016 veto rules on RUG's picks: {json.dumps(ro.get('veto_on_rug_picks'), default=str)}",
              f"- Kept RUG book (report-only): {json.dumps({leg: b[leg].get('kept_book_report_only') for leg in LEGS}, default=str)}",
              f"- Alt cells (RUG minus control): {json.dumps(ro.get('alt_cells_rug_minus_control'), default=str)}", ""]
    L += ["## Disclosures", "", *[f"- {c}" for c in CAVEATS], ""]
    return "\n".join(L) + "\n"


def write_report(out_dir: Path, rep: Mapping[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / (OUT_REPORT + ".tmp")
    tmp.write_text(json.dumps(rep, indent=2, default=str) + "\n", encoding="utf-8")
    os.replace(tmp, out_dir / OUT_REPORT)
    (out_dir / OUT_MD).write_text(render_md(rep), encoding="utf-8")


# --- tries (the EXP-016 two-log pattern, one candidate) ---------------------------------------------------------------------------


def prior_exp021_lines(log: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not Path(log).is_file():
        return out
    for line in Path(log).read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if str((rec.get("config") or {}).get("key", "")).startswith("exp021_"):
            out.append(rec)
    return out


def check_no_prior_tries(*logs: Path) -> None:
    for log in {str(Path(x).resolve()): Path(x) for x in logs}.values():
        found = prior_exp021_lines(log)
        if found:
            raise Refused(f"{log} already holds {len(found)} exp021_* line(s); the single try is spent or started. The run refuses")


def started_blocks(with_p4: bool) -> list[dict[str, str]]:
    return [{"start_hour": e15.BLOCKS[b][0], "end_hour_exclusive": e15.BLOCKS[b][1], "host": "mal-research-0", "ledger_owner": "EXP-021 row universe (bookkeeping, not a pool try)"}
            for b in ("P2", "P3") + (("P4",) if with_p4 else ())]


def _in_log(log: Path, key: str, group: str, status: str, result_path: Path) -> bool:
    if not Path(log).is_file():
        return False
    for line in Path(log).read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        c = rec.get("config") or {}
        if rec.get("tool") == TOOL and c.get("key") == key and c.get("pool_group") == group and c.get("status") == status and rec.get("result_path") == str(result_path):
            return True
    return False


def _append(log: Path, key: str, group: str, status: str, blocks: Sequence[Mapping[str, str]], out_dir: Path, extra: Mapping[str, Any]) -> bool:
    from tools import mal_result

    result_path = out_dir / OUT_REPORT
    if _in_log(log, key, group, status, result_path):
        return False
    mal_result.append_try(
        log, tool=TOOL,
        config={"key": key, "experiment": "EXP-021 screen", "status": status, "pool_group": group, "k": e16.K, "size_sol": e16.SIZE_SOL, "fee_lamports": e16.FEE,
                "exit_lag": e16.EXIT_LAG, "pricing": "V", "selection": "LODO, count-matched to the frozen selection", **extra},
        data_blocks=list(blocks), result_path=result_path, role="exploration",
    )
    return True


def log_tries(out_dir: Path, log: Path, marker_name: str, status: str, with_p4: bool, extra: Mapping[str, Any]) -> int:
    """`started`: ONE line (key exp021_started) on the universe-bookkeeping blocks, before any fit. Any other status: one line per pool group (the
    one candidate counts as a try on every pool it touches). Idempotent per log (marker + a scan); a non-completed line is never added after `completed`."""
    from tools.exp012_exit_sensitivity import _read_marker, _write_marker

    marker = out_dir / marker_name
    done = _read_marker(marker)
    n = 0
    out_dir.mkdir(parents=True, exist_ok=True)
    jobs: list[tuple[str, str, Sequence[Mapping[str, str]], Mapping[str, Any]]] = []
    if status == "started":
        jobs.append((STARTED_KEY, "universe", started_blocks(with_p4), {"keys": [CONFIG_KEY], **extra}))
    else:
        for g, blocks in e15.pool_group_blocks(with_p4).items():
            jobs.append((CONFIG_KEY, g, blocks, {"config": CONFIG["name"], **extra}))
    for key, g, blocks, ex in jobs:
        mk = f"{key}:{g}:{status}"
        if mk in done:
            continue
        if status not in ("completed", "started") and _in_log(log, key, g, "completed", out_dir / OUT_REPORT):
            continue
        if _append(log, key, g, status, blocks, out_dir, ex):
            n += 1
        done.add(mk)
        _write_marker(marker, done)
    return n


def log_all(out_dir: Path, tries_path: Path, canonical: Path, status: str, with_p4: bool, extra: Mapping[str, Any]) -> dict[str, Any]:
    """Ops tries log and the canonical data/tries.jsonl (when they differ): the same lines in both."""
    out = {"logged": log_tries(out_dir, tries_path, MARKER, status, with_p4, extra), "log_path": str(tries_path), "status": status}
    if Path(canonical).resolve() != Path(tries_path).resolve():
        out["canonical_logged"] = log_tries(out_dir, canonical, "tries_logged_canonical.marker", status, with_p4, extra)
    return out


# --- Amendment 3: loader limits sized for EXP-021, a censoring signature, and P1B dropped ----------------------------------------------


LIMIT_NO_CREATE_021 = 0.08  # Amendment 3(a): no-create-row share per source. EXP-016's own `e16.LIMIT_NO_CREATE` (2%) is NOT changed.
SIG_WINDOW_H = 24  # the censoring signature compares the first 24 h of a source's counted window with the rest
SIG_MIN_SPAN_H = 48  # a P1 source with a shorter counted span is too short for the comparison (exempt from the signature check only)
P1B_EXCLUDED = "P1B excluded (Amendment 3)"


@contextlib.contextmanager
def no_create_limit(limit: float) -> Iterator[None]:
    """`e16.check_limits` reads the module constant `LIMIT_NO_CREATE`. The limit is swapped for the call and restored (as `feature_names` does), so
    EXP-016's value is never changed."""
    old = e16.LIMIT_NO_CREATE
    e16.LIMIT_NO_CREATE = limit
    try:
        yield
    finally:
        e16.LIMIT_NO_CREATE = old


def censoring_profile(src: Any, pool_hours: Sequence[str]) -> dict[str, Any]:
    """Outcome-blind: of the migrations in the source's counted window that have a pool, how many have no create row, split into the first
    SIG_WINDOW_H hours of the counted window and the rest. Left-censoring (a token created before the tape that graduates late) shows as a higher share
    early. The counted window starts at the block start, or at the first pool hour read for a P1 source that begins later."""
    blk_a, blk_b = e15.hour_ms(e15.BLOCKS[src.block][0]), e15.hour_ms(e15.BLOCKS[src.block][1])
    hours = sorted(pool_hours)
    start = max(blk_a, e15.hour_ms(hours[0])) if hours else blk_a
    end = min(blk_b, e15.hour_ms(hours[-1]) + e15.HOUR_MS) if hours else blk_b
    pool_by_mint = rug.migration_pool_map(src.migrations.values())
    excluded = set(src.excluded_no_bonding).union(*[set(v) for v in src.excluded_other.values()])
    cut = start + SIG_WINDOW_H * e15.HOUR_MS
    out: dict[str, Any] = {"start_ms": start, "span_h": max(0, (end - start) // e15.HOUR_MS), "first": {"n": 0, "no_create": 0}, "after": {"n": 0, "no_create": 0}}
    for m, mr in src.migrations.items():
        bt = mr.get("block_time")
        if not isinstance(bt, int) or isinstance(bt, bool) or not e16.in_counted_window(src.block, mr) or not pool_by_mint.get(m):
            continue
        side = out["first" if bt * 1000 < cut else "after"]
        side["n"] += 1
        side["no_create"] += int(m not in src.creates and m not in excluded)
    return out


def check_censoring_signature(results: Sequence[Mapping[str, Any]]) -> list[str]:
    """Amendment 3(a): a source's no-create share must be ABOVE in its first 24 h what it is after (a left-censoring signature, not a missing-hours
    loader bug). Refuse when it is not. A P1 source shorter than SIG_MIN_SPAN_H, or with no migration on one side, is exempt from this check only."""
    why: list[str] = []
    for r in results:
        prof = r.get("censoring")
        if prof is None:
            why.append(f"{r['tag']}: no censoring profile (the signature check cannot run)")
            continue
        f, a = prof["first"], prof["after"]
        if str(r["tag"]).startswith("P1") and (prof["span_h"] < SIG_MIN_SPAN_H or not f["n"] or not a["n"]):
            continue
        if not f["n"] or not a["n"]:
            why.append(f"{r['tag']}: no migrations in the first {SIG_WINDOW_H} h or after it: the censoring signature cannot be read")
            continue
        sf, sa = f["no_create"] / f["n"], a["no_create"] / a["n"]
        if not sf > sa:
            why.append(f"{r['tag']}: no-create share {f['no_create']}/{f['n']} in the first {SIG_WINDOW_H} h is not above {a['no_create']}/{a['n']} after it: not a censoring signature")
    return why


def check_limits(results: Sequence[Mapping[str, Any]], oof: Mapping[str, float] | None = None, oof_cover: Mapping[str, Any] | None = None) -> list[str]:
    """EXP-016's pre-declared limits with the no-create limit at 8% (Amendment 3), plus the censoring-signature check. P1B is not a source here."""
    with no_create_limit(LIMIT_NO_CREATE_021):
        why = e16.check_limits(results, oof, oof_cover)
    return why + check_censoring_signature(results)


def enforce_limits(results: Sequence[Mapping[str, Any]], oof: Mapping[str, float] | None = None, oof_cover: Mapping[str, Any] | None = None) -> None:
    why = check_limits(results, oof, oof_cover)
    if why:
        raise Refused("pre-declared limit(s) exceeded (plan 13 item 9, Amendment 3): " + "; ".join(why))


def p1_kept_dates() -> list[str]:
    """The P1 dates covered by the retained P1 sources (P1A and P1C): the OOF-without-cell check must not count P1B-only dates as missing cells."""
    from tools.exploration_exits import POOL_HOURS as A_HOURS
    from tools.oracle_insample_adapter import POOL_C_HOURS

    return sorted({h[:10] for h in list(A_HOURS) + list(POOL_C_HOURS)} & set(e15.block_dates("P1")))


def guard_p1_no_live(fast: Path | str, insample: Path | str, verify: bool = True) -> dict[str, Any]:
    """EXP-015's `guard_p1` for the two retained P1 views (same pinned names, VIEW.sha256 and view pin); P1B (live) is not read."""
    want = {"fast": "fast-pool-2026-09-18T23_2026-09-22T00", "insample": "oracle-insample-2026-09-22_25"}
    roots: dict[str, Path] = {}
    shas: dict[str, str] = {}
    for label, p in (("fast", fast), ("insample", insample)):
        rp = e15.refuse_reserved(p, f"P1 {label} root")
        if Path(rp).name != want[label]:
            raise e15.Refused(f"P1 {label} root {str(p)!r}: directory name {Path(rp).name!r} is not the pinned {want[label]!r}")
        if not (Path(rp) / "VIEW.sha256").is_file():
            raise e15.Refused(f"P1 {label} root {rp}: VIEW.sha256 not found")
        roots[label] = Path(rp)
        if verify:
            try:
                fz.verify_view_sha256(Path(rp))
                fz.check_view_pin(Path(rp))
            except SystemExit as exc:
                raise e15.Refused(str(exc)) from None
            shas[label] = hashlib.sha256((Path(rp) / "VIEW.sha256").read_bytes()).hexdigest()
    return {"roots": roots, "view_sha256": shas}


def run_guards(args: argparse.Namespace, enforce_base: bool = True, verify: bool = True, pin_required: bool = True) -> dict[str, Any]:
    """`e16.run_guards` without P1B: refuses if `--p1-oracle-live-dir` is given (Amendment 3(b))."""
    if getattr(args, "p1_oracle_live_dir", None) is not None:
        raise Refused(f"--p1-oracle-live-dir (P1B, the Oracle live tape) is given: {P1B_EXCLUDED}; the tool refuses it")
    if pin_required:
        e16.check_pin_ready()
    try:
        e15.check_workers(args.max_workers)
        g1 = guard_p1_no_live(args.p1_fast_dir, args.p1_oracle_insample_dir, verify)
        if not args.p2_view_dir:
            raise Refused("--p2-view-dir is required (explore-0814 w1..w7)")
        g2 = e15.guard_p2(args.p2_view_dir, verify, enforce_base)
        g3 = e15.guard_p3(args.p3_root, verify, enforce_base)
        g4 = e15.guard_p4(args.p4_view_dir or [], verify)
        frozen = e15.check_frozen_model(args.artifact_dir)
        p3_hours = [h for w in g3["walkers"] for h in w.hours]
        e15.assert_hours_allowed(list(g2["pool"]) + list(g4["pool"] if g4 else []) + p3_hours, with_p4=g4 is not None)
    except e15.Refused as exc:
        raise Refused(str(exc)) from None
    e16.refuse_extra_reserved(args)
    sha = e16.check_vmap(args.vmap) if pin_required else None
    return {"g1": g1, "g2": g2, "g3": g3, "g4": g4, "vmap_sha256": sha, "frozen": frozen, "with_p4": g4 is not None, "p1b": P1B_EXCLUDED}


def build_sources(g: Mapping[str, Any]) -> list[tuple[str, str, Any, list[str], list[Path]]]:
    """EXP-016's sources without P1B. `e16.build_sources` wants a `live` root for the resolver table; the fast root stands in and is never read, because
    the P1B entry is dropped before anything is loaded."""
    g1 = {**g["g1"], "roots": {**g["g1"]["roots"], "live": g["g1"]["roots"]["fast"]}}
    return [s for s in e16.build_sources({**g, "g1": g1}) if s[0] != "P1B"]


# --- freeze mode (Part 1): train the two arms once on ALL exploration rows ------------------------------------------------------------


FREEZE_FILES = {"rug": ("model.txt", "model.md5", "features.json"), "control": ("control-model.txt", "control-model.md5", "control-features.json")}
FREEZE_MANIFEST = "train-manifest.json"
FREEZE_SCHEMA = "exp021_freeze_v1"
FREEZE_SOURCES = ("P1A", "P1C", "P2", "P3", "P4")  # P1B is excluded (Amendment 3(b)); every pool is training, there is no held-out date


def freeze_models(table: Sequence[Mapping[str, Any]], out_dir: Path, *, head: str, vmap_sha256: str | None, with_p4: bool = True) -> dict[str, Any]:
    """Train RUG (34 columns) and CONTROL (18) once on every table row and write the artifacts. Same learner as the screen (`e15.fit_cfg(x, y, MDL)`:
    EXP-015 C2, seed 1, deterministic, num_threads 1), same label (`label_row`), same column names. Bit-reproducible: nothing in the files depends on
    the clock, the host or the thread count. Reads no outcome path and no held-out date (there is none); appends NO tries line (training on
    already-read exploration labels spends no try). Refuses unless the V-map pin is set, the pool is complete, and OUT_DIR holds no frozen file."""
    e16.check_pin_ready()
    if not vmap_sha256 or not re.fullmatch(r"[0-9a-f]{64}", vmap_sha256):
        raise Refused("freeze needs the V map sha256 the guards verified")
    if not with_p4:
        raise Refused("freeze trains on P1A, P1C, P2, P3 and P4: P4 is not in the pool")
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise Refused("freeze needs a code head (40 hex)")
    out_dir = Path(out_dir)
    if any((out_dir / n).exists() for names in FREEZE_FILES.values() for n in names) or (out_dir / FREEZE_MANIFEST).exists():
        raise Refused(f"{out_dir} already holds a frozen artifact: the freeze refuses to overwrite")
    rows = sorted(table, key=lambda r: (r["date"], r["mint"]))
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["source"]] = counts.get(r["source"], 0) + 1
    unknown = sorted(set(counts) - set(FREEZE_SOURCES))
    if unknown:
        raise Refused(f"freeze rows from a source outside {list(FREEZE_SOURCES)}: {unknown}")
    if any(r["ef"] is None or r["rf"] is None for r in rows):
        raise Refused("a row lacks its feature vector")
    y = [label_row(r) for r in rows]
    if len(y) < MIN_FIT_ROWS or len(set(y)) < 2:
        raise Refused("the freeze set cannot train: too few rows or one class")
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "schema": FREEZE_SCHEMA, "experiment": "EXP-021 Part 1", "code_head": head, "vmap_sha256": vmap_sha256, "with_p4": with_p4,
        "universe_sha256": universe_sha256(rows), "feature_table_sha256": feature_table_sha256(rows),
        "n_rows_total": len(rows), "n_rows_by_source": dict(sorted(counts.items())), "n_positive_label": sum(y), "p1b": P1B_EXCLUDED,
        "label": "1 iff FILLED and pressure net (haircut, both fees) > 0, else 0 (MISS = 0); cell k6 tp50_sl30 exit lag 2, 0.05 SOL",
        "learner": {"fn": "tools.exp015_screen.fit_cfg", "min_data_in_leaf": MDL, "num_leaves": fz.LGB_PARAMS["num_leaves"], "learning_rate": fz.LGB_PARAMS["learning_rate"],
                    "rounds": fz.LGB_PARAMS["rounds"], "feature_fraction": 0.9, "bagging_fraction": 0.9, "bagging_freq": 1, "scale_pos_weight": "neg/pos",
                    "seed": fz.SEED, "deterministic": True, "num_threads": 1, "force_row_wise": True},
        "models": {},
    }
    for arm, names in (("rug", FEATURES_RUG), ("control", FEATURES_CONTROL)):
        mf, md5f, ff = FREEZE_FILES[arm]
        with feature_names(names):
            model = e15.fit_cfg([arm_vector(r, arm) for r in rows], y, MDL)
        model.save_model(str(out_dir / mf))
        md5 = hashlib.md5((out_dir / mf).read_bytes()).hexdigest()
        (out_dir / md5f).write_text(md5 + "\n", encoding="utf-8")
        (out_dir / ff).write_text(json.dumps({"schema": "exp021_features_v1", "arm": arm, "n_features": len(names), "feature_names": list(names),
                                              "rug_extra": RUG_EXTRA if arm == "rug" else []}, indent=2) + "\n", encoding="utf-8")
        manifest["models"][arm] = {"model_file": mf, "model_md5": md5, "n_features": len(names)}
    (out_dir / FREEZE_MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def freeze_main(args: argparse.Namespace) -> int:
    """`--freeze OUT_DIR`: guards (V-map pin required), the tape pass, the screen's own loader and table limits, then `freeze_models`. No lock, no
    tries line. The V constancy file is not needed to train; it gates the block read (Part 1 pre-registration section 7)."""
    out_dir = args.freeze
    try:
        e16.refuse_extra_reserved(args)
        g = run_guards(args)
        e16.set_process_workers(args)
        gs = e16.git_state()
        if gs["dirty_tools"]:
            raise Refused("tools/ is dirty (uncommitted change): the freeze records one clean head and refuses otherwise")
        vmap_raw = e16.load_pinned_vmap(args.vmap)
        fallback = {k: int(v) for k, v in json.loads(args.v_fallback_json.read_text()).items()} if args.v_fallback_json else {}
        vmap = rug.merge_v_map(vmap_raw, fallback)
        results, _pp = collect(args, g, vmap)
        cells = [c for r in results for c in r["cells"]]
        try:
            oof_all = load_oof(args.artifact_dir)
            oof, oof_days = oof_all[0], oof_all[3]
        except SystemExit as exc:
            raise Refused(str(exc)) from None
        sel = e16.frozen_flags(cells, oof, args.artifact_dir)
        enforce_limits(results, oof, e16.oof_without_cell(results, oof, oof_days, p1_kept_dates()))
        table = build_table(cells, sel)
        enforce_table_limits(table_counts(table, g["with_p4"]), g["with_p4"])
        m = freeze_models(table, out_dir, head=gs["head"], vmap_sha256=g["vmap_sha256"], with_p4=g["with_p4"])
    except (Refused, rug.PoolAttributionRefusal, e16.SimulationError) as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"refusing: unexpected {type(exc).__name__} in the freeze (message withheld)", file=sys.stderr)
        return 2
    print(json.dumps({"frozen": str(out_dir), "models": m["models"], "n_rows_by_source": m["n_rows_by_source"]}, indent=2))
    return 0


def confirm_main(args: argparse.Namespace | None) -> int:
    """`--confirm`: the one fresh-block read. NOT IMPLEMENTED in this PR (Part 1 ships the freeze and the pre-registration).
    TODO (separate PR, after the model-md5 amendment and the Part 1 merge): implement per EXP/EXP-021-part1-prereg.md section 9. Until then it refuses
    unconditionally, before any path is opened, so no outcome row can be read and no tries line can be written."""
    print("refusing: --confirm is a stub. The block read is not implemented: it needs the merged model-md5 amendment and the merged Part 1 "
          "(EXP/EXP-021-part1-prereg.md section 9). No path was opened.", file=sys.stderr)
    return 2


# --- CLI ----------------------------------------------------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    """EXP-016's parser (same roots, V map, constancy and --max-workers arguments), with this module's description."""
    ap = e16._parser()
    ap.description = __doc__
    ap.formatter_class = argparse.RawDescriptionHelpFormatter
    for a in ap._actions:
        if a.dest == "p1_oracle_live_dir":  # P1B: required in EXP-016, REFUSED here (Amendment 3(b))
            a.required, a.default = False, None
            a.help = "not accepted: P1B (the Oracle live tape) is excluded from EXP-021 (Amendment 3). The tool refuses it if given."
    for a in ap._actions:
        if a.dest == "out_dir":  # required for the screen and --precount; --freeze carries its own OUT_DIR
            a.required, a.default = False, None
    ap.add_argument("--freeze", type=Path, default=None, metavar="OUT_DIR", help="Part 1: train RUG and CONTROL once on ALL exploration rows (P1A, P1C, P2, P3, P4) and write the artifacts. No tries line, no lock. Needs the V-map pin.")
    ap.add_argument("--confirm", action="store_true", help="the one fresh-block read: NOT IMPLEMENTED (refuses; see the Part 1 pre-registration)")
    for a in ap._actions:
        if a.dest == "max_workers":
            a.help = "fork count of the tape pass (EXP-016 design: indexed history, forked cells; ~57 GB at P2 with 4). Default 4, cap 8. The fits are serial."
    return ap


def collect(args: argparse.Namespace, g: Mapping[str, Any], vmap: Mapping[str, int | None]) -> tuple[list[dict[str, Any]], dict[str, list[int]]]:
    """ONE tape pass per source, exactly EXP-016's (`e16.process_source`), rows freed after each source."""
    results: list[dict[str, Any]] = []
    pool_print_ms: dict[str, list[int]] = {}
    for tag, block, hours_fn, pool_hours, mig_roots in build_sources(g):
        src = e16.load_source_data(tag, block, hours_fn, pool_hours, mig_roots, progress_every_hour=True)
        prof = censoring_profile(src, pool_hours)  # outcome-blind counts, taken before the source is processed and freed
        results.append({**e16.process_source(src, vmap, e16.canonical_pool_str), "censoring": prof})
        for p_, ts_ in e16.pool_print_times(src, rug.migration_pool_map(src.migrations.values())).items():
            pool_print_ms.setdefault(p_, []).extend(ts_)
        del src
        gc.collect()
        e16.progress(f"{tag}: source done, rows freed, cells kept={len(results[-1]['cells'])}")
    return results, pool_print_ms


def precount(args: argparse.Namespace) -> int:
    """`--precount`: the guards and tape pass of the real run, then ONLY counts (EXP-016's counts, plus this tool's table counts and its pre-declared
    refusals as would-refuse). No lock, no tries line, no label, no P&L. JSON to stdout and OUT_DIR/precount.json."""
    try:
        e16.progress("exp021 precount: guards start")
        e16.refuse_extra_reserved(args)
        g = run_guards(args, pin_required=False)
        e16.set_process_workers(args)
        if args.vmap and re.fullmatch(r"[0-9a-f]{64}", e16.VMAP_EXP016_SHA256):
            vmap_raw, vmap_note = e16.load_pinned_vmap(args.vmap), "pinned"
        else:
            from tools.pumpswap_virtual import load_map

            vmap_raw, vmap_note = load_map(Path(args.vmap)), "UNPINNED (precount only)"
        fallback = {k: int(v) for k, v in json.loads(args.v_fallback_json.read_text()).items()} if args.v_fallback_json else {}
        closed = set(json.loads(args.closed_pools_json.read_text())) if args.closed_pools_json else set()
        vmap = rug.merge_v_map(vmap_raw, fallback)
        results, _pp = collect(args, g, vmap)
    except (Refused, rug.PoolAttributionRefusal, e16.SimulationError) as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"refusing: unexpected {type(exc).__name__} in the precount (message withheld)", file=sys.stderr)
        return 2
    cells = [c for r in results for c in r["cells"]]
    oof, oof_days, sel = None, None, [False] * len(cells)
    try:
        oof_all = load_oof(args.artifact_dir)
        oof, oof_days = oof_all[0], oof_all[3]
        sel = e16.frozen_flags(cells, oof, args.artifact_dir)
    except (SystemExit, Exception):  # noqa: BLE001
        oof = None
    table = build_table(cells, sel)
    counts = table_counts(table, g["with_p4"])
    oof_cover = e16.oof_without_cell(results, oof, oof_days, p1_kept_dates())
    would = check_limits(results, oof, oof_cover) + check_table_limits(counts, g["with_p4"])
    if oof is None:
        would.append("P1: stored OOF scores could not be loaded")
    rec = {"mode": "precount", "tool": TOOL, "p1b": P1B_EXCLUDED, "censoring": {r_["tag"]: r_.get("censoring") for r_ in results}, "vmap": vmap_note, "sources": {r["tag"]: e16.source_counts(r, vmap) for r in results}, "table_counts": counts,
           "universe_sha256": universe_sha256(table), "feature_table_sha256": feature_table_sha256(table), "frozen_selection_available": oof is not None, "would_refuse": would,
           "pre_started": e16._counts_only(e16.pre_started_counts(results, {c["mint"]: s for c, s in zip(cells, sel)}, vmap_raw, closed))}
    text = json.dumps(rec, indent=2, default=str, sort_keys=True)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "precount.json").write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    from tools.exp012_exit_sensitivity import resolve_tries_path

    if "--confirm" in (sys.argv[1:] if argv is None else list(argv)):  # the stub refuses before argparse, so it needs no path argument
        return confirm_main(None)
    args = _parser().parse_args(argv)
    if args.emit_constancy_sample:
        print("refusing: --emit-constancy-sample is not offered here (EXP-016's constancy file is the input; its guards read P1B)", file=sys.stderr)
        return 2
    if args.confirm:
        return confirm_main(args)
    if args.freeze is not None:
        return freeze_main(args)
    if args.out_dir is None:
        print("refusing: --out-dir is required", file=sys.stderr)
        return 2
    if args.precount:
        return precount(args)
    if args.v_constancy_json is None:
        print("refusing: --v-constancy-json is required (or use --emit-constancy-sample)", file=sys.stderr)
        return 2
    tries_path = resolve_tries_path(args.tries_log)
    canonical = Path(args.canonical_tries).resolve()
    out_dir = args.out_dir
    try:
        e16.progress("exp021 guards start")
        g = run_guards(args)
        e16.set_process_workers(args)
        check_no_prior_tries(tries_path, canonical)
        try:
            e15.check_run_lock(out_dir)
        except e15.Refused as exc:
            raise Refused(str(exc)) from None
        gs = e16.git_state()
        if gs["dirty_tools"]:
            raise Refused("tools/ is dirty (uncommitted change): the run records one clean head and refuses otherwise")
        vmap_raw = e16.load_pinned_vmap(args.vmap)
        fallback = {k: int(v) for k, v in json.loads(args.v_fallback_json.read_text()).items()} if args.v_fallback_json else {}
        closed = set(json.loads(args.closed_pools_json.read_text())) if args.closed_pools_json else set()
        vmap = rug.merge_v_map(vmap_raw, fallback)
        constancy_samples = json.loads(args.v_constancy_json.read_text())
        constancy = e16.check_v_constancy(constancy_samples, vmap_raw)
        input_shas = {k: (hashlib.sha256(Path(f).read_bytes()).hexdigest() if f else None)
                      for k, f in (("v_fallback_json_sha256", args.v_fallback_json), ("v_constancy_json_sha256", args.v_constancy_json), ("closed_pools_json_sha256", args.closed_pools_json))}
        print(f"input files: {json.dumps(input_shas)}", file=sys.stderr)
        if args.guards_only:
            print("guards OK (no outcome row was read)", file=sys.stderr)
            return 0
    except (Refused, rug.PoolAttributionRefusal) as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - before any row: type only
        print(f"refusing: unexpected {type(exc).__name__} in the guards (message withheld)", file=sys.stderr)
        return 2
    with_p4 = g["with_p4"]
    head, ahash = gs["head"], e15.args_hash(args)
    t0 = time.time()
    status, locked, started = "aborted_after_read", False, False

    def _on_sigterm(signum: int, frame: Any) -> None:
        raise SystemExit(128 + signum)

    prev = signal.signal(signal.SIGTERM, _on_sigterm)
    try:
        results, _pp = collect(args, g, vmap)
        cells = [c for r in results for c in r["cells"]]
        try:
            oof_all = load_oof(args.artifact_dir)
            oof, oof_days = oof_all[0], oof_all[3]
        except SystemExit as exc:
            raise Refused(str(exc)) from None
        sel = e16.frozen_flags(cells, oof, args.artifact_dir)
        pre = e16.pre_started_counts(results, {c["mint"]: s for c, s in zip(cells, sel)}, vmap_raw, closed)
        oof_cover = e16.oof_without_cell(results, oof, oof_days, p1_kept_dates())
        pre["p1_oof_without_cell"] = oof_cover
        enforce_limits(results, oof, oof_cover)
        pre["v_coverage"] = e16.v_coverage([c["pool"] for c in cells if c.get("pool")], vmap_raw)
        pre["v_constancy"] = constancy
        e16.check_constancy_sample(constancy_samples, e16.readable_pools([c["pool"] for c in cells if c.get("block") == "P2" and c.get("pool")], vmap_raw))
        table = build_table(cells, sel)
        counts = table_counts(table, with_p4)
        enforce_table_limits(counts, with_p4)  # before `started`
        usha, fsha = universe_sha256(table), feature_table_sha256(table)
        prior = e16.prior_tries_per_pool(canonical, with_p4)
        prior_exp = e16.prior_tries_per_experiment(canonical, with_p4)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / OUT_UNIVERSE).write_text(usha + "\n")
        (out_dir / OUT_FEATURES).write_text(fsha + "\n")
        try:
            e15.take_lock(out_dir, head, ahash)
        except FileExistsError:
            print(f"refusing: {out_dir / e15.OUT_LOCK} appeared (concurrent run)", file=sys.stderr)
            return 2
        locked = True
        check_no_prior_tries(tries_path, canonical)  # re-checked under the lock, right before the spend point
        extra = {"universe_sha256": usha, "feature_table_sha256": fsha, "prior_tries_per_pool": prior, "prior_tries_per_experiment": prior_exp,
                 "with_p4": with_p4, "vmap_sha256": g["vmap_sha256"], **input_shas}
        log_all(out_dir, tries_path, canonical, "started", with_p4, extra)  # the spend point
        started = True
        base = {"schema": SCHEMA, "banner": BANNER, "first_line": first_line(with_p4), "with_p4": with_p4, "p1b": P1B_EXCLUDED, "censoring": {r_["tag"]: r_.get("censoring") for r_ in results}, "pre_started": pre, "table_counts": counts, "prior_tries": prior,
                "universe_sha256": usha, "feature_table_sha256": fsha, "git_head": head, "args_hash": ahash}
        write_report(out_dir, {**base, "decision": {"outcome": "table written; folds not yet scored"}, "partial": True})
        res = run_screen(table, with_p4)
        rep = {**base, "folds": res["folds"], "bars": res["bars"], "decision": res["decision"], "report_only": res["report_only"], "wall_s": time.time() - t0, "partial": False}
        write_report(out_dir, rep)
        status = "completed"
        print(render_md(rep))
        return 0
    except (Refused, rug.PoolAttributionRefusal, e16.SimulationError) as exc:
        status = "refused_after_read" if started else "aborted_after_read"
        print(f"refusing: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        if started:
            raise
        print(f"refusing: unexpected {type(exc).__name__} before `started` (message withheld: it may carry outcome numbers)", file=sys.stderr)
        return 2
    finally:
        signal.signal(signal.SIGTERM, prev)
        if started:
            log_all(out_dir, tries_path, canonical, status, with_p4, {})
        if locked:
            e15.write_record(out_dir, status, started, {CONFIG_KEY: status})


if __name__ == "__main__":
    raise SystemExit(main())
