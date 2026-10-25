"""P31 forced-flow census: COUNTS ONLY. Rules: docs/research/p31-forced-flow/DECLARE.md.

Stages:
  S  OUT_DIR            tape pass over fresh-0903 (no network): denominators + samples
  F  OUT_DIR            getTransaction on sampled signatures (public RPC, hard cap 1,500 calls, resumable)
  A  OUT_DIR            analysis from the stored extracts (no network)
No price path, no P&L. Exploration pool fresh-0903 only.
"""
from __future__ import annotations

import collections
import json
import math
import os
import random
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone

ROOT = "/data/mal/blocks-clean/fresh-0903"
RPC = "https://api.mainnet-beta.solana.com"
FULL_DAYS = ["2026-09-04", "2026-09-05", "2026-09-06", "2026-09-07", "2026-09-08"]
SEED = 31
N_A, N_B = 200, 100
BURST_N, BURST_WIN = 3, 10
MAX_TX_CALLS = 1500
TRIVIAL = {"ComputeBudget111111111111111111111111111111", "11111111111111111111111111111111"}
MEMO_PROGS = {"MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr", "Memo1UhkJRfHyvLMcVucJwxXeuD728EqVDDwQDxFMNo"}


def day_of(bt):
    return datetime.fromtimestamp(bt, timezone.utc).strftime("%Y-%m-%d")


class Reservoir:
    def __init__(self, k, rng):
        self.k, self.rng, self.n, self.items = k, rng, 0, []

    def add(self, x):
        self.n += 1
        if len(self.items) < self.k:
            self.items.append(x)
        else:
            j = self.rng.randrange(self.n)
            if j < self.k:
                self.items[j] = x


def burst_rows(sells):
    """sells: list of (bt, mint, sig, ...) for one hour. Return set of indexes in a >=BURST_N within BURST_WIN window of one mint."""
    by = collections.defaultdict(list)
    for i, s in enumerate(sells):
        by[s[1]].append(i)
    out = set()
    for idx in by.values():
        idx.sort(key=lambda i: sells[i][0])
        lo = 0
        for hi in range(len(idx)):
            while sells[idx[hi]][0] - sells[idx[lo]][0] > BURST_WIN:
                lo += 1
            if hi - lo + 1 >= BURST_N:
                out.update(idx[lo:hi + 1])
    return out


def hour_files():
    fs = []
    for w in ("w1", "w2", "w3"):
        d = os.path.join(ROOT, w, "trades")
        for fn in sorted(os.listdir(d)):
            fs.append((fn, os.path.join(d, fn)))
    fs.sort()
    return fs


def stage_s(out):
    rng = random.Random(SEED)
    resA = {d: Reservoir(N_A, rng) for d in FULL_DAYS}
    resB = {d: Reservoir(N_B, rng) for d in FULL_DAYS}
    den = collections.defaultdict(collections.Counter)
    for fn, path in hour_files():
        p = subprocess.Popen(["zstd", "-dc", path], stdout=subprocess.PIPE, text=True)
        sells = []
        seen = set()
        for line in p.stdout:
            if '"sell"' not in line:
                continue
            r = json.loads(line)
            if r.get("side") != "sell":
                continue
            d = day_of(r["block_time"])
            den[d]["sell_rows"] += 1
            den[d]["sell_rows_" + r["venue"]] += 1
            sells.append((r["block_time"], r["mint"], r["signature"], r["venue"], r["trader"], d))
        p.wait()
        bidx = burst_rows(sells)
        for i, s in enumerate(sells):
            d = s[5]
            if i in bidx:
                den[d]["burst_rows"] += 1
                den[d]["burst_rows_" + s[3]] += 1
            if d in resA and s[2] not in seen:
                seen.add(s[2])
                resA[d].add({"sig": s[2], "venue": s[3], "trader": s[4], "day": d, "mint": s[1], "bt": s[0], "stratum": "A"})
                if i in bidx:
                    resB[d].add({"sig": s[2], "venue": s[3], "trader": s[4], "day": d, "mint": s[1], "bt": s[0], "stratum": "B"})
    os.makedirs(out, exist_ok=True)
    samples = []
    for d in FULL_DAYS:
        samples += resA[d].items + resB[d].items
    json.dump({"denominators": {d: dict(c) for d, c in sorted(den.items())},
               "pop_A": {d: resA[d].n for d in FULL_DAYS}, "pop_B": {d: resB[d].n for d in FULL_DAYS}},
              open(os.path.join(out, "denominators.json"), "w"), indent=1)
    with open(os.path.join(out, "samples.jsonl"), "w") as f:
        for s in samples:
            f.write(json.dumps(s) + "\n")
    print("samples", len(samples))


