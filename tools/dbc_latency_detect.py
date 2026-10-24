"""P15: detection lag of the venue_tape recorder on Meteora DBC. lag = t_recv - fitted block time of the notice's slot.
Block time (getBlockTime, public RPC) has 1 s resolution: it is sampled at many anchor slots and fitted linearly, so the fit
averages the quantisation out (+0.5 s floor correction). Residual scatter of the fit is reported. Paper only, public RPC, no keys.
Usage: python3 tools/dbc_latency_detect.py --tape FILE.jsonl[.zst] [--tape ...] --anchor-step 150 --out out.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
import urllib.request
from pathlib import Path

import numpy as np

RPC = "https://api.mainnet-beta.solana.com"


def rpc(method, params, tries=6):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    for k in range(tries):
        try:
            req = urllib.request.Request(RPC, body, {"content-type": "application/json"})
            return json.load(urllib.request.urlopen(req, timeout=15)).get("result")
        except Exception:
            time.sleep(1.5 * (k + 1))
    return None


def read_rows(p: str):
    if p.endswith(".zst"):
        txt = subprocess.run(["zstd", "-dc", p], capture_output=True, check=True).stdout.decode()
    else:
        txt = Path(p).read_text()
    for line in txt.splitlines():
        try:
            yield json.loads(line)
        except Exception:
            pass


def pct(a, q):
    return float(np.percentile(a, q))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tape", action="append", required=True)
    ap.add_argument("--anchor-step", type=int, default=150)
    ap.add_argument("--out", required=True)
    ap.add_argument("--sampler", action="append", default=[], help="dbc_latency_sampler JSONL (processed+confirmed)")
    ap.add_argument("--window", default=None, help="START_MS,END_MS: keep tape rows with t_recv in the window")
    ap.add_argument("--tape-opt", action="store_true")
    a = ap.parse_args()
    rows = []
    sam = {}
    for p in a.sampler:
        for r in read_rows(p):
            if r.get("swap") and not r.get("err"):
                sam.setdefault(r["c"], {}).setdefault(r["sig"], (r["slot"], r["t_recv_ms"]))
    pc = [sam["confirmed"][k][1] - sam["processed"][k][1] for k in sam.get("confirmed", {}) if k in sam.get("processed", {})]
    sam_gap = {"n_pairs": len(pc), "confirmed_minus_processed_ms": {q: pct(pc, q) for q in (10, 50, 90)}} if pc else None
    for c, d in sam.items():
        rows += [(v[0], v[1], c) for v in d.values()]
    w = [int(x) for x in a.window.split(",")] if a.window else None
    for p in a.tape:
        for r in read_rows(p):
            if r.get("type") == "ix" and r.get("slot") and r.get("t_recv_ms"):
                names = r.get("ix") or []
                if any(n.startswith("Swap") for n in names) and not r.get("failed"):
                    if w and not (w[0] <= r["t_recv_ms"] <= w[1]):
                        continue
                    rows.append((r["slot"], r["t_recv_ms"], "tape-confirmed"))
    tags = np.array([r[2] for r in rows])
    rows = [(r[0], r[1]) for r in rows]
    order = sorted(range(len(rows)), key=lambda i: rows[i])
    rows = [rows[i] for i in order]; tags = tags[order]
    slots = np.array([r[0] for r in rows])
    tr = np.array([r[1] for r in rows], dtype=float) / 1000
    lo, hi = int(slots.min()), int(slots.max())
    anchors = []
    for s in range(lo, hi + 1, a.anchor_step):
        for d in range(0, 4):  # skipped slot -> next
            bt = rpc("getBlockTime", [s + d])
            time.sleep(0.12)
            if bt:
                anchors.append((s + d, bt))
                break
    an = np.array(anchors, dtype=float)
    # fit per chunk of ~40 anchors (piecewise) to allow slot-rate drift; floor quantisation +0.5 s
    lags = np.full(len(slots), np.nan)
    resid = []
    ch = 40
    for i in range(0, len(an), ch - 1):
        seg = an[i:i + ch]
        if len(seg) < 5:
            continue
        b, c = np.polyfit(seg[:, 0], seg[:, 1] + 0.5, 1)
        resid += list(seg[:, 1] + 0.5 - (b * seg[:, 0] + c))
        m = (slots >= seg[0, 0]) & (slots <= seg[-1, 0])
        lags[m] = tr[m] - (b * slots[m] + c)
    ok = ~np.isnan(lags)
    L = lags[ok]
    first = {}
    for s, l in zip(slots[ok], L):
        first[s] = min(first.get(s, 9e9), l)
    F = np.array(list(first.values()))
    by_tag = {}
    for t in sorted(set(tags)):
        m = ok & (tags == t)
        if m.sum() > 20:
            by_tag[t] = {"n": int(m.sum()), **{f"p{q}": pct(lags[m], q) for q in (10, 25, 50, 75, 90)}}
    res = {"n_swaps": int(ok.sum()), "n_anchors": len(anchors), "slot_s_fit": float(1 / np.mean(np.diff(an[:, 0]) / np.diff(an[:, 1]))),
           "fit_resid_sd_s": float(np.std(resid)), "lag_s_all": {q: pct(L, q) for q in (5, 25, 50, 75, 90, 99)},
           "by_source": by_tag, "processed_vs_confirmed": sam_gap, "n_slots": len(F), "lag_s_first_per_slot": {q: pct(F, q) for q in (5, 25, 50, 75, 90, 99)}}
    Path(a.out).write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
