"""Step 9: Pool.virtual_quote_reserves (V, i128 at byte 245 of the PumpSwap Pool account, pump_amm IDL) of the same 60 graduates as
step 5, read now. Same 60 migrate txs (cache hits; cutoff = the step-5 sample's newest block_time). One getMultipleAccounts for the
60 pools. V is a pool parameter, not a price: the A3 monitor and the earlier reviews read it the same way. It is the CURRENT V, not
V0 (a trade can move V after the open; the pools are 0 to about 100 minutes old), so this shows the band, not V0 itself.
Counts: how many WSOL-quoted pools have V inside CAP-PICK's V0 band [17.5e9, 17.7e9] lamports (EXP-022 section 3)."""
import json
import statistics
from collections import Counter

from common import OUT, client, m, write_json

c = client(max_calls=20)
s5 = json.load(open(OUT / "s05_graduations.json"))
grads = m.sample_graduations(c, 60, s5["block_time_max"], Counter(), max_pages=2)
r = c.call("getMultipleAccounts", [[g["pool"] for g in grads], {"encoding": "base64", "commitment": "finalized"}])
rows = []
for g, v in zip(grads, r["value"]):
    d = m._account_bytes(v) if v else None
    if not d or len(d) < 261:
        rows.append(dict(quote="missing"))
        continue
    V = int.from_bytes(d[245:261], "little", signed=True)
    q = m.b58encode(d[75:107])
    rows.append(dict(quote="wsol" if q == m.WSOL_MINT else "usdc" if q == m.USDC_MINT else "other", V=V, mayhem=bool(d[243]), len=len(d), matches_migration_quote=(q == g["quote_mint"]), slot=g["slot"]))
lo, hi = 17_500_000_000, 17_700_000_000
w = [x for x in rows if x["quote"] == "wsol"]
vs = [x["V"] for x in w]
out = dict(
    n_graduates=len(grads), same_sample_as_step5=(min(g["slot"] for g in grads) == s5["slot_min"] and max(g["slot"] for g in grads) == s5["slot_max"]), pool_len=dict(Counter(x.get("len") for x in rows)),
    pool_quote_equals_migration_event_quote=sum(1 for x in rows if x.get("matches_migration_quote")),
    quote_class=dict(Counter(x["quote"] for x in rows)), n_wsol=len(w),
    wsol_V_lamports=dict(min=min(vs), median=statistics.median(vs), max=max(vs)), wsol_V_in_band=sum(1 for v in vs if lo <= v <= hi),
    wsol_V_in_band_nonmayhem=sum(1 for x in w if lo <= x["V"] <= hi and not x["mayhem"]), n_wsol_nonmayhem=sum(1 for x in w if not x["mayhem"]),
    wsol_V_outside_band=[dict(V=x["V"], mayhem=x["mayhem"]) for x in w if not lo <= x["V"] <= hi],
    # the s0 range of the 2026-10-13 fees review (17,584,505,288 to 17,584,506,405 on 13 post-fees-deploy pools): a fresh pool opens there; a pool that has since kept fees (v2 trades) sits lower
    wsol_nonmayhem_V_in_10_13_s0_range=sum(1 for x in w if not x["mayhem"] and 17_584_505_288 <= x["V"] <= 17_584_506_405),
    wsol_nonmayhem_V_above_s0_range=sum(1 for x in w if not x["mayhem"] and x["V"] > 17_584_506_405),
    wsol_nonmayhem_V_below_band_lo=sum(1 for x in w if not x["mayhem"] and x["V"] < lo),
    wsol_mayhem_V_nonpositive=sum(1 for x in w if x["mayhem"] and x["V"] <= 0),
    pool_len_287=[dict(slot=x["slot"], quote=x["quote"], mayhem=x["mayhem"]) for x in rows if x.get("len") == 287],
    other_V_lamport_units_of_quote_not_sol=[x["V"] for x in rows if x["quote"] == "other"],
    attempts_in_this_script=c.total_calls,
)
print(json.dumps(out, indent=1))
write_json("s09_pool_v.json", out)
