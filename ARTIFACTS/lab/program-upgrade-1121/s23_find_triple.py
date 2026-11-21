"""Locate the single post WSOL-only SellEvent with fee triple (lp 20, protocol 0, coin_creator 5) (ADD1 B disclosure): slot and signature. Offline, cached blocks. Also names s20's post-only error codes by IDL error name."""
import collections, json, sys, os
sys.argv = [sys.argv[0]]
exec(open("s17_pumpswap_events.py").read().split("res = {}")[0])
IDX_A = json.load(open(BASE / "raw" / "sample_index.json"))["a"]
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
            if (ev["lp_fee_basis_points"], ev["protocol_fee_basis_points"], ev["coin_creator_fee_basis_points"]) == (20, 0, 5): hits.append({"event": nm, "slot": IDX_A[tx["transaction"]["signatures"][0]]["slot"], "sig": tx["transaction"]["signatures"][0]})
names = {}
for k in ("pump_amm", "pump"):
    for e in IDL[k].get("errors", []): names[(k, e["code"])] = e["name"]
s20 = json.load(open(os.environ.get("UPGRADE_OUT", "out") + "/s20_failed_shape_txs.json")); post_only = {}
for side in ("buy_exact_quote_in", "sell"):
    po, pr = s20["post"][side]["words"], s20["pre"][side]["words"]
    post_only[side] = [{"word": w, "count_post": n, "code": int(w.split("=")[-1]) if "=" in w else None,
                        "idl_name_pump_amm": names.get(("pump_amm", int(w.split("=")[-1])), "(not a pump_amm IDL error)") if "=" in w else None} for w, n in sorted(po.items(), key=lambda x: -x[1]) if w not in pr]
out = {"triple_20_0_5_events": hits, "post_only_error_words_from_s20_report_only": post_only}
Path(os.environ.get("UPGRADE_OUT", "out") + "/s23_triple_and_errors.json").write_text(json.dumps(out, indent=1)); print(json.dumps(out, indent=1))
