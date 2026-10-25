#!/usr/bin/env python3
"""P28 pricing driver (CALL-SYNC cs12 + lag selector; FLUSH f2 + SIZE-SKEW mirror veto). PAPER ONLY, exploration block only, no profit claim.

    python -m tools.p28_price --idea callsync|flush --block /data/mal/clean-view/explore-0814 --out DIR [--hours H ...] [--workers 1]

Declared in docs/library/p28-new-screens/PRICING-DECLARE.md before any price. This is NOT a kit screen of record: it drives arena_screen.Screen.run_block on one
block (the kit's run() refuses that), then prices the kit's own per-trip arrays with the kit's own `legs` (send 160,000 deciding; 505,000 beside).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np

from tools import arena_screen as S
from tools import arena_stack as F

REPO = Path(__file__).resolve().parent.parent
RULES = REPO / "docs" / "library" / "p28-new-screens" / "p28_rules.py"
STAKE = 0.25
LAT = 1.9
SEND_DECIDING, SEND_SENS = 160_000, 505_000
DRAWS, SEED = 1000, 1
MIN_PER_DAY = 10.0
SLIP_MAX = 0.02
VETO_SHARE_MIN = 0.10


def load_rules():
    spec = importlib.util.spec_from_file_location("p28_rules", RULES)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------------------------------- statistics
def book_stats(x: np.ndarray, day: np.ndarray) -> dict | None:
    """x in SOL per trade. n, mean, both CI90 lower bounds (1000 draws, seed 1), ex-top-3, ex-best-date, per-date totals."""
    x = np.asarray(x, float)
    if len(x) == 0:
        return None
    days, inv = np.unique(day, return_inverse=True)
    dsum, dn = np.bincount(inv, weights=x), np.bincount(inv).astype(float)
    tot = float(x.sum())
    top3 = float(np.sort(x)[::-1][:3].sum())
    return {"n": int(len(x)), "days": int(len(days)), "pos_days": int((dsum > 0).sum()), "mean_sol": float(x.mean()), "mean_pct": float(x.mean() / STAKE * 100),
            "ci_lo_trade": float(F.boot_lo(x)), "ci_lo_date": float(S.boot_lo(dsum, dn)), "total": tot, "ex_top3": tot - top3, "ex_best_date": tot - float(dsum.max()),
            "by_day": {str(int(d)): {"n": int(c), "total": float(s)} for d, c, s in zip(days, dn, dsum)}}


def book_kills(st_flat: dict | None, st_press: dict | None) -> list[str]:
    """Kill rules 1-4 of PRICING-DECLARE (both fail models). Empty list = none fired."""
    if st_flat is None or st_press is None:
        return ["no trades"]
    out = []
    for tag, s in (("flat", st_flat), ("press", st_press)):
        if not s["mean_sol"] > 0:
            out.append(f"1 mean<=0 ({tag})")
        if not s["ex_top3"] > 0:
            out.append(f"2 ex-top-3<=0 ({tag})")
        if not s["ex_best_date"] > 0:
            out.append(f"3 ex-best-date<=0 ({tag})")
        if 2 * s["pos_days"] < s["days"]:
            out.append(f"4 fewer than half the dates positive ({tag}: {s['pos_days']}/{s['days']})")
    return out


def veto_lift(x: np.ndarray, flag: np.ndarray, day: np.ndarray, draws: int = DRAWS, seed: int = SEED) -> dict:
    """Lift L = mean(kept) - mean(all) of dropping flagged trades. D = L_veto - L_rand, bootstrap trade level (paired, random flags = permutation inside the resample, same share), 1000 draws,
    seed 1, plus the date-clustered form and 1000 random vetoes on the actual sample."""
    x = np.asarray(x, float)
    fl = np.asarray(flag, bool)
    n = len(x)
    if n == 0 or fl.all() or not fl.any():
        return {"n": n, "share": float(fl.mean()) if n else None, "error": "no flagged or no kept trades"}
    kept = ~fl
    L = float(x[kept].mean() - x.mean())
    rng = np.random.default_rng(seed)
    D = np.empty(draws)
    for i in range(draws):
        ix = rng.integers(0, n, n)
        xs, fs = x[ix], fl[ix]
        pr = rng.permutation(fs)
        lv = xs[~fs].mean() - xs.mean() if (~fs).any() else np.nan
        lr = xs[~pr].mean() - xs.mean() if (~pr).any() else np.nan
        D[i] = lv - lr
    days, inv = np.unique(day, return_inverse=True)
    k = len(days)
    rng2 = np.random.default_rng(seed)
    Dd = np.empty(draws)
    for i in range(draws):
        pr = rng2.permutation(fl)
        ix = rng2.integers(0, k, k)

        def lift(f):
            sa, na = np.bincount(inv, weights=x, minlength=k), np.bincount(inv, minlength=k).astype(float)
            sk, nk = np.bincount(inv, weights=np.where(f, 0.0, x), minlength=k), np.bincount(inv, weights=(~f).astype(float), minlength=k)
            return sk[ix].sum() / max(1.0, nk[ix].sum()) - sa[ix].sum() / max(1.0, na[ix].sum())

        Dd[i] = lift(fl) - lift(pr)
    rr = np.random.default_rng(seed)
    nflag = int(fl.sum())
    rand_means = np.empty(draws)
    for i in range(draws):
        drop = rr.choice(n, nflag, replace=False)
        m = np.ones(n, bool)
        m[drop] = False
        rand_means[i] = x[m].mean()
    return {"n": n, "n_flagged": nflag, "share": nflag / n, "mean_all": float(x.mean()), "mean_kept": float(x[kept].mean()), "mean_flagged": float(x[fl].mean()), "lift": L,
            "D_trade_p5": float(np.nanpercentile(D, 5)), "D_trade_mean": float(np.nanmean(D)), "D_date_p5": float(np.nanpercentile(Dd, 5)),
            "rand_kept_mean_p5_p50_p95": [float(np.percentile(rand_means, q)) for q in (5, 50, 95)], "p_rand_ge": float((rand_means >= x[kept].mean()).mean())}


# ---------------------------------------------------------------------------------------------------- pricing of one cell
def legs_for(st: dict, send: int, rent: int, intercept=None) -> dict:
    return S.legs(st, STAKE * 1e9, LAT, "total", S.screen_cfg(rent, send), intercept)


def price_cell(st: dict) -> dict:
    n = len(st["ref"])
    day = st["T"] // 86400
    res: dict = {"n": n}
    base = {}
    for send in (SEND_DECIDING, SEND_SENS):
        for rent, tag in ((S.RENT_DECIDING, "rent0"), (S.RENT_CHARGED, "rentC")):
            L = legs_for(st, send, rent)
            base[(send, tag)] = L
            res[f"{send}|{tag}"] = {fail: book_stats(L[fail] / 1e9, day) for fail in ("flat", "press")}
            res[f"{send}|{tag}"]["intercept"] = float(L["c"]) if np.ndim(L["c"]) == 0 else float(np.mean(L["c"]))
    L = base[(SEND_DECIDING, "rent0")]
    fl = L["filled"]
    with np.errstate(all="ignore"):
        slip = (st["Qe"][LAT] / st["Be"][LAT]) / st["ref"] - 1.0
    res["fills"] = int(fl.sum())
    res["guard_refused_share"] = float(1 - fl.mean()) if n else None
    res["median_entry_slip"] = float(np.nanmedian(slip[fl])) if fl.any() else None
    res["slip_p25_p75_p95"] = [float(np.nanpercentile(slip[fl], q)) for q in (25, 75, 95)] if fl.any() else None
    res["strand_share"] = float(L["strand"].mean()) if n else None
    res["exits"] = {S.EXIT_REASONS[k]: int((st["why"] == k).sum()) for k in range(4)}
    days = len(np.unique(day))
    res["trades_per_day"] = n / days if days else None
    for fail in ("flat", "press"):
        s = res[f"{SEND_DECIDING}|rent0"][fail]
        res[f"capacity_sol_per_day_{fail}"] = (s["mean_sol"] * n / days) if s and days else None  # trades/day x net edge x 0.25 SOL = trades/day x mean SOL per trade
    return res, L


def run(args) -> dict:
    rules = load_rules()
    rule_sha = S.check_rule_source(RULES)
    kind = S.check_block_path(args.block, False)
    out = S.check_out_dir(args.out)
    files = S.discover(args.block, kind, args.hours)
    vpaths = S.check_vmap_paths(S.VMAP_DEFAULT)
    rule = rules.make_callsync() if args.idea == "callsync" else rules.make_flush()
    scr = S.Screen(rule, count_only=False, min_attempts_per_day=1.0, screen_p=False)
    name = "explore-0814"
    scr.run_block(0, name, S.iter_hours(files, name, "backfill", S.VMap(vpaths), args.workers))
    doc = {"idea": args.idea, "block": name, "hours": len(files), "hours_first_last": [files[0][0].strftime("%Y-%m-%dT%H"), files[-1][0].strftime("%Y-%m-%dT%H")], "rule_sha256": rule_sha,
           "kit_provenance": S.kit_provenance(), "counters": dict(scr.counters), "cells": {}}
    if args.idea == "callsync":
        doc["rule_stats"] = rule.stats
    for ci, cell in enumerate(scr.cells):
        parts = scr.acc[ci]
        if not parts:
            doc["cells"][cell.name] = {"n": 0, "excluded_block_edge": int(scr.excl_cell[ci])}
            continue
        st = S._cat(parts)
        res, L = price_cell(st)
        res["excluded_block_edge"] = int(scr.excl_cell[ci])
        day = st["T"] // 86400
        if args.idea == "callsync":
            if cell.name == "cs12_lag":
                kills = book_kills(res[f"{SEND_DECIDING}|rent0"]["flat"], res[f"{SEND_DECIDING}|rent0"]["press"])
                if res["median_entry_slip"] is None or res["median_entry_slip"] > SLIP_MAX:
                    kills.append(f"5 median entry slip {res['median_entry_slip']} > 2%")
                if res["trades_per_day"] < MIN_PER_DAY:
                    kills.append(f"6 trades/day {res['trades_per_day']:.1f} < 10")
                res["kills"], res["verdict"] = kills, ("KILL" if kills else "SURVIVES-SCREEN (not an edge claim)")
        else:
            fk = np.array([rule.flags.get((int(p), int(s)), (0, 0, 0, None))[0] for p, s in zip(st["pool"], st["sd"])], bool)
            res["flag_lookup_missing"] = int(sum((int(p), int(s)) not in rule.flags for p, s in zip(st["pool"], st["sd"])))
            res["veto"] = {}
            xs = {fail: L[fail] / 1e9 for fail in ("flat", "press")}
            for fail in ("flat", "press"):
                res["veto"][fail] = veto_lift(xs[fail], fk, day)
            res["kept_book"] = {fail: book_stats(xs[fail][~fk], day[~fk]) for fail in ("flat", "press")}
            res["flagged_book"] = {fail: book_stats(xs[fail][fk], day[fk]) for fail in ("flat", "press")}
            res["kept_kills"] = book_kills(res["kept_book"]["flat"], res["kept_book"]["press"])
            vk = []
            share = float(fk.mean()) if len(fk) else 0.0
            if share < VETO_SHARE_MIN:
                vk.append(f"veto share {share:.3f} < 10%")
            for fail in ("flat", "press"):
                v = res["veto"][fail]
                if "error" in v or not v["D_trade_p5"] > 0:
                    vk.append(f"lift over random veto: CI90 lower bound <= 0 ({fail})")
            res["veto_kills"] = vk
            res["verdict"] = ("VETO KILLED" if vk else ("VETO INFORMATIVE, BOOK KILLED" if res["kept_kills"] else "VETO INFORMATIVE, KEPT BOOK SURVIVES-SCREEN (not an edge claim)"))
            res["flag_counts"] = {"flagged": int(fk.sum()), "both_sides_ge10": int(sum(1 for p, s in zip(st["pool"], st["sd"]) if rule.flags.get((int(p), int(s)), (0, 0, 0, None))[1] >= 10 and rule.flags.get((int(p), int(s)), (0, 0, 0, None))[2] >= 10))}
        doc["cells"][cell.name] = res
    Path(out).mkdir(parents=True, exist_ok=True)
    Path(out, f"p28_{args.idea}_result.json").write_text(json.dumps(doc, indent=1, default=float))
    return doc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--idea", choices=("callsync", "flush"), required=True)
    ap.add_argument("--block", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--hours", nargs="*")
    ap.add_argument("--workers", type=int, default=1)
    args = ap.parse_args(argv)
    try:
        doc = run(args)
    except S.Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    import resource
    doc["maxrss_mb"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    print("maxrss_mb", round(doc["maxrss_mb"]))
    print(json.dumps({"idea": doc["idea"], "hours": doc["hours"], "counters": doc["counters"], "cells": {k: {"n": v.get("n"), "verdict": v.get("verdict")} for k, v in doc["cells"].items()}}, indent=1, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
