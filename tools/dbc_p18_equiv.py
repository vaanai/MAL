#!/usr/bin/env python3
"""dbc_p18_equiv: decision-equivalence proof for P18 pool pruning (B1). PAPER ONLY. Counts, md5 and memory only; nothing is priced.

`run` streams the DBC exploration tape hours [2026-10-14T00, 2026-10-17T00) through ONE causal tools.dbc_fwd.Engine (live policy, no oracle: the book's configuration) and
writes its hash-chained decision log under OUT/declog plus OUT/equiv.json (md5 of the log, counts, engine stats, peak RSS and an RSS series). It is run twice, `--prune 0` and
`--prune 1`, in SEPARATE processes (so each peak RSS is its own); `compare` then requires the two decision-log md5s to be IDENTICAL (this tool's start row never records the prune flag; prune 0 is the pinned dbc_fwd.Engine itself).
`--limit-hours N` shortens the window for a quick run; the window is never wider than the exploration hours and the sealed trees are refused by path.

    python3 -m tools.dbc_p18_equiv run --prune 0 --out DIR0
    python3 -m tools.dbc_p18_equiv run --prune 1 --out DIR1
    python3 -m tools.dbc_p18_equiv compare DIR0 DIR1
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import json
import os
import resource
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tools import dbc_fwd as F
from tools import dbc_price as L
from tools import dbc_screen as S
from tools.dbc_p18_run import PrunedEngine

START = datetime(2026, 10, 14, 0, tzinfo=timezone.utc)
END = datetime(2026, 10, 17, 0, tzinfo=timezone.utc)  # exclusive: exploration hours only; [10-17, 10-24) is spent, >= 10-25 sealed
CFG = str(Path(__file__).resolve().parent.parent / "docs/research/dbc-p17/data/cfg_summary.json")


def window_guard(start: datetime, end: datetime, root: str | None, out: str) -> None:
    if start < START or end > END or start >= end:
        raise F.ReplayRefused(f"window_refused: [{start:%Y-%m-%dT%H}, {end:%Y-%m-%dT%H}) is not inside [2026-10-14T00, 2026-10-17T00)")
    for p in (root or "", os.path.realpath(root) if root else "", out, os.path.realpath(out)):
        if any(x in p for x in (*F.FORBIDDEN_PATH_PARTS, "meta-capture")):
            raise F.ReplayRefused(f"path_refused: {p}")
    if L.SPENT_ROOT is not None or L.HOLDOUT_READ_ENABLED or L.FWD_ACTIVE is not None:
        raise F.ReplayRefused("a non-default dbc_price data window is enabled")


def code_record() -> dict[str, Any]:
    """sha256 of the code that ran and the git head (so a proof is tied to its sha)."""
    import subprocess  # noqa: PLC0415

    here = Path(__file__).resolve().parent
    try:
        head = subprocess.run(["git", "-C", str(here), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=20).stdout.strip() or None
        dirty = bool(subprocess.run(["git", "-C", str(here), "status", "--porcelain", "--", "tools"], capture_output=True, text=True, timeout=20).stdout.strip())
    except Exception:  # noqa: BLE001
        head, dirty = None, None
    return {"git_head": head, "tools_dirty": dirty, "sha256": {f: F.sha256_file(here / f) for f in ("dbc_fwd.py", "dbc_p18_run.py", "dbc_p18_equiv.py")}}


def rss_mib() -> float:
    with open("/proc/self/statm") as f:
        return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1048576.0


def run(start: datetime, end: datetime, prune: bool, out: Path, root: str | None = None, rss_every_s: float = 10.0) -> dict[str, Any]:
    from tools.arena_declog import DecisionLog  # noqa: PLC0415

    window_guard(start, end, root, str(out))
    L.enable_swap3_pricing()
    n_cfg = L.load_config_thresholds(CFG)
    log_root = out / "declog"
    if log_root.exists() and any(log_root.rglob("*.jsonl")):
        raise SystemExit(f"REFUSED: {log_root} not empty")
    out.mkdir(parents=True, exist_ok=True)
    log = DecisionLog(str(log_root))
    first_ok = start.timestamp() + S.WARMUP_HOURS * 3600
    live_ss: dict[int, float] = {}
    kw: dict[str, Any] = dict(ss_live=lambda hk, m=live_ss: S.slot_s_for(m, hk), screen_policy=False, log=log, meta={"bt_src": "tape_block_time"})
    eng = PrunedEngine(first_ok, **kw) if prune else F.Engine(first_ok, **kw)  # prune 0 = the pinned dbc_fwd.Engine, untouched
    log.append(F.BOOK, int(start.timestamp() * 1000), {"type": "start", "book": F.BOOK, "mode": "p18-equiv", "window": [f"{start:%Y-%m-%dT%H}", f"{end:%Y-%m-%dT%H}"],
                                                       "cell": "m10", "exit_rule": F.EXIT_RULE, "schema": F.SCHEMA})
    series: list[list[float]] = []
    stop = threading.Event()
    t0 = time.time()

    def sampler() -> None:
        while not stop.wait(rss_every_s):
            series.append([round(time.time() - t0, 1), round(rss_mib(), 1), len(eng.pools)])

    th = threading.Thread(target=sampler, daemon=True)
    th.start()
    hs = F.stream_hours(eng, start, end, root, live_ss, log)
    stop.set()
    th.join()
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    rows_kept = sum(q.n() for q in eng.pools.values())
    res = {"prune": prune, "window": [f"{start:%Y-%m-%dT%H}", f"{end:%Y-%m-%dT%H}"], "hours_streamed": hs, "decisions_live": len(eng.decisions_live), "stats": eng.stats,
           "cfg_thresholds": n_cfg, "pools_end": len(eng.pools), "pools_dead": len(getattr(eng, "dead", {})), "rows_retained_end": rows_kept, "wall_s": round(time.time() - t0, 1),
           "peak_rss_mib": round(peak, 1), "peak_rss_source": "resource.ru_maxrss", "code": code_record(), "rss_series": series, "declog": {"md5": log.md5(), "head": list(log.verify(F.BOOK))},
           "decision_exit_key_md5": F.md5_keys(f"{r['type']}|{r.get('key')}|{r.get('fire_slot')}|{r.get('exit_slot')}" for r in log.rows(F.BOOK) if r["type"] in ("decision", "exit")),
           "taken_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    (out / "equiv.json").write_text(json.dumps(res, indent=1, default=float))
    return res


def compare(a: Path, b: Path) -> dict[str, Any]:
    ra, rb = json.loads((a / "equiv.json").read_text()), json.loads((b / "equiv.json").read_text())
    same = ra["declog"]["md5"] == rb["declog"]["md5"] and ra["declog"]["head"] == rb["declog"]["head"] and ra["window"] == rb["window"]
    return {"identical": bool(same), "md5": [ra["declog"]["md5"], rb["declog"]["md5"]], "head": [ra["declog"]["head"], rb["declog"]["head"]], "decisions": [ra["decisions_live"], rb["decisions_live"]],
            "prune": [ra["prune"], rb["prune"]], "peak_rss_mib": [ra["peak_rss_mib"], rb["peak_rss_mib"]], "rows_after_drop": rb["stats"].get("rows_after_drop"),
            "trim_window_clamped": rb["stats"].get("trim_window_clamped"), "trim_rows": rb["stats"].get("trim_rows"), "pools_dropped": rb["stats"].get("pools_dropped")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--prune", type=int, choices=(0, 1), required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--root", default=None)
    r.add_argument("--limit-hours", type=int, default=72)
    c = sub.add_parser("compare")
    c.add_argument("a")
    c.add_argument("b")
    ns = ap.parse_args(argv)
    if ns.cmd == "compare":
        res = compare(Path(ns.a), Path(ns.b))
        print(json.dumps(res))
        return 0 if res["identical"] else 1
    try:
        res = run(START, min(END, START + timedelta(hours=ns.limit_hours)), bool(ns.prune), Path(ns.out), ns.root)
    except F.ReplayRefused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 3
    print(json.dumps({k: res[k] for k in ("prune", "hours_streamed", "decisions_live", "peak_rss_mib", "wall_s", "declog", "pools_end", "pools_dead", "rows_retained_end")}))
    print(json.dumps(res["stats"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
