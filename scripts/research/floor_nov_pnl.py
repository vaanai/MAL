#!/usr/bin/env python3
"""floor_nov_pnl: read the scratch ledger written by floor_nov_paper.py and report per-day and pooled paper P&L at every stake the ledger carries (0.10 and 0.25 today; more with floor_nov_paper.py --stakes) (flat-15% and pressure legs),
with first-come caps K. Entry-day = UTC day of the entry decision. Phases: pre (< 2026-11-07T18Z), cutover (18Z hour, excluded), post (>= 19Z). Counts/aggregates only.
Usage: floor_nov_pnl.py LEDGER_DIR OUT_JSON [RULE=total] [LAT=1p9] [--send-lamports 160000 [--ledger-send 1673840]]
Rent correction (manager22 erratum, 2026-11-12): the first replay charged send = 1,673,840 lamports per send, of which 1,513,840 is refundable token-ATA rent. With
--send-lamports S the reader restates a ledger priced at ledger_send to S, D = ledger_send - S, following tools/arena_stack.py fail_legs / arena_paper.book_fail_legs:
a FILLED leg is (1-f)*(proceeds - stake - 2*send) - f*send, so the leg moves by (2-f)*D (flat f = 0.15; press f = p, the row's calc[lat].by_rule[rule].p_press_provisional);
a guarded or stranded/invalid row bypasses the blend and pays one send, so it moves by D. Not exact when the stranded test (proceeds < send) flips between the two
sends: re-pricing with floor_nov_paper.py --send 160000 is exact. A ledger already priced at S needs no flag. Also writes entries_by_day (a day with any open_at_tape_end entry is INCOMPLETE: counts only, no total; --to-ms adds the exit-completeness bound) and positions (for pool pairing):
total SOL and ex-top-3 SOL (None when n<=3) per UTC entry day, complete days only, per stake and leg (the 11-07T18Z cut-over hour IS excluded there, as in the funnel report). NOTE: pooled sol_per_day uses fixed 11-03..11-11 day denominators; use entries_by_day for other windows.
"""
import argparse, glob, json, sys, statistics, collections
from datetime import datetime, timezone
from pathlib import Path

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


FAIL_FLAT = 0.15


def send_addback(state, delta, model="flat", p_press=None):
    """lamports to add back to a leg net when a ledger priced at send L is restated to send L - delta (see module docstring)"""
    if not delta:
        return 0
    if state != "filled":
        return delta  # guarded / stranded / entry_invalid / exit_invalid: one send paid, no fail blend
    f = FAIL_FLAT if model == "flat" else p_press
    if f is None:
        raise ValueError("press leg needs the row's p_press_provisional")
    return (2 - f) * delta


def _p(r, rule, lat):
    return r.get("calc", {}).get(lat, {}).get("by_rule", {}).get(rule, {}).get("p_press_provisional")


# Exit-completeness bound (derived from the rule/engine constants, not tuned). FLOOR has no stop-loss: a position exits only on TP 1.5x or the 24 h time stop
# (tools/arena_rule_l1.py:47-52 REASON_TP/REASON_TIME, :85 HOLD_MS = 86_400_000 + LEG_MS 1_900; time exit fires in on_tick at :179 when T >= decision_ms + HOLD_MS).
# The host ticks every TICK_MS = 1_000 (tools/arena_rules.py:67) and stops at to_ms (arena_rules.py:572-588, ticks T < t_to), so the due tick is <= decision_ms + HOLD_MS + TICK_MS.
# The engine then needs feed up to exit_ms + (exit_lag_slots(sps)+1)*sps*1000 (tools/arena_paper.py:1215) to price or exclude it; exit_lag_slots = ceil(0.55 s/sps) (arena_stack.py:66-70),
# so (lag+1)*sps < 0.55 + 2*sps <= 1.55 s for sps <= 0.5 s (the slot is ~0.4 s). Bound: tape end >= last_entry + HOLD_MS + TICK_MS + 1_550 ms.
# The 1_550 ms is a FLOOR, not a true upper bound: _needed_slot also adds the feed delay d = 7 slots (arena_paper.py:1209-1211, ~2.8 s) and a pool with no print after the exit
# resolves only once the global feed is GRACE_SLOTS = 10_000 slots (~1 h) past need (arena_paper.py:86, :1274, the derived_last path); the replay never calls engine.flush()
# (arena_paper.py:1302-1305), so an unresolved position stays pending = open_at_tape_end. That only makes a day INCOMPLETE (stricter), never looser; the data test is the second half of the rule.
TICK_MS = 1_000
EXIT_LEG_MARGIN_MS = 1_550


