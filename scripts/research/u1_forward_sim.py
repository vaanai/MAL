#!/usr/bin/env python3
"""u1_forward_sim: decider plug-ins for the U1 forward paper book (creator-cluster outcome reputation, first-funder linked), QP-RULING-1116-u1-forward-spec. PAPER only; in-sample
research, not a gate read. Nothing here says any book makes money.

Plugs into scripts/research/c1nf_forward_sim.py (PR #1415) through its decide(hour_rows, state) -> [Entry] interface; keeper-only tape input, warm-up (decisions off), pricing legs and
book output are the runner's. Pieces
  FunderTable   Table A / B rows (u1_funder_fetch) as lookups; only `resolved` rows link launchers
  Registry      fed row by row: creates (launcher L(m) = trader, else creator), pool_creates, per-graduate P0 / last print <= pc + 24 h, data_loss gaps
  U1Decider     mode "book" (entry gate), "low_rep" (shown-beside line), "placebo" (plan from build_placebo_plan); decision instant D = pool_create + 360 s, stamped at the crossing row
  scoring       alive(g) = P24 >= 0.5 x P0, scored at D only when pool_create(g) + 86,400 s <= D (the no-look-ahead rule is asserted in code)
  build_placebo_plan   pure helper (random pick draw); the -25% stop is requested by the decider as a SellReq (price-exit mechanism of #1422, ruling 4b)
Every constant below is the ruling's.
"""
import collections, hashlib, json, os, random, re, time
from datetime import datetime, timezone
from pathlib import Path

import c1nf_forward_sim as S
import u1_funder_fetch as F
from u4_u2_deciders import post_spot, hr_pda_default  # one definition across books (QP ADD1 item 7); no local copy

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
PLACEBO_MIN_N = 5
CAP_OPEN_NOT_COMPUTED = "cap_open_stale not computed; cap_open counts the book's own entries for a full 6 h (stops ignored), so it may refuse entries an exact count would have taken."
STALE_DISCLOSURE = "cap_open uses an hour-start open count; %d entries were refused that an exact count would have taken"
PLACEBO_RIPE_S = 2160   # QP-RULING-1117-u1-start Q7: an entry is in a plan only if the hour containing P + 2,160 s is closed (D of a candidate with pool_create <= P + 1,800 s is <= P + 2,160 s)
PLACEBO_MAX_EXCLUDED_SHARE = 0.20
PLACEBO_DISCLOSURE = ("Placebo built once at the look by a deterministic plan pass (plan sha256 {sha}, built {built}, through hour {hour}) from the book's graduation and gate lists; "
                      "priced afterwards by a separate full-replay run of the same engine; it was not run alongside the book.")
FWD_DISCLOSURE = ("Forward funder links are fetched inside the book when it processes the hour holding a graduate's pool_create (after the hour closes, not within 360 s); "
                  "the funder and hub test are past-only, but status `deep` is judged on history up to the fetch.")
FETCH_ONCE_DISCLOSURE = "One funder fetch per launcher from S; fetch_error and fetch_capped launchers stay own-history-only for the book's life."
RATE_DISCLOSURE = "Forward funder fetch runs unlocked at <= 2 rps beside the walkers' four 10-rps slots."
LOWREP_DISCLOSURE = ("The low-reputation line makes no RPC call: it reads the book's forward funder journal read-only and decides an hour only after the book has stored it; "
                     "its forward links are the book's.")
PLACEBO_NO_RPC_DISCLOSURE = ("The placebo run makes no RPC call and uses the backfill funder tables only; forward launchers are unlinked in its view, which changes no placebo entry or exit "
                             "(the draw and the exits read no funder link).")
FWD_JOURNAL = "u1-fwd-journal.jsonl"
FWD_CREDITS = "u1-credits-by-day.json"
FWD_BY_HOUR = "u1-fwd-by-hour.jsonl"
H4_DISCLOSURE = "H4 lacks pump fee/global accounts; H2 (> 50 children) removes any that fund many launchers"
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
        self._seen = set()                              # launchers with a Table A row of ANY status (one fetch attempt per launcher, ever: QP Q3)

    def add_row(self, row):
        """Table A row; only status `resolved` links anything, every status marks the launcher seen"""
        self._seen.add(row["launcher"])
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
        """seen over all six statuses (a fetch_error / capped / deep launcher is never fetched again)"""
        return launcher in self._seen

    def mark_seen(self, launcher):
        self._seen.add(launcher)

    def children_upto(self, funder, slot):
        return [l for l, s in self._children.get(funder, ()) if s <= slot]

    def hub_activity(self, funder):
        return self._hub.get(funder) is True


# ------------------------------------------------------------------------------------------------------------------------------ registry
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
        self.min_slot = {}                # launcher -> minimum create slot over ALL of its create rows fed so far (launcher list, B1)
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
                    if launcher not in self.min_slot or row["slot"] < self.min_slot[launcher]:
                        self.min_slot[launcher] = row["slot"]
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
            sp = post_spot(row)
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


