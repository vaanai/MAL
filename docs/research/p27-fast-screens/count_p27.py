"""P27 counts only. argv: out.json [max_hours]. Reads fresh-0903 clean copy (exploration pool), see DECLARE.md.
No price path after a decision, no exit, no P&L. Decision rows are only identified; nothing after them is read for any metric.
Run: python3 count_p27.py out.json
"""
import json, subprocess, sys, os, collections, datetime

ROOT = "/data/mal/blocks-clean/fresh-0903"
OUT = sys.argv[1]
MAXH = int(sys.argv[2]) if len(sys.argv) > 2 else 10**9
K_PROG = 85.005359
V_SOL = 17.58


def zlines(path):
    p = subprocess.Popen(["zstd", "-dc", path], stdout=subprocess.PIPE, text=True, bufsize=1 << 20)
    for l in p.stdout:
        yield l
    p.wait()


def hours():
    hs = collections.defaultdict(dict)
    for w in ("w1", "w2", "w3"):
        for kind in ("creates", "trades", "migrations"):
            d = os.path.join(ROOT, w, kind)
            if not os.path.isdir(d):
                continue
            for fn in os.listdir(d):
                hr = fn.split(kind + "-")[1][:13]
                hs[hr][kind] = os.path.join(d, fn)
    return sorted(hs.items())


def day(bt):
    return datetime.datetime.utcfromtimestamp(bt).strftime("%Y-%m-%d")


def hr_of(bt):
    return datetime.datetime.utcfromtimestamp(bt).hour


def imp(q, s):
    return s / (q + s) + s / (q + 2 * s)


creates = {}          # mint -> (bt, mayhem, creator)
completes_by_creator = collections.defaultdict(list)  # creator -> [complete bt]
pend_grad = {}        # mint -> (grad bt, mayhem)
done_grad = set()
may_state = {}        # mayhem mint -> dict(p1=None, done=False)
fired = {"bcmid": set(), "bclate": set(), "serial": set()}
serial_flag = set()
D = {k: collections.defaultdict(lambda: dict(n=0, mints=set(), hours=collections.Counter(), q=[], age300=0))
     for k in ("collapse", "grad6_may", "bcmid", "bclate", "serial", "grad6_non")}
serial_creates = collections.Counter()
all_creates = collections.Counter()
may_creates = collections.Counter()


def rec(k, bt, mint, q_sol, age=None):
    r = D[k][day(bt)]
    r["n"] += 1
    r["mints"].add(mint)
    r["hours"][hr_of(bt)] += 1
    r["q"].append(round(q_sol, 3))
    if age is not None and age >= 300:
        r["age300"] += 1


