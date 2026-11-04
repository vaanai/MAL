"""ONE-TIME join for historical read rule B (notebook n_odOUfqwbtijjaA, part B). READS OUTCOMES: refuses without --allow-outcomes.

  python -m tools.gradscore.hist_read --dir /data2/mal/gradscore/hist-v0 --allow-outcomes [--draws 1000] [--out FILE.json]

Joins scores-<block>.jsonl (first SCORED row per mint,variant) with cards-<block>.jsonl (baseline features, has_metadata) and
labels-<block>.jsonl (this is the only module that opens labels next to scores). Statistics are tools.gradscore.auc.analyse (AUC, day bootstrap,
1000 within-day permutations, Bonferroni 0.05/6 lower bound, numeric baseline with paired bootstrap), reused, not re-implemented.
Universe: WSOL-quoted pools, one row per mint (first migration), censored_24h rows excluded and counted, no_data rows excluded and counted.
Label: max_mult_24h >= 3 (ret_4h > 0 report-only). Periods: explore-0814 alone; the three September blocks pooled. Minimums per period
(n >= 300, positives >= 20, >= 4 days) are auc.analyse's. A cell PASSES only if it passes in BOTH periods. Every cell is also reported
stratified by has_metadata. Disclosures are printed with the table.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

from tools.gradscore import auc as au

PERIODS = {"explore-0814": ("explore-0814",),
           "september_pooled": ("oracle-insample-2026-09-22_25", "fast-pool-2026-09-18T23_2026-09-22T00", "oracle-live-2026-09-25_27")}
DISCLOSURES = ("best of 6 pre-registered cells (2 variants x 3 scores), all reported",
               "AUC is not P&L (no fees, fills, impact or latency)",
               "Haiku training-data leakage possible for Aug-Sep 2026 tokens, untestable",
               "in-sample exploration blocks; the on-chain uri was read in 2026-11, not at creation; USD in cards uses a constant SOL/USD 150")


def _jsonl(path):
    with open(path) as f:
        for line in f:
            yield json.loads(line)


def _day(ts):
    return au._day(ts)


def load_period(d, blocks):
    """-> ({variant: [row]}, exclusions). Rows carry day, label, label4, the 3 scores, baselines, has_metadata."""
    rows = {"A": [], "B": []}
    exc = {"no_label_row": 0, "censored_24h": 0, "no_data": 0, "not_wsol": 0, "no_card": 0}
    for b in blocks:
        cards = {r["mint"]: r for r in _jsonl(os.path.join(d, f"cards-{b}.jsonl"))}
        labels = {}
        for r in _jsonl(os.path.join(d, f"labels-{b}.jsonl")):
            labels.setdefault(r["mint"], r)  # first migration per mint
        first = {}
        for s in _jsonl(os.path.join(d, f"scores-{b}.jsonl")):
            if s.get("action") == "SCORED":
                first.setdefault((s["mint"], s["variant"]), s)
        for (m, v), s in sorted(first.items()):
            c, lb = cards.get(m), labels.get(m)
            if c is None:
                exc["no_card"] += 1
                continue
            if lb is None:
                exc["no_label_row"] += 1
                continue
            if lb.get("quote_mint") != au.WSOL:
                exc["not_wsol"] += 1
                continue
            if lb.get("censored_24h"):
                exc["censored_24h"] += 1
                continue
            if lb.get("no_data") or lb.get("max_mult_24h") is None:
                exc["no_data"] += 1
                continue
            card = json.loads(c["card_text"])
            buys, sells = card.get("buys_5m") or 0, card.get("sells_5m") or 0
            rows[v].append({"mint": m, "day": _day(c["first_sight_ts"]), "label": lb["max_mult_24h"] >= 3,
                            "label4": (lb["ret_4h"] > 0) if lb.get("ret_4h") is not None else None,
                            "runner_prob_pct": s["runner_prob_pct"], "narrative": s["narrative"], "-rug_risk": -s["rug_risk"],
                            "mcap_usd": card.get("mcap_usd") or 0, "liq_usd": card.get("liq_usd") or 0,
                            "vol_5m_usd": card.get("vol_5m_usd") or 0, "buys_minus_sells": buys - sells,
                            "has_metadata": bool(c.get("has_metadata"))})
    return rows, exc


def read_periods(per_period, draws=au.DRAWS):
    """per_period: {period: {variant: [row]}} -> result dict with per-period cells, strata and both-period PASS flags."""
    out = {"periods": {}, "strata": {}, "pass_both": {}}
    for p, rv in per_period.items():
        out["periods"][p] = {v: au.analyse(rv[v], 1, draws) for v in ("A", "B")}
        for flag in (True, False):
            out["strata"].setdefault(p, {})[f"has_metadata={flag}"] = {
                v: au.analyse([r for r in rv[v] if r["has_metadata"] is flag], 1, draws) for v in ("A", "B")}
    for v in ("A", "B"):
        for s in au.SCORES:
            ok = True
            for p in per_period:
                res = out["periods"][p][v]
                cell = next((c for c in res.get("cells", []) if c["score"] == s), None)
                ok = ok and bool(cell and cell["pass"])
            out["pass_both"][f"{v}:{s}"] = ok
    return out


def table(res, excl):
    lines = []
    for p, pv in res["periods"].items():
        for v in ("A", "B"):
            r = pv[v]
            lines.append(f"[{p}] variant {v}: n={r['n']} positives={r['positives']} days={r['days']} status={r['status']} excluded={excl.get(p)}")
            if r["status"] != "READ":
                continue
            b = r["baseline"]
            lines.append(f"    baseline {b['name']} AUC={b['auc']:.4f}")
            for c in r["cells"]:
                lines.append(f"    {c['score']:<16} AUC={c['auc']:.4f} bonf_LB={c['bonferroni_lb']:.4f} placebo_p={c['placebo_p']:.4f} "
                             f"vs_baseline_LB={c['baseline_diff_bonferroni_lb']:.4f} period_pass={c['pass']}")
    lines.append("PASS only if the cell passes in BOTH periods: " + json.dumps(res["pass_both"], sort_keys=True))
    lines += ["DISCLOSURE: " + x for x in DISCLOSURES]
    return "\n".join(lines)


def self_sha256():
    with open(os.path.abspath(__file__), "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--allow-outcomes", action="store_true", help="required: this tool reads label files")
    ap.add_argument("--draws", type=int, default=au.DRAWS)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    if not a.allow_outcomes:
        print("REFUSED: this tool reads outcomes; pass --allow-outcomes (manager, one time, under read rule B).", file=sys.stderr)
        return 2
    sha = self_sha256()
    print("hist_read.py sha256", sha)
    per, excl = {}, {}
    for p, blocks in PERIODS.items():
        per[p], excl[p] = load_period(a.dir, blocks)
    res = read_periods(per, a.draws)
    res.update(sha256=sha, excluded=excl, disclosures=list(DISCLOSURES))
    if a.out:
        with open(a.out, "w") as f:
            json.dump(res, f, indent=1, sort_keys=True)
    print(table(res, excl))
    return 0


if __name__ == "__main__":
    sys.exit(main())
