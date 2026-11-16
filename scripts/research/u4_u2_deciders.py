"""u4_u2_deciders: plug-in deciders for the U4 (diamond-share accumulation) and U2 (cohort-residual relative strength) FORWARD paper sims, run by scripts/research/c1nf_forward_sim.py.
PAPER only; in-sample research, not a gate read. Nothing here says any book makes money.

Spec (controls): /data/mal/hunt-1008/ops/QP-RULING-1116-u4-u2-p81-forward-specs.md (sha256 297657b433180e8e8dd8233ed30d1d98885e8a3575431f0d9f054b2e26fda764), "the ruling" below.
Every constant is the ruling's; none is tuned or added. Section numbers refer to the ruling. Pure logic: no I/O, no price, no fill, no P&L (pricing is the runner's engine).

Interface: decide(hour_rows, state) -> [Entry] (as every runner decider) plus take_sells() -> [SellReq] for the conditional exits (ruling 0.2 item 3). Decisions are taken at fixed UTC marks (ruling
0.2 item 1): at mark M the decider has seen exactly the rows with block time <= M; SD = slot of the last such row; at_row = index of the first row with block time > M. A mark is evaluated only when
a later row arrives (so a mark at the very end of the tape is evaluated when the next hour's first row arrives).
Warm-up (ruling 0.3, 1.4, 2.4): rows only update state; no universe pass, ranking, breadth, fire, entry, placebo draw or sell is computed for a mark < S in code. The one exception is U4's NS / TOP1
snapshot at the marks in [S - 60 min, S), kept for the first post-S deltas (ruling 1.4).
Readings the ruling leaves open (flagged in the PR body): total supply = pump standard 1e9 tokens x 1e6 (ruling 3.1 cites it); the U2 entry print = the last print of the pool at or before slot SD + 19
(its post-trade spot is the stop / take reference; the stop / take is checked on the pool's prints with slot > SD + 19); the post-trade spot uses the engine's own mint_states convention
(buy: q + 0.99 sol, sell: q - 1.01 sol, plus the print's event V; base -/+ token_raw; a non-positive result keeps the pre-trade base).
"""
import bisect
import collections
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import c1nf_forward_sim as S  # noqa: E402

WSOL = S.WSOL
PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"   # pump.fun program (holder-rewards PDA seed owner, ruling 3.1)
TOTAL_SUPPLY_RAW = 1_000_000_000 * 10 ** 6                      # pump standard (ruling 3.1); fallback only (ADD1 A2)
# ADD1 A2: the create row's `token_raw` is the CreateEvent `token_total_supply`: tools/arena_feed.py (create rows, via tools.pump_history_backfill.lifecycle_from_logs) -> the CreateEvent decoder in
# tools/pump_history_backfill.py (`supply_b` read after real_token_reserves, stored as "token_raw": int.from_bytes(supply_b, "little"), line 274; the older fixed-offset layout at line 309 reads
# raw[72:80], the same u64). So the per-mint supply is that field; 1e15 is used only when the row has no positive integer there (counted `supply_fallback`). Mayhem-mode mints are therefore
# NOT excluded from U4 (the ADD1 A2 exclusion applies only if the field were not the supply).
LAT_SLOTS = 19                                                  # deciding entry latency in slots after SD (ruling 0.4)
GAP_PROGS = ("all", "pumpfun", "pumpswap")                      # ruling 1.1

