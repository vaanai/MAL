"""PumpSwap events as TESTS (QP-RULING-1115 Q1 (b)), offline from the cached samples: per PumpSwap event type, post (A1 + M) vs pre (P + M_pre):
count, trailing-byte lengths (event blob length minus the IDL walker's consumed length), observed blob lengths, and on WSOL-only Buy/Sell the
(lp_fee_basis_points, protocol_fee_basis_points, coin_creator_fee_basis_points) triples with counts; subset check post within pre.
Prefix parity = the independent IDL walker consumes the blob to its length plus a trailing block only (any other remainder is an N3). Structure only."""
import collections, gzip, json, os, sys
from pathlib import Path
sys.argv = [sys.argv[0]]
exec(open("s06_parity.py").read().split("acc = dict(")[0])
WSOL = td.WSOL_MINT
BASE = Path(os.environ.get("A3_BASE", "/data2/mal/scratch/a3-repin-1128"))
MIG = json.load(open(BASE / "raw" / "migrate_index.json"))

def txs(root, label):
    idx = json.load(open(root / "sample_index.json"))["a"]
    for p in sorted((root / "blocks").glob("*.json.gz")):
        blk = json.loads(gzip.open(p).read())["r"]
        for tx in (blk or {}).get("transactions", []):
            if tx["transaction"]["signatures"][0] in idx: yield tx
    seen = set(idx)
    for row in MIG["post" if label == "post" else "pre"]:
        if row["sig"] in seen: continue
        pth = BASE / "raw" / "txs" / f"{row['sig']}.json.gz"
        if pth.exists():
            r_ = json.loads(gzip.open(pth).read())["r"]
            if r_: seen.add(row["sig"]); yield r_

def events_of(tx):
    meta = tx.get("meta") or {}
    evs = list(log_events(meta.get("logMessages") or []))
    ms = collections.Counter(b for _, b in evs); keys = keys_of(tx)
    for g in meta.get("innerInstructions") or []:
        for ins in g["instructions"]:
            pid = keys[ins["programIdIndex"]] if ins["programIdIndex"] < len(keys) else None
            if pid in PNAME and ins.get("data"):
                d = b58d(ins["data"])
                if d[:8] == TAG and len(d) > 8:
                    if ms.get(d[8:], 0) > 0: ms[d[8:]] -= 1
                    else: evs.append((pid, d[8:]))
    return evs

res = {}
for label, root in (("post", BASE / "raw"), ("pre", BASE / "raw_pre")):
    ty = collections.defaultdict(lambda: {"n": 0, "trailing": collections.Counter(), "lens": collections.Counter(), "n3": 0})
    trip = collections.Counter(); n_tx = 0
    for tx in txs(root, label):
        meta = tx.get("meta") or {}
        if meta.get("err") is not None: continue
        n_tx += 1; qm = quote_mints(tx)
        for pid, blob in events_of(tx):
            if PNAME.get(pid) != "pump_amm" or len(blob) < 8: continue
            nm = EV_BY_PROG[pid].get(blob[:8])
            if nm is None: continue
            try: ev, end = parse_event("pump_amm", nm, blob)
            except Short: ty[nm]["n3"] += 1; continue
            t = ty[nm]; t["n"] += 1; t["trailing"][len(blob) - end] += 1; t["lens"][len(blob)] += 1
            if nm in ("BuyEvent", "SellEvent") and qm == {WSOL}:
                trip[f"{ev['lp_fee_basis_points']},{ev['protocol_fee_basis_points']},{ev['coin_creator_fee_basis_points']}|{nm}"] += 1
    res[label] = {"n_tx": n_tx, "types": {k: {"n": v["n"], "trailing": dict(v["trailing"]), "len_min": min(v["lens"]) if v["lens"] else None, "len_max": max(v["lens"]) if v["lens"] else None, "n3_short": v["n3"]} for k, v in sorted(ty.items())}, "wsol_only_fee_triples": dict(trip)}
pre_t = {k.split("|")[0] for k in res["pre"]["wsol_only_fee_triples"]}; post_t = {k.split("|")[0] for k in res["post"]["wsol_only_fee_triples"]}
res["fee_triples_post_subset_of_pre"] = post_t <= pre_t; res["fee_triples_new_in_post"] = sorted(post_t - pre_t)
pins = json.load(open("../../../tools/a3_redeploy_pins.json"))["trailing_bytes"]
dev = []
for nm, v in res["post"]["types"].items():
    tp = res["pre"]["types"].get(nm); tr_post = set(map(int, v["trailing"]))
    if nm in pins:
        want = set(pins[nm]) if isinstance(pins[nm], list) else {pins[nm]}
        if not tr_post <= want: dev.append(f"post {nm}: trailing {sorted(tr_post)} vs pin {pins[nm]}")
    elif tp is None: dev.append(f"{nm}: no pre event (named, not failed); post trailing {sorted(tr_post)}")
    elif set(map(int, tp["trailing"])) != tr_post: dev.append(f"UNPINNED {nm}: post trailing {sorted(tr_post)} vs pre {sorted(map(int, tp['trailing']))}")
for nm in res["pre"]["types"]:
    if nm not in res["post"]["types"]: dev.append(f"{nm}: in pre, absent from post (named, not failed)")
res["notes_and_deviations"] = dev
Path(os.environ.get("UPGRADE_OUT", "out") + "/s17_pumpswap_events.json").write_text(json.dumps(res, indent=1, sort_keys=True))
print(json.dumps({k: v for k, v in res.items() if k not in ("post", "pre")}, indent=1)); 
for l in ("post", "pre"): print(l, res[l]["n_tx"], {k: (v["n"], v["trailing"]) for k, v in res[l]["types"].items()})
