"""ONE-TIME join for historical read rule B (notebook n_odOUfqwbtijjaA, part B). READS OUTCOMES: refuses without --allow-outcomes.

  python -m tools.gradscore.hist_read --dir /data2/mal/gradscore/hist-v0 --allow-outcomes [--draws 1000] [--out FILE.json]

Joins scores-<block>.jsonl (first SCORED row per mint,variant) with cards-<block>.jsonl (baseline features, has_metadata) and
labels-<block>.jsonl (this is the only module that opens labels next to scores). Statistics are tools.gradscore.auc.analyse (AUC, day bootstrap,
1000 within-day permutations, Bonferroni 0.05/6 lower bound, numeric baseline with paired bootstrap), reused, not re-implemented.
Universe: WSOL-quoted pools, one row per mint (first migration), censored_24h rows excluded and counted, no_data rows excluded and counted.
Label: max_mult_24h >= 3 (ret_4h > 0 report-only). Periods: explore-0814 alone; the three September blocks pooled. Minimums per period
(n >= 300, positives >= 20, >= 4 days) are auc.analyse's. A cell PASSES only if it passes in BOTH periods. Every cell is also reported
stratified by has_metadata and by uri_class (derived from the enrich files, joined by mint). RULE B AS AMENDED (QP-RULING-1104-gradscore-hist):
a cell PASSES only if it passes in both periods on all rows AND in both periods on the content_addressed uri stratum; http strata are report-only. Disclosures are printed with the table.
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
class Refused(Exception):
    pass


CENSORED_24H_SHARE = {"explore-0814": "8136/15589", "fast-pool-2026-09-18T23_2026-09-22T00": "1111/3346",
                      "oracle-insample-2026-09-22_25": "1044/3480", "oracle-live-2026-09-25_27": "981/2724"}
DISCLOSURES = (
    "best of 6 pre-registered cells (2 variants x 3 scores), all reported",
    "AUC is not P&L (no fees, fills, impact or latency)",
    "Haiku training-data leakage possible for Aug-Sep 2026 tokens, untestable",
    "the on-chain uri was read in 2026-11, not at creation (pointer immutable per QP sample); USD in cards uses a constant SOL/USD 150",
    "a PASS is a revive trigger only: not a book, not SOL, not gate evidence (promotion gate: flat 15% and pressure scale 1)",
    "exploration blocks were used by earlier lab screens: in-sample",
    "PASS also required on the content-addressed-metadata stratum (uri_class content_addressed); http strata are report-only",
    "censored_24h excluded (share per block, from the labels meta): " + json.dumps(CENSORED_24H_SHARE, sort_keys=True),
    "uncensored no_data rows and rows with no trade inside 24 h are dropped, not labelled negative (certain negatives; counts in excluded)",
    "VMap: a null map value falls through to the default V (6 of 25139 cards); oracle blocks have no tx_index (about 1% same-slot order ambiguity)",
)


def _jsonl(path):
    with open(path) as f:
        for line in f:
            yield json.loads(line)


def _day(ts):
    return au._day(ts)


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_period(d, blocks):
    """-> ({variant: [row]}, exclusions). Rows carry day, label, label4, the 3 scores, baselines, has_metadata, uri_class.
    exclusions also holds coverage per variant (cards, scored, parse_skip, never_scored), cap_hit per block and pins (sha256 of every input).
    Refuses (Refused) when a block's cards meta shows with_uri == 0 or its enrich file is missing."""
    from tools.gradscore.hist import uri_class
    rows = {"A": [], "B": []}
    exc = {"no_label_row": 0, "censored_24h": 0, "no_data": 0, "not_wsol": 0, "no_card": 0,
           "coverage": {v: {"cards": 0, "scored": 0, "parse_skip": 0, "never_scored": 0} for v in "AB"}, "cap_hit": {}, "pins": {}}
    for b in blocks:
        paths = {k: os.path.join(d, f"{k}-{b}.jsonl") for k in ("cards", "enrich", "scores", "labels")}
        mp = os.path.join(d, f"cards-{b}.meta.json")
        if not os.path.exists(paths["enrich"]) or not os.path.exists(mp):
            raise Refused(f"{b}: enrich file or cards meta missing")
        with open(mp) as f:
            if (json.load(f).get("counts") or {}).get("with_uri", 0) == 0:
                raise Refused(f"{b}: cards are un-enriched (with_uri == 0)")
        for pth in list(paths.values()) + [au.__file__]:
            exc["pins"][pth] = _sha(pth)
        smp = os.path.join(d, f"scores-{b}.meta.json")
        if os.path.exists(smp):
            with open(smp) as f:
                exc["cap_hit"][b] = bool((json.load(f).get("this_run") or {}).get("cap_hit"))
        cards = {r["mint"]: r for r in _jsonl(paths["cards"])}
        ucls = {r["mint"]: uri_class(r.get("uri")) for r in _jsonl(paths["enrich"])}
        labels = {}
        for r in _jsonl(paths["labels"]):
            labels.setdefault(r["mint"], r)  # first migration per mint
        first, parse = {}, {}
        for s in _jsonl(paths["scores"]):
            if s.get("action") == "SCORED":
                first.setdefault((s["mint"], s["variant"]), s)
            elif str(s.get("skip_reason", "")).startswith("parse_"):
                parse.setdefault((s["mint"], s["variant"]), s)
        for v in "AB":
            cov = exc["coverage"][v]
            sc = sum(1 for (m, vv) in first if vv == v)
            ps = sum(1 for (m, vv) in parse if vv == v and (m, vv) not in first)
            cov["cards"] += len(cards)
            cov["scored"] += sc
            cov["parse_skip"] += ps
            cov["never_scored"] += len(cards) - sc - ps
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
                            "has_metadata": bool(c.get("has_metadata")), "uri_class": ucls.get(m, "none")})
    return rows, exc


