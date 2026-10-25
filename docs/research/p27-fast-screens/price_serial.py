"""P27 pricing step: SERIAL-CREATOR on explore-0814 only (see PRICING-DECLARE.md, frozen before this ran).
Run (from the repo root): python3 docs/research/p27-fast-screens/price_serial.py OUTDIR [max_hours]
Writes OUTDIR/trades.jsonl, OUTDIR/result.json. In-sample exploration; no gate claim.
"""
import collections, datetime, json, os, random, subprocess, sys, zlib

sys.path.insert(0, os.getcwd())
from tools.latency_curve import Pressure, fit_curve  # noqa: E402

ROOT = "/data/mal/blocks-clean/explore-0814"
OUT = sys.argv[1]
MAXH = int(sys.argv[2]) if len(sys.argv) > 2 else 10**9
os.makedirs(OUT, exist_ok=True)
K_PROG = 85.005359
STAKE = 0.25
FEE = 0.0125
SEND = 0.0001
LAG = 5            # slots (1.9 s / 0.4 s, rounded up)
CAP = 1500         # slots (600 s / 0.4 s)
D0, D1 = "2026-08-15", "2026-08-27"
FLAT = 0.15
LAM = 1e9


def zlines(path):
    p = subprocess.Popen(["zstd", "-dc", path], stdout=subprocess.PIPE, text=True, bufsize=1 << 20)
    for l in p.stdout:
        yield l
    p.wait()


def hours():
    hs = collections.defaultdict(dict)
    for w in range(1, 8):
        for kind in ("creates", "trades", "migrations"):
            d = os.path.join(ROOT, "w%d" % w, kind)
            if not os.path.isdir(d):
                continue
            for fn in os.listdir(d):
                hs[fn.split(kind + "-")[1][:13]][kind] = os.path.join(d, fn)
    return sorted(hs.items())


def day(bt):
    return datetime.datetime.utcfromtimestamp(bt).strftime("%Y-%m-%d")


creates = {}       # mint -> (bt, creator, mayhem)
by_creator = collections.defaultdict(list)  # creator -> [complete bt]
complete_slot = {}
state = {}         # mint -> dict(group, phase, ...)
trades_out = open(os.path.join(OUT, "trades.jsonl"), "w")
stats = collections.Counter()


def finalize(m, st):
    pr = st["prints"]
    D = st["D"]
    li = None
    for i, p in enumerate(pr):
        if p[0] >= D + LAG:
            li = i
            break
    rec = dict(group=st["group"], mint=m, day=st["day"], bin=st["bin"], dslot=D)
    if li is None:
        rec["status"] = "unfilled"
        stats["unfilled_" + st["group"]] += 1
        trades_out.write(json.dumps(rec) + "\n")
        return
    L, _, _, vS, vT = pr[li]
    cap = L + CAP + LAG
    xi = li
    for i in range(li, len(pr)):
        if pr[i][0] <= cap:
            xi = i
        else:
            break
    net_in = STAKE * (1 - FEE)
    toks = vT - vS * vT / (vS + net_in)
    # exit: later tape state with our buy applied (additive delta), sell all tokens
    _, _, _, xS, xT = pr[xi]
    xS2 = xS + net_in
    xT2 = xT - toks
    out = xS2 * toks / (xT2 + toks) if xT2 + toks > 0 else 0.0
    pnl = out * (1 - FEE) - STAKE - 2 * SEND
    same = sum(1 for p in pr[:li + 1] if p[1] == "buy" and p[0] == L)
    near = sum(p[2] for p in pr[:li + 1] if p[1] == "buy" and L - 5 <= p[0] <= L)
    rec.update(status="filled", pnl=pnl, same_slot=same, nearby_lam=near, entry_slot=L, exit_slot=pr[xi][0],
               exit_idx_rel=xi - li, completed=(m in complete_slot and pr[xi][0] >= complete_slot[m]),
               tokens=toks)
    stats["filled_" + st["group"]] += 1
    trades_out.write(json.dumps(rec) + "\n")


