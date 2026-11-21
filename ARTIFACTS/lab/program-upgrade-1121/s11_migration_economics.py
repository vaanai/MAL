"""QP ruling 384d99f3 Q1 item 9 (ii) and (iii): migration economics and pool accounts, M (post) vs M_pre, WSOL-quoted migrations.
(ii) per migration tx: CompletePumpAmmMigrationEvent sol_amount, mint_amount, pool_migration_fee; the CreatePoolEvent of the same tx: pool_base_amount,
     pool_quote_amount, initial_liquidity, is_mayhem_mode, creator_fee_bps; first-print V = virtual_quote_reserves (stored V BEFORE the trade) of the
     OLDEST PumpSwap Buy/Sell event of the pool (pool signature list paged back to its end, page budget 8; not reached = NOT OBSERVED for that pool).
     Chain structure constants of the migration only: nothing after the first print, no path, no outcome.
(iii) pool account (getMultipleAccounts, now): owner program, data length, 8-byte discriminator, coin_creator, is_mayhem_mode, is_cashback_coin,
     creator_fee_bps, can_edit_creator_fee, is_holder_reward, pool_base/quote token accounts (their owner programs = the token programs).
Public RPC, 0 credits."""
import base64, collections, gzip, json, os, statistics, sys
from pathlib import Path
sys.argv = [sys.argv[0]]
os.environ.setdefault("UPGRADE_RAW", "/data2/mal/scratch/a3-repin-1121/raw")
exec(open("s06_parity.py").read().split("acc = dict(")[0])
from common import LClient, OUT
BASE = Path(os.environ.get("A3_BASE", "/data2/mal/scratch/a3-repin-1121"))
MIG = json.load(open(BASE / "raw" / "migrate_index.json"))
WSOL = td.WSOL_MINT
c = LClient(max_calls=900, min_interval=0.5)


def events(tx):
    meta = tx.get("meta") or {}
    evs = [(pid, blob) for pid, blob in log_events(meta.get("logMessages") or [])]
    ms = collections.Counter(b for _, b in evs); keys = keys_of(tx)
    for g in meta.get("innerInstructions") or []:
        for ins in g["instructions"]:
            pid = keys[ins["programIdIndex"]] if ins["programIdIndex"] < len(keys) else None
            if pid in PNAME and ins.get("data"):
                d = b58d(ins["data"])
                if d[:8] == TAG and len(d) > 8:
                    if ms.get(d[8:], 0) > 0: ms[d[8:]] -= 1
                    else: evs.append((pid, d[8:]))
    out = {}
    for pid, blob in evs:
        if pid not in PNAME or len(blob) < 8: continue
        nm = EV_BY_PROG[pid].get(blob[:8])
        if nm in ("CompletePumpAmmMigrationEvent", "CreatePoolEvent"):
            try: out[nm], _ = parse_event(PNAME[pid], nm, blob)
            except Short: pass
    return out


