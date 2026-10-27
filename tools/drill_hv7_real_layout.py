#!/usr/bin/env python3
"""HV7 real-layout replay (chunk C3b): the HV7 runner over the hour files of /data/mal/arena/tape for the pre-clock days 2026-10-21..23, three times.

PAPER only. StubModel (no API), a fake chain (no RPC). Read-only on the tape; writes only under <HV7_ROOT>/drill/<drill-id>/. Modelled on
tools/drill_hv6_real_layout.py (same loader: ArenaState over the real hour files, its hour list filtered to the chosen days).

Refused: any day outside {2026-10-21, 2026-10-22, 2026-10-23}, a non-contiguous list, a tape directory other than /data/mal/arena/tape, a drill id with a path
part. The tape reader is filtered to the chosen days' hour files, so every other hour (10-19, 10-20, anything at or after 2026-10-24T00Z) is listed by name
only (a directory glob) and never opened. Pools created before the first chosen day are unknown to the replay (structure test, not a trading test).

THREE RUNS, each in its OWN process (own heap, own peak RSS):
  real-a  PYTHONHASHSEED=0
  real-b  PYTHONHASHSEED=0                           (run 2 = run 1: same inputs, same code)
  real-c  PYTHONHASHSEED=<other>, slow path           ("any fast path off"). hv7_runner has NO fast path (it never passes the candle series to hv6_cards.scan; the
          HV6 fast_scan switch does not exist in it, and a test pins that), so there is nothing to switch off; run 3 instead varies the string-hash seed,
          which exposes any decision that depends on set/dict-of-string iteration order.
PASS iff md5_equal (run1 == run2 == run3 on the decision-log md5 and on the cards md5, and the entry counts) AND periods_ge3 > 0 (not a vacuous pass) AND
peak_rss_mb (max of the three processes) < rss_cap_mb AND the per-period loop time is OK: p95 of (tick at t_d + tick at t_e), runs 1 and 2, < PERIOD_P95_CAP_MS,
AND the p95 of every one-second tick < TICK_P95_CAP_MS. Run 3's timing is printed, never tested. The caps were written down before any run.

PRINTS ONLY: md5s, counts (periods, periods with >= 3 screened candidates, entries per arm, fallbacks, exclusions, counters), peak RSS, timings.
Never a pool, mint, signature, price, P&L or per-pool row. The engine ledger sits in the drill root and is never read by a person. Pre-clock structure, not
evidence. No outcome, exit or P&L is printed; sell/close counts are not printed either.
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import hashlib
import json
import os
import re
import resource
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

os.environ.setdefault("MAL_ARENA_TEST", "1")  # the replay engine may lower its caps and the drill registry is test-registered; same switch the HV6 drill uses

from tools import hv6_agents as A  # noqa: E402
from tools import hv6_cards as H  # noqa: E402
from tools import hv7_rank as RK  # noqa: E402
from tools import hv7_runner as R  # noqa: E402
from tools.arena_paper import ArenaEngine, register_bot  # noqa: E402
from tools.arena_state import ArenaState  # noqa: E402
from tools.drill_hv6_item6 import FakeChain, cards_md5, log_md5, md5_bytes, out  # noqa: E402,F401
from tools.drill_hv6_real_layout import day_ms, hour_scan, pct, seal_read_only  # noqa: E402

REAL_TAPE = H.TAPE_ROOT
ALLOWED_DAYS = ("2026-10-21", "2026-10-22", "2026-10-23")   # pre-clock days only; nothing else is ever opened
END_EXCLUSIVE = "2026-10-24"
DRILL_ROOT = Path(R.HV7_ROOT) / "drill"
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,80}")
DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
RSS_CAP_MB = 12_288.0
PERIOD_P95_CAP_MS = 15_000.0     # tick at t_d (screen + cards) plus tick at t_e (entries): well inside the 45 s between them, leaving the model call its room
TICK_P95_CAP_MS = 1_000.0        # every one-second tick must be able to keep up with the clock
FEED_DELAY_SLOTS = 2
HASHSEEDS = {"real-a": "0", "real-b": "0", "real-c": "1234567"}
RUNS = tuple(HASHSEEDS)
PASS_RULE = ("PASS iff md5_equal (decision-log md5 and cards md5 of run1 == run2 == run3, and equal entry counts) AND periods_ge3 > 0 AND peak_rss_mb (max of the three "
             "processes) < rss_cap_mb AND period_ms_p95 (max of runs 1 and 2; tick at t_d + tick at t_e) < 15000 AND tick_ms_p95 (max of runs 1 and 2) < 1000; run 3 "
             "(other PYTHONHASHSEED; hv7_runner has no fast path to switch off) is tested for md5 only, its timing is report-only")


class Refused(Exception):
    pass


def parse_days(arg: str) -> list[str]:
    days = [d.strip() for d in arg.split(",") if d.strip()]
    if not days or len(set(days)) != len(days):
        raise Refused("days_empty_or_duplicate")
    for d in days:
        if not DAY_RE.fullmatch(d):
            raise Refused("day_format")
        if d not in ALLOWED_DAYS:
            raise Refused("day_outside_window")
    dts = [datetime.strptime(d, "%Y-%m-%d") for d in days]
    if dts != sorted(dts) or any((b - a) != timedelta(days=1) for a, b in zip(dts, dts[1:])):
        raise Refused("days_not_contiguous_ascending")
    return days


def is_drill_root(path: str | os.PathLike) -> bool:
    real = os.path.realpath(path)
    drill = os.path.realpath(DRILL_ROOT)
    return real.startswith(drill + os.sep)


def script(req: Any) -> str:
    """The StubModel's rank: a fixed permutation of the shown ids (sha256 of the id). Deterministic, uses no outcome."""
    order = sorted(req.ids, key=lambda i: hashlib.sha256(i.encode()).hexdigest())
    return json.dumps([{"id": i, "why": "f.vol_60m is highest"} for i in order])


