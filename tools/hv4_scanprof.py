#!/usr/bin/env python3
"""hv4_scanprof: replay the Haiku v4 (HV4) stub path over closed arena tape hours, measure the wall time of every scan, optionally cProfile the scans, and
fingerprint the decision logs with the timing rows left out. Paper only. No network, no key, no API call, no live process touched.

Why. Shakedown check 6 (SHAKEDOWN-CHECKS-2.md) wants scan_wall_ms p99 <= 2,000 ms and max <= 5,000 ms. This harness is for the scan-speed hedge branch
(claude/hv4-scan-perf): it runs the SAME closed hours on two checkouts (the frozen sha and the branch) and compares (a) the scan_wall_ms distribution and (b) the md5
of every decision log with the timing fields removed. It builds on tools.hv4_memreplay (real ArenaState, ArenaEngine, Runner, StubModel, HV4Chain and ChainPrefetcher
with a MOCKED http post). Everything is written under --scratch, which must be new/empty and outside /data/mal/arena.

    python tools/hv4_scanprof.py run --tape-dir /data/mal/arena/tape --hours 2026-10-19T14..2026-10-19T16 --state-start 2026-10-19T08:00:00 \\
        --start 2026-10-19T14:00:00 --scratch /tmp/x/run1 --result-json /tmp/x/run1.json [--profile /tmp/x/run1.prof.txt]
    python tools/hv4_scanprof.py compare before.json after.json

What a scan's wall time contains here. Runner.step times Runner.scan(T) only (after the exit checks, triggers, watches and re-checks of the tick). With the live chain source
(HV4ChainSource + ChainPrefetcher) chain_part READS facts stamped before T and makes no RPC; the mock http post of this harness is therefore never on the scan path, and
neither is any RPC in the live stub. What the scan does contain: the pool filter pass, features and setups, card building, the shared risk round and the entry rounds
(StubModel, BudgetGuard files), and one fsync'd append per decision-log row.
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import cProfile
import hashlib
import io
import json
import pstats
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

MIN_MS = 60_000
HOUR_MS = 3_600_000
SCAN_EVERY_MS = 5 * MIN_MS
BOARD_EVERY_MS = 15 * MIN_MS
SCAN_SECOND_MS = 2_000
TIMING_KINDS = ("scan_timing", "tick_timing")  # the only rows that hold wall-clock numbers
CHAIN_KEYS = ("seq", "prev", "sha")  # the hash chain covers the timing rows too, so it is left out of a timing-free fingerprint


def canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def decision_md5_no_timing(root: str | Path) -> dict[str, Any]:
    """md5 over every decision log under <root>/decisions (every book's rows plus SCAN and RISK) with the scan_timing and tick_timing rows removed and the chain fields
    (seq, prev, sha) removed. Two replays of the same tape must give the same value whatever the machine speed. Also a per-log md5 and row count, so a difference can be
    located."""
    base = Path(root) / "decisions"
    total = hashlib.md5()
    per: dict[str, dict[str, Any]] = {}
    rows_total = 0
    for d in sorted(p for p in base.iterdir() if p.is_dir()) if base.exists() else []:
        h, n = hashlib.md5(), 0
        for f in sorted(d.glob("*.jsonl")):
            for line in f.read_bytes().splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("kind") in TIMING_KINDS:
                    continue
                for k in CHAIN_KEYS:
                    row.pop(k, None)
                b = f.name.encode() + b"\0" + canon(row).encode() + b"\n"
                h.update(b)
                total.update(d.name.encode() + b"\0" + b)
                n += 1
        per[d.name] = {"md5": h.hexdigest(), "rows": n}
        rows_total += n
    return {"md5": total.hexdigest(), "rows": rows_total, "per_log": per}


def scan_rows(root: str | Path) -> list[dict[str, Any]]:
    """One record per scan from the SCAN log: instant, board or not, filter-pass count, cards, and the scan_wall_ms of its scan_timing row (None when timing was off)."""
    out: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    base = Path(root) / "decisions" / "SCAN"
    for f in sorted(base.glob("*.jsonl")) if base.exists() else []:
        for line in f.read_bytes().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("kind") == "scan":
                cur = {"ms": r["ms"], "n_filter_pass": r["n_filter_pass"], "n_board": r["n_board"], "n_cards": r["n_cards"], "wall_ms": None}
                out.append(cur)
            elif r.get("kind") == "scan_timing" and cur is not None and cur["ms"] == r["ms"]:
                cur["wall_ms"] = r["scan_wall_ms"]
    return out


def pct(vals: list[int], q: float) -> int:
    s = sorted(vals)
    return s[min(len(s) - 1, int(q * len(s)))] if s else 0


def dist(vals: list[int]) -> dict[str, Any]:
    if not vals:
        return {"n": 0}
    s = sorted(vals)
    return {"n": len(s), "min": s[0], "p50": pct(s, 0.5), "p90": pct(s, 0.9), "p99": pct(s, 0.99), "max": s[-1], "mean": round(sum(s) / len(s)),
            "over_2000": sum(1 for v in s if v > 2000)}


def summarize_scans(scans: list[dict[str, Any]], skip_first: int = 0) -> dict[str, Any]:
    """Distribution of scan_wall_ms overall, for ordinary scans (no board) and for board scans, skipping the first `skip_first` scans (a cold start)."""
    sc = [s for s in scans[skip_first:] if s["wall_ms"] is not None]
    board = [s["wall_ms"] for s in sc if s["n_board"] > 0]
    ordinary = [s["wall_ms"] for s in sc if s["n_board"] == 0]
    return {"all": dist([s["wall_ms"] for s in sc]), "ordinary": dist(ordinary), "board": dist(board),
            "n_filter_pass": {"min": min((s["n_filter_pass"] for s in sc), default=None), "max": max((s["n_filter_pass"] for s in sc), default=None)},
            "cards_per_board_scan": {"min": min((s["n_cards"] for s in sc if s["n_board"] > 0), default=None), "max": max((s["n_cards"] for s in sc if s["n_board"] > 0), default=None)}}


def gc_outside_summary(pauses: list[tuple[int, float]]) -> dict[str, Any]:
    """Collections that ran outside a scan, in the tick loop: count by generation, and p50 / p99 / max of the pause (ms), for all generations and for generation 2 alone."""
    def d(v: list[float]) -> dict[str, Any]:
        s = sorted(v)
        return {"n": len(s)} if not s else {"n": len(s), "p50": round(s[len(s) // 2], 1), "p99": round(s[min(len(s) - 1, int(0.99 * len(s)))], 1), "max": round(s[-1], 1),
                                            "total_ms": round(sum(s), 1)}
    return {"by_gen_n": [sum(1 for g, _ in pauses if g == k) for k in (0, 1, 2)], "all": d([ms for _g, ms in pauses]), "gen2": d([ms for g, ms in pauses if g == 2]),
            "gen2_over_500ms": sum(1 for g, ms in pauses if g == 2 and ms > 500.0)}


def exit_check_delay_sim(step_ms: list[float], period: float = 1000.0, nominal: float = 100.0) -> dict[str, Any]:
    """SIMULATED exit-check delay of the live tick loop from the measured wall time of each replay step. The runner's own tick_timing delay is `wall now - T`, which in a replay
    is hours (T is tape time), so it is meaningless here. The live loop starts tick k at max(T_k + 100 ms, end of tick k-1): the lateness L_k = max(0, L_k-1 + d_k-1 - 1000), and
    the delay the runner would record is about 100 ms + L_k. A queue model on replay step times: it carries the replay's CPU and disk, not the live host's."""
    late, out = 0.0, []
    for d in step_ms:
        out.append(nominal + late)
        late = max(0.0, late + d - period)
    return dist([int(x) for x in out])


def is_board(T: int) -> bool:
    return (T // 1000) % (BOARD_EVERY_MS // 1000) == SCAN_SECOND_MS // 1000


def run_cmd(a: argparse.Namespace) -> int:
    from tools import hv4_memreplay as M

    keys = M.hour_keys(a.hours)
    scratch = M.check_scratch(Path(a.scratch))
    tape = M.link_tape(Path(a.tape_dir), keys, scratch / "tape")

    def parse(s: str | None, default: int) -> int:
        return default if s is None else int(datetime.strptime(s, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp() * 1000)

    t_from = parse(a.start, M.hour_ms(keys[0]))
    t_to = parse(a.end, M.hour_ms(keys[-1]) + HOUR_MS)
    t_from -= t_from % 1000
    state_start = parse(a.state_start, M.hour_ms(keys[0]))
    pkw: dict[str, Any] = {}
    if getattr(a, "prefetch_budget", None):
        pkw["hourly_budget"] = a.prefetch_budget  # harness knob (PR #768 variants); never reachable in run mode
    if getattr(a, "cold_mint_refresh_min", None):
        pkw["cold_mint_refresh_ms"] = a.cold_mint_refresh_min * MIN_MS
    if getattr(a, "hot_holders_refresh_min", None):
        pkw["hot_holders_refresh_ms"] = a.hot_holders_refresh_min * MIN_MS
    if getattr(a, "warm_holders_refresh_min", None):
        pkw["warm_holders_refresh_ms"] = a.warm_holders_refresh_min * MIN_MS
    if getattr(a, "holders_age_min", None):  # the card's holders age limit (hv4_cards.CHAIN_HOLDERS_MAX_AGE_MS), changed for THIS process only: a replay variant
        from tools import hv4_cards as _C

        _C.CHAIN_HOLDERS_MAX_AGE_MS = a.holders_age_min * MIN_MS
    rig = M.Rig(tape, scratch, state_start_ms=state_start, clock_start_ms=t_from, holders=a.holders == "on", timing=not a.no_timing, prefetch_kw=pkw or None)
    run = rig.run
    if pkw.get("warm_holders_refresh_ms"):
        from tools.hv4_prefetch_tap import feed_warm_volumes

        feed_warm_volumes(rig)
    tap = None
    if getattr(a, "tap", None):
        from tools.hv4_prefetch_tap import PrefetchTap

        tap = PrefetchTap(rig).install()
    profs = {"ordinary": cProfile.Profile(), "board": cProfile.Profile()} if a.profile else {}
    n_prof = {"ordinary": 0, "board": 0}
    if profs:
        orig_scan = run.scan

        def scan(T: int) -> None:
            kind = "board" if is_board(T) else "ordinary"
            pr = profs[kind]
            pr.enable()
            try:
                orig_scan(T)
            finally:
                pr.disable()
                n_prof[kind] += 1

        run.scan = scan  # type: ignore[method-assign]
    fs: dict[int, dict[str, float]] = {}
    gc_out: list[tuple[int, float]] = []  # (generation, ms) of every collection outside a scan, after the first step
    step_ms: list[float] = []  # wall ms of every step after the first (the first reads the warm-up)
    gc_total = {"n": [0, 0, 0], "ms": [0.0, 0.0, 0.0]}  # every collection of the run by generation (the warm-up included), when --time-gc
    if a.time_fsync or a.time_gc:  # per scan: the time spent in os.fsync (decision-log rows, budget and prefetch files) and in the cyclic GC, by generation
        import gc
        import os

        acc: dict[str, Any] = {"on": 0, "loop": 0, "fsync_ms": 0.0, "n_fsync": 0, "gc_n": [0, 0, 0], "gc_ms": [0.0, 0.0, 0.0], "gc_t0": 0.0}
        orig_fsync, inner_scan = os.fsync, run.scan

        def timed_fsync(fd: int) -> None:
            t = time.perf_counter()
            try:
                return orig_fsync(fd)
            finally:
                if acc["on"]:
                    acc["fsync_ms"] += (time.perf_counter() - t) * 1000.0
                    acc["n_fsync"] += 1

        def gc_cb(phase: str, info: dict) -> None:
            if phase == "start":
                acc["gc_t0"] = time.perf_counter()
                return
            g, ms = int(info["generation"]), (time.perf_counter() - acc["gc_t0"]) * 1000.0
            gc_total["n"][g] += 1
            gc_total["ms"][g] += ms
            if acc["on"]:
                acc["gc_n"][g] += 1
                acc["gc_ms"][g] += ms
            elif acc["loop"]:  # a pause in the tick loop outside a scan (after the first step, which reads the warm-up)
                gc_out.append((g, ms))

        def timed_scan(T: int) -> None:
            acc.update(on=1, fsync_ms=0.0, n_fsync=0, gc_n=[0, 0, 0], gc_ms=[0.0, 0.0, 0.0])
            try:
                inner_scan(T)
            finally:
                acc["on"] = 0
                rec: dict[str, Any] = {}
                if a.time_fsync:
                    rec.update(fsync_ms=round(acc["fsync_ms"], 1), n_fsync=acc["n_fsync"])
                if a.time_gc:
                    rec.update(gc_n=list(acc["gc_n"]), gc_ms=[round(x, 1) for x in acc["gc_ms"]], gc2_n=acc["gc_n"][2], gc2_ms=round(acc["gc_ms"][2], 1))
                fs[T] = rec

        if a.time_fsync:
            os.fsync = timed_fsync  # type: ignore[assignment]
        if a.time_gc:
            gc.callbacks.append(gc_cb)
        run.scan = timed_scan  # type: ignore[method-assign]
        if a.time_gc:
            inner_step, first_step = run.step, [True]

            def timed_step(T: int) -> None:
                t = time.perf_counter()
                try:
                    inner_step(T)
                finally:
                    if first_step[0]:
                        first_step[0] = False
                        acc["loop"] = 1
                    else:
                        step_ms.append((time.perf_counter() - t) * 1000.0)

            run.step = timed_step  # type: ignore[method-assign]
    t0 = time.time()
    rows_s = M.replay(rig, t_from, t_to, sample_ms=a.sample_min * MIN_MS, tracemalloc_top=0, log=lambda *x, **k: None)
    wall = round(time.time() - t0)
    run.flush_timing()
    if tap is not None:
        tap.write(a.tap)
    scans = scan_rows(run.cfg.root)
    for sc in scans:
        sc.update(fs.get(sc["ms"], {}))
    res: dict[str, Any] = {"hours": a.hours, "start": a.start, "state_start": a.state_start, "wall_s": wall, "n_scans": len(scans), "timing": not a.no_timing,
                           "summary": summarize_scans(scans, a.skip_scans), "summary_all_scans": summarize_scans(scans, 0), "scans": scans,
                           "decision_md5_no_timing": decision_md5_no_timing(run.cfg.root), "counters": dict(run.counters),
                           "hook_errors": sorted(set(run.hook_errors)), "mock_calls": dict(rig.rpc_mock.calls), "prefetch": rig.prefetch.stats(),
                           "ledger_digest": M.ledger_digest(scratch / "ledger")}  # the scratch engine ledger of THIS replay, never a live one
    res["rss"] = {"samples_mb": [[r["sim"], r["rss_mb"]] for r in rows_s], "peak_sampled_mb": max((r["rss_mb"] for r in rows_s), default=None), "hwm_mb": round(M.peak_rss_mb())}
    if a.time_gc:
        res["gc_outside_scans"] = gc_outside_summary(gc_out)
        res["step_ms"] = dist([int(x) for x in step_ms])
        res["exit_check_delay_sim_ms"] = exit_check_delay_sim(step_ms)
        res["gc_total"] = {"n_by_gen": gc_total["n"], "ms_by_gen": [round(x, 1) for x in gc_total["ms"]]}
    if profs:
        txt = io.StringIO()
        for kind, pr in profs.items():
            txt.write(f"\n===== {kind} scans: {n_prof[kind]} profiled =====\n")
            if n_prof[kind]:
                for sort in ("cumulative", "tottime"):
                    s = io.StringIO()
                    pstats.Stats(pr, stream=s).strip_dirs().sort_stats(sort).print_stats(a.top)
                    txt.write(f"--- sorted by {sort}\n" + s.getvalue())
        Path(a.profile).write_text(txt.getvalue())
        res["profiled"] = dict(n_prof)
    if a.result_json:
        Path(a.result_json).write_text(json.dumps(res))
    print(json.dumps({k: v for k, v in res.items() if k not in ("scans",)}))
    return 0


def compare(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """The decision-equivalence verdict and the timing delta of two run results over the same hours. `identical` needs equal timing-free md5 (every log), equal row counts,
    equal scratch-ledger digest, equal scan count, no hook error, and the same counters apart from none (counters are a function of the tape)."""
    b, c = before["decision_md5_no_timing"], after["decision_md5_no_timing"]
    diff_logs = sorted(k for k in set(b["per_log"]) | set(c["per_log"]) if b["per_log"].get(k) != c["per_log"].get(k))
    out = {"hours": before["hours"], "md5_before": b["md5"], "md5_after": c["md5"], "rows_before": b["rows"], "rows_after": c["rows"], "logs": len(b["per_log"]),
           "logs_differing": diff_logs, "ledger_equal": before["ledger_digest"] == after["ledger_digest"], "ledger_digest": before["ledger_digest"],
           "n_scans": [before["n_scans"], after["n_scans"]], "counters_equal": before["counters"] == after["counters"],
           "hook_errors": [before["hook_errors"], after["hook_errors"]], "summary_before": before["summary"], "summary_after": after["summary"]}
    out["identical"] = bool(b["md5"] == c["md5"] and b["rows"] == c["rows"] and not diff_logs and out["ledger_equal"] and before["n_scans"] == after["n_scans"]
                            and out["counters_equal"] and not before["hook_errors"] and not after["hook_errors"])
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--tape-dir", default="/data/mal/arena/tape")
    r.add_argument("--hours", required=True)
    r.add_argument("--scratch", required=True)
    r.add_argument("--start", default=None)
    r.add_argument("--state-start", default=None)
    r.add_argument("--end", default=None)
    r.add_argument("--holders", choices=("on", "off"), default="on", help="on = the Helius run (holders refreshed for hot pools, as the shakedown does); off = public")
    r.add_argument("--sample-min", type=int, default=60)
    r.add_argument("--no-timing", action="store_true")
    r.add_argument("--prefetch-budget", type=int, default=None, help="harness knob: the prefetcher's hourly call budget (default 1,500)")
    r.add_argument("--cold-mint-refresh-min", type=int, default=None, help="harness knob: the mint refresh interval of a pool that is not hot, in minutes (default 10)")
    r.add_argument("--hot-holders-refresh-min", type=int, default=None, help="harness knob: the holders refresh interval of a hot pool, in minutes (default 4)")
    r.add_argument("--warm-holders-refresh-min", type=int, default=None, help="harness knob: also read holders of filter-pass pools that are not hot, by 60-min volume, every N minutes (default off)")
    r.add_argument("--holders-age-min", type=int, default=None, help="harness knob: the card's holders age limit in minutes for this process (default 5)")
    r.add_argument("--tap", default=None, help="write the prefetcher tap summary (tools/hv4_prefetch_tap.py) to this JSON file")
    r.add_argument("--time-fsync", action="store_true", help="record per scan the time spent in os.fsync (fsync_ms, n_fsync)")
    r.add_argument("--time-gc", action="store_true", help="record per scan the cyclic-GC collections by generation (gc_n, gc_ms, gc2_n, gc2_ms) via gc.callbacks, and the run totals")
    r.add_argument("--profile", default=None, help="write cProfile text (cumulative and tottime tables for ordinary and board scans) here; slows the scans")
    r.add_argument("--skip-scans", type=int, default=0, help="leave the first N scans out of the summary")
    r.add_argument("--top", type=int, default=35)
    r.add_argument("--result-json", default=None)
    c = sub.add_parser("compare")
    c.add_argument("before")
    c.add_argument("after")
    a = ap.parse_args(argv)
    if a.cmd == "compare":
        res = compare(json.loads(Path(a.before).read_text()), json.loads(Path(a.after).read_text()))
        print(json.dumps(res, indent=1))
        return 0 if res["identical"] else 1
    return run_cmd(a)


if __name__ == "__main__":
    raise SystemExit(main())
