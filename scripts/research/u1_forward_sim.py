#!/usr/bin/env python3
"""u1_forward_sim: decider plug-ins for the U1 forward paper book (creator-cluster outcome reputation, first-funder linked), QP-RULING-1116-u1-forward-spec. PAPER only; in-sample
research, not a gate read. Nothing here says any book makes money.

Plugs into scripts/research/c1nf_forward_sim.py (PR #1415) through its decide(hour_rows, state) -> [Entry] interface; keeper-only tape input, warm-up (decisions off), pricing legs and
book output are the runner's. Pieces
  FunderTable   Table A / B rows (u1_funder_fetch) as lookups; only `resolved` rows link launchers
  Registry      fed row by row: creates (launcher L(m) = trader, else creator), pool_creates, per-graduate P0 / last print <= pc + 24 h, data_loss gaps
  U1Decider     mode "book" (entry gate), "low_rep" (shown-beside line), "placebo" (plan from build_placebo_plan); decision instant D = pool_create + 360 s, stamped at the crossing row
  scoring       alive(g) = P24 >= 0.5 x P0, scored at D only when pool_create(g) + 86,400 s <= D (the no-look-ahead rule is asserted in code)
  build_placebo_plan / first_stop_print   pure helpers (random pick draw; the -25% stop rule)
Every constant below is the ruling's.
"""
import collections, hashlib, random
from pathlib import Path

import c1nf_forward_sim as S

WSOL = S.WSOL
GATE_MIN_HIST = 3
GATE_MIN_SHARE = 0.50
ALIVE_FRAC = 0.50
SCORE_DELAY_S = 86_400
HIST_WINDOW_S = 336 * 3600
DECISION_DELAY_S = 360
HOLD_S = 21_600
STOP_FRAC = 0.75
STOP_SELL_LAG_SLOTS = 19
MAX_OPEN = 10
HUB_FUNDER_CHILDREN = 50
HUB_LAUNCHER_GRADS = 50
PLACEBO_WINDOW_S = 1800
FEE_NOTE = "UR 0.4 pricing, fees, rent and fail legs are the runner's engine; unchanged"
MODES = ("book", "low_rep", "placebo")
# H4: program or protocol accounts (fixed list in code). Pump fee / global accounts are NOT listed (not verified by the builder): the manager adds them with a source before S.
H4_ACCOUNTS = frozenset({
    "11111111111111111111111111111111",                      # System Program
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",           # pump program
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",           # PumpSwap program
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",           # SPL Token program
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",          # Associated Token program
    "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5", "HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe", "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY",
    "ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49", "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh", "ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt",
    "DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL", "3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT",   # Jito tip accounts
})


# ------------------------------------------------------------------------------------------------------------------------------ funder table
class FunderTable:
    def __init__(self):
        self._of = {}                                   # launcher -> (funder, funding_slot)
        self._children = collections.defaultdict(list)  # funder -> [(launcher, funding_slot)]
        self._hub = {}                                  # funder -> True / False / "error"

    def add_row(self, row):
        """Table A row; only status `resolved` links anything"""
        if row.get("status") == "resolved" and row["launcher"] not in self._of:
            self._of[row["launcher"]] = (row["funder"], row["funding_slot"])
            self._children[row["funder"]].append((row["launcher"], row["funding_slot"]))

    def set_hub(self, funder, v):
        self._hub[funder] = v

    @classmethod
    def from_rows(cls, table_a, table_b=()):
        t = cls()
        for r in table_a:
            t.add_row(r)
        for r in table_b:
            t.set_hub(r["funder"], r["hub_activity"])
        return t

    def funder_of(self, launcher):
        return self._of.get(launcher)

    def known(self, launcher):
        return launcher in self._of

    def children_upto(self, funder, slot):
        return [l for l, s in self._children.get(funder, ()) if s <= slot]

    def hub_activity(self, funder):
        return self._hub.get(funder) is True


