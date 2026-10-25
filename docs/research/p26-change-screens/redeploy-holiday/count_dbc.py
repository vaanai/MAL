"""P26 redeploy-holiday DBC leg: counts only. Reads meteora_dbc hours 2026-10-14T00..2026-10-16T23 only."""
import json, subprocess, sys, collections, statistics, datetime
D = "/data2/mal/venue-tape-backfill/meteora_dbc/tape-%s.jsonl.zst"
hours = [f"2026-10-{d}T{h:02d}" for d in (14, 15, 16) for h in range(24)]
out = {}
for hr in hours:
    p = subprocess.Popen(["zstd", "-dc", D % hr], stdout=subprocess.PIPE, text=True)
    ev = collections.Counter(); creates = {}; trades = []
    undec = 0
    for l in p.stdout:
        r = json.loads(l); t = r["type"]
        if t == "trade":
            ev[r.get("event")] += 1; trades.append((r["pool"], r["slot"], r.get("signer")))
        elif t == "create": creates[r["pool"]] = r["slot"]
        elif t == "event_undecoded": undec += 1
    sig = collections.defaultdict(set); nt = collections.Counter()
    for pool, slot, s in trades:
        c = creates.get(pool)
        if c is not None and c <= slot <= c + 5:
            sig[pool].add(s); nt[pool] += 1
    meds = [len(sig[p]) for p in creates]  # pools with no trade in window count 0
    out[hr] = dict(events=dict(ev), undecoded=undec, creates=len(creates), trades=len(trades),
                   pools_with_trade5=len([p for p in creates if sig[p]]),
                   median_signers5=(statistics.median(meds) if meds else None),
                   mean_signers5=(round(sum(meds)/len(meds), 3) if meds else None),
                   distinct_signers=len({s for _, _, s in trades}))
json.dump(out, open(sys.argv[1], "w"), indent=1)
