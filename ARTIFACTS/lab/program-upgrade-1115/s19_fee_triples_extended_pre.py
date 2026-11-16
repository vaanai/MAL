"""POST-HOC addition (declared here, after s17 found 5 post fee triples absent from the declared pre control P + M_pre; see review section 6c): the same triple count over the
cached blocks of the 2026-11-14 review (chain blocks 469058660..469263564 and 469015660..469055069 and 468975660..469014669: PumpSwap at the pinned e3d3be22 binary the whole time,
all before this redeploy; the earlier sample files are read-only here). Report-only: it does not replace the declared bar. Structure only."""
import collections, gzip, json, sys, os
from pathlib import Path
sys.argv = [sys.argv[0]]
src = open("s17_pumpswap_events.py").read()
exec(src.split("res = {}")[0])
def txs_dir(root):
    idx = json.load(open(root / "sample_index.json"))["a"]
    for p in sorted((root / "blocks").glob("*.json.gz")):
        blk = json.loads(gzip.open(p).read())["r"]
        for tx in (blk or {}).get("transactions", []):
            if tx["transaction"]["signatures"][0] in idx: yield tx
B14 = Path("/data2/mal/scratch/a3-repin-1114")
trip = collections.Counter(); n_tx = 0; ntrig_by_root = {}
for r in ("raw", "raw_pre", "raw_pre2"):
    t0 = sum(trip.values())
    for tx in txs_dir(B14 / r):
        meta = tx.get("meta") or {}
        if meta.get("err") is not None: continue
        n_tx += 1; qm = quote_mints(tx)
        for pid, blob in events_of(tx):
            if PNAME.get(pid) != "pump_amm" or len(blob) < 8: continue
            nm = EV_BY_PROG[pid].get(blob[:8])
            if nm not in ("BuyEvent", "SellEvent") or qm != {WSOL}: continue
            try: ev, _ = parse_event("pump_amm", nm, blob)
            except Short: continue
            trip[f"{ev['lp_fee_basis_points']},{ev['protocol_fee_basis_points']},{ev['coin_creator_fee_basis_points']}|{nm}"] += 1
    ntrig_by_root[r] = sum(trip.values()) - t0
s17r = json.load(open(os.environ.get("UPGRADE_OUT", "out") + "/s17_pumpswap_events.json"))
post_t = {k.split("|")[0] for k in s17r["post"]["wsol_only_fee_triples"]}; pre_t = {k.split("|")[0] for k in s17r["pre"]["wsol_only_fee_triples"]}
ext_t = {k.split("|")[0] for k in trip}
res = {"n_tx": n_tx, "wsol_only_events_by_root": ntrig_by_root, "triples": dict(sorted(trip.items())), "post_minus_pre": sorted(post_t - pre_t), "post_minus_pre_minus_ext": sorted(post_t - pre_t - ext_t),
       "post_minus_ext_only": sorted(post_t - ext_t), "n_distinct_pre_P": len(pre_t), "n_distinct_ext": len(ext_t), "n_distinct_post": len(post_t)}
Path(os.environ.get("UPGRADE_OUT", "out") + "/s19_fee_triples_extended_pre.json").write_text(json.dumps(res, indent=1))
print(json.dumps({k: v for k, v in res.items() if k != "triples"}, indent=1))