WIRE = {"n": 0}
WIRE_CAP = 1950  # total HTTP attempts, retries included (declared cap < 2,000)
WIRE_PRIOR = 600  # pessimistic attempts of the first F run (no wire counter; cancelled with 217 extracts, 8 failures)


def rpc_tx(sig, tries=4):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getTransaction",
                       "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0, "commitment": "finalized"}]}).encode()
    err = "x"
    for i in range(tries):
        if WIRE["n"] >= WIRE_CAP:
            return {"_err": "wire_cap"}
        WIRE["n"] += 1
        try:
            req = urllib.request.Request(RPC, body, {"content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.load(r)
            if "result" in d:
                return d["result"]
            err = "rpc_error"
        except Exception as e:  # class only
            err = type(e).__name__
        time.sleep(min(30, 2 ** i))
    return {"_err": err}


def extract(tx, trader):
    """Compact extract from a jsonParsed getTransaction result (public addresses only)."""
    if tx is None:
        return {"ok": False, "why": "null"}
    if "_err" in tx:
        return {"ok": False, "why": tx["_err"]}
    msg = tx["transaction"]["message"]
    keys = msg["accountKeys"]
    signers = [k["pubkey"] for k in keys if k.get("signer")]
    payer = keys[0]["pubkey"]
    progs, memos, transfers = [], [], []
    for ins in msg["instructions"]:
        pid = ins.get("programId")
        if pid in MEMO_PROGS:
            m = ins.get("parsed")
            memos.append(m if isinstance(m, str) else str(m))
        elif pid not in TRIVIAL:
            progs.append(pid)
        if pid == "11111111111111111111111111111111":
            pr = ins.get("parsed") or {}
            if pr.get("type") == "transfer":
                i = pr["info"]
                transfers.append([i["source"], i["destination"], i["lamports"]])
    logs = (tx.get("meta") or {}).get("logMessages") or []
    names = sorted({m.split("Instruction: ", 1)[1].strip() for m in logs if "Instruction: " in m})
    for inner in (tx.get("meta") or {}).get("innerInstructions") or []:
        for ins in inner["instructions"]:
            if ins.get("programId") == "11111111111111111111111111111111":
                pr = ins.get("parsed") or {}
                if pr.get("type") == "transfer":
                    i = pr["info"]
                    if i["lamports"] <= 50_000_000 and i["source"] in signers:
                        transfers.append([i["source"], i["destination"], i["lamports"], "inner"])
    return {"ok": True, "err": (tx.get("meta") or {}).get("err") is not None, "payer": payer,
            "n_signers": len(signers), "signers": signers[:4], "payer_is_trader": payer == trader,
            "trader_signed": trader in signers, "progs": sorted(set(progs)), "memos": memos[:3],
            "ix_names": names, "transfers": transfers[:12]}


def stage_f(out):
    samples = [json.loads(l) for l in open(os.path.join(out, "samples.jsonl"))]
    path = os.path.join(out, "extracts.jsonl")
    done = set()
    if os.path.exists(path):
        done = {json.loads(l)["sig"] for l in open(path)}
    calls = len(done)
    wp = os.path.join(out, "wire.txt")
    WIRE["n"] = int(open(wp).read()) if os.path.exists(wp) else WIRE_PRIOR
    random.Random(SEED).shuffle(samples)
    with open(path, "a") as f:
        for s in samples:
            if s["sig"] in done:
                continue
            if calls >= MAX_TX_CALLS:
                print("cap reached")
                break
            rec = dict(s)
            rec.update(extract(rpc_tx(s["sig"]), s["trader"]))
            f.write(json.dumps(rec) + "\n")
            f.flush()
            open(wp, "w").write(str(WIRE["n"]))
            calls += 1
            if rec.get("why") == "wire_cap":
                break
            time.sleep(0.5)
    print("tx extracts", calls, "wire attempts (incl. prior estimate)", WIRE["n"])


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, c - h), min(1.0, c + h))


MEMO_NORM = re.compile(r"\d+")


def norm_memo(m):
    m = MEMO_NORM.sub("#", m)
    return m[:40]


