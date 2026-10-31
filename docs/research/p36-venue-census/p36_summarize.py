"""Summarize p36_hist outputs into the CENSUS competition table (counts only)."""
import json
import statistics as st
import sys


def summ(path):
    d = json.load(open(path))
    rows = [r for r in d["rows"] if not r.get("error")]
    errs = [r for r in d["rows"] if r.get("error")]
    def med(xs):
        xs = [x for x in xs if x is not None]
        return (round(st.median(xs), 3), len(xs)) if xs else (None, 0)
    tot = sum(r["buy_amt_30s"] for r in rows); e01 = sum(r["buy_amt_slot01"] for r in rows)
    totx = sum(r["buy_amt_30s_ex_launcher"] for r in rows); e01x = sum(r["buy_amt_slot01_ex_launcher"] for r in rows)
    sol_rows = [r for r in rows if r.get("quote_mint") in (None, "So11111111111111111111111111111111111111112")]
    return {
        "file": path.split("/")[-1], "venue": d["venue"], "n_launches": len(rows), "n_errors": len(errs), "slot_s": round(d["slot_s"], 4),
        "slots_per_30s": d["slots_per_30s"],
        "pooled_share_slot01": round(e01 / tot, 3) if tot else None,
        "pooled_share_slot01_ex_launcher": round(e01x / totx, 3) if totx else None,
        "pooled_share_slot01_ex_launcher_sol_quoted": (lambda a, b: round(a / b, 3) if b else None)(
            sum(r["buy_amt_slot01_ex_launcher"] for r in sol_rows if r.get("quote_mint")), sum(r["buy_amt_30s_ex_launcher"] for r in sol_rows if r.get("quote_mint"))),
        "median_share_slot01_ex_launcher": med([r["share_slot01_ex_launcher"] for r in rows]),
        "median_wallets_slot01": med([r["wallets_slot01"] for r in rows]),
        "median_wallets_slot01_ex_launcher": med([r["wallets_slot01_ex_launcher"] for r in rows]),
        "mean_wallets_slot01_ex_launcher": round(st.mean([r["wallets_slot01_ex_launcher"] for r in rows]), 2) if rows else None,
        "launches_with_other_buyer_slot01": sum(1 for r in rows if r["wallets_slot01_ex_launcher"] > 0),
        "launches_with_any_other_buy_30s": sum(1 for r in rows if r["buy_amt_30s_ex_launcher"] > 0),
        "median_ok_tx_30s": med([r["n_ok_window"] for r in rows]),
        "median_failed_tx_30s": med([r["n_failed_window"] for r in rows]),
        "sum_failed_tx_30s": sum(r["n_failed_window"] for r in rows),
        "sum_ok_tx_30s": sum(r["n_ok_window"] for r in rows),
        "median_first_other_buy_slot": med([r["first_other_buy_slot"] for r in rows]),
        "early_sampled_launches": sum(1 for r in rows if r.get("n_early_fetched", r.get("n_early_ok")) < r.get("n_early_ok", 0)),
        "sigs_truncated": sum(1 for r in rows if r.get("sigs_truncated")),
        "non_sol_quote": sum(1 for r in rows if r.get("quote_mint") not in (None, "So11111111111111111111111111111111111111112")),
        "t0_range_utc": [min(r["t0"] for r in rows), max(r["t0"] for r in rows)] if rows else None,
    }


if __name__ == "__main__":
    out = [summ(p) for p in sys.argv[1:]]
    print(json.dumps(out, indent=1))
