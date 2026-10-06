#!/usr/bin/env python3
"""EXP-019: post-migration flow confirmation at k = 8. EXPLORATION ONLY. NO EDGE CLAIM.

Plan (follow it exactly): EXP/EXP-019-postmig-confirm-plan.md.

Modes
  --precount   OUTCOME-BLIND. Loads the EXP-015 cache rows with an object hook that DROPS the net fields (net0, status, sides, p_press) at parse
               time, streams the clean-view tape (PumpSwap rows of the frozen-selected mints, slots [mig_slot, mig_slot + 2] on the migration pool),
               writes features.jsonl and precount.json (coverage, share passing each cell, share with a cached uncensored k8 cell). Refuses (rc 2,
               after writing the report) when a cell keeps 0 % or 100 % of the frozen selection, or any pin / hour check fails.
  (screen)     needs --features (the file --precount wrote; sha256 checked against precount.json) and --tries-log (absolute). Refuses before
               `started` on a pin, coverage, share or missing-k8-cell failure; then takes RUN.lock (O_EXCL), logs two `started` tries (A, B) to the
               ops log and the canonical log, evaluates, writes screen.json / screen.md and the `completed` tries. A second run is refused.

Reuse. Imports tools.exp015_screen and tools.exp017_screen (cache pins, universe, frozen score, bars, Holm, paired bootstrap, tries-log guards).
Only the tape resolver is copied (from tools/exp018_wallet_skill.py at b38f541, PR #424, not merged), marked `# from exp018 (#424)`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from multiprocessing import Pool
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import tools.exp015_screen as e15
import tools.exp017_screen as e17
from tools import mal_result

TOOL = "tools.exp019_postmig"
SCHEMA = "exp019_screen_v1"
FEATURES_SCHEMA = "exp019_features_v1"
REPO_ROOT = Path(__file__).resolve().parent.parent
PLAN = REPO_ROOT / "EXP" / "EXP-019-postmig-confirm-plan.md"
LAMPORTS = 1_000_000_000

# --- pins and thresholds (fixed in the plan before any outcome is read; no level is fit) -------------------------------
DEFAULT_SCRATCH = e17.DEFAULT_SCRATCH
FEATURE_SLOTS = 2  # features use PumpSwap rows with slot in [mig_slot, mig_slot + 2] (manager ruling: same 6-slot decision-to-landing budget as the k6 cell)
ENTRY_K = 8  # the cells enter at k = 8 (cached cell (8, lag 2)); the frozen comparator is (6, lag 2)
EXIT_LAG = 2
K8_CELL = (ENTRY_K, EXIT_LAG)
K6_CELL = e15.PRIMARY_CELL  # (6, 2)
NO_DUMP_MAX_SHARE = 0.05  # cell B: the largest single sell < 5 % of the pool quote reserve (vault + V)
FETCH_MS = 10_000  # tape rows with t in [mig_ms, mig_ms + 10 s] are kept per mint (>> 5 slots); the slot filter decides
MIN_FEATURE_COVERAGE = 0.99  # screen refuses if fewer frozen-selected non-P1 rows have features
CELLS = ("A",)  # Amendment 1: B is dropped (never evaluated, never logged); Holm k = 1
REPORT_CELLS = ("A", "B")  # the precount keeps reporting both shares (outcome-blind, unchanged)
MAX_K8_CENSORED = 5  # Amendment 1: refuse if more than this many frozen-selected rows lack an uncensored k8 cell; they leave BOTH arms
PINNED_FEATURES_SHA256 = "2a9a89743515291cf7a83ffaefa6b6fbd663998fb5070a166d7d59b7606bc769"  # precount #3 (job #322, head bf5776c)
PINNED_PRECOUNT_DIR = "/data/mal/exp019-screen"
CELL_DESC = {
    "A": "frozen EXP-012 selection, entered at k8 (lag 2) only if the first-to-last print price change is > 0 and the net SOL buy flow is > 0 in slots [mig, mig+2]; paired vs frozen at k6",
    "B": "SUPERSEDED by Amendment 1, never evaluated or logged: frozen EXP-012 selection, entered at k8 (lag 2) only if the largest single sell in slots [mig, mig+2] is < 5 % of the pool quote reserve (vault + V); paired vs frozen at k6",
}
FAMILY_ALPHA = 0.05
WORKERS_CAP = 4
NON_P1 = e17.NON_P1
SERIES = {  # from exp018 (#424)
    "S_P2": {"sources": ("P2",), "start": "2026-08-14T12", "end": "2026-08-28T12"},
    "S_P34": {"sources": ("P3", "P4"), "start": "2026-09-03T12", "end": "2026-09-15T12"},
}
DEFAULT_P2_VIEWS = [f"{e15.P2_BASE}/w{i}" for i in range(1, 8)]
DEFAULT_P3_ROOT = "/data/mal/blocks-clean/fresh-0903"
DEFAULT_P4_VIEWS = ["/data/mal/clean-view/exp011-0909/b", "/data/mal/clean-view/exp011-0909/c"]
OUT_SCREEN, OUT_MD, OUT_FEATURES, OUT_PRECOUNT = "screen.json", "screen.md", "features.jsonl", "precount.json"
BANNER = "EXP-019 post-migration confirmation: EXPLORATION ONLY, NO EDGE CLAIM. A pass earns one confirmation read of an unread reserved block under a later pre-registration."


class Refused(Exception):
    """A pre-declared refusal. Before `started`: nothing is logged, no try is spent."""


# --- tape resolver (from exp018 (#424)) -------------------------------------------------------------------------------


@dataclass(frozen=True)
class SplitHours:  # from exp018 (#424)
    """Picklable resolver: hours in `first_hours` resolve through `first`, the rest through `second` (P3 then P4)."""

    first: Any
    second: Any
    first_hours: frozenset

    def __call__(self, h: str) -> dict[str, Any]:
        return (self.first if h in self.first_hours else self.second)(h)


def check_hours_resolve(series: str, hours: Sequence[str], fn: Callable[[str], Mapping[str, Any]]) -> None:  # from exp018 (#424)
    """Every hour of the series must resolve to an existing trades file, or refuse (also in precount)."""
    missing: list[str] = []
    for h in hours:
        try:
            path = Path(fn(h)["trade"])
        except (SystemExit, AssertionError, KeyError) as exc:
            missing.append(f"{h} ({type(exc).__name__}: {exc})")
            continue
        if not path.is_file():
            missing.append(f"{h} ({path})")
    if missing:
        raise Refused(f"series {series}: {len(missing)} of {len(hours)} hours have no trades file (first: {missing[0]})")


def resolve_series(p2_views: Sequence[str], p3_root: str, p4_views: Sequence[str], verify: bool = True, enforce_base: bool = True) -> dict[str, dict[str, Any]]:  # from exp018 (#424)
    """Resolve both series through EXP-015's own guards and loaders (VIEW.sha256, tiling, dedupe manifests, reserved paths). Refuses on any missing hour.
    P3 hours are `trades-<h>.deduped.jsonl.zst` (e15.make_p3_hours)."""
    import tools.exp012_backcheck as bc

    try:
        g2 = e15.guard_p2(p2_views, verify, enforce_base)
        g3 = e15.guard_p3(p3_root, verify, enforce_base)
        g4 = e15.guard_p4(list(p4_views), verify)
    except e15.Refused as exc:
        raise Refused(str(exc)) from None
    if g4 is None:
        raise Refused("S_P34 needs the P4 views (exp011-0909)")
    p3_hours = e15.block_hours("P3")
    p4_hours = list(g4["pool"])
    out = {
        "S_P2": {"hours": list(g2["pool"]), "fn": bc.MultiViewHours(dict(g2["roots"]))},
        "S_P34": {"hours": p3_hours + p4_hours, "fn": SplitHours(e15.make_p3_hours(g3["walkers"]), bc.MultiViewHours(dict(g4["roots"])), frozenset(p3_hours))},
    }
    for k, d in out.items():
        spec = SERIES[k]
        if bc.hours_range(spec["start"], spec["end"]) != d["hours"]:
            raise Refused(f"series {k}: hours are not the contiguous {spec['start']}..{spec['end']} range")
        check_hours_resolve(k, d["hours"], d["fn"])
    return out


def read_trade_file(path: Path | str, hour: str, counters: dict[str, int], needle: bytes | None = None) -> Iterable[dict[str, Any]]:  # from exp018 (#424), + needle prefilter
    """zstd exiting non-zero refuses. Rows whose block_time lies outside the file's hour are dropped and counted. `needle`: a line without this byte
    string is skipped before parsing (a pure speed filter: only lines holding it are ever parsed)."""
    lo = e15.hour_ms(hour) // 1000
    hi = lo + 3600
    path = Path(path)
    proc = None
    if path.name.endswith(".zst"):
        proc = subprocess.Popen(["zstd", "-dc", "-q", str(path)], stdout=subprocess.PIPE)
        assert proc.stdout is not None
        lines: Iterable[Any] = proc.stdout
    else:
        lines = open(path, "rb")
    try:
        for raw in lines:
            raw = raw.strip()
            if not raw or (needle is not None and needle not in raw):
                continue
            try:
                r = json.loads(raw)
            except ValueError:
                counters["bad_json"] = counters.get("bad_json", 0) + 1
                continue
            if not isinstance(r, dict):
                continue
            bt = r.get("block_time")
            if isinstance(bt, int) and not isinstance(bt, bool) and not (lo <= bt < hi):
                counters["out_of_hour_rows"] = counters.get("out_of_hour_rows", 0) + 1
                continue
            yield r
    finally:
        if proc is not None:
            assert proc.stdout is not None
            proc.stdout.close()
            rc = proc.wait()
        else:
            lines.close()  # type: ignore[union-attr]
            rc = 0
    if rc != 0:
        raise Refused(f"zstd exited {rc} on {path}")


# --- features: strictly tape rows on the migration pool, slots [mig_slot, mig_slot + 2] -----------------------------------


def row_ms(r: Mapping[str, Any]) -> int | None:
    """t_recv_ms, else block_time * 1000 (as the EXP-015/016 loaders do for getBlock rows)."""
    t = r.get("t_recv_ms")
    if isinstance(t, int) and not isinstance(t, bool):
        return t
    bt = r.get("block_time")
    if isinstance(bt, int) and not isinstance(bt, bool):
        return bt * 1000
    return None


def _is_swap_wsol(r: Mapping[str, Any]) -> bool:
    bt = r.get("block_time")
    if not isinstance(bt, int) or isinstance(bt, bool):  # rows whose block_time is not an int are dropped
        return False
    return r.get("venue") == "pumpswap" and r.get("quote_is_wsol") is True and r.get("side") in ("buy", "sell") and isinstance(r.get("pool"), str)


def _is_swap_wsol_any(r: Mapping[str, Any]) -> bool:
    """A PumpSwap wSOL row with an int block_time, whether or not it carries side and pool (O1 counts the ones that lack them)."""
    bt = r.get("block_time")
    return r.get("venue") == "pumpswap" and r.get("quote_is_wsol") is True and isinstance(bt, int) and not isinstance(bt, bool)


def _key(r: Mapping[str, Any]) -> tuple[int, int, int, int]:
    return (int(r["_ms"]), int(r.get("slot") or 0), int(r.get("tx_index") or 0), int(r.get("event_index") or 0))


def features_from_rows(rows: Iterable[Mapping[str, Any]], mint: str, mig_ms: int, vmap: Mapping[str, int | None]) -> dict[str, Any]:
    """Features of one mint from its tape rows. The migration print is the first PumpSwap (wSOL) print of the mint at t == the universe row's mig_ms
    (`_Mint.add`, latency_curve.py:732-734, stamps mig_slot / mig_ms on the first PumpSwap print after a bonding print); its slot is mig_slot and its
    pool is the migration pool. Only rows of that pool with slot in [mig_slot, mig_slot + FEATURE_SLOTS] are used: a row at mig_slot + 3 is ignored.
    Pricing is the simulator's own: print_from_trade_row wrapped by the V adapter (post-trade vault + V price), as the cached cells use."""
    from tools.paper_price_path import print_from_trade_row
    from tools.pumpswap_virtual_adapter import make_wrapper

    cand = []
    n_lacking = 0
    for r in rows:
        if r.get("mint") != mint or not _is_swap_wsol_any(r):
            continue
        ms = row_ms(r)
        if ms is None or ms < mig_ms:
            continue
        if not _is_swap_wsol(r):  # lacks side or pool: counted, never used
            n_lacking += int(ms == mig_ms)
            continue
        cand.append({**r, "_ms": ms})
    first = [r for r in cand if r["_ms"] == mig_ms]
    if not first:
        return {"status": "no_mig_print", "n_candidates_lacking_side_or_pool": n_lacking}
    mig = min(first, key=_key)
    multi_slot = len({int(r.get("slot") or 0) for r in first}) > 1
    multi_pool = len({r["pool"] for r in first}) > 1
    mig_slot, pool = int(mig["slot"]), mig["pool"]
    seen: set[tuple[Any, Any]] = set()
    use = []
    for r in sorted(cand, key=_key):
        if r["pool"] != pool or not (mig_slot <= int(r.get("slot") or 0) <= mig_slot + FEATURE_SLOTS):
            continue
        d = (r.get("signature"), r.get("event_index"))
        if r.get("signature") is not None and d in seen:
            continue
        seen.add(d)
        use.append(r)
    v = vmap.get(pool)
    wrapped = make_wrapper(print_from_trade_row, vmap)
    flow = 0
    buyers: set[str] = set()
    max_sell = 0.0
    prices: list[float] = []
    n_used = 0
    for r in use:
        row = dict(r)
        row["t_recv_ms"] = r["_ms"]
        parsed = wrapped(row)
        if parsed is None:
            continue
        n_used += 1
        prices.append(float(parsed[1].price_sol))
        sol = int(r.get("sol_lamports") or 0)
        if r["side"] == "buy":
            flow += sol
            if isinstance(r.get("trader"), str):
                buyers.add(r["trader"])
        else:
            flow -= sol
            try:
                den = int(r["quote_reserve"]) + (int(v) if v is not None and v > 0 else 0)  # pre-trade vault + V (V <= 0 stays vault-only, as the adapter)
            except (KeyError, TypeError, ValueError):
                continue
            if den > 0:
                max_sell = max(max_sell, sol / den)
    if not prices or prices[0] <= 0:
        return {"status": "no_price", "n_candidates_lacking_side_or_pool": n_lacking}
    return {"status": "ok", "mig_slot": mig_slot, "pool": pool, "n_prints": n_used, "net_flow_lamports": flow, "n_buyers": len(buyers),
            "price_change": prices[-1] / prices[0] - 1.0, "max_sell_share": max_sell, "pool_has_v": bool(v is not None and v > 0),
            "mig_ms_multi_slot": multi_slot, "mig_ms_multi_pool": multi_pool, "n_candidates_lacking_side_or_pool": n_lacking}


