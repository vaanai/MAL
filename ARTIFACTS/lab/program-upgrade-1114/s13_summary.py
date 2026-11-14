"""Offline summary of the parity outputs and the sample coverage bars (QP ruling 384d99f3 item 2). Writes out/s13_summary.json. Structure only."""
import collections, json, os, time
B = "/data2/mal/scratch/a3-repin-1114/"
res = {"parity": {}, "coverage": {}}
for n, lab in (("", "post"), ("_pre", "pre_P"), ("_pre2", "pre_P2"), ("_premig", "pre_Mpre")):
    d = json.load(open(f"out/s06_parity{n}.json"))
    nd = collections.Counter(f"{x['cat']}|cap={x.get('explained_by_cap')}|trunc={x.get('truncated')}|{x.get('why', '')[:30]}" for x in d["nondecode"])
    res["parity"][lab] = {"blocks": d["n_blocks_A"], "A_txs": d["n_A_txs_success_touching"], "tx_analysed": d["tx_analysed"], "log_truncated_txs": d["log_truncated_txs"],
        "fields_compared": d["compared_fields_ok_or_not"], "unknown_event_disc": d["unknown_disc"], "unknown_ix_disc": d["unknown_ix"], "trail8_nonzero": d["trail8_nonzero"],
        "frozen_rows": d["frozen_rows_records_from_logs"], "indep_log_trade_events": d["indep_log_trade_events"], "capped_refused": sum(d["refused"].values()), "cpi_only": d["cpi_only"],
        "nondecode": dict(nd), "unresolved_mint_rows": d["unresolved_mint_rows"], "walker_rows": d["walker_rows"], "walker_bad_disc": d["walker_bad_disc"],
        "nontrade_value_unmatched": {k: [v["total"], v["value_unmatched"], v["frozen_none"]] for k, v in d["nontrade"].items()},
        "events": d["events"], "ix_boost_create": {k: v for k, v in d["ix"].items() if any(w in k for w in ("create", "migrat", "boost"))}}
for s in ("raw", "raw_pre", "raw_pre2"):
    i = json.load(open(B + s + "/sample_index.json"))["a"]
    hrs = {time.strftime("%FT%H", time.gmtime(v["blockTime"])) for v in i.values()}
    sl = [v["slot"] for v in i.values()]
    res["coverage"][s] = {"txs": len(i), "distinct_utc_hours": len(hrs), "min_slot": min(sl), "max_slot": max(sl), "n_blocks_cached": len(os.listdir(B + s + "/blocks")),
                          "first_hour": min(hrs), "last_hour": max(hrs), "slots_after_deploy_min": min(sl) - 469055660}
json.dump(res, open("out/s13_summary.json", "w"), indent=1, default=str)
for k, v in res["parity"].items():
    print("==", k, {a: b for a, b in v.items() if a not in ("events", "ix_boost_create", "nontrade_value_unmatched", "walker_rows")})
    print("   nontrade", v["nontrade_value_unmatched"], "ix", v["ix_boost_create"])
print(json.dumps(res["coverage"], indent=1))
