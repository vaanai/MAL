#!/usr/bin/env python3
"""P48 step A: group pass-2 rows by signature into one record per transaction that holds a child trade.

Counts only. No price is computed. Fields carried: time, signature, signer, and per row (prog, mint, quote_mint, pool, side,
sol_lamports, ix_name, creator). A "child row" is a trade of a custom-quote child: a pump.fun curve trade with zero_sol, or
any trade of a mint created on the tape with a non-WSOL quote. A "SOL leg" is a PumpSwap trade row in the same signature
whose quote is WSOL and whose mint is not a child traded in that signature; its mint is the parent that was routed through.
Rows of one transaction share slot and receive time. Pass 2 writes each hour file's child rows, then its legs, so the
buffer keeps a signature for 2.5 h of receive time before it is flushed.
Usage: p48_group.py DIR   (reads DIR/p48_pass1.jsonl, DIR/p48_pass2.jsonl; writes DIR/p48_sigs.jsonl)
"""
import json, sys

WSOL = "So11111111111111111111111111111111111111112"
PUMPTOK = "pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn"


def main(d):
    kidq = {}
    for l in open(f"{d}/p48_pass1.jsonl"):
        r = json.loads(l)
        if r["type"] == "create":
            kidq[r["mint"]] = r["quote_mint"]
    buf = {}
    out = open(f"{d}/p48_sigs.jsonl", "w")
    n_in = n_out = 0

    def is_child(r):
        if r["mint"] == PUMPTOK:
            return False
        if r["prog"] == "pumpfun":
            return bool(r.get("zero_sol")) or r["mint"] in kidq
        return r["mint"] in kidq and (r.get("quote_mint") or WSOL) != WSOL

    def flush(now, force=False):
        nonlocal n_out
        dead = [s for s, v in buf.items() if force or now - v[0] > 9_000_000]
        for s in dead:
            t, rows = buf.pop(s)
            kids = [r for r in rows if is_child(r)]
            if not kids:
                continue
            kmints = {r["mint"] for r in kids}
            legs = [r for r in rows if r["prog"] == "pumpswap" and r.get("quote_mint") == WSOL and r["mint"] not in kmints]
            other = [r for r in rows if r not in kids and r not in legs]
            rec = {
                "t": t, "sig": s, "trader": kids[0]["trader"],
                "kids": [[r["mint"], r["prog"], r["side"], r.get("ix_name"), r.get("creator"), kidq.get(r["mint"])] for r in kids],
                "legs": [[r["mint"], r["pool"], r["side"], r["sol_lamports"], r["trader"], r.get("vq")] for r in legs],
                "other": [[r["mint"], r.get("quote_mint"), r["pool"], r["side"], r["sol_lamports"]] for r in other if r["prog"] == "pumpswap"],
            }
            out.write(json.dumps(rec, separators=(",", ":")) + "\n"); n_out += 1

    last = 0
    for l in open(f"{d}/p48_pass2.jsonl"):
        r = json.loads(l); n_in += 1
        if r.get("type") != "trade":
            continue
        t = r["t_recv_ms"]
        e = buf.get(r["signature"])
        if e is None:
            buf[r["signature"]] = [t, [r]]
        else:
            e[1].append(r)
        if t - last > 600_000:
            flush(t); last = t
    flush(0, force=True)
    out.close()
    print("rows in", n_in, "sig records", n_out, file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1])