nh = 0
for hr, f in hours():
    nh += 1
    if nh > MAXH:
        break
    ev = []
    if "creates" in f:
        for l in zlines(f["creates"]):
            r = json.loads(l)
            if r.get("type") == "create":
                ev.append((r["block_time"], 0, r))
    if "migrations" in f:
        for l in zlines(f["migrations"]):
            r = json.loads(l)
            if r.get("type") == "complete":
                ev.append((r["block_time"], 1, r))
    ev.sort(key=lambda x: (x[0], x[1]))
    for bt, kind, r in ev:
        m = r["mint"]
        if kind == 0:
            if m in creates:
                continue
            may = bool(r.get("is_mayhem_mode"))
            cr = r.get("creator")
            creates[m] = (bt, may, cr)
            all_creates[day(bt)] += 1
            if may:
                may_creates[day(bt)] += 1
                may_state[m] = dict(p1=None, done=False)
            else:
                prior = [c for c in completes_by_creator.get(cr, ()) if bt - 48 * 3600 <= c < bt]
                if len(prior) >= 2:
                    serial_flag.add(m)
                    serial_creates[day(bt)] += 1
        else:
            c = creates.get(m)
            if c is None or m in done_grad or m in pend_grad:
                continue
            completes_by_creator[c[2]].append(bt)
            pend_grad[m] = (bt, c[1])
    if "trades" not in f:
        continue
    rows = []
    for l in zlines(f["trades"]):
        if '"venue":"pump_bonding"' in l:
            m = None
            r = json.loads(l)
            if r.get("type") not in (None, "trade"):
                continue
            m = r["mint"]
            c = creates.get(m)
            if c is None:
                continue
            rows.append((r["block_time"], r["slot"], r.get("tx_index") or 0, 0, m, r))
        else:
            i = l.find('"mint":"')
            if i < 0:
                continue
            m = l[i + 8:l.find('"', i + 8)]
            g = pend_grad.get(m)
            if g is None:
                continue
            r = json.loads(l)
            if r.get("type") not in (None, "trade") or r.get("venue") != "pumpswap":
                continue
            rows.append((r["block_time"], r["slot"], r.get("tx_index") or 0, 1, m, r))
    rows.sort(key=lambda x: x[:4])
    for bt, _, _, vn, m, r in rows:
        t0, may, cr = creates[m]
        if vn == 1:
            g = pend_grad.get(m)
            if g is None:
                continue
            gbt, gmay = g
            if bt >= gbt + 86400:
                del pend_grad[m]
                continue
            if bt >= gbt + 360:
                q = (r.get("quote_reserve") or 0) / 1e9
                rec("grad6_may" if gmay else "grad6_non", bt, m, q)
                done_grad.add(m)
                del pend_grad[m]
            continue
        age = bt - t0
        if may:
            st = may_state.get(m)
            if st is None or st["done"]:
                continue
            if 15 <= age <= 60 and r.get("price_sol"):
                st["p1"] = r["price_sol"]
            elif 300 <= age < 360 and st["p1"] and r.get("price_sol"):
                st["done"] = True
                if r["price_sol"] < 0.5 * st["p1"]:
                    rec("collapse", bt, m, (r.get("quote_reserve") or 0) / 1e9)
            elif age >= 360:
                st["done"] = True
            continue
        q = (r.get("quote_reserve") or 0) / 1e9
        if q <= 0:
            continue
        p = (q - 30.0) / K_PROG
        if age >= 60 and 0.50 <= p < 0.75 and m not in fired["bcmid"]:
            fired["bcmid"].add(m)
            rec("bcmid", bt, m, q, age)
        if age >= 60 and 0.75 <= p < 0.90 and m not in fired["bclate"]:
            fired["bclate"].add(m)
            rec("bclate", bt, m, q, age)
        if m in serial_flag and m not in fired["serial"] and age >= 30 and p < 0.25:
            fired["serial"].add(m)
            rec("serial", bt, m, q, age)
    print(hr, "creates", len(creates), "pend", len(pend_grad), flush=True)


def pct(a, p):
    a = sorted(a)
    return a[int(p * (len(a) - 1))] if a else None


out = {}
for k, dd in D.items():
    out[k] = {}
    for d, r in sorted(dd.items()):
        q = r["q"]
        extra = {}
        if k.startswith("grad6"):
            qv = [x + V_SOL for x in q]
            extra = dict(impact_025_rawq_med=pct([imp(x, .25) for x in q if x > 0], .5), impact_025_withV_med=pct([imp(x, .25) for x in qv], .5),
                         impact_05_withV_med=pct([imp(x, .5) for x in qv], .5), impact_1_withV_med=pct([imp(x, 1) for x in qv], .5),
                         share_025_le2pct_rawq=(sum(1 for x in q if x > 0 and imp(x, .25) <= .02) / len(q)) if q else None,
                         share_025_le2pct_withV=(sum(1 for x in qv if imp(x, .25) <= .02) / len(q)) if q else None)
        else:
            extra = dict(impact_025_med=pct([imp(x, .25) for x in q if x > 0], .5), impact_05_med=pct([imp(x, .5) for x in q if x > 0], .5),
                         impact_1_med=pct([imp(x, 1) for x in q if x > 0], .5),
                         share_025_le2pct=(sum(1 for x in q if x > 0 and imp(x, .25) <= .02) / len(q)) if q else None)
        hc = r["hours"]
        out[k][d] = dict(decisions=r["n"], mints=len(r["mints"]), q_p10=pct(q, .1), q_med=pct(q, .5), q_p90=pct(q, .9),
                         top_hour_share=(max(hc.values()) / r["n"]) if r["n"] else None, age_ge300=r["age300"], **extra)
out["creates_per_day"] = dict(all_creates)
out["mayhem_creates_per_day"] = dict(may_creates)
out["serial_flagged_creates_per_day"] = dict(serial_creates)
json.dump(out, open(OUT, "w"), indent=1, sort_keys=True)
print("done hours", nh)
