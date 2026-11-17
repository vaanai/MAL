#!/usr/bin/env python3
"""c1nf_forward_sim: FORWARD paper simulation of one strategy (first: C1-NF, the frozen C1 LAYA cascade + top-holder cap h_top1 <= 0.5, exit 300 s) on the arena tip feed
(hv7 `tape-<hour>.jsonl[.zst]`, arena_tape_v1 rows). PAPER only; in-sample research, not a gate read. Nothing here says any book makes money.

Design input: /data/mal/hunt-1008/ops/reports/2026-11-16/c1nf-count-feasibility.md (blockers 1-5, "Smallest fix" A-E). Entries are priced (ruling 2.2-2.6: 19 slots = pricing_lat_entry_s 4.085 deciding; 3.010 and 5.590 twins; 505,000 per send deciding, 160,000 beside; both fail models are in every row's legs; exit at landing + 300 s) by tools/arena_paper.py through
scripts/research/floor_gap_range_engine.GapRangeEngine (the FLOOR open-sim engine: virtual reserve V, feed delay d + k slots, fees and rent as the arena stack), at the total chain time pricing_lat_entry_s (osb_follow's LATENCY_VALUES_S). The book files have osb_follow's layout.

Pieces
  adapter    arena_row_to_c1nf(row): hv7 arena row -> the (kind, row) shape tools.c1nf_shadow.Shadow.feed reads (blocker 1). Pure, no I/O.
  decider    plug-in interface: decide(hour_rows, state) -> [Entry]. Entry = what to buy (pool, mint, the row index it is decided before, decision instant and slot, meta).
             C1nfDecider wraps the pinned Shadow (decisions only: the outcome code path of the shadow is switched off). Later strategies implement the same two-line interface.
  ledger     the as-of wallet ledger is built FORWARD (blocker 3): a UTC day's ledger is built only once all 24 hours of that day were processed by this run (warm-up hours
             included, and listed), through tools/c1nf_wallet_ledger (the tip adapter over a scratch copy of the day's adapted trade rows). Until asof-<day> exists the
             engine answers wallet_ok False and the shadow never picks: those minutes are counted as `no_wallet_ledger` refusals, no pick is faked.
  seal       the pick-oracle skip is the manifest key `pick_oracle` ("none: EXP-022 withdrawn ..."), valid only from S (S >= 2026-10-16T01Z); never --no-seal.
  warm-up    W = [max(2026-11-03T03Z, S - 336 h), S): the hours before S feed the decider's engine with decisions OFF in code; no buy, sell, ledger row or outcome code path runs for
             them (an entry from a decider in a warm-up hour, or decided before S, is refused). W and the disclosure sentence are in meta.json. Pricing history starts at S.
  input      only the osb keeper's hard links under /data2/mal/scratch/floor-open-sim/keep/, read through symlinks under out_root/tapelink (QP-RULING-1116-c1nf-forward-sim 1.1).
Output (out_root): manifest.sha256, code.sha256, meta.json, status.json, md5-by-hour.jsonl, decisions.md5, counts.jsonl (per hour: decider counters), runs/NNNN/{ledger/<bot>/trades-DAY.jsonl,
bots.json, variant.json}, `book` -> the active run. Restart: replays from the warm-up start into a new run and compares every stored hour's decision and ledger md5 (exit 4 on a difference).
Exit codes: 0 ok; 2 refused; 3 ledger write fatal; 4 md5 mismatch.
Usage: c1nf_forward_sim.py MANIFEST.json [--once] [--max-hours N]   (every parameter, including pick_oracle and pricing_lat_entry_s, is a pinned manifest key; QP-RULING-1116-c1nf-forward-sim)
"""
import argparse, collections, dataclasses, fcntl, hashlib, heapq, json, os, re, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Protocol, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from tools import arena_paper as AP  # noqa: E402
from tools import arena_rules as R  # noqa: E402
from tools.arena_paper import canon  # noqa: E402
from tools.arena_stack_v2 import stack_config_for  # noqa: E402
import osb_follow as O  # noqa: E402
from floor_gap_range_engine import GapRangeEngine  # noqa: E402

REPO = Path(__file__).resolve().parent.parent.parent
KEEP_ROOT = "/data2/mal/scratch/floor-open-sim/keep"  # QP-RULING-1116-c1nf-forward-sim 1.1: the osb keeper's hard links of closed hv7 hours; the only tape input
SIM_ROOT = "/data2/mal/scratch/c1nf-open-sim"          # the only place this runner writes
FORBIDDEN_PATH_WORDS = ("/var/lib/mal/sealed", "tip-tape-archive", "arena-mirror", "arena-tip-feed", "fast0-offload", "arena/tape", "/forward-", "r4b", "meta-capture", "laya-opt")
WARMUP_MAX_H, WARMUP_START_MIN = 336, "2026-11-03T03"  # ruling 1.2: W = [max(2026-11-03T03Z, S - 336 h), S)
MIN_WARMUP_H = 26                                    # run_live's bootstrap minimum; tests lower it
SENDS = (505_000, 160_000)                           # deciding C1-NF send; FLOOR's live send shown beside (ruling 2.5)
LAT_DECIDING_S = 4.085                               # 19 slots, deciding; 3.010 (14) and 5.590 (26) are twins (ruling 2.2)
HOUR_MS = 3_600_000
POLL_S = 60
SEAL_FIRST_HOUR = "2026-10-16T01"  # EXP-022 s9: a pool whose first print is at or after this instant needs the pick oracle (tools.c1nf_shadow.SEAL_START_MS)
EXIT_S_DEFAULT = 300.0
WSOL = "So11111111111111111111111111111111111111112"
EXIT_OK, EXIT_REFUSED, EXIT_FATAL, EXIT_MISMATCH = 0, 2, 3, 4
MANIFEST_KEYS = {"book_id", "title", "decider", "stakes_sol", "send_lamports", "start_hour", "warmup_hours", "tape_dir", "out_root", "exit_s", "pricing_lat_entry_s",
                 "pick_oracle", "feed_delay_slots", "wallet_ledger_root", "model_manifest", "model_manifest_sha256", "duckdb_pin", "rent_mode", "u1"}
REQUIRED_KEYS = {"book_id", "title", "decider", "stakes_sol", "send_lamports", "start_hour", "tape_dir", "out_root"}
DECIDERS = ("c1nf", "u4", "u2", "u1", "u1_lowrep", "u1_placebo")
U1_DECIDERS = ("u1", "u1_lowrep", "u1_placebo")      # scripts/research/u1_forward_sim.py (QP-RULING-1116-u1-forward-spec); no model, no wallet ledger; each is its own book run (bot "main")
WARMUP_MIN_BY_DECIDER = {"u4": 168, "u2": 192, "u1": 168, "u1_lowrep": 168, "u1_placebo": 168}      # QP-RULING-1116-u4-u2-p81-forward-specs 0.3: W >= 168 h (U4), >= 192 h (U2); otherwise S moves later
EXIT_S_MAX = {"c1nf": 3600, "u4": 259_200, "u2": 259_200, "u1": 3600, "u1_lowrep": 3600, "u1_placebo": 3600}  # ruling 0.2 item 2: exit_s up to 72 h for the mark-clock books; c1nf stays at 3600
GAP_DISCLOSURE = ("Strict feed-gap exclusion: any data_loss gap between decision and exit removes the trade (engine default; gap-in-hold mode cannot be combined with the 19-slot latency). "
                  "Exclusion odds rise with hold length, so early exits (stops, takes, share-fall) are over-represented among priced trades; excluded {n} of {N} entries ({timed}/{requested}).")  # ADD1 A1, verbatim
