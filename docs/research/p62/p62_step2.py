"""P62 step 2 per docs/research/p62/PREREG.md. Exploration blocks only (explore-0814 = discovery,
fresh-0903 + exp011-0909 = confirmation). Public RPC only. Prints counts/AUC; writes a json."""
import glob, json, random, re, subprocess, sys, time, urllib.request, collections

RPC = "https://api.mainnet-beta.solana.com"
CV = "/data/mal/clean-view"
SPLITS = {"discovery": ["explore-0814"], "confirmation": ["fresh-0903", "exp011-0909"]}
N = int(sys.argv[1]) if len(sys.argv) > 1 else 400
OUT = sys.argv[2] if len(sys.argv) > 2 else "p62_step2.json"
MAXF = int(sys.argv[3]) if len(sys.argv) > 3 else 10**9  # test slice: max create/migration files per block


BAD = collections.Counter()


def zlines(p):
    """stream decoded lines (no whole-file buffer)."""
    pr = subprocess.Popen(["zstd", "-dc", p], stdout=subprocess.PIPE, text=True, errors="replace")
    for l in pr.stdout:
        yield l.rstrip("\n")
    pr.stdout.close()
    if pr.wait() != 0:
        BAD["zstd_nonzero:" + p.split("/")[-3]] += 1


def jl(l, kind):
    try:
        return json.loads(l)
    except ValueError:
        BAD[kind] += 1
        return None


def rpc_tx(sig):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getTransaction",
                       "params": [sig, {"encoding": "json", "maxSupportedTransactionVersion": 1}]}).encode()
    for i in range(6):
        try:
            r = json.load(urllib.request.urlopen(urllib.request.Request(RPC, body, {"Content-Type": "application/json"}), timeout=30))
            if "error" not in r:
                return r["result"]
        except Exception:
            pass
        time.sleep(1 + 2 * i)
    return "ERR"


def auc(score, y):
    pos = [s for s, v in zip(score, y) if v]
    neg = [s for s, v in zip(score, y) if not v]
    if not pos or not neg:
        return None
    w = 0.0
    for p in pos:
        for n in neg:
            w += 1.0 if p > n else 0.5 if p == n else 0.0
    return w / (len(pos) * len(neg))


def build(split):
    rnd = random.Random(1)
    sig, comp = {}, []
    for blk in SPLITS[split]:
        for f in sorted(glob.glob(f"{CV}/{blk}/*/creates/creates-*.jsonl.zst"))[:MAXF]:
            for l in zlines(f):
                c = jl(l, "bad_creates")
                if not c or "mint" not in c or "signature" not in c:
                    continue
                sig[c["mint"]] = c["signature"]
        for f in sorted(glob.glob(f"{CV}/{blk}/*/migrations/migrations-*.jsonl.zst"))[:MAXF]:
            for l in zlines(f):
                m = jl(l, "bad_migrations")
                if m and m.get("type") == "complete":
                    comp.append((m["mint"], m["event_ts"], f)) if "mint" in m and "event_ts" in m else BAD.update(["migration_missing_fields"])
    comp = sorted(set((a, b) for a, b, _ in comp))
    have = [(m, t) for m, t in comp if m in sig]
    print(split, "migrations", len(comp), "with create in block", len(have), flush=True)
    rnd.shuffle(have)
    return have, sig, len(comp)


def outcomes(split, cand):
    """first-trade reference and 30-min min price for candidate (mint, ts); streams hour files once."""
    need = collections.defaultdict(set)
    for m, t in cand:
        h = t - t % 3600
        need[h].add(m)
        need[h + 3600].add(m)
    byhour = {}
    for blk in SPLITS[split]:
        for f in glob.glob(f"{CV}/{blk}/*/trades/trades-*.jsonl.zst"):
            ts = re.search(r"trades-(\d{4}-\d\d-\d\dT\d\d)", f).group(1)
            h = int(time.mktime(time.strptime(ts, "%Y-%m-%dT%H")) - time.timezone)
            byhour[h] = f
    px = collections.defaultdict(list)
    for h, mints in need.items():
        f = byhour.get(h)
        if not f:
            continue
        for l in zlines(f):
            if '"pumpswap"' not in l or '"quote_is_wsol":true' not in l:
                continue
            i = l.find('"mint":"')
            if l[i + 8:i + 52].split('"')[0] not in mints:
                continue
            r = jl(l, "bad_trades")
            if not r or "event_ts" not in r or "price_sol" not in r:
                BAD["trade_missing_fields"] += 1
                continue
            px[r.get("mint")].append((r["event_ts"], r.get("slot", 0), r.get("event_index", 0), r["price_sol"]))
    res = {}
    for m, t in cand:
        s = sorted(x for x in px.get(m, []) if x[0] >= t)
        if not s:
            continue
        ref = s[0][3]
        later = [x[3] for x in s[1:] if x[0] <= t + 1800]
        if not later or not ref or ref <= 0:
            continue
        res[(m, t)] = 1 if min(later) <= 0.2 * ref else 0
    return res


def main():
    out = {}
    for split in SPLITS:
        have, sig, ncomp = build(split)
        # eligibility needs outcome existence; compute on the first 2N shuffled candidates, keep first N eligible
        cand = have[:2 * N]
        lab = outcomes(split, cand)
        elig = [c for c in cand if c in lab][:N]
        print(split, "eligible", len(elig), flush=True)
        rows = []
        for m, t in elig:
            tx = rpc_tx(sig[m])
            time.sleep(0.15)
            if tx == "ERR" or tx is None:
                rows.append({"mint": m, "cfg": None, "bl": lab[(m, t)]})
                continue
            logs = tx.get("meta", {}).get("logMessages", [])
            cfg = int(any("CreateFeeSharingConfig" in x for x in logs))
            rows.append({"mint": m, "ts": t, "cfg": cfg, "bl": lab[(m, t)]})
        out[split] = {"n_migrations": ncomp, "rows": rows}
        json.dump(out, open(OUT, "w"))
    summ = {}
    for split, d in out.items():
        r = [x for x in d["rows"] if x["cfg"] is not None]
        score = [1 - x["cfg"] for x in r]
        y = [x["bl"] for x in r]
        a = auc(score, y)
        rnd = random.Random(1)
        cfgs = [x["cfg"] for x in r]
        sh = []
        for _ in range(1000):
            rnd.shuffle(cfgs)
            sh.append(auc([1 - c for c in cfgs], y))
        sh = sorted(v for v in sh if v is not None)
        tab = collections.Counter((x["cfg"], x["bl"]) for x in r)
        summ[split] = {"n": len(r), "n_rpc_fail": len(d["rows"]) - len(r), "cfg1_share": sum(x["cfg"] for x in r) / max(1, len(r)),
                       "veto_share": sum(score) / max(1, len(r)), "bl_rate": sum(y) / max(1, len(y)),
                       "bl_rate_cfg0": tab[(0, 1)] / max(1, tab[(0, 0)] + tab[(0, 1)]),
                       "bl_rate_cfg1": tab[(1, 1)] / max(1, tab[(1, 0)] + tab[(1, 1)]),
                       "n_cfg1": tab[(1, 0)] + tab[(1, 1)], "auc": a,
                       "placebo_mean": sum(sh) / len(sh) if sh else None, "placebo_p95": sh[int(.95 * (len(sh) - 1))] if sh else None}
    print(json.dumps(summ, indent=1))
    print("malformed/skipped line counts:", dict(BAD))
    summ["_skipped"] = dict(BAD)
    json.dump({"summary": summ, **out}, open(OUT, "w"))


main()
