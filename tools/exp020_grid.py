#!/usr/bin/env python3
"""EXP-020 size x entry-slot grid: REPORT-ONLY measurement of the frozen EXP-012 selection at k in {2,3,4,6} x stake {0.25,0.5,1.0} SOL (exit lag 2,
V pin 0909, haircut, 505k per side, both fail models) on the 27 non-P1 dates, plus (6, 0.05) as the equivalence control. No edge claim, no decision.
Plan: EXP/EXP-020-size-latency-grid-plan.md. Built on tools/exp017_resim.py (re-sim machinery) and tools/exp017_screen.py (sized-cache checks).

  --precount   outcome-blind counts (selected mints, combos, cells). Reads no net, no tape.
  --resim      the SEALED grid cache: <out-dir>/grid_cache/v_P*.rows.jsonl + GRID.manifest.sha256 (one V pass per source, <= 4 workers, selected mints only).
               Not a try, not a read; prints counts and hashes only.
  --bound end  PESSIMISTIC re-run (report-only, plan amendment 2026-10-07 "end bound"): entry AND exit fill after every trade in the landing slot
               (ENTRY_BOUND="end" in exploration_exits and exploration_entry_model, set before the spawn workers start). Default `start`: nothing changes.
               The end grid has its own pin line `GRID_END_MANIFEST_SHA256 = <sha>`; the start pin is never accepted for it.
  --combos reduced   k {2,3,6} x {0.25, 0.5} SOL plus the (6, 0.05) control (default `all`).
  --report     needs a `GRID_MANIFEST_SHA256 = <sha>` line in the plan; re-hashes the cache, runs the equivalence check against the EXP-015 cache, takes a
               lock, writes ONE tries line (exp020_grid) to the ops log and the canonical log, then grid.json and grid.md in --report-dir.

Run (mal-research-0, MiScusi job, <= 48 GB, 4 CPUs, nice): see the plan section 7.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

import tools.exp015_screen as e15
import tools.exp017_resim as r17
import tools.exp017_screen as x17
from tools import mal_result

TOOL = "tools.exp020_grid"
SCHEMA = "exp020_grid_v1"
KEY = "exp020_grid"
END_KEY = "exp020_grid_end"  # the end-bound re-run is its own report-only try; the start line never blocks it and it never blocks the start line
REPO_ROOT = Path(__file__).resolve().parent.parent
PLAN = REPO_ROOT / "EXP" / "EXP-020-size-latency-grid-plan.md"
LAMPORTS = 1_000_000_000
LAG = 2
KS = (2, 3, 4, 6)
SIZES_SOL = (0.25, 0.5, 1.0)
CONTROL = (6, 0.05, LAG)
GRID_COMBOS = tuple((k, s, LAG) for s in SIZES_SOL for k in KS)
CONTROL_K4 = (4, 0.05, LAG)  # second equivalence control, against the EXP-015 cache's (k=4, lag 2) cell
COMBOS = (CONTROL, CONTROL_K4, *GRID_COMBOS)  # the controls first, as EXP-017's R3
BASE_K = 6
REDUCED_KS = (2, 3, 6)
REDUCED_SIZES_SOL = (0.25, 0.5)
REDUCED_COMBOS = (CONTROL, *((k, s, LAG) for s in REDUCED_SIZES_SOL for k in REDUCED_KS))
COMBO_SETS = {"all": COMBOS, "reduced": REDUCED_COMBOS}
BOUNDS = ("start", "end")
GRID_DIR = "grid_cache"
MANIFEST_FILE = "GRID.manifest.sha256"
SELECTED_FILE = "selected_mints.json"
PIN_RE = re.compile(r"^GRID_MANIFEST_SHA256 = ([0-9a-f]{64})\s*$", re.MULTILINE)
END_PIN_RE = re.compile(r"^GRID_END_MANIFEST_SHA256 = ([0-9a-f]{64})\s*$", re.MULTILINE)  # distinct line; PIN_RE is anchored at ^ so it never matches this one
PIN_NAME = {"start": "GRID_MANIFEST_SHA256", "end": "GRID_END_MANIFEST_SHA256"}
MAX_MISSING_FRACTION = e15.V_MAX_MISSING_FRACTION
BOOT_DRAWS = 1000
BOOT_SEED = 1
LEGS = e15.LEGS
Refused = x17.Refused


def lam(sol: float) -> int:
    return int(round(sol * LAMPORTS))


def cell_key(k: int, sol: float) -> str:
    return f"k{k}_s{sol:g}"


# --- pin, manifest, meta ----------------------------------------------------------------------------------------------


def grid_pin(plan: Path = PLAN, bound: str = "start") -> str | None:
    """The single `GRID_MANIFEST_SHA256 = <sha>` line (bound start) or `GRID_END_MANIFEST_SHA256 = <sha>` line (bound end) a plan amendment adds after the
    outcome-blind --resim. Two different pins of the same kind refuse; the other bound's line is never read."""
    if bound not in BOUNDS:
        raise Refused(f"--bound {bound!r}: expected start or end")
    try:
        found = set((PIN_RE if bound == "start" else END_PIN_RE).findall(Path(plan).read_text(encoding="utf-8")))
    except OSError:
        return None
    if len(found) > 1:
        raise Refused(f"{plan} carries {len(found)} different {PIN_NAME[bound]} lines")
    return next(iter(found), None)


