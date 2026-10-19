#!/usr/bin/env python3
"""hv4_memreplay: replay the Haiku v4 (HV4) stub path over closed arena tape hours and measure memory. Paper only. No network, no key, no API call.

Why. The 2026-10-19 stub shakedown (MiScusi #1236) was OOM-killed by its own 6 GB cgroup after about 2 h 32 min. This harness reproduces the runner's
steady-state loop on the real arena tape with the chain layer MOCKED, and samples process RSS plus the size of every container the runner, the state, the
engine and the prefetcher own, every N simulated minutes, so the thing that grows can be named instead of guessed.

What is real: ArenaState on the real tape, ArenaEngine (ledger in a scratch dir), Runner with StubModel (every answer abstains; the X arms still trade on code
rules), the real HV4Chain (cache, token bucket, credit counter, parsers) and the real ChainPrefetcher with its real cadence (mint facts every 10 min, holders
every 4 min for hot pools, the 1,500 calls an hour budget), all driven by the REPLAY clock.
What is mocked: the HTTP post under HV4Chain. It answers from the recorded public account bytes of tools/fixtures/hv4_chain/recorded.json, with a holder list
of 20 token accounts, so every response parses and has a realistic size. No RPC call is made.
What is not the live run: call latency (the mock answers at once), threads (the harness runs the prefetch pass between ticks, every CYCLE_S of replay time),
and the wall clock (the replay is a function of the tape).

Safety. The tape directory is read through symlinks in a scratch directory. The scratch root must not be under /data/mal/arena, must not exist (or be empty),
and every write (decision log, ledger, registry, budget files, chain state) goes there.

    python tools/hv4_memreplay.py --tape-dir /data/mal/arena/tape --hours 2026-10-19T08..2026-10-19T16 --scratch /tmp/x/run1 \\
        --holders on --sample-min 15 --out /tmp/x/run1.tsv [--tracemalloc]

Trim proof (PR claude/hv4-trim-wire; QP's precondition 3 on #745). `--trim-history on|off` sets RunnerConfig.trim_engine_history for one run (default on: the
runner's own default). `--compare-trim` runs the SAME closed hours twice, trim ON then trim OFF, as two child processes one after the other (so each has its own
RSS; the caller's `systemd-run --scope -p MemoryMax=...` covers both), and prints one `compare_trim` JSON: decision-log md5, ledger digest (mint included) and
ledger digest without the mint field, ON vs OFF and equal or not; the engine counters of both (hist_trimmed_prints, rows_dup, rows_below_trim_floor,
refused_history_trimmed); the per-trim cost; and the hourly RSS and engine print count of both. The path is the runner's own (Runner.step: scan, exits, entries,
sells, housekeeping), not a stand-in. A run that refuses, errors or differs exits non-zero in --compare-trim mode only when `--fail-on-diff` is given.
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import base64
import collections
import gc
import json
import os
import random
import sys
import time
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

MIN_MS = 60_000
HOUR_MS = 3_600_000
R_HOUSEKEEP = 5 * MIN_MS
FORBIDDEN_PREFIX = "/data/mal/arena"
TRIM_COUNTERS = ("hist_trimmed_prints", "rows_below_trim_floor", "refused_history_trimmed", "rows_dup")  # the engine counters QP asked to see (PR #745, comment 6275482286)
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "hv4_chain" / "recorded.json"


# ---------------------------------------------------------------------------------------------------------------------------------- mock rpc
class SimClock:
    """The replay clock, in ms. The chain layer, the prefetcher and the cache all read it, never the wall clock."""

    def __init__(self, ms: int = 0) -> None:
        self.ms = ms

    def __call__(self) -> int:
        return self.ms

    def seconds(self) -> float:
        return self.ms / 1000.0


class MockRpc:
    """The `post` callable of tools.hv4_chain.RpcClient: JSON-RPC answers built from recorded public account bytes. Counts calls and response bytes."""

    def __init__(self, clock: SimClock, mint_of_pool, slot_fn=None, *, seed: int = 1) -> None:
        from tools import hv4_chain as H

        self.H, self.clock, self.mint_of_pool = H, clock, mint_of_pool
        self.slot_fn = slot_fn  # the arena's latest slot: a chain fact stamped with a slot AFTER it is refused as a leak (hv4_prefetch._slot_ok)
        fx = json.loads(FIXTURE.read_text())
        pump = [v for v in fx["mints"].values() if v["label"].startswith("non_synthetic")]
        self.mint_blobs = [(v["data_b64"], v["owner"]) for v in pump]
        self.pool_blob = next(iter(fx["pools"].values()))
        self.rng = random.Random(seed)
        self.calls: collections.Counter = collections.Counter()
        self.bytes = 0
        self.supply = 1_000_000_000 * 10 ** 6

    def _slot(self) -> int:
        return max(0, int(self.slot_fn()) - 2) if self.slot_fn is not None else 400_000_000 + self.clock.ms // 400

    def _account(self, data_b64: str, owner: str) -> dict:
        return {"context": {"slot": self._slot()}, "value": {"data": [data_b64, "base64"], "owner": owner, "lamports": 1_461_600, "executable": False}}

    def __call__(self, url: str, payload: dict, timeout: float) -> dict:
        H = self.H
        method, params = payload["method"], payload["params"]
        self.calls[method] += 1
        if method == "getAccountInfo":
            addr = params[0]
            mint = self.mint_of_pool(addr)
            if mint is not None:  # a PumpSwap pool account: patch the base mint so the parser's mint check passes
                raw = bytearray(base64.b64decode(self.pool_blob["data_b64"]))
                raw[H.POOL_BASE_MINT: H.POOL_BASE_MINT + 32] = H.M.b58decode(mint)
                res = self._account(base64.b64encode(bytes(raw)).decode(), self.pool_blob["owner"])
            else:
                b64, owner = self.mint_blobs[zlib.crc32(addr.encode()) % len(self.mint_blobs)]  # not hash(): str hashes differ per process
                res = self._account(b64, owner)
        elif method == "getTokenSupply":
            res = {"context": {"slot": self._slot()}, "value": {"amount": str(self.supply), "decimals": 6, "uiAmount": 1e9, "uiAmountString": "1000000000"}}
        elif method == "getTokenLargestAccounts":
            rows, left = [], self.supply // 3
            for _ in range(20):
                amt = max(1, int(left * self.rng.uniform(0.05, 0.3)))
                left -= amt
                addr = H.M.b58encode(bytes(self.rng.getrandbits(8) for _ in range(32)))
                rows.append({"address": addr, "amount": str(amt), "decimals": 6, "uiAmount": amt / 1e6, "uiAmountString": str(amt / 1e6)})
            res = {"context": {"slot": self._slot()}, "value": rows}
        else:
            raise AssertionError(f"unexpected method {method}")
        out = {"jsonrpc": "2.0", "id": payload["id"], "result": res}
        self.bytes += len(json.dumps(out))
        return out


# ---------------------------------------------------------------------------------------------------------------------------------- build
def hour_keys(spec: str) -> list[str]:
    """'2026-10-19T08..2026-10-19T16' -> every hour key in the range, inclusive."""
    lo, _, hi = spec.partition("..")
    t = datetime.strptime(lo, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
    end = datetime.strptime(hi or lo, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
    out = []
    while t <= end:
        out.append(t.strftime("%Y-%m-%dT%H"))
        t += timedelta(hours=1)
    return out


def hour_ms(key: str) -> int:
    return int(datetime.strptime(key, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp() * 1000)


def check_scratch(scratch: Path) -> Path:
    scratch = scratch.resolve()
    if str(scratch) == FORBIDDEN_PREFIX or str(scratch).startswith(FORBIDDEN_PREFIX + "/"):
        raise SystemExit("hv4_memreplay: the scratch root must not be under /data/mal/arena")
    if scratch.exists() and any(scratch.iterdir()):
        raise SystemExit("hv4_memreplay: the scratch root must be new or empty")
    scratch.mkdir(parents=True, exist_ok=True)
    return scratch


def link_tape(tape_dir: Path, keys: list[str], dest: Path) -> Path:
    """Symlink the named closed hours into `dest` (the state then cannot reach any other hour of the live tape dir). `zstd -dc` skips a symlink unless -f is
    given, so the state is pointed at a one-line wrapper that adds it; nothing is written to the tape."""
    import shutil

    from tools import arena_zst

    dest.mkdir(parents=True, exist_ok=True)
    for k in keys:
        src = tape_dir / f"tape-{k}.jsonl.zst"
        if not src.exists():
            raise SystemExit(f"hv4_memreplay: closed hour missing: {src.name}")
        (dest / src.name).symlink_to(src)
    exe = shutil.which("zstd")
    if exe is None:
        raise SystemExit("hv4_memreplay: the zstd CLI is needed")
    wrapper = dest.parent / "zstd-f"
    wrapper.write_text(f'#!/bin/sh\nexec {exe} -f "$@"\n')
    wrapper.chmod(0o755)
    arena_zst.zstd_path = lambda: str(wrapper)
    return dest


class Rig:
    """Everything the stub runner owns, wired to a scratch root, a mock RPC and the replay clock."""

    def __init__(self, tape_dir: Path, scratch: Path, *, state_start_ms: int, clock_start_ms: int, holders: bool, feed_delay_slots: int = 2,
                 timing: bool = True, evict_idle_min: int | None = None, bound_memory: bool = True, shadow: bool = False, engine_prune: bool = False,
                 trim_history: bool = True) -> None:
        os.environ.setdefault("MAL_ARENA_TEST", "1")  # scratch registry only: register_bot's test overrides
        from tools import hv4_prefetch as P
        from tools import hv4_runner as R
        from tools.arena_paper import ArenaEngine
        from tools.arena_state import ArenaState
        from tools.hv4_budget import BudgetGuard
        from tools.hv4_chain import HV4Chain, RpcClient, RpcConfig, TokenBucket, TtlCache
        from tools.hv4_model import StubModel

        self.R, self.P = R, P
        self.clock = SimClock(clock_start_ms)
        root = scratch / "hv4"
        guard = BudgetGuard(root)
        model = StubModel(guard=guard)
        kw = {} if evict_idle_min is None else {"evict_idle_min": evict_idle_min}
        self.state = ArenaState(tape_dir, start_ms=state_start_ms, **kw)
        engine = ArenaEngine(scratch / "ledger", scratch / "bots.json", cfg=R.stack_config(feed_delay_slots=feed_delay_slots), sps_fn=lambda: 0.4)
        self.rpc_mock = MockRpc(self.clock, lambda a: (p.mint if (p := self.state.pool(a)) is not None and p.prog == "pumpswap" else None),
                                lambda: self.run.slot)
        bucket = TokenBucket(1e9, burst=1e9)
        client = RpcClient(RpcConfig("http://mock.invalid/rpc", "helius" if holders else "public"), bucket=bucket, post=self.rpc_mock, sleep=lambda s: None,
                           now_ms=self.clock, state_path=root / R.CHAIN_CREDITS_FILE)
        self.chain = HV4Chain(client, TtlCache(clock=self.clock.seconds), now_ms=self.clock)
        self.prefetch = P.ChainPrefetcher(self.chain, R.HV4ChainSource.compose, clock=self.clock, holders=holders, spend_path=root / R.CHAIN_SPEND_FILE)
        self.source = R.HV4ChainSource(self.chain, prefetch=self.prefetch)
        cfg = R.RunnerConfig(root=str(root), frozen_sha="abcdef1234567", clock_start_ms=clock_start_ms, lane_h=False, timing=timing, bound_memory=bound_memory,
                          trim_engine_history=trim_history)
        self.run = R.Runner(cfg, self.state, engine, model, self.source, guard)
        self.run.register_all(str(scratch / "bots.json"), now_ms=clock_start_ms - 61 * MIN_MS, verify_sha=False)
        self.engine = engine
        self.diffs: dict[tuple, tuple] = {}
        self.shadow: Any = None  # a second, never-pruned engine fed the same rows: the reference for the print-level comparison
        self.engine_pruned = 0
        if shadow:
            self.shadow = ArenaEngine(scratch / "ledger-shadow", scratch / "bots-shadow.json", cfg=R.stack_config(feed_delay_slots=feed_delay_slots), sps_fn=lambda: 0.4)
        if shadow or engine_prune:
            orig, nxt = engine.on_row, [0]

            def on_row(row: Any) -> Any:
                if self.shadow is not None:
                    self.shadow.on_row(row)
                out = orig(row)
                t = row.get("t_recv_ms")
                if engine_prune and isinstance(t, int) and t >= nxt[0]:  # EXPERIMENT: what #744 at 8ee39dc did, every 5 minutes of tape time
                    nxt[0] = t - t % R_HOUSEKEEP + R_HOUSEKEEP
                    self.engine_pruned += engine.prune()
                return out

            engine.on_row = on_row


# ---------------------------------------------------------------------------------------------------------------------------------- print-level comparison
def compare_engines(primary: Any, shadow: Any, diffs: dict[tuple, tuple]) -> dict[str, Any]:
    """For every pool the primary engine holds and every print it still holds there, compare (slot, v, vsrc) with the same print (matched by order key) in the
    never-pruned shadow engine. A pool whose held keys are not a suffix of the shadow's counts as `pools_not_suffix`. Differing prints are added to `diffs`
    (key: (pool, order key); value: (primary triple, shadow triple)), so repeated samples count a print once."""
    held = n_diff = pools_diff = not_suffix = last_v = mint = 0
    for k, h in primary.pools.items():
        g = shadow.pools.get(k)
        m = len(h.slot)
        held += m
        if g is None:
            not_suffix += 1
            continue
        off = len(g.slot) - m
        if off < 0 or g.keys[off:] != h.keys:
            not_suffix += 1
            continue
        if h.last_v_row != g.last_v_row:
            last_v += 1
        if h.mint != g.mint:
            mint += 1
        if h.slot == g.slot[off:] and h.v == g.v[off:] and h.vsrc == g.vsrc[off:]:
            continue
        pools_diff += 1
        for i in range(m):
            a, b = (h.slot[i], h.v[i], h.vsrc[i]), (g.slot[off + i], g.v[off + i], g.vsrc[off + i])
            if a != b:
                n_diff += 1
                diffs.setdefault((k, h.keys[i]), (a, b))
    return {"pools_held": len(primary.pools), "prints_held": held, "prints_differing": n_diff, "pools_with_a_differing_print": pools_diff,
            "pools_not_suffix": not_suffix, "pools_last_v_row_differs": last_v, "pools_mint_differs": mint}


def summarize_diffs(diffs: dict[tuple, tuple]) -> dict[str, Any]:
    """Distinct differing prints over the whole run, by (primary vsrc, shadow vsrc), and how many pools they sit in."""
    by = collections.Counter(f"{a[2]}<-{b[2]}" for a, b in diffs.values())
    return {"distinct_differing_prints": len(diffs), "distinct_pools": len({k[0] for k in diffs}), "by_vsrc_primary_from_shadow": dict(by)}


# ---------------------------------------------------------------------------------------------------------------------------------- what-if (never used by the runner)
def whatif_trim(engine: Any, keep_slots: int) -> int:
    """EXPERIMENT, harness only: cut each pool's print history in the engine (arena_paper.PoolHist) to the last `keep_slots` slots, for pools with no engine position.
    It stands in for a change to ArenaEngine.prune that the manager would have to approve (arena_paper.py is pinned and is not edited by this branch). Returns the
    number of prints dropped."""
    import bisect

    busy = {p.pool for p in engine.positions.values()}
    cut = engine.hw_slot - keep_slots
    dropped = 0
    for k, h in engine.pools.items():
        if k in busy or not h.slot or h.slot[0] >= cut:
            continue
        i = bisect.bisect_left(h.slot, cut)  # h.slot is sorted ascending
        if i <= 0:
            continue
        for name in ("keys", "slot", "isbuy", "sol", "tok", "q", "b", "v", "vsrc"):
            del getattr(h, name)[:i]
        h.seen = {d for d in h.seen if d[0] >= cut}
        h._np = None
        dropped += i
    return dropped


# ---------------------------------------------------------------------------------------------------------------------------------- census
_SIZED = (dict, list, set, frozenset, collections.deque)


def _inner(v: Any, cap: int = 200_000) -> int:
    """Total length of the sized containers inside a dict or list (the per-key lists of a dict of lists). Sampled past `cap` entries."""
    vals = list(v.values()) if isinstance(v, dict) else list(v) if isinstance(v, (list, collections.deque)) else []
    if len(vals) > cap:
        step = len(vals) // cap
        sample = vals[::step]
        return int(sum(len(x) for x in sample if isinstance(x, _SIZED)) * len(vals) / max(1, len(sample)))
    return sum(len(x) for x in vals if isinstance(x, _SIZED))


def census(obj: Any, prefix: str, out: dict[str, int]) -> None:
    """len() of every container attribute of `obj` (and, for a dict or list of containers, the sum of the inner lengths)."""
    for name, v in sorted(vars(obj).items()):
        if isinstance(v, _SIZED):
            out[f"{prefix}.{name}"] = len(v)
            inner = _inner(v)
            if inner:
                out[f"{prefix}.{name}[*]"] = inner
        elif isinstance(v, (collections.Counter,)):
            out[f"{prefix}.{name}"] = len(v)


def pool_census(state: Any, out: dict[str, int]) -> None:
    pools = list(state._pools.values())
    out["state.pools"] = len(pools)
    out["state.pool.net"] = sum(len(p.net) for p in pools)
    out["state.pool.candles"] = sum(len(p.candles) for p in pools)
    out["state.pool.events"] = sum(len(p.events) for p in pools)


def rss_mb() -> float:
    with open("/proc/self/statm") as f:
        return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1e6


def sample(rig: Rig, T: int, wall0: float, extra: dict[str, Any]) -> dict[str, Any]:
    run, st = rig.run, rig.state
    row: dict[str, Any] = {"sim": datetime.fromtimestamp(T / 1000, tz=timezone.utc).strftime("%m-%dT%H:%M"), "wall_s": round(time.time() - wall0), "rss_mb": round(rss_mb()),
                           "rows": st.counters["rows"]}
    c: dict[str, int] = {}
    pool_census(st, c)
    census(st, "state", c)
    census(run, "runner", c)
    census(run.quotes, "quotes", c)
    census(run.cards, "cards", c)
    census(run.limiter, "limiter", c)
    census(run.engine, "engine", c)
    census(rig.prefetch, "prefetch", c)
    census(rig.chain.cache, "cache", c)
    census(rig.chain.rpc.counter, "counter", c)
    c["engine.hist_rows"] = sum(len(h.slot) for h in run.engine.pools.values())  # PoolHist: one row per print, kept by the engine for pricing
    c["engine.hist_seen"] = sum(len(h.seen) for h in run.engine.pools.values())
    for k in TRIM_COUNTERS:
        c[f"engine.n.{k}"] = run.engine.counters.get(k, 0)
    c["prefetch.pools[*].mints"] =sum(len(p.mints) for p in rig.prefetch.pools.values())
    c["prefetch.pools[*].holders"] = sum(len(p.holders) for p in rig.prefetch.pools.values())
    row.update(c)
    if rig.shadow is not None:
        cmp = compare_engines(rig.engine, rig.shadow, rig.diffs)
        row.update({f"cmp.{k}": v for k, v in cmp.items()})
        row["cmp.distinct_differing_prints"] = len(rig.diffs)
    row["mock_calls"] = sum(rig.rpc_mock.calls.values())
    row["gc_objects"] = len(gc.get_objects())
    row.update(extra)
    return row


# ---------------------------------------------------------------------------------------------------------------------------------- loop
def replay(rig: Rig, t_from: int, t_to: int, *, sample_ms: int, tracemalloc_top: int, log=print, on_sample=None, whatif_keep_slots: int = 0) -> list[dict[str, Any]]:
    """Ticks T = t_from, +1 s ... < t_to. After each tick the prefetch pass runs every CYCLE_S of replay time, with the chain clock a little after T so a fact is
    stamped after the scan that asked for it (the live order: the scan at T runs at T + 100 ms, the refresh thread later)."""
    import tracemalloc

    run, P = rig.run, rig.P
    wall0 = time.time()
    rows: list[dict[str, Any]] = []
    snap0 = None
    snap_first = None
    next_sample = t_from - t_from % sample_ms + sample_ms
    cyc = int(P.CYCLE_S * 1000)
    T = t_from
    while T < t_to:
        rig.clock.ms = T + 100
        run.step(T)
        if whatif_keep_slots and T % R_HOUSEKEEP == 0:
            run.counters["whatif_prints_trimmed"] = run.counters.get("whatif_prints_trimmed", 0) + whatif_trim(run.engine, whatif_keep_slots)
        if (T // 1000) % int(P.CYCLE_S) == 0:
            rig.clock.ms = T + 500
            rig.prefetch.run_once(T + 500)
        T += 1000
        if T >= next_sample:
            next_sample += sample_ms
            extra: dict[str, Any] = {}
            if tracemalloc_top:
                snap = tracemalloc.take_snapshot()
                extra["tm_total_mb"] = round(sum(s.size for s in snap.statistics("filename")) / 1e6)
                top = snap.statistics("lineno")[:tracemalloc_top]
                extra["tm_top"] = [(f"{s.traceback[0].filename.rsplit('/', 1)[-1]}:{s.traceback[0].lineno}", round(s.size / 1e6, 1)) for s in top]
                if snap0 is not None:
                    extra["tm_growth"] = [(f"{d.traceback[0].filename.rsplit('/', 1)[-1]}:{d.traceback[0].lineno}", round(d.size_diff / 1e6, 1))
                                          for d in snap.compare_to(snap0, "lineno")[:tracemalloc_top]]
                snap0 = snap
                if snap_first is None:
                    snap_first = snap
                else:
                    extra["tm_growth_total"] = [(f"{d.traceback[0].filename.rsplit('/', 1)[-1]}:{d.traceback[0].lineno}", round(d.size_diff / 1e6, 1), d.count_diff)
                                                for d in snap.compare_to(snap_first, "lineno")[:tracemalloc_top]]
            row = sample(rig, T, wall0, extra)
            rows.append(row)
            log(json.dumps({k: row[k] for k in ("sim", "wall_s", "rss_mb", "rows", "state.pools", "gc_objects") if k in row}), flush=True)
            if on_sample is not None:
                on_sample(row)
    run.flush_timing()
    return rows


def ledger_digest(ledger_dir: Path, drop_keys: tuple[str, ...] = ()) -> str:
    """md5 of the engine ledger rows that are a function of the tape: every file, every row except the process-start record (a fresh uuid and the wall clock),
    without the engine_id uuid and the code_head / code_dirty process identity, and without any top-level key in `drop_keys`. Used to show two runs priced the same fills."""
    import hashlib

    from tools.hv4_runner import canon

    h = hashlib.md5()
    for f in sorted(Path(ledger_dir).rglob("*")):
        if f.is_file():
            h.update(f.relative_to(ledger_dir).as_posix().encode() + b"\0")
            for line in f.read_bytes().splitlines():
                row = json.loads(line)
                if row.get("kind") == "start":
                    continue
                for k in ("engine_id", "code_head", "code_dirty", *drop_keys):  # the process identity: a different commit of the harness gives a different value
                    row.pop(k, None)
                h.update(canon(row).encode() + b"\n")
    return h.hexdigest()


def write_tsv(rows: list[dict[str, Any]], path: Path) -> None:
    cols: list[str] = []
    for r in rows:
        for k in r:
            if k not in cols and not k.startswith("tm_"):
                cols.append(k)
    with open(path, "w") as f:
        f.write("\t".join(cols) + "\n")
        for r in rows:
            f.write("\t".join(str(r.get(c, "")) for c in cols) + "\n")


def write_md(rows: list[dict[str, Any]], path: Path, title: str, args: str) -> None:
    """The measurement table as markdown: RSS per sample, and (with tracemalloc) the allocation sites that grew most between the first and the last sample."""
    out = [f"# {title}", "", "Generated by `tools/hv4_memreplay.py` (not hand-written). Arguments: `" + args + "`.", "",
           "| sim (UTC) | RSS MB | tracemalloc MB | rows applied | state pools | engine pools | engine hist rows | quote rows | prefetch pools |",
           "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        out.append(f"| {r['sim']} | {r['rss_mb']} | {r.get('tm_total_mb', '')} | {r['rows']} | {r.get('state.pools', '')} | {r.get('engine.pools', '')} | "
                   f"{r.get('engine.hist_rows', '')} | {r.get('quotes.t[*]', '')} | {r.get('prefetch.pools', '')} |")
    last = rows[-1] if rows else {}
    if last.get("tm_growth_total"):
        out += ["", f"## Top {len(last['tm_growth_total'])} growing allocation sites, {rows[0]['sim']} to {last['sim']}", "", "| site (file:line) | size delta MB | count delta |", "|---|---|---|"]
        out += [f"| {site} | {mb} | {cnt} |" for site, mb, cnt in last["tm_growth_total"]]
    if last.get("tm_top"):
        out += ["", f"## Largest allocation sites at {last['sim']}", "", "| site (file:line) | MB |", "|---|---|"]
        out += [f"| {site} | {mb} |" for site, mb in last["tm_top"]]
    path.write_text("\n".join(out) + "\n")


# ---------------------------------------------------------------------------------------------------------------------------------- trim proof
def peak_rss_mb() -> float:
    """VmHWM of this process (the high-water mark, which the sampled RSS can miss between two samples)."""
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) / 1024.0
    return 0.0


def trim_file_stats(root: Path) -> dict[str, Any] | None:
    """Read back <root>/engine-trim-counters.jsonl of THIS harness's scratch run: line count, per-trim cost, the last line, and whether the cumulative counters only ever
    grew and the lines were written in tape order (what an append-only file of cumulative counters must look like). None when the runner wrote no file (trim off)."""
    from tools.hv4_runner import ENGINE_TRIM_FILE

    f = Path(root) / ENGINE_TRIM_FILE
    if not f.exists():
        return None
    lines = [json.loads(x) for x in f.read_bytes().splitlines() if x.strip()]
    ms = [x["trim_ms"] for x in lines]
    cum = ("hist_trimmed_prints", "rows_below_trim_floor", "refused_history_trimmed", "rows_dup", "trim_no", "tape_ms")
    mono = all(lines[i][k] <= lines[i + 1][k] for i in range(len(lines) - 1) for k in cum)
    return {"lines": len(lines), "trim_ms_sum": round(sum(ms), 1), "trim_ms_max": max(ms) if ms else None, "trim_ms_mean": round(sum(ms) / len(ms), 3) if ms else None,
            "monotone": mono, "last": lines[-1] if lines else None, "engine_ids": len({x["engine_id"] for x in lines})}


def run_result(rig: Rig, rows: list[dict[str, Any]], scratch: Path) -> dict[str, Any]:
    """Everything a trim ON / OFF comparison needs from one finished run."""
    run = rig.run
    errs = collections.Counter(run.hook_errors)
    return {"done": True, "decision_log_md5": run.log.md5(), "ledger_digest": ledger_digest(scratch / "ledger"), "ledger_digest_no_mint": ledger_digest(scratch / "ledger", ("mint",)),
            "mock_calls": dict(rig.rpc_mock.calls), "prefetch": rig.prefetch.stats(), "counters": run.counters, "engine_counters": dict(run.engine.counters),
            "hook_errors": dict(errs), "trim_file": trim_file_stats(Path(run.cfg.root)), "trim_engine_history": run.cfg.trim_engine_history,
            "peak_rss_mb": max(r["rss_mb"] for r in rows) if rows else None, "hwm_mb": round(peak_rss_mb()),
            "samples": [{"sim": r["sim"], "rss_mb": r["rss_mb"], "rows": r["rows"], "engine_pools": r.get("engine.pools"), "engine_hist_rows": r.get("engine.hist_rows"),
                         "engine_hist_seen": r.get("engine.hist_seen"), "wall_s": r["wall_s"]} for r in rows]}


def compare_trim(on: dict[str, Any], off: dict[str, Any]) -> dict[str, Any]:
    """The trim ON vs trim OFF comparison of two `run_result`s over the same closed hours. `identical` is the md5 proof: decision log, ledger (mint included) and ledger
    without the mint field all equal, no hook error in either run, the engine counters other than the two the trim itself adds equal (rows_dup among them: a row the trim
    drops below its floor is counted rows_dup, exactly as the unbounded engine's dedupe counts the same row), no buy refused for a trimmed history, and the trim really
    ran in the ON run and not in the OFF run."""
    def pair(k: str) -> dict[str, Any]:
        return {"on": on[k], "off": off[k], "equal": on[k] == off[k]}

    own = ("hist_trimmed_prints", "rows_below_trim_floor")  # counted only when the trim ran
    ec_on, ec_off = on["engine_counters"], off["engine_counters"]
    other_on, other_off = {k: v for k, v in ec_on.items() if k not in own}, {k: v for k, v in ec_off.items() if k not in own}
    hourly = []
    by_off = {s["sim"]: s for s in off["samples"]}
    for s in on["samples"]:
        if s["sim"].endswith(":00") and s["sim"] in by_off:
            o = by_off[s["sim"]]
            hourly.append({"sim": s["sim"], "rss_on_mb": s["rss_mb"], "rss_off_mb": o["rss_mb"], "hist_rows_on": s["engine_hist_rows"], "hist_rows_off": o["engine_hist_rows"],
                           "hist_seen_on": s["engine_hist_seen"], "hist_seen_off": o["engine_hist_seen"], "rows": s["rows"]})
    ran = bool(on["trim_file"] and on["trim_file"]["lines"] and ec_on.get("hist_trimmed_prints", 0) > 0) and off["trim_file"] is None
    out: dict[str, Any] = {"decision_log_md5": pair("decision_log_md5"), "ledger_digest": pair("ledger_digest"), "ledger_digest_no_mint": pair("ledger_digest_no_mint"),
                           "engine_counters": {k: {"on": ec_on.get(k, 0), "off": ec_off.get(k, 0)} for k in TRIM_COUNTERS},
                           "engine_counters_other_equal": other_on == other_off,
                           "engine_counters_other_diff": {k: [other_on.get(k), other_off.get(k)] for k in sorted(set(other_on) | set(other_off)) if other_on.get(k) != other_off.get(k)},
                           "hook_errors": {"on": on["hook_errors"], "off": off["hook_errors"]}, "trim_file_on": on["trim_file"], "trim_file_off": off["trim_file"],
                           "trim_actually_ran": ran, "peak_rss_mb": {"on": on["peak_rss_mb"], "off": off["peak_rss_mb"]}, "hwm_mb": {"on": on["hwm_mb"], "off": off["hwm_mb"]},
                           "wall_s": {"on": on["samples"][-1]["wall_s"] if on["samples"] else None, "off": off["samples"][-1]["wall_s"] if off["samples"] else None},
                           "hourly": hourly}
    out["identical"] = bool(out["decision_log_md5"]["equal"] and out["ledger_digest"]["equal"] and out["ledger_digest_no_mint"]["equal"] and out["engine_counters_other_equal"]
                            and not on["hook_errors"] and not off["hook_errors"] and ec_on.get("refused_history_trimmed", 0) == 0 and ran)
    # arena_paper (TRIM_KEEP_SLOTS): a nonzero rows_below_trim_floor or any history_trimmed refusal VOIDS the equivalence for a live engine even when this tape's md5s match
    out["equivalence_void"] = bool(ec_on.get("rows_below_trim_floor", 0) > 0 or ec_on.get("refused_history_trimmed", 0) > 0)
    return out


def child_argv(a: argparse.Namespace, arm: str, scratch: Path) -> list[str]:
    """The command line of one --compare-trim arm: the same hours and options as the parent, trim `arm` (on or off), its own scratch directory and result file."""
    out = [sys.executable, str(Path(__file__).resolve()), "--tape-dir", a.tape_dir, "--hours", a.hours, "--scratch", str(scratch / arm), "--holders", a.holders,
           "--sample-min", str(a.sample_min), "--out", str(scratch / f"{arm}.tsv"), "--result-json", str(scratch / f"{arm}-result.json"), "--trim-history", arm]
    for flag, val in (("--start", a.start), ("--state-start", a.state_start), ("--end", a.end)):
        if val:
            out += [flag, val]
    if a.evict_idle_min is not None:
        out += ["--evict-idle-min", str(a.evict_idle_min)]
    out.append("--no-timing")  # always: timing rows hold wall-clock counts, so a log with them is not a function of the tape and no md5 could match
    return out


def compare_trim_main(a: argparse.Namespace) -> int:
    """Run the same closed hours with trim ON and then OFF, each as its own process (own RSS), one after the other (one heavy replay at a time), and compare."""
    import subprocess

    scratch = check_scratch(Path(a.scratch))
    results = {}
    for arm in ("on", "off"):
        r = subprocess.run(child_argv(a, arm, scratch), check=False)
        if r.returncode != 0:
            print(json.dumps({"compare_trim": None, "failed_arm": arm, "returncode": r.returncode}))
            return 1
        results[arm] = json.loads((scratch / f"{arm}-result.json").read_text())
    cmp = compare_trim(results["on"], results["off"])
    if a.compare_out:
        Path(a.compare_out).write_text(json.dumps(cmp, indent=1) + "\n")
    print(json.dumps({"compare_trim": cmp}))
    return 0 if (cmp["identical"] or not a.fail_on_diff) else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tape-dir", default="/data/mal/arena/tape", help="read-only: closed .jsonl.zst hours are symlinked into the scratch dir")
    ap.add_argument("--hours", required=True, help="hour range, e.g. 2026-10-19T08..2026-10-19T16 (closed hours only)")
    ap.add_argument("--scratch", required=True, help="a new directory outside /data/mal/arena")
    ap.add_argument("--start", default=None, help="replay clock start, e.g. 2026-10-19T14:35:23 (default: the first hour)")
    ap.add_argument("--state-start", default=None, help="ArenaState start_ms (the warm-up window start), default: the first hour")
    ap.add_argument("--end", default=None, help="replay clock end (default: the end of the last hour)")
    ap.add_argument("--holders", choices=("on", "off"), default="off", help="on = the Helius run (holders refreshed for hot pools); off = the public stub run")
    ap.add_argument("--sample-min", type=int, default=15)
    ap.add_argument("--out", default=None, help="TSV of the samples")
    ap.add_argument("--tracemalloc", type=int, default=0, metavar="TOP", help="take a tracemalloc snapshot at each sample and keep the TOP sites (slow)")
    ap.add_argument("--md-out", default=None, help="write the measurement table as markdown")
    ap.add_argument("--evict-idle-min", type=int, default=None, help="ArenaState evict_idle_min (default: the state's own default)")
    ap.add_argument("--no-timing", action="store_true")
    ap.add_argument("--whatif-trim-slots", type=int, default=0, help="EXPERIMENT: also trim each quiet-of-positions engine pool history to the last N slots (see whatif_trim)")
    ap.add_argument("--compare-engine", action="store_true", help="feed a second, never-pruned engine and compare (slot, v, vsrc) of every print the primary holds at each sample")
    ap.add_argument("--engine-prune", action="store_true", help="EXPERIMENT: call ArenaEngine.prune() every 5 tape minutes, as #744 at 8ee39dc did (the exposure measurement)")
    ap.add_argument("--no-bound-memory", action="store_true", help="RunnerConfig.bound_memory off: the 2026-10-19 behaviour (for the before curve and the md5 proof)")
    ap.add_argument("--trim-history", choices=("on", "off"), default="on", help="RunnerConfig.trim_engine_history (default on, the runner's own default); needs bound memory on")
    ap.add_argument("--result-json", default=None, help="write the final result (md5s, digests, counters, samples) here")
    ap.add_argument("--compare-trim", action="store_true", help="run the same hours with trim ON then OFF (two processes, one after the other) and print the comparison")
    ap.add_argument("--compare-out", default=None, help="with --compare-trim: also write the comparison JSON here")
    ap.add_argument("--fail-on-diff", action="store_true", help="with --compare-trim: exit 1 when the two runs are not identical")
    a = ap.parse_args(argv)
    if a.compare_trim:
        return compare_trim_main(a)

    keys = hour_keys(a.hours)
    scratch = check_scratch(Path(a.scratch))
    tape = link_tape(Path(a.tape_dir), keys, scratch / "tape")

    def parse(s: str | None, default: int) -> int:
        return default if s is None else int(datetime.strptime(s, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp() * 1000)

    t_from = parse(a.start, hour_ms(keys[0]))
    t_to = parse(a.end, hour_ms(keys[-1]) + HOUR_MS)
    t_from -= t_from % 1000
    state_start = parse(a.state_start, hour_ms(keys[0]))
    if a.tracemalloc:
        import tracemalloc

        tracemalloc.start(1)
    rig = Rig(tape, scratch, state_start_ms=state_start, clock_start_ms=t_from, holders=a.holders == "on", timing=not a.no_timing, evict_idle_min=a.evict_idle_min,
              bound_memory=not a.no_bound_memory, shadow=a.compare_engine, engine_prune=a.engine_prune, trim_history=a.trim_history == "on")
    rows = replay(rig, t_from, t_to, sample_ms=a.sample_min * MIN_MS, tracemalloc_top=a.tracemalloc, whatif_keep_slots=a.whatif_trim_slots)
    if a.out:
        write_tsv(rows, Path(a.out))
        if a.tracemalloc:
            Path(str(a.out) + ".tracemalloc.json").write_text(json.dumps([{"sim": r["sim"], "top": r.get("tm_top"), "growth": r.get("tm_growth"), "total_mb": r.get("tm_total_mb")} for r in rows], indent=1))
    if a.md_out:
        write_md(rows, Path(a.md_out), f"HV4 stub replay memory, {a.hours}", " ".join(argv if argv is not None else sys.argv[1:]))
    if rig.shadow is not None:
        print(json.dumps({"engine_compare_final": {k[4:]: v for k, v in rows[-1].items() if k.startswith("cmp.")}, "engine_compare_distinct": summarize_diffs(rig.diffs),
                          "engine_pools_pruned_by_experiment": rig.engine_pruned}))
    result = run_result(rig, rows, scratch)
    if a.result_json:
        Path(a.result_json).write_text(json.dumps(result))
    print(json.dumps({k: v for k, v in result.items() if k != "samples"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
