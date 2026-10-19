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
        --warm-hours 0 --holders on --sample-min 15 --out /tmp/x/run1.tsv [--tracemalloc]
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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

MIN_MS = 60_000
HOUR_MS = 3_600_000
FORBIDDEN_PREFIX = "/data/mal/arena"
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

    def __init__(self, clock: SimClock, mint_of_pool, *, seed: int = 1) -> None:
        from tools import hv4_chain as H

        self.H, self.clock, self.mint_of_pool = H, clock, mint_of_pool
        fx = json.loads(FIXTURE.read_text())
        pump = [v for v in fx["mints"].values() if v["label"].startswith("non_synthetic")]
        self.mint_blobs = [(v["data_b64"], v["owner"]) for v in pump]
        self.pool_blob = next(iter(fx["pools"].values()))
        self.rng = random.Random(seed)
        self.calls: collections.Counter = collections.Counter()
        self.bytes = 0
        self.supply = 1_000_000_000 * 10 ** 6

    def _slot(self) -> int:
        return 400_000_000 + self.clock.ms // 400

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
                b64, owner = self.mint_blobs[hash(addr) % len(self.mint_blobs)]
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
                 timing: bool = True, evict_idle_min: int | None = None) -> None:
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
        self.rpc_mock = MockRpc(self.clock, lambda a: (p.mint if (p := self.state.pool(a)) is not None and p.prog == "pumpswap" else None))
        bucket = TokenBucket(1e9, burst=1e9)
        client = RpcClient(RpcConfig("http://mock.invalid/rpc", "helius" if holders else "public"), bucket=bucket, post=self.rpc_mock, sleep=lambda s: None,
                           now_ms=self.clock, state_path=root / R.CHAIN_CREDITS_FILE)
        self.chain = HV4Chain(client, TtlCache(clock=self.clock.seconds), now_ms=self.clock)
        self.prefetch = P.ChainPrefetcher(self.chain, R.HV4ChainSource.compose, clock=self.clock, holders=holders, spend_path=root / R.CHAIN_SPEND_FILE)
        self.source = R.HV4ChainSource(self.chain, prefetch=self.prefetch)
        cfg = R.RunnerConfig(root=str(root), frozen_sha="abcdef1234567", clock_start_ms=clock_start_ms, lane_h=False, timing=timing)
        self.run = R.Runner(cfg, self.state, engine, model, self.source, guard)
        self.run.register_all(str(scratch / "bots.json"), now_ms=clock_start_ms - 61 * MIN_MS, verify_sha=False)
        self.engine = engine


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
    c["prefetch.pools[*].mints"] =sum(len(p.mints) for p in rig.prefetch.pools.values())
    c["prefetch.pools[*].holders"] = sum(len(p.holders) for p in rig.prefetch.pools.values())
    row.update(c)
    row["mock_calls"] = sum(rig.rpc_mock.calls.values())
    row["gc_objects"] = len(gc.get_objects())
    row.update(extra)
    return row


# ---------------------------------------------------------------------------------------------------------------------------------- loop
def replay(rig: Rig, t_from: int, t_to: int, *, sample_ms: int, tracemalloc_top: int, log=print, on_sample=None) -> list[dict[str, Any]]:
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
    a = ap.parse_args(argv)

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
    rig = Rig(tape, scratch, state_start_ms=state_start, clock_start_ms=t_from, holders=a.holders == "on", timing=not a.no_timing, evict_idle_min=a.evict_idle_min)
    rows = replay(rig, t_from, t_to, sample_ms=a.sample_min * MIN_MS, tracemalloc_top=a.tracemalloc)
    if a.out:
        write_tsv(rows, Path(a.out))
        if a.tracemalloc:
            Path(str(a.out) + ".tracemalloc.json").write_text(json.dumps([{"sim": r["sim"], "top": r.get("tm_top"), "growth": r.get("tm_growth"), "total_mb": r.get("tm_total_mb")} for r in rows], indent=1))
    if a.md_out:
        write_md(rows, Path(a.md_out), f"HV4 stub replay memory, {a.hours}", " ".join(argv if argv is not None else sys.argv[1:]))
    print(json.dumps({"done": True, "mock_calls": dict(rig.rpc_mock.calls), "prefetch": rig.prefetch.stats(), "counters": rig.run.counters, "peak_rss_mb": max(r["rss_mb"] for r in rows) if rows else None}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