PLACEBO_DECIDERS = ("u4", "u2")                      # ruling 0.7: a second bot_id whose entries the decider draws; it never enters the decider's view of the deciding book


class Refused(Exception):
    """exit 2"""


class Mismatch(Exception):
    """exit 4"""


# ------------------------------------------------------------------------------------------------------------------------------ hours
def parse_hour(s):
    try:
        return int(datetime.strptime(s, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp() * 1000)
    except (ValueError, TypeError):
        raise Refused("bad hour %r (want YYYY-MM-DDTHH)" % (s,))


def hour_key(ms):
    return datetime.fromtimestamp(ms // 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H")


def day_key(ms):
    return hour_key(ms)[:10]


def hour_file(tape_dir, ms):
    k = hour_key(ms)
    for suf in (".jsonl", ".jsonl.zst"):
        p = Path(tape_dir) / ("tape-%s%s" % (k, suf))
        if p.exists():
            return p
    return None


def iter_hour_rows(path):
    """STREAM the rows of one closed hour file in file order (one line in memory at a time); a torn last line (no newline) is dropped. A .zst file is decompressed by `zstd -dc` on its realpath
    (zstd refuses a symlink; the caller, link_hour, has already refused any realpath outside the keeper dir) through a pipe; the exit code is checked at the end and a non-zero exit
    (or a generator closed early) never passes silently: Refused. QP / memory fix after job #2912 (OOM)."""
    path = Path(path)
    proc = None
    if path.name.endswith(".zst"):
        proc = subprocess.Popen(["zstd", "-dc", os.path.realpath(path)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        fh = proc.stdout
    else:
        fh = open(path, "rb")
    done = False
    try:
        for ln in fh:
            if not ln.endswith(b"\n"):
                continue  # torn last line
            if ln.strip():
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if isinstance(r, dict):
                    yield r
        done = True
    finally:
        fh.close()
        if proc is not None:
            if not done and proc.poll() is None:
                proc.kill()
            rc = proc.wait()
            if done and rc != 0:
                raise Refused("zstd -dc %s exited %d: hour file unreadable" % (path.name, rc))


def read_hour_rows(path):
    """all rows of one hour as a list (tests and small files only; the runner streams with iter_hour_rows)"""
    return list(iter_hour_rows(path))


# ------------------------------------------------------------------------------------------------------------------------------ adapter
def _isint(x):
    return isinstance(x, int) and not isinstance(x, bool)


def _bt(row):
    bt = row.get("block_time")
    if _isint(bt):
        return bt
    ts = row.get("event_ts")
    if _isint(ts):
        return ts
    t = row.get("t_recv_ms")
    return t // 1000 if _isint(t) else None


PROG_VENUE = {"pumpswap": "pumpswap", "pumpfun": "pump_bonding"}


def arena_row_to_c1nf(row):
    """one hv7 arena_tape_v1 row -> (kind, row) for Shadow.feed, or None when the row is not one the shadow reads / cannot be placed.
    kinds: "trades" (venue pumpswap | pump_bonding), "creates", "migrations" (type complete | pool_create), "gap" (a data_loss gap: -> Shadow.note_follower_gap).
    The arena trade row keeps the walker's keys (arena_tip_adapter.trade_row) minus TRADE_DROP, so trader, mint, pool, side, sol_lamports, token_raw, quote_reserve, base_reserve and the
    event V `virtual_quote_reserves` are already there; this renames `prog` to the shadow's `venue` and guarantees an integer block_time. It never invents a field."""
    typ, prog = row.get("type", "trade"), row.get("prog")
    slot = row.get("slot")
    if typ == "gap":
        if not (row.get("data_loss") or str(row.get("kind", "")).startswith("tip_gap")):
            return None
        lo, hi = row.get("from_slot", slot), row.get("to_slot", slot)
        return "gap", {"slot_from": lo if _isint(lo) else None, "slot_to": hi if _isint(hi) else None, "reason": str(row.get("kind"))[:60]}
    bt = _bt(row)
    if not _isint(slot) or bt is None:
        return None
    if typ == "trade":
        venue = PROG_VENUE.get(prog)
        if venue is None:
            return None
        r = dict(row)
        r["venue"], r["block_time"] = venue, bt
        return "trades", r
    if typ == "create":
        return "creates", {"mint": row.get("mint"), "creator": row.get("creator"), "name": row.get("name"), "symbol": row.get("symbol"), "slot": slot, "block_time": bt,
                           "is_mayhem_mode": row.get("is_mayhem_mode")}
    if typ == "complete":
        return "migrations", {"type": "complete", "mint": row.get("mint"), "slot": slot, "block_time": bt}
    if typ == "pool_create":
        return "migrations", {"type": "pool_create", "mint": row.get("mint"), "pool": row.get("pool"), "quote_mint": row.get("quote_mint") or WSOL, "slot": slot, "block_time": bt}
    return None


# ------------------------------------------------------------------------------------------------------------------------------ plug-in interface
@dataclasses.dataclass
class Entry:
    """One entry decided by a Decider. at_row: index into the hour's rows; the runner buys just BEFORE that row is given to the pricing engine (the engine has then seen exactly the
    rows the decider had seen). decision_ms / decision_slot: the stream instant of the decision = t_recv_ms and slot of the row that crossed the minute (row at_row itself; QP review on
    #1415: never the row before, a live process cannot decide before it has received the trigger row)."""
    pool: str
    mint: str
    at_row: int
    decision_ms: int
    decision_slot: int
    exit_after_s: float = EXIT_S_DEFAULT
    meta: dict = dataclasses.field(default_factory=dict)
    bot: str = "main"                 # "main" (the deciding book) | "placebo" (ruling 0.7)
    exit_from_decision: bool = False  # True: the timed exit is decision instant + exit_after_s (ruling U4 1.3, U2 2.3); False: entry landing + exit_after_s (C1-NF)

    def as_dict(self):
        d = dataclasses.asdict(self)
        if d["bot"] == "main":
            del d["bot"]  # the C1-NF decision md5 stays byte-identical to the pre-extension runner
        if not d["exit_from_decision"]:
            del d["exit_from_decision"]
        return d


@dataclasses.dataclass
class SellReq:
    """A decider-requested sell (ruling 0.2 item 3): position = the open (bot, pool) position. at_row: the sell is handed to the engine just BEFORE that row, like an Entry.
    decision_ms / decision_slot: the stream instant of the trigger (the print, or the mark's last row). The engine prices it at the exit lag after decision_slot."""
    pool: str
    at_row: int
    decision_ms: int
    decision_slot: int
    reason: str
    bot: str = "main"

    def as_dict(self):
        return dataclasses.asdict(self)


@dataclasses.dataclass
class RunState:
    """what a decider may read about the run (it must not read anything else about outcomes)"""
    hour: str
    warmup: bool
    day: str
    counts: collections.Counter = dataclasses.field(default_factory=collections.Counter)


class Decider(Protocol):
    def decide(self, hour_rows: Sequence[Mapping[str, Any]], state: RunState) -> list: ...


class C1nfDecider:
    """C1-NF through the pinned tools.c1nf_shadow.Shadow (decisions only). `engine` is the c1nf_features engine (production: tools.c1nf_shadow.build_engine(ledger, v_source="event")),
    `models` a ModelSet. The shadow's outcome code path is switched off (pending outcomes are never resolved or written): this class has no price, fill or P&L."""

    def __init__(self, engine, models, *, oracle_bypass=False, exit_s=EXIT_S_DEFAULT, shadow_cls=None, stream_expire_s="live", prune=True):
        from tools import c1nf_shadow as CS
        self.CS = CS
        # stream_expire_s: the live shadow's own setting (run_live: EXPIRE_S = 600): engine.expire runs on the stream clock, which frees pools, windows prints and bounds every engine table. Without it a replay
        # never expires anything (Shadow.tick is never called) and memory grows with the hours processed (job #2914: OOM at 131 warm-up hours). None = off (tests of old-vs-new equality only).
        self.stream_expire_s = CS.EXPIRE_S if stream_expire_s == "live" else stream_expire_s
        self.prune, self.pruned = prune, collections.Counter()

        class _NoOutcomeShadow(CS.Shadow):
            def _resolve_due(self):  # no outcome code path (decisions only)
                self.pending.clear()

        self.sink = CS.MemorySink()
        self.exit_s = float(exit_s)
        self._ms = 0
        self.shadow = (shadow_cls or _NoOutcomeShadow)(engine, models, self.sink, replay=False, wall=lambda: self._ms, seal_start_ms=None if oracle_bypass else CS.SEAL_START_MS,
                                                         stream_expire_s=self.stream_expire_s)
        self._resumed = False
        self._last = (None, None)  # (t_recv_ms, slot) of the last row fed
        self.counters_prev = collections.Counter()
        self.adapter_stats = collections.Counter()

    PRUNE_FIRST_S = 26 * 3600  # a pool decides only from graduation + 10 min to + 24 h (c1nf_features GRID_START / GRID_END) and its first print is within seconds of the graduation: 26 h is past every decision

    def prune_shadow(self):
        """Drop the shadow's per-pool and per-mint tables for pools that can no longer be decided (first print older than PRUNE_FIRST_S) and for expired re-entry book entries. Only tables read at a decision of an ALIVE
        pool are touched: first_ms (seal check, pick state), first_slot and v0_bad (v0-gap check of a pool's first prints), last_print and before_slot (decision state), book (re-entry block, REENTRY_S). Decisions are unchanged
        (md5-equal on the long fixture in tools/test_c1nf_forward_sim.py). Returns the number of pools dropped."""
        sh = self.shadow
        bt = sh.hw_bt
        if bt is None:
            return 0
        cut_ms = (bt - self.PRUNE_FIRST_S) * 1000
        old = [p for p, t in sh.first_ms.items() if t < cut_ms]
        for p in old:
            sh.first_ms.pop(p, None)
            sh.first_slot.pop(p, None)
            sh.last_print.pop(p, None)
            sh.before_slot.pop(p, None)
            sh.v0_bad.discard(p)
        stale = [mnt for mnt, t in sh.book.items() if t + self.CS.REENTRY_S + 120 < bt]
        for mnt in stale:
            del sh.book[mnt]
        self.pruned["pools"] += len(old)
        self.pruned["book"] += len(stale)
        return len(old)

    def decide(self, hour_rows, state):
        sh = self.shadow
        if state.warmup:
            sh.decide_enabled = False
        elif not self._resumed:
            self._resumed = True
            sh.resume_after_bootstrap()  # decisions on from the next whole minute after the warm-up
        out = []
        for i, row in enumerate(hour_rows):
            a = arena_row_to_c1nf(row)
            if a is None:
                self.adapter_stats["rows_not_adapted"] += 1
            else:
                kind, r = a
                if _isint(row.get("t_recv_ms")):
                    self._ms = row["t_recv_ms"]
                if kind == "gap":
                    sh.note_follower_gap(r)
                else:
                    sh.feed(r, kind)
            for p in self.sink.of("c1nf_pick"):
                if state.warmup:
                    continue  # cannot happen (decisions are off); never an entry in a warm-up hour
                out.append(Entry(pool=p["pool"], mint=p["mint"], at_row=i, decision_ms=int(row["t_recv_ms"]), decision_slot=int(p["SD_slot"]), exit_after_s=self.exit_s,
                                 meta={"pred": p["pred"], "h_top1": p["h_top1"], "model_sha": p["model_sha"], "feature_hash": p["feature_hash"], "decision_T_ms": p["decision_T_ms"],
                                       "SD_slot": p["SD_slot"], "q_lamports": p["q_lamports"], "v_lamports": p["v_lamports"]}))
            self.sink.records.clear()
            if _isint(row.get("t_recv_ms")) and _isint(row.get("slot")):
                self._last = (row["t_recv_ms"], row["slot"])
        cnt = collections.Counter(sh.c)
        state.counts.update({k: v - self.counters_prev.get(k, 0) for k, v in cnt.items() if v != self.counters_prev.get(k, 0)})
        state.counts.update(self.adapter_stats)
        self.adapter_stats.clear()
        self.counters_prev = cnt
        if self.prune:
            self.prune_shadow()
        return out


# ------------------------------------------------------------------------------------------------------------------------------ forward wallet ledger
def tree_files_md5(root):
    """md5 over (relative path, bytes) of every file under root (dot files skipped), sorted: the wallet-ledger state compared across the twin / fee-beside processes"""
    h = hashlib.md5()
    root = Path(root)
    if not root.is_dir():
        return h.hexdigest()
    for f in sorted(p for p in root.rglob("*") if p.is_file() and not p.name.startswith(".")):
        h.update(str(f.relative_to(root)).encode() + b"\0")
        with open(f, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    return h.hexdigest()


def compare_books(out_a, out_b):
    """twin / fee-beside comparison (QP condition on #1415): -> {"decision_md5_first_mismatch": hour | None, "wallet_md5_first_mismatch": day | None, "hours_compared": n, "days_compared": n}.
    From the first mismatching hour (or day) the other book's columns are dropped; the deciding book alone stands. Reads only md5 files."""
    def jl(p):
        return [json.loads(ln) for ln in Path(p).read_text().splitlines() if ln.strip()] if Path(p).exists() else []
    a, b = {r["hour"]: r["decision_md5"] for r in jl(Path(out_a) / "md5-by-hour.jsonl")}, {r["hour"]: r["decision_md5"] for r in jl(Path(out_b) / "md5-by-hour.jsonl")}
    common = sorted(set(a) & set(b))
    dm = next((h for h in common if a[h] != b[h]), None)
    wa, wb = {r["day"]: r["md5"] for r in jl(Path(out_a) / "wallet-ledger-md5.jsonl")}, {r["day"]: r["md5"] for r in jl(Path(out_b) / "wallet-ledger-md5.jsonl")}
    wcommon = sorted(set(wa) & set(wb))
    wm = next((d for d in wcommon if wa[d] != wb[d]), None)
    return {"decision_md5_first_mismatch": dm, "wallet_md5_first_mismatch": wm, "hours_compared": len(common), "days_compared": len(wcommon)}


class LedgerStep:
    """Builds the as-of wallet ledger forward. note_hour(hour, adapted trade rows) after each processed hour (warm-up included); a day's ledger is built only when all 24 hours of that
    UTC day were noted (builder(day, src_dir, ledger_root)); otherwise `ledger_day_incomplete` counts it. `builder` is injectable: the default calls tools/c1nf_wallet_ledger
    (build_day with the tip adapter over `src_dir`, then build_asof for the next day) and needs the pinned duckdb; tests pass a fake."""

    def __init__(self, src_root, ledger_root, builder=None):
        self.src_root, self.ledger_root, self.builder = Path(src_root), Path(ledger_root), builder or default_ledger_builder
        self.hours = collections.defaultdict(set)
        self.built, self.incomplete = [], []
        self.md5s = {}  # day -> md5 of the wallet-ledger tree right after that day's build

    TIP_FIELDS = ("venue", "mint", "trader", "side", "sol_lamports", "signature", "event_index")  # the columns c1nf_wallet_ledger's tip reader selects (TIP_JSON_COLUMNS); nothing else is stored

    def open_hour(self, hour):
        """a streaming writer for one hour's adapted trade rows: .write(row) one by one, .close() ends the hour (and closes the day after T23). Only TIP_FIELDS are written."""
        day = hour[:10]
        d = self.src_root / day
        d.mkdir(parents=True, exist_ok=True)
        step = self

        class _W:
            def __init__(self):
                self.fh = open(d / ("trades-%s.jsonl" % hour), "w")

            def write(self, r):
                self.fh.write(json.dumps({k: r.get(k) for k in step.TIP_FIELDS}, sort_keys=True, separators=(",", ":")) + "\n")

            def close(self):
                self.fh.close()
                step.hours[day].add(hour)
                if hour.endswith("T23"):
                    step.close_day(day)
        return _W()

    def note_hour(self, hour, trade_rows):
        w = self.open_hour(hour)
        for r in trade_rows:
            w.write(r)
        w.close()

    def close_day(self, day):
        """build the day if all 24 hours were noted; then the day's scratch rows are deleted either way (they are not needed again) and its hour set is released"""
        import shutil
        try:
            if len(self.hours[day]) != 24:
                self.incomplete.append(day)
                return False
            self._build(day)
            return True
        finally:
            shutil.rmtree(self.src_root / day, ignore_errors=True)
            self.hours.pop(day, None)

    def _build(self, day):
        self.builder(day, self.src_root / day, self.ledger_root)
        self.built.append(day)
        self.md5s[day] = tree_files_md5(self.ledger_root)
        return True


DUCKDB_MEM_GB, DUCKDB_THREADS = 4.0, 2  # memory_limit and threads of the day build (job #2912 died OOM with the 6 GB / 3 thread default and no spill dir)


def default_ledger_builder(day, src_dir, ledger_root):
    from tools import c1nf_wallet_ledger as W
    tmp = Path(ledger_root).parent / "duckdb-tmp"  # duckdb spill under the book's out_root (wallet_ledger_root is guarded to sit there)
    W.build_day(day, "tip", Path(ledger_root), tip_dirs=[src_dir], allow_open_day=True, threads=DUCKDB_THREADS, mem_gb=DUCKDB_MEM_GB, tmp_dir=tmp)
    W.build_asof(Path(ledger_root), W.next_day(day))
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------------------------------------------------------------------ manifest
def load_manifest(path):
    try:
        m = json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        raise Refused("manifest unreadable: %s" % e)
    return check_manifest(m)


def check_manifest(m):
    if not isinstance(m, dict):
        raise Refused("manifest must be a JSON object")
    extra, missing = sorted(set(m) - MANIFEST_KEYS), sorted(REQUIRED_KEYS - set(m))
    if extra or missing:
        raise Refused("manifest keys: unknown %s, missing %s" % (extra, missing))
    if not isinstance(m["book_id"], str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", m["book_id"]):
        raise Refused("book_id must match [A-Za-z0-9_.-]{1,64}")
    if not isinstance(m["title"], str):
        raise Refused("title must be a string")
    if m["decider"] not in DECIDERS:
        raise Refused("decider must be one of %s" % (list(DECIDERS),))
    if m["decider"] in U1_DECIDERS:
        u1 = m.get("u1")
        if not (isinstance(u1, dict) and isinstance(u1.get("table_a"), str) and set(u1) <= {"table_a", "table_b", "labels", "table_a_sha256", "table_b_sha256", "labels_sha256", "placebo_plan", "placebo_plan_sha256", "hr_exclusion"}):
            raise Refused("decider %s needs manifest key u1 = {table_a, table_a_sha256, [table_b, table_b_sha256], [labels, labels_sha256], [placebo_plan, placebo_plan_sha256], hr_exclusion} (file paths and sha256)" % m["decider"])
        for k in ("table_a", "table_b", "labels"):  # QP 1117 Q10: a file key needs its sha256 key (and no sha256 key without its file); build_decider refuses on a mismatch
            if (k in u1) != (k + "_sha256" in u1) or (k + "_sha256" in u1 and not (isinstance(u1[k + "_sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", u1[k + "_sha256"]))):
                raise Refused("u1.%s and u1.%s_sha256 must be given together (64 hex)" % (k, k))
        if u1.get("hr_exclusion") is not True:
            raise Refused("u1.hr_exclusion must be true: the holder-rewards exclusion is required (QP ADD1 4); the runner does not start without it")
        if m["decider"] == "u1_placebo" and not (isinstance(u1.get("placebo_plan"), str) and isinstance(u1.get("placebo_plan_sha256"), str)):
            raise Refused("decider u1_placebo needs u1.placebo_plan and u1.placebo_plan_sha256 (the plan is written and hashed before any placebo price)")
    elif "u1" in m:
        raise Refused("manifest key u1 is only for the u1 deciders")
    st = m["stakes_sol"]
    if not (isinstance(st, list) and st and all(isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0 for x in st)) or any(x > 2.0 for x in st) or any(b <= a for a, b in zip(st, st[1:])):
        raise Refused("stakes_sol: non-empty, each value in (0, 2.0], strictly ascending")
    if not (_isint(m["send_lamports"]) and m["send_lamports"] >= 0):
        raise Refused("send_lamports must be a non-negative integer")
    for k in ("tape_dir", "out_root"):
        if not isinstance(m[k], str) or not m[k]:
            raise Refused("%s must be a path string" % k)
    parse_hour(m["start_hour"])
    w = m.get("warmup_hours", None)
    if w is not None and not (_isint(w) and MIN_WARMUP_H <= w <= WARMUP_MAX_H):
        raise Refused("warmup_hours must be an integer in [%d, %d] (default: the whole window W)" % (MIN_WARMUP_H, WARMUP_MAX_H))
    if m["send_lamports"] not in SENDS:
        raise Refused("send_lamports must be one of %s (505,000 deciding, 160,000 beside)" % (list(SENDS),))
    es = m.get("exit_s", EXIT_S_DEFAULT)
    es_max = EXIT_S_MAX[m["decider"]]
    if isinstance(es, bool) or not isinstance(es, (int, float)) or not 1 <= es <= es_max:
        raise Refused("exit_s must be a number of seconds in [1, %d] for decider %s" % (es_max, m["decider"]))
    m.setdefault("pricing_lat_entry_s", LAT_DECIDING_S)
    if True:
        v = m["pricing_lat_entry_s"]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v not in O.LATENCY_VALUES_S:
            raise Refused("pricing_lat_entry_s must be one of the declared values %s" % (list(O.LATENCY_VALUES_S),))
    if m.get("rent_mode", "stranded") not in ("stranded", "refunded"):
        raise Refused("rent_mode must be 'stranded' (2,039,280 lamports subtracted per fill: the deciding mode until C1-NF's live ATA close is verified, ruling 2.5) or 'refunded'")
    mh = m.get("model_manifest_sha256")
    if mh is not None and not (isinstance(mh, str) and re.fullmatch(r"[0-9a-f]{64}", mh)):
        raise Refused("model_manifest_sha256 must be 64 hex")
    dp = m.get("duckdb_pin")
    if dp is not None and not (isinstance(dp, dict) and set(dp) == {"version", "sha256"} and isinstance(dp["version"], str) and re.fullmatch(r"[0-9a-f]{64}", str(dp["sha256"]))):
        raise Refused("duckdb_pin must be {version: str, sha256: 64 hex of the installed duckdb module file}")
    po = m.get("pick_oracle")
    if po is not None and not (isinstance(po, str) and po.startswith("none: EXP-022 withdrawn")):
        raise Refused("pick_oracle must be a string starting 'none: EXP-022 withdrawn' (the runner never imports the oracle)")
    fd = m.get("feed_delay_slots", R.DEFAULT_FEED_DELAY_SLOTS)
    if not (_isint(fd) and 0 <= fd <= 100):
        raise Refused("feed_delay_slots must be a whole number of slots")
    return m


def manifest_sha256(m):
    return hashlib.sha256(json.dumps(m, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def hours_needed(m):
    """-> (warm-up hour keys, S in ms). W = [max(2026-11-03T03Z, S - 336 h), S); warmup_hours (a manifest key) may only shorten it, never below MIN_WARMUP_H. Refused when the window
    before S holds less than MIN_WARMUP_H hours (S moves later)."""
    start = parse_hour(m["start_hour"])
    avail = (start - parse_hour(WARMUP_START_MIN)) // HOUR_MS
    w = min(WARMUP_MAX_H, avail) if m.get("warmup_hours") is None else m["warmup_hours"]
    need = max(MIN_WARMUP_H, WARMUP_MIN_BY_DECIDER.get(m["decider"], 0))
    if w > avail or w < need:
        raise Refused("warm-up window: %d h wanted, %d h available from %s (minimum %d for decider %s): S moves later" % (w, avail, WARMUP_START_MIN, need, m["decider"]))
    return [hour_key(start - (w - i) * HOUR_MS) for i in range(w)], start


def check_seal(m):
    """the pick-oracle skip is a manifest key (pick_oracle), valid only from S: S itself must be at or after SEAL_FIRST_HOUR (warm-up hours take no decision, so the oracle never matters there).
    Without the key the shadow keeps its seal and, with no oracle, withholds every pool first seen after 2026-10-16T01Z (fail closed). Never --no-seal."""
    if m.get("pick_oracle") is None:
        return
    if parse_hour(m["start_hour"]) < parse_hour(SEAL_FIRST_HOUR):
        raise Refused("pick_oracle skip refused: S %s is before %s (the skip is for hours from S, EXP-022 withdrawn 2026-10-15)" % (m["start_hour"], SEAL_FIRST_HOUR))


def guard_paths(m, roots):
    """tape_dir under the keeper root, out_root under the sim root (roots = (tape_root, sim_root)), disjoint, never a forbidden path word, never inside the arena output root"""
    tape_root, sim_root = roots
    t, o = os.path.realpath(m["tape_dir"]), os.path.realpath(m["out_root"])
    for k, pth in (("tape_dir", t), ("out_root", o), ("tape_dir", str(m["tape_dir"])), ("out_root", str(m["out_root"]))):
        for w in FORBIDDEN_PATH_WORDS:
            if w in pth.replace(tape_root, "").replace(sim_root, ""):
                raise Refused("%s: forbidden path word %r" % (k, w))
    if not (t == tape_root or t.startswith(tape_root + os.sep)):
        raise Refused("tape_dir must be %s" % tape_root)
    if not (o.startswith(sim_root + os.sep) or o == sim_root):
        raise Refused("out_root must sit under %s" % sim_root)
    if R._inside(m["out_root"], R.ARENA_OUTPUT_ROOT):
        raise Refused("refusing to write inside the arena output root")
    if o == t or o.startswith(t + os.sep) or t.startswith(o + os.sep):
        raise Refused("tape_dir and out_root must be disjoint")


def duckdb_identity():
    """(version, sha256 of the installed duckdb module file): the wallet ledger's trader keying depends on the duckdb build (tools/c1nf_wallet_ledger check_hash_pin)"""
    import duckdb
    return duckdb.__version__, hashlib.sha256(Path(duckdb.__file__).read_bytes()).hexdigest()


MODEL_DIR = REPO / "ARTIFACTS" / "c1nf_model"


def check_production_pins(m, fixture=False):
    """pre-S guards (QP/reviewer on #1415). A production manifest MUST carry duckdb_pin and model_manifest_sha256; `fixture=True` (a test seam, not a CLI flag or manifest key) skips the
    requirement. Always: model_manifest sits inside ARTIFACTS/c1nf_model (fixture: anywhere) and its file sha256 equals the pinned value; wallet_ledger_root sits under out_root and carries
    no forbidden word."""
    if m["decider"] in PLACEBO_DECIDERS or m["decider"] in U1_DECIDERS:  # U4 / U2 / U1 load no model and build no wallet ledger (ruling 0.1): there is nothing to pin, and a model or ledger key is refused
        for k in ("duckdb_pin", "model_manifest", "model_manifest_sha256", "wallet_ledger_root", "pick_oracle"):
            if m.get(k) is not None:
                raise Refused("%s is not allowed for decider %s (no model, no wallet ledger, no pick oracle)" % (k, m["decider"]))
        return
    if not fixture:
        for k in ("duckdb_pin", "model_manifest_sha256"):
            if m.get(k) is None:
                raise Refused("%s is required in a production manifest" % k)
    mm = Path(m.get("model_manifest") or MODEL_DIR / "manifest.json")
    real = os.path.realpath(mm)
    if not fixture and not real.startswith(os.path.realpath(MODEL_DIR) + os.sep):
        raise Refused("model_manifest must sit inside %s" % MODEL_DIR)
    if m.get("model_manifest_sha256") is not None:
        try:
            got = hashlib.sha256(Path(real).read_bytes()).hexdigest()
        except OSError as e:
            raise Refused("model_manifest unreadable: %s" % e)
        if got != m["model_manifest_sha256"]:
            raise Refused("model_manifest sha256 %s != pinned %s: refused" % (got, m["model_manifest_sha256"]))
    wl = m.get("wallet_ledger_root")
    if wl is not None:
        wr, orr = os.path.realpath(wl), os.path.realpath(m["out_root"])
        if not wr.startswith(orr + os.sep):
            raise Refused("wallet_ledger_root must sit under out_root")
        for w in FORBIDDEN_PATH_WORDS:
            if w in wr.replace(orr, ""):
                raise Refused("wallet_ledger_root: forbidden path word %r" % w)


def check_duckdb_pin(m, probe=None):
    dp = m.get("duckdb_pin")
    if dp is None:
        return
    try:
        got = (probe or duckdb_identity)()
    except Exception as e:  # noqa: BLE001 - no duckdb, no start
        raise Refused("duckdb_pin set but duckdb cannot be inspected: %s" % type(e).__name__)
    if tuple(got) != (dp["version"], dp["sha256"]):
        raise Refused("duckdb differs from the manifest pin: have %s, pinned %s: refused" % (got, (dp["version"], dp["sha256"])))


def link_hour(m, out_root, ms):
    """the tape input of one hour: a symlink under out_root/tapelink to the keeper's file (the runner reads only through it). -> path or None when the keeper has no such hour"""
    src = hour_file(m["tape_dir"], ms)
    if src is None:
        return None
    real, root = os.path.realpath(src), os.path.realpath(m["tape_dir"])
    if not real.startswith(root + os.sep):
        raise Refused("hour file %s resolves to %s, outside the keeper dir %s: refused" % (src.name, real, root))
    for w in FORBIDDEN_PATH_WORDS:
        if w in real.replace(root, ""):
            raise Refused("hour file %s resolves to a forbidden path word %r" % (src.name, w))
    d = Path(out_root) / "tapelink"
    d.mkdir(parents=True, exist_ok=True)
    dst = d / src.name
    if not dst.is_symlink():
        os.symlink(os.path.realpath(src), dst)
    return dst


def code_sha256(extra=()):
    files = {}
    for rel in ("scripts/research/c1nf_forward_sim.py", "scripts/research/osb_follow.py", "scripts/research/floor_gap_range_engine.py", "tools/arena_paper.py", "tools/c1nf_shadow.py",
                "tools/c1nf_features.py", "tools/c1nf_wallet_ledger.py", "tools/arena_stack_v2.py", "scripts/research/u1_forward_sim.py", "scripts/research/u1_funder_fetch.py"):
        files[rel] = hashlib.sha256((REPO / rel).read_bytes()).hexdigest()
    files.update(dict(extra))
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# ------------------------------------------------------------------------------------------------------------------------------ book
class Book:
    """pricing side of one run: GapRangeEngine + the entries / exits the decider asked for"""

    def __init__(self, m, run_dir, code_sha, engine_kw=None):
        self.m, self.run_dir = m, Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.bot = "c1nf-fwd-" + re.sub(r"[^A-Za-z0-9_.-]", "-", m["book_id"])[:40]
        ledger, registry = str(self.run_dir / "ledger"), str(self.run_dir / "bots.json")
        start_ms = parse_hour(m["start_hour"])
        (self.run_dir / "variant.json").write_text(json.dumps({"book_id": m["book_id"], "decider": m["decider"], "exit_s": m.get("exit_s", EXIT_S_DEFAULT),
                                                                "pricing_lat_entry_s": m.get("pricing_lat_entry_s"), "send": m["send_lamports"], "bot_id": self.bot}, indent=1, sort_keys=True) + "\n")
        self._register(registry, code_sha, start_ms)
        cfg = dataclasses.replace(stack_config_for(2, m.get("feed_delay_slots", R.DEFAULT_FEED_DELAY_SLOTS)), send=m["send_lamports"], rent=(0 if m.get("rent_mode", "stranded") == "refunded" else AP.RENT_LAMPORTS),
                                  stakes_sol=tuple(float(x) for x in m["stakes_sol"]))
        self.clock = start_ms
        kw = dict(engine_kw or {})
        if "pricing_lat_entry_s" in m:
            kw["pricing_lat_entry_s"] = float(m["pricing_lat_entry_s"])
        kw.setdefault("now_fn", lambda: self.clock)
        kw.setdefault("code_state_fn", lambda: (code_sha[:40], False))
        self.engine = GapRangeEngine(ledger, registry, cfg=cfg, **kw)
        self.sells = []  # heap of (exit_ms, position_id)
        self.c = collections.Counter()
        self.bots = {"main": self.bot}
        if m["decider"] in PLACEBO_DECIDERS:  # ruling 0.2 item 4 / 0.7: the placebo is a second bot_id priced by the same engine; the decider's view never includes it
            self.bots["placebo"] = self.bot + "-plc"
            self._register(registry, code_sha, start_ms, self.bots["placebo"])
        self.pid_bot = {}    # position_id -> engine bot id
        self.exit_kind = {}  # position_id -> "timed" | "requested" (the decider's own exit kind; known without pricing)
        self.n_entries = collections.Counter()  # (bot key, decision day) -> entries handed to the engine
        self.open = {}       # (bot key, pool) -> position_id of the open position
        self.sold = set()    # position ids that already have a sell (timed or requested)

    def _register(self, registry, code_sha, start_ms, bot=None):
        os.environ[AP.TEST_ENV] = "1"  # scratch registry under the output root only (as osb_follow's scratch_register): a replay cannot register in the real clock's past
        try:
            AP.register_bot(bot or self.bot, code_sha[:40], start_ms, registry, deciding_stake=AP.STAKES_SOL[0], model_ids=[], now_ms=start_ms - 1, verify_sha=False)
        finally:
            os.environ.pop(AP.TEST_ENV, None)

    def _due_sells(self, until_ms):
        while self.sells and self.sells[0][0] <= until_ms:
            ems, pid = heapq.heappop(self.sells)
            if pid in self.sold:  # a requested sell (stop / take / share fall) already closed it
                continue
            self.clock = ems
            self.sold.add(pid)
            try:
                self.engine.sell(self.pid_bot[pid], pid, 1.0, ems, max(self.engine.hw_slot, 0), "time_exit")
                self.c["sells"] += 1
            except AP.ArenaRefused as e:
                self.c["sell_refused_" + str(e).split(":")[0][:30].replace(" ", "_")] += 1

    def buy(self, e):
        if e.decision_ms < parse_hour(self.m["start_hour"]):  # nothing is decided or priced before S (ruling 1.2)
            raise Refused("entry decided before S (%d): refused" % e.decision_ms)
        self._due_sells(e.decision_ms)
        self.clock = e.decision_ms
        if e.bot not in self.bots:
            raise Refused("entry for bot %r but this book has only %s" % (e.bot, sorted(self.bots)))
        if (e.bot, e.pool) in self.open and self.open[(e.bot, e.pool)] not in self.sold:  # one open position per pool per bot (ruling 0.4)
            self.c["buy_refused_open_position"] += 1
            return None
        try:
            pid = self.engine.buy(self.bots[e.bot], e.pool, float(self.m["stakes_sol"][0]), e.decision_ms, e.decision_slot, e.meta)
        except AP.ArenaRefused as exc:
            self.c["buy_refused_" + str(exc).split(":")[0][:30].replace(" ", "_")] += 1
            return None
        self.c["buys" if e.bot == "main" else "buys_" + e.bot] += 1
        self.pid_bot[pid] = self.bots[e.bot]
        self.exit_kind[pid] = "timed"
        self.n_entries[(e.bot, day_key(e.decision_ms))] += 1
        self.open[(e.bot, e.pool)] = pid
        # C1-NF (ruling 2.3): the sell is decided at entry landing + exit_s. U4 / U2 (ruling 1.3, 2.3): decision instant + exit_s
        land_ms = 0 if e.exit_from_decision else int(round(float(self.m["pricing_lat_entry_s"]) * 1000))
        heapq.heappush(self.sells, (e.decision_ms + land_ms + int(round(e.exit_after_s * 1000)), pid))
        return pid

    def sell_req(self, s):
        """a decider-requested sell of the open (bot, pool) position (ruling 0.2 item 3); nothing is decided before S"""
        if s.decision_ms < parse_hour(self.m["start_hour"]):
            raise Refused("sell decided before S (%d): refused" % s.decision_ms)
        if s.bot not in self.bots:
            raise Refused("sell for bot %r but this book has only %s" % (s.bot, sorted(self.bots)))
        pid = self.open.get((s.bot, s.pool))
        if pid is None or pid in self.sold:
            self.c["sell_req_no_open_position"] += 1
            return
        self._due_sells(s.decision_ms)
        if pid in self.sold:
            return
        self.clock = s.decision_ms
        try:
            self.engine.sell(self.bots[s.bot], pid, 1.0, s.decision_ms, s.decision_slot, s.reason)
        except AP.ArenaRefused as e:  # e.g. decided before the slower entry leg lands: the timed exit stays
            self.c["sell_req_refused_" + str(e).split(":")[0][:30].replace(" ", "_")] += 1
            return
        self.sold.add(pid)
        self.exit_kind[pid] = "requested"
        self.c["sell_req_" + re.sub(r"[^a-z_]", "", s.reason)[:12]] += 1

    def run_hour(self, rows, entries, sells=()):
        by_row = collections.defaultdict(list)
        for e in entries:
            by_row[e.at_row].append(e)
        sells_by_row = collections.defaultdict(list)
        for s in sells:
            sells_by_row[s.at_row].append(s)
        n = 0
        for i, row in enumerate(rows):
            n = i + 1
            t = row.get("t_recv_ms")
            if _isint(t):
                self._due_sells(t)
            for s in sells_by_row.get(i, ()):
                self.sell_req(s)
            for e in by_row.get(i, ()):
                self.buy(e)
            self.engine.on_row(row)
            if _isint(t):
                self.clock = t
        for s in sells_by_row.get(n, ()):  # a trigger on the hour's last row: handed over at the hour end
            self.sell_req(s)
        self.engine.prune()
        self.engine.trim_history()

    def gap_exclusion_split(self):
        """QP-RULING-1116-u4-u2-p81-forward-specs-ADD1 A1 (counters only, no pricing): `feed_gap*` exclusions of the engine's strict exclusion, split by the decider's own exit kind of the position
        (timed vs requested sell), and the excluded share of entries per decision day, per bot. Read from the engine's ledger `excluded` rows (one position = one row per stake: deduplicated)."""
        key_of = {v: k for k, v in self.bots.items()}
        seen, per = set(), collections.defaultdict(lambda: collections.Counter())
        for f in sorted((self.run_dir / "ledger").rglob("trades-*.jsonl")):
            for ln in f.read_text().splitlines():
                r = json.loads(ln)
                if r.get("kind") != "excluded" or not str(r.get("exclusion_reason", "")).startswith("feed_gap") or r["position_id"] in seen:
                    continue
                seen.add(r["position_id"])
                dm = (r.get("decision_ms") or {}).get("entry")
                k = key_of.get(r["bot_id"], r["bot_id"])
                per[k][("feed_gap_" + self.exit_kind.get(r["position_id"], "timed"), day_key(dm) if _isint(dm) else "?")] += 1
        out = {}
        for k in sorted(set(per) | {b for b, _ in self.n_entries}):
            days = sorted({d for (b, d) in self.n_entries if b == k} | {d for (_, d) in per.get(k, {})})
            tot = collections.Counter()
            rows = {}
            for d in days:
                t, q, n = per[k][("feed_gap_timed", d)], per[k][("feed_gap_requested", d)], self.n_entries.get((k, d), 0)
                rows[d] = {"entries": n, "feed_gap_timed": t, "feed_gap_requested": q, "excluded_share": round((t + q) / n, 4) if n else None}
                tot.update({"entries": n, "feed_gap_timed": t, "feed_gap_requested": q})
            out[k] = {"total": dict(tot), "by_day": rows}
        mt = out.get("main", {"total": {}})["total"]  # QP #1420: the disclosure counts the book's own trades only, never the placebo / random-pick trades
        out["disclosure"] = GAP_DISCLOSURE.format(n=mt.get("feed_gap_timed", 0) + mt.get("feed_gap_requested", 0), N=mt.get("entries", 0), timed=mt.get("feed_gap_timed", 0),
                                                  requested=mt.get("feed_gap_requested", 0))
        return out

    def open_positions(self):
        return len(self.engine.positions)


# ------------------------------------------------------------------------------------------------------------------------------ follow
def default_decider(m, ledger_provider=None):
    if m["decider"] in U1_DECIDERS:
        import u1_forward_sim as U1
        return U1.build_decider(m)
    if m["decider"] in PLACEBO_DECIDERS:
        import u4_u2_deciders as UD
        return UD.make_decider(m, hour_to_ms=parse_hour)
    from tools import c1nf_shadow as CS
    mm = m.get("model_manifest") or str(REPO / "ARTIFACTS" / "c1nf_model" / "manifest.json")
    models = CS.ModelSet.from_manifest(mm)
    if ledger_provider is None:
        ledger_provider = CS.AsofDirLedger(m.get("wallet_ledger_root") or str(Path(m["out_root"]) / "wallet-ledger"))
    engine = CS.build_engine(ledger_provider, v_source="event")
    return C1nfDecider(engine, models, oracle_bypass=m.get("pick_oracle") is not None, exit_s=m.get("exit_s", EXIT_S_DEFAULT))


_DUCKDB_PROBE = [None]  # test seam


def follow(manifest_path_or_dict, once=False, max_hours=None, *, sleep_fn=time.sleep, _roots=None, decider_factory=None, ledger_builder=None, engine_kw=None, fixture=False):
    """-> exit code. _roots, decider_factory, ledger_builder, engine_kw are test seams (the CLI does not expose them)."""
    try:
        m = check_manifest(dict(manifest_path_or_dict)) if isinstance(manifest_path_or_dict, dict) else load_manifest(manifest_path_or_dict)
        return _follow(m, once, max_hours, sleep_fn, _roots or (KEEP_ROOT, SIM_ROOT), decider_factory, ledger_builder, engine_kw, fixture)
    except Refused as e:
        print("c1nf_forward_sim: refused: %s" % e, file=sys.stderr)
        return EXIT_REFUSED
    except Mismatch as e:
        print("c1nf_forward_sim: MD5 MISMATCH: %s" % e, file=sys.stderr)
        return EXIT_MISMATCH
    except (AP.LedgerCorrupt, R.HostFatal) as e:
        print("c1nf_forward_sim: ledger fatal: %s" % str(e)[:300], file=sys.stderr)
        return EXIT_FATAL


def _follow(m, once, max_hours, sleep_fn, roots, decider_factory, ledger_builder, engine_kw, fixture=False):
    check_seal(m)
    check_production_pins(m, fixture)
    check_duckdb_pin(m, _DUCKDB_PROBE[0])
    guard_paths(m, roots)
    out_root = Path(m["out_root"])
    out_root.mkdir(parents=True, exist_ok=True)
    lock = open(out_root / ".c1nf-fwd.lock", "a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise Refused("another c1nf_forward_sim holds %s" % (out_root / ".c1nf-fwd.lock"))
    try:
        return _locked(m, out_root, once, max_hours, sleep_fn, decider_factory, ledger_builder, engine_kw)
    finally:
        import shutil
        shutil.rmtree(out_root / "duckdb-tmp", ignore_errors=True)  # duckdb spill is tape-derived: deleted when the runner stops (QP ADD1 (c)1)
        lock.close()


def _locked(m, out_root, once, max_hours, sleep_fn, decider_factory, ledger_builder, engine_kw):
    sha = manifest_sha256(m)
    sf = out_root / "manifest.sha256"
    if sf.exists():
        if sf.read_text().strip() != sha:
            raise Refused("manifest differs from the one this book started with (%s): a tweak is a new book" % sf.read_text().strip()[:12])
    else:
        sf.write_text(sha + "\n")
    csha, cf = code_sha256(), out_root / "code.sha256"
    if cf.exists():
        if cf.read_text().strip() != csha:
            raise Refused("simulator version changed (code.sha256 %s != %s): a new book" % (cf.read_text().strip()[:12], csha[:12]))
    else:
        cf.write_text(csha + "\n")
    warm, start_ms = hours_needed(m)
    (out_root / "meta.json").write_text(json.dumps({"book_id": m["book_id"], "decider": m["decider"], "start_hour": m["start_hour"], "warmup_hours": warm, "pick_oracle": m.get("pick_oracle"), "pricing_lat_entry_s": m["pricing_lat_entry_s"], "send_lamports": m["send_lamports"],
                                                    "first_processed_hour": warm[0] if warm else m["start_hour"], "note": "warm-up hours feed the decider only: decisions off, no buy, sell, ledger row or outcome"},
                                                   indent=1, sort_keys=True) + "\n")
    stored = O.read_stored(out_root)
    run_dir = O._next_run_dir(out_root)
    book = Book(m, run_dir, csha, engine_kw)
    decider = (decider_factory or default_decider)(m)
    lstep = LedgerStep(out_root / "ledger-src", m.get("wallet_ledger_root") or out_root / "wallet-ledger", ledger_builder)
    verified = not stored
    if verified:
        O._flip(out_root, run_dir)
    last_stored = max(stored) if stored else None
    cursor, done = parse_hour(warm[0]) if warm else start_ms, 0
    status = {"book_id": m["book_id"], "manifest_sha256": sha, "code_sha256": csha, "run": run_dir.name, "stakes_sol": list(m["stakes_sol"])}
    waiting_since = None
    pools_seen, creators_seen, warm_pools = set(), set(), 0
    while True:
        f, nxt = hour_file(m["tape_dir"], cursor), hour_file(m["tape_dir"], cursor + HOUR_MS)
        f = link_hour(m, out_root, cursor) if f is not None else None
        if f is None or nxt is None:  # an hour is closed once the next hour's file exists
            if once or (max_hours is not None and done >= max_hours):
                break
            waiting_since = waiting_since or datetime.now(timezone.utc).isoformat()
            status.update(waiting_for_hour=hour_key(cursor), waiting_since_utc=waiting_since, wall_time_utc=datetime.now(timezone.utc).isoformat())
            O._atomic_json(out_root / "status.json", status)
            sleep_fn(POLL_S)
            continue
        waiting_since = None
        status.pop("waiting_for_hour", None)
        status.pop("waiting_since_utc", None)
        key, end = hour_key(cursor), cursor + HOUR_MS
        is_warm = cursor < start_ms
        st = RunState(hour=key, warmup=is_warm, day=key[:10])
        entries = decider.decide(iter_hour_rows(f), st)  # pass 1: the file is streamed, never held whole (a decider only enumerates its rows)
        sells = decider.take_sells() if hasattr(decider, "take_sells") else []
        if is_warm and (entries or sells):
            raise Refused("a decider returned entries or sells in a warm-up hour (%s): refused" % key)
        use_ledger = m["decider"] == "c1nf"  # the U4 / U2 books never build or read the C1-NF wallet ledger (ruling 0.1)
        lw = lstep.open_hour(key) if use_ledger else None
        seen = {"n": 0}

        def observe(rows_iter):  # pass 2: counts, the scratch ledger rows and (forward hours) the pricing engine see each row once, one at a time
            for r in rows_iter:
                seen["n"] += 1
                if r.get("pool"):
                    pools_seen.add(r["pool"])
                if r.get("type") == "create" and r.get("creator"):
                    creators_seen.add(r["creator"])
                if lw is not None:
                    a = arena_row_to_c1nf(r)
                    if a is not None and a[0] == "trades":
                        lw.write(a[1])
                yield r
        if is_warm:
            for _ in observe(iter_hour_rows(f)):
                pass
            warm_pools = len(pools_seen)
        else:
            book.run_hour(observe(iter_hour_rows(f)), entries, sells)
        st.counts["rows"], st.counts["pools_seen_total"], st.counts["creators_seen_total"] = seen["n"], len(pools_seen), len(creators_seen)
        if use_ledger:
            n_built = len(lstep.built)
            lw.close()
            if len(lstep.built) > n_built and verified and key not in stored:
                with open(out_root / "wallet-ledger-md5.jsonl", "a") as fh:
                    fh.write(canon({"day": lstep.built[-1], "md5": lstep.md5s[lstep.built[-1]], "hour": key}) + "\n")
        dmd5 = hashlib.md5(canon([e.as_dict() for e in entries] + [s.as_dict() for s in sells]).encode()).hexdigest()
        lmd5 = O.tree_md5(run_dir / "ledger") if (run_dir / "ledger").exists() else hashlib.md5(b"").hexdigest()
        if key in stored:
            for fld, got in (("decision_md5", dmd5), ("ledger_md5", lmd5)):
                if stored[key].get(fld) != got:
                    raise Mismatch("hour %s %s: stored %s, replay %s (run %s kept; stored list and `book` untouched)" % (key, fld, stored[key].get(fld), got, run_dir.name))
        if not verified and key == last_stored:
            verified = True
            O._flip(out_root, run_dir)
        if verified and key not in stored:
            with open(out_root / "md5-by-hour.jsonl", "a") as fh:
                fh.write(canon({"hour": key, "end_ms": end, "warmup": is_warm, "decision_md5": dmd5, "ledger_md5": lmd5}) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            if hasattr(decider, "drain_marks"):  # board-safe per-mark records (universe size, buckets, breadth, gate, picks); no pool, mint, price or slot
                with open(out_root / "marks.jsonl", "a") as fh:
                    for rec in decider.drain_marks():
                        fh.write(canon(dict(rec, hour=key)) + "\n")
            with open(out_root / "counts.jsonl", "a") as fh:
                fh.write(canon({"hour": key, "warmup": is_warm, "entries": len(entries), "decider": dict(st.counts), "book": dict(book.c)}) + "\n")
        if verified and not is_warm:
            O._atomic_text(out_root / "decisions.md5", O.decisions_md5(run_dir) + "\n")
        status.update(last_hour=key, last_hour_end_ms=end, warmup_hour=is_warm, wall_time_utc=datetime.now(timezone.utc).isoformat(), active=verified, open_positions=book.open_positions(),
                      ledger_days_built=list(lstep.built), ledger_days_incomplete=list(lstep.incomplete), decision_md5=dmd5, ledger_md5=lmd5, book_counts=dict(book.c))
        O._atomic_json(out_root / "status.json", status)
        if (m["decider"] in PLACEBO_DECIDERS or m["decider"] in U1_DECIDERS) and not is_warm:  # U1: strict exclusion + GAP_DISCLOSURE (QP verdict item 2); no placebo bot, "main" only
            O._atomic_json(out_root / "gap_exclusions.json", book.gap_exclusion_split())
        if is_warm and end == start_ms:  # the last warm-up hour is done: the disclosure carries the count of pools first seen in W
            meta = json.loads((out_root / "meta.json").read_text())
            meta["pools_first_seen_in_warmup"] = warm_pools
            meta["disclosure"] = ("warm-up W = [%s, S), decisions off, no outcome code path; creator and wallet history start at %s, not at tape start as in training; "
                                  "pools first seen in warm-up: %d" % (warm[0], warm[0], warm_pools))
            O._atomic_json(out_root / "meta.json", meta)
        cursor, done = end, done + 1
        if max_hours is not None and done >= max_hours:
            break
    return EXIT_OK


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "--compare":  # c1nf_forward_sim.py --compare OUT_A OUT_B : md5 files only
        print(json.dumps(compare_books(argv[1], argv[2]), sort_keys=True))
        return EXIT_OK
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("manifest")
    ap.add_argument("--once", action="store_true", help="process the closed hours present now, then exit")
    ap.add_argument("--max-hours", type=int, default=None)
    a = ap.parse_args(argv)
    try:
        m = load_manifest(a.manifest)
    except Refused as e:
        print("c1nf_forward_sim: refused: %s" % e, file=sys.stderr)
        return EXIT_REFUSED
    return follow(m, once=a.once, max_hours=a.max_hours)


if __name__ == "__main__":
    raise SystemExit(main())