def first_print_v(pool):
    """oldest PumpSwap Buy/Sell event of the pool: its virtual_quote_reserves (V before the trade). None = not reached or none."""
    before, last = None, None
    for _ in range(8):
        opts = {"limit": 1000, "commitment": "finalized"}
        if before: opts["before"] = before
        r = c.call("getSignaturesForAddress", [pool, opts]) or []
        if r: last = r; before = r[-1]["signature"]
        if len(r) < 1000: break
    else:
        return {"observed": False, "why": "page budget (8 x 1000) exhausted before the pool's first signature"}
    if not last: return {"observed": False, "why": "no signatures"}
    for s in reversed([x for x in last if x.get("err") is None][-12:]):   # oldest first
        t = c.call("getTransaction", [s["signature"], {"encoding": "json", "commitment": "finalized", "maxSupportedTransactionVersion": 1}])
        if not t: continue
        meta = t.get("meta") or {}; evs = [(pid, blob) for pid, blob in log_events(meta.get("logMessages") or [])]
        keys = keys_of(t)
        for g in meta.get("innerInstructions") or []:
            for ins in g["instructions"]:
                pid = keys[ins["programIdIndex"]] if ins["programIdIndex"] < len(keys) else None
                if pid == PID["pump_amm"] and ins.get("data"):
                    d = b58d(ins["data"])
                    if d[:8] == TAG and len(d) > 8: evs.append((pid, d[8:]))
        for pid, blob in evs:
            if pid == PID["pump_amm"] and len(blob) >= 8 and EV_BY_PROG[pid].get(blob[:8]) in ("BuyEvent", "SellEvent"):
                try: ev, _ = parse_event("pump_amm", EV_BY_PROG[pid][blob[:8]], blob)
                except Short: continue
                if ev["pool"] == pool:
                    return {"observed": True, "slot": s["slot"], "event": EV_BY_PROG[pid][blob[:8]], "virtual_quote_reserves": ev.get("virtual_quote_reserves")}
    return {"observed": False, "why": "no Buy/Sell event among the 12 oldest successful signatures"}


res = {}
for label in ("post", "pre"):
    rows = []
    for r in MIG[label]:
        if not r["quote_wsol"]: continue
        tx = json.loads(gzip.open(BASE / "raw" / "txs" / f"{r['sig']}.json.gz").read())["r"]
        e = events(tx); mg = e.get("CompletePumpAmmMigrationEvent"); cp = e.get("CreatePoolEvent")
        row = {"sig": r["sig"], "slot": r["slot"], "pool": r["pool"], "mint": r["mint"]}
        if mg: row.update({k: mg.get(k) for k in ("sol_amount", "mint_amount", "pool_migration_fee")})
        if cp: row.update({k: cp.get(k) for k in ("pool_base_amount", "pool_quote_amount", "initial_liquidity", "is_mayhem_mode", "creator_fee_bps", "base_amount_in", "quote_amount_in", "coin_creator")})
        rows.append(row)
    res[label] = {"rows": rows}
# first-print V for the first 20 post and 10 pre WSOL pools (the page budget bounds the old pools)
for label, n in (("post", 20), ("pre", 10)):
    for row in res[label]["rows"][:n]:
        row["first_print"] = first_print_v(row["pool"])
        print(label, row["pool"], row["first_print"], flush=True)
# pool accounts (iii): first 12 post and 12 pre WSOL pools
def acct(addrs):
    out = {}
    for i in range(0, len(addrs), 90):
        ch = addrs[i:i + 90]
        r = c.call("getMultipleAccounts", [ch, {"encoding": "base64", "commitment": "finalized"}])
        for a, v in zip(ch, r["value"]):
            out[a] = None if v is None else {"owner": v["owner"], "data": base64.b64decode(v["data"][0])}
    return out
OLD = json.load(open("../program-upgrade-1114/out/s11_migration_economics.json"))
def pool_info(pools, x_pool, a):
    if not a: return {"pool": x_pool, "closed": True}
    d = a["data"]
    def pk(off): return b58e(d[off:off + 32])
    return {"pool": x_pool, "owner": a["owner"], "len": len(d), "disc": d[:8].hex(), "pool_bump": d[8], "index": int.from_bytes(d[9:11], "little"),
            "creator": pk(11), "base_mint": pk(43), "quote_mint": pk(75), "lp_mint": pk(107), "pool_base_token_account": pk(139), "pool_quote_token_account": pk(171),
            "lp_supply": int.from_bytes(d[203:211], "little"), "coin_creator": pk(211), "is_mayhem_mode": d[243], "is_cashback_coin": d[244],
            "creator_fee_bps": int.from_bytes(d[261:269], "little"), "can_edit_creator_fee": d[269], "is_holder_reward": d[270], "coin_creator_is_zero": pk(211) == "1" * 32}
