"""Builds tools/data/dbc_swap3_fixture.json from real tape hours (needs the tape + /data/.../cfg_raw.json). Usage: make_fixture.py OUT cfg_raw.json hour.jsonl.zst ..."""
import collections, json, subprocess, sys
sys.path.insert(0, ".")
from tools import dbc_config as C
out, rawp, *hours = sys.argv[1:]
raw = json.load(open(rawp))
cfgs = {k: C.decode_b64(v["data_b64"]) for k, v in raw.items()}
by = collections.defaultdict(list)
for f in hours:
    for l in subprocess.Popen(["zstd", "-dc", "-q", f], stdout=subprocess.PIPE).stdout:
        if b'"trade"' not in l:
            continue
        r = json.loads(l)
        if r.get("type") == "trade" and r["event"].startswith("EvtSwap3") and r["config"] in cfgs and r["quote_mint"][:4] == "So11":
            by[r["pool"]].append(r)
want = collections.Counter()
quota = {("PoolConfig", "buy", True): 30, ("PoolConfig", "buy", False): 20, ("PoolConfig", "sell", False): 30, ("ConfigWithTransferHook", "buy", False): 15, ("ConfigWithTransferHook", "sell", False): 15}
rows, used = [], {}
KEEP = ("pool", "config", "slot", "signature", "event", "ix", "side", "swap_mode", "fee_on_base_token", "has_referral", "next_sqrt_price", "trading_fee", "protocol_fee", "referral_fee",
        "amount_in", "amount_in_excl_fee", "amount_out", "amount_out_incl_transfer_fee", "base_vault_pre", "base_vault_post", "quote_vault_pre", "quote_vault_post", "quote_reserve_post")
for pool, rs in by.items():
    cnt = collections.Counter(r["slot"] for r in rs)
    prev = None
    for r in rs:
        ok = prev is not None and cnt[r["slot"]] == 1 and cnt[prev["slot"]] == 1 and prev["slot"] < r["slot"] and not r["has_referral"] and r["swap_mode"] == 0
        if ok:
            k = (cfgs[r["config"]]["kind"], r["side"], r["fee_on_base_token"])
            if want[k] < quota.get(k, 0):
                want[k] += 1
                d = {x: r.get(x) for x in KEEP}
                d["prev_next_sqrt_price"] = prev["next_sqrt_price"]
                rows.append(d)
                used[r["config"]] = raw[r["config"]]["data_b64"]
        prev = r
json.dump({"rows": rows, "configs": used}, open(out, "w"), indent=0)
print(len(rows), dict(want))
