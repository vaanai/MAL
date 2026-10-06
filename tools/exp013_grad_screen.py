"""EXP-013 PR3: the graduation-classifier exploration screen (plan:
EXP/EXP-013-graduation-classifier-plan.md, Amendment 5 as revised after quant-proof).
EXPLORATION, NOT EVIDENCE: no edge claim.

One run, one verdict. Every item of Amendment 5 is its own small function (`item_*`) so a single
item can change without touching the rest. Items 1-3 and 4a/4b call the gate's own
`tools.paper_attention_promote.book_stats` once per fail model (flat 15%, pressure at slope scale 1).
The verdict is PASS only if every item holds under both fail models.

Guards run before any model is fit (see `run`): the real-data time cutoff, the pinned view
manifest (and its recorded VIEW.sha256 mtimes), the runs-once ledger, the tries log (any prior
exp013_grad entry refuses; a "started" line is appended before the first fit, so a crash still uses
the try), the EXP-012 read/ refusal, and a fresh out-dir. Nothing is written under ARTIFACTS/.

  python -m tools.exp013_grad_screen --table-run-dir D --view-manifest M.json --out-dir O [--n-jobs N]
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import tools.exp013_grad_model as gm
from tools import mal_result
from tools.exp013_screens import _assert_not_read_path
from tools.paper_attention_promote import BookTrade, _utc_day, book_stats

TOOL = "exp013_grad"
SCREEN_K = gm.PRIMARY_K
K_REFERENCE_SLOT8 = 8
K_REFERENCE_SLOT1 = 1
EXCLUDE_CAP_MS = 1_800_000
SLOT_MS = 400
JACCARD_MAX = 0.5
LAMPORTS = 1_000_000_000
LEGS = ("flat", "press")
DEFAULT_LEDGER_DIR = Path("/data/mal/exp013-grad")
DEFAULT_EXP012_DIR = Path(__file__).resolve().parent.parent / "ARTIFACTS" / "exp012"
LEDGER_NAME = "SCREEN_RUNS.jsonl"
POOL_TAG_FOR_ROOT = {"A": "fast", "C": "insample", "B": "live"}  # table manifest roots / view shas
BUILTIN_POOLS = ("A", "B", "C")
VIEW_CUTOFF = gm.REAL_DATA_CUTOFF
E12_DAYS = tuple(f"2026-09-{d}" for d in range(19, 28))
ITEM_ORDER = ("1", "2", "3", "4a", "4b", "5", "6")
V_SHA256 = "70914a1619e4cf6adbb1d1981cbd8a49483f559b230e7dcfc224335a0635b42e"  # pool_v_0909.json (Amendment 7, 2026-10-06 note); must equal tools.exp013_grad_table.VMAP_0909_SHA256
AM7_DISCLOSURE = (
    "Amendment 7 disclosure: PumpSwap legs are priced on vault + V (tools.pumpswap_virtual_adapter, mcap_mode v). The EXP-012 backcheck read migrate-entry "
    "outcomes on the explore-0814 days before this screen, so the August bars (items 4a and 4b) are no longer on unread data, in addition to the w1 "
    "disclosure in Amendment 5. No bar is relaxed; every item still gates."
)
_UTC = timezone.utc


def _utc_now() -> datetime:
    return datetime.now(_UTC)


def _parse_utc(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_UTC)


def _hour_ms(hour: str) -> int:
    return int(datetime.strptime(hour, "%Y-%m-%dT%H").replace(tzinfo=_UTC).timestamp()) * 1000


# --- guards ------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _view_key(v: Mapping[str, Any]) -> tuple[str, str, str, str]:
    h = v.get("hours") or ["", ""]
    return (os.path.realpath(str(v["root"])), str(h[0]), str(h[-1]), str(v["view_sha256_file_sha256"]))


def expected_views(table_manifest: Mapping[str, Any]) -> dict[str, Any]:
    """The views the table says it read: pools A/B/C (root, hours, sha) and the extra views."""
    runs = table_manifest.get("pool_runs", {})
    shas = table_manifest.get("view_sha256_file_sha256", {})
    roots = table_manifest.get("roots", {})
    pools: dict[str, Any] = {}
    for tag in BUILTIN_POOLS:
        name = POOL_TAG_FOR_ROOT[tag]
        if tag not in runs or name not in roots or not shas.get(name):
            raise SystemExit(f"table manifest does not describe pool {tag} (pool_runs/roots/view_sha256_file_sha256)")
        pools[tag] = {"root": roots[name], "hours": [runs[tag][0][0], runs[tag][-1][-1]], "view_sha256_file_sha256": shas[name]}
    return {"pools": pools, "extra_views": list(table_manifest.get("extra_views", []))}


def assert_view_manifest(
    view_manifest: Mapping[str, Any],
    table_manifest: Mapping[str, Any],
    *,
    check_files: bool = False,
    mtime_fn: Callable[[Path], float] | None = None,
    cutoff: datetime = VIEW_CUTOFF,
) -> None:
    """The pinned view manifest and the table's manifest must list exactly the same views. Every extra view
    records `view_sha256_mtime_utc`, which must not be after `cutoff`. check_files (the real run): the file itself
    must hash to the listed sha and its mtime (`mtime_fn`, injectable) must not be after `cutoff`."""
    exp = expected_views(table_manifest)
    pinned = view_manifest.get("pools", {})
    for tag in BUILTIN_POOLS:
        if tag not in pinned:
            raise SystemExit(f"view manifest does not list pool {tag}")
        if _view_key(pinned[tag]) != _view_key(exp["pools"][tag]):
            raise SystemExit(f"view manifest pool {tag} {_view_key(pinned[tag])} != table manifest {_view_key(exp['pools'][tag])}")
    for v in [*pinned.values(), *view_manifest.get("extra_views", [])]:
        if "view_sha256_mtime_utc" not in v:
            raise SystemExit(f"view manifest view {v.get('root')} does not record view_sha256_mtime_utc")
        if _parse_utc(v["view_sha256_mtime_utc"]) > cutoff:
            raise SystemExit(f"view manifest view {v['root']}: VIEW.sha256 mtime {v['view_sha256_mtime_utc']} is after {cutoff.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    extra_pinned = {_view_key(v) for v in view_manifest.get("extra_views", [])}
    extra_table = {_view_key(v) for v in exp["extra_views"]}
    if len(extra_pinned) != len(view_manifest.get("extra_views", [])):
        raise SystemExit("view manifest lists an extra view twice")
    unlisted = extra_table - extra_pinned
    if unlisted:
        raise SystemExit(f"table view(s) not listed in the view manifest (or sha mismatch): {sorted(unlisted)}")
    surplus = extra_pinned - extra_table
    if surplus:
        raise SystemExit(f"view manifest lists view(s) the table did not read: {sorted(surplus)}")
    if check_files:
        mt = mtime_fn or (lambda p: p.stat().st_mtime)
        for v in [*exp["pools"].values(), *exp["extra_views"]]:
            f = Path(os.path.realpath(str(v["root"]))) / "VIEW.sha256"
            if not f.is_file():
                raise SystemExit(f"{f} missing")
            if _sha256_file(f) != v["view_sha256_file_sha256"]:
                raise SystemExit(f"{f} no longer hashes to the pinned sha256")
            if datetime.fromtimestamp(mt(f), _UTC) > cutoff:
                raise SystemExit(f"{f} was written after {cutoff.strftime('%Y-%m-%dT%H:%M:%SZ')}: a view built after the cutoff is not allowed")


def assert_v_adapter(table_manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Amendment 7: a real table must have been priced through the V adapter on the pinned map, with the pre-pass
    inside the limit. Refuses before the try is spent."""
    if not re.fullmatch(r"[0-9a-f]{64}", V_SHA256):
        raise SystemExit(f"V_SHA256 is {V_SHA256!r}, not a sha256: the manager fills it in after job #228. Refusing to run")
    v = table_manifest.get("v_adapter")
    if not isinstance(v, dict):
        raise SystemExit("table manifest has no v_adapter record: the table was not priced through the V adapter (Amendment 7)")
    if v.get("vmap_sha256") != V_SHA256 or v.get("mcap_mode") != "v":
        raise SystemExit(f"table manifest v_adapter is not the pinned map in mode v (sha {v.get('vmap_sha256')}, mode {v.get('mcap_mode')})")
    cov = v.get("prepass") or {}
    frac = cov.get("missing_fraction")
    if not cov.get("prints") or frac is None or frac > cov.get("max_missing_fraction", 0.01):
        raise SystemExit(f"table manifest v_adapter pre-pass is outside the limit ({cov.get('missing')} of {cov.get('prints')} PumpSwap prints without V)")
    if not (v.get("adapter_counts") or {}).get("corrected"):
        raise SystemExit("table manifest v_adapter records no corrected PumpSwap print")
    return v


