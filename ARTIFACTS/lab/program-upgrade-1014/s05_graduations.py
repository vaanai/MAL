"""Step 5: graduations, the migrate path and the quote mix, after the pump redeploy (all newest migrate txs are post-deploy).

Uses the monitor's own sampler (m.sample_graduations: newest N successful migrate txs on the migration fee account, one
getTransaction each), then reads each graduate's BondingCurve account (depth at byte 141, quote_mint at 83, size) in one
getMultipleAccounts, and for each non-WSOL quote mint reads that mint's own pump BondingCurve (PDA ["bonding-curve", mint]) to see
whether the quote is a pump coin and at what depth. Counts only. No trade size, price or outcome is read.
"""
import json
import time
from collections import Counter

from common import client, m, write_json

N = 60
c = client(max_calls=110)
errs = Counter()
cutoff = int(time.time()) - 120
grads = m.sample_graduations(c, N, cutoff, errs, max_pages=2)
m.annotate_mayhem(c, grads)
print("graduations", len(grads), "errs", dict(errs))


def curve_fields(d):
    if d is None or len(d) < 142:
        return None
    return dict(space=len(d), complete=d[48], mayhem=d[81], cashback=d[82], quote_mint=m.b58encode(d[83:115]), depth=d[141])


curves = [g["curve"] for g in grads]
r = c.call("getMultipleAccounts", [curves, {"encoding": "base64", "commitment": "finalized"}])
for g, v in zip(grads, r["value"]):
    g["curve_acct"] = curve_fields(m._account_bytes(v)) if v else None
    g["curve_space"] = v["space"] if v else None


def qclass(q):
    return "wsol" if q == m.WSOL_MINT else "usdc" if q == m.USDC_MINT else "other"


# the quote coins of the non-WSOL graduations: is the quote mint itself a pump coin, and at what depth?
other_q = sorted({g["quote_mint"] for g in grads if qclass(g["quote_mint"]) == "other"})
qcurve = {}
if other_q:
    pdas = {q: m.find_program_address([b"bonding-curve", m.b58decode(q)], m.PUMP_PROGRAM)[0] for q in other_q}
    rr = c.call("getMultipleAccounts", [list(pdas.values()), {"encoding": "base64", "commitment": "finalized"}])
    for q, v in zip(pdas, rr["value"]):
        qcurve[q] = curve_fields(m._account_bytes(v)) if v else None

summary = dict(
    n=len(grads), slot_min=min(g["slot"] for g in grads), slot_max=max(g["slot"] for g in grads),
    block_time_min=min(g["block_time"] for g in grads), block_time_max=max(g["block_time"] for g in grads),
    migrate_ix=dict(Counter(g["migrate_ix"] for g in grads)),
    quote_class=dict(Counter(qclass(g["quote_mint"]) for g in grads)),
    mayhem=dict(Counter(str(g.get("mayhem")) for g in grads)),
    init_boost=dict(Counter(g["init_boost"] for g in grads)),
    synthetic_hint_post_complete_in_tx=sum(1 for g in grads if g["post_complete_in_tx"]),
    curve_acct_found=sum(1 for g in grads if g["curve_acct"]),
    curve_space=dict(Counter(g["curve_space"] for g in grads)),
    graduate_depth=dict(Counter(f"{qclass(g['quote_mint'])}/depth={(g['curve_acct'] or {}).get('depth')}" for g in grads)),
    curve_quote_mint_matches_migration=dict(Counter(
        "native_or_wsol" if (g["curve_acct"] and g["curve_acct"]["quote_mint"] in (m.NATIVE_SOL, m.WSOL_MINT)) else ("equal" if (g["curve_acct"] and g["curve_acct"]["quote_mint"] == g["quote_mint"]) else "other/none") for g in grads)),
    non_wsol=[dict(sig=g["sig"][:12], slot=g["slot"], migrate_ix=g["migrate_ix"], quote_class=qclass(g["quote_mint"]), mayhem=g.get("mayhem"), init_boost=g["init_boost"],
                   depth=(g["curve_acct"] or {}).get("depth"), quote_mint=g["quote_mint"][:8] + "...", quote_is_pump_coin=bool(qcurve.get(g["quote_mint"])),
                   quote_coin_depth=(qcurve.get(g["quote_mint"]) or {}).get("depth")) for g in grads if qclass(g["quote_mint"]) != "wsol"],
    n_distinct_other_quote_mints=len(other_q),
    errs=dict(errs), attempts=c.total_calls, by_method=dict(c.calls),
)
print(json.dumps(summary, indent=1))
write_json("s05_graduations.json", summary)
