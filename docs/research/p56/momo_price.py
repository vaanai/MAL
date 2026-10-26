#!/usr/bin/env python3
"""P56 exploration pricing (see PREREG.md). Usage: momo_price.py OUT.json STAKE_SOL WALKER_DIR [WALKER_DIR ...]
Each walker dir is processed in hour order by one process. Exploration block only."""
import sys, json, subprocess, bisect, glob, collections
from multiprocessing import Pool
sys.path.insert(0, ".")
from tools.paper_curve_math import PUMPSWAP_SOL_FEE_TIERS as T

TH = [a for a, _ in T]
PPM = [b for _, b in T]
SEND = 160_000
LAG, HOLD, WIN, COOL = 8, 150, 150, 1500
MC_MIN, DEPTH_MIN, SIG = 9820.0, 400.0, 0.03


def fee(mc):
    return PPM[bisect.bisect_right(TH, mc + 1e-9) - 1] / 1e6


def work(args):
    wdir, stake = args
    S = int(stake * 1e9)
    files = sorted(glob.glob(wdir + "/trades/*.zst"))
    st = {}
    rows = []
    sigs = collections.Counter()
    open_end = 0
    for path in files:
        p = subprocess.Popen(["zstdcat", path], stdout=subprocess.PIPE)
        for ln in p.stdout:
            if b'"venue":"pumpswap"' not in ln:
                continue
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if not r.get("quote_is_wsol") or not str(r.get("mint", "")).endswith("pump"):
                continue
            q, b = r["quote_reserve"], r["base_reserve"]
            if not q or not b:
                continue
            slot = r["slot"]
            mc = q / 1e9 * 1e15 / b
            px = q / b
            s = st.setdefault(r["pool"], {"h": collections.deque(), "pend": None, "pos": None, "cool": 0})
            pos = s["pos"]
            if pos is not None:
                if slot >= pos["xs"]:
                    x = pos["x"]
                    q2, b2 = q + x, b - pos["tok"]  # our buy applied to exit reserves
                    if b2 > 0:
                        out = q2 * pos["tok"] / (b2 + pos["tok"])
                        fo = fee(q2 / 1e9 * 1e15 / b2)
                        for name, fi_, fo_ in (("tier", pos["fi"], fo), ("flat", 0.0125, 0.0125)):
                            xin = S * (1 - fi_)
                            tok = pos["b0"] * xin / (pos["q0"] + xin)
                            q3, b3 = q + xin, b - tok
                            o = q3 * tok / (b3 + tok) * (1 - fo_)
                            pos["net_" + name] = (o - S - 2 * SEND) / S
                        rows.append({"day": slot_day(r), "mc": pos["mc"], "q0": pos["q0"] / 1e9, "fi": pos["fi"], "fo": fo,
                                     "net_tier": pos["net_tier"], "net_flat": pos["net_flat"]})
                    s["pos"] = None
                continue
            if s["pend"] is not None:
                if slot >= s["pend"] + LAG:
                    fi = fee(mc)
                    x = S * (1 - fi)
                    tok = b * x / (q + x)
                    s["pos"] = {"xs": slot + HOLD, "x": x, "tok": tok, "fi": fi, "q0": q, "b0": b, "mc": mc}
                    s["pend"] = None
                continue
            h = s["h"]
            h.append((slot, px))
            while len(h) > 1 and h[1][0] <= slot - WIN:
                h.popleft()
            if slot > s["cool"] and mc >= MC_MIN and q / 1e9 >= DEPTH_MIN and len(h) > 1 and h[0][0] <= slot - WIN:
                if px / h[0][1] - 1 >= SIG:
                    s["pend"] = slot
                    s["cool"] = slot + COOL
                    sigs[slot_day(r)] += 1
        p.wait()
    open_end = sum(1 for v in st.values() if v["pos"] is not None or v["pend"] is not None)
    return rows, dict(sigs), open_end


def slot_day(r):
    return (r.get("block_time") or r.get("event_ts") or 0) // 86400


if __name__ == "__main__":
    stake = float(sys.argv[2])
    res = Pool(3).map(work, [(d, stake) for d in sys.argv[3:]])
    rows = [x for r in res for x in r[0]]
    sigs = collections.Counter()
    for r in res:
        sigs.update(r[1])
    json.dump({"rows": rows, "signals_by_day": sigs, "unclosed": [r[2] for r in res]}, open(sys.argv[1], "w"))
    print(len(rows), dict(sigs))
