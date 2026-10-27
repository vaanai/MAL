"""P93 score: AUC + bootstrap CI + decile calibration + CV baseline. Usage: p93_score.py LABELS.json"""
import sys, json, re
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold
from sklearn.metrics import roc_auc_score
lab = json.load(open(sys.argv[1])); km = json.load(open("/data2/mal/p93/keymap.json"))
cards = {}
for b in range(1, 7):
    for l in open(f"/data2/mal/p93/batch_{b}.jsonl"):
        d = json.loads(l); cards[d["id"]] = d
P = {}
for b in range(1, 7):
    for i, p in json.load(open(f"/data2/mal/p93/ratings/batch_{b}.json")): P[i] = float(p)
ids = [i for i in sorted(km) if lab.get(km[i]) and i in P]
print("cards", len(km), "rated", len(P), "labelled", sum(1 for i in km if lab.get(km[i])), "used", len(ids))
y = np.array([lab[km[i]]["y"] for i in ids]); p = np.array([P[i] for i in ids])
print("base rate", y.mean(), "n", len(y))
def auc(y, s): return roc_auc_score(y, s)
a = auc(y, p); rng = np.random.default_rng(1); bs = []
while len(bs) < 1000:
    ix = rng.integers(0, len(y), len(y))
    if 0 < y[ix].sum() < len(y): bs.append(auc(y[ix], p[ix]))
lo, hi = np.percentile(bs, [5, 95])
print(f"Haiku AUC {a:.4f}  CI90 [{lo:.4f}, {hi:.4f}]")
# calibration by decile of P (ties kept together by rank order)
o = np.argsort(p, kind="stable"); print("decile  n  meanP  survive_rate")
for k, ch in enumerate(np.array_split(o, 10), 1): print(k, len(ch), round(p[ch].mean(), 2), round(y[ch].mean(), 4))
print("distinct P values", len(set(p)), "mean P", p.mean(), "sd P", p.std())
# baseline
ln = np.array([len(cards[i]["name"]) for i in ids], float)
caps = np.array([int(any(c.isalpha() for c in cards[i]["symbol"]) and not any(c.islower() for c in cards[i]["symbol"])) for i in ids], float)
print("raw AUC -len(name)", auc(y, -ln), " all-caps flag", auc(y, caps), " (all-caps share", caps.mean(), ")")
X = np.c_[ln, caps]; oof = np.zeros(len(y))
for tr, te in StratifiedKFold(5, shuffle=True, random_state=1).split(X, y):
    m = LogisticRegression(max_iter=1000).fit(X[tr], y[tr]); oof[te] = m.predict_proba(X[te])[:, 1]
base = auc(y, oof); print(f"baseline 5-fold OOF AUC {base:.4f}")
v = "KILL" if (lo <= 0.55 or a <= base) else "LEAD"
print("kill: CI lower <= 0.55:", lo <= 0.55, " Haiku AUC <= baseline:", a <= base, " -> VERDICT", v)