# ---- U4 (ruling 1)
U4_MARK_S = 300                      # every 5 minutes on the UTC clock
U4_AGE_MIN_S, U4_AGE_MAX_S = 3600, 86_400   # pool age [1 h, 24 h)
U4_PRINT_WINDOW_S = 3600             # a PumpSwap print in (M - 60 min, M]
U4_NS_MIN = 0.50
U4_DNS_MIN = 0.05
U4_DELTA_S = 3600                    # dNS / dTOP1 over 60 min
U4_EXIT_S = 4 * 3600                 # exit at decision + 4 h
U4_NS_FALL = 0.80                    # or NS(M) <= 0.80 x NS(entry mark)
U4_TRANSFER_HEAVY = 0.05             # cumulative unexplained sell tokens <= 5% of total supply
U4_ROUTER_MINTS_24H = 50             # a wallet that traded > 50 distinct mints in the 24 h before M is a router / bot
U4_BUNDLE_SLOTS = 1                  # first buy in the create slot or the next slot
# ---- U2 (ruling 2)
U2_MARK_HOURS = (0, 6, 12, 18)
U2_AGE_MIN_S, U2_AGE_MAX_S = 24 * 3600, 168 * 3600
U2_DEPTH_LAMPORTS = 150_000_000_000
U2_FRESH_S = 6 * 3600
U2_R_S = 24 * 3600
U2_BUCKETS_H = ((24, 48), (48, 96), (96, 168))
U2_BUCKET_MIN_POOLS = 5
U2_BREADTH_MIN_POOLS = 10
U2_BREADTH_GATE = 0.40               # trade only if breadth > 0.40
U2_TOP_N = 3
U2_EXIT_S = 12 * 3600
U2_STOP, U2_TAKE = 0.80, 1.40


def _isint(x):
    return isinstance(x, int) and not isinstance(x, bool)


def post_spot(row):
    """post-trade spot (quote + V) / base after the print's own trade, no own impact; the engine's mint_states convention. None when the print has no event V (missing_v) or is unusable."""
    v = row.get("virtual_quote_reserves")
    try:
        q, b, so, tk = float(row["quote_reserve"]), float(row["base_reserve"]), float(row["sol_lamports"]), float(row["token_raw"])
    except (KeyError, TypeError, ValueError):
        return None
    if not _isint(v) or v <= 0:
        return None
    buy = row.get("side") == "buy"
    qp = (q + so * 0.99 if buy else q - so * 1.01) + v
    bp = b - tk if buy else b + tk
    if not bp > 0:
        bp = b
    return qp / bp if bp > 0 else None


def hr_pda_default(mint):
    """the holder-rewards PDA ["holder-rewards", mint] under the pump program (ruling 3.1; V2)"""
    from solders.pubkey import Pubkey
    return str(Pubkey.find_program_address([b"holder-rewards", bytes(Pubkey.from_string(mint))], Pubkey.from_string(PUMP_PROGRAM))[0])


