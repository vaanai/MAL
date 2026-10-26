#!/usr/bin/env python3
"""P48 step B: QSINK count labels from p48_sigs.jsonl (step A), p48_pass1.jsonl and p48_pass4.jsonl.

Counts only. Buy and sell counts and SOL. No price, no P&L, no outcome of any position.

Landing marker (tape-only). PumpSwap tape reserves are PRE-trade. For each WSOL-quoted print of a top parent's pool
(pass 4), the next print in that pool (ordered by slot, receive time, event index) should show the quote reserve moved by
this print's pool_quote_amount (+ for a buy, - for a sell). A print is "applied" when the move is within 25% of the
expected move plus 0.002 SOL, else "not applied". Rows of transactions that never landed carry the pre-state and do not
move the next print. Public-RPC samples show the marker is noisy in busy pools (57 of 60 sampled "not applied" rows
landed), so the deciding split is by signer: "phantom" for the PHANTOM signers below, "landed" for every other signer.
Reversal labels, trader shares and child cohorts use landed-class legs only (COUNTS.md section 0).

Seal guard: a leg in a WSOL pool whose pool_create is on the tape, with virtual quote reserve in [17.5e9, 17.7e9], within
360 s of that pool_create, is dropped and counted (EXP-024 Look-2 seal). No G0 trigger, BOOST test or outcome is computed.
Usage: p48_analyze.py DIR OUT_JSON
"""
import bisect, collections, datetime as dt, json, sys

WSOL = "So11111111111111111111111111111111111111112"
PUMPTOK = "pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
BAND = (17_500_000_000, 17_700_000_000)
# Signers whose tape rows do not land on chain (public-RPC getSignatureStatuses with searchTransactionHistory: BUpWiWy8
# 0 of 60 routed buys >= 0.5 SOL and 0 of 20 child buys found; oi1ewFJP 0 of 10 found; controls 240 of 240 found).
# Their reserve marker is 99.1% and 98.5% not applied. Every other signer is "landed" class (sampled, not proven).
PHANTOM = frozenset({"BUpWiWy8Tqed4jy86dr7ZpoaE747DpQsrGnwmtpMwfBr", "oi1ewFJP7HikgdbjhnUboCzuWXHanAe4Wtvy8UFEnd6"})
END_MS = 1792886400000  # 2026-10-25T00:00Z, end of the read data
H = {"1h": 3_600_000, "6h": 21_600_000, "24h": 86_400_000}


