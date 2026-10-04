#!/usr/bin/env python3
"""EXP-012 under PumpSwap virtual-quote pricing: a CORRECTION ANALYSIS, not a new read.

No frozen file is edited. Everything runs through `tools.pumpswap_virtual_adapter` (V added to PumpSwap
prints in-process, restored after). Outputs go to /data/mal/exp012-virtual-rescore/<run-id>/.

Subcommands
  pools     collect the PumpSwap pools of migrated mints in the given clean views -> pools.json
  fetch-v   getMultipleAccounts the pools once (<= 5 rps) -> /data/mal/pumpswap-virtual/pool_v.json
  validate  real buys/sells vs the paper quote with and without V; fee-tier evidence
  rescore   (a) exploration OOF book (nested LODO selection, re-priced), (b) EXP-012 read block (re-priced)

Selection is NOT refit: the entered set is the frozen one (same model, same threshold, same features,
which exclude the lookahead features); only the pricing of fills, tp/sl marks and exits changes.
The read lock for the spent block is not touched; this module never calls tools.exp012_score.main.
The FORWARD directories (/data/mal/blocks/forward-1002, /data/mal/exp012-forward*) are never opened.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

OUT_ROOT = Path("/data/mal/exp012-virtual-rescore")
VIEW_A = "/data/mal/clean-view/fast-pool-2026-09-18T23_2026-09-22T00"
VIEW_EXPLORE0814 = "/data/mal/clean-view/explore-0814/w1"
HOLDOUT_CLEAN = "/data/mal/blocks-clean/fresh-0903"
HOLDOUT_RAW = "/data/mal/blocks/fresh-0903"
TABLE = Path("/data/mal/exp012/table.jsonl")
FROZEN_SCRATCH = Path("/data/mal/exp012/read/scratch")
ARTIFACT_DIR = Path(__file__).resolve().parents[1] / "ARTIFACTS" / "exp012"
FORBIDDEN = ("/data/mal/blocks/forward-1002", "/data/mal/exp012-forward")


def _guard(path: str | Path) -> Path:
    for f in FORBIDDEN:
        if str(path).startswith(f):
            raise SystemExit(f"refusing to open {path}: forward files are under DEC-016 Amendment 2's no-peek rule")
    return Path(path)


def _zcat_lines(path: Path, needle: str, limit: int | None = None) -> Iterable[str]:
    p1 = subprocess.Popen(["zstdcat", str(path)], stdout=subprocess.PIPE)
    p2 = subprocess.Popen(["grep", "-F", needle], stdin=p1.stdout, stdout=subprocess.PIPE, text=True)
    assert p1.stdout is not None
    p1.stdout.close()
    n = 0
    assert p2.stdout is not None
    try:
        for line in p2.stdout:
            yield line
            n += 1
            if limit and n >= limit:
                break
    finally:
        p2.kill()
        p1.kill()
        p2.wait()
        p1.wait()


# --- stats helpers -----------------------------------------------------------------


def bps_stats(errs: Sequence[float]) -> dict[str, Any]:
    if not errs:
        return {"n": 0}
    s = sorted(errs)
    q = lambda p: s[min(len(s) - 1, int(p * len(s)))]  # noqa: E731
    return {
        "n": len(s), "median_bps": statistics.median(s), "mean_bps": statistics.fmean(s), "p05_bps": q(0.05), "p95_bps": q(0.95),
        "min_bps": s[0], "max_bps": s[-1], "mean_abs_bps": statistics.fmean(abs(x) for x in s),
        "share_abs_lt_1bps": sum(1 for x in s if abs(x) < 1) / len(s), "share_abs_lt_10bps": sum(1 for x in s if abs(x) < 10) / len(s),
    }


# --- validation ----------------------------------------------------------------------


def validate_rows(rows: Sequence[dict[str, Any]], vmap: dict[str, int | None]) -> dict[str, Any]:
    """Real PumpSwap rows vs the paper quote, with V and without.

    Sells: `sol_lamports` is the user's SOL out, so the paper quote (`paper_curve_math.quote_sell`, portal fee 0)
    is compared with it directly, on the row's own pre-trade reserves ("pre") and on the reserves the paper path
    would hold (the previous print of the same pool, posted, "chain").
    Buys: the tape's `sol_lamports` is NOT a consistent gross spend (it is net of fees on some rows), so a
    paper-form quote from it is not a clean test. Instead the fee-free identity is used: on pre-trade reserves
    the net quote that bought `token_raw` is net = token_raw * (Q + V) / (B - token_raw); the implied fee is
    sol_lamports / net - 1. It must sit on a known convention (0, the flat 30 bps non-canonical tier, or the
    canonical tier at mcap) to within rounding. Reported as the residual to the nearest convention, with V and
    with V = 0. Rows are split by pool: V > 0 (migrated pump.fun pools) and V == 0 (native pools: control)."""
    from tools import paper_curve_math as pcm
    from tools.paper_price_path import print_from_trade_row

    keys = ("sell_pre_V", "sell_pre_noV", "sell_chain_V", "sell_chain_noV", "buy_fee_resid_V", "buy_fee_resid_noV")
    out: dict[str, dict[str, list[float]]] = {g: {k: [] for k in keys} for g in ("v_pos", "v_zero")}
    tier: dict[str, int] = {"differs": 0, "match_V": 0, "match_vault": 0, "neither_within_3bps": 0}
    last: dict[str, tuple[int, int]] = {}
    n_missing = n_rows = 0
    for row in rows:
        pool = row.get("pool")
        if row.get("venue") != "pumpswap" or row.get("quote_is_wsol") is not True or not isinstance(pool, str):
            continue
        n_rows += 1
        v = vmap.get(pool)
        if v is None:
            n_missing += 1
            continue
        g = out["v_pos" if v > 0 else "v_zero"]
        try:
            q, b, spend, tok = int(row["quote_reserve"]), int(row["base_reserve"]), int(row["sol_lamports"]), int(row["token_raw"])
        except (KeyError, TypeError, ValueError):
            continue
        side = row.get("side")
        if min(q, b, spend, tok) <= 0 or side not in ("buy", "sell") or tok >= b:
            continue
        mc_vault = q / (b * 1000) * 1e9
        mc_v = (q + v) / (b * 1000) * 1e9
        t_v, t_vault = pcm.pumpswap_sol_fee_ppm(mc_v) / 100, pcm.pumpswap_sol_fee_ppm(mc_vault) / 100
        if side == "sell":
            for label, qq, mc in (("V", q + v, mc_v), ("noV", q, mc_vault)):
                o = pcm.quote_sell(venue="pumpswap", tokens_raw=tok, quote_lamports=qq, base_raw=b, market_cap=mc, portal_fee_ppm=0)
                if o:
                    g[f"sell_pre_{label}"].append((o - spend) / spend * 1e4)
        else:
            for label, vv, cands in (("V", v, (0.0, 30.0, t_v)), ("noV", 0, (0.0, 30.0, t_vault))):
                net = tok * (q + vv) / (b - tok)
                if net > 0 and spend > 4:
                    imp = (spend / net - 1) * 1e4
                    g[f"buy_fee_resid_{label}"].append(min((imp - c for c in cands), key=abs))
            if v > 0 and t_v != t_vault and spend > 1000:
                net = tok * (q + v) / (b - tok)
                imp = (spend / net - 1) * 1e4
                tier["differs"] += 1
                dv, dn = abs(imp - t_v), abs(imp - t_vault)
                if min(dv, dn) > 3:
                    tier["neither_within_3bps"] += 1
                elif dv < dn:
                    tier["match_V"] += 1
                else:
                    tier["match_vault"] += 1
        prev = last.get(pool)
        post = print_from_trade_row(dict(row, t_recv_ms=int(row.get("t_recv_ms") or (row.get("block_time") or 0) * 1000)))
        if side == "sell" and prev is not None and prev[1] == b:  # consecutive: base moved exactly by the previous token_raw
            for label, qq in (("V", prev[0] + v), ("noV", prev[0])):
                o = pcm.quote_sell(venue="pumpswap", tokens_raw=tok, quote_lamports=qq, base_raw=b, market_cap=qq / (b * 1000) * 1e9, portal_fee_ppm=0)
                if o:
                    g[f"sell_chain_{label}"].append((o - spend) / spend * 1e4)
        if post is not None:
            last[pool] = (post[1].quote_reserve, post[1].base_reserve)  # post-trade vault reserves, as the paper path holds them
    return {"n_pumpswap_rows": n_rows, "n_missing_v": n_missing, "dist_bps": {grp: {k: bps_stats(v_) for k, v_ in d.items()} for grp, d in out.items()}, "fee_tier_vs_implied": tier}


def cmd_validate(a: argparse.Namespace) -> int:
    from tools.pumpswap_virtual import build_map, load_map, save_map

    run = OUT_ROOT / a.run_id
    run.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for root, tag in ((VIEW_A, "A"), (VIEW_EXPLORE0814, "E")):
        files = sorted((Path(root) / "trades").glob("trades-*.jsonl.zst"))
        step = max(1, len(files) // a.hours_per_view)
        for f in files[step // 2 :: step][: a.hours_per_view]:
            got = [json.loads(line) for line in _zcat_lines(f, '"venue":"pumpswap"', a.rows_per_hour)]
            rows.extend(got)
            print(f"validate sample {tag} {f.name}: {len(got)} pumpswap rows", file=sys.stderr, flush=True)
    pools = sorted({r["pool"] for r in rows if isinstance(r.get("pool"), str)})
    vmap = load_map(Path(a.vmap)) if Path(a.vmap).exists() else {}
    need = [p for p in pools if p not in vmap]
    calls = 0
    if need:
        vmap, calls = build_map(need, existing=vmap)
        save_map(Path(a.vmap), vmap, calls)
    res = validate_rows(rows, vmap)
    res.update({"sample_pools": len(pools), "fetch_calls_this_step": calls, "views": [VIEW_A, VIEW_EXPLORE0814]})
    (run / "validation.json").write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(res, indent=1))
    return 0


# --- pools / fetch -----------------------------------------------------------------------


def _migrated_mints(root: Path, dedup: bool) -> set[str]:
    out: set[str] = set()
    pat = "migrations-*.jsonl.zst"
    for f in sorted((root / "migrations").glob(pat)):
        for line in _zcat_lines(f, '"complete"'):
            try:
                out.add(json.loads(line)["mint"])
            except (ValueError, KeyError):
                pass
    return out


def _pools_from_file(args: tuple[str, frozenset[str]]) -> dict[str, str]:
    import re

    path, mints = args
    rx = re.compile(r'"mint":"([^"]+)".*?"pool":"([^"]+)"')
    got: dict[str, str] = {}
    for line in _zcat_lines(Path(path), '"venue":"pumpswap"'):
        m = rx.search(line)
        if m and m.group(1) in mints:
            got.setdefault(m.group(1), m.group(2))
    return got


def cmd_pools(a: argparse.Namespace) -> int:
    import multiprocessing as mp

    roots = [_guard(r) for r in a.root]
    pools: dict[str, str] = {}
    for root in roots:
        mints = frozenset(_migrated_mints(root, False))
        if not mints and not (root / "migrations").exists():  # oracle live view: no migrations dir; use the table's pool-B mints
            mints = frozenset(json.loads(line)["mint"] for line in TABLE.read_text().splitlines() if line.strip() and '"pool": "B"' in line)
        files = sorted((root / "trades").glob("trades-*.jsonl*"))
        print(f"pools: {root} migrated_mints={len(mints)} trade_files={len(files)}", file=sys.stderr, flush=True)
        with mp.get_context("spawn").Pool(processes=min(a.workers, 2)) as pool:
            for part in pool.imap_unordered(_pools_from_file, [(str(f), mints) for f in files]):
                pools.update({p: m for m, p in part.items()})
    out = OUT_ROOT / a.run_id / a.out_name
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(sorted(pools)) + "\n", encoding="utf-8")
    print(f"pools: {len(pools)} distinct -> {out}", file=sys.stderr)
    return 0


def cmd_fetch_v(a: argparse.Namespace) -> int:
    from tools.pumpswap_virtual import build_map, load_map, missing_pools, save_map

    pools = json.loads(Path(a.pools).read_text(encoding="utf-8"))
    vp = Path(a.vmap)
    vmap, calls = build_map(pools, existing=load_map(vp) if vp.exists() else {}, rps=a.rps, log=lambda m: print(m, file=sys.stderr, flush=True))
    save_map(vp, vmap, calls)
    miss = missing_pools(vmap, pools)
    vals = [vmap[p] for p in pools if vmap.get(p) is not None]
    rep = {"pools": len(pools), "calls": calls, "n_v_null": len(miss), "null_pools_head": miss[:20], "v_min": min(vals) if vals else None, "v_max": max(vals) if vals else None, "v_median": statistics.median(vals) if vals else None}
    (OUT_ROOT / a.run_id).mkdir(parents=True, exist_ok=True)
    (OUT_ROOT / a.run_id / "fetch_v_report.json").write_text(json.dumps(rep, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(rep, indent=1))
    return 0


# --- rescore -----------------------------------------------------------------------------


def _set_env(vmap: str, mcap: str, counts: Path, capture: bool = False, frozen: bool = False) -> None:
    from tools import pumpswap_virtual_adapter as ad

    os.environ[ad.ENV_MAP], os.environ[ad.ENV_MCAP], os.environ[ad.ENV_COUNTS] = vmap, mcap, str(counts)
    os.environ[ad.ENV_CAPTURE] = "1" if capture else "0"
    os.environ[ad.ENV_FROZEN] = "1" if frozen else "0"


def _read_counts(d: Path) -> dict[str, Any]:
    tot = {"pumpswap_prints": 0, "corrected": 0, "no_v": 0, "bonding_untouched": 0}
    pools: set[str] = set()
    for f in d.glob("counts-*.json"):
        c = json.loads(f.read_text())
        for k in tot:
            tot[k] += c.get(k, 0)
        pools.update(c.get("no_v_pools", []))
    return {**tot, "n_no_v_pools": len(pools), "no_v_pools_head": sorted(pools)[:20]}


def gate_numbers(entered: Sequence[dict[str, Any]], all_rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    import tools.exp011_score as e11

    g = e11.compute_gate(entered)
    n_filled = sum(1 for r in entered if r.get("filled"))
    return {"gate": g, "n_entered": len(entered), "fill_rate_entered": n_filled / len(entered) if entered else None,
            "fill_rate_all": sum(1 for r in all_rows if r.get("filled")) / len(all_rows) if all_rows else None,
            "cohorts": e11.fill_conditional_stats(all_rows, entered), "per_day": e11.per_day_table(all_rows, entered)}


def exit_mix(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    mix: dict[str, int] = {}
    for r in rows:
        k = r.get("exit", "untagged")
        if k == "trigger":
            k = "trigger_gain" if r.get("flat", 0) > 0 else "trigger_loss"
        mix[k] = mix.get(k, 0) + 1
    return mix


def _key(r: dict[str, Any]) -> tuple[str, str]:
    return r["mint"], r["spec"]


def rescore_oof(a: argparse.Namespace, run: Path) -> dict[str, Any]:
    import tools.exp011_freeze as f
    import tools.exp011_score as e11
    from tools import pumpswap_virtual_adapter as ad

    rows_f, manifest = f.load_table(TABLE)
    cache = run / "oof_frozen_entries.json"
    if cache.exists():
        entries_f, fold_info = json.loads(cache.read_text())
    else:
        entries_f, fold_info = f.nested_fixed_threshold_lodo(rows_f)
        cache.write_text(json.dumps([entries_f, fold_info]))
    ref = json.loads((ARTIFACT_DIR / "nested_fixed_threshold_lodo.json").read_text())
    assert len(entries_f) == ref["flat"]["n"], (len(entries_f), ref["flat"]["n"])
    corrected = run / "oof_table_corrected.jsonl"
    if not corrected.exists():
        counts = run / "counts_oof"
        _set_env(a.vmap, a.mcap_mode, counts)
        roots = manifest["roots"]
        with ad.patched_pool_workers():
            rows_c, _ = f.load_tp50_rows(max_workers=2, buffer_hours=24, max_home_hours=12, out_dir=run / "scratch_oof", fast_dir=Path(roots["A"]), insample_dir=Path(roots["C"]), live_dir=Path(roots["B"]))
        corrected.write_text("".join(json.dumps(r) + "\n" for r in rows_c), encoding="utf-8")
    rows_c = [json.loads(line) for line in corrected.read_text().splitlines() if line.strip()]
    by_c = {_key(r): r for r in rows_c}
    entries_c, missing = [], 0
    feat_same = 0
    for e in entries_f:
        c = by_c.get(_key(e))
        if c is None:
            missing += 1
            continue
        feat_same += c["features"] == e["features"]
        entries_c.append({**e, "flat": c["flat"], "press": c["press"], "filled": c["filled"], "gross": c["gross"], "status": c["status"]})
    rep = {
        "n_entries_frozen": len(entries_f), "n_entries_matched": len(entries_c), "n_unmatched": missing, "features_identical": feat_same,
        "frozen": {"nested": f.nested_lodo_report(entries_f, fold_info), **gate_numbers(entries_f, rows_f)},
        "corrected": {"nested": f.nested_lodo_report(entries_c, fold_info), **gate_numbers(entries_c, rows_c)},
        "adapter_counts": _read_counts(run / "counts_oof"),
        "baseline_unfiltered_migrate_direct": {"frozen": e11._cohort_stats(rows_f), "corrected": e11._cohort_stats(rows_c), "n_rows": len(rows_f), "fill_rate_frozen": sum(r["filled"] for r in rows_f) / len(rows_f), "fill_rate_corrected": sum(r["filled"] for r in rows_c) / len(rows_c)},
    }
    return rep


def rescore_holdout(a: argparse.Namespace, run: Path) -> dict[str, Any]:
    import tools.exp011_score as e11
    import tools.exp012_score as s12
    from tools import pumpswap_virtual_adapter as ad

    ranges = s12.DEFAULT_RANGES
    raw = {k: f"{HOLDOUT_RAW}/{k}" for k in ranges}
    clean = {k: f"{HOLDOUT_CLEAN}/{k}" for k in ranges}
    walkers = s12.build_walkers(raw, clean, ranges)  # type: ignore[arg-type]
    hours = s12.make_hours(walkers)
    model, threshold, feature_names = e11.load_frozen_spec(ARTIFACT_DIR)
    out: dict[str, Any] = {"label": "correction analysis, not a new read", "threshold": threshold}
    results: dict[str, list[dict[str, Any]]] = {}
    for tag, frozen in (("frozen_reproduced", True), ("corrected", False)):
        path = run / f"holdout_rows_{tag}.jsonl"
        if not path.exists():
            counts = run / f"counts_holdout_{tag}"
            _set_env(a.vmap, a.mcap_mode, counts, capture=True, frozen=frozen)
            rows = s12.load_rows(hours, 2, s12.BUFFER_HOURS, s12.MAX_HOME_HOURS, run / f"scratch_{tag}", worker_fn=ad.holdout_worker)
            e11.score_rows(model, rows, feature_names)
            path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        results[tag] = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    ent = {t: [r for r in rs if r["score"] >= threshold] for t, rs in results.items()}
    same = {r["mint"] for r in ent["frozen_reproduced"]} == {r["mint"] for r in ent["corrected"]}
    # the original read's own rows, for a byte-level reproduction check of the unpatched pass
    orig_entered = [json.loads(line) for line in Path("/data/mal/exp012/read/entered_rows.jsonl").read_text().splitlines() if line.strip()]
    om = {r["mint"]: r for r in orig_entered}
    repro = sum(1 for r in ent["frozen_reproduced"] if r["mint"] in om and abs(om[r["mint"]]["flat_lamports"] - r["flat"]) < 1e-3)
    out["entered_set_identical_frozen_vs_corrected"] = same
    out["reproduction_vs_original_read"] = {"original_n_entered": len(orig_entered), "reproduced_n_entered": len(ent["frozen_reproduced"]), "n_flat_equal": repro}
    for tag in results:
        g = gate_numbers(ent[tag], results[tag])
        g["exit_mix_entered"] = exit_mix(ent[tag])
        g["report"] = e11.build_report(results[tag], ent[tag], threshold)
        out[tag] = g
    out["adapter_counts_corrected"] = _read_counts(run / "counts_holdout_corrected")
    out["baseline_unfiltered_migrate_direct"] = {t: e11._cohort_stats(rs) for t, rs in results.items()}
    out["baseline_unfiltered_migrate_direct"]["n_rows"] = len(results["corrected"])
    return out


def cmd_rescore(a: argparse.Namespace) -> int:
    run = OUT_ROOT / a.run_id
    run.mkdir(parents=True, exist_ok=True)
    res: dict[str, Any] = {"mcap_mode": a.mcap_mode, "vmap": a.vmap, "note": "pricing re-run only; selection not refit; sells not simulated on-chain; V assumed constant per pool"}
    if "oof" in a.books:
        res["a_exploration_oof"] = rescore_oof(a, run)
        (run / "report_oof.json").write_text(json.dumps(res["a_exploration_oof"], indent=1, default=str) + "\n", encoding="utf-8")
    if "holdout" in a.books:
        res["b_exp012_read_block"] = rescore_holdout(a, run)
        (run / "report_holdout.json").write_text(json.dumps(res["b_exp012_read_block"], indent=1, default=str) + "\n", encoding="utf-8")
    (run / "report.json").write_text(json.dumps(res, indent=1, default=str) + "\n", encoding="utf-8")
    print(f"wrote {run}/report.json", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("pools", "fetch-v", "validate", "rescore"):
        sp = sub.add_parser(name)
        sp.add_argument("--run-id", required=True)
        sp.add_argument("--vmap", default="/data/mal/pumpswap-virtual/pool_v.json")
        if name == "pools":
            sp.add_argument("--root", action="append", required=True)
            sp.add_argument("--workers", type=int, default=2)
            sp.add_argument("--out-name", default="pools.json")
        if name == "fetch-v":
            sp.add_argument("--pools", required=True)
            sp.add_argument("--rps", type=float, default=5.0)
        if name == "validate":
            sp.add_argument("--hours-per-view", type=int, default=6)
            sp.add_argument("--rows-per-hour", type=int, default=60000)
        if name == "rescore":
            sp.add_argument("--books", default="oof,holdout")
            sp.add_argument("--mcap-mode", choices=("v", "vault"), default="v")
    a = ap.parse_args(argv)
    if hasattr(a, "books"):
        a.books = a.books.split(",")
    os.nice(10)
    return {"pools": cmd_pools, "fetch-v": cmd_fetch_v, "validate": cmd_validate, "rescore": cmd_rescore}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
