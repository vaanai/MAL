#!/usr/bin/env python3
"""floor_variants_funnel: distinct trigger pools per UTC day for single-filter relaxations of l1-floor-v2 (tools/arena_rule_l1.py). Counts only, PAPER, read-only.

One pass over the tip tape copy; the pool table is fed every row (as HostV2 does). Each variant changes ONE parameter, rest as the live rule. Variants are evaluated on the
same rows with the same table. Notes: the V band also gates universe admission at the pool's first print (arena_pool_table.V_LO/V_HI), so the V-band variant needs its own
table: use --vband wide (tools.arena_pool_table.V_LO/V_HI patched, only variants named vwide are counted). The age 24-400 h variant is limited by tape coverage (tape starts
2026-11-02T01) and by AGE_DROP_S (patched to 401 h here).
Usage: floor_variants_funnel.py TAPE_DIR OUT_DIR FIRST_HOUR LAST_HOUR [--vband narrow|wide]
"""
import json, os, sys, collections, subprocess
from bisect import bisect_left, bisect_right
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tools.arena_pool_table as PT
args = sys.argv[1:]
vband = "narrow"
if "--vband" in args:
    i = args.index("--vband"); vband = args[i + 1]; del args[i:i + 2]
if vband == "wide":
    PT.V_LO, PT.V_HI = 17_400_000_000, 17_800_000_000
PT.AGE_DROP_S = 401 * 3600
from tools.arena_pool_table import PoolTable, WSOL, HIST_HOURS, HOUR_MS, FEE
from tools import arena_rule_l1 as L
from floor_nov_funnel import hours, day_of  # noqa: E402  (importing runs nothing: main() guarded below)

H = 3600
BASE = dict(q=0.5e9, vol=1e9, r6=0.80, alo=24 * H, ahi=200 * H, prev=6 * H)
VARIANTS = {"base": {}, "vault075": dict(q=0.75e9), "vault100": dict(q=1.0e9), "vol05": dict(vol=0.5e9), "vol025": dict(vol=0.25e9), "r085": dict(r6=0.85), "r090": dict(r6=0.90),
            "age12_200": dict(alo=12 * H), "age24_400": dict(ahi=400 * H), "prev12": dict(prev=12 * H)}
if vband == "wide":
    VARIANTS = {"vwide": {}}   # the table itself is widened; every other parameter as base
CFG = {k: {**BASE, **v} for k, v in VARIANTS.items()}


def main():
    tape, out, h0, h1 = args[:4]
    os.makedirs(out, exist_ok=True)
    tab = PoolTable(L.TABLE_MODE)
    trig_pools = {k: collections.defaultdict(set) for k in CFG}
    trig_rows = {k: collections.Counter() for k in CFG}
    fh = open(os.path.join(out, f"triggers-{vband}.jsonl"), "w")
    for h in hours(h0, h1):
        z = subprocess.Popen(["zstd", "-dc", "-q", os.path.join(tape, f"tape-{h}.jsonl.zst")], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
        for line in z.stdout:
            ty_trade = b'"type":"trade"' in line
            if ty_trade and b'"prog":"pumpswap"' not in line:
                continue
            if not ty_trade and b'"type":"complete"' not in line and b'"type":"create"' not in line and b'"type":"gap"' not in line and b'"type":"pool_create"' not in line:
                continue
            r = json.loads(line)
            if r.get("type") == "gap":
                continue
            tab.update(r)
            t = r.get("t_recv_ms")
            if r.get("type") != "trade" or not isinstance(t, int):
                continue
            pool = r.get("pool")
            if r.get("quote_mint") != WSOL or not pool or r.get("side") != "sell":
                continue
            v = r.get("virtual_quote_reserves")
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not PT.V_LO <= v <= PT.V_HI:
                continue
            ts = r.get("event_ts")
            if isinstance(ts, bool) or not isinstance(ts, (int, float)):
                continue
            M = tab.base(pool)
            if M is None:
                continue
            age = int(ts) - M
            if not 12 * H <= age <= 400 * H:
                continue
            try:
                qpost = float(r["quote_reserve"]) - float(r["sol_lamports"]) / (1 - FEE)
            except (KeyError, TypeError, ValueError):
                continue
            if qpost > 1.0e9:
                continue
            pp = tab.pool_prints(pool)
            if pp is None or not len(pp):
                continue
            n = len(pp); j = n - 1
            lo = bisect_left(pp.hidx, t // HOUR_MS - (HIST_HOURS - 1))
            if j - lo <= 0 or pp.bt[j] != int(ts):
                continue
            gap = pp.bt[j] - pp.bt[j - 1]
            j24 = bisect_left(pp.bt, pp.bt[j] - 86400, lo, n)
            vol = float(sum(pp.sol[j24:j + 1]))
            j6 = bisect_right(pp.bt, pp.bt[j] - 6 * H, lo, j + 1) - 1
            if j6 < lo:
                continue
            r6 = pp.spot[j] / pp.spot[j6] if pp.spot[j6] != 0 else float("nan")
            hit = []
            for k, c in CFG.items():
                if c["alo"] <= age <= c["ahi"] and qpost <= c["q"] and gap <= c["prev"] and vol >= c["vol"] and r6 < c["r6"]:
                    d = day_of(t); trig_pools[k][d].add(pool); trig_rows[k][d] += 1; hit.append(k)
            if hit:
                fh.write(json.dumps({"t": t, "pool": pool, "age_s": age, "q": qpost, "vol": vol, "r6": r6, "gap_s": gap, "v": v, "hit": hit}) + "\n")
        z.stdout.close(); z.wait()
        open(os.path.join(out, f"progress-{vband}.txt"), "a").write(f"{h} done\n")
    fh.close()
    res = {k: {"pools": {d: len(s) for d, s in sorted(trig_pools[k].items())}, "rows": dict(sorted(trig_rows[k].items()))} for k in CFG}
    json.dump(res, open(os.path.join(out, f"variants-{vband}.json"), "w"), indent=1)
    print("done")


if __name__ == "__main__":
    main()
