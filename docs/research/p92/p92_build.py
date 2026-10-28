"""P92 step 1: sample 80 test hours (stratified by UTC day, seed 1), build outcome-free cards. Reads cand.parquet (features only) and labeled.parquet ONLY for the eligible-hour list (td values; no label column is read into the cards).
Usage: p92_build.py OUTDIR"""
import sys, json, hashlib
import numpy as np
import pandas as pd

OUT = sys.argv[1]
c = pd.read_parquet("/data2/mal/p86/cand.parquet")
lab_td = pd.read_parquet("/data2/mal/p86/labeled.parquet", columns=["td"])
cnt = lab_td.groupby("td").size()
elig = sorted(t for t in cnt[cnt >= 3].index if t >= 1788393600)  # 2026-09-03T00Z
day = lambda t: pd.to_datetime(t, unit="s", utc=True).strftime("%Y-%m-%d")
days = {}
for t in elig:
    days.setdefault(day(t), []).append(t)
N = 80
tot = sum(len(v) for v in days.values())
raw = {d: N * len(v) / tot for d, v in days.items()}
q = {d: int(np.floor(x)) for d, x in raw.items()}
for d in sorted(raw, key=lambda d: -(raw[d] - q[d]))[: N - sum(q.values())]:
    q[d] += 1
rng = np.random.default_rng(1)
sample = []
for d in sorted(days):
    sample += sorted(rng.choice(days[d], q[d], replace=False).tolist())
assert len(sample) == N
order = rng.permutation(N)  # hour -> batch assignment
batches = {}
keymap = {}
for j, i in enumerate(order):
    td = sample[i]; hk = "h%02d" % (j + 1); keymap[hk] = {"td": int(td), "batch": j // 10 + 1}
    g = c[c.td == td].copy()
    r2 = np.random.default_rng([1, int(td)])
    g = g.iloc[r2.permutation(len(g))]
    cards = []
    for k, r in enumerate(g.itertuples()):
        f = {
            "vol_60m": round(r.vol60, 2), "net_flow_60m": round(r.net60, 2), "flow_ratio_60m": round(r.flow60, 4),
            "ret_5m": round(r.ret_5m, 4), "ret_15m": round(r.ret_15m, 4), "ret_60m": round(r.ret_60m, 4),
            "quote_sol": round(r.q_real, 2), "quote_eff_sol": round(r.q_real + r.v_sol, 2),
            "age_s": int(r.age_s), "close_vs_hi_6h": round(r.close_vs_hi, 4),
            "traders_15m": int(r.signers15), "top3_share_60m": round(r.top3_60m, 4),
            "prints_15m": int(r.n15), "prints_60m": int(r.n60), "last_print_age_s": int(r.td - r.lastbt),
        }
        cards.append({"id": "c%d" % (k + 1), "f": f})
        keymap[hk].setdefault("pools", {})["c%d" % (k + 1)] = r.pool
    batches.setdefault(j // 10 + 1, []).append({"hour": hk, "cards": cards})
for b, rows in batches.items():
    with open(f"{OUT}/prompts/batch_{b}.jsonl", "w") as fh:
        for row in rows:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
json.dump(keymap, open(f"{OUT}/keymap.json", "w"))  # NOT shown to raters
sysprompt = open("tools/hv7_playbooks/RANK_v0.txt").read()
old = "and sell it by a fixed code rule (stop -8%, target +20%, or 60 minutes)"
assert old in sysprompt
sysprompt = sysprompt.replace(old, "and sell it exactly 60 minutes later")
open(f"{OUT}/prompts/system.txt", "w").write(sysprompt)
print("days", {d: q[d] for d in sorted(q)})
print("sample td:", sample)
print("sha256 sample", hashlib.sha256(json.dumps(sample).encode()).hexdigest())
for b in sorted(batches):
    print("batch", b, hashlib.sha256(open(f"{OUT}/prompts/batch_{b}.jsonl", "rb").read()).hexdigest()[:16])
print("system sha256", hashlib.sha256(sysprompt.encode()).hexdigest()[:16])
