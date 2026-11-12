#!/usr/bin/env python3
"""floor_gap_range: read the ledger of floor_nov_paper.py --gap-exclusion off --ts-variant and report the FLOOR November paper P&L for
(A) positions with no gap overlap (the 50 normally priced), (B) gap-overlap positions priced through the holes (engine exit), (C) the same overlap positions
with every exit forced to the 24 h time stop (shadow rows in ledger/<bot>-ts; TP-exit positions use the shadow, time exits keep their own price), and the
combined A+B, A+C. Plain reader: NO rent add-back (the ledger is priced at --send 160000). Stakes 0.10 / 0.25, flat and pressure, first-come caps K=8/16/uncapped,
SOL/day over 8.9583 d (cutover hour 2026-11-07T18Z excluded from pooled). In-sample paper research; no gate claim.
Usage: floor_gap_range.py LEDGER_DIR OUT_JSON [CONTROL_PNL_JSON]
"""
import collections, glob, json, sys
sys.path.insert(0, __import__("os").path.dirname(__file__))
from floor_nov_pnl import stats, phase, day  # noqa: E402

DAYS = 4.75 + 4.0 + 5 / 24.0
RULE, LAT = "total", "1p9"


def load(led):
    opens, trades, shadows = {}, collections.defaultdict(dict), collections.defaultdict(dict)
    for f in sorted(glob.glob(led + "/*/trades-*.jsonl")):
        for line in open(f):
            r = json.loads(line)
            k = r.get("kind")
            if k == "open":
                opens[r["position_id"]] = r
            elif k == "trade":
                (shadows if r["position_id"].endswith("-ts") else trades)[r["position_id"]][r["stake_sol"]] = r
    return opens, trades, shadows


def cap(rows, K):
    acc, openx = [], []
    for x in rows:
        openx = [y for y in openx if y is None or y > x["t0"]]
        if K is not None and len(openx) >= K:
            continue
        openx.append(x["t1"]); acc.append(x)
    return acc


def summarize(rows, stake):
    out = {"per_day": {}, "pooled": {}}
    for d in sorted({x["day"] for x in rows}):
        sub = [x for x in rows if x["day"] == d]
        if d == "2026-11-07":
            sub = [x for x in sub if x["ph"] != "cutover"]
        s = stats([x["net"] for x in sub], stake); s["tp_share"] = (sum(x["tp"] for x in sub) / len(sub)) if sub else None
        out["per_day"][d] = s
    sc = [x for x in rows if x["ph"] != "cutover"]
    for K in (8, 16, None):
        acc = cap(sc, K)
        s = stats([x["net"] for x in acc], stake); s["tp_share"] = (sum(x["tp"] for x in acc) / len(acc)) if acc else None
        s["sol_per_day"] = s.get("sum_sol", 0.0) / DAYS
        out["pooled"]["K=" + str(K)] = s
    return out


def main(led, outp, control=None):
    opens, trades, shadows = load(led)
    entries = collections.Counter(day(o["decision_ms"]["entry"]) for o in opens.values())
    res = {"n_open": len(opens), "n_priced": len(trades), "entries_by_day": dict(sorted(entries.items())), "n_shadow_pids": len(shadows)}
    pos = []
    for pid, o in opens.items():
        t = trades.get(pid)
        if not t:
            continue
        r0 = next(iter(t.values()))
        holes = r0.get("gap_holes") or []
        ex = r0["decision_ms"]["exit"]
        pos.append({"pid": pid, "t0": o["decision_ms"]["entry"], "t1": ex, "reason": r0["exit_reason"], "ov": bool(r0.get("gap_overlap")),
                    "hole_before_exit": any(h[0] <= ex for h in holes), "t": t, "sh": shadows.get(pid + "-ts")})
    pos.sort(key=lambda p: p["t0"])
    ov = [p for p in pos if p["ov"]]
    res["counts"] = {"A_n": sum(1 for p in pos if not p["ov"]), "overlap_n": len(ov), "overlap_tp_exit": sum(1 for p in ov if p["reason"].startswith("tp")),
                     "overlap_time_exit": sum(1 for p in ov if not p["reason"].startswith("tp")),
                     "overlap_hole_before_exit": sum(1 for p in ov if p["hole_before_exit"]),
                     "overlap_tp_exit_hole_before_exit": sum(1 for p in ov if p["hole_before_exit"] and p["reason"].startswith("tp")),
                     "overlap_tp_without_shadow": sum(1 for p in ov if p["reason"].startswith("tp") and not p["sh"]),
                     "overlap_by_entry_day": dict(collections.Counter(day(p["t0"]) for p in ov))}

    def row(p, stake, model, variant):
        src = p["t"]; t1 = p["t1"]; tp = int(p["reason"].startswith("tp"))
        if variant == "ts" and p["ov"] and tp:
            if not p["sh"] or stake not in p["sh"]:
                return None
            src = p["sh"]; t1 = src[stake]["decision_ms"]["exit"]; tp = 0
        r = src.get(stake)
        if r is None:
            return None
        leg = r["legs"][RULE][f"{model}_{LAT}"]
        return {"t0": p["t0"], "t1": t1, "net": leg["net_lamports"], "tp": tp, "ph": phase(p["t0"]), "day": day(p["t0"])}
    for stake in (0.10, 0.25):
        for model in ("flat", "press"):
            key = f"{stake}:{model}"
            groups = {
                "A": [row(p, stake, model, "eng") for p in pos if not p["ov"]],
                "B": [row(p, stake, model, "eng") for p in ov],
                "C_ts": [row(p, stake, model, "ts") for p in ov],
                "B_hole_before_exit": [row(p, stake, model, "eng") for p in ov if p["hole_before_exit"]],
                "B_hole_not_before_exit": [row(p, stake, model, "eng") for p in ov if not p["hole_before_exit"]],
                "A+B": [row(p, stake, model, "eng") for p in pos],
                "A+C_ts": [row(p, stake, model, "ts") for p in pos],
            }
            res.setdefault("groups", {})[key] = {g: summarize(sorted([x for x in v if x], key=lambda x: x["t0"]), stake) for g, v in groups.items()}
    if control:
        c = json.load(open(control))
        res["control_file"] = control
        res["control_pooled_all_K16"] = {k: c["out"][k]["pooled"]["all"]["K=16"] for k in ("0.1:flat", "0.1:press", "0.25:flat", "0.25:press") if k in c["out"]}
    json.dump(res, open(outp, "w"), indent=1, default=str)
    a = res["groups"]["0.1:flat"]["A"]["pooled"]["K=16"]
    print("A 0.10 flat K16:", a.get("n"), a.get("sum_sol"), a.get("sum_ex_top3_sol"))
    print("counts", res["counts"]); print("entries_by_day", res["entries_by_day"])


if __name__ == "__main__":
    main(*sys.argv[1:4])