# ------------------------------------------------------------------------------------------------------------------------------ launcher list (B1)
LAUNCHER_LIST = "u1-launchers.jsonl"
LAUNCHER_LIST_BY_HOUR = "u1-launchers-by-hour.jsonl"
PLAN_INPUT = "u1-plan-input.jsonl"
# QP-RULING-1117-u1-start Q1: first_create_slot(L) = the minimum create slot over ALL of L's create rows seen so far (graduated or not); the ONE definition, used by the launcher list and by the
# forward fetch. (No other rule exists: the graduate's own create slot is not acceptable.)


def graduate_first_slot(reg, launcher):
    slots = [reg.grads[m].create_slot for m in reg.by_launcher.get(launcher, ()) if reg.grads[m].create_slot is not None]
    return min(slots) if slots else None


def first_create_slot(reg, launcher):
    """the ONE definition of the launcher list's and the forward fetch's first_create_slot (Q1)"""
    return reg.min_slot.get(launcher)


def launcher_list_rows(reg):
    """one row per launcher with >= 1 graduate (with a create row) among the rows fed so far, sorted by launcher -> ([{launcher, first_create_slot}], n_below)
    where n_below counts listed launchers whose chosen slot is below their earliest graduate's create slot (u1_first_slot_below_graduate_create)"""
    rows, below = [], 0
    for l in sorted(reg.by_launcher):
        if not reg.by_launcher[l]:
            continue
        fs = first_create_slot(reg, l)
        if fs is None:
            continue
        rows.append({"launcher": l, "first_create_slot": fs})
        gs = graduate_first_slot(reg, l)
        below += gs is not None and fs < gs
    return rows, below


def _atomic_write_0440(path, data):
    _atomic_write(path, data, 0o440)


def _atomic_write(path, data, mode):
    """temp + fsync + rename + directory fsync"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    if tmp.exists():
        tmp.unlink()
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(tmp, mode)
    os.replace(tmp, path)
    dfd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def write_launcher_list(reg, path):
    """atomic rewrite (temp + rename, 0440) of the launcher list -> (n_launchers, sha256 of the file bytes, n_first_slot_from_graduate_only)"""
    rows, below = launcher_list_rows(reg)
    data = "".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in rows).encode()
    _atomic_write_0440(path, data)
    return len(rows), hashlib.sha256(data).hexdigest(), below


# ------------------------------------------------------------------------------------------------------------------------------ pure helpers
def build_placebo_plan(universe, entries, gate_passed_mints, seed_base=1):
    """random-pick comparison (ruling 5). universe: [{pool, mint, pc_s}] graduates of the universe; entries: [{pool, mint, pc_s}] the BOOK's entries; gate_passed_mints: every mint
    that passed the entry gate (entered or not). For each entry in pool_create order: draw one pool uniformly (random.Random(1 + P epoch s)) from universe graduates with
    pool_create in [P - 1800, P + 1800], excluding the entry's mint, gate-passed mints (cap_open refusals included) and any pool already in the plan (one placebo entry per pool, ever: QP ADD1 3). Graduation and gate
    membership only: no outcome is read. -> (plan {pool: {"for": entry pool, "pc_s": ...}}, counters)"""
    plan, c = {}, collections.Counter()
    uni = sorted(universe, key=lambda u: (u["pc_s"], u["pool"]))
    for e in sorted(entries, key=lambda x: (x["pc_s"], x["pool"])):
        P = e["pc_s"]
        cands = [u for u in uni if P - PLACEBO_WINDOW_S <= u["pc_s"] <= P + PLACEBO_WINDOW_S and u["mint"] != e["mint"] and u["mint"] not in gate_passed_mints
                 and u["pool"] not in plan]
        if not cands:
            c["placebo_no_candidate"] += 1
            continue
        pick = random.Random(seed_base + int(P)).choice(cands)
        plan[pick["pool"]] = {"for": e["pool"], "pc_s": pick["pc_s"]}
        c["placebo_drawn"] += 1
    return plan, c


def write_placebo_plan(plan, counters, path):
    """the plan, written (0440) with its sha256 BEFORE any placebo price is computed; never rebuilt: refuses to overwrite. -> sha256 of the file bytes"""
    path = Path(path)
    if path.exists():
        raise FileExistsError("placebo plan exists: never rebuilt after a placebo price may exist")
    data = (json.dumps({"plan": plan, "counters": dict(counters)}, sort_keys=True, separators=(",", ":")) + "\n").encode()
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.chmod(path, 0o440)
    return hashlib.sha256(data).hexdigest()


def load_placebo_plan(path, sha256):
    """-> plan dict; refuses when the pinned sha256 is missing or differs (the plan is fixed before the second pass prices anything)"""
    if not sha256:
        raise ValueError("placebo plan sha256 not pinned")
    data = Path(path).read_bytes()
    if hashlib.sha256(data).hexdigest() != sha256:
        raise ValueError("placebo plan sha256 differs from the pinned one")
    return json.loads(data)["plan"]


def placebo_summary(book_n, placebo_n, plan_sha="<sha>", built="<instant>", through_hour="<H>", placebo_excluded_share=None):
    """5-day look condition (6) input: placebo n beside book n; fewer than 5 placebo trades, or the placebo run's own excluded share > 20% = not evaluable = NO-GO (ADD1 3, QP 1117 Q11)"""
    ok = placebo_n >= PLACEBO_MIN_N and not (placebo_excluded_share is not None and placebo_excluded_share > PLACEBO_MAX_EXCLUDED_SHARE)
    why = "placebo n < %d" % PLACEBO_MIN_N if placebo_n < PLACEBO_MIN_N else "placebo excluded share > %d%%" % round(100 * PLACEBO_MAX_EXCLUDED_SHARE)
    return {"book_n": book_n, "placebo_n": placebo_n, "condition_6_evaluable": ok, "verdict": None if ok else "NO-GO (%s: condition 6 not evaluable)" % why,
            "disclosure": PLACEBO_DISCLOSURE.format(sha=plan_sha, built=built, hour=through_hour)}