def tags_for(r):
    """Return list of (keytype, key) tags for one extracted record."""
    t = []
    if not r.get("ok"):
        return t
    if not r["payer_is_trader"]:
        t.append(("K1", r["payer"]))
    for m in r["memos"]:
        t.append(("K3", norm_memo(m)))
    for tr in r["transfers"]:
        if tr[2] <= 50_000_000 and tr[0] != tr[1]:
            t.append(("K2", tr[1]))
    for n in r["ix_names"]:
        if re.search(r"creator.?fee|claim|collect|burn", n, re.I):
            t.append(("K4", n))
    t.append(("K5", "|".join(r["progs"])))
    return t


def stage_a(out):
    den = json.load(open(os.path.join(out, "denominators.json")))
    recs = [json.loads(l) for l in open(os.path.join(out, "extracts.jsonl"))]
    ok = [r for r in recs if r.get("ok")]
    summary = {"n_extracts": len(recs), "n_ok": len(ok),
               "unreachable": collections.Counter(r.get("why") for r in recs if not r.get("ok")),
               "payer_is_trader": {s: [sum(1 for r in ok if r["stratum"] == s and r["payer_is_trader"]),
                                       sum(1 for r in ok if r["stratum"] == s)] for s in "AB"}}
    # tag tables
    cnt = collections.defaultdict(lambda: collections.Counter())
    venue = collections.defaultdict(collections.Counter)
    perday = collections.defaultdict(lambda: collections.Counter())
    nA = collections.Counter(r["day"] for r in ok if r["stratum"] == "A")
    nB = collections.Counter(r["day"] for r in ok if r["stratum"] == "B")
    ex = {}
    for r in ok:
        seen = set()
        for tg in tags_for(r):
            if tg in seen:
                continue
            seen.add(tg)
            cnt[tg][r["stratum"]] += 1
            venue[tg][r["venue"]] += 1
            perday[tg][(r["stratum"], r["day"])] += 1
            ex.setdefault(tg, {"memos": r["memos"][:1], "progs": r["progs"][:4], "n_signers": r["n_signers"], "ix": r["ix_names"][:6]})
    thr = {"K1": 5, "K2": 10, "K3": 5, "K4": 1, "K5": None}
    rows = []
    for tg, c in cnt.items():
        kt = tg[0]
        tot = c["A"] + c["B"]
        if kt == "K5":
            if c["A"] < 0.03 * sum(nA.values()):
                continue
        elif tot < thr[kt]:
            continue
        # projection: per full day share of A x sell_rows; median of 5 days
        proj, burst = [], []
        for d in den["denominators"]:
            if d not in nA:
                continue
            kA = perday[tg][("A", d)]
            kB = perday[tg][("B", d)]
            sh = kA / nA[d]
            proj.append(sh * den["denominators"][d]["sell_rows"])
            if nB.get(d):
                burst.append(kB / nB[d] * den["denominators"][d].get("burst_rows", 0))
        kA, nAt = c["A"], sum(nA.values())
        lo, hi = wilson(kA, nAt)
        med = sorted(proj)[len(proj) // 2] if proj else None
        meddays = sorted(den["denominators"][d]["sell_rows"] for d in nA)
        pop = meddays[len(meddays) // 2]
        vt = sum(venue[tg].values())
        rows.append({"type": kt, "key": tg[1], "A": c["A"], "B": c["B"], "nA": nAt, "nB": sum(nB.values()),
                     "share_A": round(kA / nAt, 4), "wilson95_A": [round(lo, 4), round(hi, 4)],
                     "tagged_per_day_median": round(med) if med is not None else None,
                     "tagged_per_day_wilson_hi": round(hi * pop), "tagged_per_day_wilson_lo": round(lo * pop),
                     "tagged_in_burst_per_day_median": round(sorted(burst)[len(burst) // 2]) if burst else None,
                     "pumpswap_share": round(venue[tg].get("pumpswap", 0) / vt, 3) if vt else None,
                     "example": ex[tg]})
    rows.sort(key=lambda x: (x["type"], -(x["A"] + x["B"])))
    summary["tags"] = rows
    summary["denominators"] = den["denominators"]
    summary["pop_A"], summary["pop_B"] = den["pop_A"], den["pop_B"]
    json.dump(summary, open(os.path.join(out, "census.json"), "w"), indent=1, default=dict)
    print("tags", len(rows), "ok", len(ok), "of", len(recs))


if __name__ == "__main__":
    {"S": stage_s, "F": stage_f, "A": stage_a}[sys.argv[1]](sys.argv[2])
