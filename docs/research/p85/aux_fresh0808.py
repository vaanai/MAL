"""P85: aux table for fresh-0808 built exactly like hunt24 l4-audit/aux.py (same counts.rd_mig / rd_cre), no lookback. Reads migrations + creates rows of the block (structure only)."""
import sys, json, glob
sys.dont_write_bytecode = True
sys.path.insert(0, "/data/mal/hunt-1008/push-1016/hunt24/l4-audit")
from multiprocessing import Pool
import counts as C
CV = "/data/mal/clean-view/fresh-0808"
dirs = [f"{CV}/w{i}" for i in (1, 2, 3)]
mig = {}; may = set(); have = set()
with Pool(2) as P:
    for d in P.imap(C.rd_mig, [f for _, f in C.hourfiles(dirs, "migrations")]):
        for k, v in d.items(): mig.setdefault(k, v)
    for m, h in P.imap(C.rd_cre, [f for _, f in C.hourfiles(dirs, "creates")]):
        may |= m; have |= h
json.dump(dict(mig=mig, have=sorted(have), mayhem=sorted(may)), open("/data/mal/hunt-1008/ops/manager17/p85/work/aux_fresh0808.json", "w"))
print("fresh0808", len(mig), len(have), len(may))
