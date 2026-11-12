#!/usr/bin/env python3
"""P48 QSINK regime: counts-only extractor over CLOSED arena tape hours 2026-10-19T08..2026-10-24T23.

Pass 1 (creates): every create row whose quote_mint is not WSOL (a "child"), plus pool_create and complete rows.
Pass 2 (legs): every trade row whose mint is a child or PUMP, every pump.fun curve trade with zero_sol (a custom-quote
child, including children created before the tape start), plus every PumpSwap trade row that shares a signature
with a child trade in the same hour file (the parent leg). Kept fields: type, prog, t_recv_ms, slot, signature, event_index,
mint, quote_mint, pool, trader, side, sol_lamports, token_raw, ix_name, creator, zero_sol, virtual_quote_reserves (pool
class only). No price is computed anywhere; reserves are not kept. Output: compact jsonl in OUT_DIR.
Path guard: refuses any file outside the listed closed hours (nothing at or after 2026-10-25T00Z).
Usage: p48_extract.py pass1|pass2 OUT_DIR
"""
import json, os, re, subprocess, sys

WSOL = "So11111111111111111111111111111111111111112"
TAPE = "/data/mal/arena/tape"
PATH_RE = re.compile(r"^/data/mal/arena/tape/tape-2026-10-(19T(0[89]|1[0-9]|2[0-3])|2[0-4]T([01][0-9]|2[0-3]))\.jsonl\.zst$")
PUMPTOK = "pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn"
SIGB = re.compile(rb'"signature":"(\w+)"')
KEEP = ("type", "prog", "t_recv_ms", "slot", "signature", "event_index", "mint", "quote_mint", "pool", "trader", "side",
        "sol_lamports", "token_raw", "ix_name", "creator", "zero_sol", "is_mayhem_mode", "name", "symbol")


def files():
    out = []
    for fn in sorted(os.listdir(TAPE)):
        p = f"{TAPE}/{fn}"
        if PATH_RE.match(p):
            out.append(p)
    assert all(PATH_RE.match(p) for p in out)
    assert not any("2026-10-25" in p or "2026-10-26" in p for p in out)
    return out


def stream(path, grep_args):
    if not PATH_RE.match(path):
        raise SystemExit(f"REFUSED {path}")
    z = subprocess.Popen(["zstd", "-dc", path], stdout=subprocess.PIPE)
    g = subprocess.Popen(["grep"] + grep_args, stdin=z.stdout, stdout=subprocess.PIPE)
    z.stdout.close()
    for line in g.stdout:
        yield json.loads(line)
    g.wait(); z.wait()
    if z.returncode != 0:
        raise SystemExit(f"zstd failed on {path}")


def slim(r):
    o = {k: r[k] for k in KEEP if k in r}
    if r.get("type") in ("pool_create", "pool_info") or (r.get("prog") == "pumpswap" and r.get("type") == "trade"):
        o["vq"] = r.get("virtual_quote_reserves")  # only to flag V-band WSOL pools (seal guard); not a price
    return o


def pass1(out_dir):
    n = 0
    with open(f"{out_dir}/p48_pass1.jsonl", "w") as w:
        for f in files():
            for r in stream(f, ["-E", '^.{0,40}"type":"(create|pool_create|complete)"']):
                if r["type"] == "create" and (r.get("quote_mint") or WSOL) == WSOL:
                    continue
                w.write(json.dumps(slim(r), separators=(",", ":")) + "\n"); n += 1
            print("p1", f[-23:], n, file=sys.stderr, flush=True)


def _p2_file(args):
    f, out_dir, kids = args
    pat = f"{out_dir}/p48_patterns_kids.txt"
    rows, sigs = [], set()
    for r in stream(f, ["-F", "-f", pat]):
        if r.get("type") == "trade" and (r.get("mint") in kids or (r.get("prog") == "pumpfun" and r.get("zero_sol"))):
            rows.append(slim(r)); sigs.add(r["signature"])
    # second read: every PumpSwap trade row that shares a signature with a child trade (the parent leg), or any PUMP row
    z = subprocess.Popen(["zstd", "-dc", f], stdout=subprocess.PIPE, bufsize=1 << 22)
    have = {(x["signature"], x.get("event_index"), x["mint"]) for x in rows}
    for line in z.stdout:
        if b'"prog":"pumpswap"' not in line[:60] or b'"type":"trade"' not in line[:40]:
            continue
        m = SIGB.search(line)
        if m and m.group(1).decode() in sigs:
            r = json.loads(line)
            if (r["signature"], r.get("event_index"), r.get("mint")) not in have:
                rr = slim(r); rr["leg"] = 1; rows.append(rr)
    z.wait()
    if z.returncode != 0:
        raise SystemExit(f"zstd failed on {f}")
    return f, rows


