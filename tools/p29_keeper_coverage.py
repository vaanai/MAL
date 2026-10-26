"""P29 tag-coverage dry pass: COUNTS ONLY, reads NO price, quantity, reserve or side-conditioned value.

Per declared block and day: (a) keeper fill txs (public keeper history, rule 2 of DECLARE.md), (b) tape rows and distinct signatures
carrying a fill signature, (c) the ratio rows/fills and distinct/fills, (d) events with >= 3 DISTINCT tagged SL fill signatures in one
mint-or-pool within 60 s (60 s refractory) -- a count of times only.

Own explicit allowlist: exactly the declared blocks and days below, nothing else is opened (this tool does not use tools/arena_screen.py's
guard and does not change it). Paths contain no sealed token. Keeper rows are loaded only for blockTime < 2026-09-25T00Z; no October keeper
count enters this tool.

Usage: python3 p29_keeper_coverage.py KEEPER.jsonl OUT.json
"""
from __future__ import annotations

import collections
import json
import os
import statistics
import subprocess
import sys
from datetime import datetime, timezone

CV = "/data/mal/clean-view"
# (block label, role, [trade dirs], [UTC days])  -- the declared set, nothing else is read
BLOCKS = (
    ("fresh-0903", "in-sample", [f"{CV}/fresh-0903/w{i}/trades" for i in (1, 2, 3)], ["2026-09-04", "2026-09-05", "2026-09-06", "2026-09-07", "2026-09-08"]),
    ("exp011-0909", "OOS", [f"{CV}/exp011-0909/{s}/trades" for s in ("b", "c")], ["2026-09-10", "2026-09-11", "2026-09-12", "2026-09-13", "2026-09-14"]),
    ("fast-pool-0918", "OOS", [f"{CV}/fast-pool-2026-09-18T23_2026-09-22T00/trades"], ["2026-09-19", "2026-09-20", "2026-09-21"]),
    ("oracle-insample-0922", "OOS", [f"{CV}/oracle-insample-2026-09-22_25/trades"], ["2026-09-22", "2026-09-23", "2026-09-24"]),
)
KEEPER_CUTOFF = int(datetime(2026, 9, 25, tzinfo=timezone.utc).timestamp())
MIN_RATIO = 0.85


def day(bt):
    return datetime.fromtimestamp(bt, timezone.utc).strftime("%Y-%m-%d")


def fill_map(rows):
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


def events_distinct(times_sigs, n=3, win=60, refr=60):
    """times_sigs: list of (bt, sig) sorted. -> event times (>= n distinct sigs within win; refractory refr)."""
    out, recent, last = [], [], -10**18
    for t, s in times_sigs:
        recent.append((t, s))
        while recent and t - recent[0][0] > win:
            recent.pop(0)
        if len({x[1] for x in recent}) >= n and t - last > refr:
            out.append(t)
            last = t
    return out


def hour_files(tdir, days):
    for fn in sorted(os.listdir(tdir)):
        if not fn.startswith("trades-"):
            continue
        if fn[len("trades-"):len("trades-") + 10] in days:
            yield os.path.join(tdir, fn)


def scan(tdirs, days, fm):
    rows_by_day = collections.Counter()
    sigs_by_day = collections.defaultdict(set)
    sl = collections.defaultdict(list)  # key -> [(bt, sig)] SL fill rows
    nfiles = 0
    for td in tdirs:
        for path in hour_files(td, set(days)):
            nfiles += 1
            p = subprocess.Popen(["zstd", "-dc", path], stdout=subprocess.PIPE, text=True)
            for line in p.stdout:
                i = line.find('"signature":"')
                if i < 0:
                    continue
                sig = line[i + 13:line.find('"', i + 13)]
                k = fm.get(sig)
                if k is None:
                    continue
                r = json.loads(line)
                bt = r.get("block_time") or r.get("event_ts")
                if bt is None or day(bt) not in days:
                    continue
                d = day(bt)
                rows_by_day[d] += 1
                sigs_by_day[d].add(sig)
                if k == "SL":
                    sl[r.get("mint") or r.get("pool")].append((bt, sig))
            p.wait()
    ev = collections.Counter()
    for key, ts in sl.items():
        ts.sort()
        for t in events_distinct(ts):
            ev[day(t)] += 1
    return rows_by_day, {d: len(s) for d, s in sigs_by_day.items()}, ev, nfiles


def main(argv):
    keeper, out = argv[1], argv[2]
    rows = []
    for l in open(keeper):
        r = json.loads(l)
        if r["bt"] and r["bt"] < KEEPER_CUTOFF:
            rows.append(r)
    fm = fill_map(rows)
    fills_by_day = collections.Counter()
    for r in rows:
        if r["sig"] in fm:
            fills_by_day[day(r["bt"])] += 1
    res = {"keeper_rows_used": len(rows), "keeper_cutoff": "2026-09-25T00:00Z", "blocks": {}}
    for label, role, tdirs, days in BLOCKS:
        rb, sb, ev, nf = scan(tdirs, days, fm)
        per = {}
        for d in days:
            f = fills_by_day.get(d, 0)
            per[d] = {"keeper_fills": f, "tape_rows_with_fill_sig": rb.get(d, 0), "tape_distinct_fill_sigs": sb.get(d, 0),
                      "ratio_rows": round(rb.get(d, 0) / f, 4) if f else None, "ratio_distinct": round(sb.get(d, 0) / f, 4) if f else None,
                      "events_3_distinct_SL_60s": ev.get(d, 0)}
        med = statistics.median(v["ratio_rows"] for v in per.values() if v["ratio_rows"] is not None)
        meds = statistics.median(v["ratio_distinct"] for v in per.values() if v["ratio_distinct"] is not None)
        res["blocks"][label] = {"role": role, "files_read": nf, "median_ratio_rows": round(med, 4), "median_ratio_distinct": round(meds, 4),
                                "dropped_below_0.85": med < MIN_RATIO, "days": per}
    kept = [b for b in res["blocks"].values() if b["role"] == "OOS" and not b["dropped_below_0.85"]]
    res["oos_days_kept"] = sum(len(b["days"]) for b in kept)
    res["oos_events_kept"] = sum(v["events_3_distinct_SL_60s"] for b in kept for v in b["days"].values())
    res["not_decidable_by_count"] = res["oos_days_kept"] < 5 or res["oos_events_kept"] < 100
    json.dump(res, open(out, "w"), indent=1)
    print("wrote", out)


if __name__ == "__main__":
    main(sys.argv)
