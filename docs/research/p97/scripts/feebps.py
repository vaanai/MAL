# Reads ONLY fee basis-point fields from PumpSwap Buy/Sell events of pools proven older than 360 s
# (their creator fees were already swept by transfer_creator_fees_to_pump in a round >= 360 s before this read).
# Reserve and amount fields are skipped by offset and never printed or stored.
import json, sys, struct, base64, datetime
sys.path.insert(0, ".")
from rpc import call
from tx import get
BUY = bytes([103,244,82,31,44,245,119,119]); SELL = bytes([62,47,55,10,165,3,220,42])
def fee_fields(b, is_buy):
    o = 8 + 8 + 8*6 + 8          # timestamp, 6 amounts/reserves, quote amount
    lp_bps = struct.unpack_from("<Q", b, o)[0]; o += 16
    pr_bps = struct.unpack_from("<Q", b, o)[0]; o += 16
    o += 16 + 32*7                # two amounts, 7 pubkeys incl coin_creator
    cc_bps = struct.unpack_from("<Q", b, o)[0]; o += 16
    if is_buy:
        o += 1 + 8*4 + 8          # track_volume, 3 u64, i64, min_base_amount_out
        n = struct.unpack_from("<I", b, o)[0]; o += 4 + n
    o += 32                       # cashback bps/amt, buyback bps/amt
    o += 16 + 1 + 8               # i128 vqr, can_boost, base_supply
    hr_bps = struct.unpack_from("<Q", b, o)[0]
    return {"lp_bps": lp_bps, "protocol_bps": pr_bps, "coin_creator_bps": cc_bps, "holder_rewards_bps": hr_bps}
pool, round_end = sys.argv[1], int(sys.argv[2])
sigs = call("getSignaturesForAddress", [pool, {"limit": 10}])
for s in sigs:
    if s["err"] or s["blockTime"] < round_end + 360: continue
    r = get(s["signature"])
    for l in r["meta"]["logMessages"] or []:
        if l.startswith("Program data: "):
            b = base64.b64decode(l[14:])
            if b[:8] in (BUY, SELL):
                print(pool[:8], datetime.datetime.fromtimestamp(s["blockTime"], datetime.UTC).strftime("%m-%dT%H:%M:%S"), "buy" if b[:8] == BUY else "sell", fee_fields(b, b[:8] == BUY)); sys.exit()
print(pool[:8], "no trade event found in last 10 sigs")