nh = 0
for hr, f in hours():
    nh += 1
    if nh > MAXH:
        break
    ev = []
    for l in (zlines(f["creates"]) if "creates" in f else ()):
        r = json.loads(l)
        if r.get("type") == "create":
            ev.append((r["block_time"], 0, r))
    for l in (zlines(f["migrations"]) if "migrations" in f else ()):
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
            creates[m] = (bt, cr, may)
            if may:
                continue
            prior = [c for c in by_creator.get(cr, ()) if bt - 48 * 3600 <= c < bt]
            if len(prior) >= 2:
                state[m] = dict(group="serial", phase="wait", prints=[])
                stats["serial_creates"] += 1
            elif zlib.crc32(m.encode()) % 3 == 0:
                state[m] = dict(group="placebo", phase="wait", prints=[])
        else:
            if m in creates and m not in complete_slot:
                complete_slot[m] = r["slot"]
                by_creator[creates[m][1]].append(bt)
    if "trades" not in f:
        continue
    rows = []
    for l in zlines(f["trades"]):
        if '"venue":"pump_bonding"' not in l:
            continue
        i = l.find('"mint":"')
        if i < 0:
            continue
        m = l[i + 8:l.find('"', i + 8)]
        st = state.get(m)
        if st is None or st["phase"] == "done":
            continue
        r = json.loads(l)
        if r.get("type") not in (None, "trade"):
            continue
        rows.append((r["block_time"], r["slot"], r.get("tx_index") or 0, m, r))
    rows.sort(key=lambda x: x[:3])
    for bt, slot, _, m, r in rows:
        st = state[m]
        if st["phase"] == "done":
            continue
        q = r.get("quote_reserve") or 0
        b = r.get("base_reserve") or 0
        if q <= 0 or b <= 0:
            continue
        pr_t = (slot, r.get("side"), int(r.get("sol_lamports") or 0), q / LAM, float(b))
        if st["phase"] == "wait":
            age = bt - creates[m][0]
            p = (q / LAM - 30.0) / K_PROG
            d = day(bt)
            if age >= 30 and p < 0.25:
                if D0 <= d <= D1:
                    st.update(phase="track", D=slot, day=d, bin=int(max(p, 0) / 0.05), prints=[pr_t], L=None)
                    stats["decisions_" + st["group"]] += 1
                else:
                    st["phase"] = "done"
            continue
        # tracking
        if st["L"] is not None and slot > st["L"] + CAP + LAG:
            finalize(m, st)
            st["phase"] = "done"
            st["prints"] = []
            continue
        st["prints"].append(pr_t)
        if st["L"] is None and slot >= st["D"] + LAG:
            st["L"] = slot
    print(hr, "creates", len(creates), "active", sum(1 for s in state.values() if s["phase"] == "track"), dict(stats), flush=True)

for m, st in state.items():
    if st["phase"] == "track":
        finalize(m, st)
        st["phase"] = "done"
trades_out.close()

# ---------------- analysis ----------------
recs = [json.loads(l) for l in open(os.path.join(OUT, "trades.jsonl"))]
filled = {g: [r for r in recs if r["group"] == g and r["status"] == "filled"] for g in ("serial", "placebo")}
days = [(datetime.date(2026, 8, 15) + datetime.timedelta(i)).isoformat() for i in range(13)]


def ci_lo(v):
    rng = random.Random(1)
    n = len(v)
    means = []
    for _ in range(1000):
        t = 0.0
        for _i in range(n):
            t += v[rng.randrange(n)]
        means.append(t / n)
    means.sort()
    return means[int(0.05 * (len(means) - 1))]


def ev_series(rs, model):
    if model == "flat":
        return [(1 - FLAT) * r["pnl"] - FLAT * SEND for r in rs]
    curve = fit_curve([Pressure(r["same_slot"], r["nearby_lam"]) for r in rs])
    out = []
    for r in rs:
        p = curve.p(Pressure(r["same_slot"], r["nearby_lam"]))
        out.append((1 - p) * r["pnl"] - p * SEND)
    return out