# ------------------------------------------------------------------------------------------------------------------------------ registry
def spot_after(row):
    """spot after the print's own trade, no own impact: (quote + V) / base. PumpSwap tape reserves are PRE-trade (memory: reserve convention), so the trade's own delta is applied here.
    -> float or None. V = event virtual_quote_reserves."""
    v, q, b = row.get("virtual_quote_reserves"), row.get("quote_reserve"), row.get("base_reserve")
    sol, tok = row.get("sol_lamports"), row.get("token_raw")
    if not all(S._isint(x) for x in (v, q, b, sol, tok)):
        return None
    if row.get("side") == "buy":
        q2, b2 = q + sol, b - tok
    elif row.get("side") == "sell":
        q2, b2 = q - sol, b + tok
    else:
        return None
    return (q2 + v) / b2 if b2 > 0 and q2 + v > 0 else None


class Grad:
    __slots__ = ("mint", "pool", "launcher", "pc_s", "pc_ms", "quote_ok", "p0", "p24", "create_slot")

    def __init__(self, mint, pool, launcher, pc_s, pc_ms, quote_ok, create_slot):
        self.mint, self.pool, self.launcher, self.pc_s, self.pc_ms, self.quote_ok, self.create_slot = mint, pool, launcher, pc_s, pc_ms, quote_ok, create_slot
        self.p0 = None   # (time_s, spot) first print at or after pool_create
        self.p24 = None  # (time_s, spot) last print at or before pool_create + 86,400 s


class Registry:
    def __init__(self):
        self.creates = {}                 # mint -> dict(launcher, slot, from_creator, trader_ne_creator)
        self.grads = {}                   # mint -> Grad
        self.pool_mint = {}               # pool -> mint
        self.by_launcher = collections.defaultdict(list)  # launcher -> [mint] graduates (with a create row)
        self.gaps = []                    # [(t_s)] data_loss gap instants (prog all | pumpswap)
        self.c = collections.Counter()

    def feed(self, row):
        """one hv7 row (any kind). Order = stream order."""
        typ = row.get("type", "trade")
        if typ == "gap":
            if (row.get("data_loss") or str(row.get("kind", "")).startswith("tip_gap")) and row.get("prog") in ("all", "pumpswap"):
                t = S._bt(row)
                if t is not None:
                    self.gaps.append(t)
            return None
        if typ == "create":
            m = row.get("mint")
            if m and m not in self.creates and S._isint(row.get("slot")):
                trader, creator = row.get("trader"), row.get("creator")
                launcher = trader or creator
                if launcher:
                    self.creates[m] = {"launcher": launcher, "slot": row["slot"], "from_creator": not trader, "trader_ne_creator": bool(trader and creator and trader != creator), "row": row}
                    self.c["launcher_from_creator"] += (not trader)
                    self.c["trader_ne_creator"] += bool(trader and creator and trader != creator)
            return None
        if typ == "pool_create":
            m, bt = row.get("mint"), S._bt(row)
            if not m or bt is None or m in self.grads or not row.get("pool"):
                return None
            cr = self.creates.get(m)
            g = Grad(m, row["pool"], cr["launcher"] if cr else None, bt, row.get("t_recv_ms") if S._isint(row.get("t_recv_ms")) else bt * 1000,
                     (row.get("quote_mint") or WSOL) == WSOL, cr["slot"] if cr else None)
            self.grads[m] = g
            self.pool_mint[g.pool] = m
            if cr:
                self.by_launcher[g.launcher].append(m)
            return g
        if typ == "trade" and row.get("prog") == "pumpswap":
            m = self.pool_mint.get(row.get("pool"))
            if m is None:
                return None
            g, bt = self.grads[m], S._bt(row)
            sp = spot_after(row)
            if bt is None or sp is None or bt < g.pc_s:
                return None
            if g.p0 is None:
                g.p0 = (bt, sp)
            if bt <= g.pc_s + SCORE_DELAY_S:
                g.p24 = (bt, sp)
        return None

    def scored(self, g, d_s, w_start_s):
        """-> True (alive) / False (not alive) / None (unscored). Uses only rows with block time <= pool_create + 86,400 s <= D."""
        if g.pc_s + SCORE_DELAY_S > d_s:
            return None
        if g.pc_s < max(w_start_s, d_s - HIST_WINDOW_S):
            return None
        if g.mint not in self.creates or not g.quote_ok or g.p0 is None or g.p24 is None:
            return None
        t0, t24 = g.p0[0], g.p24[0]
        end = g.pc_s + SCORE_DELAY_S
        if t24 > end or t0 > end:
            return None
        for t in self.gaps:
            if g.pc_s <= t <= t0 or t24 < t <= end:
                return None
        return g.p24[1] >= ALIVE_FRAC * g.p0[1]