def day(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d")


def pclass(m):
    return "PUMP" if m == PUMPTOK else "USDC" if m == USDC else "pumpcoin" if m.endswith("pump") else "other"


def main(d, out):
    assert END_MS == int(dt.datetime(2026, 10, 25, tzinfo=dt.timezone.utc).timestamp() * 1000)
    # creates
    C = collections.defaultdict(collections.Counter)
    creators = collections.defaultdict(collections.Counter)
    pool_create = {}
    for l in open(f"{d}/p48_pass1.jsonl"):
        r = json.loads(l)
        if r["type"] == "create":
            dd = day(r["t_recv_ms"]); q = r["quote_mint"]
            C[dd]["creates_" + pclass(q)] += 1
            C[dd]["creates_all_nonwsol"] += 1
            if q == PUMPTOK:
                creators[dd][r.get("creator")] += 1
        elif r["type"] == "pool_create":
            pool_create[r["pool"]] = (r["t_recv_ms"], r.get("vq"), r.get("quote_mint"))
    creates = {dd: dict(v) for dd, v in sorted(C.items())}
    for dd, c in creators.items():
        n = sum(c.values()); top = c.most_common(1)[0][1]
        creates[dd]["PUMP_children_distinct_creators"] = len(c)
        creates[dd]["PUMP_children_top_creator_share"] = round(top / n, 4)

    # landing marker from pass 4
    bypool = collections.defaultdict(list)
    vol = collections.defaultdict(collections.Counter)
    for l in open(f"{d}/p48_pass4.jsonl"):
        r = json.loads(l)
        if r[5] != WSOL:
            continue
        r[12] = None  # ix_name is not used here (memory)
        bypool[r[6]].append(r)
    mark = {}
    for pool, rs in bypool.items():
        rs.sort(key=lambda r: (r[1], r[0], r[3] or 0))
        seen = set()
        for a, b in zip(rs, rs[1:] + [None]):
            ok = None
            if b is not None and a[9] is not None and a[10] is not None and b[10] is not None:
                exp = a[9] if a[7] == "buy" else -a[9]
                ok = abs((b[10] - a[10]) - exp) <= 0.25 * abs(exp) + 2_000_000
                mark[(a[2], a[3], pool)] = ok
            k = (a[2], a[3])
            dup = k in seen; seen.add(k)
            dd = day(a[0]); mm = a[4]
            tag = "applied" if ok else ("not_applied" if ok is False else "unmarked")
            vol[(mm, dd)][f"{a[7]}_n_{tag}"] += 1
            vol[(mm, dd)][f"{a[7]}_sol_{tag}"] += a[8] or 0
            tag2 = "phantom" if a[11] in PHANTOM else "landed"
            vol[(mm, dd)][f"{a[7]}_n_{tag2}"] += 1
            vol[(mm, dd)][f"{a[7]}_sol_{tag2}"] += a[8] or 0
            if dup:
                vol[(mm, dd)]["dup_sig_rows"] += 1
    del bypool

    # routed legs
    R = collections.defaultdict(collections.Counter)            # (class, day) counters
    RP = collections.defaultdict(collections.Counter)           # (parent, day) counters
    trader_buy = collections.defaultdict(collections.Counter)   # (class, day) -> trader -> applied buy SOL
    kids_routed = collections.defaultdict(set)
    seal_drop = collections.Counter()
    events = []  # applied routed legs: (t, trader, child, parent, side, sol)
    for l in open(f"{d}/p48_sigs.jsonl"):
        if '"legs":[]' in l:
            continue
        r = json.loads(l)
        t = r["t"]; dd = day(t)
        kid_sides = {k[2] for k in r["kids"]}
        kid = r["kids"][0][0]
        for m, pool, side, sol, tr, vq in r["legs"]:
            pc = pool_create.get(pool)
            if pc and pc[2] == WSOL and pc[1] is not None and BAND[0] <= pc[1] <= BAND[1] and 0 <= t - pc[0] <= 360_000:
                seal_drop[dd] += 1
                continue
            if side not in kid_sides:
                R[(pclass(m), dd)]["legs_side_mismatch"] += 1
            ok = mark.get((r["sig"], None, pool))
            if ok is None:
                for ev in range(0, 6):
                    ok = mark.get((r["sig"], ev, pool))
                    if ok is not None:
                        break
            tag = "applied" if ok else ("not_applied" if ok is False else "unmarked")
            big = "ge0.5" if sol >= 500_000_000 else "lt0.5"
            tag2 = "phantom" if tr in PHANTOM else "landed"
            for key in (R[(pclass(m), dd)], RP[(m, dd)]):
                for tg in (tag, tag2):
                    key[f"{side}_n_{tg}"] += 1
                    key[f"{side}_sol_{tg}"] += sol
                    key[f"{side}_{big}_n_{tg}"] += 1
                    key[f"{side}_{big}_sol_{tg}"] += sol
            if tag2 == "landed":
                if side == "buy":
                    trader_buy[(pclass(m), dd)][tr] += sol
                    kids_routed[(pclass(m), dd)].add(kid)
                events.append((t, tr, kid, m, side, sol))

    # reversal labels: same trader, same child, applied routed sell after an applied routed buy
    events.sort()
    sells = collections.defaultdict(list)
    for t, tr, kid, m, side, sol in events:
        if side == "sell":
            sells[(tr, kid)].append((t, sol))
    REV = collections.defaultdict(collections.Counter)
    for t, tr, kid, m, side, sol in events:
        if side != "buy":
            continue
        dd = day(t); pc = pclass(m)
        s = sells.get((tr, kid), [])
        ts = [x[0] for x in s]
        for hn, hms in H.items():
            if t + hms > END_MS:
                REV[(pc, dd)][f"buys_censored_{hn}"] += 1
                continue
            i = bisect.bisect_right(ts, t); j = bisect.bisect_right(ts, t + hms)
            REV[(pc, dd)][f"buys_{hn}"] += 1
            REV[(pc, dd)][f"buy_sol_{hn}"] += sol
            if j > i:
                REV[(pc, dd)][f"buys_with_sell_{hn}"] += 1
    # per child cohort: all traders, applied routed buy vs sell SOL within h of the child's first applied routed buy
    first = {}
    for t, tr, kid, m, side, sol in events:
        if side == "buy" and kid not in first:
            first[kid] = (t, m)
    COH = collections.defaultdict(collections.Counter)
    for t, tr, kid, m, side, sol in events:
        f = first.get(kid)
        if f is None or t < f[0]:
            continue
        dd = day(f[0]); pc = pclass(f[1])
        for hn, hms in H.items():
            if f[0] + hms > END_MS:
                continue
            if t <= f[0] + hms:
                COH[(pc, dd)][f"{side}_sol_{hn}"] += sol
                COH[(pc, dd)][f"{side}_n_{hn}"] += 1
    for (pc, dd) in list(COH):
        for hn, hms in H.items():
            COH[(pc, dd)][f"children_{hn}"] = sum(1 for k, f in first.items() if pclass(f[1]) == pc and day(f[0]) == dd and f[0] + hms <= END_MS)

    def lam(c):
        return {k: (round(v / 1e9, 3) if "_sol" in k or k.startswith("sol") else v) for k, v in sorted(c.items())}

    top_parents = collections.Counter()
    for (m, dd), c in RP.items():
        top_parents[m] += c["buy_sol_landed"]
    res = {
        "end_ms": END_MS,
        "creates": creates,
        "routed_by_class": {f"{k[0]}|{k[1]}": lam(v) for k, v in sorted(R.items())},
        "routed_top_parents": {m: {dd: lam(RP[(m, dd)]) for dd in sorted({k[1] for k in RP if k[0] == m})} for m, _ in top_parents.most_common(15)},
        "parent_pool_volume": {f"{m}|{dd}": lam(v) for (m, dd), v in sorted(vol.items()) if m in dict(top_parents.most_common(15)) or m in (PUMPTOK, USDC)},
        "routed_children_distinct": {f"{k[0]}|{k[1]}": len(v) for k, v in sorted(kids_routed.items())},
        "top_trader_share_landed_buy_sol": {f"{k[0]}|{k[1]}": {"traders": len(v), "top": v.most_common(1)[0][0], "top_share": round(v.most_common(1)[0][1] / sum(v.values()), 4), "top3_share": round(sum(x for _, x in v.most_common(3)) / sum(v.values()), 4)} for k, v in sorted(trader_buy.items()) if sum(v.values()) > 0},
        "reversal_same_trader_child": {f"{k[0]}|{k[1]}": lam(v) for k, v in sorted(REV.items())},
        "child_cohort_flows": {f"{k[0]}|{k[1]}": lam(v) for k, v in sorted(COH.items())},
        "seal_guard_dropped_legs": dict(seal_drop),
        "marker_rows": len(mark),
    }
    json.dump(res, open(out, "w"), indent=1, sort_keys=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