def new_run(base: Path, tag: str, sha: str, days: list[str], tape: Path, *, start_ms: int, end_ms: int) -> R.Hv7Runner:
    d = base / tag
    root = d / "v7"
    guard = R.Hv7Guard(root)
    model = A.StubModel(script, guard=guard, latency_ms=50)
    reg = d / "reg.json"
    eng = ArenaEngine(root / "engine-ledger", reg, cfg=R.stack_config(FEED_DELAY_SLOTS), sps_fn=lambda: 0.4)
    if not reg.exists():
        for arm in R.ARM_ORDER:
            register_bot(R.bot_id(arm), sha[:13], start_ms - 60 * 60_000, str(reg), deciding_stake=0.25, model_ids=[A.MODEL_ID] if arm == "L" else [],
                         now_ms=start_ms - 61 * 60_000, verify_sha=False)
    run = R.Hv7Runner(R.Hv7Config(root=str(root), frozen_sha=sha[:13], clock_start_ms=start_ms - 60 * 60_000), ArenaState(tape, start_ms=0), eng, model, FakeChain(), guard)
    orig, want = run.state._hour_files, frozenset(days)
    run.state._hour_files = lambda: [t for t in orig() if t[0][:10] in want and t[0] < END_EXCLUSIVE]     # other hours: listed by name, never opened
    return run


def summarize(run: R.Hv7Runner) -> dict:
    scan = run.log.rows("SCAN")
    per = [r for r in scan if r.get("kind") == "period"]
    ranks = [r for r in run.log.rows("L") if r.get("kind") == "rank"]
    return {"periods": len(per), "periods_ge3": sum(1 for r in per if not r.get("no_trade")),
            "periods_no_trade": sum(1 for r in per if r.get("no_trade")),
            "periods_with_ge3_raw_candidates": sum(1 for r in per if int(r.get("n_candidates", 0)) >= RK.N_MIN),
            "entries": {a: sum(1 for r in run.log.rows(a) if r.get("kind") == "entry") for a in R.ARM_ORDER},
            "order_refused": sum(1 for a in R.ARM_ORDER for r in run.log.rows(a) if r.get("kind") == "order_refused"),
            "rank_rows": sum(1 for r in ranks if not r.get("excluded")),
            "fallbacks": sum(1 for r in ranks if r.get("fallback") is True and not r.get("excluded")),
            "exclusions": sum(1 for r in scan if r.get("kind") == "period_excluded"),
            "gaps": sum(1 for r in scan if r.get("kind") == "gap"),
            "counters": dict(sorted(run.counters.items())), "hook_errors": len(run.hook_errors)}


