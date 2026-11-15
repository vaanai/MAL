"""Locate the single post WSOL-only SellEvent with fee triple (lp 20, protocol 0, coin_creator 5) (ADD1 B disclosure): slot and signature. Offline, cached blocks. Also names s20's post-only error codes by IDL error name."""
import collections, json, sys
sys.argv = [sys.argv[0]]
exec(open("s17_pumpswap_events.py").read().split("res = {}")[0])
hits = []
for tx in txs(BASE / "raw", "post"):
    meta = tx.get("meta") or {}
    if meta.get("err") is not None: continue
    qm = quote_mints(tx)
    for pid, blob in events_of(tx):
        if PNAME.get(pid) != "pump_amm" or len(blob) < 8: continue
        nm = EV_BY_PROG[pid].get(blob[:8])
        if nm in ("BuyEvent", "SellEvent") and qm == {WSOL}:
            ev, _ = parse_event("pump_amm", nm, blob)
            if (ev["lp_fee_basis_points"], ev["protocol_fee_basis_points"], ev["coin_creator_fee_basis_points"]) == (20, 0, 5): hits.append({"event": nm, "slot": tx.get("slot"), "sig": tx["transaction"]["signatures"][0]})
names = {}
for k in ("pump_amm", "pump"):
    for e in IDL[k].get("errors", []): names[(k, e["code"])] = e["name"]
codes = {"buy_post_only": [6000, 6036, 3005, 2052, 15], "sell_post_only": [6014, 6002, 6006, 7, 1000, 259, 1771, 6001, 2052]}
out = {"triple_20_0_5_events": hits, "error_names_pump_amm": {k: [(c, names.get(("pump_amm", c), "(not an IDL error of pump_amm)")) for c in v] for k, v in codes.items()}}
Path("out/s23_triple_and_errors.json").write_text(json.dumps(out, indent=1)); print(json.dumps(out, indent=1))
