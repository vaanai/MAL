#!/usr/bin/env python3
"""floor_nov_pnl: read the scratch ledger written by floor_nov_paper.py and report per-day and pooled paper P&L at stake 0.10 and 0.25 (flat-15% and pressure legs),
with first-come caps K. Entry-day = UTC day of the entry decision. Phases: pre (< 2026-11-07T18Z), cutover (18Z hour, excluded), post (>= 19Z). Counts/aggregates only.
Usage: floor_nov_pnl.py LEDGER_DIR OUT_JSON [RULE=total] [LAT=1p9] [--send-lamports 160000 [--ledger-send 1673840]]
Rent correction (manager22 erratum, 2026-11-12): the first replay charged send = 1,673,840 lamports per send, of which 1,513,840 is refundable token-ATA rent. With
--send-lamports S the reader adds back (ledger_send - S) per send actually paid: 2 sends for a filled trade (buy + sell), 1 for a guarded trade (the lost buy). A ledger
already priced at S needs no flag. Also writes entries_by_day: n_entries (open rows), n_priced, n_unpriced (no trade row, i.e. engine exclusion, normally feed_gap),
total SOL and ex-top-3 SOL per UTC entry day, per stake and leg (cutover hour NOT excluded there). NOTE: pooled sol_per_day uses fixed 11-03..11-11 day denominators; use entries_by_day for other windows.
"""
import argparse, glob, json, sys, statistics, collections
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


def send_addback(state, delta):
    """lamports to add back to a leg net when a ledger priced at send L is restated to send L - delta"""
    return {"filled": 2 * delta, "guarded": delta}.get(state, 0)


def entries_by_day(opens, pos, rule, lat, send_delta):
    ids_priced = {p["pid"] for p in pos}
    days = sorted({day(o["decision_ms"]["entry"]) for o in opens.values()})
    out = {}
    for stake in (0.10, 0.25):
        for model in ("flat", "press"):
            blk = {}
            for d in days:
                n_ent = sum(1 for o in opens.values() if day(o["decision_ms"]["entry"]) == d)
                nets = []
                for p in pos:
                    r = p["stakes"].get(stake)
                    if r is None or day(p["t0"]) != d:
                        continue
                    leg = r["legs"][rule][f"{model}_{lat}"]
                    nets.append(leg["net_lamports"] + send_addback(leg["state"], send_delta))
                srt = sorted(nets, reverse=True)
                blk[d] = {"n_entries": n_ent, "n_priced": len(nets), "n_unpriced": n_ent - len(nets), "total_sol": sum(nets) / 1e9,
                          "ex_top3_sol": sum(srt[3:]) / 1e9 if len(nets) > 3 else (0.0 if nets else None)}
            out[f"{stake}:{model}"] = blk
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("ledger"); ap.add_argument("out"); ap.add_argument("rule", nargs="?", default="total"); ap.add_argument("lat", nargs="?", default="1p9")
    ap.add_argument("--send-lamports", type=int, default=None); ap.add_argument("--ledger-send", type=int, default=1_673_840)
    a = ap.parse_args(argv)
    led, outp, rule, lat = a.ledger, a.out, a.rule, a.lat
    send_delta = (a.ledger_send - a.send_lamports) if a.send_lamports is not None else 0
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
                rows.append({"t0": p["t0"], "t1": p["t1"], "reason": p["reason"], "net": leg["net_lamports"] + send_addback(leg["state"], send_delta), "state": leg["state"], "ph": phase(p["t0"]), "day": day(p["t0"])})
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
    res["send_delta_lamports"] = send_delta
    res["entries_by_day"] = entries_by_day(opens, pos, rule, lat, send_delta)
    json.dump(res, open(outp, "w"), indent=1, default=str)
    print("ok", len(pos), res["states"])


if __name__ == "__main__":
    main()
