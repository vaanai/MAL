"""When did PumpSwap's virtual quote reserve appear? Read-only evidence helper.

For historical PumpSwap buy signatures, fetch the transaction, decode the BuyEvent, and
infer the virtual quote V that makes the constant product reproduce the event's
base_amount_out from its own reserves and net input:

    V = net * pool_base / base_out - net - pool_quote

Also reports the event's byte length (does the old event carry more fields?).
Usage: python -m tools.pumpswap_virtual_history --sig SIG [--sig SIG ...]
Only getTransaction (+ optionally getAccountInfo / getSignaturesForAddress) is called.
"""

from __future__ import annotations

import argparse
import base64
import json

from tools import pumpswap_decompose as d
from tools import pumpswap_simulate as sim


def pool_net(ev: dict) -> int:
    """Net quote that enters the constant product: quote_amount_in_with_lp_fee - lp_fee.
    (For `buy_exact_quote_in` events this equals user_quote_amount_in; for older `buy`
    events user_quote_amount_in is the gross including fees, so it must not be used.)"""
    return ev["quote_amount_in_with_lp_fee"] - ev["lp_fee"]


def implied_virtual(ev: dict) -> float:
    net, pb, pq, out = pool_net(ev), ev["pool_base_token_reserves"], ev["pool_quote_token_reserves"], ev["base_amount_out"]
    return net * pb / out - net - pq


def cp_out(net: int, pq: int, pb: int, v: int) -> int:
    return net * pb // (pq + v + net)


def event_from_tx(t: dict) -> tuple[dict, int] | None:
    for line in t["meta"].get("logMessages", []):
        if line.startswith("Program data: "):
            raw = base64.b64decode(line[14:])
            if raw[:8] == d.BUY_EVENT_DISC:
                return d.decode_buy_event(raw), len(raw)
    return None


def analyze(rpc: sim.Rpc, sig: str) -> dict | None:
    t = rpc("getTransaction", [sig, {"encoding": "json", "maxSupportedTransactionVersion": 1}])
    if not t:
        return None
    got = event_from_tx(t)
    if not got:
        return None
    ev, n = got
    net, pq, pb, out = pool_net(ev), ev["pool_quote_token_reserves"], ev["pool_base_token_reserves"], ev["base_amount_out"]
    v_imp = implied_virtual(ev)
    return {"sig": sig[:10], "slot": t["slot"], "block_time": t.get("blockTime"), "event_len": n, "ev_fields": {k: ev[k] for k in ("quote_amount_in", "lp_fee", "quote_amount_in_with_lp_fee", "user_quote_amount_in")}, "tail_u64_count": len(ev["tail_u64"]),
            "pool_quote_sol": round(pq / 1e9, 3), "out": out, "cp_V0": cp_out(net, pq, pb, 0), "cp_V17_58": cp_out(net, pq, pb, 17_584_000_000),
            "resid_V0_bps": round((out - cp_out(net, pq, pb, 0)) * 1e4 / out, 2), "resid_V17_58_bps": round((out - cp_out(net, pq, pb, 17_584_000_000)) * 1e4 / out, 3),
            "implied_V_sol": round(v_imp / 1e9, 4)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sig", action="append", required=True)
    ap.add_argument("--rpc")
    ap.add_argument("--env-file", default=sim.DEFAULT_ENV_FILE)
    a = ap.parse_args(argv)
    rpc = sim.Rpc(sim.load_rpc_url(a.rpc, a.env_file))
    for s in a.sig:
        print(json.dumps(analyze(rpc, s)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