def hold_ms():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from tools import arena_rule_l1 as L
    return L.HOLD_MS


def complete_bound_ms(last_entry_ms):
    """earliest tape end (ms) at which every position entered at or before last_entry_ms has an exit decision (or an engine exclusion)"""
    return last_entry_ms + hold_ms() + TICK_MS + EXIT_LEG_MARGIN_MS


BASE_STAKES = (0.10, 0.25)


def stakes_of(trades, excl):
    """stakes the ledger carries (trade and excluded rows), always including the two the A/B table reads; 2-stake ledger -> (0.1, 0.25) as before"""
    seen = {s for t in trades.values() for s in t} | {s for x in excl.values() for s in x}
    return tuple(sorted(set(BASE_STAKES) | seen))


def entries_by_day(opens, trades, excl, rule, lat, send_delta, to_ms=None):
    """-> (by_day, positions). The 11-07T18Z cut-over hour is skipped (as in the funnel report). Per position and stake the status is
    closed (trade row), engine_excluded (engine `excluded` row; reason kept) or open_at_tape_end (neither: no exit decision by the end of the tape).
    A day is complete only if it has no open_at_tape_end entry (any stake) and, when to_ms is given, to_ms >= complete_bound_ms(last entry of the day).
    An incomplete day carries counts only: total_sol / ex_top3_sol are None."""
    stakes = stakes_of(trades, excl)
    ents = {pid: o for pid, o in opens.items() if phase(o["decision_ms"]["entry"]) != "cutover"}
    days = sorted({day(o["decision_ms"]["entry"]) for o in ents.values()})
    positions = []
    for pid, o in sorted(ents.items(), key=lambda kv: kv[1]["decision_ms"]["entry"]):
        rec = {"pid": pid, "pool": o["pool"], "t0": o["decision_ms"]["entry"], "day": day(o["decision_ms"]["entry"]), "status": {}, "reason": {}, "net": {}}
        t1s = [t["decision_ms"]["exit"] for t in trades.get(pid, {}).values() if t.get("decision_ms", {}).get("exit") is not None]
        rec["t1"] = min(t1s) if t1s else None  # exit decision ms (None: no priced exit); board input
        for stake in stakes:
            t, x = trades.get(pid, {}).get(stake), excl.get(pid, {}).get(stake)
            rec["status"][str(stake)] = "closed" if t is not None else "engine_excluded" if x is not None else "open_at_tape_end"
            if x is not None and t is None:
                rec["reason"][str(stake)] = x
            if t is not None:
                for model in ("flat", "press"):
                    leg = t["legs"][rule][f"{model}_{lat}"]
                    rec["net"][f"{stake}:{model}"] = leg["net_lamports"] + send_addback(leg["state"], send_delta, model, _p(t, rule, lat))
        # landing-state fields for the forward A/B landing bins (added keys only; raw ledger values, derivations live in floor_forward_ab.py)
        tr_any = trades.get(pid, {})
        src = tr_any.get(0.25) or tr_any.get(0.10)
        if src is not None:
            c, m = (src.get("calc") or {}).get(lat) or {}, src.get("meta") or {}
            rec["landing"] = {"Qe": c.get("Qe"), "Be": c.get("Be"), "v": m.get("v"), "mark0": m.get("mark0")}
        rec["leg_state"] = {str(s): t["legs"][rule][f"flat_{lat}"]["state"] for s, t in tr_any.items()}
        rec["exit_reason"] = {str(s): t.get("exit_reason") for s, t in tr_any.items()}
        positions.append(rec)
    out = {}
    for stake in stakes:
        for model in ("flat", "press"):
            blk = {}
            for d in days:
                ps = [p for p in positions if p["day"] == d]
                st = [p["status"][str(stake)] for p in ps]
                nets = [p["net"][f"{stake}:{model}"] for p in ps if p["status"][str(stake)] == "closed"]
                n_open_any = sum(1 for p in ps if "open_at_tape_end" in p["status"].values())
                last = max(p["t0"] for p in ps)
                bound_ok = to_ms is None or to_ms >= complete_bound_ms(last)
                complete = n_open_any == 0 and bound_ok
                srt = sorted(nets, reverse=True)
                reasons = collections.Counter(p["reason"][str(stake)] for p in ps if str(stake) in p["reason"])
                blk[d] = {"n_entries": len(ps), "n_closed": len(nets), "n_priced": len(nets), "n_engine_excluded": st.count("engine_excluded"), "exclusion_reasons": dict(reasons),
                          "n_open_at_tape_end": st.count("open_at_tape_end"), "last_entry_ms": last, "complete_bound_ms": complete_bound_ms(last), "complete": complete,
                          "total_sol": sum(nets) / 1e9 if complete else None,
                          "ex_top3_sol": (sum(srt[3:]) / 1e9 if len(nets) > 3 else None) if complete else None}
            out[f"{stake}:{model}"] = blk
    return out, positions