def _cell_pass(res, s):
    cell = next((c for c in (res or {}).get("cells", []) if c["score"] == s), None)
    return bool(cell and cell["pass"])


def read_periods(per_period, draws=au.DRAWS):
    """per_period: {period: {variant: [row]}}. PASS (rule B as amended) = pass in every period on all rows AND in every period on the
    content_addressed stratum. Strata (has_metadata, uri_class) are reported for every cell; only content_addressed gates."""
    out = {"periods": {}, "strata": {}, "pass_all_rows": {}, "pass_content_addressed": {}, "pass_both": {}}
    strata = [("has_metadata=True", lambda r: r["has_metadata"]), ("has_metadata=False", lambda r: not r["has_metadata"])]
    strata += [(f"uri_class={u}", (lambda u: lambda r: r["uri_class"] == u)(u)) for u in ("content_addressed", "http", "none")]
    for p, rv in per_period.items():
        out["periods"][p] = {v: au.analyse(rv[v], 1, draws) for v in ("A", "B")}
        st = out["strata"].setdefault(p, {})
        for key, pred in strata:
            st[key] = {v: au.analyse([r for r in rv[v] if pred(r)], 1, draws) for v in ("A", "B")}
    for v in ("A", "B"):
        for s in au.SCORES:
            a_ = all(_cell_pass(out["periods"][p][v], s) for p in per_period)
            c_ = all(_cell_pass(out["strata"][p]["uri_class=content_addressed"][v], s) for p in per_period)
            out["pass_all_rows"][f"{v}:{s}"], out["pass_content_addressed"][f"{v}:{s}"] = a_, c_
            out["pass_both"][f"{v}:{s}"] = a_ and c_
    return out


def table(res, excl):
    lines = []
    for p, pv in res["periods"].items():
        for v in ("A", "B"):
            r = pv[v]
            lines.append(f"[{p}] variant {v}: n={r['n']} positives={r['positives']} days={r['days']} status={r['status']} excluded="
                         + json.dumps({k: x for k, x in (excl.get(p) or {}).items() if k not in ("pins", "coverage", "cap_hit")}, sort_keys=True))
            if r["status"] != "READ":
                continue
            b = r["baseline"]
            lines.append(f"    baseline {b['name']} AUC={b['auc']:.4f}")
            for c in r["cells"]:
                lines.append(f"    {c['score']:<16} AUC={c['auc']:.4f} bonf_LB={c['bonferroni_lb']:.4f} placebo_p={c['placebo_p']:.4f} "
                             f"vs_baseline_LB={c['baseline_diff_bonferroni_lb']:.4f} period_pass={c['pass']}")
    for p, st in res["strata"].items():
        for k, pv in st.items():
            for v in ("A", "B"):
                r = pv[v]
                lines.append(f"  stratum [{p}] {k} variant {v}: n={r['n']} positives={r['positives']} days={r['days']} status={r['status']}"
                             + ("" if r["status"] != "READ" else " " + " ".join(f"{c['score']}:AUC={c['auc']:.3f},pass={c['pass']}" for c in r["cells"])))
    for p, e in excl.items():
        lines.append(f"  coverage [{p}]: {json.dumps(e.get('coverage'), sort_keys=True)} cap_hit={json.dumps(e.get('cap_hit'), sort_keys=True)}")
    lines.append("PASS only if the cell passes in BOTH periods on all rows AND on content_addressed: " + json.dumps(res["pass_both"], sort_keys=True))
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
    try:
        for p, blocks in PERIODS.items():
            per[p], excl[p] = load_period(a.dir, blocks)
    except Refused as e:
        print("REFUSED:", e, file=sys.stderr)
        return 2
    pins = {k: v for e in excl.values() for k, v in e["pins"].items()}
    for k in sorted(pins):
        print("input sha256", pins[k], k)
    res = read_periods(per, a.draws)
    res.update(sha256=sha, input_sha256=pins, excluded=excl, disclosures=list(DISCLOSURES))
    if a.out:
        with open(a.out, "w") as f:
            json.dump(res, f, indent=1, sort_keys=True)
    print(table(res, excl))
    return 0


if __name__ == "__main__":
    sys.exit(main())
