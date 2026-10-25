"""P26 keeper-absorb: counts only. argv: worker_dir out.json. Reads fresh-0903 clean trades for one worker dir.
No price path, no P&L. Cluster = >=3 sells in one mint within 10 s (block_time), 30 s refractory per mint.
Impact bar: 2 * 0.25 / quote_reserve_SOL <= 2% (reserve of the clustering sell row)."""
import json, subprocess, sys, os, collections

wd, out = sys.argv[1], sys.argv[2]
res = {}
last_cluster = {}
recent = collections.defaultdict(list)
files = sorted(os.listdir(os.path.join(wd, "trades")))
for fn in files:
    hr = fn.split("trades-")[1][:13]
    p = subprocess.Popen(["zstd", "-dc", os.path.join(wd, "trades", fn)], stdout=subprocess.PIPE, text=True)
    n = ns = nc = npass = 0
    mints_s = set(); mints_c = set(); venue_c = collections.Counter(); venue_s = collections.Counter()
    resv = []
    rows = []
    for l in p.stdout:
        n += 1
        if '"side":"sell"' not in l:
            continue
        r = json.loads(l)
        if r.get("type") not in (None, "trade") or r.get("side") != "sell":
            continue
        rows.append((r["block_time"], r["slot"], r.get("tx_index") or 0, r["mint"], r["venue"], r.get("quote_reserve")))
    rows.sort()
    for bt, _, _, mint, venue, q in rows:
        ns += 1; mints_s.add(mint); venue_s[venue] += 1
        lst = recent[mint]; lst.append(bt)
        while lst and bt - lst[0] > 10:
            lst.pop(0)
        if len(lst) >= 3 and bt - last_cluster.get(mint, -999) > 30:
            last_cluster[mint] = bt; nc += 1; mints_c.add(mint); venue_c[venue] += 1
            qs = (q or 0) / 1e9
            ok = qs > 0 and (2 * 0.25 / qs) <= 0.02
            npass += ok
            resv.append((venue, round(qs, 2), bool(ok)))
    res[hr] = dict(trades=n, sells=ns, sell_mints=len(mints_s), clusters=nc, clusters_pass=npass, cluster_mints=len(mints_c),
                   sells_by_venue=dict(venue_s), clusters_by_venue=dict(venue_c), cluster_reserves=resv)
    json.dump(res, open(out, "w"))