def load_variant(ledger):
    """variant.json written by floor_nov_paper.py next to the ledger (or in its parent). Absent -> unrecorded (ledgers made before the header existed)."""
    for p in (Path(ledger) / "variant.json", Path(ledger).parent / "variant.json"):
        if p.exists():
            return json.load(open(p))
    return {"variant": None, "frozen_rule": None, "note": "no variant.json: ledger predates the header; do not assume it is the frozen rule"}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("ledger"); ap.add_argument("out"); ap.add_argument("rule", nargs="?", default="total"); ap.add_argument("lat", nargs="?", default="1p9")
    ap.add_argument("--send-lamports", type=int, default=None); ap.add_argument("--ledger-send", type=int, default=1_673_840)
    ap.add_argument("--to-ms", type=int, default=None, help="tape end (ms, exclusive); enables the exit-completeness bound per entry day. Default None = no bound (JSON bound_applied=false); floor_forward_ab.py table refuses such a file")
    a = ap.parse_args(argv)
    led, outp, rule, lat = a.ledger, a.out, a.rule, a.lat
    send_delta = (a.ledger_send - a.send_lamports) if a.send_lamports is not None else 0
    opens, trades, other = {}, collections.defaultdict(dict), collections.Counter()
    excl = collections.defaultdict(dict)
    for f in sorted(glob.glob(led + "/*/trades-*.jsonl")):
        for line in open(f):
            r = json.loads(line)
            k = r.get("kind")
            if k == "open":
                opens[r["position_id"]] = r
            elif k == "trade":
                trades[r["position_id"]][r["stake_sol"]] = r
            else:
                if k == "excluded":
                    excl[r["position_id"]][r["stake_sol"]] = r.get("exclusion_reason") or "unknown"
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
    for stake in stakes_of(trades, excl):
        for model in ("flat", "press"):
            key = f"{stake}:{model}"
            rows = []
            for p in pos:
                r = p["stakes"].get(stake)
                if r is None:
                    continue
                leg = r["legs"][rule][f"{model}_{lat}"]
                rows.append({"t0": p["t0"], "t1": p["t1"], "reason": p["reason"], "net": leg["net_lamports"] + send_addback(leg["state"], send_delta, model, _p(r, rule, lat)), "state": leg["state"], "ph": phase(p["t0"]), "day": day(p["t0"])})
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
    res["entries_by_day"], res["positions"] = entries_by_day(opens, trades, excl, rule, lat, send_delta, a.to_ms)
    res["to_ms"] = a.to_ms
    res["bound_applied"] = a.to_ms is not None  # the A/B table refuses a pnl file without it
    res["header"] = load_variant(led)
    res["n_open_at_tape_end_any_stake"] = sum(1 for p in res["positions"] if "open_at_tape_end" in p["status"].values())
    if res["n_open_at_tape_end_any_stake"]:  # K-cap figures must never be quoted from a ledger with open positions at tape end (QP #1331)
        res["out"] = None
        res["out_refused"] = "%d position(s) open_at_tape_end: K-cap per_day/pooled figures withheld" % res["n_open_at_tape_end_any_stake"]
    res["pooled_note"] = "`out` (per_day/pooled) counts closed positions only and is NOT censor-corrected; use entries_by_day (complete days) for any read"
    json.dump(res, open(outp, "w"), indent=1, default=str)
    print("ok", len(pos), res["states"])


if __name__ == "__main__":
    main()
