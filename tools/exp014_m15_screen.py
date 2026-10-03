"""EXP-014 PR3: the migration + 15 min PumpSwap selector screen (plan:
EXP/EXP-014-mig15-pumpswap-selector-plan.md, "Screen", "Multiplicity", Amendments 1-2).
EXPLORATION, NOT EVIDENCE: no edge claim.

One run, one verdict. EXP-013's item functions are IMPORTED from tools.exp013_grad_screen (bars via the
gate's own `book_stats`, once per fail model; items 1-3, 4a/4b, 5; Jaccard; Pearson) and not edited, so
EXP-013's single screen run is unaffected. New here: the 27-feature model (tools.exp014_m15_model), the
`excluded_by_time` removal, item 1x, the 4b precondition, items 6(a)/6(b) (gating) and 6(c) (report-only,
by migration day), and the EXP-014 guards:

  - real data and every VIEW.sha256 mtime not after 2026-10-05T12:00:00Z (CUTOFF);
  - tries-log key exp014_m15: any prior entry refuses; a "started" line is written before any fit;
  - once-only ledger /data/mal/exp014-m15/SCREEN_RUNS.jsonl;
  - ARTIFACTS/exp012/read is refused; no CLI path overrides; the out-dir must not exist.

Pass: every one of items 1, 2, 3, 1x, 4a, 4b, 5, 6a, 6b under BOTH fail models (6a has no fail model).
With fewer than 6 August days in the manifest the screen exits NOT_DECIDABLE before fitting anything.

  python -m tools.exp014_m15_screen --table-run-dir D --view-manifest M.json --out-dir O [--n-jobs N]
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import tools.exp013_grad_screen as sc
import tools.exp014_m15_model as mm
from tools import mal_result
from tools.paper_attention_promote import _utc_day

TOOL = "exp014_m15"
LAMPORTS = sc.LAMPORTS
LEGS = sc.LEGS
CUTOFF = mm.REAL_DATA_CUTOFF
DEFAULT_LEDGER_DIR = Path("/data/mal/exp014-m15")
DEFAULT_EXP012_DIR = sc.DEFAULT_EXP012_DIR
LEDGER_NAME = "SCREEN_RUNS.jsonl"
E12_DAYS = sc.E12_DAYS
E12_THRESHOLD, E12_N_SELECTED = sc.E12_THRESHOLD, sc.E12_N_SELECTED
JACCARD_MAX = sc.JACCARD_MAX
MIN_AUGUST_DAYS = 6
PICK_DAY_C = "2026-09-25"  # the offset-picking day inside pool C
ITEM_ORDER = ("1", "2", "3", "1x", "4a", "4b", "5", "6a", "6b")
SCREEN_POOLS = ("A", "C")  # built-in pools with hours; B is guarded but excluded (Amendment 2)
_FMT = "%Y-%m-%dT%H:%M:%SZ"
_UTC = timezone.utc


def _utc_now() -> datetime:
    return datetime.now(_UTC)


# --- guards ------------------------------------------------------------------------


def expected_views(tm: Mapping[str, Any]) -> dict[str, Any]:
    """Pools A and C (root, hours, sha), pool B (root, sha only: guarded, excluded) and the extra views."""
    runs, shas, roots = tm.get("pool_runs", {}), tm.get("view_sha256_file_sha256", {}), tm.get("roots", {})
    name = {"A": "fast", "C": "insample", "B": "live"}
    if list(tm.get("excluded_pools", [])) != ["B"] or "B" in runs:
        raise SystemExit("table manifest must list excluded_pools == ['B'] and no pool B run (Amendment 2)")
    pools: dict[str, Any] = {}
    for tag in SCREEN_POOLS:
        if tag not in runs or name[tag] not in roots or not shas.get(name[tag]):
            raise SystemExit(f"table manifest does not describe pool {tag}")
        pools[tag] = {"root": roots[name[tag]], "hours": [runs[tag][0][0], runs[tag][-1][-1]], "view_sha256_file_sha256": shas[name[tag]]}
    if "live" not in roots or not shas.get("live"):
        raise SystemExit("table manifest does not pin the pool B root and sha")
    pools["B"] = {"root": roots["live"], "view_sha256_file_sha256": shas["live"]}
    return {"pools": pools, "extra_views": list(tm.get("extra_views", []))}


def _key(v: Mapping[str, Any]) -> tuple[str, str, str, str]:
    h = v.get("hours") or ["", ""]
    return (os.path.realpath(str(v["root"])), str(h[0]), str(h[-1]), str(v["view_sha256_file_sha256"]))


def assert_view_manifest(
    vm: Mapping[str, Any], tm: Mapping[str, Any], *, check_files: bool = False, mtime_fn: Callable[[Path], float] | None = None, cutoff: datetime = CUTOFF
) -> None:
    """The pinned view manifest and the table manifest must list exactly the same views; pool B is listed with
    `excluded: true` and no hours. Every view records `view_sha256_mtime_utc` not after the cutoff; check_files
    (the real run) re-hashes each VIEW.sha256 and re-reads its mtime."""
    exp = expected_views(tm)
    pinned = vm.get("pools", {})
    for tag in (*SCREEN_POOLS, "B"):
        if tag not in pinned:
            raise SystemExit(f"view manifest does not list pool {tag}")
    if pinned["B"].get("excluded") is not True or pinned["B"].get("hours"):
        raise SystemExit("view manifest must list pool B as excluded: true with no hours")
    for tag in SCREEN_POOLS:
        if _key(pinned[tag]) != _key(exp["pools"][tag]):
            raise SystemExit(f"view manifest pool {tag} {_key(pinned[tag])} != table manifest {_key(exp['pools'][tag])}")
    b, eb = pinned["B"], exp["pools"]["B"]
    if os.path.realpath(str(b["root"])) != os.path.realpath(str(eb["root"])) or b["view_sha256_file_sha256"] != eb["view_sha256_file_sha256"]:
        raise SystemExit("view manifest pool B root/sha != table manifest")
    for v in [*pinned.values(), *vm.get("extra_views", [])]:
        if "view_sha256_mtime_utc" not in v:
            raise SystemExit(f"view manifest view {v.get('root')} does not record view_sha256_mtime_utc")
        if datetime.strptime(v["view_sha256_mtime_utc"], _FMT).replace(tzinfo=_UTC) > cutoff:
            raise SystemExit(f"view manifest view {v['root']}: VIEW.sha256 mtime {v['view_sha256_mtime_utc']} is after {cutoff.strftime(_FMT)}")
    extra_pinned = [_key(v) for v in vm.get("extra_views", [])]
    if len(set(extra_pinned)) != len(extra_pinned):
        raise SystemExit("view manifest lists an extra view twice")
    extra_table = {_key(v) for v in exp["extra_views"]}
    if extra_table - set(extra_pinned):
        raise SystemExit(f"table view(s) not listed in the view manifest (or sha mismatch): {sorted(extra_table - set(extra_pinned))}")
    if set(extra_pinned) - extra_table:
        raise SystemExit(f"view manifest lists view(s) the table did not read: {sorted(set(extra_pinned) - extra_table)}")
    if check_files:
        mt = mtime_fn or (lambda p: p.stat().st_mtime)
        for v in [*exp["pools"].values(), *exp["extra_views"]]:
            f = Path(os.path.realpath(str(v["root"]))) / "VIEW.sha256"
            if not f.is_file():
                raise SystemExit(f"{f} missing")
            if sc._sha256_file(f) != v["view_sha256_file_sha256"]:
                raise SystemExit(f"{f} no longer hashes to the pinned sha256")
            if datetime.fromtimestamp(mt(f), _UTC) > cutoff:
                raise SystemExit(f"{f} was written after {cutoff.strftime(_FMT)}: a view built after the cutoff is not allowed")


def assert_no_prior_try(tries_log: Path | str | None) -> None:
    n = sc.count_tries(tries_log, TOOL)
    if n:
        raise SystemExit(f"tries log already holds {n} {TOOL} entr{'y' if n == 1 else 'ies'}: the screen runs once")


def ledger_path(ledger_dir: Path | str) -> Path:
    return Path(ledger_dir) / LEDGER_NAME


def assert_ledger_empty(ledger_dir: Path | str) -> None:
    p = ledger_path(ledger_dir)
    if p.exists() and TOOL in p.read_text(encoding="utf-8"):
        raise SystemExit(f"{p} already holds a {TOOL} line: the screen runs once")


def _ledger_write(ledger_dir: Path | str, event: str, info: Mapping[str, Any], *, once: bool) -> None:
    p = ledger_path(ledger_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            if once:
                fh.seek(0)
                for x in fh.read().splitlines():
                    if x.strip():
                        try:
                            prior = json.loads(x)
                        except json.JSONDecodeError:
                            prior = {"tool": TOOL}  # a damaged ledger counts as a prior run
                        if prior.get("tool") == TOOL:
                            raise SystemExit(f"{p} already holds a {TOOL} line: the screen runs once")
            fh.seek(0, os.SEEK_END)
            fh.write(json.dumps({"tool": TOOL, "event": event, "ts_utc": _utc_now().strftime(_FMT), **info}, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def ledger_start(ledger_dir: Path | str, info: Mapping[str, Any]) -> None:
    """Second lock. Under an exclusive lock: refuse if any exp014_m15 line exists, else append "started"."""
    _ledger_write(ledger_dir, "started", info, once=True)


def ledger_finish(ledger_dir: Path | str, info: Mapping[str, Any]) -> None:
    _ledger_write(ledger_dir, "finished", info, once=False)


# --- days and trades ----------------------------------------------------------------


def august_days(tm: Mapping[str, Any]) -> set[str]:
    return {d for d in sc.pool_days(tm).get("X", set()) if d.startswith("2026-08")}


def days_1x(tm: Mapping[str, Any]) -> set[str]:
    """All manifest days except pool A days and 2026-09-25 within pool C (the days used to pick the offset)."""
    pd = sc.pool_days(tm)
    drop = set(pd.get("A", set()))
    if PICK_DAY_C in pd.get("C", set()):
        drop.add(PICK_DAY_C)
    return sc.all_manifest_days(tm) - drop


def selected_trades(selected: Sequence[Mapping[str, Any]], rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Each selected mint joined to its d=4 row. day = UTC day of T; mig_day is kept for item 6. MISS rows stay in."""
    by_mint = sc.k_rows_by_mint(rows, mm.PRIMARY_K)
    out = []
    for s in selected:
        r = by_mint[s["mint"]]
        out.append(
            {"mint": s["mint"], "day": s["day"], "mig_day": r["mig_day"], "trigger_ms": int(r["trigger_ms"]), "flat": r["flat"], "press": r["press"], "filled": bool(r["filled"]),
             "edge": bool(r.get("edge_left") or r.get("edge_right")), "pool": r.get("pool")}
        )
    sc.assert_trigger_days(out)
    return out


