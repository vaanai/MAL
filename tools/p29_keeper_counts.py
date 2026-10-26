"""P29 keeper-absorb: COUNTS ONLY. Tag fresh-0903 sells by keeper fill signature, count tagged sells and clusters.

Usage: python3 p29_keeper_counts.py KEEPER.jsonl OUT.json WORKER_DIR [WORKER_DIR ...]
Reads (a) the pager output (public keeper history), (b) fresh-0903 clean trades (exploration pool).
No price path, no exit, no P&L. Rules: docs/research/p29-keeper-absorb/DECLARE.md.
"""
from __future__ import annotations

import collections
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

BAR = 0.02
STAKE = 0.25
V_FLAT = 17.584e9
CLOSED_BEFORE = 1792886400  # 2026-10-25T00:00:00Z


def day(bt):
    return datetime.fromtimestamp(bt, timezone.utc).strftime("%Y-%m-%d")


def hour(bt):
    return datetime.fromtimestamp(bt, timezone.utc).strftime("%Y-%m-%dT%H")


def fill_map(rows):
    """sig -> 'SL'|'TP'|'MX'|'OT' for keeper txs in a slot holding a create, err==0, no pump-limit memo."""
    kinds = collections.defaultdict(set)
    for r in rows:
        if r["kind"]:
            kinds[r["slot"]].add(r["kind"])
    out = {}
    for r in rows:
        if r["kind"] or r["err"]:
            continue
        ks = kinds.get(r["slot"])
        if ks:
            out[r["sig"]] = next(iter(ks)) if len(ks) == 1 else "MX"
    return out


def cluster_times(times, n, win, refr):
    """times sorted ascending (seconds). >= n within win -> event at the nth time; then refractory refr."""
    out, recent, last = [], [], -10**18
    for t in times:
        recent.append(t)
        while recent and t - recent[0] > win:
            recent.pop(0)
        if len(recent) >= n and t - last > refr:
            out.append(t)
            last = t
    return out


def impact_ok(vault_lamports, v_lamports):
    r = (vault_lamports + v_lamports) / 1e9
    return r > 0 and (2 * STAKE / r) <= BAR


def keeper_summary(rows):
    d = collections.defaultdict(lambda: collections.Counter())
    h = collections.defaultdict(lambda: collections.Counter())
    fm = fill_map(rows)
    for r in rows:
        if not r["bt"]:
            continue
        for key, tgt in ((day(r["bt"]), d), (hour(r["bt"]), h)):
            c = tgt[key]
            c["sigs"] += 1
            c["err"] += r["err"]
            if r["kind"]:
                c["create_" + r["kind"]] += 1
                c["ver_" + (r["ver"] or "?")] += 1
            elif r["sig"] in fm:
                c["fill_" + fm[r["sig"]]] += 1
    hh = {k: dict(v) for k, v in h.items() if k < "2026-10-25T00" and True}
    # per-hour only for the most recent 48 fully closed hours before 2026-10-25T00Z
    keys = sorted(hh)[-48:]
    return {k: dict(v) for k, v in sorted(d.items())}, {k: hh[k] for k in keys}, fm


def stream_sells(wd, fills):
    """yield (block_time, slot, mint, venue, side, sol_lamports, quote_reserve, sig) for sells; count fill sigs by side."""
    tdir = os.path.join(wd, "trades")
    for fn in sorted(os.listdir(tdir)):
        p = subprocess.Popen(["zstd", "-dc", os.path.join(tdir, fn)], stdout=subprocess.PIPE, text=True)
        for line in p.stdout:
            i = line.find('"signature":"')
            if i < 0:
                continue
            j = line.find('"', i + 13)
            sig = line[i + 13:j]
            if sig not in fills:
                continue
            r = json.loads(line)
            yield (r["block_time"], r["slot"], r["mint"], r["venue"], r["side"], r.get("sol_lamports") or 0,
                   r.get("quote_reserve") or 0, sig, r.get("tx_index") or 0)
        p.wait()


def main(argv):
    keeper, out, wds = argv[1], argv[2], argv[3:]
    rows = [json.loads(l) for l in open(keeper)]
    kd, kh, fm = keeper_summary(rows)
    tagged = []
    side_ct = collections.Counter()
    for wd in wds:
        for t in stream_sells(wd, fm):
            side_ct[(day(t[0]), t[4], t[3])] += 1
            if t[4] == "sell":
                tagged.append(t)
    tagged.sort(key=lambda t: (t[0], t[1], t[8]))
    per_day = collections.defaultdict(lambda: collections.Counter())
    for t in tagged:
        d = day(t[0])
        per_day[d]["tagged_sells"] += 1
        per_day[d]["kind_" + fm[t[7]]] += 1
        per_day[d]["venue_" + t[3]] += 1
    # clusters
    by_mint = collections.defaultdict(list)
    for t in tagged:
        by_mint[t[2]].append(t)
    defs = {"SL60": (3, 60, 60, ("SL",)), "ANY10": (3, 10, 30, ("SL", "TP", "MX", "OT"))}
    cl = {k: collections.defaultdict(lambda: collections.Counter()) for k in defs}
    for mint, ts in by_mint.items():
        for name, (n, win, refr, kinds) in defs.items():
            sel = [t for t in ts if fm[t[7]] in kinds]
            times = [t[0] for t in sel]
            evs = cluster_times(times, n, win, refr)
            idx = 0
            for ev in evs:
                last = [t for t in sel if t[0] == ev][-1]
                d = day(ev)
                c = cl[name][d]
                c["clusters"] += 1
                c["venue_" + last[3]] += 1
                q = last[6]
                pool_q = q - last[5] if last[3] == "pumpswap" else q  # PumpSwap pre-trade reserve -> post-sell estimate
                vflat = V_FLAT if last[3] == "pumpswap" else 0
                c["pass_vault"] += impact_ok(pool_q, 0)
                c["pass_vault_plus_V"] += impact_ok(pool_q, vflat)
    res = {"keeper_per_day": kd, "keeper_per_hour_last48_closed": kh,
           "n_keeper_rows": len(rows), "oldest_bt": min((r["bt"] for r in rows if r["bt"]), default=None),
           "n_fill_sigs": len(fm), "tape_rows_with_fill_sig_by_day_side_venue": {"|".join(k): v for k, v in sorted(side_ct.items())},
           "tagged_per_day": {d: dict(c) for d, c in sorted(per_day.items())},
           "clusters": {k: {d: dict(c) for d, c in sorted(v.items())} for k, v in cl.items()}}
    json.dump(res, open(out, "w"), indent=1)
    print("wrote", out)


if __name__ == "__main__":
    main(sys.argv)
