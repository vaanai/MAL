"""F5 extra (QP-RULING-1121 Q1 F5): total fee (lp + protocol + creator bps) per WSOL-only PumpSwap Buy/Sell event, count per total, in P and in A; 0 post events with total above
the maximum total in R'; event-weighted mean total in A vs P (more than 5 bps above P goes to QP). R' = declared pre set (P + M_pre) U s19 extended pre set U {20,0,5}.
Offline, from out/s17_pumpswap_events.json and out/s19_fee_triples_extended_pre.json. Structure only."""
import json, os, collections
from pathlib import Path
O = Path(os.environ.get("UPGRADE_OUT", "out"))
s17 = json.load(open(O / "s17_pumpswap_events.json")); s19 = json.load(open(O / "s19_fee_triples_extended_pre.json"))
def by_triple(d):
    c = collections.Counter()
    for k, v in d.items(): c[k.split("|")[0]] += v
    return c
post = by_triple(s17["post"]["wsol_only_fee_triples"]); pre = by_triple(s17["pre"]["wsol_only_fee_triples"])
R = set(pre) | {k.split("|")[0] for k in s19["triples"]} | {"20,0,5"}
tot = lambda t: sum(int(x) for x in t.split(","))
def dist(c):
    d = collections.Counter()
    for t, n in c.items(): d[tot(t)] += n
    return dict(sorted(d.items()))
def mean(c): return sum(tot(t) * n for t, n in c.items()) / sum(c.values())
mx = max(tot(t) for t in R)
res = {"max_total_in_R": mx, "count_per_total_post": dist(post), "count_per_total_pre": dist(pre), "n_post": sum(post.values()), "n_pre": sum(pre.values()),
       "post_events_total_above_max_R": sum(n for t, n in post.items() if tot(t) > mx), "mean_total_post": mean(post), "mean_total_pre": mean(pre)}
res["mean_diff_bps"] = res["mean_total_post"] - res["mean_total_pre"]; res["to_QP_mean_gt_5bps"] = res["mean_diff_bps"] > 5
res["verdict_zero_above_max"] = "MET" if res["post_events_total_above_max_R"] == 0 else "NOT MET"
(O / "s28_fee_totals.json").write_text(json.dumps(res, indent=1)); print(json.dumps(res, indent=1))
