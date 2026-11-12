#!/usr/bin/env python3
"""floor_nov_pnl: read the scratch ledger written by floor_nov_paper.py and report per-day and pooled paper P&L at stake 0.10 and 0.25 (flat-15% and pressure legs),
with first-come caps K. Entry-day = UTC day of the entry decision. Phases: pre (< 2026-11-07T18Z), cutover (18Z hour, excluded), post (>= 19Z). Counts/aggregates only.
Usage: floor_nov_pnl.py LEDGER_DIR OUT_JSON [RULE=total] [LAT=1p9]
"""
import glob, json, sys, statistics, collections
from datetime import datetime, timezone

CUT = int(datetime(2026, 11, 7, 18, tzinfo=timezone.utc).timestamp() * 1000)
KS = (3, 5, 8, 16, None)


def phase(ms):
    return "pre" if ms < CUT else "cutover" if ms < CUT + 3600000 else "post"


def day(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def stats(pn, stake):
    """pn: list of lamport nets. -> dict"""
    n = len(pn)
    if n == 0:
        return {"n": 0}
    pct = [100.0 * x / (stake * 1e9) for x in pn]
    srt = sorted(pn, reverse=True)
    return {"n": n, "mean_pct": sum(pct) / n, "median_pct": statistics.median(pct), "sum_sol": sum(pn) / 1e9, "sum_ex_top3_sol": sum(srt[3:]) / 1e9 if n > 3 else None,
            "top3_sol": sum(srt[:3]) / 1e9}


def main():
    led, outp = sys.argv[1], sys.argv[2]
    rule = sys.argv[3] if len(sys.argv) > 3 else "total"
    lat = sys.argv[4] if len(sys.argv) > 4 else "1p9"
    opens, trades, other = {}, collections.defaultdict(dict), collections.Counter()
    for f in sorted(glob.glob(led + "/*/trades-*.jsonl")):
        for line in open(f):
            r = json.loads(line)
            k = r.get("kind")
            if k == "open":
                opens[r["position_id"]] = r
            elif k == "trade":
                trades[r["position_id"]][r["stake_sol"]] = r
            else:
                other[k + ":" + str(r.get("reason") or r.get("exit_reason") or "")] += 1
    pos = []
    for pid, o in opens.items():
        t = trades.get(pid)
        if not t:
            continue
        any_r = next(iter(t.values()))
        pos.append({"pid": pid, "t0": o["decision_ms"]["entry"], "t1": any_r["decision_ms"]["exit"], "reason": any_r["exit_reason"], "pool": o["pool"],
                    "stakes": {s: r for s, r in t.items()}})
    pos.sort(key=lambda p: p["t0"])
    res = {"rule": rule, "lat": lat, "n_open_rows": len(opens), "n_priced": len(pos), "other_rows": dict(other), "states": collections.Counter()}
    for p in pos:
        for s, r in p["stakes"].items():
            res["states"][f"{s}:{r['legs'][rule]['flat_' + lat]['state']}"] += 1
    res["states"] = dict(res["states"])
    spans = {"pre": (datetime(2026, 11, 3, tzinfo=timezone.utc), 4.75), "post": (None, 4.25)}  # days of phase (pre: 11-03T00..11-07T18 = 4.75; post: 11-07T19..11-11T24 = 4.2083)
    days_of = {"pre": 4.75, "post": 4.0 + 5 / 24.0, "all": 4.75 + 4.0 + 5 / 24.0}
    out = {}
    for stake in (0.10, 0.25):
        for model in ("flat", "press"):
            key = f"{stake}:{model}"
            rows = []
            for p in pos:
                r = p["stakes"].get(stake)
                if r is None:
                    continue
                leg = r["legs"][rule][f"{model}_{lat}"]
                rows.append({"t0": p["t0"], "t1": p["t1"], "reason": p["reason"], "net": leg["net_lamports"], "state": leg["state"], "ph": phase(p["t0"]), "day": day(p["t0"])})
            blk = {"per_day": {}, "pooled": {}}
            for d in sorted({x["day"] for x in rows}):
                sub = [x for x in rows if x["day"] == d and x["ph"] != "cutover"] if d != "2026-11-07" else None
                if d == "2026-11-07":
                    for ph in ("pre", "post"):
                        s2 = [x for x in rows if x["day"] == d and x["ph"] == ph]
                        blk["per_day"][d + "_" + ph] = dict(stats([x["net"] for x in s2], stake), tp=sum(1 for x in s2 if x["reason"].startswith("tp")))
                    continue
                blk["per_day"][d] = dict(stats([x["net"] for x in sub], stake), tp=sum(1 for x in sub if x["reason"].startswith("tp")))
            for scope in ("pre", "post", "all"):
                sc = [x for x in rows if x["ph"] != "cutover" and (scope == "all" or x["ph"] == scope)]
                blk["pooled"][scope] = {}
                for K in KS:
                    acc, openx = [], []
                    for x in sc:  # first-come: an open slot is free once the exit DECISION is at or before the next entry decision
                        openx = [y for y in openx if y is None or y > x["t0"]]
                        if K is not None and len(openx) >= K:
                            continue
                        openx.append(x["t1"] if x["t1"] is not None else None)
                        acc.append(x)
                    st = stats([x["net"] for x in acc], stake)
                    st["tp_share"] = (sum(1 for x in acc if x["reason"].startswith("tp")) / len(acc)) if acc else None
                    st["sol_per_day"] = st.get("sum_sol", 0.0) / days_of[scope] if acc else 0.0
                    st["triggers_per_day"] = len(sc) / days_of[scope]
                    st["peak_open_uncapped"] = None
                    blk["pooled"][scope]["K=" + str(K)] = st
            out[key] = blk
    res["out"] = out
    json.dump(res, open(outp, "w"), indent=1, default=str)
    print("ok", len(pos), res["states"])


main()
