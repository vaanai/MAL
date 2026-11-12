#!/usr/bin/env python3
"""floor_variants_price: price a variant replay ledger and the positions it ADDS vs a base replay ledger (same send). Keys positions by (pool, entry decision_ms); 'added' =
variant positions whose pool+entry time is absent from base. Counts/aggregates only. Legs: flat_1p9 and press_1p9 (total rule), stakes 0.10 and 0.25.
Usage: floor_variants_price.py VARIANT_LEDGER BASE_LEDGER OUT_JSON   (ledger dir = .../paper-X/ledger)
"""
import glob, json, sys, statistics


def load(led):
    opens, trades = {}, {}
    for f in sorted(glob.glob(led + "/*/trades-*.jsonl")):
        for line in open(f):
            r = json.loads(line)
            if r.get("kind") == "open":
                opens[r["position_id"]] = r
            elif r.get("kind") == "trade":
                trades.setdefault(r["position_id"], {})[r["stake_sol"]] = r
    out = {}
    for pid, o in opens.items():
        out[(o["pool"], o["decision_ms"]["entry"])] = (o, trades.get(pid))
    return out


def stat(nets, stake, tp):
    n = len(nets)
    if not n:
        return {"n": 0}
    s = sorted(nets, reverse=True)
    return {"n": n, "mean_pct": 100 * sum(nets) / n / (stake * 1e9), "median_pct": 100 * statistics.median(nets) / (stake * 1e9), "total_sol": sum(nets) / 1e9,
            "ex_top3_sol": sum(s[3:]) / 1e9 if n > 3 else None, "tp_share": tp / n}


def table(pos, keys):
    res = {}
    for stake in (0.10, 0.25):
        for leg in ("flat_1p9", "press_1p9"):
            nets, tp, guard = [], 0, 0
            for k in keys:
                o, t = pos[k]
                if not t or stake not in t:
                    guard += 1; continue
                r = t[stake]
                nets.append(r["legs"]["total"][leg]["net_lamports"]); tp += r["exit_reason"].startswith("tp")
            res[f"{stake}:{leg}"] = dict(stat(nets, stake, tp), unpriced=guard)
    return res


v, b = load(sys.argv[1]), load(sys.argv[2])
added = [k for k in v if k not in b]
res = {"variant_positions": len(v), "base_positions": len(b), "added": len(added), "base_missing_in_variant": len([k for k in b if k not in v]),
       "variant_all": table(v, list(v)), "added_only": table(v, added)}
json.dump(res, open(sys.argv[3], "w"), indent=1)
print(json.dumps(res)[:400])