class _Base:
    """shared plumbing: row ingest, registries, mark clock, placebo bookkeeping"""
    name = "base"

    def __init__(self, start_ms, hr_pda_fn=None):
        self.start_s = start_ms // 1000
        self.hr_pda_fn = hr_pda_fn or hr_pda_default
        self.creates = {}                    # mint -> {bt, slot, creator, trader}
        self.pools = {}                      # pool -> {mint, created_bt, quote_ok}
        self.mint_pool = {}                  # mint -> pool (first canonical)
        self._hr = {}                        # mint -> bool
        self.last_row = None                 # (t_recv_ms, slot) of the last row with a slot
        self.next_mark = None
        self.sells = []
        self.c = collections.Counter()
        self.seen_once = set()
        self.state = None
        self.cur_i = 0

    # ---- helpers
    def is_hr(self, mint):
        if mint not in self._hr:
            cr = self.creates.get(mint)
            self._hr[mint] = bool(cr and cr["creator"] and cr["creator"] == self.hr_pda_fn(mint))
        return self._hr[mint]

    def _count(self, k, n=1):
        self.c[k] += n

    def _once(self, k, key):
        """count a per-pool universe refusal once per pool, not once per mark"""
        if (k, key) not in self.seen_once:
            self.seen_once.add((k, key))
            self.c[k] += 1

    def take_sells(self):
        out, self.sells = self.sells, []
        return out

    def _entry(self, pool, mint, bot, mark, exit_s, meta):
        ms, slot = self.last_row if self.last_row else (None, None)
        return S.Entry(pool=pool, mint=mint, at_row=self.cur_i, decision_ms=ms, decision_slot=slot, exit_after_s=exit_s, meta=dict(meta, mark_s=mark), bot=bot, exit_from_decision=True)

    def _sell(self, pool, bot, reason, at_row=None, ms=None, slot=None):
        lms, lslot = self.last_row if self.last_row else (None, None)
        self.sells.append(S.SellReq(pool=pool, at_row=self.cur_i if at_row is None else at_row, decision_ms=lms if ms is None else ms, decision_slot=lslot if slot is None else slot, reason=reason, bot=bot))

    # ---- ingest of non-trade rows (shared)
    def _ingest_meta(self, row):
        typ = row.get("type", "trade")
        bt = S._bt(row)
        if typ == "create" and row.get("mint") and bt is not None and _isint(row.get("slot")):
            self.creates.setdefault(row["mint"], {"bt": bt, "slot": row["slot"], "creator": row.get("creator"), "trader": row.get("trader"),
                                                  "supply": row["token_raw"] if _isint(row.get("token_raw")) and row["token_raw"] > 0 else None})
            self.on_create(row["mint"])
            return True
        if typ == "pool_create" and row.get("pool") and row.get("mint") and bt is not None:
            if row["pool"] not in self.pools:
                self.pools[row["pool"]] = {"mint": row["mint"], "created_bt": bt, "quote_ok": row.get("quote_mint") == WSOL}  # unknown quote = excluded, counted (ruling 0.4)
                self.mint_pool.setdefault(row["mint"], row["pool"])
            return True
        return False

    def on_create(self, mint):
        pass

    # ---- the mark clock
    def first_mark_at_or_after(self, t):
        raise NotImplementedError

    def mark_after(self, m):
        raise NotImplementedError

    def eval_enabled(self, m):
        """False: the mark's evaluation is skipped for good (before the warm-up tail); True: evaluate (only forward)"""
        return m >= self.start_s

    def decide(self, hour_rows, state):
        self.state = state
        for i, row in enumerate(hour_rows):
            self.cur_i = i
            bt = S._bt(row)
            if bt is not None and row.get("type", "trade") != "gap":
                if self.next_mark is None:
                    self.next_mark = self.first_mark_at_or_after(self.snap_from_s(bt))
                while self.next_mark < bt:
                    m = self.next_mark
                    if m >= self.start_s and state.warmup:
                        break  # a forward mark is never evaluated from a warm-up hour: it waits for the first forward row (no evaluation for a mark >= S inside W)
                    self.run_mark(m, state.warmup)
                    self.next_mark = self.mark_after(m)
            self.ingest(row)
            if _isint(row.get("t_recv_ms")) and _isint(row.get("slot")):
                self.last_row = (row["t_recv_ms"], row["slot"])
            self.after_row(row, i)
        state.counts.update(self.c)
        self.c.clear()
        out = self.take_entries()
        return out

    def snap_from_s(self, bt):
        return max(bt, self.start_s - self.snap_window_s)

    snap_window_s = 0

    def run_mark(self, m, warmup):
        raise NotImplementedError

    def ingest(self, row):
        raise NotImplementedError

    def after_row(self, row, i):
        pass

    def take_entries(self):
        out, self.entries = self.entries, []
        return out


# ====================================================================================================================================== U4
class _MintU4:
    __slots__ = ("bal", "sold", "first_buy_slot", "unexplained", "tainted", "heavy", "snaps", "last_swap_bt", "counted_taint", "counted_heavy", "counted_fallback")

    def __init__(self):
        self.bal, self.sold, self.first_buy_slot = {}, set(), {}
        self.unexplained, self.tainted, self.heavy = 0, False, False
        self.snaps = {}          # mark -> (NS, TOP1)
        self.last_swap_bt = None
        self.counted_taint = self.counted_heavy = self.counted_fallback = False


