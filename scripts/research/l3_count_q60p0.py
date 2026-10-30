#!/usr/bin/env python3
"""Q60 P0 counter (DEC-031 Amendment 19 item 3; plan Q4): scripts/research/l3_count_q50p0.py (sha256 8c6b4905...) with its OUTPUT restricted to the
cell G0_wsol_band|trig|Q60|strict plus the hour_quality (gap) block; every count is computed exactly as there.
hunt24 L3 BOOST-family variant STRUCTURE counts on the October arena tape. Count only: decision-time fields, no price after a decision,
no fill, no exit, no outcome, no return. stdlib only. Usage: l3_count.py OUT_JSON FILE [FILE ...]  (files in time order)

Fields read per row: type, prog, slot, t_recv_ms, signature, mint, quote_mint, trader, side, sol_lamports, quote_reserve,
virtual_quote_reserves, pool_quote_amount, pool, kind/data_loss/from_ms/to_ms (gap rows). The pool state used for the trigger test is the
PRE-trade reserve of the trigger print minus its own gross quote out (decision-time state of that print). Nothing after the decision is read.
"""
from __future__ import annotations
import collections, datetime as dt, json, re, subprocess, sys

WSOL = "So11111111111111111111111111111111111111112"
PUMPTOK = "pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
MINTRE = re.compile(r'"mint":"(\w+)"')
POOLRE = re.compile(r'"pool":"(\w+)"')
PATH_RE = re.compile(r"^/data/mal/arena/tape/tape-2026-10-(2[123]T([01][0-9]|2[0-3]))\.jsonl\.zst$")
V_SEED = 17_584_505_288.0
BAND = (17_500_000_000, 17_700_000_000)
BUDGET_SOL = 17.585e9  # H5 BOOST budget in lamports at V_SEED; scaled by V0/V_SEED elsewhere
WIN_S = 300.0       # H5 trigger window
TRACK_S = 420.0     # BOOSTed test window
LO, HI = 0.2e9, 2.0e9   # H5 BOOST slice bounds (SOL) at V_SEED; scaled
TOT_MAX = 17.7e9
QSTARS = (40.0, 50.0, 60.0)
BAD_GAP_KINDS = None
CELL = "G0_wsol_band|trig|Q60|strict"  # the only cell this copy writes (DEC-031 Amendment 19 item 3)


class Pool:
    __slots__ = ("pool", "mint", "quote", "create_ms", "s0_ms", "s0_slot", "v0", "q0", "b0", "w", "trig", "boosted", "nprints", "complete_seen", "last_ms")

    def __init__(self, pool, mint, quote, create_ms):
        self.pool, self.mint, self.quote, self.create_ms = pool, mint, quote, create_ms
        self.s0_ms = None; self.s0_slot = None; self.v0 = 0; self.q0 = 0; self.b0 = 0
        self.w = {}            # trader -> [n_ok, sum_ok, n_bad, n_sell, min_ok, max_ok]
        self.trig = {}         # (qstar, mode) -> t seconds of first qualifying sell
        self.boosted = False; self.nprints = 0; self.complete_seen = False; self.last_ms = create_ms


def group_of(p):
    q, v = p.quote, p.v0
    if q == WSOL:
        if BAND[0] <= v <= BAND[1]:
            return "G0_wsol_band"
        if 0.5e9 <= v <= 30e9:
            return "G1_wsol_offband_v0.5to30"
        if v > 30e9:
            return "G1x_wsol_v_gt30"
        return "Gz_wsol_v_lt0.5_or_neg"
    if q == PUMPTOK:
        return "G2_nonwsol_PUMPquote"
    if q == USDC:
        return "G2_nonwsol_USDCquote"
    return "G2_nonwsol_otherquote"


def day_of(ms):
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).strftime("%Y-%m-%d")


def hour_of(ms):
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).strftime("%Y-%m-%dT%H")