def confirm_a(f: Mapping[str, Any] | None) -> bool:
    """(a) momentum: price change > 0 and net flow > 0. Missing features never confirm."""
    return bool(f and f.get("status") == "ok" and f["price_change"] > 0 and f["net_flow_lamports"] > 0)


def confirm_b(f: Mapping[str, Any] | None) -> bool:
    """(b) no dump: the largest single sell is < 5 % of the pool quote reserve (no sell at all passes). Missing features never confirm."""
    return bool(f and f.get("status") == "ok" and f["max_sell_share"] < NO_DUMP_MAX_SHARE)


def _hour_key(ms: int) -> str:
    return time.strftime("%Y-%m-%dT%H", time.gmtime(ms // 1000))


def _hour_task(task: tuple[str, str, dict[str, int]]) -> tuple[str, dict[str, list[dict[str, Any]]], dict[str, int]]:
    h, path, wanted = task
    counters: dict[str, int] = {}
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in read_trade_file(path, h, counters, needle=b'"pumpswap"'):
        m = r.get("mint")
        mig = wanted.get(m) if isinstance(m, str) else None
        if mig is None or not _is_swap_wsol_any(r):  # rows lacking side or pool are kept so features_from_rows can count them (O1)
            continue
        ms = row_ms(r)
        if ms is not None and mig <= ms <= mig + FETCH_MS:
            out[m].append(r)
    return h, dict(out), counters


def build_features(wanted: Mapping[str, int], series_hours: Sequence[str], fn: Callable[[str], Mapping[str, Any]], vmap: Mapping[str, int | None], workers: int = 1,
                   progress: Callable[[str], None] | None = None) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    """mint -> features for `wanted` (mint -> mig_ms). A mint whose needed hours (the hour of mig_ms and of mig_ms + 10 s) are not all in the series
    is `truncated_window` (no features); a mint with no tape row at mig_ms is `no_mig_print`."""
    hours = set(series_hours)
    per_hour: dict[str, dict[str, int]] = defaultdict(dict)
    status: dict[str, dict[str, Any]] = {}
    for m, mig in wanted.items():
        need = {_hour_key(mig - 60_000), _hour_key(mig), _hour_key(mig + FETCH_MS)}
        if not need <= hours:
            status[m] = {"status": "truncated_window"}
            continue
        for h in need:
            per_hour[h][m] = mig
    tasks = [(h, str(fn(h)["trade"]), w) for h, w in sorted(per_hour.items())]
    rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    counters: dict[str, int] = {}
    it: Iterable[Any]
    pool = None
    if workers > 1:
        pool = Pool(min(workers, WORKERS_CAP))
        it = pool.imap(_hour_task, tasks)
    else:
        it = map(_hour_task, tasks)
    try:
        for h, got, c in it:
            for m, rs in got.items():
                rows[m].extend(rs)
            for k, n in c.items():
                counters[k] = counters.get(k, 0) + n
            if progress:
                progress(h)
    finally:
        if pool is not None:
            pool.close()
            pool.join()
    for m, mig in wanted.items():
        if m not in status:
            status[m] = features_from_rows(rows.get(m, ()), m, mig, vmap)
    return status, counters


# --- selection, shares, refusals -------------------------------------------------------------------------------------------


def non_p1(u: Mapping[str, Any]) -> bool:
    return u["block"] != "P1"


def selected_rows(universe: Sequence[Mapping[str, Any]], scores: Sequence[float]) -> list[int]:
    """Frozen-selected non-P1 rows (score >= THR90). This is every cell's scope: the rows the frozen book would trade."""
    return [i for i, (u, s) in enumerate(zip(universe, scores)) if non_p1(u) and s >= e17.THR90]


def k8_missing(universe: Sequence[Mapping[str, Any]], rows: Sequence[int]) -> int:
    """Selected rows lacking an uncensored (8, lag 2) cell (keys only: presence and `censored`; no net field is read)."""
    n = 0
    for i in rows:
        c = universe[i]["cells"].get(K8_CELL)
        n += int(c is None or bool(c.get("censored")))
    return n


def k6_missing(universe: Sequence[Mapping[str, Any]], rows: Sequence[int]) -> int:
    """Selected rows lacking an uncensored (6, lag 2) cell. The universe is built from uncensored primary cells, so this must be 0 (O6)."""
    n = 0
    for i in rows:
        c = universe[i]["cells"].get(K6_CELL)
        n += int(c is None or bool(c.get("censored")))
    return n


def share_report(universe: Sequence[Mapping[str, Any]], rows: Sequence[int], feats: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"n_selected_non_p1": len(rows), "by_source": {}}
    for s in NON_P1:
        rs = [i for i in rows if universe[i]["source"] == s]
        fs = [feats.get(universe[i]["mint"]) for i in rs]
        d: dict[str, Any] = {"n_selected": len(rs), "with_features": sum(1 for f in fs if f and f.get("status") == "ok"),
                             "status": {}, "pass_A": sum(confirm_a(f) for f in fs), "pass_B": sum(confirm_b(f) for f in fs),
                             "with_uncensored_k8_cell": len(rs) - k8_missing(universe, rs)}
        for f in fs:
            k = (f or {}).get("status", "absent")
            d["status"][k] = d["status"].get(k, 0) + 1
        d["coverage"] = d["with_features"] / len(rs) if rs else None
        out["by_source"][s] = d
    fs_all = [feats.get(universe[i]["mint"]) for i in rows]
    out["with_features"] = sum(1 for f in fs_all if f and f.get("status") == "ok")
    out["coverage"] = out["with_features"] / len(rows) if rows else None
    for c, fn in ((c, {"A": confirm_a, "B": confirm_b}[c]) for c in REPORT_CELLS):
        n = sum(fn(f) for f in fs_all)
        out[f"pass_{c}"] = n
        out[f"share_{c}"] = n / len(rows) if rows else None
    out["n_without_k8_cell"] = k8_missing(universe, rows)
    out["n_without_k6_cell"] = k6_missing(universe, rows)
    for c, fn in ((c, {"A": confirm_a, "B": confirm_b}[c]) for c in REPORT_CELLS):
        ds = sorted({universe[i]["date"] for i in rows if fn(feats.get(universe[i]["mint"]))})
        out[f"dates_with_entered_row_{c}"] = {"n": len(ds), "of": len({universe[i]["date"] for i in rows}), "dates": ds}
    ok = [f for f in fs_all if f and f.get("status") == "ok"]
    out["feature_summary"] = {"n_ok": len(ok), "n_pool_without_v": sum(1 for f in ok if not f["pool_has_v"]),
                              "n_one_print_only": sum(1 for f in ok if f["n_prints"] <= 1), "n_with_a_sell": sum(1 for f in ok if f["max_sell_share"] > 0),
                              "n_mig_ms_multi_slot": sum(1 for f in ok if f.get("mig_ms_multi_slot")), "n_mig_ms_multi_pool": sum(1 for f in ok if f.get("mig_ms_multi_pool")),
                              "n_candidates_lacking_side_or_pool": sum(int((f or {}).get("n_candidates_lacking_side_or_pool", 0)) for f in fs_all),
                              "median_n_buyers": sorted(f["n_buyers"] for f in ok)[len(ok) // 2] if ok else None}
    return out


def check_shares(rep: Mapping[str, Any]) -> None:
    """Refuse when a cell keeps 0 % or 100 % of the frozen selection (it would test nothing)."""
    if not rep["n_selected_non_p1"]:
        raise Refused("zero frozen-selected non-P1 rows")
    bad = [f"{c}: {rep['pass_' + c]} of {rep['n_selected_non_p1']}" for c in CELLS if rep[f"pass_{c}"] in (0, rep["n_selected_non_p1"])]
    if bad:
        raise Refused(f"share refusal (a cell keeps 0 % or 100 % of the frozen selection): {bad}")


def check_screen_ready(rep: Mapping[str, Any]) -> None:
    check_shares(rep)
    if rep["n_without_k6_cell"]:
        raise Refused(f"{rep['n_without_k6_cell']} frozen-selected row(s) lack an uncensored (6, lag 2) cell")
    if rep["n_without_k8_cell"] > MAX_K8_CENSORED:
        raise Refused(f"{rep['n_without_k8_cell']} frozen-selected row(s) lack an uncensored (8, lag 2) cell (more than {MAX_K8_CENSORED}); up to {MAX_K8_CENSORED} leave both arms (Amendment 1)")
    if (rep["coverage"] or 0.0) < MIN_FEATURE_COVERAGE:
        raise Refused(f"feature coverage {rep['coverage']} of the frozen-selected non-P1 rows is below {MIN_FEATURE_COVERAGE}")


# --- evaluation (bars from exp017/exp015; the k8 cell replaces the primary cell in a view of the universe) --------------------


def view_k8(universe: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The universe with each row's primary cell replaced by its (8, 2) cell, so exp017.evaluate_cell books the k8 trades (net, filled flag)."""
    return [{**u, "cells": {K6_CELL: u["cells"].get(K8_CELL)}} for u in universe]


def cell_nets_k8(universe: Sequence[Mapping[str, Any]], enter: Sequence[bool]) -> list[dict[str, float] | None]:
    return [e15.cell_nets(u["cells"].get(K8_CELL)) if en else None for u, en in zip(universe, enter)]


MISS_SHARE_MAX = 0.5  # reading rule: > 50 % of the positive paired sum from k8-MISS rows on either leg => MISS-driven


def k8_filled(u: Mapping[str, Any]) -> bool:
    c = u["cells"].get(K8_CELL)
    return bool(c is not None and not c.get("censored") and c.get("filled"))


def miss_breakdown(universe: Sequence[Mapping[str, Any]], v8: Sequence[Mapping[str, Any]], nets: Sequence[Mapping[str, float] | None], fnets: Sequence[Mapping[str, float] | None],
                   rows: Sequence[int]) -> dict[str, Any]:
    """Report-only per cell and leg (the reading rule below uses `positive_sum_share_from_miss`). Entered rows whose k8 cell is a MISS book -fee, so x > 0
    wherever frozen k6 lost: that is not confirmation information. Per leg: n entered k8-MISS rows; sum of x on rows with k8 MISS and k6 filled; the
    positive paired sum (sum of x over rows with x > 0) and its share from entered k8-MISS rows; paired stats on the rows left after removing the
    entered k8-MISS rows (k8-filled entered rows plus the rows the cell skips)."""
    miss_rows = [i for i in rows if nets[i] is not None and not k8_filled(universe[i])]
    miss_set = set(miss_rows)
    out: dict[str, Any] = {"n_entered_k8_miss": len(miss_rows), "n_entered": sum(1 for i in rows if nets[i] is not None)}
    for leg in e15.LEGS:
        x = {i: (nets[i][leg] if nets[i] is not None else 0.0) - (fnets[i][leg] if fnets[i] is not None else 0.0) for i in rows}
        pos = sum(v for v in x.values() if v > 0)
        pos_miss = sum(v for i, v in x.items() if i in miss_set and v > 0)
        out[leg] = {"sum_x_k8_miss_k6_filled_sol": sum(x[i] for i in miss_rows if bool(universe[i]["cells"][K6_CELL].get("filled"))) / LAMPORTS,
                    "sum_x_k8_miss_sol": sum(x[i] for i in miss_rows) / LAMPORTS,
                    "positive_sum_sol": pos / LAMPORTS, "positive_sum_from_miss_sol": pos_miss / LAMPORTS,
                    "positive_sum_share_from_miss": (pos_miss / pos) if pos > 0 else None}
    rest = [i for i in rows if i not in miss_set]
    out["paired_without_k8_miss_rows"] = e17.paired(v8, nets, fnets, rest)
    return out


def miss_driven(bd: Mapping[str, Any]) -> bool:
    """Pre-declared rule: on either leg, more than 50 % of the positive paired sum comes from k8-MISS rows."""
    return any((bd[leg]["positive_sum_share_from_miss"] or 0.0) > MISS_SHARE_MAX for leg in e15.LEGS)


def decide(res: Mapping[str, Mapping[str, Any]], hm: Mapping[str, Mapping[str, Any]]) -> str:
    passing = [c for c in hm if hm[c]["reject"] and res[c]["bars_all"]]
    notes = []
    wins = []
    for c in passing:
        if miss_driven(res[c]["miss_breakdown"]):
            notes.append(f"{c}: MISS-driven -- earns nothing")
        else:
            wins.append(c)
    tail = (" " + "; ".join(notes) + ".") if notes else ""
    if not wins:
        return "SCREEN NONE: no cell is Holm-significant with bars 1-6 passing and not MISS-driven. Nothing goes to confirmation." + tail
    return f"SCREEN PASS: {', '.join(wins)} clear Holm and bars 1-6. This means 'worth one confirmation read', never 'has an edge'." + tail


def run_screen(universe: Sequence[Mapping[str, Any]], scores: Sequence[float], feats: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    all_sel = selected_rows(universe, scores)
    # Amendment 1: rows lacking an uncensored k8 cell (a tape-end timing property, outcome-blind) leave BOTH arms: x is computed only where both are defined.
    excluded = [i for i in all_sel if universe[i]["cells"].get(K8_CELL) is None or universe[i]["cells"][K8_CELL].get("censored")]
    ex = set(excluded)
    rows = [i for i in all_sel if i not in ex]
    sel = set(rows)
    frozen_nets = [e15.cell_nets(u["cells"].get(K6_CELL)) if i in sel else None for i, u in enumerate(universe)]
    v8 = view_k8(universe)
    enter = {"A": [i in sel and confirm_a(feats.get(u["mint"])) for i, u in enumerate(universe)],
             "ref_k8_all": [i in sel for i in range(len(universe))]}
    res: dict[str, Any] = {}
    for c in (*CELLS, "ref_k8_all"):
        nets = cell_nets_k8(universe, enter[c])
        res[c] = e17.evaluate_cell(v8, nets, frozen_nets, rows)
        res[c]["n_entered"] = sum(enter[c])
        res[c]["miss_breakdown"] = miss_breakdown(universe, v8, nets, frozen_nets, rows)
    hm = e17.holm({c: res[c]["p"] for c in CELLS}, FAMILY_ALPHA)
    for c in CELLS:
        res[c]["bars"]["B2"]["pass"] = bool(res[c]["bars"]["B2"]["pass"] and hm[c]["reject"])  # B2 includes Holm significance
        res[c]["bars_all"] = all(b["pass"] for b in res[c]["bars"].values())
        res[c]["miss_driven"] = miss_driven(res[c]["miss_breakdown"])
    frozen_all = e17.evaluate_cell(universe, frozen_nets, frozen_nets, rows)  # the k6 comparator book (paired with itself: x = 0)
    return {"cells": {c: res[c] for c in CELLS}, "holm": hm, "report_only": {"frozen_k6_book": frozen_all["bars"]["B1"]["report"], "frozen_all_at_k8": res["ref_k8_all"]["bars"]["B1"]["report"],
                                                                         "frozen_all_at_k8_miss_breakdown": res["ref_k8_all"]["miss_breakdown"]},
            "n_scope_rows": len(rows), "n_selected_before_exclusion": len(all_sel),
            "excluded_k8_censored": {"n": len(excluded), "by_source": {sc: sum(1 for i in excluded if universe[i]["source"] == sc) for sc in NON_P1}}, "outcome": decide(res, hm), "caveats": list(CAVEATS)}


CAVEATS = (
    "Exploration. Cells reuse the 27 non-P1 dates EXP-015, EXP-017 and EXP-018 already looked at; Holm k = 1 (cell A alone; B was dropped by Amendment 1).",
    "The paired mean x is per frozen-selected migration with an uncensored k8 cell (x = cell net at k8 - frozen net at k6; a row the cell does not enter scores -frozen net).",
    "Features use slots up to mig_slot + 2 and the entry lands at mig_slot + 8: a decision-to-landing budget of 8 - 2 = 6 slots, the same as the frozen k6 cell (decision at the migration, landing 6 slots later).",
    "A k8 MISS is booked as -fee (cell_nets). On a row where k8 MISSes and frozen k6 lost, x is positive without any confirmation information. The report-only MISS breakdown and the MISS-driven rule (plan section 7) guard against passing on that.",
    "The k8 cells are the cached EXP-015 cells (V pin, lag 2, haircut, 505k per side, 0.05 SOL, both fail models); SLIPPAGE_CAP 0.15 against the migration-slot price still applies and can turn a later entry into a MISS.",
    "P1 dates are neither featured nor scored.",
)


# --- tries, lock, outputs ----------------------------------------------------------------------------------------------------


def prior_exp019_lines(log: Path) -> list[dict[str, Any]]:
    out = []
    if Path(log).is_file():
        for line in Path(log).read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("tool") == TOOL or str((rec.get("config") or {}).get("key", "")).startswith("exp019_"):
                out.append(rec)
    return out


def check_no_prior_tries(*logs: Path) -> None:
    seen: set[Path] = set()
    for lg in logs:
        r = Path(lg).resolve()
        if r in seen:
            continue
        seen.add(r)
        prior = prior_exp019_lines(r)
        if prior:
            raise Refused(f"{len(prior)} earlier exp019 line(s) in {r}: a second run is refused")


def log_cell_tries(log: Path, out_dir: Path, status: str) -> dict[str, dict[str, Any]]:
    """`started`: one line per cell on the bookkeeping block. Other statuses: one line per (cell, pool group) (as exp017)."""
    groups = {"universe": e15.UNIVERSE_BLOCKS} if status == "started" else e15.pool_group_blocks(True)
    info: dict[str, dict[str, Any]] = {}
    for c in CELLS:
        info[c] = {"groups": {}}
        for g, blocks in groups.items():
            ret = mal_result.append_try(
                log, tool=TOOL,
                config={"key": f"exp019_{c.lower()}", "experiment": "EXP-019", "cell": c, "status": status, "pool_group": g, "desc": CELL_DESC[c], "k": ENTRY_K, "exit_lag": EXIT_LAG,
                        "fee_lamports": e15.FEE, "pricing": "V"},
                data_blocks=list(blocks), result_path=out_dir / OUT_SCREEN, role="exploration")
            info[c]["groups"][g] = ret
            info[c].update(ret)
    return info


def log_cell_tries_all(logs: Sequence[Path], out_dir: Path, status: str) -> None:
    seen: set[Path] = set()
    for lg in logs:
        if Path(lg).resolve() in seen:
            continue
        seen.add(Path(lg).resolve())
        log_cell_tries(lg, out_dir, status)


def write_json(path: Path, obj: Any) -> None:
    e17.write_json(path, obj)


def features_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_features(path: Path, feats: Mapping[str, Mapping[str, Any]]) -> str:
    lines = [json.dumps({"mint": m, **f}, sort_keys=True) for m, f in sorted(feats.items())]
    Path(path).write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return features_sha256(path)


def check_features_pin(features: Path, pin_sha: str = PINNED_FEATURES_SHA256, pin_dir: str = PINNED_PRECOUNT_DIR) -> str:  # as exp018's check_features_pin
    """The screen refuses unless --features is the pinned precount's file, by location and by sha256."""
    f = Path(features)
    if f.resolve() != (Path(pin_dir) / OUT_FEATURES).resolve():
        raise Refused(f"--features {f} is not the pinned {Path(pin_dir) / OUT_FEATURES}")
    got = features_sha256(f)
    if got != pin_sha:
        raise Refused(f"features sha256 {got} != pinned {pin_sha}")
    return got


def load_features(features: Path, out_dir: Path) -> dict[str, dict[str, Any]]:
    """The file --precount wrote; its sha256 must equal the one in precount.json (so the screen reads exactly the outcome-blind build)."""
    pre = Path(out_dir) / OUT_PRECOUNT
    try:
        want = json.loads(pre.read_text(encoding="utf-8"))["features_sha256"]
    except (OSError, ValueError, KeyError):
        raise Refused(f"{pre} missing or unreadable: run --precount first") from None
    if features_sha256(features) != want:
        raise Refused(f"{features} sha256 differs from precount.json")
    out = {}
    for line in Path(features).read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            out[d.pop("mint")] = d
    return out


def render_md(rep: Mapping[str, Any]) -> str:
    L = [f"# EXP-019 post-migration confirmation screen", "", f"**{BANNER}**", "", f"Outcome: {rep['outcome']}", "",
         "| Cell | n entered | n non-P1 trades | p (paired) | Holm threshold | Holm reject | B1 | B2 | B3 | B4 | B5 | B6 | all bars |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for c in CELLS:
        r, h = rep["cells"][c], rep["holm"][c]
        L.append(f"| {c} | {r['n_entered']} | {r['n_trades_non_p1']} | {e17._fmt(h['p'])} | {h['threshold']:.4f} | {h['reject']} | " + " | ".join(str(r["bars"][f"B{i}"]["pass"]) for i in range(1, 7)) + f" | {r['bars_all']} |")
    L += ["", "Report-only: " + json.dumps(rep["report_only"], default=str)[:2000], "", "## Caveats"] + [f"- {x}" for x in rep["caveats"]]
    return "\n".join(L) + "\n"


# --- CLI ----------------------------------------------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scratch", default=DEFAULT_SCRATCH)
    ap.add_argument("--out-dir", type=Path, default=Path("/data/mal/exp019-screen"))
    ap.add_argument("--tries-log", default=None, help="screen mode: absolute ops log, e.g. /data/mal/ops/tries-exp019-screen.jsonl")
    ap.add_argument("--canonical-tries", type=Path, default=e17.CANONICAL_TRIES)
    ap.add_argument("--features", type=Path, default=None, help="screen mode: the features.jsonl --precount wrote")
    ap.add_argument("--vmap", default=e15.VMAP_0909_PATH)
    ap.add_argument("--artifact-dir", type=Path, default=None)
    ap.add_argument("--p2-view", action="append", default=None)
    ap.add_argument("--p3-root", default=DEFAULT_P3_ROOT)
    ap.add_argument("--p4-view", action="append", default=None)
    ap.add_argument("--workers", type=int, default=WORKERS_CAP)
    ap.add_argument("--precount", action="store_true")
    return ap


def common_guards(args: argparse.Namespace) -> None:
    e17.check_manifests(args.scratch)
    e17.check_cache_heads(args.scratch)
    e15.check_vmap(args.vmap, e15.VMAP_0909_SHA256, "V map")
    if args.workers < 1 or args.workers > WORKERS_CAP:
        raise Refused(f"--workers {args.workers}: 1..{WORKERS_CAP}")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    out_dir: Path = args.out_dir
    tries_path = canonical = None
    try:
        common_guards(args)
        universe, _stats = e17.load_universe(args.scratch, blind=bool(args.precount))
        scores = e17.frozen_scores(universe, args.artifact_dir)
        rows = selected_rows(universe, scores)
        if args.precount:
            if args.tries_log is not None:
                raise Refused("--precount reads no tries log; do not pass --tries-log")
            series = resolve_series(args.p2_view or DEFAULT_P2_VIEWS, args.p3_root, args.p4_view or DEFAULT_P4_VIEWS)
            from tools.pumpswap_virtual import load_map

            vmap = load_map(Path(args.vmap))
            feats: dict[str, dict[str, Any]] = {}
            counters: dict[str, int] = {}
            for name, spec in SERIES.items():
                wanted = {universe[i]["mint"]: int(universe[i]["mig_ms"]) for i in rows if universe[i]["source"] in spec["sources"]}
                f, c = build_features(wanted, series[name]["hours"], series[name]["fn"], vmap, args.workers, progress=lambda h, n=name: print(f"[{n}] {h}", file=sys.stderr, flush=True))
                feats.update(f)
                for k, v in c.items():
                    counters[k] = counters.get(k, 0) + v
            out_dir.mkdir(parents=True, exist_ok=True)
            rep = share_report(universe, rows, feats)
            sha = write_features(out_dir / OUT_FEATURES, feats)
            write_json(out_dir / OUT_PRECOUNT, {"mode": "precount", "outcome_blind": True, "features_sha256": sha, "reader": counters, "report": rep, "thr90": e17.THR90,
                                                "feature_slots": FEATURE_SLOTS, "no_dump_max_share": NO_DUMP_MAX_SHARE})
            print(json.dumps({"mode": "precount", "outcome_blind": True, "features_sha256": sha, "report": rep}, indent=2, default=str))
            check_screen_ready(rep)  # printed first, then refuses (rc 2) on 0 % / 100 % shares, a missing k8 cell or low coverage
            return 0
        tries_path = e17.check_ops_tries_log(args.tries_log)
        canonical = Path(args.canonical_tries).resolve()
        e17.check_canonical_tries(canonical)
        logs = [tries_path, canonical]
        check_no_prior_tries(*logs)
        if args.features is None:
            raise Refused("screen mode needs --features (the file --precount wrote)")
        check_features_pin(args.features)
        feats = load_features(args.features, out_dir)
        rep = share_report(universe, rows, feats)
        check_screen_ready(rep)  # includes: every frozen-selected row has an uncensored (8, 2) cell
        e15.check_run_lock(out_dir)
        check_no_prior_tries(*logs)  # re-checked right before the spend point
        head = e15.git_state()["head"]
        e15.take_lock(out_dir, head, hashlib.sha256(json.dumps(sorted(vars(args).items(), key=lambda kv: kv[0]), default=str).encode()).hexdigest())
    except (Refused, e17.Refused, e15.Refused) as exc:
        print(f"refusing (before started, no tries line): {exc}", file=sys.stderr)
        return 2
    assert tries_path is not None and canonical is not None
    started = False
    status = "aborted_after_read"
    try:
        log_cell_tries_all([tries_path, canonical], out_dir, "started")
        started = True
        res = run_screen(universe, scores, feats)
        res.update({"schema": SCHEMA, "head": head, "shares": rep, "disclosures": {"cache_code_sha": e17.CACHE_CODE_SHA, "prior_tries_per_pool": e17.prior_tries_per_pool(canonical)}})
        write_json(out_dir / OUT_SCREEN, res)
        (out_dir / OUT_MD).write_text(render_md(res), encoding="utf-8")
        log_cell_tries_all([tries_path, canonical], out_dir, "completed")
        status = "completed"
        print(res["outcome"])
        return 0
    except Exception as exc:  # noqa: BLE001 - tries are spent after `started`; record and stop
        print(f"aborted after started: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    finally:
        e15.write_record(out_dir, status, started, {c: status for c in CELLS})


if __name__ == "__main__":
    raise SystemExit(main())
