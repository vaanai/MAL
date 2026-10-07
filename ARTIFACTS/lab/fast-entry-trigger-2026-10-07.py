"""Section 2 of fast-entry-trigger-2026-10-07.md: lead of tip `complete` and processed `migrate` over the tip's first PumpSwap print.

python3 ARTIFACTS/lab/fast-entry-trigger-2026-10-07.py /data/mal/ops/grad-t70   (job #253 files; latency only, no P&L)
"""
import glob
import json
import sys

root = sys.argv[1] if len(sys.argv) > 1 else "/data/mal/ops/grad-t70"
EDGE_MS = 60_000
S = [json.loads(l) for f in sorted(glob.glob(f"{root}/stream/grad-*.jsonl")) for l in open(f)]
smig = {}
for r in S:
    if r.get("kind") == "migrate" and r.get("mint") and r["mint"] not in smig:
        smig[r["mint"]] = r
lo, hi = min(r["t_recv_ms"] for r in S), max(r["t_recv_ms"] for r in S)
comp = {}
for f in sorted(glob.glob(f"{root}/tip/migrations-*.jsonl")):
    for l in open(f):
        r = json.loads(l)
        if r.get("type") == "complete":
            comp.setdefault(r["mint"], r)
first = {}
for f in sorted(glob.glob(f"{root}/tip/trades-*.jsonl")):
    for l in open(f):
        if "pumpswap" not in l:
            continue
        r = json.loads(l)
        m = r.get("mint")
        if m in comp and r.get("venue") == "pumpswap" and m not in first:
            first[m] = r
ref = [m for m in first if lo + EDGE_MS <= first[m]["t_recv_ms"] <= hi - EDGE_MS]


def q(v):  # nearest-rank, index n//10, n//2, 9n//10
    v = sorted(v)
    n = len(v)
    return {"n": n, "p10": v[n // 10], "p50": v[n // 2], "p90": v[9 * n // 10]} if n else None


tipc = [first[m]["t_recv_ms"] - comp[m]["t_recv_ms"] for m in ref]
strm = [first[m]["t_recv_ms"] - smig[m]["t_recv_ms"] for m in ref if m in smig]
best = [max(first[m]["t_recv_ms"] - comp[m]["t_recv_ms"], (first[m]["t_recv_ms"] - smig[m]["t_recv_ms"]) if m in smig else -10**9) for m in ref]
vs = [comp[m]["t_recv_ms"] - smig[m]["t_recv_ms"] for m in ref if m in smig]
miss = [m for m in ref if m not in smig]
print("ref", len(ref), "covered", len(strm))
print("tip complete lead", q(tipc), "share<=0", round(sum(1 for x in tipc if x <= 0) / len(tipc), 4))
print("stream migrate lead", q(strm), "min", min(strm))
print("earlier-of lead", q(best))
print("stream before tip complete", round(sum(1 for x in vs if x > 0) / len(vs), 4), q(vs))
print("misses", len(miss), "complete->first-print slot gaps", sorted(first[m]["slot"] - comp[m]["slot"] for m in miss))
print("tip lead on misses", sorted(first[m]["t_recv_ms"] - comp[m]["t_recv_ms"] for m in miss))
same = sum(1 for m in ref if m in smig and first[m]["slot"] == smig[m]["slot"])
plus1 = sum(1 for m in ref if m in smig and first[m]["slot"] == smig[m]["slot"] + 1)
print("first print same slot as stream migrate", same, "one slot later", plus1)