class U4Decider(_Base):
    """ruling 1: tape-derived never-sold share (NS) accumulation"""
    name = "u4"
    snap_window_s = U4_DELTA_S

    def __init__(self, start_ms, hr_pda_fn=None, supply_raw=TOTAL_SUPPLY_RAW):
        super().__init__(start_ms, hr_pda_fn)
        self.supply = supply_raw
        self.mints = {}                      # mint -> _MintU4
        self.wallet_mints = collections.defaultdict(collections.OrderedDict)  # wallet -> OrderedDict mint -> last trade bt (router rule)
        self.entries = []
        self.entered = {"main": set(), "placebo": set()}      # once per mint, ever, per bot
        self.held = {"main": {}, "placebo": {}}               # mint -> {pool, mark, ns0, expiry_s, disabled}
        self.latest_bt = 0

    def first_mark_at_or_after(self, t):
        return -(-t // U4_MARK_S) * U4_MARK_S

    def mark_after(self, m):
        return m + U4_MARK_S

    def on_create(self, mint):
        self.mints.setdefault(mint, _MintU4())

    # ---- state
    def ingest(self, row):
        typ = row.get("type", "trade")
        if typ == "gap":
            if (row.get("data_loss") or str(row.get("kind", "")).startswith("tip_gap")) and (row.get("prog") in GAP_PROGS or row.get("prog") is None):
                hi = row.get("to_slot", row.get("slot"))
                for mint, cr in self.creates.items():  # every mint created at or before the gap's end may have lost a trade
                    ms = self.mints.get(mint)
                    if ms is not None and (hi is None or cr["slot"] <= hi):
                        ms.tainted = True
            return
        if self._ingest_meta(row):
            return
        if typ != "trade" or row.get("prog") not in ("pumpswap", "pumpfun"):
            return
        mint, w, bt = row.get("mint"), row.get("trader"), S._bt(row)
        tok, side, slot = row.get("token_raw"), row.get("side"), row.get("slot")
        if not (mint and w and bt is not None and _isint(tok) and side in ("buy", "sell") and _isint(slot)):
            return
        self.latest_bt = max(self.latest_bt, bt)
        wm = self.wallet_mints[w]  # router rule: distinct mints per wallet in the trailing 24 h
        wm[mint] = bt
        wm.move_to_end(mint)
        while wm and next(iter(wm.values())) < bt - 86_400:
            wm.popitem(last=False)
        ms = self.mints.get(mint)
        if ms is None:
            return  # no create row: no history from the create, never in the universe (counted at the mark)
        if row.get("prog") == "pumpswap":
            ms.last_swap_bt = bt
        if side == "buy":
            ms.bal[w] = ms.bal.get(w, 0) + tok
            ms.first_buy_slot.setdefault(w, slot)
        else:
            b = ms.bal.get(w, 0)
            if tok > b:
                ms.unexplained += tok - b
                ms.bal[w] = 0
            else:
                ms.bal[w] = b - tok
            ms.sold.add(w)
            sup = self.creates[mint]["supply"]
            if sup is None:
                sup = self.supply
                if not ms.counted_fallback:
                    ms.counted_fallback = True
                    self._count("supply_fallback")
            if ms.unexplained > U4_TRANSFER_HEAVY * sup:
                ms.heavy = True

    def _routers(self, m):
        return {w for w, wm in self.wallet_mints.items() if sum(1 for t in wm.values() if t > m - 86_400) > U4_ROUTER_MINTS_24H}

    def ns_top1(self, mint, m, routers):
        """(NS, TOP1) at mark m, or None when no tape-visible holder supply"""
        ms, cr = self.mints[mint], self.creates[mint]
        D = sum(b for b in ms.bal.values() if b > 0)
        if D <= 0:
            return None
        cs = cr["slot"]
        num_ns, top = 0, 0
        for w, b in ms.bal.items():
            if b <= 0 or w in (cr["creator"], cr["trader"]) or w in routers:
                continue
            fb = ms.first_buy_slot.get(w)
            if fb is not None and fb <= cs + U4_BUNDLE_SLOTS:
                continue
            top = max(top, b)
            if w not in ms.sold and fb is not None:
                num_ns += b
        return num_ns / D, top / D

    # ---- the mark
    def run_mark(self, m, warmup):
        if m < self.start_s:
            # U4 snapshot-only marks in [S - 60 min, S): NS / TOP1 for the first post-S deltas; no universe pass, no fire, no draw (ruling 1.4)
            routers = self._routers(m)
            for pool, p in self.pools.items():
                mint = p["mint"]
                ms = self.mints.get(mint)
                if ms is None or ms.tainted or ms.heavy or not p["quote_ok"] or m - p["created_bt"] >= U4_AGE_MAX_S:
                    continue
                r = self.ns_top1(mint, m, routers)
                if r is not None:
                    ms.snaps[m] = r
            self._prune_snaps(m)
            return
        self._count("marks_evaluated")
        routers = self._routers(m)
        self._count("routers_excluded", len(routers))
        universe, fires = [], []
        for pool, p in self.pools.items():
            mint = p["mint"]
            age = m - p["created_bt"]
            if age < 0 or age >= U4_AGE_MAX_S:
                continue
            ms, cr = self.mints.get(mint), self.creates.get(mint)
            if ms is None or cr is None:
                if age >= U4_AGE_MIN_S:
                    self._once("no_create_row", pool)
                continue
            if not p["quote_ok"]:
                if age >= U4_AGE_MIN_S:
                    self._once("quote_unknown", pool)
                continue
            if self.is_hr(mint):
                if age >= U4_AGE_MIN_S:
                    self._once("hr_coin_excluded", pool)
                continue
            if ms.tainted:
                if not ms.counted_taint:
                    ms.counted_taint = True
                    self._count("gap_tainted")
                continue
            if ms.heavy:
                if not ms.counted_heavy:
                    ms.counted_heavy = True
                    self._count("transfer_heavy")
                continue
            r = self.ns_top1(mint, m, routers)
            if r is not None:
                ms.snaps[m] = r
            if age < U4_AGE_MIN_S or ms.last_swap_bt is None or not (m - U4_PRINT_WINDOW_S < ms.last_swap_bt <= m) or r is None:
                continue
            universe.append((pool, mint))
            prev = ms.snaps.get(m - U4_DELTA_S)
            if prev is None:
                self._count("no_prior_snapshot")
                continue
            ns, top1 = r
            if ns >= U4_NS_MIN and ns - prev[0] >= U4_DNS_MIN - 1e-12 and top1 - prev[1] < 0 and mint not in self.entered["main"]:
                fires.append((pool, mint, ns, top1, ns - prev[0], top1 - prev[1]))
        self._count("universe_sum", len(universe))
        fires.sort(key=lambda f: f[1])
        # conditional exits: the share-fall exit of held positions (book and placebo), on the position's own NS
        self._ns_exits(m, routers)
        fire_pools = {f[0] for f in fires}
        for j, (pool, mint, ns, top1, dns, dtop) in enumerate(fires):
            self.entered["main"].add(mint)
            self.held["main"][mint] = {"pool": pool, "mark": m, "ns0": ns, "expiry_s": m + U4_EXIT_S, "disabled": False}
            self.entries.append(self._entry(pool, mint, "main", m, U4_EXIT_S, {"ns": ns, "top1": top1, "dns": dns, "dtop1": dtop}))
            self._count("fires")
        # placebo (ruling 0.7): for each book entry one pool drawn uniformly from the universe pools that did not fire, are not held / entered by the placebo
        drawn = set()
        for j in range(len(fires)):
            cands = sorted(p for p, mt in universe if p not in fire_pools and p not in drawn and mt not in self.entered["placebo"] and mt not in self.held["placebo"])
            if not cands:
                self._count("placebo_no_candidate")
                continue
            pick = random.Random(1 + m + j).choice(cands)
            drawn.add(pick)
            mt = self.pools[pick]["mint"]
            r = self.ns_top1(mt, m, routers)
            self.entered["placebo"].add(mt)
            self.held["placebo"][mt] = {"pool": pick, "mark": m, "ns0": r[0] if r else None, "expiry_s": m + U4_EXIT_S, "disabled": r is None}
            self.entries.append(self._entry(pick, mt, "placebo", m, U4_EXIT_S, {"ns": r[0] if r else None, "draw_index": j}))
            self._count("placebo_entries")
        self._prune_snaps(m)

    def _ns_exits(self, m, routers):
        for bot in ("main", "placebo"):
            for mint, h in list(self.held[bot].items()):
                if m >= h["expiry_s"]:
                    del self.held[bot][mint]
                    continue
                if h["mark"] == m or h["disabled"] or h["ns0"] is None:
                    continue
                ms = self.mints.get(mint)
                if ms is None or ms.tainted:
                    if not h["disabled"]:
                        h["disabled"] = True  # a gap reached the mint after entry: the share exit is off, timed only (ruling 1.3)
                        self._count("ns_exit_disabled")
                    continue
                r = self.ns_top1(mint, m, routers)
                if r is not None and r[0] <= U4_NS_FALL * h["ns0"]:
                    self._sell(h["pool"], bot, "ns_fall")
                    self._count("ns_fall_exits" if bot == "main" else "ns_fall_exits_placebo")
                    del self.held[bot][mint]

    def _prune_snaps(self, m):
        for ms in self.mints.values():
            if ms.snaps:
                for k in [k for k in ms.snaps if k < m - U4_DELTA_S]:
                    del ms.snaps[k]
        # a mint whose pool is older than the universe ceiling (or never graduated and old) is dropped for good
        if self.latest_bt:
            dead = [mt for mt, cr in self.creates.items() if mt in self.mints and cr["bt"] < m - 8 * 86_400]
            for mt in dead:
                del self.mints[mt]


# ====================================================================================================================================== U2
class U2Decider(_Base):
    """ruling 2: cohort-residual relative strength on 1-7 day survivors"""
    name = "u2"
    snap_window_s = 0

    def __init__(self, start_ms, hr_pda_fn=None):
        super().__init__(start_ms, hr_pda_fn)
        self.series = collections.defaultdict(lambda: ([], []))   # pool -> (block times, post-trade spots), prints with a price only
        self.last_print = {}                                       # pool -> {"bt","slot","quote","spot"}
        self.entries = []
        self.pos = {"main": {}, "placebo": {}}                     # pool -> {"land_slot","ref","expiry_s"}
        self.mark_records = []

    def first_mark_at_or_after(self, t):
        d = t - t % 86_400
        for k in range(0, 3):
            for h in U2_MARK_HOURS:
                if d + k * 86_400 + h * 3600 >= t:
                    return d + k * 86_400 + h * 3600

    def mark_after(self, m):
        return self.first_mark_at_or_after(m + 1)

    def ingest(self, row):
        typ = row.get("type", "trade")
        if typ == "gap" or self._ingest_meta(row):
            return
        if typ != "trade" or row.get("prog") != "pumpswap":
            return
        pool, bt, slot = row.get("pool"), S._bt(row), row.get("slot")
        if not (pool and bt is not None and _isint(slot)):
            return
        spot = post_spot(row)
        lp = self.last_print.get(pool)
        if spot is None:
            self._count("missing_v")
        else:
            ts, ps = self.series[pool]
            ts.append(bt)
            ps.append(spot)
        try:
            quote = int(row["quote_reserve"])
        except (KeyError, TypeError, ValueError):
            quote = None
        self.last_print[pool] = {"bt": bt, "slot": slot, "quote": quote, "spot": spot if spot is not None else (lp or {}).get("spot")}
        self._check_exit(pool, row, spot, slot, lp)

    def _check_exit(self, pool, row, spot, slot, prev):
        if spot is None:
            return
        for bot in ("main", "placebo"):
            p = self.pos[bot].get(pool)
            if p is None:
                continue
            bt = S._bt(row)
            if bt is not None and bt >= p["expiry_s"]:
                del self.pos[bot][pool]  # past the timed exit: nothing is requested any more
                continue
            if p["ref"] is None:
                if slot <= p["land_slot"]:
                    continue
                if prev is None or prev.get("spot") is None:
                    continue
                p["ref"] = prev["spot"]  # the entry print: the pool's last print at or before the landing slot SD + 19
            if spot <= U2_STOP * p["ref"]:
                self._queue_sell(pool, bot, row, "stop")
            elif spot >= U2_TAKE * p["ref"]:
                self._queue_sell(pool, bot, row, "take")

    def _queue_sell(self, pool, bot, row, kind):
        # the sell is handed over just after this print (at_row = this row + 1); the runner prices it at the exit lag after the print's slot
        self.sells.append(S.SellReq(pool=pool, at_row=self.cur_i + 1, decision_ms=row["t_recv_ms"] if _isint(row.get("t_recv_ms")) else self.last_row[0], decision_slot=row["slot"], reason=kind, bot=bot))
        self._count(("%s_exits" % kind) + ("" if bot == "main" else "_placebo"))
        del self.pos[bot][pool]

    def after_row(self, row, i):
        pass

    def price_at(self, pool, t):
        ts, ps = self.series.get(pool, ([], []))
        j = bisect.bisect_right(ts, t) - 1
        return ps[j] if j >= 0 else None

    def run_mark(self, m, warmup):
        if m < self.start_s:
            return  # nothing is ranked or drawn before S (ruling 2.4)
        self._count("marks_evaluated")
        for pool, (ts, ps) in self.series.items():  # keep one anchor at or before M - 24 h and everything after (memory only; no decision reads older prints)
            j = bisect.bisect_right(ts, m - U2_R_S - 3600) - 1
            if j > 0:
                del ts[:j]
                del ps[:j]
        for bot in ("main", "placebo"):
            for pool, p in list(self.pos[bot].items()):
                if m >= p["expiry_s"]:
                    del self.pos[bot][pool]
        uni = []  # (pool, mint, age_s, R, spot)
        for pool, p in self.pools.items():
            mint, age = p["mint"], m - p["created_bt"]
            if not (U2_AGE_MIN_S <= age < U2_AGE_MAX_S):
                continue
            if mint not in self.creates:
                self._once("no_create_row", pool)
                continue
            if not p["quote_ok"]:
                self._once("quote_unknown", pool)
                continue
            if self.is_hr(mint):
                self._once("hr_coin_excluded", pool)
                continue
            lp = self.last_print.get(pool)
            if lp is None or lp["quote"] is None or lp["quote"] < U2_DEPTH_LAMPORTS or lp["bt"] <= m - U2_FRESH_S or lp["bt"] > m or lp["spot"] is None:
                continue
            p0 = self.price_at(pool, m - U2_R_S)
            p1 = self.price_at(pool, m)
            if p0 is None or p1 is None or p0 <= 0:
                continue
            uni.append((pool, mint, age, p1 / p0 - 1.0))
        rec = {"mark_s": m, "universe": len(uni)}
        if len(uni) < U2_BREADTH_MIN_POOLS:
            self._count("universe_small")
            rec.update(breadth=None, gate=False, picks=0, buckets=[])
            self.mark_records.append(rec)
            return
        breadth = sum(1 for u in uni if u[3] > 0) / len(uni)
        buckets, med = [], {}
        for k, (lo, hi) in enumerate(U2_BUCKETS_H):
            rs = [u[3] for u in uni if lo * 3600 <= u[2] < hi * 3600]
            buckets.append(len(rs))
            if len(rs) < U2_BUCKET_MIN_POOLS:
                if rs:
                    self._count("bucket_skipped")
            else:
                med[k] = statistics.median(rs)
        gate = breadth > U2_BREADTH_GATE
        rec.update(breadth=round(breadth, 3), gate=gate, buckets=buckets)
        picks = []
        if gate:
            res = []
            for pool, mint, age, r in uni:
                k = next(i for i, (lo, hi) in enumerate(U2_BUCKETS_H) if lo * 3600 <= age < hi * 3600)
                if k in med and r - med[k] > 0 and pool not in self.pos["main"]:
                    res.append((-(r - med[k]), pool, mint))
            res.sort()
            for e, pool, mint in res[:U2_TOP_N]:
                picks.append((pool, mint, -e))
        for pool, mint, e in picks:
            self._open("main", pool, m)
            self.entries.append(self._entry(pool, mint, "main", m, U2_EXIT_S, {"residual": e, "breadth": round(breadth, 3)}))
            self._count("picks")
        if picks:  # placebo: the same number of pools, uniformly without replacement from the same gated universe, excluding the picks and what the placebo holds
            pickset = {p[0] for p in picks}
            cands = sorted(u[0] for u in uni if u[0] not in pickset and u[0] not in self.pos["placebo"])
            chosen = random.Random(1 + m).sample(cands, min(len(picks), len(cands)))
            for pool in chosen:
                self._open("placebo", pool, m)
                self.entries.append(self._entry(pool, self.pools[pool]["mint"], "placebo", m, U2_EXIT_S, {"draw": True}))
                self._count("placebo_entries")
        rec["picks"] = len(picks)
        self.mark_records.append(rec)

    def _open(self, bot, pool, m):
        ms, slot = self.last_row if self.last_row else (None, None)
        self.pos[bot][pool] = {"land_slot": (slot or 0) + LAT_SLOTS, "ref": None, "expiry_s": m + U2_EXIT_S}

    def drain_marks(self):
        out, self.mark_records = self.mark_records, []
        return out


def make_decider(m, hour_to_ms=None, hr_pda_fn=None):
    start_ms = hour_to_ms(m["start_hour"])
    cls = {"u4": U4Decider, "u2": U2Decider}[m["decider"]]
    return cls(start_ms, hr_pda_fn=hr_pda_fn)