def summarize(rs, v):
    n = len(v)
    s = dict(n=n, mean=sum(v) / n, total=sum(v), ci90_lo=ci_lo(v))
    s["ex_top3_total"] = sum(v) - sum(sorted(v, reverse=True)[:3])
    dd = collections.OrderedDict((d, [0, 0.0]) for d in days)
    for r, x in zip(rs, v):
        dd[r["day"]][0] += 1
        dd[r["day"]][1] += x
    s["by_day"] = {d: dict(n=a, total=b) for d, (a, b) in dd.items()}
    s["pos_days"] = sum(1 for a, b in dd.values() if b > 0)
    s["ex_best_day_total"] = sum(v) - max(b for a, b in dd.values())
    s["trades_per_day"] = n / 13.0
    s["sol_per_day_at_0.25"] = n / 13.0 * s["mean"]
    return s


res = dict(stats=dict(stats), serial_unfilled=sum(1 for r in recs if r["status"] == "unfilled" and r["group"] == "serial"))
res["completed_before_cap_serial"] = sum(1 for r in filled["serial"] if r["completed"])
res["landed_mean_pnl_serial"] = sum(r["pnl"] for r in filled["serial"]) / max(len(filled["serial"]), 1)
res["landed_mean_pnl_placebo_all"] = sum(r["pnl"] for r in filled["placebo"]) / max(len(filled["placebo"]), 1)
for model in ("flat", "pressure"):
    res["serial_" + model] = summarize(filled["serial"], ev_series(filled["serial"], model))
rng = random.Random(1)
cells = collections.defaultdict(list)
for r in filled["placebo"]:
    cells[(r["day"], r["bin"])].append(r)
matched = []
for r in filled["serial"]:
    c = cells.get((r["day"], r["bin"]))
    if not c:
        matched.append(None)
    elif len(c) > 1:
        matched.append(c.pop(rng.randrange(len(c))))
    else:
        matched.append(c[0])
        res["placebo_reused"] = res.get("placebo_reused", 0) + 1
res["placebo_unmatched"] = sum(1 for m in matched if m is None)
pairs = [(a, b) for a, b in zip(filled["serial"], matched) if b is not None]
res["placebo_n"] = len(pairs)
for model in ("flat", "pressure"):
    if pairs:
        sa = [a for a, b in pairs]
        pb = [b for a, b in pairs]
        pv = ev_series(pb, model)
        res["placebo_" + model] = summarize(pb, pv)
        diffs = [x - y for x, y in zip(ev_series(sa, model), pv)]
        res["diff_" + model] = dict(mean=sum(diffs) / len(diffs), ci90_lo=ci_lo(diffs))
kill = []
for model in ("flat", "pressure"):
    s = res["serial_" + model]
    if s["mean"] <= 0:
        kill.append("mean<=0 (%s)" % model)
    if s["ex_top3_total"] <= 0:
        kill.append("ex-top-3<=0 (%s)" % model)
    if s["pos_days"] < 5:
        kill.append("pos_days<5 (%s)" % model)
res["kill_reasons"] = kill
res["verdict"] = "KILL" if kill else "SURVIVES kill rules (not a gate pass)"
json.dump(res, open(os.path.join(OUT, "result.json"), "w"), indent=1)
print("RESULT", res["verdict"], kill, dict(stats))
for model in ("flat", "pressure"):
    s = res["serial_" + model]
    print(model, {k: v for k, v in s.items() if k != "by_day"})
    for d, x in s["by_day"].items():
        print("  ", d, x["n"], round(x["total"], 4))
for k in ("placebo_flat", "placebo_pressure", "diff_flat", "diff_pressure"):
    if k in res:
        print(k, {a: b for a, b in res[k].items() if a != "by_day"})