# --- items --------------------------------------------------------------------------


def item_1x(trades: Sequence[Mapping[str, Any]], tm: Mapping[str, Any]) -> dict[str, Any]:
    days = days_1x(tm)
    sub = [t for t in trades if t["day"] in days]
    return {"item": "1x", "what": "bars 1-3 on all screen days except pool A days and 2026-09-25 (pool C)", "n_days_in_group": len(days), **sc.bars(sub, days)}


def _mean_item(item: str, what: str, vals_by_leg: Mapping[str, list[float]]) -> dict[str, Any]:
    legs = {}
    for leg in LEGS:
        v = vals_by_leg[leg]
        mean = (sum(v) / len(v) / LAMPORTS) if v else None
        legs[leg] = {"mean_sol": mean, "n": len(v), "pass": bool(mean is not None and mean > 0)}
    return {"item": item, "what": what, "legs": legs, "pass": all(legs[leg]["pass"] for leg in LEGS)}


def exp012_b(e12_sel: set[tuple[str, str]], all_rows: Sequence[Mapping[str, Any]]) -> tuple[set[str], set[str]]:
    """(B, B unrestricted): EXP-012's OOF-selected mints, B restricted to mints with an EXP-014 d=4 row in the full table."""
    unrestricted = {m for _, m in e12_sel}
    have = {r["mint"] for r in all_rows if r["entry_land_k"] == mm.PRIMARY_K}
    return unrestricted & have, unrestricted