def pass2(out_dir, workers=4):
    import multiprocessing as mp
    kids = set()
    for l in open(f"{out_dir}/p48_pass1.jsonl"):
        r = json.loads(l)
        if r["type"] == "create":
            kids.add(r["mint"])
    kids.add(PUMPTOK)  # PUMP itself: every PUMP trade row is kept (pool volume denominators)
    with open(f"{out_dir}/p48_patterns_kids.txt", "w") as w:
        for m in sorted(kids):
            w.write(f'"mint":"{m}"\n')
        w.write('"zero_sol":true\n')  # curve trades of custom-quote children created before the tape start
    n = 0
    with open(f"{out_dir}/p48_pass2.jsonl", "w") as w, mp.Pool(workers) as pool:
        for f, rows in pool.imap(_p2_file, [(f, out_dir, kids) for f in files()]):
            for r in rows:
                w.write(json.dumps(r, separators=(",", ":")) + "\n")
            n += len(rows)
            print("p2", f[-23:], n, file=sys.stderr, flush=True)


def pass3(out_dir):
    """Every tape row (any type) whose signature is in p48_bigpump_sigs.txt: the full leg set of the large PUMP/WSOL buys."""
    sigs = [s.strip() for s in open(f"{out_dir}/p48_bigpump_sigs.txt") if s.strip()]
    pat = f"{out_dir}/p48_patterns_bigsig.txt"
    with open(pat, "w") as w:
        for s in sigs:
            w.write(f'"signature":"{s}"\n')
    n = 0
    with open(f"{out_dir}/p48_pass3.jsonl", "w") as w:
        for f in files():
            for r in stream(f, ["-F", "-f", pat]):
                w.write(json.dumps(slim(r), separators=(",", ":")) + "\n"); n += 1
            print("p3", f[-23:], n, file=sys.stderr, flush=True)


def _p4_file(args):
    f, out_dir = args
    rows = []
    for r in stream(f, ["-F", "-f", f"{out_dir}/p48_patterns_parents.txt"]):
        if r.get("type") != "trade" or r.get("prog") != "pumpswap":
            continue
        rows.append([r["t_recv_ms"], r["slot"], r["signature"], r.get("event_index"), r["mint"], r.get("quote_mint"), r["pool"],
                     r["side"], r.get("sol_lamports"), r.get("pool_quote_amount"), r.get("quote_reserve"), r["trader"], r.get("ix_name")])
    return f, rows


def pass4(out_dir, workers=4):
    """Every PumpSwap trade row of the top parents (p48_parents_top.txt): the parent-pool volume denominators and the
    reserve-consistency marker (does the next print's PRE-trade quote reserve move by this print's quote amount?).
    Reserves are read only for that landing check; no price is computed."""
    import multiprocessing as mp
    mints = [m.strip() for m in open(f"{out_dir}/p48_parents_top.txt") if m.strip()]
    with open(f"{out_dir}/p48_patterns_parents.txt", "w") as w:
        for m in mints:
            w.write(f'"mint":"{m}"\n')
    n = 0
    with open(f"{out_dir}/p48_pass4.jsonl", "w") as w, mp.Pool(workers) as pool:
        for f, rows in pool.imap(_p4_file, [(f, out_dir) for f in files()]):
            for r in rows:
                w.write(json.dumps(r, separators=(",", ":")) + "\n")
            n += len(rows)
            print("p4", f[-23:], n, file=sys.stderr, flush=True)


if __name__ == "__main__":
    os.makedirs(sys.argv[2], exist_ok=True)
    {"pass1": pass1, "pass2": pass2, "pass3": pass3, "pass4": pass4}[sys.argv[1]](sys.argv[2])
