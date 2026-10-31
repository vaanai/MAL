#!/usr/bin/env python3
"""r4b engine memory projection on a SYNTHETIC stream (QP-RULING-1031-r4b-pc3 section 2.6; DEC-032 Am.8 Erratum 4 item 5(iv)).

Feeds the r4b_live Engine, through its live row API (ingest_create / ingest_migration / ingest_trade, one row at a time, as tools/r4b_host_glue does), a
synthetic stream of `--hours` hours after a `--preroll` fill, calls members(T) and prune(T) at every hourly mark as the host does after decide(T), and
samples the process' anon memory (/proc/self/status RssAnon, after gc.collect()) plus a structural byte count of the engine by category.

  * Run it once per code version: `--repo` is the checkout whose `tools/r4b_live.py` is used (the main checkout at 8e4d93aa for "without eviction", this
    branch for "with"). It uses only the Engine API both versions share (`evict=` is passed only if the constructor has it).
  * It computes NO decision: no decide(), no score. It measures state size. Decision equivalence is proved by tools/test_r4b_evict.py and the P-C2 md5 jobs.
  * The stream is invented. `--scale` multiplies every rate (1.0 = the rates below); the structural bytes are linear in the number of mints, so a run at
    --scale 0.1 times 10 estimates the full-rate state size (the harness prints both). The rates are a reading of QP-RULING-1031-r4b-pc3 section 2.6
    (87,113 creates / 52 h = about 1,675 a hour; 15 bonding sellers a mint in its tracemalloc figure) and are NOT measurements of the tip tape. The absolute
    MiB are not a forecast of fast-0's anon; the difference between the two versions on the same stream, and the slope by structure, are the result.
Output: one JSON object on stdout (and --out). Nothing is read from /data/mal; nothing is written but --out.
"""
from __future__ import annotations

import argparse
import gc
import inspect
import json
import math
import random
import sys
import time
import zlib
from pathlib import Path

H = 3600
BT0 = 1_786_665_600  # 2026-08-14T00Z, the synthetic clock's origin (the same as the unit tests')
S0 = 438_000_000
V = 17_600_000_000
CREATES_PER_H = 1675.0
FRAC_CREATE_ONLY = 0.10
FRAC_GRADUATE = 0.012  # about 20 graduations a hour
NC_TRADES_MEDIAN, NC_TRADES_SIGMA, NC_TRADES_CAP = 6.0, 1.2, 300
GRAD_BOND_TRADES = 800
GRAD_BOND_TRADERS = 400
GRAD_PRINTS_PER_POOL = 300  # over 60 h (the flat component; its size is the weakest guess here)
GRAD_DELAY_MEAN_S = 3 * H


def rss_anon_kb() -> int:
    for ln in open("/proc/self/status"):
        if ln.startswith("RssAnon:"):
            return int(ln.split()[1])
    return -1


def crc_hash(s):
    b = ("" if s is None else s).encode()
    return (zlib.crc32(b) << 32) | zlib.crc32(b[::-1])


def slot_of(bt: int) -> int:
    return S0 + (bt - BT0) * 5 // 2


def deep(o, seen: set) -> int:
    """Bytes of o and everything it owns that is not in `seen` (ids). Small ints / interned strings shared across objects are counted once."""
    i = id(o)
    if i in seen:
        return 0
    seen.add(i)
    n = sys.getsizeof(o)
    if isinstance(o, (str, int, float, bool, type(None), bytes)):
        return n
    if isinstance(o, dict):
        return n + sum(deep(k, seen) + deep(v, seen) for k, v in o.items())
    if isinstance(o, (list, tuple, set, frozenset)):
        return n + sum(deep(x, seen) for x in o)
    if hasattr(o, "items") and hasattr(o, "keys"):  # mappingproxy
        return n + sum(deep(k, seen) + deep(v, seen) for k, v in o.items())
    if hasattr(o, "__slots__") and not hasattr(o, "__dict__"):
        return n + sum(deep(getattr(o, a), seen) for c in type(o).__mro__ for a in getattr(c, "__slots__", ()) if hasattr(o, a))
    if hasattr(o, "__dict__"):
        return n + deep(o.__dict__, seen)
    return n


def account(eng, now_bt: int, lv) -> dict:
    """Engine state bytes by category (structural, via sys.getsizeof; not the allocator's view)."""
    seen: set = set()
    cat = {"mint_never_completed_with_bonding": 0, "mint_create_only": 0, "mint_graduated_le_50h": 0, "mint_graduated_gt_50h": 0}
    cnt = dict.fromkeys(cat, 0)
    for mint, st in eng.mints.items():
        if st.mbt is None and st.mslot is None:
            k = "mint_never_completed_with_bonding" if st.b_n else "mint_create_only"
        else:
            k = "mint_graduated_gt_50h" if (st.mbt is not None and now_bt > st.mbt + getattr(lv, "EVICT_AFTER_S", lv.GRID_HI_S + 2 * lv.MARK_S)) else "mint_graduated_le_50h"
        cat[k] += deep(st, seen) + sys.getsizeof(mint) * (id(mint) not in seen)
        seen.add(id(mint))
        cnt[k] += 1
    other = {"mints_dict_table": sys.getsizeof(eng.mints), "pda": deep(eng._pda, seen), "member_final": deep(eng._member_final, seen), "mkt_ncr": deep(eng.mkt, seen) + deep(eng.ncr, seen),
             "clock": deep(eng.clock, seen), "pending": deep(eng._pending, seen)}
    return {"bytes": cat, "counts": cnt, "other_bytes": other, "total_bytes": sum(cat.values()) + sum(other.values())}


