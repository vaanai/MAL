"""P41 (p37-cto-hold) counts, COUNTS ONLY. Rules: docs/research/p41-cto-hold/DECLARE.md.

Inputs: --events (JSON list from the public-RPC decode: one dict per admin_cto instruction with sig, blockTime,
mint, new_creator, is_holder_reward, creator_fee_bps, win) and --scan (output dir of tools/p41_cto_tape_scan.py).
Output: one JSON document of counts. No return, price path or P&L is computed; q_up24 is a direction count label.
"""
import argparse, bisect, collections, datetime as dt, json, statistics, sys

U = lambda s: int(dt.datetime.strptime(s, "%Y-%m-%dT%H").replace(tzinfo=dt.timezone.utc).timestamp())
WINDOWS = {"A": ("2026-08-14T12", "2026-08-28T12"), "B": ("2026-09-03T12", "2026-09-15T12"), "C": ("2026-09-18T23", "2026-09-25T07")}
H = 3600
# DECLARE Amendment 1: set P (admin_cto only) covers the full UTC days after the 2026-09-12 switch-over
P_DAYS = ["2026-09-13", "2026-09-14"] + [f"2026-09-{d}" for d in range(19, 25)]


def full_days(win):
    lo, hi = (U(x) for x in WINDOWS[win])
    d0 = (lo + 86399) // 86400 * 86400
    return [dt.datetime.fromtimestamp(d, dt.timezone.utc).strftime("%Y-%m-%d") for d in range(d0, hi - 86399, 86400)]


def kind(e):
    if e.get("new_creator"): return "new_creator"
    if e.get("is_holder_reward"): return "holder_reward_only"
    return "other_flags"


def qstats(xs):
    if not xs: return None
    xs = sorted(xs)
    q = lambda p: xs[min(len(xs) - 1, int(p * (len(xs) - 1) + 0.5))]
    return {"n": len(xs), "p25": q(0.25), "median": statistics.median(xs), "p75": q(0.75)}