def assert_out_dir_fresh(out_dir: Path | str) -> Path:
    real = Path(os.path.realpath(str(out_dir)))
    if "ARTIFACTS" in real.parts:
        raise SystemExit(f"--out-dir {real} is under ARTIFACTS/: the screen never writes there")
    if real.exists():
        raise SystemExit(f"--out-dir {real} already exists")
    return real


def assert_exp012_dir(path: Path | str) -> Path:
    return _assert_not_read_path(Path(path))


def count_tries(tries_log: Path | str | None, tool: str = TOOL) -> int:
    p = mal_result._tries_log_path(tries_log)
    n = 0
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                if json.loads(line).get("tool") == tool:
                    n += 1
            except (json.JSONDecodeError, AttributeError):
                if tool in line:  # a damaged line that mentions this tool counts as a prior try
                    n += 1
    return n


def assert_no_prior_try(tries_log: Path | str | None) -> None:
    """Amendment 5 section 6: any exp013_grad entry in the tries log (any manifest) refuses the run."""
    n = count_tries(tries_log)
    if n:
        raise SystemExit(f"tries log already holds {n} {TOOL} entr{'y' if n == 1 else 'ies'}: the screen runs once")


def ledger_path(ledger_dir: Path | str) -> Path:
    return Path(ledger_dir) / LEDGER_NAME


def assert_ledger_empty(ledger_dir: Path | str) -> None:
    p = ledger_path(ledger_dir)
    if p.exists() and TOOL in p.read_text(encoding="utf-8"):
        raise SystemExit(f"{p} already holds a {TOOL} line: the screen runs once")


def resolved_tries_log(tries_log: Path | str | None) -> str:
    return os.path.realpath(str(mal_result._tries_log_path(tries_log)))