def item_6a(trades: Sequence[Mapping[str, Any]], b: set[str], b_unrestricted: set[str], e12_days: Sequence[str] = E12_DAYS) -> dict[str, Any]:
    """A = selected mints whose migration day is one of EXP-012's 9 OOF days. J(A, B) <= 0.5; an empty union fails."""
    days = set(e12_days)
    a = {t["mint"] for t in trades if t["mig_day"] in days}
    j = sc.jaccard(a, b)
    return {"item": "6a", "what": f"mint Jaccard J(A, B) <= {JACCARD_MAX} on EXP-012's OOF days", "n_a": len(a), "n_b": len(b), "n_b_unrestricted": len(b_unrestricted), "n_both": len(a & b),
            "jaccard": j, "pass": bool(j is not None and j <= JACCARD_MAX)}


def item_6b(trades: Sequence[Mapping[str, Any]], b: set[str]) -> dict[str, Any]:
    """Selected trades on mints not in B: pooled mean > 0 under both fail models. None there fails."""
    out = [t for t in trades if t["mint"] not in b]
    it = _mean_item("6b", "selected trades on mints outside EXP-012's B: pooled mean SOL > 0", {leg: [t[leg] for t in out] for leg in LEGS})
    it["n_outside_b"] = len(out)
    return it


