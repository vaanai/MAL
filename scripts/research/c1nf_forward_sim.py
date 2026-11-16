#!/usr/bin/env python3
"""c1nf_forward_sim: FORWARD paper simulation of one strategy (first: C1-NF, the frozen C1 LAYA cascade + top-holder cap h_top1 <= 0.5, exit 300 s) on the arena tip feed
(hv7 `tape-<hour>.jsonl[.zst]`, arena_tape_v1 rows). PAPER only; in-sample research, not a gate read. Nothing here says any book makes money.

Design input: /data/mal/hunt-1008/ops/reports/2026-11-16/c1nf-count-feasibility.md (blockers 1-5, "Smallest fix" A-E). Entries are priced by tools/arena_paper.py through
scripts/research/floor_gap_range_engine.GapRangeEngine (the FLOOR open-sim engine: virtual reserve V, feed delay d + k slots, fees and rent as the arena stack), at the strict
book's d + k, or at a declared total chain time with pricing_lat_entry_s (the same LATENCY_VALUES_S as osb_follow). The book files have osb_follow's layout.

Pieces
  adapter    arena_row_to_c1nf(row): hv7 arena row -> the (kind, row) shape tools.c1nf_shadow.Shadow.feed reads (blocker 1). Pure, no I/O.
  decider    plug-in interface: decide(hour_rows, state) -> [Entry]. Entry = what to buy (pool, mint, the row index it is decided before, decision instant and slot, meta).
             C1nfDecider wraps the pinned Shadow (decisions only: the outcome code path of the shadow is switched off). Later strategies implement the same two-line interface.
  ledger     the as-of wallet ledger is built FORWARD (blocker 3): a UTC day's ledger is built only once all 24 hours of that day were processed by this run (warm-up hours
             included, and listed), through tools/c1nf_wallet_ledger (the tip adapter over a scratch copy of the day's adapted trade rows). Until asof-<day> exists the
             engine answers wallet_ok False and the shadow never picks: those minutes are counted as `no_wallet_ledger` refusals, no pick is faked.
  seal       oracle bypass (blocker 4) only behind --oracle-bypass-forward, which refuses (exit 2) any processed hour (start, warm-up included) before 2026-10-16T01Z.
  warm-up    --warmup-hours N: the N hours before start_hour feed the decider's engine with decisions OFF; no buy, sell, ledger row or outcome code path runs for them.
             They are listed in meta.json. Pricing history starts at start_hour.
Output (out_root): manifest.sha256, code.sha256, meta.json, status.json, md5-by-hour.jsonl, decisions.md5, counts.jsonl (per hour: decider counters), runs/NNNN/{ledger/<bot>/trades-DAY.jsonl,
bots.json, variant.json}, `book` -> the active run. Restart: replays from the warm-up start into a new run and compares every stored hour's decision and ledger md5 (exit 4 on a difference).
Exit codes: 0 ok; 2 refused; 3 ledger write fatal; 4 md5 mismatch.
Usage: c1nf_forward_sim.py MANIFEST.json [--once] [--max-hours N] [--warmup-hours N] [--oracle-bypass-forward] [--pricing-lat-entry-s S]
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
HV7_ROOT = "/data2/mal/arena-tip-feed/hv7"
SIM_ROOT = "/data2/mal/scratch/c1nf-forward-sim"
HOUR_MS = 3_600_000
POLL_S = 60
SEAL_FIRST_HOUR = "2026-10-16T01"  # EXP-022 s9: a pool whose first print is at or after this instant needs the pick oracle (tools.c1nf_shadow.SEAL_START_MS)
EXIT_S_DEFAULT = 300.0
WSOL = "So11111111111111111111111111111111111111112"
EXIT_OK, EXIT_REFUSED, EXIT_FATAL, EXIT_MISMATCH = 0, 2, 3, 4
MANIFEST_KEYS = {"book_id", "title", "decider", "stakes_sol", "send_lamports", "start_hour", "warmup_hours", "tape_dir", "out_root", "exit_s", "pricing_lat_entry_s",
                 "oracle_bypass_forward", "feed_delay_slots", "wallet_ledger_root", "model_manifest"}
REQUIRED_KEYS = {"book_id", "title", "decider", "stakes_sol", "send_lamports", "start_hour", "tape_dir", "out_root"}
DECIDERS = ("c1nf",)


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


def read_hour_rows(path):
    """rows of one closed hour file, in file order; a torn last line (no newline) is dropped"""
    path = Path(path)
    if path.name.endswith(".zst"):
        data = subprocess.run(["zstd", "-dc", str(path)], check=True, capture_output=True).stdout
    else:
        data = path.read_bytes()
    out = []
    for ln in data[: data.rfind(b"\n") + 1].splitlines():
        if ln.strip():
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if isinstance(r, dict):
                out.append(r)
    return out


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
    rows the decider had seen). decision_ms / decision_slot: the stream instant of the decision (t_recv_ms and slot of the last row before at_row)."""
    pool: str
    mint: str
    at_row: int
    decision_ms: int
    decision_slot: int
    exit_after_s: float = EXIT_S_DEFAULT
    meta: dict = dataclasses.field(default_factory=dict)

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

    def __init__(self, engine, models, *, oracle_bypass=False, exit_s=EXIT_S_DEFAULT, shadow_cls=None):
        from tools import c1nf_shadow as CS
        self.CS = CS

        class _NoOutcomeShadow(CS.Shadow):
            def _resolve_due(self):  # no outcome code path (decisions only)
                self.pending.clear()

        self.sink = CS.MemorySink()
        self.exit_s = float(exit_s)
        self._ms = 0
        self.shadow = (shadow_cls or _NoOutcomeShadow)(engine, models, self.sink, replay=False, wall=lambda: self._ms, seal_start_ms=None if oracle_bypass else CS.SEAL_START_MS)
        self._resumed = False
        self._last = (None, None)  # (t_recv_ms, slot) of the last row fed
        self.counters_prev = collections.Counter()
        self.adapter_stats = collections.Counter()

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
                out.append(Entry(pool=p["pool"], mint=p["mint"], at_row=i, decision_ms=self._last[0] if self._last[0] is not None else self._ms,
                                 decision_slot=self._last[1] if self._last[1] is not None else p["SD_slot"], exit_after_s=self.exit_s,
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
        return out


# ------------------------------------------------------------------------------------------------------------------------------ forward wallet ledger
class LedgerStep:
    """Builds the as-of wallet ledger forward. note_hour(hour, adapted trade rows) after each processed hour (warm-up included); a day's ledger is built only when all 24 hours of that
    UTC day were noted (builder(day, src_dir, ledger_root)); otherwise `ledger_day_incomplete` counts it. `builder` is injectable: the default calls tools/c1nf_wallet_ledger
    (build_day with the tip adapter over `src_dir`, then build_asof for the next day) and needs the pinned duckdb; tests pass a fake."""

    def __init__(self, src_root, ledger_root, builder=None):
        self.src_root, self.ledger_root, self.builder = Path(src_root), Path(ledger_root), builder or default_ledger_builder
        self.hours = collections.defaultdict(set)
        self.built, self.incomplete = [], []

    def note_hour(self, hour, trade_rows):
        day = hour[:10]
        d = self.src_root / day
        d.mkdir(parents=True, exist_ok=True)
        with open(d / ("trades-%s.jsonl" % hour), "w") as fh:
            for r in trade_rows:
                fh.write(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n")
        self.hours[day].add(hour)
        if hour.endswith("T23"):
            self.close_day(day)

    def close_day(self, day):
        if len(self.hours[day]) != 24:
            self.incomplete.append(day)
            return False
        self.builder(day, self.src_root / day, self.ledger_root)
        self.built.append(day)
        return True


def default_ledger_builder(day, src_dir, ledger_root):
    from tools import c1nf_wallet_ledger as W
    W.build_day(day, "tip", Path(ledger_root), tip_dirs=[src_dir], allow_open_day=True)
    W.build_asof(Path(ledger_root), W.next_day(day))


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
    st = m["stakes_sol"]
    if not (isinstance(st, list) and st and all(isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0 for x in st)) or any(x > 2.0 for x in st) or any(b <= a for a, b in zip(st, st[1:])):
        raise Refused("stakes_sol: non-empty, each value in (0, 2.0], strictly ascending")
    if not (_isint(m["send_lamports"]) and m["send_lamports"] >= 0):
        raise Refused("send_lamports must be a non-negative integer")
    for k in ("tape_dir", "out_root"):
        if not isinstance(m[k], str) or not m[k]:
            raise Refused("%s must be a path string" % k)
    parse_hour(m["start_hour"])
    w = m.get("warmup_hours", 0)
    if not (_isint(w) and 0 <= w <= 72):
        raise Refused("warmup_hours must be an integer in [0, 72]")
    es = m.get("exit_s", EXIT_S_DEFAULT)
    if isinstance(es, bool) or not isinstance(es, (int, float)) or not 1 <= es <= 3600:
        raise Refused("exit_s must be a number of seconds in [1, 3600]")
    if "pricing_lat_entry_s" in m:
        v = m["pricing_lat_entry_s"]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v not in O.LATENCY_VALUES_S:
            raise Refused("pricing_lat_entry_s must be one of the declared values %s" % (list(O.LATENCY_VALUES_S),))
    if not isinstance(m.get("oracle_bypass_forward", False), bool):
        raise Refused("oracle_bypass_forward must be true or false")
    fd = m.get("feed_delay_slots", R.DEFAULT_FEED_DELAY_SLOTS)
    if not (_isint(fd) and 0 <= fd <= 100):
        raise Refused("feed_delay_slots must be a whole number of slots")
    return m


def manifest_sha256(m):
    return hashlib.sha256(json.dumps(m, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def hours_needed(m):
    """-> (warm-up hour keys, first forward hour ms)"""
    start = parse_hour(m["start_hour"])
    w = m.get("warmup_hours", 0)
    return [hour_key(start - (w - i) * HOUR_MS) for i in range(w)], start


def check_seal(m):
    """--oracle-bypass-forward is for forward hours only: every processed hour (warm-up included) must be at or after SEAL_FIRST_HOUR"""
    if not m.get("oracle_bypass_forward", False):
        return
    warm, start = hours_needed(m)
    first = warm[0] if warm else hour_key(start)
    if parse_hour(first) < parse_hour(SEAL_FIRST_HOUR):
        raise Refused("oracle_bypass_forward refused: hour %s is before %s (the bypass is for forward hours only)" % (first, SEAL_FIRST_HOUR))


def guard_paths(m, roots):
    """tape_dir under the hv7 root, out_root under the sim root (roots = (tape_root, out_roots)), disjoint, never inside the arena output root"""
    tape_root, out_root_ok = roots
    t, o = os.path.realpath(m["tape_dir"]), os.path.realpath(m["out_root"])
    if not (t == tape_root or t.startswith(tape_root + os.sep)):
        raise Refused("tape_dir must be %s" % tape_root)
    if not (o.startswith(out_root_ok + os.sep) or o == out_root_ok):
        raise Refused("out_root must sit under %s" % out_root_ok)
    if R._inside(m["out_root"], R.ARENA_OUTPUT_ROOT):
        raise Refused("refusing to write inside the arena output root")
    if o == t or o.startswith(t + os.sep) or t.startswith(o + os.sep):
        raise Refused("tape_dir and out_root must be disjoint")


def code_sha256(extra=()):
    files = {}
    for rel in ("scripts/research/c1nf_forward_sim.py", "scripts/research/osb_follow.py", "scripts/research/floor_gap_range_engine.py", "tools/arena_paper.py", "tools/c1nf_shadow.py",
                "tools/c1nf_features.py", "tools/c1nf_wallet_ledger.py", "tools/arena_stack_v2.py"):
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
        cfg = dataclasses.replace(stack_config_for(2, m.get("feed_delay_slots", R.DEFAULT_FEED_DELAY_SLOTS)), send=m["send_lamports"], stakes_sol=tuple(float(x) for x in m["stakes_sol"]))
        self.clock = start_ms
        kw = dict(engine_kw or {})
        if "pricing_lat_entry_s" in m:
            kw["pricing_lat_entry_s"] = float(m["pricing_lat_entry_s"])
        kw.setdefault("now_fn", lambda: self.clock)
        kw.setdefault("code_state_fn", lambda: (code_sha[:40], False))
        self.engine = GapRangeEngine(ledger, registry, cfg=cfg, **kw)
        self.sells = []  # heap of (exit_ms, position_id)
        self.c = collections.Counter()

    def _register(self, registry, code_sha, start_ms):
        os.environ[AP.TEST_ENV] = "1"  # scratch registry under the output root only (as osb_follow's scratch_register): a replay cannot register in the real clock's past
        try:
            AP.register_bot(self.bot, code_sha[:40], start_ms, registry, deciding_stake=AP.STAKES_SOL[0], model_ids=[], now_ms=start_ms - 1, verify_sha=False)
        finally:
            os.environ.pop(AP.TEST_ENV, None)

    def _due_sells(self, until_ms):
        while self.sells and self.sells[0][0] <= until_ms:
            ems, pid = heapq.heappop(self.sells)
            self.clock = ems
            try:
                self.engine.sell(self.bot, pid, 1.0, ems, max(self.engine.hw_slot, 0), "time_exit")
                self.c["sells"] += 1
            except AP.ArenaRefused as e:
                self.c["sell_refused_" + str(e).split(":")[0][:30].replace(" ", "_")] += 1

    def buy(self, e):
        self._due_sells(e.decision_ms)
        self.clock = e.decision_ms
        try:
            pid = self.engine.buy(self.bot, e.pool, float(self.m["stakes_sol"][0]), e.decision_ms, e.decision_slot, e.meta)
        except AP.ArenaRefused as exc:
            self.c["buy_refused_" + str(exc).split(":")[0][:30].replace(" ", "_")] += 1
            return None
        self.c["buys"] += 1
        heapq.heappush(self.sells, (e.decision_ms + int(round(e.exit_after_s * 1000)), pid))
        return pid

    def run_hour(self, rows, entries):
        by_row = collections.defaultdict(list)
        for e in entries:
            by_row[e.at_row].append(e)
        for i, row in enumerate(rows):
            t = row.get("t_recv_ms")
            if _isint(t):
                self._due_sells(t)
            for e in by_row.get(i, ()):
                self.buy(e)
            self.engine.on_row(row)
            if _isint(t):
                self.clock = t
        self.engine.prune()
        self.engine.trim_history()

    def open_positions(self):
        return len(self.engine.positions)


# ------------------------------------------------------------------------------------------------------------------------------ follow
def default_decider(m, ledger_provider=None):
    from tools import c1nf_shadow as CS
    mm = m.get("model_manifest") or str(REPO / "ARTIFACTS" / "c1nf_model" / "manifest.json")
    models = CS.ModelSet.from_manifest(mm)
    if ledger_provider is None:
        ledger_provider = CS.AsofDirLedger(m.get("wallet_ledger_root") or str(Path(m["out_root"]) / "wallet-ledger"))
    engine = CS.build_engine(ledger_provider, v_source="event")
    return C1nfDecider(engine, models, oracle_bypass=m.get("oracle_bypass_forward", False), exit_s=m.get("exit_s", EXIT_S_DEFAULT))


def follow(manifest_path_or_dict, once=False, max_hours=None, *, sleep_fn=time.sleep, _roots=None, decider_factory=None, ledger_builder=None, engine_kw=None):
    """-> exit code. _roots, decider_factory, ledger_builder, engine_kw are test seams (the CLI does not expose them)."""
    try:
        m = check_manifest(dict(manifest_path_or_dict)) if isinstance(manifest_path_or_dict, dict) else load_manifest(manifest_path_or_dict)
        return _follow(m, once, max_hours, sleep_fn, _roots or (HV7_ROOT, SIM_ROOT), decider_factory, ledger_builder, engine_kw)
    except Refused as e:
        print("c1nf_forward_sim: refused: %s" % e, file=sys.stderr)
        return EXIT_REFUSED
    except Mismatch as e:
        print("c1nf_forward_sim: MD5 MISMATCH: %s" % e, file=sys.stderr)
        return EXIT_MISMATCH
    except (AP.LedgerCorrupt, R.HostFatal) as e:
        print("c1nf_forward_sim: ledger fatal: %s" % str(e)[:300], file=sys.stderr)
        return EXIT_FATAL


def _follow(m, once, max_hours, sleep_fn, roots, decider_factory, ledger_builder, engine_kw):
    check_seal(m)
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
    (out_root / "meta.json").write_text(json.dumps({"book_id": m["book_id"], "decider": m["decider"], "start_hour": m["start_hour"], "warmup_hours": warm, "oracle_bypass_forward": m.get("oracle_bypass_forward", False),
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
    while True:
        f, nxt = hour_file(m["tape_dir"], cursor), hour_file(m["tape_dir"], cursor + HOUR_MS)
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
        rows = read_hour_rows(f)
        st = RunState(hour=key, warmup=is_warm, day=key[:10])
        entries = decider.decide(rows, st)
        if is_warm and entries:
            raise Refused("a decider returned entries in a warm-up hour (%s): refused" % key)
        if not is_warm:
            book.run_hour(rows, entries)
        trades = [a[1] for a in (arena_row_to_c1nf(r) for r in rows) if a is not None and a[0] == "trades"]
        lstep.note_hour(key, trades)
        dmd5 = hashlib.md5(canon([e.as_dict() for e in entries]).encode()).hexdigest()
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
            with open(out_root / "counts.jsonl", "a") as fh:
                fh.write(canon({"hour": key, "warmup": is_warm, "entries": len(entries), "decider": dict(st.counts), "book": dict(book.c)}) + "\n")
        if verified and not is_warm:
            O._atomic_text(out_root / "decisions.md5", O.decisions_md5(run_dir) + "\n")
        status.update(last_hour=key, last_hour_end_ms=end, warmup_hour=is_warm, wall_time_utc=datetime.now(timezone.utc).isoformat(), active=verified, open_positions=book.open_positions(),
                      ledger_days_built=list(lstep.built), ledger_days_incomplete=list(lstep.incomplete), decision_md5=dmd5, ledger_md5=lmd5, book_counts=dict(book.c))
        O._atomic_json(out_root / "status.json", status)
        cursor, done = end, done + 1
        if max_hours is not None and done >= max_hours:
            break
    return EXIT_OK


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("manifest")
    ap.add_argument("--once", action="store_true", help="process the closed hours present now, then exit")
    ap.add_argument("--max-hours", type=int, default=None)
    ap.add_argument("--warmup-hours", type=int, default=None, help="N hours before start_hour with decisions OFF (listed in meta.json); part of the manifest pin")
    ap.add_argument("--oracle-bypass-forward", action="store_true", help="no pick oracle (EXP-022 withdrawn); refuses any hour before 2026-10-16T01Z; part of the manifest pin")
    ap.add_argument("--pricing-lat-entry-s", type=float, default=None, help="total decision-to-landing chain time in s (osb_follow LATENCY_VALUES_S); default: the strict d + k")
    a = ap.parse_args(argv)
    try:
        m = load_manifest(a.manifest)
        if a.warmup_hours is not None:
            m["warmup_hours"] = a.warmup_hours
        if a.oracle_bypass_forward:
            m["oracle_bypass_forward"] = True
        if a.pricing_lat_entry_s is not None:
            m["pricing_lat_entry_s"] = a.pricing_lat_entry_s
    except Refused as e:
        print("c1nf_forward_sim: refused: %s" % e, file=sys.stderr)
        return EXIT_REFUSED
    return follow(m, once=a.once, max_hours=a.max_hours)


if __name__ == "__main__":
    raise SystemExit(main())
