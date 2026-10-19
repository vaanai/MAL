#!/usr/bin/env python3
"""arena_prune_replay: prove that ArenaEngine.trim_history() is pricing-neutral on the real arena tape, and measure its memory. Paper only. No network.

Replays closed arena tape hours (`zstd -dc`, read-only) through ArenaEngines with a deterministic synthetic order stream. The order stream is a function of the
tape alone (never of an engine's state), so engines that price the same must write byte-identical ledgers:
  HOT     every HOT_EVERY-th print of the eligible pools (PumpSwap, WSOL quote, V in [17.5, 30] SOL) and every HOT_EVERY_BONDING-th bonding print
  SPARSE  the 5th eligible print of every fourth pool
  QUIET   a print that ends a silence of more than QUIET_SLOTS slots on a pool with at least 3 earlier prints (a pool the trim had cut back to its anchor)
  exits   a fixed hold of 20 s, 45 s, 90 s or 300 s (by buy number), sold at the first row at or after the due instant
Every ledger write goes to a scratch dir (refused under /data/mal/arena). The clock, engine id, pid and code state are fixed so that nothing in a row depends on
the run.

  --mode lockstep  ONE pass over the tape feeds engine A (never trimmed: today's hosts), B (trim_history() every 5 min of tape) and, with --with-prune, C (the
                   existing prune() every 5 min). Every 60 sim-minutes and at the end it compares, for every pool and every print an engine still holds,
                   (slot, v, vsrc, side, size, reserves) against A's print with the same order key, and the pool's scalars (mint, venue, last_v_row). It reports the
                   number of differing prints and the ledger digests. Pass criterion for B: 0 differing prints, ledger bytes equal to A's.
  --mode solo      one engine (--trim off = never trimmed, on = trim_history() every 5 min, prune = prune() every 5 min): RSS and peak RSS of that run alone.
  --compare A B    checks two solo summaries (ledger bytes, counters, order stats).

    python tools/arena_prune_replay.py --hours 2026-10-19T08..2026-10-19T16 --scratch /tmp/x/lock --mode lockstep --with-prune
    python tools/arena_prune_replay.py --hours 2026-10-19T08..2026-10-19T16 --scratch /tmp/x/off --mode solo --trim off
    python tools/arena_prune_replay.py --hours 2026-10-19T08..2026-10-19T16 --scratch /tmp/x/on  --mode solo --trim on
    python tools/arena_prune_replay.py --compare /tmp/x/off /tmp/x/on
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import bisect
import hashlib
import heapq
import json
import os
import re
import subprocess
import sys
import time
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

os.environ.setdefault("MAL_ARENA_TEST", "1")  # the scratch registry uses the test-only clock / sha overrides; nothing here touches the real registry

from tools.arena_paper import MIN_FREE_PCT_FLOOR, ArenaEngine, ArenaRefused, StackConfig, register_bot  # noqa: E402

FORBIDDEN_PREFIX = "/data/mal/arena"
WSOL = "So11111111111111111111111111111111111111112"
V_LO, V_HI = 17_500_000_000, 30_000_000_000
HOT_EVERY = 1_500
HOT_EVERY_BONDING = 4_000
SPARSE_INDEX = 5
QUIET_SLOTS = 5_500
HOLDS_MS = (20_000, 45_000, 90_000, 300_000)
PRUNE_MS = 5 * 60_000
SAMPLE_MS = 15 * 60_000
CHECK_MS = 60 * 60_000
BOT = "botA"
NEW_COUNTERS = ("hist_trimmed_prints", "rows_below_trim_floor", "refused_history_trimmed")


def hour_keys(spec: str) -> list[str]:
    lo, hi = spec.split("..") if ".." in spec else (spec, spec)
    t = datetime.strptime(lo, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
    end = datetime.strptime(hi, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
    out = []
    while t <= end:
        out.append(t.strftime("%Y-%m-%dT%H"))
        t += timedelta(hours=1)
    return out


def tape_rows(tape_dir: Path, keys: list[str]) -> Iterator[dict[str, Any]]:
    """Rows of the named CLOSED hours (.jsonl.zst only), oldest first. The open hour is never read."""
    for k in keys:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}", k):
            raise SystemExit(f"bad hour key {k}")
        p = tape_dir / f"tape-{k}.jsonl.zst"
        if not p.is_file():
            raise SystemExit(f"{p} is not a closed (compacted) hour")
        pr = subprocess.Popen(["zstd", "-dc", str(p)], stdout=subprocess.PIPE)
        assert pr.stdout is not None
        for line in pr.stdout:
            yield json.loads(line)
        if pr.wait() != 0:
            raise SystemExit(f"zstd failed on {p}")


def rss_kb() -> tuple[int, int]:
    """(VmRSS, VmHWM) in kB of this process."""
    out = {}
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith(("VmRSS:", "VmHWM:")):
                out[line.split(":")[0]] = int(line.split()[1])
    return out["VmRSS"], out["VmHWM"]


class OrderStream:
    """Deterministic buys and exits, a function of the tape rows alone."""

    def __init__(self, eng: ArenaEngine) -> None:
        self.eng = eng
        self.n_elig = 0
        self.n_bond = 0
        self.per_pool: dict[str, int] = {}
        self.last_slot: dict[str, int] = {}
        self.due: list[tuple[int, int, str]] = []  # (due ms, buy number, position id)
        self.n_buys = 0
        self.stats: dict[str, Any] = {"buy_attempts": 0, "buys_open": 0, "buys_refused": 0, "sells": 0, "sells_refused": 0,
                                      "by_rule": {"hot": 0, "sparse": 0, "quiet": 0}}

    def _buy(self, pool: str, slot: int, rule: str) -> None:
        eng = self.eng
        self.stats["buy_attempts"] += 1
        self.stats["by_rule"][rule] += 1
        self.n_buys += 1
        try:
            pid = eng.buy(BOT, pool, 0.25, int(eng.hw_ms or 0), int(slot), {"rule": rule, "n": self.n_buys})
        except ArenaRefused:
            self.stats["buys_refused"] += 1
            return
        self.stats["buys_open"] += 1
        heapq.heappush(self.due, (int(eng.hw_ms or 0) + HOLDS_MS[self.n_buys % len(HOLDS_MS)], self.n_buys, pid))

    def after_row(self, row: dict[str, Any]) -> None:
        eng = self.eng
        while self.due and self.due[0][0] <= (eng.hw_ms or 0):
            _, _, pid = heapq.heappop(self.due)
            try:
                eng.sell(BOT, pid, 1.0, int(eng.hw_ms or 0), int(eng.hw_slot), "hold")
                self.stats["sells"] += 1
            except ArenaRefused:
                self.stats["sells_refused"] += 1
        if row.get("type", "trade") != "trade" or row.get("slot") is None or row.get("side") not in ("buy", "sell"):
            return
        prog = row.get("prog")
        slot = int(row["slot"])
        if prog == "pumpswap":
            v = row.get("virtual_quote_reserves")
            if v is None or not (V_LO <= v <= V_HI) or row.get("quote_mint") != WSOL:
                return
            pool = row["pool"]
            self.n_elig += 1
            k = self.per_pool.get(pool, 0) + 1
            self.per_pool[pool] = k
            prev = self.last_slot.get(pool)
            self.last_slot[pool] = max(slot, prev if prev is not None else slot)
            if self.n_elig % HOT_EVERY == 0:
                self._buy(pool, slot, "hot")
            elif k == SPARSE_INDEX and zlib.crc32(pool.encode()) % 4 == 0:
                self._buy(pool, slot, "sparse")
            elif prev is not None and k >= 4 and slot - prev > QUIET_SLOTS:
                self._buy(pool, slot, "quiet")
        elif prog == "pumpfun":
            pool = row.get("pool") or row.get("mint")
            if not pool or not row.get("quote_reserve") or not row.get("base_reserve"):
                return
            self.n_bond += 1
            if self.n_bond % HOT_EVERY_BONDING == 0:
                self._buy(pool, slot, "hot")


def ledger_digest(root: Path) -> tuple[str, dict[str, str]]:
    per = {}
    top = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            d = hashlib.sha256(p.read_bytes()).hexdigest()
            per[str(p.relative_to(root))] = d
            top.update(str(p.relative_to(root)).encode() + b"\0" + d.encode() + b"\0")
    return top.hexdigest(), per


class Arm:
    """One engine, its own scratch ledger, its own order stream, and how it is housekept: none | trim (trim_history) | prune (prune)."""

    def __init__(self, name: str, scratch: Path, how: str) -> None:
        self.name, self.how, self.scratch = name, how, scratch
        scratch.mkdir(parents=True, exist_ok=True)
        register_bot(BOT, "abc1234", 0, scratch / "bots.json", deciding_stake=0.25, model_ids=[], now_ms=0, verify_sha=False)
        self.eng = eng = ArenaEngine(scratch / "ledger", scratch / "bots.json", StackConfig(feed_delay_slots=2), now_fn=lambda: 0,
                                     code_state_fn=lambda: ("0" * 40, False), min_free_pct=MIN_FREE_PCT_FLOOR)
        eng.now_fn = lambda: eng.hw_ms or 0  # the call clock is the latest feed row: a bot called inline, thinking for no time
        eng.engine_id = "0" * 16
        self.orders = OrderStream(eng)

    def housekeep(self) -> None:
        if self.how == "trim":
            self.eng.trim_history()
        elif self.how == "prune":
            self.eng.prune()

    def hist_rows(self) -> int:
        return sum(len(h.slot) for h in self.eng.pools.values())


def compare_prints(ref: ArenaEngine, x: ArenaEngine) -> dict[str, int]:
    """Every print engine `x` holds against the reference engine's print with the same order key: (slot, v, vsrc, side, size, reserves) must be equal. Also the
    pool scalars. Counts only."""
    prints = diff = missing = pool_missing = scalar_diff = 0
    fields = ("slot", "v", "vsrc", "isbuy", "sol", "tok", "q", "b")
    for pool, hx in x.pools.items():
        hr = ref.pools.get(pool)
        prints += len(hx.keys)
        if hr is None:
            pool_missing += 1
            missing += len(hx.keys)
            continue
        if hx.mint != hr.mint or hx.venue != hr.venue or hx.last_v_row != hr.last_v_row:
            scalar_diff += 1
        cols_x = [getattr(hx, f) for f in fields]
        cols_r = [getattr(hr, f) for f in fields]
        rk = hr.keys
        for i, key in enumerate(hx.keys):
            j = bisect.bisect_left(rk, key)
            if j >= len(rk) or rk[j] != key:
                missing += 1
                continue
            if any(cx[i] != cr[j] for cx, cr in zip(cols_x, cols_r)):
                diff += 1
    return {"pools": len(x.pools), "prints": prints, "differing_prints": diff, "prints_not_in_reference": missing, "pools_not_in_reference": pool_missing,
            "pools_with_scalar_diff": scalar_diff}


def arm_summary(arm: Arm) -> dict[str, Any]:
    arm.eng.flush()
    top, per = ledger_digest(arm.scratch / "ledger")
    return {"how": arm.how, "orders": arm.orders.stats, "counters": dict(sorted(arm.eng.counters.items())), "positions_left": len(arm.eng.positions),
            "pending_left": len(arm.eng.pending), "ledger_sha256": top, "ledger_files": per,
            "ledger_bytes": sum(p.stat().st_size for p in (arm.scratch / "ledger").rglob("*") if p.is_file()), "hist_rows_end": arm.hist_rows(),
            "pools_end": len(arm.eng.pools)}


def run(args: argparse.Namespace) -> dict[str, Any]:
    scratch = Path(args.scratch).resolve()
    if str(scratch).startswith(FORBIDDEN_PREFIX):
        raise SystemExit(f"refusing a scratch dir under {FORBIDDEN_PREFIX}")
    if scratch.exists() and any(scratch.iterdir()):
        raise SystemExit(f"scratch dir {scratch} is not empty")
    scratch.mkdir(parents=True, exist_ok=True)
    os.getpid = lambda: 4242  # type: ignore[assignment]  # the start record carries the pid; fixed so that two runs write the same bytes
    keys = hour_keys(args.hours)
    if args.mode == "lockstep":
        arms = [Arm("A", scratch / "A", "none"), Arm("B", scratch / "B", "trim")] + ([Arm("C", scratch / "C", "prune")] if args.with_prune else [])
    else:
        arms = [Arm("S", scratch / "S", {"off": "none", "on": "trim", "prune": "prune"}[args.trim])]
    ref = arms[0].eng
    series: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    t0 = time.time()
    next_prune = next_sample = next_check = None
    n = 0
    for row in tape_rows(Path(args.tape_dir), keys):
        for arm in arms:
            arm.eng.on_row(row)
            arm.orders.after_row(row)
        n += 1
        t = ref.hw_ms
        if t is None:
            continue
        if next_prune is None:
            next_prune, next_sample, next_check = t + PRUNE_MS, t + SAMPLE_MS, t + CHECK_MS
        if t >= next_prune:
            for arm in arms:
                arm.housekeep()
            next_prune = t + PRUNE_MS
        if t >= next_sample:  # type: ignore[operator]
            rss, hwm = rss_kb()
            series.append({"sim": datetime.fromtimestamp(t / 1000, tz=timezone.utc).strftime("%m-%dT%H:%M"), "rows": n, "rss_mb": round(rss / 1024),
                           "hwm_mb": round(hwm / 1024), "wall_s": round(time.time() - t0), **{f"hist_rows_{a.name}": a.hist_rows() for a in arms}})
            print(json.dumps(series[-1]), flush=True)
            next_sample = t + SAMPLE_MS
        if args.mode == "lockstep" and t >= next_check:  # type: ignore[operator]
            checks.append({"sim": series[-1]["sim"] if series else "", **{a.name: compare_prints(ref, a.eng) for a in arms[1:]}})
            print(json.dumps(checks[-1]), flush=True)
            next_check = t + CHECK_MS
    rss, hwm = rss_kb()
    if args.mode == "lockstep":
        checks.append({"sim": "end", **{a.name: compare_prints(ref, a.eng) for a in arms[1:]}})
        print(json.dumps(checks[-1]), flush=True)
    summary: dict[str, Any] = {"mode": args.mode, "hours": keys, "rows": n, "rss_end_mb": round(rss / 1024), "rss_peak_mb": round(hwm / 1024),
                               "wall_s": round(time.time() - t0), "arms": {a.name: arm_summary(a) for a in arms}, "print_checks": checks, "series": series}
    if args.mode == "lockstep":
        d0 = summary["arms"]["A"]
        for nm, d in summary["arms"].items():
            if nm != "A":
                ca = {k: v for k, v in d0["counters"].items() if k not in NEW_COUNTERS}
                cx = {k: v for k, v in d["counters"].items() if k not in NEW_COUNTERS}
                d["vs_A"] = {"ledger_byte_identical": d["ledger_files"] == d0["ledger_files"], "counters_equal_but_new_keys": ca == cx,
                             "differing_counters": {k: [ca.get(k), cx.get(k)] for k in sorted(set(ca) | set(cx)) if ca.get(k) != cx.get(k)}}
    else:
        summary["arm"] = summary["arms"]["S"]
    (scratch / "summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True))
    print(json.dumps({k: v for k, v in summary.items() if k not in ("series", "arms", "print_checks")}), flush=True)
    for nm, d in summary["arms"].items():
        print(nm, json.dumps({k: v for k, v in d.items() if k != "ledger_files"}), flush=True)
    return summary


def compare(a: str, b: str) -> int:
    ra, rb = (json.loads((Path(x) / "summary.json").read_text()) for x in (a, b))
    sa, sb = ra["arm"], rb["arm"]
    same_ledger = sa["ledger_files"] == sb["ledger_files"]
    ca = {k: v for k, v in sa["counters"].items() if k not in NEW_COUNTERS}
    cb = {k: v for k, v in sb["counters"].items() if k not in NEW_COUNTERS}
    out = {"ledger_byte_identical": same_ledger, "ledger_sha256": [sa["ledger_sha256"], sb["ledger_sha256"]], "ledger_bytes": [sa["ledger_bytes"], sb["ledger_bytes"]],
           "counters_equal_but_new_keys": ca == cb, "new_counters": [{k: v for k, v in s["counters"].items() if k in NEW_COUNTERS} for s in (sa, sb)],
           "orders_equal": sa["orders"] == sb["orders"], "orders": sa["orders"], "rss_peak_mb": [ra["rss_peak_mb"], rb["rss_peak_mb"]],
           "rss_end_mb": [ra["rss_end_mb"], rb["rss_end_mb"]], "hist_rows_end": [sa["hist_rows_end"], sb["hist_rows_end"]]}
    print(json.dumps(out, indent=1))
    return 0 if same_ledger and ca == cb and out["orders_equal"] else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tape-dir", default="/data/mal/arena/tape", help="read-only; only the named closed .jsonl.zst hours are opened")
    ap.add_argument("--hours", help="first..last hour key, e.g. 2026-10-19T08..2026-10-19T16")
    ap.add_argument("--scratch", help="new or empty dir for the ledgers, registries and summary.json (refused under /data/mal/arena)")
    ap.add_argument("--mode", choices=("lockstep", "solo"), default="solo")
    ap.add_argument("--trim", choices=("off", "on", "prune"), default="off", help="solo: off = never housekept, on = trim_history() every 5 min of tape, prune = prune() every 5 min")
    ap.add_argument("--with-prune", action="store_true", help="lockstep: add arm C running the existing prune() (measures its exposure; not a pass criterion)")
    ap.add_argument("--compare", nargs=2, metavar=("DIR_A", "DIR_B"))
    a = ap.parse_args(argv)
    if a.compare:
        return compare(*a.compare)
    if not (a.hours and a.scratch):
        ap.error("--hours and --scratch are required")
    run(a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