def board_disclosures(counts, exact_open_computed=False):
    """cap_open_stale is shown only when an exact open count was wired (exact_open_computed); otherwise the "not computed" wording is ALWAYS emitted and no cap_open_stale number (never a 0) is shown"""
    if not exact_open_computed:
        return [CAP_OPEN_NOT_COMPUTED]
    out = []
    if counts.get("cap_open_stale", 0) > 0:
        out.append(STALE_DISCLOSURE % counts["cap_open_stale"])
    return out


def hr_coin(create_row):
    """a mint whose create row creator is the holder-rewards PDA of the mint is excluded (tape cannot give its creator-fee rate)"""
    m, cr = create_row.get("mint"), create_row.get("creator")
    if not m or not cr:
        return False
    try:
        return cr == hr_pda_default(m)
    except Exception:  # noqa: BLE001 - a non-base58 mint cannot be a PDA owner mint
        return False


# ------------------------------------------------------------------------------------------------------------------------------ forward fetch (B3, QP 1117 Q2-Q4)
class ForwardFetch:
    """fetch_fn(launcher, first_create_slot) -> Table A row, for launchers first graduating from S. ONE append-only journal `<out_root>/u1-fwd-journal.jsonl`: one line per launcher, the Table A
    keys launcher / funder / funding_slot / status, plus `hub_activity` on the same line when that fetch ran the hub test of the row's funder (one line = nothing to tear apart; QP asked for
    separate B lines, reviewer round 3 asked for one line); written + flushed + fsynced before the decider uses a row. It is NOT loaded
    at start: a restart consumes it as a FIFO at the same calls that produced it (the next A line must be the requested launcher, else Refused; a B line is applied right after its A line, so
    hub_of never returns a later fact). No RPC while replaying a stored hour: a launcher the journal lacks in a stored hour is Refused. One attempt per launcher from S. The fetcher runs unlocked
    at FORWARD_RPS (2 rps), is seeded with the frozen backfill rows (incl. by_funder), and its per-UTC-day credit use is persisted to `u1-credits-by-day.json` at every counted call.
    The key is read only when the first live call is made (a dry run, all hours warm-up, makes 0 RPC calls and never touches the key file)."""

    def __init__(self, out_root, table_a_rows=(), table_b_rows=(), *, transport_factory=None, cap_per_day=F.CAPS["forward_day"], rps=F.FORWARD_RPS, clock=time.monotonic,
                 sleep_fn=time.sleep, day_fn=None, allow_rpc=True):
        self.root = Path(out_root)
        self._replaying = False  # True while the current hour is already stored in md5-by-hour.jsonl: any RPC attempt is refused
        self.allow_rpc = allow_rpc  # False (manifest u1.no_rpc, the dry run): any live fetch raises
        self.journal = self.root / FWD_JOURNAL
        self.credits_path = self.root / FWD_CREDITS
        self.by_hour_path = self.root / FWD_BY_HOUR
        self.transport_factory = transport_factory
        self._transport = None
        self.day_fn = day_fn or (lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        used = json.loads(self.credits_path.read_text()) if self.credits_path.exists() else {}
        self.meters = F.DailyMeters(cap_per_day, used_by_day=used, on_charge=self._persist_credits)
        self.fetcher = F.Fetcher(self._rpc, self.meters, rps=rps, clock=clock, sleep_fn=sleep_fn, day_fn=self.day_fn)
        self.fetcher.seed(table_a_rows, table_b_rows)
        self.rps = rps
        self.stored_hours = set()
        mh = self.root / "md5-by-hour.jsonl"
        if mh.exists():
            self.stored_hours = {json.loads(ln)["hour"] for ln in mh.read_text().splitlines() if ln.strip()}
        self.hour = None
        self.counters = collections.Counter()
        self._hub_now = {}
        self._rfh, self._buf, self._torn_at, self._eof, self._buf_pos = None, None, None, False, 0
        self._rows_total = 0
        self._calls_hour0 = 0

    # -- transport (lazy: the key file is touched at the first live call only)
    def _rpc(self, method, params):
        if self._replaying or not self.allow_rpc:
            raise S.Refused("u1 forward fetch: RPC refused (dry run no_rpc, or a stored hour being replayed)")  # code guard, not only a test
        if self._transport is None:
            if self.transport_factory is None:
                raise F.RpcError("no_transport")
            self._transport = self.transport_factory()
        return self._transport(method, params)

    def _persist_credits(self):
        self.root.mkdir(parents=True, exist_ok=True)
        _atomic_write(self.credits_path, json.dumps(self.meters.used_by_day(), sort_keys=True).encode(), 0o640)

    def total_calls(self):
        return sum(sum(m.calls.values()) for m in self.meters.days.values())

    # -- journal
    def _peek(self):
        if self._buf is None:
            if self._eof:
                return None  # the replay is over: everything after is live and is appended (never re-read)
            if self._rfh is None:
                if not self.journal.exists():
                    self._eof = True
                    return None
                self._rfh = open(self.journal, "rb")
            pos = self._rfh.tell()
            self._buf_pos = pos
            ln = self._rfh.readline()
            if not ln:
                self._eof = True
                return None
            if not ln.endswith(b"\n"):
                self._torn_at = pos  # a torn last line (crash mid-write): only the fragment is dropped (complete lines stay), cut off at the first append
                self.counters["u1_fwd_torn_line_dropped"] += 1
                self._eof = True
                return None
            self._buf = json.loads(ln)
        return self._buf

    def _take(self):
        r, self._buf = self._peek(), None
        self._rows_total += 1
        self.counters["u1_fwd_rows"] += 1
        return r

    def _append(self, rows):
        if self._rfh is not None:
            self._rfh.close()
            self._rfh = None
        self.root.mkdir(parents=True, exist_ok=True)
        if self._torn_at is not None:
            with open(self.journal, "r+b") as fh:
                fh.truncate(self._torn_at)
            self._torn_at = None
        fd = os.open(str(self.journal), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640)
        with os.fdopen(fd, "ab") as fh:
            for r in rows:
                fh.write((json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n").encode())
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(self.journal, 0o640)
        self._rows_total += len(rows)
        self.counters["u1_fwd_rows"] += len(rows)

    # -- fetch_fn interface
    def set_hour(self, hour, warmup=False):
        self.hour = hour
        self._replaying = hour in self.stored_hours
        self._calls_hour0 = self.total_calls()

    def hub_of(self, funder):
        return self._hub_now.get(funder)

    def __call__(self, launcher, first_create_slot):
        self._hub_now = {}
        a = self._peek()
        row = None
        if a is not None:  # replay: the journal answers, no RPC
            if a.get("launcher") != launcher or not (set(a) == set(F.TABLE_A_KEYS) or set(a) == set(F.TABLE_A_KEYS) | {"hub_activity"}):
                raise S.Refused("u1 forward journal out of order")
            self._take()
            row = {k: a[k] for k in F.TABLE_A_KEYS}
            self.fetcher.table_a[launcher] = row
            if row["status"] == "resolved":
                self.fetcher.by_funder[row["funder"]].append(launcher)
            if "hub_activity" in a:  # the hub result of THIS fetch (one journal line = one launcher row + its hub row: nothing to tear apart)
                self.fetcher.table_b[row["funder"]] = a["hub_activity"]
                self._hub_now[row["funder"]] = a["hub_activity"]
        if row is None:
            if self.hour in self.stored_hours:
                raise S.Refused("u1 forward journal lacks launcher for a stored hour: no RPC while replaying")
            if not self.allow_rpc:
                raise S.Refused("u1 forward fetch: RPC refused (dry run no_rpc)")
            before = set(self.fetcher.table_b)
            row = dict(self.fetcher.fetch_launcher(launcher, first_create_slot))
            row = {k: row[k] for k in F.TABLE_A_KEYS}
            new_b = [(f, v) for f, v in self.fetcher.table_b.items() if f not in before]
            line = dict(row)
            for f, v in new_b:  # at most one: the hub test is on this row's funder
                line["hub_activity"] = v
                self._hub_now[f] = v
            self._append([line])
        if row["status"] in ("fetch_error", "fetch_capped"):
            self.counters["u1_fetch_once_unlinked"] += 1
        self.counters["u1_rpc_calls"] = self.total_calls()
        self.counters["u1_n429"] = self.fetcher.counters.get("n429", 0)
        return row

    def end_hour(self, hour):
        """per-hour chain (counts only, sidecar `u1-fwd-by-hour.jsonl`, one line per hour, never duplicated by a replay). Rewritten atomically with fsync; a torn or unparsable last line
        left by an older crash is dropped (and counted) and the record recomputed, never a reason to stop the book."""
        recs = []
        if self.by_hour_path.exists():
            lines = self.by_hour_path.read_text().splitlines()
            for i, ln in enumerate(lines):
                try:
                    recs.append(json.loads(ln))
                except ValueError:
                    if i != len(lines) - 1:
                        raise S.Refused("u1 forward by-hour sidecar corrupt before its last line")
                    self.counters["u1_by_hour_torn_dropped"] += 1
        if hour in {r["hour"] for r in recs}:
            if self.counters.get("u1_by_hour_torn_dropped"):
                _atomic_write(self.by_hour_path, "".join(json.dumps(r, sort_keys=True) + "\n" for r in recs).encode(), 0o640)
            return
        data = self.journal.read_bytes() if self.journal.exists() else b""
        recs.append({"hour": hour, "fwd_rows_total": self._rows_total, "journal_sha256": hashlib.sha256(data).hexdigest(), "rpc_calls_hour": self.total_calls() - self._calls_hour0,
                     "credits_day": self.meters.used_by_day().get(self.day_fn(), 0)})
        _atomic_write(self.by_hour_path, "".join(json.dumps(r, sort_keys=True) + "\n" for r in recs).encode(), 0o640)


class BookJournalReader:
    """fetch_fn of `u1_lowrep` (QP-RULING-1117-u1-start ADD1): reads the BOOK's forward journal read-only. No transport, no key, no journal or credits file of its own; any path that would call
    RPC raises Refused. Before deciding a forward hour H it waits (poll) until the book's md5-by-hour.jsonl holds H (the book fsyncs its journal rows before it stores the hour), then consumes the
    journal as a FIFO exactly like ForwardFetch's replay. End of file is not permanent: the reader resumes from its byte offset. A launcher the journal lacks once the book has stored the hour
    is Refused, never fetched."""

    def __init__(self, book_out_root, own_out_root, *, sleep_fn=time.sleep, poll_s=None, status_fn=None, book_manifest_sha256=None):
        self.book, self.own = Path(book_out_root), Path(own_out_root)
        if os.path.realpath(self.book) == os.path.realpath(self.own):
            raise S.Refused("u1.book_out_root must differ from the line's own out_root")
        if not (self.book / "manifest.sha256").exists():
            raise S.Refused("u1.book_out_root holds no manifest.sha256: not a book run")
        if book_manifest_sha256 is not None and (self.book / "manifest.sha256").read_text().split()[:1] != [book_manifest_sha256]:
            raise S.Refused("u1.book_manifest_sha256 differs from the book's manifest.sha256")
        try:
            meta = json.loads((self.book / "meta.json").read_text())
        except (OSError, ValueError):
            raise S.Refused("u1.book_out_root meta.json unreadable")
        if meta.get("decider") != "u1":
            raise S.Refused("u1.book_out_root is not the deciding u1 book")
        self.journal, self.md5_path = self.book / FWD_JOURNAL, self.book / "md5-by-hour.jsonl"
        self.sleep_fn, self.poll_s = sleep_fn, (S.POLL_S if poll_s is None else poll_s)
        self.status_fn = status_fn or self._write_status
        self.counters = collections.Counter()
        self.counters["u1_rpc_calls"] = 0
        self._off, self._hub_now, self.hour = 0, {}, None

    def _write_status(self, hour):
        sp = self.own / "status.json"
        try:
            st = json.loads(sp.read_text()) if sp.exists() else {}
        except ValueError:
            st = {}
        if hour is None:
            st.pop("waiting_for_book_hour", None)
        else:
            st["waiting_for_book_hour"] = hour
        self.own.mkdir(parents=True, exist_ok=True)
        _atomic_write(sp, json.dumps(st, sort_keys=True).encode(), 0o640)

    def _book_hours(self):
        if not self.md5_path.exists():
            return set()
        out = set()
        for ln in self.md5_path.read_text().splitlines():
            try:
                out.add(json.loads(ln)["hour"])
            except (ValueError, KeyError):
                continue  # a line still being written
        return out

    def set_hour(self, hour, warmup=False):
        self.hour = hour
        if warmup:
            return
        waited = False
        while hour not in self._book_hours():
            if not waited:
                self.status_fn(hour)
                waited = True
            self.counters["u1_lowrep_waits"] += 1
            self.sleep_fn(self.poll_s)
        if waited:
            self.status_fn(None)

    def _next(self):
        """-> the next complete journal line (parsed) or None when there is none YET; the offset only moves over complete lines"""
        if not self.journal.exists():
            return None
        with open(self.journal, "rb") as fh:
            fh.seek(self._off)
            ln = fh.readline()
        if not ln or not ln.endswith(b"\n"):
            return None
        return json.loads(ln), len(ln)

    def hub_of(self, funder):
        return self._hub_now.get(funder)

    def __call__(self, launcher, first_create_slot):
        self._hub_now = {}
        got = self._next()
        if got is None:
            raise S.Refused("u1 lowrep has no live fetch: the book's journal lacks launcher for a stored book hour")
        a, n = got
        if a.get("launcher") != launcher or not (set(a) == set(F.TABLE_A_KEYS) or set(a) == set(F.TABLE_A_KEYS) | {"hub_activity"}):
            raise S.Refused("u1 forward journal out of order")
        self._off += n
        self.counters["u1_fwd_rows"] += 1
        if "hub_activity" in a:
            self._hub_now[a["funder"]] = a["hub_activity"]
        row = {k: a[k] for k in F.TABLE_A_KEYS}
        if row["status"] in ("fetch_error", "fetch_capped"):
            self.counters["u1_fetch_once_unlinked"] += 1
        return row

    def end_hour(self, hour):
        return None


# ------------------------------------------------------------------------------------------------------------------------------ decider
class U1Decider:
    """decide(hour_rows, state) -> [Entry] (the c1nf_forward_sim interface). Keeper-only input; every decision uses only rows fed up to the crossing row of D.
    start_ms = S; w_start_s = W start (S - 336 h, never before 2026-11-03T03Z); table = FunderTable; labels = H3 label set pinned before S (empty -> H3 empty); fetch_fn(launcher,
    first_create_slot) -> Table A row, optional forward fetch when a launcher is untraced; open_fn() -> open positions of the book now; hr_fn(create_row) -> holder-rewards coin
    (injected: the UR 0.4 rule was not read by the builder; default off)."""

    def __init__(self, start_ms, table, *, mode="book", labels=frozenset(), plan=None, fetch_fn=None, open_fn=None, exact_open_fn=None, hr_fn=None, max_open=MAX_OPEN, lat_s=S.LAT_DECIDING_S, out_root=None):
        if mode not in MODES:
            raise ValueError("mode must be one of %s" % (MODES,))
        if mode == "placebo" and plan is None:
            raise ValueError("placebo mode needs a plan (build_placebo_plan)")
        self.start_ms, self.table, self.mode, self.labels = int(start_ms), table, mode, frozenset(labels)
        self.plan, self.fetch_fn, self.open_fn, self.hr_fn, self.max_open, self.lat_s = plan, fetch_fn, open_fn, hr_fn or hr_coin, max_open, lat_s
        self.exact_open_fn = exact_open_fn
        self.pos = {}              # pool -> {"land_slot", "expiry_s", "ref"} open book positions watched for the stop
        self.last_spot = {}        # pool -> spot of its last print (post_spot)
        self.sell_reqs = []
        self.own_entries = []      # decision_ms of every entry this decider emitted (own timed upper bound for the open count)
        self.w_start_s = max(start_ms // 1000 - HIST_WINDOW_S, S.parse_hour(S.WARMUP_START_MIN) // 1000)
        self.reg = Registry()
        self.pending = []          # [(D_ms, mint)]
        self.last_slot = None
        self.entered = set()
        self.gate_log = []         # [{pool, mint, pc_s, entered}] every gate pass (book mode), for the placebo plan
        self.universe_log = []     # [{pool, mint, pc_s}] graduates that entered the universe
        self.c = collections.Counter()
        self.counters_prev = collections.Counter()
        self.fetch_seen = {}
        self.out_root = Path(out_root) if out_root is not None else None
        self.launcher_hours = []   # [{hour, launchers, sha256}] one record per warm-up hour (rewritten whole: a replay rebuilds it identically)
        if self.out_root is not None and self.mode == "book":
            pi = self.out_root / PLAN_INPUT
            if pi.exists():
                pi.unlink()  # a restart replays from W and rebuilds the same file: never a duplicate line

    # -- persistence (B1 launcher list, B2 plan input); names / counts / sha256 only leave this class, the files carry addresses
    def _write_launcher_list(self, hour):
        n, sha, below = write_launcher_list(self.reg, self.out_root / LAUNCHER_LIST)
        self.c["u1_launchers_listed"], self.c["u1_first_slot_below_graduate_create"] = n, below
        self.launcher_hours.append({"hour": hour, "launchers": n, "sha256": sha})
        _atomic_write_0440(self.out_root / LAUNCHER_LIST_BY_HOUR, "".join(json.dumps(r, sort_keys=True) + "\n" for r in self.launcher_hours).encode())

    def _append_plan_input(self, hour):
        """append-only: this hour's new universe and gate rows, then ALWAYS one `{"kind": "hour", "hour": H}` marker (empty hours included): a reader knows the file is complete through H only
        if H's marker is there. The file is 0440 between appends. The in-memory logs are cleared once flushed (bounded memory)."""
        path = self.out_root / PLAN_INPUT
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            os.close(os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        os.chmod(path, 0o640)
        with open(path, "ab") as fh:
            for r in self.universe_log:
                fh.write((json.dumps({"kind": "universe", "pool": r["pool"], "mint": r["mint"], "pc_s": r["pc_s"]}, sort_keys=True) + "\n").encode())
            for r in self.gate_log:
                fh.write((json.dumps({"kind": "gate", "pool": r["pool"], "mint": r["mint"], "pc_s": r["pc_s"], "entered": r["entered"]}, sort_keys=True) + "\n").encode())
            fh.write((json.dumps({"kind": "hour", "hour": hour}, sort_keys=True) + "\n").encode())
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(path, 0o440)
        self.universe_log.clear()
        self.gate_log.clear()

    # -- funder side
    def _ensure_traced(self, g):
        if self.fetch_fn and g.launcher and g.create_slot is not None and not self.table.known(g.launcher):
            fs = first_create_slot(self.reg, g.launcher)  # Q1: the same definition as the launcher list (min over ALL create rows), never the graduate's own create slot
            row = self.fetch_fn(g.launcher, fs if fs is not None else g.create_slot)
            self.table.mark_seen(g.launcher)
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

    def open_count(self, row, n_batch):
        """open positions for the cap: NEVER below the exact count (QP ADD1 2). With open_fn (the Book's hour-start count: every position decided before the hour start and not closed
        by it) the batch entries are added; positions that close inside the hour stay counted (over-count only). Without open_fn: the decider's own entries whose 6 h timed end is
        still ahead of the current time (ignores earlier stop exits: over-count only)."""
        if self.open_fn is not None:
            return self.open_fn() + n_batch
        now = int(row["t_recv_ms"])
        return sum(1 for ms in self.own_entries if ms + HOLD_S * 1000 > now)

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
        opened = self.open_count(row, len(out))
        if opened >= self.max_open:
            c["cap_open"] += 1  # a refusal before pricing, not an exclusion of a priced entry
            if self.exact_open_fn is not None and self.exact_open_fn(d_ms) < self.max_open:
                c["cap_open_stale"] += 1  # an exact count at D (after the fact, rows <= D) would have taken it; shown beside, not in the GO bar
            if self.mode == "book":
                self.gate_log.append({"pool": g.pool, "mint": mint, "pc_s": g.pc_s, "entered": False})
            return
        self.entered.add(mint)
        self.own_entries.append(int(row["t_recv_ms"]))
        c["entries"] += 1
        if self.mode == "book":
            self.gate_log.append({"pool": g.pool, "mint": mint, "pc_s": g.pc_s, "entered": True})
        self.pos[g.pool] = {"land_slot": int(sd_slot) + STOP_SELL_LAG_SLOTS, "expiry_s": d_ms // 1000 + HOLD_S, "ref": None}
        out.append(S.Entry(pool=g.pool, mint=mint, at_row=i, decision_ms=int(row["t_recv_ms"]), decision_slot=int(sd_slot), exit_after_s=HOLD_S - self.lat_s, meta=meta))

    def _check_stop(self, row, i):
        """hard stop (ruling 4b), same mechanism as U2: reference = the entry print's spot (post_spot, no own impact) = the pool's last print at or before the landing slot SD + 19;
        trigger = the first PumpSwap print with slot > landing and spot <= 0.75 x reference; the sell is requested just after that print (at_row = i + 1) and the runner prices it at the
        engine's exit lag after the print's slot. Independent of stake; the same code serves the book, the low-reputation line and the placebo."""
        if row.get("type", "trade") != "trade" or row.get("prog") != "pumpswap" or not row.get("pool"):
            return
        pool, slot, spot = row["pool"], row.get("slot"), post_spot(row)
        prev = self.last_spot.get(pool)
        if spot is not None:
            self.last_spot[pool] = spot
        p = self.pos.get(pool)
        if p is None or spot is None or not S._isint(slot):
            return
        bt = S._bt(row)
        if bt is not None and bt >= p["expiry_s"]:
            del self.pos[pool]  # past the timed exit: no stop is requested any more
            return
        if p["ref"] is None:
            if slot <= p["land_slot"] or prev is None:
                return
            p["ref"] = prev
        if spot <= STOP_FRAC * p["ref"]:
            self.sell_reqs.append(S.SellReq(pool=pool, at_row=i + 1, decision_ms=int(row["t_recv_ms"]), decision_slot=int(slot), reason="stop", bot="main"))
            self.c["stop_exits"] += 1
            del self.pos[pool]

    def take_sells(self):
        out, self.sell_reqs = self.sell_reqs, []
        return out

    def decide(self, hour_rows, state):
        out = []
        if hasattr(self.fetch_fn, "set_hour"):
            self.fetch_fn.set_hour(state.hour, state.warmup)
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
            self._check_stop(row, i)
            g = self.reg.feed(row)
            if g is not None:
                self.c["graduates_seen"] += 1
                if g.pc_ms + DECISION_DELAY_S * 1000 >= self.start_ms:
                    if state.warmup:
                        self.c["queued_in_last_warmup_hour"] += 1  # D >= S: decided at S or later; _ensure_traced stays forward-only
                    else:
                        self._ensure_traced(g)
                    self.pending.append((g.pc_ms + DECISION_DELAY_S * 1000, g.mint))
                    self.pending.sort()
            if S._isint(row.get("slot")) and S._isint(t):
                self.last_slot = row["slot"]
        if state.warmup and out:
            raise AssertionError("U1 decider produced an entry in a warm-up hour")
        if self.out_root is not None:
            if state.warmup:
                self._write_launcher_list(state.hour)
            elif self.mode == "book":
                self._append_plan_input(state.hour)
        if hasattr(self.fetch_fn, "counters"):
            if not state.warmup:
                self.fetch_fn.end_hour(state.hour)
            self.c.update({k: v - self.fetch_seen.get(k, 0) for k, v in self.fetch_fn.counters.items()})
            self.fetch_seen = dict(self.fetch_fn.counters)
        cnt = collections.Counter(self.c)
        cnt.update(self.reg.c)
        state.counts.update({k: v - self.counters_prev.get(k, 0) for k, v in cnt.items() if v != self.counters_prev.get(k, 0)})
        self.counters_prev = cnt
        return out


def _checked_file(path, want, key):
    """file bytes whose sha256 equals the manifest's checked key; Refused on a missing key or a mismatch (QP 1117 Q10)"""
    if not (isinstance(want, str) and re.fullmatch(r"[0-9a-f]{64}", want)):
        raise S.Refused("u1.%s_sha256 must be 64 hex" % key)
    data = Path(path).read_bytes()
    if hashlib.sha256(data).hexdigest() != want:
        raise S.Refused("u1.%s file sha256 differs from the pinned one" % key)
    return data


def build_decider(m, table=None, **kw):
    """runner glue (manifest key decider 'u1' | 'u1_lowrep' | 'u1_placebo'). Tables, labels and the placebo plan come from files named in the manifest `u1` object; table_a / table_b / labels
    are checked against table_a_sha256 / table_b_sha256 / labels_sha256 (Refused on mismatch). No labels key = H3 empty. The forward fetch (ForwardFetch) is built here and makes no call and
    reads no key until the first live fetch."""
    u1 = m.get("u1") or {}
    a_rows, b_rows = [], []
    if table is None:
        a_rows = [json.loads(x) for x in _checked_file(u1["table_a"], u1.get("table_a_sha256"), "table_a").decode().splitlines() if x.strip()]
        if u1.get("table_b"):
            b_rows = [json.loads(x) for x in _checked_file(u1["table_b"], u1.get("table_b_sha256"), "table_b").decode().splitlines() if x.strip()]
        table = FunderTable.from_rows(a_rows, b_rows)
    labels = frozenset(_checked_file(u1["labels"], u1.get("labels_sha256"), "labels").decode().split()) if u1.get("labels") else frozenset()
    mode = {"u1": "book", "u1_lowrep": "low_rep", "u1_placebo": "placebo"}[m["decider"]]
    plan = load_placebo_plan(u1["placebo_plan"], u1.get("placebo_plan_sha256")) if mode == "placebo" else None
    kw.setdefault("out_root", m.get("out_root"))
    if kw.get("out_root") is not None and "fetch_fn" not in kw and mode == "low_rep":
        kw["fetch_fn"] = BookJournalReader(u1["book_out_root"], kw["out_root"], book_manifest_sha256=u1.get("book_manifest_sha256"))   # ADD1: no transport, no key; placebo: no fetcher at all (fetch_fn None)
    if kw.get("out_root") is not None and "fetch_fn" not in kw and mode == "book":
        # only the deciding book builds a live fetcher
        kw["fetch_fn"] = ForwardFetch(kw["out_root"], a_rows, b_rows, transport_factory=lambda: F.make_transport(F.load_key()), allow_rpc=not u1.get("no_rpc", False))
    return U1Decider(S.parse_hour(m["start_hour"]), table, mode=mode, labels=labels, plan=plan, lat_s=float(m.get("pricing_lat_entry_s", S.LAT_DECIDING_S)), **kw)


# ------------------------------------------------------------------------------------------------------------------------------ plan subcommand (B2)
def read_plan_input(path, through_hour):
    """-> (universe, entries, gate_passed_mints, n_not_ripe). Refuses (ValueError) an incomplete file: no hour markers, markers not consecutive, no marker for through_hour (or none at all), or no
    book entry. through_hour 'YYYY-MM-DDTHH' (required): a book entry is kept only if the hour containing P + 2,160 s is closed by the end of that hour (Q7 rule 1; every
    candidate with pool_create <= P + 1,800 s has then reached D = pool_create + 360 s and is in the file); the others are counted (placebo_entry_not_ripe). Universe and gate rows are kept
    whole: a ripe entry's draw depends on nothing later, so an extension plan reproduces every earlier draw."""
    uni, ents, gate, hours, pending = [], [], set(), [], []
    for ln in Path(path).read_text().splitlines():
        if not ln.strip():
            continue
        r = json.loads(ln)
        if r["kind"] == "hour":
            hours.append(r["hour"])
        elif r["kind"] == "universe":
            uni.append({"pool": r["pool"], "mint": r["mint"], "pc_s": r["pc_s"]})
        elif r["kind"] == "gate":
            gate.add(r["mint"])
            if r.get("entered"):
                pending.append({"pool": r["pool"], "mint": r["mint"], "pc_s": r["pc_s"]})
    if not hours:
        raise ValueError("plan input has no hour marker: incomplete (restart replay, exit 4 or empty file): refused")
    for a, b in zip(hours, hours[1:]):
        if S.parse_hour(b) != S.parse_hour(a) + S.HOUR_MS:
            raise ValueError("plan input hour markers are not consecutive (%s then %s): refused" % (a, b))
    if through_hour not in hours:
        raise ValueError("plan input has no marker for hour %s: not complete through it: refused" % through_hour)
    end_s = (S.parse_hour(through_hour) + S.HOUR_MS) // 1000
    not_ripe = 0
    for e in pending:
        if e["pc_s"] + PLACEBO_RIPE_S >= end_s:
            not_ripe += 1
        else:
            ents.append(e)
    if not ents:
        raise ValueError("plan input has no ripe book entry through %s: refused" % through_hour)
    return uni, ents, gate, not_ripe


def plan_main(a_in, a_out, through_hour, extends=None):
    uni, ents, gate, not_ripe = read_plan_input(a_in, through_hour)
    plan, c = build_placebo_plan(uni, ents, gate)
    c["placebo_entry_not_ripe"] += not_ripe
    if extends:
        old = json.loads(Path(extends).read_text())["plan"]
        bad = [k for k, v in old.items() if plan.get(k) != v]
        if bad:
            raise ValueError("extension plan changes %d row(s) of the earlier plan: refused (condition 6 is not evaluable = NO-GO)" % len(bad))
    sha = write_placebo_plan(plan, c, a_out)
    return {"plan_sha256": sha, "plan_pools": len(plan), "universe": len(uni), "book_entries": len(ents), "counters": dict(c)}


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="u1_forward_sim helpers")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("plan", help="build the placebo plan from the persisted plan input; prints the sha256 and counters only")
    pl.add_argument("--in", dest="inp", required=True)
    pl.add_argument("--out", required=True)
    pl.add_argument("--through-hour", required=True, help="YYYY-MM-DDTHH, the last closed hour the plan may use")
    pl.add_argument("--extends", default=None, help="an earlier plan file whose rows must appear unchanged in the new plan (else refused)")
    a = ap.parse_args(argv)
    print(json.dumps(plan_main(a.inp, a.out, a.through_hour, a.extends), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