def ledger_start(ledger_dir: Path | str, info: Mapping[str, Any]) -> None:
    """Second lock. Under an exclusive lock: refuse if any line for exp013_grad exists, else append "started"."""
    p = ledger_path(ledger_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            fh.seek(0)
            prior = []
            for x in fh.read().splitlines():
                if x.strip():
                    try:
                        prior.append(json.loads(x))
                    except json.JSONDecodeError:
                        prior.append({"tool": TOOL})  # a damaged ledger counts as a prior run
            if any(r.get("tool") == TOOL for r in prior):
                raise SystemExit(f"{p} already holds a {TOOL} line: the screen runs once")
            fh.seek(0, os.SEEK_END)
            fh.write(json.dumps({"tool": TOOL, "event": "started", "ts_utc": _utc_now().strftime("%Y-%m-%dT%H:%M:%SZ"), **info}, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def ledger_finish(ledger_dir: Path | str, info: Mapping[str, Any]) -> None:
    with open(ledger_path(ledger_dir), "a", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            fh.write(json.dumps({"tool": TOOL, "event": "finished", "ts_utc": _utc_now().strftime("%Y-%m-%dT%H:%M:%SZ"), **info}, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


# --- day membership and the trigger-time exclusion ---------------------------------------


def _days_of_run(first: str, last: str) -> set[str]:
    from tools.exp013_pool import _hour_range

    return {h[:10] for h in _hour_range(first, last)}


def pool_days(table_manifest: Mapping[str, Any]) -> dict[str, set[str]]:
    """Days per pool tag from the manifest's pool_runs (hour ranges), not from row date prefixes."""
    return {tag: {d for first, last in runs for d in _days_of_run(first, last)} for tag, runs in table_manifest.get("pool_runs", {}).items()}


def all_manifest_days(table_manifest: Mapping[str, Any]) -> set[str]:
    return {d for s in pool_days(table_manifest).values() for d in s}


def day_groups(table_manifest: Mapping[str, Any]) -> dict[str, set[str]]:
    """4a = pool A plus the extra (getBlock) views; 4b = the extra views alone."""
    pd = pool_days(table_manifest)
    return {"4a": set(pd.get("A", set())) | set(pd.get("X", set())), "4b": set(pd.get("X", set()))}


def exit_bound_ms(pool: str | None, trigger_ms: int, pool_runs: Mapping[str, Sequence[Sequence[str]]]) -> int | None:
    """End of the pool run that contains the trigger (hour_ms(first) <= trigger_ms < end); a gap starts where a run of
    the same pool ends, so this is also the first gap start after the trigger. None if no run contains it."""
    for first, last in pool_runs.get(pool or "", []):
        end = _hour_ms(last) + 3_600_000
        if _hour_ms(first) <= trigger_ms < end:
            return end
    return None  # no run contains the trigger (in a gap, before the first run, or after the last): excluded


def trigger_excluded(row: Mapping[str, Any], pool_runs: Mapping[str, Sequence[Sequence[str]]], trigger_ms: int | None = None) -> bool:
    """Amendment 5 section 1: excluded, whatever the realized exit, if trigger_ms + 1,800,000 + (k + max(4, k)) x 400
    is at or after the end of the pool run or the first gap start after the trigger."""
    k = int(row["entry_land_k"])
    t = int(row["trigger_ms"]) if trigger_ms is None else trigger_ms
    bound = exit_bound_ms(row.get("pool"), t, pool_runs)
    if bound is None:
        return True
    return t + EXCLUDE_CAP_MS + (k + max(4, k)) * SLOT_MS >= bound


def split_eligible(rows: Sequence[Mapping[str, Any]], pool_runs: Mapping[str, Sequence[Sequence[str]]]) -> tuple[list[Mapping[str, Any]], dict[str, Any]]:
    keep, by_k = [], {}
    for r in rows:
        if "trigger_ms" not in r:
            raise SystemExit(f"row {r.get('mint')} k={r.get('entry_land_k')} has no trigger_ms")
        if trigger_excluded(r, pool_runs):
            by_k[str(r["entry_land_k"])] = by_k.get(str(r["entry_land_k"]), 0) + 1
        else:
            keep.append(r)
    return keep, {"n_rows": len(rows), "n_eligible": len(keep), "n_excluded_by_trigger_time": len(rows) - len(keep), "excluded_by_k": dict(sorted(by_k.items()))}


def censored_report(censored: Sequence[Mapping[str, Any]] | None, rows: Sequence[Mapping[str, Any]], pool_runs: Mapping[str, Sequence[Sequence[str]]]) -> dict[str, Any]:
    """Rows the table censored on a realized exit (censored.jsonl), counted by reason, and how many of them the
    trigger-time rule would have kept. A censored record carries no trigger_ms: it is taken from another row of the
    same mint (same trigger, any k); a mint with no row at all is `unknown`."""
    if censored is None:
        return {"available": False}
    trig = {r["mint"]: (r["trigger_ms"], r.get("pool")) for r in rows}
    reasons: dict[str, int] = {}
    kept = unknown = 0
    kept_by_k: dict[str, int] = {}
    for c in censored:
        reasons[c.get("reason", "?")] = reasons.get(c.get("reason", "?"), 0) + 1
        t = c.get("trigger_ms")
        pool = c.get("pool")
        if t is None and c["mint"] in trig:
            t, pool = trig[c["mint"]]
        if t is None or pool is None:
            unknown += 1
        elif not trigger_excluded({"entry_land_k": c["entry_land_k"], "pool": pool}, pool_runs, int(t)):
            kept += 1
            kept_by_k[str(c["entry_land_k"])] = kept_by_k.get(str(c["entry_land_k"]), 0) + 1
    return {"available": True, "n_censored": len(censored), "by_reason": dict(sorted(reasons.items())), "n_trigger_time_rule_would_keep": kept,
            "n_trigger_time_rule_would_keep_by_k": dict(sorted(kept_by_k.items())), "n_trigger_time_unknown": unknown}


def k_rows_by_mint(rows: Sequence[Mapping[str, Any]], k: int = SCREEN_K) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    for r in rows:
        if r["entry_land_k"] == k:
            if r["mint"] in out:
                raise ValueError(f"duplicate (mint, k) = ({r['mint']!r}, {k})")
            out[r["mint"]] = r
    return out


def assert_trigger_days(trades: Sequence[Mapping[str, Any]]) -> None:
    """The gate buckets a trade by its t_ms: that day must be the row's own day."""
    for t in trades:
        if _utc_day(int(t["trigger_ms"])) != t["day"]:
            raise SystemExit(f"trade {t['mint']}: day {t['day']} != UTC day of trigger_ms {t['trigger_ms']}")


def selected_trades(selected: Sequence[Mapping[str, Any]], rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Each selected mint joined to its k=4 row: day, pool, trigger_ms (the trade time), flat, press, edge flag.
    MISS rows stay in as trades."""
    by_mint = k_rows_by_mint(rows)
    out = []
    for s in selected:
        r = by_mint[s["mint"]]
        out.append(
            {"mint": s["mint"], "day": s["day"], "trigger_ms": int(r["trigger_ms"]), "flat": r["flat"], "press": r["press"], "filled": bool(r["filled"]),
             "edge": bool(r.get("edge_left") or r.get("edge_right")), "pool": r.get("pool")}
        )
    assert_trigger_days(out)
    return out


def edge_days(pool_runs: Mapping[str, Sequence[Sequence[str]]]) -> set[str]:
    """Amendment 3 edge days from the manifest's pool_runs: the first and last day of every contiguous run."""
    return {d[:10] for runs in pool_runs.values() for first, last in runs for d in (first, last)}


def book(trades: Sequence[Mapping[str, Any]], leg: str) -> list[BookTrade]:
    assert_trigger_days(trades)
    return [BookTrade(mint=t["mint"], t_ms=int(t["trigger_ms"]), pnl=int(round(t[leg]))) for t in trades]


# --- bars 1-3 (the gate's own book_stats, once per fail model) -----------------------------


def leg_bars(trades: Sequence[Mapping[str, Any]], leg: str, all_days: set[str] | None = None) -> dict[str, Any]:
    """One book_stats call for this fail model (no pressure_scale_1 keyword, no promote fields). `all_days` = every UTC
    day in the manifest for the item; bar 3 counts positive days against N = len(all_days) (default: the days with a
    trade). A day with no trade is not positive."""
    st = book_stats(book(trades, leg))
    ci, ex3 = st["mean_ci90_sol"], st["total_ex_top3_sol"]
    by_day: dict[str, int] = {}
    for t in trades:
        d = _utc_day(int(t["trigger_ms"]))
        by_day[d] = by_day.get(d, 0) + int(round(t[leg]))
    days = set(all_days) if all_days is not None else set(by_day)
    pos = sum(1 for d in days if by_day.get(d, 0) > 0)
    bar1 = bool(st["mean_sol"] is not None and st["mean_sol"] > 0 and ci is not None and ci[0] > 0)
    bar2 = bool(ex3 is not None and ex3 > 0)
    bar3 = bool(len(days) > 0 and pos * 2 > len(days))
    return {
        "n": st["n"], "mean_sol": st["mean_sol"], "total_sol": st["total_sol"], "ci90_lo_sol": None if ci is None else ci[0], "ex_top3_sol": ex3,
        "n_days": st["n_days"], "days_positive": pos, "n_manifest_days": len(days), "book_stats_majority_days_positive": st["majority_days_positive"],
        "bar1_mean_gt_0_and_ci_lo_gt_0": bar1, "bar2_ex_top3_gt_0": bar2, "bar3_majority_of_manifest_days_positive": bar3,
        "report_only_n_ge_100": st["n"] >= 100, "report_only_days_ge_5": st["n_days"] >= 5, "pass": bool(bar1 and bar2 and bar3),
    }


def bars(trades: Sequence[Mapping[str, Any]], all_days: set[str] | None = None) -> dict[str, Any]:
    legs = {leg: leg_bars(trades, leg, all_days) for leg in LEGS}
    return {"legs": legs, "pass": all(legs[leg]["pass"] for leg in LEGS)}


def _bar_item(item: str, what: str, key: str, trades: Sequence[Mapping[str, Any]], all_days: set[str] | None, legs: Mapping[str, Any] | None) -> dict[str, Any]:
    """`legs` = a precomputed bars()["legs"]: evaluate() computes book_stats once per fail model and shares it across items 1-3."""
    legs = dict(legs) if legs is not None else {leg: leg_bars(trades, leg, all_days) for leg in LEGS}
    return {"item": item, "what": what, "pass": all(legs[leg][key] for leg in LEGS), "legs": legs}


def item_1(trades: Sequence[Mapping[str, Any]], all_days: set[str] | None = None, legs: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Bar 1: mean > 0 and CI90 lower bound > 0, both fail models."""
    return _bar_item("1", "mean SOL > 0 and CI90 lower bound > 0", "bar1_mean_gt_0_and_ci_lo_gt_0", trades, all_days, legs)


def item_2(trades: Sequence[Mapping[str, Any]], all_days: set[str] | None = None, legs: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Bar 2: total SOL > 0 after removing the top 3 trades (None fails)."""
    return _bar_item("2", "ex-top-3 total SOL > 0", "bar2_ex_top3_gt_0", trades, all_days, legs)


def item_3(trades: Sequence[Mapping[str, Any]], all_days: set[str] | None = None, legs: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Bar 3: positive days x 2 > N, N = every manifest day; an empty day is not positive."""
    return _bar_item("3", "positive days x 2 > manifest days", "bar3_majority_of_manifest_days_positive", trades, all_days, legs)


def assert_pool_agrees(trades: Sequence[Mapping[str, Any]], tags: set[str], days: set[str], pd: Mapping[str, set[str]]) -> None:
    """A trade's pool tag and its day must agree with the manifest day list."""
    for t in trades:
        p = t.get("pool")
        if p in tags and t["day"] not in days:
            raise SystemExit(f"trade {t['mint']}: pool {p} but day {t['day']} is not in the manifest day list for this item")
        if p not in tags and t["day"] in days and t["day"] not in pd.get(p, set()):
            raise SystemExit(f"trade {t['mint']}: pool {p} but day {t['day']} is in the manifest day list for this item")


def item_4(name: str, trades: Sequence[Mapping[str, Any]], tags: set[str], days: set[str], pd: Mapping[str, set[str]]) -> dict[str, Any]:
    """Bars 1-3 again on the same pooled selected trades, restricted by pool tag (day list asserted). No model is fit."""
    assert_pool_agrees(trades, tags, days, pd)
    sub = [t for t in trades if t.get("pool") in tags]
    return {"item": name, "what": f"bars 1-3 on the pooled selected trades, pools {sorted(tags)}", "n_days_in_group": len(days), **bars(sub, days)}


def item_4a(trades: Sequence[Mapping[str, Any]], groups: Mapping[str, set[str]], pd: Mapping[str, set[str]]) -> dict[str, Any]:
    return item_4("4a", trades, {"A", "X"}, groups["4a"], pd)


def item_4b(trades: Sequence[Mapping[str, Any]], groups: Mapping[str, set[str]], pd: Mapping[str, set[str]]) -> dict[str, Any]:
    return item_4("4b", trades, {"X"}, groups["4b"], pd)


def item_5(selected: Sequence[Mapping[str, Any]], eligible_rows: Sequence[Mapping[str, Any]], all_rows: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Slot + 8: pooled mean of the k=8 rows of the selected mints > 0 under both fail models. `eligible_rows` already
    excludes the trigger-time-excluded rows. A selected mint without an eligible k=8 row is counted, never imputed;
    with `all_rows` (the table before the exclusion) the count is split into trigger-time exclusions (the table has a
    k=8 row) and table-censored k=8 (it has none)."""
    p = gm.pnl_at_k(selected, eligible_rows, K_REFERENCE_SLOT8)
    legs: dict[str, Any] = {}
    for leg in LEGS:
        vals = [r[leg] for r in p["records"]]
        mean = (sum(vals) / len(vals) / LAMPORTS) if vals else None
        legs[leg] = {"mean_sol": mean, "n": len(vals), "pass": bool(mean is not None and mean > 0)}
    out = {"item": "5", "what": "slot+8 pooled mean SOL > 0", "n_selected": p["n_selected"], "n_joined": p["n_joined"], "n_missing_k8": p["n_missing_k"],
           "legs": legs, "pass": all(legs[leg]["pass"] for leg in LEGS)}
    if all_rows is not None:
        have8 = {r["mint"] for r in all_rows if r["entry_land_k"] == K_REFERENCE_SLOT8}
        miss = [m["mint"] for m in p["missing"]]
        out["n_missing_k8_trigger_time_excluded"] = sum(1 for m in miss if m in have8)
        out["n_missing_k8_table_censored"] = sum(1 for m in miss if m not in have8)
    return out


E12_THRESHOLD = 0.8030766588450794
E12_N_SELECTED = 881


def exp012_selected(e12_dir: Path | str, expect: tuple[float, int] | None = (E12_THRESHOLD, E12_N_SELECTED)) -> tuple[set[str], set[tuple[str, str]], float]:
    """(days, selected (day, mint) pairs, threshold) from EXP-012's oof_scores.json at threshold.json. Refuses unless the
    threshold is exactly E12_THRESHOLD and the selected count is E12_N_SELECTED (`expect=None` is for synthetic fixtures)."""
    d = assert_exp012_dir(e12_dir)
    oof = json.loads((d / "oof_scores.json").read_text(encoding="utf-8"))
    thr = float(json.loads((d / "threshold.json").read_text(encoding="utf-8"))["threshold"])
    sel = {(o["day"], o["mint"]) for o in oof["rows"] if o["score"] >= thr}
    if expect is not None and (thr != expect[0] or len(sel) != expect[1]):
        raise SystemExit(f"EXP-012 threshold {thr!r} / selected {len(sel)} != expected {expect[0]!r} / {expect[1]}")
    return set(oof["days"]), sel, thr


def exp012_daily_pnl(e12_dir: Path | str) -> dict[str, dict[str, float]]:
    """{leg: {day: SOL}} from nested_fixed_threshold_lodo.json per_day: n_entered x mean_pct x 0.5 SOL (mean_pct in percent)."""
    d = assert_exp012_dir(e12_dir)
    doc = json.loads((d / "nested_fixed_threshold_lodo.json").read_text(encoding="utf-8"))
    out: dict[str, dict[str, float]] = {leg: {} for leg in LEGS}
    for r in doc["per_day"]:
        for leg in LEGS:
            m = r.get(f"{leg}_mean_pct")
            out[leg][r["day"]] = (r["n_entered"] * m / 100.0 * 0.5) if (m is not None and r["n_entered"]) else 0.0
    return out


def jaccard(a: set, b: set) -> float | None:
    u = a | b
    return (len(a & b) / len(u)) if u else None


def pearson(x: Sequence[float], y: Sequence[float]) -> float | None:
    n = len(x)
    if n < 3:
        return None
    mx, my = sum(x) / n, sum(y) / n
    sx = math.sqrt(sum((a - mx) ** 2 for a in x))
    sy = math.sqrt(sum((b - my) ** 2 for b in y))
    if sx == 0 or sy == 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (sx * sy)


def item_6(
    trades: Sequence[Mapping[str, Any]],
    rows: Sequence[Mapping[str, Any]],
    e12_sel: set[tuple[str, str]],
    e12_daily: Mapping[str, Mapping[str, float]] | None = None,
    e12_days: Sequence[str] = E12_DAYS,
    eligible_rows: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """A = selected mints with trigger day in EXP-012's 9 days. B = EXP-012's OOF-selected mints that have a k=4 row in
    the full table (`rows`, before the trigger-time exclusion). J on B restricted to the eligible k=4 mints is report-only. Bar: J(A, B) <= 0.5; an empty union fails. Reported: J over the unrestricted B, |A and B| / min, and the
    daily-PnL correlation on those days (report-only)."""
    days = set(e12_days)
    a = {t["mint"] for t in trades if t["day"] in days}
    k4 = {r["mint"] for r in rows if r["entry_land_k"] == SCREEN_K}
    b_all = {m for _, m in e12_sel}
    b = b_all & k4
    j = jaccard(a, b)
    j_post = None
    if eligible_rows is not None:
        k4e = {r["mint"] for r in eligible_rows if r["entry_land_k"] == SCREEN_K}
        j_post = jaccard(a, b_all & k4e)
    corr: dict[str, Any] = {}
    if e12_daily is not None:
        for leg in LEGS:
            mine = [sum(t[leg] for t in trades if t["day"] == d) / LAMPORTS for d in sorted(days)]
            corr[leg] = pearson(mine, [e12_daily[leg].get(d, 0.0) for d in sorted(days)])
    return {
        "item": "6", "what": f"mint Jaccard J(A, B) <= {JACCARD_MAX}", "n_a": len(a), "n_b": len(b), "n_b_unrestricted": len(b_all), "n_both": len(a & b),
        "jaccard": j, "jaccard_post_exclusion_b_report_only": j_post, "jaccard_unrestricted_b_report_only": jaccard(a, b_all), "overlap_min_report_only": (len(a & b) / min(len(a), len(b))) if a and b else None,
        "other_days": "n/a", "daily_pnl_corr_report_only": corr, "pass": bool(j is not None and j <= JACCARD_MAX),
    }


def verdict_of(items: Sequence[Mapping[str, Any]]) -> str:
    """PASS only if every one of items 1, 2, 3, 4a, 4b, 5 and 6 passed (a missing item fails)."""
    got = {i["item"]: i["pass"] for i in items}
    if set(got) != set(ITEM_ORDER):
        return "FAIL"
    return "PASS" if all(got[k] for k in ITEM_ORDER) else "FAIL"


# --- report-only extras ----------------------------------------------------------------


def per_day_table(rows: Sequence[Mapping[str, Any]], trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for d in sorted({r["day"] for r in rows if r["entry_land_k"] == SCREEN_K}):
        n_rows = sum(1 for r in rows if r["day"] == d and r["entry_land_k"] == SCREEN_K)
        sel = [t for t in trades if t["day"] == d]
        out.append(
            {"day": d, "n_rows": n_rows, "n_selected": len(sel),
             "flat_mean_sol": (sum(t["flat"] for t in sel) / len(sel) / LAMPORTS) if sel else None,
             "press_mean_sol": (sum(t["press"] for t in sel) / len(sel) / LAMPORTS) if sel else None}
        )
    return out


def fold_fraction_by_source(fold_info: Sequence[Mapping[str, Any]], rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Each outer fold's selected fraction, labelled by the pool tag(s) of that day's rows."""
    pools: dict[str, set[str]] = {}
    for r in rows:
        if r["entry_land_k"] == SCREEN_K:
            pools.setdefault(r["day"], set()).add(str(r.get("pool")))
    return [{"day": f["outer_day"], "source": "+".join(sorted(pools.get(f["outer_day"], {"?"}))), "trained": f["trained"], "selected_fraction": f["selected_fraction"]} for f in fold_info]


def edge_split(trades: Sequence[Mapping[str, Any]], edge: set[str], all_days: set[str], full: Mapping[str, Any] | None = None) -> dict[str, Any]:
    kept = [t for t in trades if t["day"] not in edge]
    return {"edge_days": sorted(edge), "with_edge_days": full if full is not None else bars(trades, all_days), "without_edge_days": bars(kept, all_days - edge), "n_removed": len(trades) - len(kept)}


def slot1_reference(selected: Sequence[Mapping[str, Any]], eligible_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    p = gm.pnl_at_k(selected, eligible_rows, K_REFERENCE_SLOT1)
    out: dict[str, Any] = {"n_joined": p["n_joined"], "n_missing_k1": p["n_missing_k"]}
    for leg in LEGS:
        vals = [r[leg] for r in p["records"]]
        out[f"{leg}_mean_sol"] = (sum(vals) / len(vals) / LAMPORTS) if vals else None
    return out


def freeze_reference(rows: Sequence[Mapping[str, Any]], days: Sequence[str]) -> dict[str, Any]:
    oof, info = gm.outer_lodo_oof(rows, days)
    if not oof:
        return {"label": "freeze reference, not screen", "threshold": None, **info}
    thr = gm.pooled_threshold(oof)
    return {"label": "freeze reference, not screen", "threshold": thr["threshold"], "n_oof": thr["n_oof"], **info}


# --- the run ----------------------------------------------------------------------------


def evaluate(
    rows: Sequence[Mapping[str, Any]],
    table_manifest: Mapping[str, Any],
    selected: Sequence[Mapping[str, Any]],
    fold_info: Sequence[Mapping[str, Any]],
    counts: Mapping[str, int],
    e12_sel: set[tuple[str, str]],
    e12_daily: Mapping[str, Mapping[str, float]] | None = None,
    all_rows: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Everything after selection. `rows` are the eligible rows (trigger-time exclusion applied); `all_rows` the full
    table (item 6's set B and item 5's split). book_stats runs once per fail model for the full set and is shared. No fitting."""
    all_rows = rows if all_rows is None else all_rows
    trades = selected_trades(selected, rows)
    groups, pd, all_days = day_groups(table_manifest), pool_days(table_manifest), all_manifest_days(table_manifest)
    shared = sorted(d for d in all_days if sum(1 for s in pd.values() if d in s) > 1)
    full = bars(trades, all_days)
    items = [
        item_1(trades, all_days, full["legs"]), item_2(trades, all_days, full["legs"]), item_3(trades, all_days, full["legs"]), item_4a(trades, groups, pd),
        item_4b(trades, groups, pd), item_5(selected, rows, all_rows), item_6(trades, all_rows, e12_sel, e12_daily, eligible_rows=rows),
    ]
    return {
        "schema": "exp013_grad_screen_v1",
        "note": "EXPLORATION screen, Amendment 5. Not evidence, no edge claim.",
        "verdict": verdict_of(items),
        "items": items,
        "gate_report_only": full,
        "n_selected": len(trades),
        "day_groups": {k: sorted(v) for k, v in groups.items()},
        "days_in_more_than_one_pool": shared,
        "edge_split_report_only": edge_split(trades, edge_days(table_manifest.get("pool_runs", {})), all_days, full),
        "per_day": per_day_table(rows, trades),
        "fold_info": list(fold_info),
        "fold_fraction_by_source": fold_fraction_by_source(fold_info, rows),
        "counts": dict(counts),
        "slot1_reference": slot1_reference(selected, rows),
    }


def _f(v: Any) -> str:
    if v is None:
        return "n/a"
    return f"{v:+.4f}" if isinstance(v, float) else str(v)


def to_markdown(doc: Mapping[str, Any]) -> str:
    lines = ["# EXP-013 graduation screen (exploration, no edge claim)", "", f"**Verdict: {doc['verdict']}**", "", doc["note"], "", f"> {doc.get('am7_disclosure', AM7_DISCLOSURE)}", "", "## Items", "", "| Item | Pass | Detail |", "| --- | --- | --- |"]
    for it in doc["items"]:
        detail = ""
        if "legs" in it:
            keys = ("n", "mean_sol", "ci90_lo_sol", "ex_top3_sol", "days_positive", "n_manifest_days")
            detail = "; ".join(f"{leg}: " + ", ".join(f"{k}={_f(v)}" for k, v in lg.items() if k in keys) for leg, lg in it["legs"].items())
        if it["item"] == "5":
            detail += (f" | joined {it['n_joined']} of {it['n_selected']}, missing k=8: {it['n_missing_k8']} (trigger-time excluded {it.get('n_missing_k8_trigger_time_excluded')}, "
                       f"table-censored {it.get('n_missing_k8_table_censored')})")
        if it["item"] == "6":
            detail = (f"J={_f(it['jaccard'])} (post-exclusion B, report-only: {_f(it['jaccard_post_exclusion_b_report_only'])}), |A|={it['n_a']}, |B|={it['n_b']}, both={it['n_both']}, J(unrestricted B)={_f(it['jaccard_unrestricted_b_report_only'])}, "
                      f"overlap/min={_f(it['overlap_min_report_only'])}, corr(report-only)={it['daily_pnl_corr_report_only']}, other days: n/a")
        lines.append(f"| {it['item']} {it['what']} | {'PASS' if it['pass'] else 'FAIL'} | {detail} |")
    tc = doc["table_censored"]
    k4 = tc.get("n_trigger_time_rule_would_keep_by_k", {}).get(str(SCREEN_K), 0) if tc.get("available") else "n/a"
    lines += ["", f"Table-censored k={SCREEN_K} rows the trigger-time rule would have kept (read next to bars 1-3 above): {k4}"]
    g = doc["gate_report_only"]["legs"]
    lines += ["", "## Gate counts (report-only)", ""]
    lines += [f"- {leg}: n={g[leg]['n']}, days={g[leg]['n_days']}, n>=100: {g[leg]['report_only_n_ge_100']}, days>=5: {g[leg]['report_only_days_ge_5']}, book_stats majority: {g[leg]['book_stats_majority_days_positive']}" for leg in LEGS]
    es = doc["edge_split_report_only"]
    lines += ["", "## Edge-flagged days (report-only)", "", f"Edge days: {', '.join(es['edge_days']) or 'none'}; trades removed: {es['n_removed']}"]
    for name in ("with_edge_days", "without_edge_days"):
        for leg in LEGS:
            lg = es[name]["legs"][leg]
            lines.append(f"- {name} {leg}: n={lg['n']}, mean={_f(lg['mean_sol'])}, ci90_lo={_f(lg['ci90_lo_sol'])}, ex_top3={_f(lg['ex_top3_sol'])}, bar3={lg['bar3_majority_of_manifest_days_positive']}")
    lines += ["", "## Per day", "", "| Day | rows | selected | flat mean SOL | press mean SOL |", "| --- | ---: | ---: | ---: | ---: |"]
    lines += [f"| {d['day']} | {d['n_rows']} | {d['n_selected']} | {_f(d['flat_mean_sol'])} | {_f(d['press_mean_sol'])} |" for d in doc["per_day"]]
    lines += ["", "## Selected fraction by fold and source", ""]
    lines += [f"- {f['day']} ({f['source']}): {_f(f['selected_fraction'])}{'' if f['trained'] else ' (skipped)'}" for f in doc["fold_fraction_by_source"]]
    lines += ["", "## Exclusions and reference", "", f"- trigger-time exclusion: {json.dumps(doc['trigger_time_exclusion'])}", f"- table-censored rows: {json.dumps(doc['table_censored'])}", f"- tries log: {doc['tries_log']}",
              f"- slot+1 (reference): {json.dumps(doc['slot1_reference'])}",
              f"- frozen pooled-OOF p90 (freeze reference, not screen): {json.dumps({k: v for k, v in doc['freeze_reference'].items() if k != 'skipped_folds'})}",
              f"- counts: {json.dumps(doc['counts'])}", f"- folds: {len(doc['fold_info'])}, skipped: {sum(1 for f in doc['fold_info'] if not f['trained'])}"]
    return "\n".join(lines) + "\n"


def _trades_for_result(trades: Sequence[Mapping[str, Any]], leg: str) -> list[dict[str, Any]]:
    out = []
    for t in trades:
        sol = t[leg] / LAMPORTS
        out.append({"sol": sol, "pct": sol / 0.5 * 100.0, "day": _utc_day(int(t["trigger_ms"])), "filled": t["filled"]})
    return out


def _git_sha() -> str:
    import subprocess

    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parent, capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return "unknown"


def data_blocks_of(table_manifest: Mapping[str, Any]) -> list[dict[str, str]]:
    from datetime import timedelta

    blocks = []
    for tag, runs in sorted(table_manifest.get("pool_runs", {}).items()):
        for first, last in runs:
            end = datetime.strptime(last, "%Y-%m-%dT%H").replace(tzinfo=_UTC) + timedelta(hours=1)
            blocks.append({"start_hour": first, "end_hour_exclusive": end.strftime("%Y-%m-%dT%H"), "host": f"exp013-pool-{tag}", "ledger_owner": "EXP-013"})
    return blocks


def _read_censored(run_dir: Path) -> list[dict[str, Any]] | None:
    p = run_dir / "censored.jsonl"
    if not p.is_file():
        return None
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


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
    view_cutoff: datetime = VIEW_CUTOFF,
    check_view_files: bool | None = None,
    command: str = "",
) -> dict[str, Any]:
    """ledger_dir, tries_log, exp012_dir, e12_expect, now, mtime_fn, view_cutoff and check_view_files are for tests
    only; the CLI never sets them."""
    t0 = time.time()
    # --- guards, all before any fit ---
    real_table = gm.assert_run_dir_allowed(table_run_dir, now=now)
    is_real = str(real_table) == gm.REAL_DATA_PREFIX or str(real_table).startswith(gm.REAL_DATA_PREFIX + "/")
    if is_real and os.path.realpath(str(ledger_dir)) != os.path.realpath(str(DEFAULT_LEDGER_DIR)):
        raise SystemExit(f"a real-data run must use the default ledger dir {DEFAULT_LEDGER_DIR}, got {ledger_dir}")
    out = assert_out_dir_fresh(out_dir)
    assert_exp012_dir(exp012_dir)
    rows, tmanifest = gm.load_table(real_table, now=now)
    vm_bytes = Path(view_manifest_path).read_bytes()
    view_manifest = json.loads(vm_bytes.decode("utf-8"))
    assert_view_manifest(view_manifest, tmanifest, check_files=is_real if check_view_files is None else check_view_files, mtime_fn=mtime_fn, cutoff=view_cutoff)
    if is_real:
        assert_v_adapter(tmanifest)
    tries_path = resolved_tries_log(tries_log)
    assert_no_prior_try(tries_path)
    assert_ledger_empty(ledger_dir)
    e12_days, e12_sel, e12_thr = exp012_selected(exp012_dir, e12_expect)
    e12_daily = exp012_daily_pnl(exp012_dir)
    pool_runs = tmanifest.get("pool_runs", {})
    eligible, exclusion = split_eligible(rows, pool_runs)
    censored = censored_report(_read_censored(real_table), rows, pool_runs)
    manifest_sha = hashlib.sha256(vm_bytes).hexdigest()
    blocks = data_blocks_of(tmanifest)
    base_cfg = {"k": SCREEN_K, "table_md5": tmanifest.get("table_md5"), "view_manifest_sha256": manifest_sha, "code_commit": _git_sha()}
    result_path = out / "result.json"
    # --- the try is used from here on, even if the run crashes. The tries line comes first: a kill between the two
    # lines leaves the tries log refusing a rerun. ---
    out.mkdir(parents=True)  # a mkdir collision fails here, before the try is spent
    mal_result.append_try(tries_path, tool=TOOL, config={**base_cfg, "event": "started"}, data_blocks=blocks, result_path=result_path, role="exploration")
    ledger_start(ledger_dir, {"table_md5": tmanifest.get("table_md5"), "view_manifest_sha256": manifest_sha, "out_dir": str(out)})
    # --- the screen ---
    days = sorted({r["day"] for r in gm.training_rows(eligible)})
    selected, fold_info, counts = gm.nested_lodo_select(eligible, days, n_jobs=n_jobs)
    doc = evaluate(eligible, tmanifest, selected, fold_info, counts, e12_sel, e12_daily, all_rows=rows)
    doc["freeze_reference"] = freeze_reference(eligible, days)
    doc["trigger_time_exclusion"] = exclusion
    doc["table_censored"] = censored
    doc["table_md5"] = tmanifest.get("table_md5")
    doc["v_adapter"] = tmanifest.get("v_adapter")
    doc["am7_disclosure"] = AM7_DISCLOSURE
    doc["view_manifest_sha256"] = manifest_sha
    doc["tries_log"] = tries_path
    doc["exp012"] = {"threshold": e12_thr, "oof_days": sorted(e12_days)}
    # --- the screen record first: screen.json and screen.md hold everything, so a later failure cannot lose the one-shot output ---
    (out / "screen.json").write_text(json.dumps(doc, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    (out / "screen.md").write_text(to_markdown(doc), encoding="utf-8")
    # --- then the result tries line and ledger "finished" ---
    trades = selected_trades(selected, eligible)
    ap = mal_result.append_try(tries_path, tool=TOOL, config={**base_cfg, "event": "result", "verdict": doc["verdict"]}, data_blocks=blocks, result_path=result_path, role="exploration")
    tries = {**ap, **mal_result.tries_summary(tries_path, ap["data_key"])}
    ledger_finish(ledger_dir, {"verdict": doc["verdict"], "out_dir": str(out), "n_selected": len(trades)})
    # --- result.v1 last. It raises on a schema error; the screen record above stays, the failure is written down, the run exits nonzero ---
    try:
        result = mal_result.build_result(
            tool=TOOL, git_sha=base_cfg["code_commit"], command=command, config={**base_cfg, "verdict": doc["verdict"]}, role="exploration", data_blocks=blocks,
            stage="failed" if doc["verdict"] == "FAIL" else "candidate",
            trades_flat=_trades_for_result(trades, "flat"), trades_pressure_s1=_trades_for_result(trades, "press"), tries=tries,
            n_candidates=counts["n_k4_rows"], n_days=len(days), runtime_s=time.time() - t0,
            notes=f"EXP-013 exploration screen, verdict {doc['verdict']}. Not evidence, no edge claim. {GATE_NOTE}",
        )
        result["verdict"] = doc["verdict"]
        result["gate_note"] = GATE_NOTE
        mal_result.write_result(result_path, result)
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
        try:
            (out / "result_error.txt").write_text(err + "\n", encoding="utf-8")
        finally:
            ledger_finish(ledger_dir, {"event": "result_v1_error", "result_v1_error": err, "verdict": doc["verdict"], "out_dir": str(out)})
        raise SystemExit(f"result.v1 failed ({err}); the screen record is {out}/screen.json (verdict {doc['verdict']}); see result_error.txt. The try is spent.") from exc
    sink = os.environ.get("MISCUSI_RESULT")
    if sink:
        metrics = {"n_selected": len(trades), "n_days": len(days), **{f"item_{it['item']}": it["pass"] for it in doc["items"]}}
        summary = f"EXP-013 grad screen {doc['verdict']}: {len(trades)} selected over {len(days)} days (exploration, no edge claim)"
        Path(sink).write_text(json.dumps({"ok": True, "verdict": doc["verdict"], "metrics": metrics, "summary": summary}) + "\n", encoding="utf-8")
    return doc


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="EXP-013 grad exploration screen (Amendment 5). Runs once. The tries log is MAL_TRIES_LOG or the default.")
    ap.add_argument("--table-run-dir", required=True)
    ap.add_argument("--view-manifest", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--n-jobs", type=int, default=1)
    args = ap.parse_args(argv)
    doc = run(args.table_run_dir, args.view_manifest, args.out_dir, n_jobs=args.n_jobs, command="python -m tools.exp013_grad_screen " + " ".join(argv if argv is not None else sys.argv[1:]))
    print(json.dumps({"verdict": doc["verdict"], "items": {i["item"]: i["pass"] for i in doc["items"]}, "n_selected": doc["n_selected"]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