class Gen:
    """Invented walker-schema rows, hour by hour, in slot order inside an hour."""

    def __init__(self, seed: int, scale: float) -> None:
        self.rnd = random.Random(seed)
        self.scale = scale
        self.n_mint = 0
        self.pending_grad: list[tuple[int, str]] = []  # (graduation bt, mint) of mints created earlier
        self.pools: list[tuple[str, int]] = []  # (mint, graduation bt) with prints still to come

    def mint_name(self, i: int) -> str:
        return ("M%d" % i).ljust(44, "x")

    def hour(self, h: int) -> list[tuple]:
        """All rows of simulated hour h as (bt, order, kind, row)."""
        rnd, sc = self.rnd, self.scale
        out: list[tuple] = []
        t0 = BT0 + h * H
        n_new = max(1, int(CREATES_PER_H * sc))
        for _ in range(n_new):
            i = self.n_mint
            self.n_mint += 1
            m = self.mint_name(i)
            bt = t0 + rnd.randrange(H)
            out.append((bt, 0, "create", {"type": "create", "mint": m, "creator": "C%d" % i, "is_mayhem_mode": False, "slot": slot_of(bt), "block_time": bt}))
            r = rnd.random()
            if r < FRAC_GRADUATE:
                gb = bt + 600 + int(rnd.expovariate(1.0 / GRAD_DELAY_MEAN_S))
                self.pending_grad.append((gb, m))
                n_tr, n_u = GRAD_BOND_TRADES, GRAD_BOND_TRADERS
                for j in range(n_tr):
                    tb = bt + 1 + (gb - bt - 1) * j // n_tr
                    out.append((tb, 1, "trade", self.bond(m, tb, "U%d_%d" % (i, rnd.randrange(n_u)) if j % 9 else "C%d" % i, "sell" if j % 5 == 4 else "buy", j)))
            elif r > FRAC_CREATE_ONLY + FRAC_GRADUATE:
                n_tr = min(NC_TRADES_CAP, max(1, int(rnd.lognormvariate(math.log(NC_TRADES_MEDIAN), NC_TRADES_SIGMA))))
                seen_tr: list[str] = []
                for j in range(n_tr):
                    tb = bt + 1 + j * 7
                    if not seen_tr or rnd.random() < 0.85:
                        seen_tr.append("U%d_%d" % (i, len(seen_tr)))
                        tr = seen_tr[-1]
                    else:
                        tr = rnd.choice(seen_tr)
                    out.append((tb, 1, "trade", self.bond(m, tb, tr, "sell" if rnd.random() < 0.35 else "buy", j)))
        due = [g for g in self.pending_grad if g[0] < t0 + H]
        self.pending_grad = [g for g in self.pending_grad if g[0] >= t0 + H]
        for gb, m in due:
            out.append((gb, 2, "migration", {"type": "complete", "mint": m, "slot": slot_of(gb), "block_time": gb, "tx_index": 3, "event_index": 0}))
            out.append((gb, 2, "migration", {"type": "migration", "mint": m, "slot": slot_of(gb), "block_time": gb, "tx_index": 3, "event_index": 1, "pool": "P" + m,
                                             "quote_mint": "So11111111111111111111111111111111111111112"}))
            self.pools.append((m, gb))
        per_h = GRAD_PRINTS_PER_POOL / 60.0
        live = []
        for m, gb in self.pools:
            if t0 > gb + 60 * H:
                continue
            live.append((m, gb))
            k = max(0, int(rnd.gauss(per_h, math.sqrt(per_h)))) if t0 + H > gb else 0
            for _ in range(k):
                tb = max(t0 + rnd.randrange(H), gb + 3)
                buy = rnd.random() < 0.5
                out.append((tb, 3, "trade", {"venue": "pumpswap", "mint": m, "pool": "P" + m, "trader": "W%d" % rnd.randrange(5000), "side": "buy" if buy else "sell",
                                             "sol_lamports": rnd.randint(5 * 10**7, 3 * 10**9), "token_raw": rnd.randint(10**9, 10**12), "quote_reserve": 85 * 10**9,
                                             "base_reserve": 206 * 10**12, "slot": slot_of(tb), "block_time": tb, "event_index": 0, "tx_index": rnd.randint(0, 900),
                                             "virtual_quote_reserves": V}))
        self.pools = live
        for bt in range(t0, t0 + H, 15):  # market rows with no mint: minutes and clock seconds
            out.append((bt, 4, "trade", {"venue": "pumpswap", "mint": None, "pool": None, "slot": slot_of(bt), "block_time": bt, "side": "buy", "sol_lamports": 10**8}))
        out.sort(key=lambda e: (e[0], e[1]))
        return out

    def bond(self, m: str, bt: int, trader: str, side: str, j: int) -> dict:
        return {"venue": "pump_bonding", "mint": m, "trader": trader, "side": side, "sol_lamports": 10**9, "token_raw": 10**11, "quote_reserve": 30 * 10**9,
                "base_reserve": 10**15, "pool": None, "slot": slot_of(bt), "event_index": 0, "tx_index": j, "block_time": bt}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parents[2]), help="checkout whose tools/r4b_live.py is measured")
    ap.add_argument("--hours", type=int, default=336, help="hours after the pre-roll (14 d = 336)")
    ap.add_argument("--preroll", type=int, default=52, help="hours of fill before the first sample (the host's catch-up window)")
    ap.add_argument("--scale", type=float, default=0.1)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--sample-every", type=int, default=4)
    ap.add_argument("--label", default="")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    sys.path.insert(0, a.repo)
    from tools import r4b_live as lv

    kw = {"evict": True} if "evict" in inspect.signature(lv.Engine.__init__).parameters else {}
    eng = lv.Engine(universe_min_mbt=BT0, pda_fn=lambda m: "P" + m, hash_fn=crc_hash, score_fn=lambda b, X: X[:, 0], **kw)
    gen = Gen(a.seed, a.scale)
    samples, t_start = [], time.monotonic()
    total = a.preroll + a.hours
    for h in range(total + 1):
        for bt, _o, kind, row in gen.hour(h):
            {"create": eng.ingest_create, "migration": eng.ingest_migration, "trade": eng.ingest_trade}[kind](row)
        T = BT0 + (h + 1) * H
        eng.members(T)
        eng.members()  # the host's `_decide_and_log` builds this map (T None) before prune
        eng.prune(T)
        eng.prune_clock(T)  # decide(T) does this in fast mode (CLOCK_KEEP_S = 2 h); the harness decides nothing, so it calls it
        if h % a.sample_every == 0 or h == total:
            gc.collect()
            acc = account(eng, T, lv)
            samples.append({"h": h, "since_preroll_h": h - a.preroll, "anon_kb": rss_anon_kb(), "state_bytes": acc["total_bytes"], "bytes": acc["bytes"], "counts": acc["counts"],
                            "other_bytes": acc["other_bytes"], "n_mints": len(eng.mints)})
            print(f"h {h} anon {samples[-1]['anon_kb'] // 1024} MiB state {acc['total_bytes'] // 2**20} MiB mints {len(eng.mints)} {time.monotonic() - t_start:.0f}s", file=sys.stderr, flush=True)

    def slope(key, h0, h1):
        s0 = next((s for s in samples if s["since_preroll_h"] >= h0), None)
        s1 = [s for s in samples if s["since_preroll_h"] <= h1][-1]
        if s0 is None or s1["h"] <= s0["h"]:
            return None
        v0 = s0[key] if key in s0 else 0
        return (s1[key] - v0) / (s1["h"] - s0["h"])

    scale_up = 1.0 / a.scale
    windows = [(1, 12), (12, 116), (116, a.hours), (1, a.hours)]
    rep = {"schema": "r4b_evict_memory_v1", "label": a.label, "repo": a.repo, "has_evict_flag": bool(kw), "scale": a.scale, "scale_up_factor": scale_up, "hours": a.hours, "preroll": a.preroll,
           "seed": a.seed, "synthetic": True, "engine_counters": {k: v for k, v in eng.counters.items() if "evict" in k or "skipped" in k or "after" in k},
           "windows_since_preroll_h": windows,
           "anon_slope_mib_per_h": {f"{w[0]}-{w[1]}": (None if slope("anon_kb", *w) is None else round(slope("anon_kb", *w) / 1024, 4)) for w in windows},
           "state_slope_mib_per_h": {f"{w[0]}-{w[1]}": (None if slope("state_bytes", *w) is None else round(slope("state_bytes", *w) / 2**20, 4)) for w in windows},
           "state_slope_mib_per_h_full_rate": {f"{w[0]}-{w[1]}": (None if slope("state_bytes", *w) is None else round(slope("state_bytes", *w) / 2**20 * scale_up, 3)) for w in windows},
           "anon_slope_mib_per_h_full_rate_if_linear": {f"{w[0]}-{w[1]}": (None if slope("anon_kb", *w) is None else round(slope("anon_kb", *w) / 1024 * scale_up, 3)) for w in windows},
           "final": samples[-1], "samples": samples, "seconds": round(time.monotonic() - t_start, 1)}
    txt = json.dumps(rep, indent=1, sort_keys=True)
    if a.out:
        Path(a.out).write_text(txt + "\n")
    print(txt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