def ranks(x: Sequence[float]) -> list[float]:
    order = sorted(range(len(x)), key=lambda i: x[i])
    r = [0.0] * len(x)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and x[order[j + 1]] == x[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(x: Sequence[float], y: Sequence[float]) -> float | None:
    return sc.pearson(ranks(x), ranks(y)) if len(x) == len(y) else None


def item_6c(trades: Sequence[Mapping[str, Any]], e12_daily: Mapping[str, Mapping[str, float]] | None, e12_days: Sequence[str] = E12_DAYS) -> dict[str, Any]:
    """Report only. Daily SOL totals of the EXP-014 trades grouped by MIGRATION day (no trade = 0) against EXP-012's
    per-day totals, on EXP-012's 9 OOF days. None = undefined (zero variance, or too few days)."""
    days = sorted(e12_days)
    out: dict[str, Any] = {"item": "6c", "what": "daily SOL total correlation with EXP-012 (report only)", "days": days, "pearson": {}, "spearman": {}}
    if e12_daily is None:
        return out
    for leg in LEGS:
        mine = [sum(t[leg] for t in trades if t["mig_day"] == d) / LAMPORTS for d in days]
        theirs = [e12_daily[leg].get(d, 0.0) for d in days]
        out["pearson"][leg] = sc.pearson(mine, theirs)
        out["spearman"][leg] = spearman(mine, theirs)
    return out


def verdict_of(items: Sequence[Mapping[str, Any]]) -> str:
    got = {i["item"]: i["pass"] for i in items}
    if set(got) != set(ITEM_ORDER):
        return "FAIL"
    return "PASS" if all(got[k] for k in ITEM_ORDER) else "FAIL"


def evaluate(
    rows: Sequence[Mapping[str, Any]],
    tm: Mapping[str, Any],
    selected: Sequence[Mapping[str, Any]],
    fold_info: Sequence[Mapping[str, Any]],
    counts: Mapping[str, int],
    e12_sel: set[tuple[str, str]],
    e12_daily: Mapping[str, Mapping[str, float]] | None = None,
    all_rows: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Everything after selection. `rows` are the eligible rows (excluded_by_time removed), `all_rows` the full table
    (item 6's B and item 5's split). book_stats runs once per fail model for the full set. No fitting."""
    all_rows = rows if all_rows is None else all_rows
    trades = selected_trades(selected, rows)
    groups, pd, all_days = sc.day_groups(tm), sc.pool_days(tm), sc.all_manifest_days(tm)
    full = sc.bars(trades, all_days)
    b, b_unrestricted = exp012_b(e12_sel, all_rows)
    items = [
        sc.item_1(trades, all_days, full["legs"]), sc.item_2(trades, all_days, full["legs"]), sc.item_3(trades, all_days, full["legs"]),
        item_1x(trades, tm), sc.item_4a(trades, groups, pd), sc.item_4b(trades, groups, pd), sc.item_5(selected, rows, all_rows),
        item_6a(trades, b, b_unrestricted), item_6b(trades, b),
    ]
    return {
        "schema": "exp014_m15_screen_v1",
        "note": "EXPLORATION screen. Not evidence, no edge claim.",
        "verdict": verdict_of(items),
        "items": items,
        "item_6c_report_only": item_6c(trades, e12_daily),
        "gate_report_only": full,
        "n_selected": len(trades),
        "day_groups": {k: sorted(v) for k, v in groups.items()} | {"1x": sorted(days_1x(tm))},
        "edge_split_report_only": sc.edge_split(trades, sc.edge_days(tm.get("pool_runs", {})), all_days, full),
        "per_day": sc.per_day_table(rows, trades),
        "fold_info": list(fold_info),
        "fold_fraction_by_source": sc.fold_fraction_by_source(fold_info, rows),
        "counts": dict(counts),
        "slot1_reference": sc.slot1_reference(selected, rows),
    }


def not_decidable_doc(tm: Mapping[str, Any]) -> dict[str, Any]:
    n = len(august_days(tm))
    return {"schema": "exp014_m15_screen_v1", "verdict": "NOT_DECIDABLE", "items": [], "n_selected": 0, "n_august_days": n, "min_august_days": MIN_AUGUST_DAYS,
            "note": f"4b precondition not met: {n} August days in the manifest, need {MIN_AUGUST_DAYS}. Nothing was fit. The cutoff is never moved."}


def _f(v: Any) -> str:
    if v is None:
        return "n/a"
    return f"{v:+.4f}" if isinstance(v, float) else str(v)


def to_markdown(doc: Mapping[str, Any]) -> str:
    lines = ["# EXP-014 mig+15 PumpSwap screen (exploration, no edge claim)", "", f"**Verdict: {doc['verdict']}**", "", doc["note"], ""]
    if doc["verdict"] == "NOT_DECIDABLE":
        return "\n".join(lines) + "\n"
    lines += ["## Items", "", "| Item | Pass | Detail |", "| --- | --- | --- |"]
    for it in doc["items"]:
        detail = ""
        if "legs" in it:
            keys = ("n", "mean_sol", "ci90_lo_sol", "ex_top3_sol", "days_positive", "n_manifest_days")
            detail = "; ".join(f"{leg}: " + ", ".join(f"{k}={_f(v)}" for k, v in lg.items() if k in keys) for leg, lg in it["legs"].items())
        if it["item"] == "6a":
            detail = f"J={_f(it['jaccard'])}, |A|={it['n_a']}, |B|={it['n_b']}, both={it['n_both']}"
        lines.append(f"| {it['item']} {it['what']} | {'PASS' if it['pass'] else 'FAIL'} | {detail} |")
    c = doc["item_6c_report_only"]
    lines += ["", "## Item 6c (report only)", "", f"- pearson: {json.dumps(c['pearson'])}", f"- spearman: {json.dumps(c['spearman'])}"]
    lines += ["", "## Exclusions and reference", "", f"- excluded_by_time: {json.dumps(doc['excluded_by_time'])}", f"- tries log: {doc['tries_log']}",
              f"- slot+1 (reference): {json.dumps(doc['slot1_reference'])}", f"- counts: {json.dumps(doc['counts'])}"]
    return "\n".join(lines) + "\n"


def data_blocks_of(tm: Mapping[str, Any]) -> list[dict[str, str]]:
    from datetime import timedelta

    blocks = []
    for tag, runs in sorted(tm.get("pool_runs", {}).items()):
        for first, last in runs:
            end = datetime.strptime(last, "%Y-%m-%dT%H").replace(tzinfo=_UTC) + timedelta(hours=1)
            blocks.append({"start_hour": first, "end_hour_exclusive": end.strftime("%Y-%m-%dT%H"), "host": f"exp014-pool-{tag}", "ledger_owner": "EXP-014"})
    return blocks


GATE_NOTE = (
    "gate.pass_both in this file comes from mal_result.build_result and is NOT the screen verdict: it counts only days that have a trade "
    "and applies n >= 100. The screen verdict is the top-level `verdict` (bars over every manifest day, items 1-6)."
)


def run(
    table_run_dir: Path | str,
    view_manifest_path: Path | str,
    out_dir: Path | str,
    *,
    n_jobs: int = 1,
    ledger_dir: Path | str = DEFAULT_LEDGER_DIR,
    tries_log: Path | str | None = None,
    exp012_dir: Path | str = DEFAULT_EXP012_DIR,
    e12_expect: tuple[float, int] | None = (E12_THRESHOLD, E12_N_SELECTED),
    now: datetime | None = None,
    mtime_fn: Callable[[Path], float] | None = None,
    view_cutoff: datetime = CUTOFF,
    check_view_files: bool | None = None,
    command: str = "",
) -> dict[str, Any]:
    """ledger_dir, tries_log, exp012_dir, e12_expect, now, mtime_fn, view_cutoff and check_view_files are for tests
    only; the CLI never sets them."""
    t0 = time.time()
    # --- guards, all before any fit ---
    real_table = mm.assert_run_dir_allowed(table_run_dir, now=now)
    is_real = str(real_table) == mm.REAL_DATA_PREFIX or str(real_table).startswith(mm.REAL_DATA_PREFIX + "/")
    if is_real and os.path.realpath(str(ledger_dir)) != os.path.realpath(str(DEFAULT_LEDGER_DIR)):
        raise SystemExit(f"a real-data run must use the default ledger dir {DEFAULT_LEDGER_DIR}, got {ledger_dir}")
    out = sc.assert_out_dir_fresh(out_dir)
    sc.assert_exp012_dir(exp012_dir)
    rows, tm = mm.load_table(real_table, now=now)
    vm_bytes = Path(view_manifest_path).read_bytes()
    vm = json.loads(vm_bytes.decode("utf-8"))
    assert_view_manifest(vm, tm, check_files=is_real if check_view_files is None else check_view_files, mtime_fn=mtime_fn, cutoff=view_cutoff)
    tries_path = sc.resolved_tries_log(tries_log)
    assert_no_prior_try(tries_path)
    assert_ledger_empty(ledger_dir)
    e12_days, e12_sel, e12_thr = sc.exp012_selected(exp012_dir, e12_expect)
    e12_daily = sc.exp012_daily_pnl(exp012_dir)
    eligible, exclusion = mm.eligible_rows(rows)
    manifest_sha = hashlib.sha256(vm_bytes).hexdigest()
    blocks = data_blocks_of(tm)
    base_cfg = {"d": mm.PRIMARY_K, "table_md5": tm.get("table_md5"), "view_manifest_sha256": manifest_sha, "code_commit": sc._git_sha()}
    result_path = out / "result.json"
    # --- the try is used from here on, even if the run crashes (tries line first, then the ledger) ---
    mal_result.append_try(tries_path, tool=TOOL, config={**base_cfg, "event": "started"}, data_blocks=blocks, result_path=result_path, role="exploration")
    ledger_start(ledger_dir, {"table_md5": tm.get("table_md5"), "view_manifest_sha256": manifest_sha, "out_dir": str(out)})
    # --- 4b precondition: before any fit ---
    if len(august_days(tm)) < MIN_AUGUST_DAYS:
        doc = not_decidable_doc(tm)
        doc["table_md5"], doc["view_manifest_sha256"], doc["tries_log"] = tm.get("table_md5"), manifest_sha, tries_path
        mal_result.append_try(tries_path, tool=TOOL, config={**base_cfg, "event": "result", "verdict": doc["verdict"]}, data_blocks=blocks, result_path=result_path, role="exploration")
        ledger_finish(ledger_dir, {"verdict": doc["verdict"], "out_dir": str(out), "n_selected": 0, "n_august_days": doc["n_august_days"]})
        out.mkdir(parents=True)
        (out / "screen.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (out / "screen.md").write_text(to_markdown(doc), encoding="utf-8")
        return doc
    # --- the screen ---
    days = sorted({r["day"] for r in mm.training_rows(eligible)})
    selected, fold_info, counts = mm.nested_lodo_select(eligible, days, n_jobs=n_jobs)
    doc = evaluate(eligible, tm, selected, fold_info, counts, e12_sel, e12_daily, all_rows=rows)
    doc.update({"excluded_by_time": exclusion, "table_md5": tm.get("table_md5"), "view_manifest_sha256": manifest_sha, "tries_log": tries_path,
                "exp012": {"threshold": e12_thr, "oof_days": sorted(e12_days)}})
    trades = selected_trades(selected, eligible)
    ap = mal_result.append_try(tries_path, tool=TOOL, config={**base_cfg, "event": "result", "verdict": doc["verdict"]}, data_blocks=blocks, result_path=result_path, role="exploration")
    tries = {**ap, **mal_result.tries_summary(tries_path, ap["data_key"])}
    ledger_finish(ledger_dir, {"verdict": doc["verdict"], "out_dir": str(out), "n_selected": len(trades)})
    result = mal_result.build_result(
        tool=TOOL, git_sha=base_cfg["code_commit"], command=command, config={**base_cfg, "verdict": doc["verdict"]}, role="exploration", data_blocks=blocks,
        stage="failed" if doc["verdict"] == "FAIL" else "candidate",
        trades_flat=sc._trades_for_result(trades, "flat"), trades_pressure_s1=sc._trades_for_result(trades, "press"), tries=tries,
        n_candidates=counts["n_k4_rows"], n_days=len(days), runtime_s=time.time() - t0,
        notes=f"EXP-014 exploration screen, verdict {doc['verdict']}. Not evidence, no edge claim. {GATE_NOTE}",
    )
    result["verdict"] = doc["verdict"]
    result["gate_note"] = GATE_NOTE
    out.mkdir(parents=True)
    mal_result.write_result(result_path, result)
    (out / "screen.json").write_text(json.dumps(doc, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    (out / "screen.md").write_text(to_markdown(doc), encoding="utf-8")
    sink = os.environ.get("MISCUSI_RESULT")
    if sink:
        metrics = {"n_selected": len(trades), "n_days": len(days), **{f"item_{it['item']}": it["pass"] for it in doc["items"]}}
        summary = f"EXP-014 m15 screen {doc['verdict']}: {len(trades)} selected over {len(days)} days (exploration, no edge claim)"
        Path(sink).write_text(json.dumps({"ok": True, "verdict": doc["verdict"], "metrics": metrics, "summary": summary}) + "\n", encoding="utf-8")
    return doc


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="EXP-014 mig+15 PumpSwap exploration screen. Runs once. The tries log is MAL_TRIES_LOG or the default.")
    ap.add_argument("--table-run-dir", required=True)
    ap.add_argument("--view-manifest", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--n-jobs", type=int, default=1)
    args = ap.parse_args(argv)
    doc = run(args.table_run_dir, args.view_manifest, args.out_dir, n_jobs=args.n_jobs, command="python -m tools.exp014_m15_screen " + " ".join(argv if argv is not None else sys.argv[1:]))
    print(json.dumps({"verdict": doc["verdict"], "items": {i["item"]: i["pass"] for i in doc["items"]}, "n_selected": doc["n_selected"]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
