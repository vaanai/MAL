"""AUC read of gradscore scores against v1 outcomes. READS OUTCOMES: refuses to run without --allow-outcomes.

Implements the pre-registered GRADSCORE v0 READ RULE of QP-RULING-1104-gradscore (/data/mal/hunt-1008/ops/manager20/). The manager runs it
once, at or after 2026-11-09T01:00Z, after the HOLDOUT_LEDGER declaration has merged. Not run on real data by the collector or any test.

  python -m tools.gradscore.auc --dir DIR --start 2026-11-04T17:00:00Z --allow-outcomes [--end 2026-11-08T00:00:00Z]

Reads only outcomes-v1-*.jsonl (the v0 tracker's rows are never read). Higher score = runner; rug_risk enters as -rug_risk.
Every printed result is walk-2-hour information: not blind for walk 2; AUC is not P&L (no fees, fills or impact); best of 6 cells, all reported.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import random
import sys
from datetime import datetime, timezone

WSOL = "So11111111111111111111111111111111111111112"
SCORES = ("runner_prob_pct", "narrative", "-rug_risk")
BASELINES = ("mcap_usd", "liq_usd", "vol_5m_usd", "buys_minus_sells")
ALPHA = 0.05 / 6
DRAWS = 1000
MIN_N, MIN_POS, MIN_DAYS = 300, 20, 4
DEFAULT_END = "2026-11-08T00:00:00Z"


def _epoch(ts):
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


def _day(t):
    return datetime.fromtimestamp(float(t), timezone.utc).strftime("%Y-%m-%d")


def auc(scores, labels):
    """Mann-Whitney AUC, ties averaged; None if one class is empty."""
    npos = sum(1 for y in labels if y)
    nneg = len(labels) - npos
    if not npos or not nneg:
        return None
    order = sorted(range(len(scores)), key=lambda i: scores[i])
    ranks, i = [0.0] * len(scores), 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and scores[order[j + 1]] == scores[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    rsum = sum(r for r, y in zip(ranks, labels) if y)
    return (rsum - npos * (npos + 1) / 2) / (npos * nneg)


def top_decile_hit_rate(scores, labels):
    n = len(scores)
    if n < 10:
        return None
    k = max(1, n // 10)
    idx = sorted(range(n), key=lambda i: -scores[i])[:k]
    return sum(1 for i in idx if labels[i]) / k


def quantile(vals, q):
    v = sorted(vals)
    return v[int(q * (len(v) - 1))] if v else None


def day_picks(days, draws, seed):
    """The shared day resamples: `draws` lists of days (with replacement), seed fixed. Cells and baseline reuse them (paired)."""
    rng = random.Random(seed)
    return [[rng.choice(days) for _ in days] for _ in range(draws)]


def boot_auc(rows, col, picks, sign=1):
    """rows: dicts with day, label, col. AUC per resample (None draws dropped by returning None in place)."""
    by = {}
    for r in rows:
        by.setdefault(r["day"], []).append((sign * r[col], r["label"]))
    out = []
    for pick in picks:
        sub = [x for d in pick for x in by[d]]
        out.append(auc([s for s, _ in sub], [y for _, y in sub]))
    return out


def perm_p(rows, col, obs, draws=DRAWS, seed=1):
    """Within-UTC-day label permutation placebo: p = (1 + #{perm AUC >= obs}) / (draws + 1)."""
    rng = random.Random(seed)
    by = {}
    for r in rows:
        by.setdefault(r["day"], []).append(r)
    ge = 0
    for _ in range(draws):
        sc, lb = [], []
        for d in sorted(by):
            ys = [r["label"] for r in by[d]]
            rng.shuffle(ys)
            sc += [r[col] for r in by[d]]
            lb += ys
        a = auc(sc, lb)
        if a is not None and a >= obs:
            ge += 1
    return (1 + ge) / (draws + 1)


def _read(pattern):
    for p in sorted(glob.glob(pattern)):
        with open(p) as f:
            for line in f:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue


def load_universe(d, start_s, end_s):
    """Rows for the read plus counts. Excluded mints are counted by reason."""
    disc, n_by_mint = {}, {}
    for r in _read(os.path.join(d, "discoveries-*.jsonl")):
        n_by_mint[r["mint"]] = n_by_mint.get(r["mint"], 0) + 1
        disc.setdefault(r["mint"], r)
    exc = {"not_pump_suffix": 0, "multi_discovery": 0, "outside_window": 0, "quote_not_wsol": 0}
    uni, quote_unrecorded = {}, 0
    for m, r in disc.items():
        t = float(r["first_seen_s"])
        if not start_s <= t < end_s:
            exc["outside_window"] += 1
        elif not r.get("is_pump_suffix"):
            exc["not_pump_suffix"] += 1
        elif n_by_mint[m] != 1:
            exc["multi_discovery"] += 1
        elif r.get("quote_mint") not in (None, WSOL):
            exc["quote_not_wsol"] += 1
        else:
            if r.get("quote_mint") is None:
                quote_unrecorded += 1
            uni[m] = r
    first, skips, cap_rows = {}, {"scored": 0, "parse": 0, "cap": 0}, 0
    for r in _read(os.path.join(d, "scores-*.jsonl")):
        if r["mint"] not in uni:
            continue
        if r.get("action") == "SCORED":
            first.setdefault((r["mint"], r["variant"]), r)
            skips["scored"] += 1
        elif str(r.get("skip_reason", "")).startswith("parse_"):
            skips["parse"] += 1
        elif r.get("skip_reason") == "budget_cap":
            skips["cap"] += 1
    oc = {}
    for r in _read(os.path.join(d, "outcomes-v1-*.jsonl")):  # v0 rows (outcomes-2*) are never read
        oc.setdefault((r["mint"], r["horizon_min"]), r)
    return disc, uni, first, oc, exc, quote_unrecorded, skips


def build_rows(uni, first, oc):
    rows = {"A": [], "B": []}
    for (m, v), s in first.items():
        o24, o4 = oc.get((m, 1440)), oc.get((m, 240))
        if not o24 or o24.get("status") != "ok":
            continue
        r = uni[m]
        buys, sells = r.get("buys_5m") or 0, r.get("sells_5m") or 0
        row = {"mint": m, "day": _day(r["first_seen_s"]), "label": o24["max_mult"] >= 3,
               "runner_prob_pct": s["runner_prob_pct"], "narrative": s["narrative"], "-rug_risk": -s["rug_risk"],
               "mcap_usd": r.get("mcap_usd") or r.get("fdv_usd") or 0, "liq_usd": r.get("liq_usd") or 0,
               "vol_5m_usd": r.get("vol_5m") or 0, "buys_minus_sells": buys - sells,
               "label4": (o4["ret"] > 0) if o4 and o4.get("status") == "ok" else None}
        rows[v].append(row)
    return rows


def analyse(rows_v, picks_seed=1, draws=DRAWS):
    """Per-variant result dict. NOT_DECIDABLE (no AUC computed) when the minimums fail."""
    n, pos = len(rows_v), sum(1 for r in rows_v if r["label"])
    days = sorted({r["day"] for r in rows_v})
    res = {"n": n, "positives": pos, "days": len(days)}
    if n < MIN_N or pos < MIN_POS or len(days) < MIN_DAYS:
        res["status"] = "NOT_DECIDABLE"
        return res
    res["status"] = "READ"
    picks = day_picks(days, draws, picks_seed)
    base = {}
    for b in BASELINES:
        a = auc([r[b] for r in rows_v], [r["label"] for r in rows_v])
        base[b] = a
    bname = max(BASELINES, key=lambda b: max(base[b], 1 - base[b]))
    bsign = 1 if base[bname] >= 0.5 else -1
    bboot = boot_auc(rows_v, bname, picks, bsign)
    res["baseline"] = {"name": bname, "auc": max(base[bname], 1 - base[bname]), "all": base}
    res["cells"] = []
    for sname in SCORES:
        obs = auc([r[sname] for r in rows_v], [r["label"] for r in rows_v])
        boot = boot_auc(rows_v, sname, picks)
        ok = [x for x in boot if x is not None]
        diff = [c - b for c, b in zip(boot, bboot) if c is not None and b is not None]
        p = perm_p(rows_v, sname, obs, draws, 1)
        lb, dlb = quantile(ok, ALPHA), quantile(diff, ALPHA)
        sub = [r for r in rows_v if r["label4"] is not None]
        res["cells"].append({
            "score": sname, "auc": obs, "bonferroni_lb": lb, "placebo_p": p, "baseline_diff_bonferroni_lb": dlb,
            "top_decile_hit_rate": top_decile_hit_rate([r[sname] for r in rows_v], [r["label"] for r in rows_v]),
            "report_only_auc_ret_4h_gt_0": auc([r[sname] for r in sub], [r["label4"] for r in sub]) if sub else None,
            "pass": bool(obs >= 0.60 and lb is not None and lb > 0.50 and p < ALPHA and dlb is not None and dlb > 0)})
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--allow-outcomes", action="store_true", help="required: this tool reads outcome files")
    ap.add_argument("--start", help="#2159 start, UTC ISO (…Z)")
    ap.add_argument("--end", default=DEFAULT_END)
    ap.add_argument("--draws", type=int, default=DRAWS)
    a = ap.parse_args(argv)
    if not a.allow_outcomes:
        print("REFUSED: this tool reads outcomes; pass --allow-outcomes (manager, after QP and the ledger declaration).", file=sys.stderr)
        return 2
    if not a.start:
        print("REFUSED: --start (the #2159 start instant) is required.", file=sys.stderr)
        return 2
    s, e = _epoch(a.start), _epoch(a.end)
    disc, uni, first, oc, exc, quote_unrec, skips = load_universe(a.dir, s, e)
    in_win = [r for r in disc.values() if s <= float(r["first_seen_s"]) < e]
    ndays = len({_day(r["first_seen_s"]) for r in in_win}) or 1
    nd = [o for (m, h), o in oc.items() if h == 1440 and m in uni]
    rows = build_rows(uni, first, oc)
    parse_den = skips["scored"] + skips["parse"]
    print(json.dumps({"header": "walk-2-hour information, not blind for walk 2; best of 6 pre-registered cells (2 variants x 3 scores), all reported; "
                      "AUC is not P&L (no fees, fills or impact); not gate evidence",
                      "universe": len(uni), "excluded": exc, "quote_unrecorded": quote_unrec, "days_in_window": ndays,
                      "discovered_per_day": len(in_win) / ndays, "cap_skip_rows_per_day": skips["cap"] / ndays,
                      "parse_skip_rate": (skips["parse"] / parse_den) if parse_den else None,
                      "no_data_rate_24h": (sum(1 for o in nd if o.get("status") != "ok") / len(nd)) if nd else None}, sort_keys=True))
    for v in ("A", "B"):
        print(json.dumps(dict(analyse(rows[v], 1, a.draws), variant=v), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