def one_run(base: Path, tag: str, sha: str, days: list[str], tape: Path, *, start_ms: int | None = None, end_ms: int | None = None) -> dict:
    start = day_ms(days[0]) if start_ms is None else start_ms
    end = day_ms(days[-1]) + 86_400_000 if end_ms is None else end_ms
    run = new_run(base, tag, sha, days, tape, start_ms=start, end_ms=end)
    tick_ms: list[float] = []
    period_ms: dict[int, float] = {}
    orig = run.tick

    def tick(T: int) -> None:
        t = time.perf_counter()
        try:
            return orig(T)
        finally:
            ms = (time.perf_counter() - t) * 1000.0
            tick_ms.append(ms)
            m = T % R.HOUR_MS
            if m in (R.T_D_OFFSET_MS, R.T_E_OFFSET_MS):
                period_ms[T // R.HOUR_MS] = period_ms.get(T // R.HOUR_MS, 0.0) + ms

    run.tick = tick
    run.sim_now = start
    run.log_start()
    run.run(start, end)
    for x in (*R.ARM_ORDER, "SCAN"):
        run.log.verify(x)
    root = Path(run.cfg.root)
    res = summarize(run)
    pm = list(period_ms.values())
    res.update({"log_md5": log_md5(root), "cards_md5": md5_bytes(b"") if not (root / "cards").exists() else cards_md5(root),
                "ticks": len(tick_ms), "tick_ms_p50": round(pct(tick_ms, 0.5), 2), "tick_ms_p95": round(pct(tick_ms, 0.95), 2),
                "tick_ms_max": round(max(tick_ms), 2) if tick_ms else 0.0, "period_ms_p50": round(pct(pm, 0.5), 2), "period_ms_p95": round(pct(pm, 0.95), 2),
                "period_ms_max": round(max(pm), 2) if pm else 0.0, "period_timings": len(pm)})
    return res


def child_one_run(tag: str, days: list[str], drill_id: str, sha: str) -> int:
    """One replay in ITS OWN process. Writes <drill root>/<tag>.json (counts, md5s, this process's peak RSS)."""
    base = DRILL_ROOT / drill_id
    if not ID_RE.fullmatch(drill_id) or tag not in RUNS or not is_drill_root(base):
        return 2
    try:
        res = one_run(base, tag, sha, days, Path(REAL_TAPE))
        res["peak_rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)
        res["hashseed"] = os.environ.get("PYTHONHASHSEED")
    except Exception as e:  # noqa: BLE001 - the class name only
        res = {"error_class": type(e).__name__}
    (base / f"{tag}.json").write_text(json.dumps(res, sort_keys=True, separators=(",", ":")) + "\n")
    return 0 if "error_class" not in res else 3


def _isolated_run(tag: str, days: list[str], drill_id: str, sha: str, base: Path) -> dict:
    import subprocess

    root = Path(__file__).resolve().parent.parent
    env = {**os.environ, "PYTHONHASHSEED": HASHSEEDS[tag]}
    r = subprocess.run([sys.executable, "-B", "-m", "tools.drill_hv7_real_layout", "--one-run", tag, "--days", ",".join(days), "--drill-id", drill_id, "--sha", sha],
                       cwd=str(root), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    f = base / f"{tag}.json"
    if r.returncode != 0 or not f.exists():
        raise RuntimeError(f"child_{tag}_rc{r.returncode}")
    res = json.loads(f.read_text())
    if "error_class" in res:
        raise RuntimeError("child_error_" + str(res["error_class"])[:40])
    return res


def verdict(a: dict, b: dict, c: dict, rss_mb: float, rss_cap_mb: float) -> dict:
    equal12 = a["log_md5"] == b["log_md5"] and a["cards_md5"] == b["cards_md5"] and a["entries"] == b["entries"]
    equal3 = c["log_md5"] == a["log_md5"] and c["cards_md5"] == a["cards_md5"] and c["entries"] == a["entries"]
    period_p95 = max(a["period_ms_p95"], b["period_ms_p95"])
    tick_p95 = max(a["tick_ms_p95"], b["tick_ms_p95"])
    non_vacuous = a["periods_ge3"] > 0
    ok = equal12 and equal3 and non_vacuous and rss_mb < rss_cap_mb and period_p95 < PERIOD_P95_CAP_MS and tick_p95 < TICK_P95_CAP_MS
    return {"result": "PASS" if ok else "FAIL", "pass_rule": PASS_RULE, "md5_equal": equal12 and equal3, "md5_equal_run1_run2": equal12, "md5_equal_run3_other_hashseed": equal3,
            "non_vacuous": non_vacuous, "rss_ok": rss_mb < rss_cap_mb, "period_p95_ok": period_p95 < PERIOD_P95_CAP_MS, "tick_p95_ok": tick_p95 < TICK_P95_CAP_MS,
            "period_ms_p95": period_p95, "tick_ms_p95": tick_p95, "peak_rss_mb": rss_mb, "rss_cap_mb": rss_cap_mb}


def run_real(days: list[str], drill_id: str, sha: str, *, rss_cap_mb: float = RSS_CAP_MB, tape_dir: str | None = None, base: Path | None = None, isolate: bool = True) -> dict:
    tape_dir = REAL_TAPE if tape_dir is None else tape_dir
    if tape_dir != REAL_TAPE:
        raise Refused("tape_dir_not_allowed")
    if not isinstance(drill_id, str) or not ID_RE.fullmatch(drill_id):
        raise Refused("drill_id")
    base = Path(base) if base is not None else DRILL_ROOT / drill_id
    if not is_drill_root(base):
        raise Refused("not_a_drill_root")
    R.check_path(base / "x", "w")
    base.mkdir(parents=True, exist_ok=True)
    info = hour_scan(Path(tape_dir), days)
    if info["hours_present"] == 0:
        raise Refused("no_hour_files_for_the_days")
    out({"hv7_real_layout": "start", "sha": sha, "days": len(days), "first_day": days[0], "last_day": days[-1], **info, "model": "StubModel", "api_calls": 0,
         "chain": "fake", "runs": list(RUNS), "pass_rule": PASS_RULE, "rss_cap_mb": rss_cap_mb})
    if isolate:
        a, b, c = (_isolated_run(t, days, drill_id, sha, base) for t in RUNS)
        rss_mb = max(x.pop("peak_rss_mb") for x in (a, b, c))
    else:
        a, b, c = (one_run(base, t, sha, days, Path(tape_dir)) for t in RUNS)
        rss_mb = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)
    v = verdict(a, b, c, rss_mb, rss_cap_mb)
    res = {"hv7_real_layout": "end", **v, "run1_log_md5": a["log_md5"], "run2_log_md5": b["log_md5"], "run3_log_md5": c["log_md5"], "run1_cards_md5": a["cards_md5"],
           "run3_cards_md5": c["cards_md5"],
           "counts": {k: a[k] for k in ("periods", "periods_ge3", "periods_no_trade", "periods_with_ge3_raw_candidates", "entries", "order_refused", "rank_rows", "fallbacks",
                                        "exclusions", "gaps", "counters", "hook_errors")},
           "run2_counts_equal": {k: a[k] == b[k] for k in ("periods", "periods_ge3", "entries", "fallbacks", "exclusions", "counters")},
           "timing": {"run1": {k: a[k] for k in ("ticks", "tick_ms_p50", "tick_ms_p95", "tick_ms_max", "period_ms_p50", "period_ms_p95", "period_ms_max")},
                      "run2": {k: b[k] for k in ("ticks", "tick_ms_p50", "tick_ms_p95", "tick_ms_max", "period_ms_p50", "period_ms_p95", "period_ms_max")},
                      "run3_report_only": {k: c[k] for k in ("tick_ms_p95", "period_ms_p95")}}}
    out(res)
    rf = base / "real-layout-result.json"
    rf.write_text(json.dumps(res, sort_keys=True, separators=(",", ":")) + "\n")
    digest = hashlib.sha256(rf.read_bytes()).hexdigest()
    seal_read_only(base)
    out({"hv7_real_layout": "sealed", "root_read_only": True, "result_file": "real-layout-result.json", "result_sha256": digest})
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="HV7 real-layout replay: StubModel, fake chain, read-only on /data/mal/arena/tape, days 2026-10-21..23 only. Prints md5 and counts only.")
    ap.add_argument("--days", required=True, help="contiguous UTC days within 2026-10-21..2026-10-23")
    ap.add_argument("--drill-id", required=True)
    ap.add_argument("--sha", required=True)
    ap.add_argument("--rss-cap-mb", type=float, default=RSS_CAP_MB)
    ap.add_argument("--one-run", default=None, help="internal: run one replay (real-a, real-b or real-c) in this process and write its result file")
    a = ap.parse_args(argv)
    try:
        days = parse_days(a.days)
        if a.one_run:
            return child_one_run(a.one_run, days, a.drill_id, a.sha)
        res = run_real(days, a.drill_id, a.sha, rss_cap_mb=a.rss_cap_mb)
    except Refused as e:
        print(json.dumps({"hv7_real_layout": "refused", "why": str(e)}), file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001 - the class name only: a traceback or message could echo a row value
        print(json.dumps({"hv7_real_layout": "error", "class": type(e).__name__}), file=sys.stderr)
        return 3
    return 0 if res["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