def cluster_id(key):
    return hashlib.sha256(str(key).encode()).hexdigest()[:12]


# ------------------------------------------------------------------------------------------------------------------------------ pure helpers
def first_stop_print(prints, entry_spot, landing_slot):
    """hard stop (ruling 4): the first PumpSwap print AFTER the entry landing whose spot (no own impact) <= 0.75 x entry spot. prints: iterable of (slot, spot) in stream order.
    -> (decide_slot, price_slot = decide_slot + 19) or None. Independent of stake."""
    for slot, sp in prints:
        if slot > landing_slot and sp <= STOP_FRAC * entry_spot:
            return slot, slot + STOP_SELL_LAG_SLOTS
    return None


def build_placebo_plan(universe, entries, gate_passed_mints, seed_base=1):
    """random-pick comparison (ruling 5). universe: [{pool, mint, pc_s}] graduates of the universe; entries: [{pool, mint, pc_s}] the BOOK's entries; gate_passed_mints: every mint
    that passed the entry gate (entered or not). For each entry in pool_create order: draw one pool uniformly (random.Random(1 + P epoch s)) from universe graduates with
    pool_create in [P - 1800, P + 1800], excluding the entry's mint, gate-passed mints and pools the placebo already holds (drawn, still inside their 6 h hold). Graduation and gate
    membership only: no outcome is read. -> (plan {pool: {"for": entry pool, "pc_s": ...}}, counters)"""
    plan, c = {}, collections.Counter()
    held = {}  # pool -> hold end (pc_s + 360 + 21600)
    uni = sorted(universe, key=lambda u: (u["pc_s"], u["pool"]))
    for e in sorted(entries, key=lambda x: (x["pc_s"], x["pool"])):
        P = e["pc_s"]
        cands = [u for u in uni if P - PLACEBO_WINDOW_S <= u["pc_s"] <= P + PLACEBO_WINDOW_S and u["mint"] != e["mint"] and u["mint"] not in gate_passed_mints
                 and held.get(u["pool"], -1) < u["pc_s"] + DECISION_DELAY_S and u["pool"] not in plan]
        if not cands:
            c["placebo_no_candidate"] += 1
            continue
        pick = random.Random(seed_base + int(P)).choice(cands)
        plan[pick["pool"]] = {"for": e["pool"], "pc_s": pick["pc_s"]}
        held[pick["pool"]] = pick["pc_s"] + DECISION_DELAY_S + HOLD_S
        c["placebo_drawn"] += 1
    return plan, c