def apply_bound(bound: str) -> None:
    """Set the entry/exit bound in this process (the parent, BEFORE any worker starts) and, through the environment, in every spawned worker.
    `eem.ENTRY_BOUND` is a by-value import of `exploration_exits.ENTRY_BOUND`, so both are set. `start` leaves everything untouched."""
    if bound not in BOUNDS:
        raise Refused(f"--bound {bound!r}: expected start or end")
    if bound == "start":
        return
    import tools.exploration_entry_model as eem
    import tools.exploration_exits as ee

    ee.ENTRY_BOUND = eem.ENTRY_BOUND = bound
    os.environ[e15.ENV_BOUND] = bound


def check_grid_meta(grid_dir: str | Path, sel_sha: str, bound: str = "start", combos: Sequence[tuple[int, float, int]] = COMBOS) -> dict[str, Any]:
    """Per-source manifest meta: V map sha, the EXP-020 combos, selected_sha256, one common 40-hex head; the rows file hashes to its manifest."""
    heads = set()
    for src in x17.SOURCES:
        mp, rp = Path(grid_dir) / f"v_{src}.manifest.json", Path(grid_dir) / f"v_{src}.rows.jsonl"
        try:
            man = json.loads(mp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise Refused(f"grid manifest {mp} unreadable") from None
        meta = man.get("meta") or {}
        if meta.get("vmap_sha256") != e15.VMAP_0909_SHA256:
            raise Refused(f"{mp}: V map sha differs from the pin")
        # the pinned start-bound cache predates the field: a missing `bound` means start; the end cache must say "end" in every source
        if meta.get("bound", "start") != bound or (bound == "end" and "bound" not in meta):
            raise Refused(f"{mp}: meta bound is {meta.get('bound', '<absent: start>')!r}, --bound is {bound!r} (mixed or wrong-bound cache)")
        if [list(c) for c in meta.get("combos", [])] != [list(c) for c in combos]:
            raise Refused(f"{mp}: combos differ from the EXP-020 combos for this run")
        if meta.get("selected_sha256") != sel_sha:
            raise Refused(f"{mp}: selected_sha256 differs from the frozen selection re-derived from the pinned cache")
        if man.get("rows_sha256") != x17._file_sha256(rp):
            raise Refused(f"{rp}: rows sha256 differs from its manifest")
        h = str(meta.get("head", ""))
        if len(h) != 40:
            raise Refused(f"{mp}: head is not a 40-hex sha")
        heads.add(h)
    if len(heads) != 1:
        raise Refused(f"grid cache sources were built at {len(heads)} different heads")
    return {"head": next(iter(heads))}


def end_consistency(sized_rows: Mapping[str, Any], selected_mints: set[str], sel_sha: str) -> dict[str, Any]:
    """End-bound mode only: the EXP-015 cache is start-bound, so cell equivalence cannot hold. Instead: the re-simulated mint set is exactly the frozen
    selected set (same selected_sha256 as the start grid, already enforced per source by check_grid_meta) and n is identical. Counts only."""
    if set(sized_rows) != selected_mints or not selected_mints:
        raise Refused(f"end-bound internal-consistency failed: {len(sized_rows)} re-simulated mints vs {len(selected_mints)} selected (set differs)")
    return {"kind": "internal-consistency (END bound: the start-bound EXP-015 cache equivalence cannot hold)", "n": len(sized_rows), "selected_sha256": sel_sha, "same_selected_set_as_start_grid": True}


def equivalence_check_k(cache_rows: Mapping[str, Mapping[str, Any]], sized_rows: Mapping[str, Mapping[str, Any]], k: int) -> dict[str, int]:
    """As x17.equivalence_check but for the (k, lag 2, 0.05 SOL) cell: per mint, sha256 of [mint, mig_ms, features, cell] equals the EXP-015 cache's."""
    def dig(row: Mapping[str, Any]) -> str | None:
        cell = [c for c in row["cells"] if (int(c["k"]), int(c.get("lag", 0)), int(c["size"])) == (k, LAG, x17.SIZE_1X)]
        if len(cell) != 1:
            return None
        return hashlib.sha256(json.dumps([row["mint"], row["mig_ms"], row["features"], cell[0]], sort_keys=True).encode("utf-8")).hexdigest()

    match = bad = 0
    for m, r in sized_rows.items():
        a, b = dig(r), (dig(cache_rows[m]) if m in cache_rows else None)
        if a is not None and a == b:
            match += 1
        else:
            bad += 1
    if bad or not match:
        raise Refused(f"decision-equivalence proof (k={k}) failed: {match} matched, {bad} mismatched (re-sim 0.05 SOL cell vs the EXP-015 cache)")
    return {"matched": match, "mismatched": bad}


def blind_missing_by_combo(rows: Sequence[Mapping[str, Any]], cells: Mapping[str, Mapping[tuple[int, int], Mapping[str, Any]]], combos: Sequence[tuple[int, float, int]] = COMBOS) -> dict[str, int]:
    """Outcome-blind: per combo, selected non-P1 rows whose cell is absent or censored (presence and the `censored` flag only; no net field is read)."""
    out: dict[str, int] = {}
    for k, sol, _ in combos:
        c = lam(sol)
        n = 0
        for u in rows:
            cell = (cells.get(u["mint"]) or {}).get((k, c))
            n += int(cell is None or bool(cell.get("censored")))
        out[cell_key(k, sol)] = n
    return out


def check_blind_missing(rows: Sequence[Mapping[str, Any]], cells: Mapping[str, Mapping[tuple[int, int], Mapping[str, Any]]], combos: Sequence[tuple[int, float, int]] = COMBOS) -> dict[str, int]:
    """Refuse (before the tries line, so no try is burned) when any combo lacks an uncensored cell on more than 1 % of the rows."""
    if not rows:
        raise Refused("no selected non-P1 rows")
    miss = blind_missing_by_combo(rows, cells, combos)
    bad = {k: v for k, v in miss.items() if v / len(rows) > MAX_MISSING_FRACTION}
    if bad:
        raise Refused(f"cells absent or censored on > {MAX_MISSING_FRACTION:.0%} of {len(rows)} rows (blind status only): {bad}")
    return miss


def load_grid_cells(rows: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[tuple[int, int], dict[str, Any]]]:
    """mint -> {(k, size_lamports): cell} for exit lag 2 (all cells of the re-sim)."""
    out: dict[str, dict[tuple[int, int], dict[str, Any]]] = {}
    for r in rows.values():
        for c in r["cells"]:
            if int(c.get("lag", 0)) == LAG:
                out.setdefault(r["mint"], {})[(int(c["k"]), int(c["size"]))] = c
    return out


# --- statistics -------------------------------------------------------------------------------------------------------


def trade_ci(values: Sequence[float], draws: int = BOOT_DRAWS, seed: int = BOOT_SEED) -> list[float] | None:
    """CI90 of the mean, resampling trades (1,000 draws, seed 1, 5th / 95th percentile); lamports in, SOL out."""
    if not values:
        return None
    rng, n = random.Random(seed), len(values)
    means = sorted(sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(draws))
    return [e15._pct(means, 0.05) / LAMPORTS, e15._pct(means, 0.95) / LAMPORTS]


def _row_nets(rows: Sequence[Mapping[str, Any]], cells: Mapping[str, Mapping[tuple[int, int], Mapping[str, Any]]], k: int, size_l: int) -> list[tuple[Mapping[str, Any], Mapping[str, Any] | None, dict[str, float] | None]]:
    out = []
    for u in rows:
        c = (cells.get(u["mint"]) or {}).get((k, size_l))
        out.append((u, c, e15.cell_nets(c)))
    return out


def _pct_of(sol: float, v: float | None) -> float | None:
    return None if v is None else v / sol * 100.0


def best_date_stats(dates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Report-only robustness (added 2026-10-07 before any outcome read): drop the single best UTC date.
    Mirrors EXP-017 `concentration_bar` (best = max per-date total). Ties go to the earliest date. No dates: all None."""
    if not dates:
        return {"best_date": None, "best_date_sol": None, "ex_best_date_sol": None, "ex_best_date_dates_positive": None}
    best = min(dates, key=lambda d: (-d["total_sol"], d["day"]))
    total = sum(d["total_sol"] for d in dates)
    pos = sum(1 for d in dates if d["total_sol"] > 0) - (1 if best["total_sol"] > 0 else 0)
    return {"best_date": best["day"], "best_date_sol": best["total_sol"], "ex_best_date_sol": total - best["total_sol"], "ex_best_date_dates_positive": pos}


def cell_report(rows: Sequence[Mapping[str, Any]], cells: Mapping[str, Mapping[tuple[int, int], Mapping[str, Any]]], k: int, sol: float, n_dates: int) -> dict[str, Any]:
    """One grid cell on the selected non-P1 rows: n, filled, MISS share, mean SOL, mean % of stake, CI90 (trade and date-cluster), total, ex-top-3, dates positive."""
    size_l = lam(sol)
    trades: list[dict[str, Any]] = []
    missing = miss = 0
    for u, c, nets in _row_nets(rows, cells, k, size_l):
        if nets is None:
            missing += 1
            continue
        miss += int(c["status"] == e15.MISS)
        trades.append({"mint": u["mint"], "day": u["date"], "filled": bool(c["filled"]), "flat": nets["flat"], "press": nets["press"], "source": u["source"], "block": u["block"]})
    out: dict[str, Any] = {"k": k, "size_sol": sol, "n_rows": len(rows), "n_without_cell": missing, "n": len(trades), "miss_share": (miss / len(trades)) if trades else None}
    for leg in LEGS:
        st = e15.leg_stats(trades, leg)
        out[leg] = {
            "n": st["n"], "filled": st["filled"], "mean_sol": st["mean_sol"], "mean_pct_of_stake": _pct_of(sol, st["mean_sol"]),
            "ci90_trade_sol": st["ci90_sol"], "ci90_date_sol": st["ci90_date_sol"],
            "ci90_trade_pct": None if not st["ci90_sol"] else [_pct_of(sol, v) for v in st["ci90_sol"]],
            "ci90_date_pct": None if not st["ci90_date_sol"] else [_pct_of(sol, v) for v in st["ci90_date_sol"]],
            "total_sol": st["total_sol"], "ex_top3_sol": st["ex_top3_sol"],
            **best_date_stats(st["dates"]),
            "dates_positive": st["dates_positive"], "dates_with_trades": st["dates_with_trades"], "n_scope_dates": n_dates,
        }
    return out


def paired_vs_base(rows: Sequence[Mapping[str, Any]], cells: Mapping[str, Mapping[tuple[int, int], Mapping[str, Any]]], k: int, sol: float) -> dict[str, Any]:
    """x_m = net(k, size) - net(6, size) per migration, over the rows where both cells exist (the dropped count is reported)."""
    size_l = lam(sol)
    a, b = _row_nets(rows, cells, k, size_l), _row_nets(rows, cells, BASE_K, size_l)
    out: dict[str, Any] = {"k": k, "vs_k": BASE_K, "size_sol": sol}
    both = [(u, na, nb) for (u, _, na), (_, _, nb) in zip(a, b) if na is not None and nb is not None]
    out["n_pairs"], out["n_dropped"] = len(both), len(rows) - len(both)
    for leg in LEGS:
        by_date: dict[str, list[float]] = {}
        xs: list[float] = []
        for u, na, nb in both:
            x = na[leg] - nb[leg]
            xs.append(x)
            by_date.setdefault(u["date"], []).append(x)
        mean = (sum(xs) / len(xs) / LAMPORTS) if xs else None
        dci, tci = e15.date_cluster_ci(by_date), trade_ci(xs)
        out[leg] = {"mean_x_sol": mean, "mean_x_pct_of_stake": _pct_of(sol, mean), "ci90_date_sol": dci, "ci90_trade_sol": tci,
                    "ci90_date_pct": None if not dci else [_pct_of(sol, v) for v in dci], "ci90_trade_pct": None if not tci else [_pct_of(sol, v) for v in tci],
                    "total_x_sol": (sum(xs) / LAMPORTS) if xs else None}
    return out


def build_grid(rows: Sequence[Mapping[str, Any]], cells: Mapping[str, Mapping[tuple[int, int], Mapping[str, Any]]], n_dates: int, combos: Sequence[tuple[int, float, int]] = COMBOS) -> dict[str, Any]:
    """Every grid cell, the (6, 0.05) control, and the paired x of each k < 6 cell vs k = 6 at the same size. Refuses a cell with > 1 % missing rows."""
    grid: dict[str, Any] = {}
    for k, sol, _ in combos:
        rep = cell_report(rows, cells, k, sol, n_dates)
        if rows and rep["n_without_cell"] / len(rows) > MAX_MISSING_FRACTION:
            raise Refused(f"cell {cell_key(k, sol)}: {rep['n_without_cell']} of {len(rows)} rows lack an uncensored cell (> {MAX_MISSING_FRACTION:.0%})")
        grid[cell_key(k, sol)] = rep
    paired = {cell_key(k, sol): paired_vs_base(rows, cells, k, sol) for sol in SIZES_SOL for k in KS if k != BASE_K and (k, sol, LAG) in combos}
    return {"cells": grid, "paired_vs_k6": paired}


# --- rendering --------------------------------------------------------------------------------------------------------


def _f(v: Any, nd: int = 5) -> str:
    return "n/a" if v is None else (f"{v:.{nd}f}" if isinstance(v, float) else str(v))


def _r(v: Sequence[float] | None) -> str:
    return "n/a" if not v else str([round(x, 3) for x in v])


def bound_lines(bound: str) -> list[str]:
    if bound == "end":
        return ["END-of-slot bound (pessimistic): entry and exit fill after every trade in the slot (ENTRY_BOUND=end). Report-only sensitivity requested by quant-proof after the start-bound report; not a gate, not a decision. Compare with the start-bound report, do not replace it.",
                "A larger stake fills a different set of trades (see MISS share)."]
    return ["Entry is bounded at the START of the landing slot (ENTRY_BOUND=start, the most optimistic point): every cell, and every paired x vs k6, is an upper bound. A larger stake fills a different set of trades (see MISS share)."]


def render_md(rep: Mapping[str, Any]) -> str:
    L = ["# EXP-020 size x entry-slot grid (REPORT-ONLY)", "", "REPORT-ONLY. Not gate evidence; no row is a promotion read.", "",
         "No edge claim, no decision. The 27 non-P1 dates have had 100+ tries: the base book is tuned (winner's curse). k < 6 cells assume landing at k is achievable (live k p50 today is 5).",
         *bound_lines(rep.get("bound", "start")),
         "A change of operating point needs a fresh-block confirmation and live calibration (DEC-021 section 7).", "",
         f"Head `{rep['head']}`; equivalence control: {rep['equivalence']}; selected non-P1 rows {rep['n_rows']}, dates {rep['n_dates']}.", ""]
    for leg in LEGS:
        L += [f"## Cells, {leg} fail model", "", "| k | size SOL | n | filled | mean SOL | mean % stake | MISS share | CI90 trade (%) | CI90 date (%) | total SOL | ex-top3 SOL | dates + | best date | best date SOL | ex-best-date SOL | dates + ex-best |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for c in rep["grid"]["cells"].values():
            s = c[leg]
            L.append(f"| {c['k']} | {c['size_sol']:g} | {c['n']} | {s['filled']} | {_f(s['mean_sol'], 6)} | {_f(s['mean_pct_of_stake'], 3)} | {_f(c['miss_share'], 3)} | {_r(s['ci90_trade_pct'])} | "
                     f"{_r(s['ci90_date_pct'])} | {_f(s['total_sol'], 4)} | {_f(s['ex_top3_sol'], 4)} | {s['dates_positive']}/{s['n_scope_dates']} | "
                     f"{_f(s['best_date'])} | {_f(s['best_date_sol'], 4)} | {_f(s['ex_best_date_sol'], 4)} | {_f(s['ex_best_date_dates_positive'])}/{s['n_scope_dates']} |")
        L += ["", f"## Paired x = net(k) - net(6), same size, per migration, {leg}", "", "| k | size SOL | pairs | mean x SOL | mean x % stake | CI90 date (%) | CI90 trade (%) |", "|---|---|---|---|---|---|---|"]
        for p in rep["grid"]["paired_vs_k6"].values():
            s = p[leg]
            L.append(f"| {p['k']} | {p['size_sol']:g} | {p['n_pairs']} | {_f(s['mean_x_sol'], 6)} | {_f(s['mean_x_pct_of_stake'], 3)} | {_r(s['ci90_date_pct'])} | {_r(s['ci90_trade_pct'])} |")
        L.append("")
    return "\n".join(L) + "\n"


# --- tries ------------------------------------------------------------------------------------------------------------


def key_for(bound: str) -> str:
    return END_KEY if bound == "end" else KEY


def prior_lines(log: Path, bound: str = "start") -> list[dict[str, Any]]:
    out = []
    if Path(log).is_file():
        for line in Path(log).read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            key = str((rec.get("config") or {}).get("key", ""))
            if (key == END_KEY) if bound == "end" else (key == KEY or (rec.get("tool") == TOOL and key != END_KEY)):
                out.append(rec)
    return out


def check_no_prior(*logs: Path, bound: str = "start") -> None:
    seen: set[Path] = set()
    for lg in logs:
        r = Path(lg).resolve()
        if r in seen:
            continue
        seen.add(r)
        if prior_lines(r, bound):
            raise Refused(f"an earlier {key_for(bound)} line is in {r}: a second report run is refused")


def log_try(logs: Sequence[Path], report_dir: Path, bound: str = "start") -> dict[str, Any]:
    """ONE line per log (ops and canonical), a measurement of an execution parameter. Data blocks: the three non-P1 pools' counted windows."""
    info: dict[str, Any] = {}
    seen: set[Path] = set()
    blocks = [{"start_hour": e15.BLOCKS[b][0], "end_hour_exclusive": e15.BLOCKS[b][1], "host": "mal-research-0", "ledger_owner": "EXP-020 measurement (execution parameter, non-P1 pools)"} for b in ("P2", "P3", "P4")]
    for lg in logs:
        if Path(lg).resolve() in seen:
            continue
        seen.add(Path(lg).resolve())
        r = mal_result.append_try(lg, tool=TOOL, config={"key": key_for(bound), "bound": bound, "experiment": "EXP-020", "status": "report", "measurement": "execution parameter (stake size x entry slot), report-only",
                                                           "ks": list(KS), "sizes_sol": list(SIZES_SOL), "exit_lag": LAG, "fee_lamports": e15.FEE, "pricing": "V"},
                                  data_blocks=blocks, result_path=report_dir / "grid.json", role="exploration")
        info = info or r
    return info


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, default=str, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# --- modes ------------------------------------------------------------------------------------------------------------


def non_p1_selected(universe: Sequence[Mapping[str, Any]], scores: Sequence[float]) -> list[Mapping[str, Any]]:
    return [u for u, s in zip(universe, scores) if s >= x17.THR90 and u["block"] != "P1"]


def precount(sel: Sequence[str], by_src: Mapping[str, int], combos: Sequence[tuple[int, float, int]] = COMBOS) -> dict[str, Any]:
    non_p1 = sum(v for k, v in by_src.items() if not k.startswith("P1"))
    return {"mode": "precount", "outcome_blind": True, "n_selected": len(sel), "n_selected_non_p1": non_p1, "by_source": dict(by_src),
            "combos": [list(c) for c in combos], "n_combos": len(combos), "n_cells_to_simulate": len(sel) * len(combos),
            "n_dates_non_p1": len(e15.non_p1_dates(True)), "max_workers": r17.WORKERS_CAP}


def _parser() -> Any:
    ap = r17._parser()
    ap.add_argument("--resim", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--bound", choices=BOUNDS, default="start", help="entry AND exit fill bound; `end` is the pessimistic report-only re-run (own pin line GRID_END_MANIFEST_SHA256)")
    ap.add_argument("--combos", choices=sorted(COMBO_SETS), default="all", help="`reduced` = k {2,3,6} x {0.25, 0.5} SOL + the (6, 0.05) control")
    ap.add_argument("--report-dir", type=Path, default=None, help="--report: output dir for grid.json / grid.md (default <out-dir>/report)")
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if sum(map(bool, (args.precount, args.resim, args.report))) != 1:
        print("refusing: give exactly one of --precount, --resim, --report", file=sys.stderr)
        return 2
    out_dir: Path = args.out_dir
    art = args.artifact_dir if args.artifact_dir != e15.DEFAULT_ARTIFACT_DIR else None
    try:
        if args.precount:
            sel, by_src = r17.selected_mints(args.scratch, art)
            print(json.dumps({**precount(sel, by_src, COMBO_SETS[args.combos]), "bound": args.bound}, indent=2))
            return 0
        if args.resim:
            return resim(args, out_dir, art)
        return report(args, out_dir, art)
    except (Refused, e15.Refused) as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 2


def resim(args: Any, out_dir: Path, art: Path | None) -> int:
    if os.environ.get(e15.ENV_COMBOS) or os.environ.get(e15.ENV_SELECTED) or os.environ.get(e15.ENV_BOUND):
        raise Refused("the MAL_EXP015_* / MAL_EXP020_BOUND env overrides are set: unset them (this mode sets them itself)")
    combos = COMBO_SETS[args.combos]
    sel, _ = r17.selected_mints(args.scratch, art)
    g = e15.run_guards(args)
    if g["g4"] is None:
        raise Refused("--p4-view-dir is required: the grid cache covers all six sources")
    if (out_dir / GRID_DIR).exists() or (out_dir / MANIFEST_FILE).exists() or (out_dir / r17.SIZED_DIR).exists():
        raise Refused(f"{out_dir} already holds a sized/grid cache: a second re-sim is refused")
    if args.guards_only:
        print(json.dumps({"guards": "ok", "n_selected": len(sel)}))
        return 0
    head = e15.git_state()["head"]
    e15.check_run_lock(out_dir)
    e15.take_lock(out_dir, head, "exp020-resim")
    apply_bound(args.bound)  # BEFORE run_pass starts any worker: the parent's globals (in-process path) and the env (spawn workers)
    sel_path = out_dir / SELECTED_FILE
    sel_path.write_text(json.dumps(sel) + "\n", encoding="utf-8")
    status = "aborted"
    try:
        # exp017_resim.run_pass writes <out-dir>/sized_cache; EXP-020 renames it to grid_cache so the two caches can never be confused
        r17.run_pass(args, g, sel_path, out_dir, head, combos=combos, extra_meta={"bound": args.bound})
        (out_dir / r17.SIZED_DIR).rename(out_dir / GRID_DIR)
        for f in sorted((out_dir / GRID_DIR).glob("v_P*")):
            os.chmod(f, 0o400)
        lines, sha = x17.manifest(out_dir / GRID_DIR, x17.SIZED_PATTERNS)
        (out_dir / MANIFEST_FILE).write_text(sha + "\n", encoding="utf-8")
        print(json.dumps({"grid_manifest_sha256": sha, "n_files": len(lines), "bound": args.bound, "note": f"pin it with a plan amendment line {PIN_NAME[args.bound]} = <sha>; nets are never printed"}))
        status = "completed"
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"aborted: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    finally:
        e15.write_record(out_dir, status, False, {"resim": status})


def report(args: Any, out_dir: Path, art: Path | None) -> int:
    if not args.tries_log or not Path(args.tries_log).is_absolute():
        raise Refused("--report needs an absolute --tries-log (e.g. /data/mal/ops/tries-exp020-grid.jsonl)")
    ops, canonical = Path(args.tries_log), Path(args.canonical_tries).resolve()
    x17.check_canonical_tries(canonical)
    check_no_prior(ops, canonical, bound=args.bound)
    bound, combos = args.bound, COMBO_SETS[args.combos]
    pin = grid_pin(PLAN, bound)
    if pin is None:
        raise Refused(f"no {PIN_NAME[bound]} pin in the plan: an amendment must pin the grid cache before --report")
    grid_dir = out_dir / GRID_DIR
    e15.refuse_reserved(grid_dir, "grid cache")
    x17.check_manifests(args.scratch)
    x17.check_cache_heads(args.scratch)
    _, sha = x17.manifest(grid_dir, x17.SIZED_PATTERNS)
    if sha != pin:
        raise Refused(f"grid cache manifest {sha} != pinned {pin}")
    uni, _ = x17.load_universe(args.scratch, blind=True)
    scores = x17.frozen_scores(uni, art)
    sel_sha = x17.selected_sha256(uni, scores)
    meta = check_grid_meta(grid_dir, sel_sha, bound, combos)
    sized_rows = x17.load_sized_rows(grid_dir)
    if bound == "end":
        eq = end_consistency(sized_rows, {u["mint"] for u, s in zip(uni, scores) if s >= x17.THR90}, sel_sha)
        print(f"end-bound check (internal consistency, not the EXP-015 equivalence): {json.dumps(eq)}", file=sys.stderr, flush=True)
    else:
        cache_rows = {r["mint"]: r for src in x17.SOURCES for r in x17.read_cache_rows(Path(args.scratch) / "cache" / f"v_{src}.rows.jsonl", blind=False)}
        eq = x17.equivalence_check(cache_rows, sized_rows)
        eq["k4"] = equivalence_check_k(cache_rows, sized_rows, 4)
        del cache_rows
    rows = non_p1_selected(uni, scores)
    cells = load_grid_cells(sized_rows)
    blind_missing = check_blind_missing(rows, cells, combos)  # before the tries line: a refusal here burns no try
    report_dir = args.report_dir or (out_dir / "report")
    head = e15.git_state()["head"]
    check_no_prior(ops, canonical, bound=args.bound)
    e15.check_run_lock(report_dir)
    e15.take_lock(report_dir, head, hashlib.sha256(json.dumps(sorted(vars(args).items(), key=lambda kv: kv[0]), default=str).encode()).hexdigest())
    t0, status, started = time.time(), "aborted_after_read", False
    try:
        info = log_try([ops, canonical], report_dir, args.bound)  # the one tries line, written at the spend point (before any net is evaluated)
        started = True
        n_dates = len(e15.non_p1_dates(True))
        rep = {"schema": SCHEMA, "head": head, "grid_head": meta["head"], "equivalence": eq, "blind_missing_by_combo": blind_missing, "n_rows": len(rows), "n_dates": n_dates, "grid": build_grid(rows, cells, n_dates, combos), "bound": bound,
               "try": info, "report_only": True, "runtime_s": time.time() - t0}
        write_json(report_dir / "grid.json", rep)
        (report_dir / "grid.md").write_text(render_md(rep), encoding="utf-8")
        status = "completed"
        print(json.dumps({"report": "written", "dir": str(report_dir), "equivalence": eq}))
        return 0
    except Exception as exc:  # noqa: BLE001 - the try is spent once logged
        print(f"aborted after the tries line: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    finally:
        e15.write_record(report_dir, status, started, {key_for(args.bound): status})


if __name__ == "__main__":
    raise SystemExit(main())