def main(out, files):
    for f in files:
        if not PATH_RE.match(f):
            raise SystemExit(f"REFUSED path outside the plan: {f}")
    pools = {}
    complete_ms = {}
    gaps = collections.deque(maxlen=4000)
    C = collections.defaultdict(lambda: collections.Counter())      # C[group][key|day] counters
    D = collections.defaultdict(lambda: collections.Counter())      # D[key][day]
    HQ = collections.defaultdict(lambda: collections.Counter())     # hour quality
    sigmap = {}
    pump_children = set()
    pump_day = collections.defaultdict(lambda: collections.Counter())
    pump_ratio = collections.defaultdict(list)
    first_ms = None; last_ms = None
    nrows = 0

    def finalize(p, now_ms):
        if p.s0_ms is None:
            D["pool_create_no_first_print"][day_of(p.create_ms)] += 1
            return
        g = group_of(p)
        d = day_of(p.s0_ms)
        gap_hit = any(gp[2] == "pumpswap" and gp[1] >= p.s0_ms and gp[0] <= p.s0_ms + 330_000 for gp in gaps)
        p.complete_seen = (p.mint in complete_ms and abs(p.create_ms - complete_ms[p.mint]) <= 1_800_000)
        D[f"{g}|pools"][d] += 1
        if gap_hit:
            D[f"{g}|pools_gap330"][d] += 1
        if p.complete_seen:
            D[f"{g}|pools_complete_seen"][d] += 1
        if p.boosted:
            D[f"{g}|boosted"][d] += 1
        for (qs, mode), t in p.trig.items():
            D[f"{g}|trig|Q{int(qs)}|{mode}"][d] += 1
            if not gap_hit:
                D[f"{g}|trig_clean|Q{int(qs)}|{mode}"][d] += 1
            if g == "G0_wsol_band" and qs == 40.0 and mode == "loose":
                D["G0_trig_by_hour"][hour_of(p.s0_ms)] += 1

    def sweep(now_ms):
        dead = [k for k, p in pools.items() if (p.s0_ms is not None and now_ms - p.s0_ms > TRACK_S * 1000 + 5000) or (p.s0_ms is None and now_ms - p.create_ms > 600_000)]
        for k in dead:
            finalize(pools.pop(k), now_ms)

    def flush_sig(now_ms, force=False):
        dead = [s for s, v in sigmap.items() if force or now_ms - v["t"] > 60_000]
        for s in dead:
            v = sigmap.pop(s)
            if v["pump_buy"] is not None:
                d = day_of(v["t"])
                b, q = v["pump_buy"]
                pump_day[d]["pump_buys"] += 1
                pump_day[d]["pump_buy_lamports"] += b
                for thr, nm in ((0.25e9, "ge0.25"), (0.5e9, "ge0.5"), (1e9, "ge1.0")):
                    if b >= thr:
                        pump_day[d]["pump_buys_" + nm] += 1
                if v["child"]:
                    pump_day[d]["pump_buys_with_child_leg"] += 1
                    pump_day[d]["pump_buys_with_child_leg_lamports"] += b
                    for thr, nm in ((0.25e9, "ge0.25"), (0.5e9, "ge0.5"), (1e9, "ge1.0")):
                        if b >= thr:
                            pump_day[d]["pump_buys_with_child_leg_" + nm] += 1
                    if q > 0:
                        pump_ratio[d].append(int(b * 1e6 / q))   # buy / pre-trade Q, ppm (a decision-time size ratio, not a price)

    for f in files:
        proc = subprocess.Popen(["zstd", "-dc", f], stdout=subprocess.PIPE, bufsize=1 << 22)
        hr = f[-len("2026-10-19T08.jsonl.zst"):-len(".jsonl.zst")]
        for line in proc.stdout:
            line = line.decode("utf-8", "replace") if isinstance(line, bytes) else line
            nrows += 1
            head = line[:34]
            if '"type":"trade"' in head:
                is_swap = '"prog":"pumpswap"' in line[:70]
                if is_swap:
                    m = POOLRE.search(line)
                    pk = m.group(1) if m else None
                    p = pools.get(pk) if pk else None
                    pump_hit = PUMPTOK in line
                    if p is None and not pump_hit:
                        continue
                    r = json.loads(line)
                    tms = r["t_recv_ms"]
                    if pump_hit:
                        qm = r.get("quote_mint")
                        if r.get("mint") == PUMPTOK and qm == WSOL and r["side"] == "buy":
                            e = sigmap.setdefault(r["signature"], {"t": tms, "pump_buy": None, "child": False})
                            q = (r.get("quote_reserve") or 0) + (r.get("virtual_quote_reserves") or 0)
                            e["pump_buy"] = (r["sol_lamports"], q)
                        elif qm == PUMPTOK and r.get("mint") != PUMPTOK and r["side"] == "buy":
                            e = sigmap.setdefault(r["signature"], {"t": tms, "pump_buy": None, "child": False})
                            e["child"] = True
                    if p is not None:
                        if p.s0_ms is None:
                            p.s0_ms = tms; p.s0_slot = r["slot"]
                            p.v0 = r.get("virtual_quote_reserves") or 0
                            p.q0 = r.get("quote_reserve") or 0; p.b0 = r.get("base_reserve") or 0
                        t = (tms - p.s0_ms) / 1000.0
                        if t > TRACK_S:
                            continue
                        p.nprints += 1
                        v0 = p.v0
                        if v0 < 0.5e9 or v0 > 30e9 * 1e6:
                            pass
                        k = v0 / V_SEED if v0 > 0 else 0.0
                        tr = r["trader"]; side = r["side"]; amt = r.get("sol_lamports") or 0
                        w = p.w.get(tr)
                        if w is None:
                            w = p.w[tr] = [0, 0, 0, 0, 1 << 62, 0]
                        if side == "buy":
                            if k > 0 and LO * k <= amt <= HI * k:
                                w[0] += 1; w[1] += amt; w[4] = min(w[4], amt); w[5] = max(w[5], amt)
                            else:
                                w[2] += 1
                        else:
                            w[3] += 1
                            if k > 0 and t <= WIN_S:
                                # H5 trigger test at this sell print (decision-time state = pre-trade reserves less this print's own quote out)
                                qpost = (r.get("quote_reserve") or 0) + (r.get("virtual_quote_reserves") or 0) - (r.get("pool_quote_amount") or 0)
                                for mode in ("loose", "strict"):
                                    best = None
                                    for wt, ww in p.w.items():
                                        if ww[3] == 0 and ww[2] == 0 and ww[0] >= 3 and ww[1] <= TOT_MAX * k:
                                            if mode == "strict" and ww[5] > 1.2 * ww[4]:
                                                continue
                                            if best is None or ww[0] > best[0]:
                                                best = ww
                                    if best is not None and best[1] < 0.999 * BUDGET_SOL * k:
                                        for qs in QSTARS:
                                            if qpost <= qs * 1e9 * k and (qs, mode) not in p.trig:
                                                p.trig[(qs, mode)] = t
                        if t <= TRACK_S and not p.boosted and k > 0:
                            for wt, ww in p.w.items():
                                if ww[0] >= 15 and ww[2] == 0 and ww[3] == 0 and abs(ww[1] - v0) <= 0.01 * v0:
                                    p.boosted = True
                                    break
                    last_ms = tms
                else:
                    # pump.fun curve trade: a buy of a PUMP-quoted child is the "child leg" of a forced PUMP buy
                    if pump_children and '"side":"buy"' in line:
                        m2 = MINTRE.search(line)
                        if m2 and m2.group(1) in pump_children:
                            r = json.loads(line)
                            e = sigmap.setdefault(r["signature"], {"t": r["t_recv_ms"], "pump_buy": None, "child": False})
                            e["child"] = True
                            D["pump_child_curve_buys"][day_of(r["t_recv_ms"])] += 1
                continue
            if '"type":"create"' in head:
                if PUMPTOK in line:
                    r = json.loads(line)
                    if r.get("quote_mint") == PUMPTOK:
                        pump_children.add(r["mint"])
                        D["pump_quoted_child_creates"][day_of(r["t_recv_ms"])] += 1
                continue
            if '"type":"pool_create"' in head:
                r = json.loads(line)
                mint = r.get("mint") or ""
                if mint.endswith("pump"):
                    pools[r["pool"]] = Pool(r["pool"], mint, r.get("quote_mint"), r["t_recv_ms"])
                    D["pool_create_pumpmint"][day_of(r["t_recv_ms"])] += 1
                else:
                    D["pool_create_other_mint"][day_of(r["t_recv_ms"])] += 1
                continue
            if '"type":"complete"' in head:
                r = json.loads(line)
                complete_ms[r["mint"]] = r["t_recv_ms"]
                D["complete_rows"][day_of(r["t_recv_ms"])] += 1
                if len(complete_ms) > 20000:
                    cut = r["t_recv_ms"] - 3_600_000
                    for k2 in [k2 for k2, v in complete_ms.items() if v < cut]:
                        del complete_ms[k2]
                continue
            if '"type":"gap"' in head:
                r = json.loads(line)
                if r.get("data_loss"):
                    gaps.append((r.get("from_ms") or r["t_recv_ms"], r.get("to_ms") or r["t_recv_ms"], r["prog"]))
                    HQ[hr]["dataloss_gaps_" + r["prog"]] += 1
                    HQ[hr]["dataloss_ms_" + r["prog"]] += max(0, (r.get("to_ms") or 0) - (r.get("from_ms") or 0))
                continue
            if '"type":"hb"' in head:
                r = json.loads(line)
                HQ[hr]["hb_" + r["prog"]] += 1
                continue
            if nrows % 50000 == 0:
                pass
        # end of file
        rc = proc.wait()
        if rc != 0:
            raise SystemExit(f"zstd failed {rc} on {f}")
        if last_ms:
            sweep(last_ms)
            flush_sig(last_ms)
        HQ[hr]["rows"] = nrows
        print("done", hr, "rows", nrows, "pools_open", len(pools), file=sys.stderr, flush=True)
    sweep(1 << 60)
    flush_sig(0, force=True)
    res = {  # DEC-031 Am.19 item 3 / plan Q4: the deciding cell and the hour_quality (gap) block only; no other cell, group or Q* level is written
        "files": [x.split("/")[-1] for x in files],
        "D": {CELL: dict(D.get(CELL, {}))},
        "hour_quality": {k: dict(v) for k, v in HQ.items()},
    }
    json.dump(res, open(out, "w"), indent=1, sort_keys=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