# ------------------------------------------------------------------------------------------------------------------------------ decider
class U1Decider:
    """decide(hour_rows, state) -> [Entry] (the c1nf_forward_sim interface). Keeper-only input; every decision uses only rows fed up to the crossing row of D.
    start_ms = S; w_start_s = W start (S - 336 h, never before 2026-11-03T03Z); table = FunderTable; labels = H3 label set pinned before S (empty -> H3 empty); fetch_fn(launcher,
    first_create_slot) -> Table A row, optional forward fetch when a launcher is untraced; open_fn() -> open positions of the book now; hr_fn(create_row) -> holder-rewards coin
    (injected: the UR 0.4 rule was not read by the builder; default off)."""

    def __init__(self, start_ms, table, *, mode="book", labels=frozenset(), plan=None, fetch_fn=None, open_fn=None, hr_fn=None, max_open=MAX_OPEN, lat_s=S.LAT_DECIDING_S):
        if mode not in MODES:
            raise ValueError("mode must be one of %s" % (MODES,))
        if mode == "placebo" and plan is None:
            raise ValueError("placebo mode needs a plan (build_placebo_plan)")
        self.start_ms, self.table, self.mode, self.labels = int(start_ms), table, mode, frozenset(labels)
        self.plan, self.fetch_fn, self.open_fn, self.hr_fn, self.max_open, self.lat_s = plan, fetch_fn, open_fn, hr_fn or (lambda r: False), max_open, lat_s
        self.w_start_s = max(start_ms // 1000 - HIST_WINDOW_S, S.parse_hour(S.WARMUP_START_MIN) // 1000)
        self.reg = Registry()
        self.pending = []          # [(D_ms, mint)]
        self.last_slot = None
        self.entered = set()
        self.gate_log = []         # [{pool, mint, pc_s, entered}] every gate pass (book mode), for the placebo plan
        self.universe_log = []     # [{pool, mint, pc_s}] graduates that entered the universe
        self.c = collections.Counter()
        self.counters_prev = collections.Counter()

    # -- funder side
    def _ensure_traced(self, g):
        if self.fetch_fn and g.launcher and g.create_slot is not None and not self.table.known(g.launcher):
            row = self.fetch_fn(g.launcher, g.create_slot)
            if row:
                self.table.add_row(row)
                if row.get("funder") is not None and hasattr(self.fetch_fn, "hub_of"):
                    h = self.fetch_fn.hub_of(row["funder"])
                    if h is not None:
                        self.table.set_hub(row["funder"], h)

    def hub_reason(self, funder, sd_slot):
        """-> "H1".."H4" or None. H1 hub_activity; H2 > 50 distinct traced launchers funded at slot <= SD; H3 pinned label list; H4 program / protocol account."""
        if funder in H4_ACCOUNTS:
            return "H4"
        if funder in self.labels:
            return "H3"
        if self.table.hub_activity(funder):
            return "H1"
        if len(set(self.table.children_upto(funder, sd_slot))) > HUB_FUNDER_CHILDREN:
            return "H2"
        return None

    def cluster(self, launcher, sd_slot):
        """-> (set of launchers, kind "funder" | "own", hub reason or None). One hop, no transitive closure."""
        own = self.table.funder_of(launcher)
        if own is None or own[1] > sd_slot:
            return {launcher}, "own", None
        f = own[0]
        hub = self.hub_reason(f, sd_slot)
        if hub:
            return {launcher}, "own", hub
        return set(self.table.children_upto(f, sd_slot)) | {launcher}, "funder", None

    def history(self, mint, launcher, d_s, sd_slot):
        """-> (n_scored, n_alive, n_unscored, cluster set, kind, hub reason). Only graduates scored at D."""
        members, kind, hub = self.cluster(launcher, sd_slot)
        n = alive = unsc = 0
        for l in members:
            for gm in self.reg.by_launcher.get(l, ()):
                if gm == mint:
                    continue
                r = self.reg.scored(self.reg.grads[gm], d_s, self.w_start_s)
                if r is None:
                    unsc += 1
                else:
                    n += 1
                    alive += bool(r)
        return n, alive, unsc, members, kind, hub

    # -- one decision
    def _evaluate(self, mint, d_ms, sd_slot, out, row, i):
        g, cr = self.reg.grads[mint], self.reg.creates.get(mint)
        c = self.c
        if cr is None:
            c["no_create_row"] += 1
            return
        if not g.quote_ok:
            c["non_wsol"] += 1
            return
        if self.hr_fn(cr["row"]):
            c["hr_coin_excluded"] += 1
            return
        d_s = d_ms // 1000
        if len([x for x in self.reg.by_launcher[g.launcher] if self.reg.grads[x].pc_s <= d_s]) > HUB_LAUNCHER_GRADS:
            c["hub_launcher"] += 1
            return
        c["universe_graduates"] += 1
        self.universe_log.append({"pool": g.pool, "mint": mint, "pc_s": g.pc_s})
        n, alive, unsc, members, kind, hub = self.history(mint, g.launcher, d_s, sd_slot)
        if hub:
            c["hub_" + hub] += 1
        c["scored_priors"] += n
        c["unscored_priors"] += unsc
        share = alive / n if n else 0.0
        meta = {"mode": self.mode, "pc_s": g.pc_s, "n_hist": n, "n_alive": alive, "alive_share": share, "hist_kind": kind, "cluster": cluster_id(self.table.funder_of(g.launcher)[0] if kind == "funder" else g.launcher), "SD_slot": sd_slot}
        if self.mode == "placebo":
            if g.pool not in self.plan:
                return
            meta["placebo_for"] = cluster_id(self.plan[g.pool]["for"])
        else:
            passes = n >= GATE_MIN_HIST and share >= GATE_MIN_SHARE
            if self.mode == "low_rep":
                passes = n >= GATE_MIN_HIST and share < GATE_MIN_SHARE
            if not passes:
                c["gate_fail" if self.mode == "book" else "low_rep_no"] += 1
                return
            c["gate_pass"] += 1
            c["mean_hist_sum"] += n
            c["entry_hist_funder" if kind == "funder" else "entry_hist_own"] += 1
        if mint in self.entered:
            return
        opened = (self.open_fn() if self.open_fn else 0) + len(out)
        if opened >= self.max_open:
            c["cap_open"] += 1  # a refusal before pricing, not an exclusion of a priced entry
            if self.mode == "book":
                self.gate_log.append({"pool": g.pool, "mint": mint, "pc_s": g.pc_s, "entered": False})
            return
        self.entered.add(mint)
        c["entries"] += 1
        if self.mode == "book":
            self.gate_log.append({"pool": g.pool, "mint": mint, "pc_s": g.pc_s, "entered": True})
        out.append(S.Entry(pool=g.pool, mint=mint, at_row=i, decision_ms=int(row["t_recv_ms"]), decision_slot=int(sd_slot), exit_after_s=HOLD_S - self.lat_s, meta=meta))

    def decide(self, hour_rows, state):
        out = []
        for i, row in enumerate(hour_rows):
            t = row.get("t_recv_ms")
            if S._isint(t):
                # decisions whose D has been crossed by THIS row: the decider has seen only rows before it
                while self.pending and self.pending[0][0] <= t:
                    d_ms, mint = self.pending.pop(0)
                    if not state.warmup and d_ms >= self.start_ms and self.last_slot is not None:
                        self._evaluate(mint, d_ms, self.last_slot, out, row, i)
                    else:
                        self.c["decision_before_S"] += 1
            g = self.reg.feed(row)
            if g is not None:
                self.c["graduates_seen"] += 1
                if not state.warmup and g.pc_ms + DECISION_DELAY_S * 1000 >= self.start_ms:
                    self._ensure_traced(g)
                    self.pending.append((g.pc_ms + DECISION_DELAY_S * 1000, g.mint))
                    self.pending.sort()
            if S._isint(row.get("slot")) and S._isint(t):
                self.last_slot = row["slot"]
        if state.warmup and out:
            raise AssertionError("U1 decider produced an entry in a warm-up hour")
        cnt = collections.Counter(self.c)
        cnt.update(self.reg.c)
        state.counts.update({k: v - self.counters_prev.get(k, 0) for k, v in cnt.items() if v != self.counters_prev.get(k, 0)})
        self.counters_prev = cnt
        return out


def build_decider(m, table=None, **kw):
    """runner glue (manifest key decider 'u1' | 'u1_lowrep' | 'u1_placebo'). Tables and the placebo plan come from files named in the manifest `u1` object."""
    import json
    u1 = m.get("u1") or {}
    if table is None:
        a = [json.loads(x) for x in Path(u1["table_a"]).read_text().splitlines() if x.strip()]
        b = [json.loads(x) for x in Path(u1["table_b"]).read_text().splitlines() if x.strip()] if u1.get("table_b") else []
        table = FunderTable.from_rows(a, b)
    labels = frozenset(Path(u1["labels"]).read_text().split()) if u1.get("labels") else frozenset()
    mode = {"u1": "book", "u1_lowrep": "low_rep", "u1_placebo": "placebo"}[m["decider"]]
    plan = json.loads(Path(u1["placebo_plan"]).read_text()) if mode == "placebo" else None
    return U1Decider(S.parse_hour(m["start_hour"]), table, mode=mode, labels=labels, plan=plan, lat_s=float(m.get("pricing_lat_entry_s", S.LAT_DECIDING_S)), **kw)