# (c3): the 24 pool accounts of the 1114 review (12 post-1114 + 12 pre-1114) read NOW and compared with the 1114 owner/len/disc, plus the first 10 WSOL pools created after D (from M)
old = [(lab, x["pool"], x) for lab in ("post", "pre") for x in OLD[lab]["pool_accounts"] if "owner" in x]
new10 = res["post"]["rows"][:10]
allp = [p for _, p, _ in old] + [x["pool"] for x in new10]
pools = acct(allp)
c3 = {"old24": [], "new10": []}
for lab, p, o in old:
    i = pool_info(pools, p, pools.get(p)); i["from_1114"] = lab
    i["equal_1114_owner_len_disc"] = bool("owner" in i and (i["owner"], i["len"], i["disc"]) == (o["owner"], o["len"], o["disc"]))
    i["coin_creator_unchanged_vs_1114"] = bool("owner" in i and i["coin_creator"] == o["coin_creator"]); c3["old24"].append(i)
for x in new10:
    i = pool_info(pools, x["pool"], pools.get(x["pool"]))
    i["base_mint_equals_CreatePool_event_mint"] = bool("owner" in i and i["base_mint"] == x["mint"]); i["coin_creator_equals_event"] = bool("owner" in i and i["coin_creator"] == x.get("coin_creator")); c3["new10"].append(i)
toks = acct([i[k] for g in c3.values() for i in g if "owner" in i for k in ("pool_base_token_account", "pool_quote_token_account")])
for g in c3.values():
    for i in g:
        if "owner" in i:
            i["base_token_account_owner_program"] = (toks.get(i["pool_base_token_account"]) or {}).get("owner"); i["quote_token_account_owner_program"] = (toks.get(i["pool_quote_token_account"]) or {}).get("owner")
res["c3_pool_accounts"] = c3
res["c3_summary"] = {"old24_n": len(c3["old24"]), "old24_equal_1114": sum(i.get("equal_1114_owner_len_disc", False) for i in c3["old24"]),
                     "new10_n": len(c3["new10"]), "new10_owner_len_disc": sorted({(i.get("owner"), i.get("len"), i.get("disc")) for i in c3["new10"]}),
                     "new10_coin_creator_set": sum(1 for i in c3["new10"] if "owner" in i and not i["coin_creator_is_zero"]), "new10_base_mint_matches_event": sum(i.get("base_mint_equals_CreatePool_event_mint", False) for i in c3["new10"]),
                     "new10_coin_creator_matches_event": sum(i.get("coin_creator_equals_event", False) for i in c3["new10"])}
for label in ("post", "pre"): res[label]["pool_accounts"] = []


def rng(vals):
    vals = [v for v in vals if v is not None]
    return {"n": len(vals), "min": min(vals), "median": statistics.median(vals), "max": max(vals)} if vals else {"n": 0}
for label in ("post", "pre"):
    rows = res[label]["rows"]
    res[label]["summary"] = {k: rng([r.get(k) for r in rows]) for k in ("sol_amount", "mint_amount", "pool_migration_fee", "pool_base_amount", "pool_quote_amount", "initial_liquidity", "creator_fee_bps")}
    v = [r["first_print"]["virtual_quote_reserves"] for r in rows if r.get("first_print", {}).get("observed")]
    res[label]["summary"]["first_print_V"] = rng(v)
    res[label]["summary"]["first_print_V_in_17.5e9_17.7e9"] = sum(1 for x in v if 17.5e9 <= x <= 17.7e9)
    res[label]["summary"]["first_print_not_observed"] = sum(1 for r in rows if "first_print" in r and not r["first_print"]["observed"])
    res[label]["summary"]["mayhem_true"] = sum(1 for r in rows if r.get("is_mayhem_mode"))
(OUT / "s11_migration_economics.json").write_text(json.dumps(res, indent=1, default=str))
print(json.dumps({k: v["summary"] for k, v in res.items()}, indent=1, default=str)); print("rpc", c.total_calls)
