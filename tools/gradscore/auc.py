"""AUC analysis of gradscore scores against outcomes. READS OUTCOMES: refuses to run without --allow-outcomes.

The manager runs this after QP has ruled on the seal question (see the PR body). Not run by the collector or any test on real data.

  python -m tools.gradscore.auc --dir DIR --allow-outcomes [--draws 1000] [--seed 1]

Labels (per mint): max_mult_24h >= 3 (the +24h outcome row's max_mult) and ret_4h > 0 (the +4h row's return).
Per variant (A, B) and per score (runner_prob_pct, narrative, rug_risk): AUC, day-bootstrap 90% CI (resample first-sight UTC days
with replacement, 1000 draws, seed 1), top-decile hit rate, and a placebo AUC from shuffled scores (same seed). Tokens with no
outcome row for a label are dropped from that label; n and positives are printed.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import random
import sys
from datetime import datetime, timezone

SCORES = ("runner_prob_pct", "narrative", "rug_risk")


def auc(scores, labels):
    """Mann-Whitney AUC with tie correction; None if one class is empty."""
    pos = [s for s, y in zip(scores, labels) if y]
    neg = [s for s, y in zip(scores, labels) if not y]
    if not pos or not neg:
        return None
    pairs = sorted(range(len(scores)), key=lambda i: scores[i])
    ranks, i = [0.0] * len(scores), 0
    while i < len(pairs):
        j = i
        while j + 1 < len(pairs) and scores[pairs[j + 1]] == scores[pairs[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[pairs[k]] = (i + j) / 2 + 1
        i = j + 1
    rsum = sum(r for r, y in zip(ranks, labels) if y)
    return (rsum - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


def top_decile_hit_rate(scores, labels):
    n = len(scores)
    if n < 10:
        return None
    k = max(1, n // 10)
    idx = sorted(range(n), key=lambda i: -scores[i])[:k]
    return sum(1 for i in idx if labels[i]) / k


def day_bootstrap(rows, draws=1000, seed=1):
    """rows: [(day, score, label)]. 5th and 95th percentile of AUC over day resamples."""
    by_day = {}
    for d, s, y in rows:
        by_day.setdefault(d, []).append((s, y))
    days = sorted(by_day)
    if len(days) < 2:
        return None
    rng, vals = random.Random(seed), []
    for _ in range(draws):
        pick = [rng.choice(days) for _ in days]
        sub = [x for d in pick for x in by_day[d]]
        a = auc([s for s, _ in sub], [y for _, y in sub])
        if a is not None:
            vals.append(a)
    if not vals:
        return None
    vals.sort()
    return vals[int(0.05 * (len(vals) - 1))], vals[int(0.95 * (len(vals) - 1))]


def placebo_auc(scores, labels, seed=1):
    s = list(scores)
    random.Random(seed).shuffle(s)
    return auc(s, labels)


def _read(pattern):
    for p in sorted(glob.glob(pattern)):
        for line in open(p):
            try:
                yield json.loads(line)
            except ValueError:
                continue


def load_joined(d):
    """{(mint, variant): {day, scores..., max_mult_24h, ret_4h}} from scores-*.jsonl and outcomes-*.jsonl."""
    out = {}
    for r in _read(os.path.join(d, "scores-*.jsonl")):
        if r.get("action") == "SCORED":
            day = datetime.fromtimestamp(float(r["first_seen_s"]), timezone.utc).strftime("%Y-%m-%d")
            out[(r["mint"], r["variant"])] = {"day": day, **{k: r[k] for k in SCORES}}
    oc = {}
    for r in _read(os.path.join(d, "outcomes-2*.jsonl")):
        if r.get("status") == "ok":
            oc[(r["mint"], r["horizon_min"])] = r
    for (m, _), row in out.items():
        r24, r4 = oc.get((m, 1440)), oc.get((m, 240))
        row["max_mult_24h"] = r24["max_mult"] if r24 else None
        row["ret_4h"] = r4["ret"] if r4 else None
    return out


def analyse(joined, draws=1000, seed=1):
    res = []
    for variant in ("A", "B"):
        for sname in SCORES:
            for lname, getter in (("max_mult_24h>=3", lambda r: None if r["max_mult_24h"] is None else r["max_mult_24h"] >= 3),
                                  ("ret_4h>0", lambda r: None if r["ret_4h"] is None else r["ret_4h"] > 0)):
                rows = [(r["day"], r[sname], getter(r)) for (m, v), r in joined.items() if v == variant and getter(r) is not None]
                sc, lb = [x[1] for x in rows], [x[2] for x in rows]
                res.append({"variant": variant, "score": sname, "label": lname, "n": len(rows), "positives": sum(1 for y in lb if y),
                            "auc": auc(sc, lb), "ci90_day_bootstrap": day_bootstrap(rows, draws, seed) if rows else None,
                            "top_decile_hit_rate": top_decile_hit_rate(sc, lb) if rows else None,
                            "placebo_auc": placebo_auc(sc, lb, seed) if rows else None})
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--allow-outcomes", action="store_true", help="required: this tool reads outcome files")
    ap.add_argument("--draws", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args(argv)
    if not a.allow_outcomes:
        print("REFUSED: this tool reads outcomes; pass --allow-outcomes (manager, after QP).", file=sys.stderr)
        return 2
    for r in analyse(load_joined(a.dir), a.draws, a.seed):
        print(json.dumps(r, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
