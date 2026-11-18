#!/usr/bin/env python3
"""floor_other_quote_funnel: COUNTS-ONLY funnel of FLOOR-style (l1-floor-v2, tools/arena_rule_l1.py) trigger pools among custom-pair (non-WSOL quote) PumpSwap pools.
PAPER, read-only. Reached through `floor_variants_funnel.py --universe other-quote` (design: floor-siblings-design.md section 3).

Own table (tools/arena_pool_table.PoolTable drops every non-WSOL row at line ~161 and is left unchanged): per pool, base time = first print seen (so the tape must be seeded
>= 201 h before the first counted day, else ages are under-estimated and counts are a lower bound), V_ref = the pool's first-row virtual_quote_reserves, prints trimmed to the
pool-table history window.
Rule, in the order FLOOR tests it (stage names in STAGES): PumpSwap SELL, quote_mint != WSOL; the quote has a SOL price in the declared price file; V / V_ref inside
[V_LO/V_MID, V_HI/V_MID] (the WSOL band 17.5-17.7e9 scaled by the pool's own first V; V_MID = 17.6e9); age [24 h, 200 h]; vault after the print (quote units, same 1.25% formula as
FLOOR) times the quote's SOL price <= 0.5 SOL; a previous visible print; previous print <= 6 h; 24 h volume (quote units times price) >= 1 SOL; post-trade spot / spot at the
print 6 h back < 0.80 (a ratio of two spots of the same pool, so the quote price cancels).
Quote price source (declared): the JSON file given with --quote-sol-prices, {quote_mint: lamports of SOL per ONE RAW unit of the quote}, one static number per quote mint for the
whole run, supplied by the manager from a public reference outside the tape and the tip tape. The tool never derives a price from a trade row. A mint missing from the file is
dropped at stage `quote_price_known`. The quote amounts are read from `sol_lamports` / `quote_reserve`, which for a non-WSOL pool hold raw quote units.
OUTPUT: only the keys in ALLOWED_* below (distinct pool counts, row counts, quote-mint counts, stage names); no price, mark, exit, P&L, pool or mint identifier, or decision
content is held in the accumulators or written, and write_counts() refuses any other key.
"""
import collections
import hashlib
import json
import os
import subprocess
import sys
from bisect import bisect_left, bisect_right
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tools.arena_pool_table import PoolPrints, WSOL, HIST_HOURS, HOUR_MS, FEE, V_LO, V_HI, spot_after  # noqa: E402
from floor_nov_funnel import hours, day_of  # noqa: E402

H = 3600
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
V_MID = 17_600_000_000
AGE_LO, AGE_HI, Q_STAR, ALIVE_S, VOL_MIN, R6_MAX = 24 * H, 200 * H, 0.5e9, 6 * H, 1e9, 0.80
AGE_DROP_S = AGE_HI + H
STAGES = ["other_quote_sell_rows", "quote_price_known", "v_scaled_band", "age_24_200", "vault_le_0.5_soleq", "prev_print_visible", "prev_le_6h", "vol24_ge_1_soleq",
          "r6_lt_0.80(trigger)"]
CLASSES = ("usdc", "pump", "other")
ALLOWED_TOP = {"universe", "first_hour", "last_hour", "stages", "quote_price_file_sha256", "days"}
ALLOWED_CELL = {"trigger_pools", "trigger_rows", "funnel", "quote_mints_seen", "quote_mints_trigger"}
ALLOWED_FUNNEL = {"stage", "pools_reaching", "pools_dropped"}


def quote_class(mint):
    if mint == USDC:
        return "usdc"
    if isinstance(mint, str) and mint.endswith("pump"):
        return "pump"
    return "other"


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