def label(e, tr, creates, migs):
    """Per-event labels from the mint's tape rows (sorted by block_time, slot, event_index)."""
    t = e["blockTime"]; win = e["win"]; wend = U(WINDOWS[win][1])
    ts = [r["block_time"] for r in tr]
    i_le = bisect.bisect_right(ts, t) - 1
    out = {"sig": e["sig"], "mint": e["mint"], "t": t, "win": win, "kind": kind(e),
           "day": dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%d")}
    out["trades_1h_after"] = bisect.bisect_left(ts, t + H) - bisect.bisect_left(ts, t)
    out["trigger"] = out["trades_1h_after"] > 0
    prev = tr[i_le] if i_le >= 0 else None
    out["venue_t"] = prev["venue"] if prev else "none"
    out["quote_t_sol"] = prev["quote_reserve"] / 1e9 if prev and prev.get("quote_reserve") is not None else None
    # pool / curve age at t
    age, cens = None, None
    if out["venue_t"] == "pumpswap":
        m = [r["block_time"] for r in migs if r.get("type") == "migration" and r["block_time"] <= t] or \
            [r["block_time"] for r in migs if r.get("type") == "complete" and r["block_time"] <= t]
        if m: age, cens = t - min(m), False
        else:
            f = [r["block_time"] for r in tr if r["venue"] == "pumpswap"]
            if f: age, cens = t - f[0], True
    elif out["venue_t"] == "pump_bonding":
        c = [r["block_time"] for r in creates if r["block_time"] <= t]
        if c: age, cens = t - min(c), False
        elif ts: age, cens = t - ts[0], True
    out["age_s"], out["age_left_censored"] = age, cens
    out["censored24"] = t + 25 * H > wend
    if not out["censored24"]:
        out["alive24"] = bisect.bisect_left(ts, t + 25 * H) - bisect.bisect_left(ts, t + 23 * H) > 0
        j = bisect.bisect_right(ts, t + 24 * H) - 1
        later = tr[j] if j >= 0 else None
        if prev is None or later is None or out["quote_t_sol"] is None or later.get("quote_reserve") is None:
            out["q_up24"] = "no_quote"
        elif later["venue"] != prev["venue"]:
            out["q_up24"] = "venue_changed"
        else:
            out["q_up24"] = later["quote_reserve"] >= prev["quote_reserve"]
    return out


def summarize(rows, days_by_win):
    s = {"events": len(rows), "distinct_mints": len({r["mint"] for r in rows})}
    trig = [r for r in rows if r["trigger"]]
    s["triggers"] = len(trig); s["no_trade_1h"] = len(rows) - len(trig)
    per_day = collections.Counter(r["day"] for r in trig)
    alldays = [d for w in days_by_win for d in days_by_win[w]]
    s["triggers_per_full_day"] = {d: per_day.get(d, 0) for d in alldays}
    s["median_triggers_per_full_day"] = statistics.median([per_day.get(d, 0) for d in alldays]) if alldays else None
    s["median_by_window"] = {w: statistics.median([per_day.get(d, 0) for d in days_by_win[w]]) for w in days_by_win if days_by_win[w]}
    s["venue_t"] = dict(collections.Counter(r["venue_t"] for r in trig))
    for v in ("pumpswap", "pump_bonding"):
        s[f"quote_t_sol_{v}"] = qstats([r["quote_t_sol"] for r in trig if r["venue_t"] == v and r["quote_t_sol"] is not None])
        s[f"age_h_{v}"] = qstats([r["age_s"] / 3600 for r in trig if r["venue_t"] == v and r["age_s"] is not None])
        s[f"age_left_censored_{v}"] = sum(1 for r in trig if r["venue_t"] == v and r["age_left_censored"])
    unc = [r for r in trig if not r["censored24"]]
    s["uncensored"] = len(unc); s["censored24"] = len(trig) - len(unc)
    s["alive24_n"] = sum(1 for r in unc if r["alive24"])
    s["alive24_share"] = s["alive24_n"] / len(unc) if unc else None
    qs = [r for r in unc if isinstance(r["q_up24"], bool)]
    s["q_up24_n"] = sum(1 for r in qs if r["q_up24"]); s["q_up24_den"] = len(qs)
    s["q_up24_share"] = s["q_up24_n"] / len(qs) if qs else None
    s["q_up24_other"] = dict(collections.Counter(r["q_up24"] for r in unc if not isinstance(r["q_up24"], bool)))
    deep = [r for r in trig if r["quote_t_sol"] is not None and
            ((r["venue_t"] == "pumpswap" and r["quote_t_sol"] >= 20) or (r["venue_t"] == "pump_bonding" and r["quote_t_sol"] >= 40))]
    s["depth_ok_n"] = len(deep); s["depth_ok_share"] = len(deep) / len(trig) if trig else None
    return s


def verdict(s, covered_full_days):
    k = []
    if covered_full_days < 5: return "NOT_DECIDABLE", ["fewer than 5 covered full days"]
    if s["median_triggers_per_full_day"] is None or s["median_triggers_per_full_day"] < 3: k.append("K0")
    if s["alive24_share"] is None or s["alive24_share"] < 0.5: k.append("K1")
    if s["q_up24_share"] is None or s["q_up24_share"] < 0.25: k.append("K2")
    if s["depth_ok_share"] is None or s["depth_ok_share"] < 0.5: k.append("K3")
    return ("KILL" if k else "ADVANCE-TO-PRICING-DECLARE"), k


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", required=True); ap.add_argument("--scan", required=True)
    a = ap.parse_args(argv)
    ev = json.load(open(a.events))
    load = lambda n: [json.loads(l) for l in open(f"{a.scan}/{n}.jsonl")]
    by = {n: collections.defaultdict(list) for n in ("trades", "creates", "migrations")}
    for n in by:
        for r in load(n): by[n][r["mint"]].append(r)
    for rs in by["trades"].values(): rs.sort(key=lambda r: (r["block_time"], r["slot"], r.get("event_index") or 0))
    rows = [label(e, by["trades"].get(e["mint"], []), by["creates"].get(e["mint"], []), by["migrations"].get(e["mint"], [])) for e in ev]
    for r, e in zip(rows, ev): r["op"] = e.get("op", "admin_cto")
    days_s = {w: full_days(w) for w in WINDOWS}
    days_p = {w: [d for d in days_s[w] if d in P_DAYS] for w in WINDOWS}
    rows_p = [r for r in rows if r["op"] == "admin_cto"]
    res = {"set_P": summarize(rows_p, days_p), "set_S": summarize(rows, days_s),
           "set_P_new_creator": summarize([r for r in rows_p if r["kind"] == "new_creator"], days_p),
           "set_S_new_creator": summarize([r for r in rows if r["kind"] == "new_creator"], days_s)}
    res["covered_full_days"] = {"P": sum(len(v) for v in days_p.values()), "S": sum(len(v) for v in days_s.values())}
    for k in ("set_P", "set_S"):
        res[k]["verdict"], res[k]["kill_rules_hit"] = verdict(res[k], res["covered_full_days"][k[-1]])
    vs = (res["set_P"]["verdict"], res["set_S"]["verdict"])
    res["lane_verdict"] = ("ADVANCE-TO-PRICING-DECLARE" if vs == ("ADVANCE-TO-PRICING-DECLARE",) * 2 else
                           "NOT_DECIDABLE" if "NOT_DECIDABLE" in vs and "KILL" not in vs else "KILL")
    res["kinds"] = {f"{a}/{b}": n for (a, b), n in collections.Counter((r["op"], r["kind"]) for r in rows).items()}
    res["rows"] = rows
    json.dump(res, sys.stdout, indent=1)


if __name__ == "__main__":
    main()