class Counter:
    """Accumulates distinct-pool sets and row counts per (day, quote class). Holds identifiers only for de-duplication; write_counts emits sizes."""

    def __init__(self, prices):
        self.prices = {k: float(v) for k, v in prices.items()}
        self.first = {}     # pool -> first print time (event_ts, s)
        self.vref = {}      # pool -> first-row V
        self.prints = {}    # pool -> PoolPrints (sol field holds SOL-equivalent lamports)
        self.reach = collections.defaultdict(lambda: collections.defaultdict(set))   # (day, cls) -> stage -> pools
        self.trig_rows = collections.Counter()
        self.mints_seen = collections.defaultdict(set)
        self.mints_trig = collections.defaultdict(set)

    def row(self, r):
        t = r.get("t_recv_ms")
        if r.get("type") != "trade" or not isinstance(t, int) or isinstance(t, bool):
            return
        if (r.get("venue") or r.get("prog")) != "pumpswap":
            return
        qm, pool, ts, side = r.get("quote_mint"), r.get("pool"), r.get("event_ts"), r.get("side")
        if not qm or qm == WSOL or not pool or r.get("slot") is None or side not in ("buy", "sell") or not _num(ts) or ts <= 0:
            return
        bt, hidx = int(ts), t // HOUR_MS
        if pool not in self.first:
            self.first[pool] = bt
            v0 = r.get("virtual_quote_reserves")
            self.vref[pool] = float(v0) if _num(v0) and v0 > 0 else None
        price = self.prices.get(qm)
        if price is not None:
            try:
                sol_eq, spot = float(r["sol_lamports"]) * price, spot_after(r)
            except (KeyError, TypeError, ValueError):
                return
            pp = self.prints.setdefault(pool, PoolPrints())
            pp.add(bt, sol_eq, spot, hidx)
            pp.trim(hidx - (HIST_HOURS - 1))
            if bt - self.first[pool] > AGE_DROP_S:
                del self.prints[pool]
                pp = None
        if side != "sell":
            return
        cell = (day_of(t), quote_class(qm))
        reach = self.reach[cell]
        reach[STAGES[0]].add(pool)
        self.mints_seen[cell].add(qm)
        if price is None:
            return
        reach[STAGES[1]].add(pool)
        v, vref = r.get("virtual_quote_reserves"), self.vref[pool]
        if not _num(v) or vref is None or not V_LO / V_MID <= v / vref <= V_HI / V_MID:
            return
        reach[STAGES[2]].add(pool)
        age = bt - self.first[pool]
        if not AGE_LO <= age <= AGE_HI:
            return
        reach[STAGES[3]].add(pool)
        try:
            qpost = float(r["quote_reserve"]) - float(r["sol_lamports"]) / (1 - FEE)
        except (KeyError, TypeError, ValueError):
            return
        if not qpost * price <= Q_STAR:
            return
        reach[STAGES[4]].add(pool)
        pp = self.prints.get(pool)
        if pp is None or not len(pp):
            return
        n = len(pp); j = n - 1
        lo = bisect_left(pp.hidx, hidx - (HIST_HOURS - 1))
        if j - lo <= 0 or pp.bt[j] != bt:
            return
        reach[STAGES[5]].add(pool)
        if pp.bt[j] - pp.bt[j - 1] > ALIVE_S:
            return
        reach[STAGES[6]].add(pool)
        j24 = bisect_left(pp.bt, pp.bt[j] - 86400, lo, n)
        if float(sum(pp.sol[j24:j + 1])) < VOL_MIN:
            return
        reach[STAGES[7]].add(pool)
        j6 = bisect_right(pp.bt, pp.bt[j] - ALIVE_S, lo, j + 1) - 1
        if j6 < lo:
            return
        r6 = pp.spot[j] / pp.spot[j6] if pp.spot[j6] != 0 else float("nan")
        if not r6 < R6_MAX:
            return
        reach[STAGES[8]].add(pool)
        self.trig_rows[cell] += 1
        self.mints_trig[cell].add(qm)

    def counts(self, first_hour, last_hour, price_sha):
        days = {}
        for cell in sorted(self.reach):
            d, c = cell
            reach = self.reach[cell]
            funnel, prev = [], None
            for s in STAGES:
                k = len(reach.get(s, ()))
                funnel.append({"stage": s, "pools_reaching": k, "pools_dropped": 0 if prev is None else prev - k})
                prev = k
            days.setdefault(d, {})[c] = {"trigger_pools": len(reach.get(STAGES[-1], ())), "trigger_rows": self.trig_rows[cell], "funnel": funnel,
                                         "quote_mints_seen": len(self.mints_seen[cell]), "quote_mints_trigger": len(self.mints_trig[cell])}
        return {"universe": "other-quote", "first_hour": first_hour, "last_hour": last_hour, "stages": list(STAGES), "quote_price_file_sha256": price_sha, "days": days}


def check_keys(o):
    """Refuse any key outside the allowed set (names and counts only)."""
    if set(o) != ALLOWED_TOP:
        raise ValueError(f"output keys {sorted(set(o) ^ ALLOWED_TOP)} not allowed")
    for d, cl in o["days"].items():
        for c, cell in cl.items():
            if c not in CLASSES or set(cell) != ALLOWED_CELL:
                raise ValueError("cell keys not allowed")
            for f in cell["funnel"]:
                if set(f) != ALLOWED_FUNNEL:
                    raise ValueError("funnel keys not allowed")
    return o


def write_counts(path, o):
    check_keys(o)
    with open(path, "w") as fh:
        json.dump(o, fh, indent=1)


def load_prices(path):
    raw = open(path, "rb").read()
    d = json.loads(raw)
    if not isinstance(d, dict) or not all(isinstance(k, str) and _num(v) and v > 0 for k, v in d.items()):
        raise ValueError("quote price file must be {quote_mint: positive lamports-per-raw-unit}")
    return d, hashlib.sha256(raw).hexdigest()


def run(tape, out, h0, h1, price_file):
    prices, sha = load_prices(price_file)
    os.makedirs(out, exist_ok=True)
    c = Counter(prices)
    for h in hours(h0, h1):
        z = subprocess.Popen(["zstd", "-dc", "-q", os.path.join(tape, f"tape-{h}.jsonl.zst")], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
        for line in z.stdout:
            if b'"type":"trade"' not in line or b'"prog":"pumpswap"' not in line:
                continue
            c.row(json.loads(line))
        z.stdout.close(); z.wait()
        open(os.path.join(out, "progress-other-quote.txt"), "a").write(f"{h} done\n")
    write_counts(os.path.join(out, "counts-other-quote.json"), c.counts(h0, h1, sha))
    print("done")


def main(argv):
    if len(argv) != 5 or argv[0] == "":
        sys.exit("usage: floor_variants_funnel.py --universe other-quote --quote-sol-prices FILE TAPE_DIR OUT_DIR FIRST_HOUR LAST_HOUR")
    price_file, tape, out, h0, h1 = argv
    run(tape, out, h0, h1, price_file)
