#!/usr/bin/env python3
"""r4-b live decision path: md5 decision-equivalence replay on EXPLORATION hours (explore-0814), live engine vs the pinned EXP-027 decision calls.

  batch  tools/r4b_layout.build (v_source static_map, pool_v_0909.json, as E-d) -> tools/r4b_read.build_rows (asof features, D3 drop, W0..E-1h marks)
         -> tools/r4b_score.bag_score -> tools/r4b_book.xs_book: exactly the decision calls of r4b_read.run_deciding.
  live   tools/r4b_live.replay_walker: the streaming Engine fed hour by hour from the same clean-view files, deciding each mark as it would live.
  equal  md5 of the scored decision rows (mint, T, sd, 63 float32 features, float64 score) and md5 of the book's trades (mint, T0, T1, reason).

Exploration block only (hunt23/RULES.md): /data/mal/clean-view/explore-0814 windows w7, w6, w5. No walk-2 hour, no October hour, no sealed path.
Output: md5s, counts and decision timings only. No price, no P&L, no fill, no exit state is written or printed (the batch side computes entry states
inside build_rows because the pinned function does; they are dropped unread).

Run (MiScusi job on mal-research-0, inside a memory cap; the wrapper scripts/research/r4b-live-equiv.sh does this):
  PYTHONPATH=/data/mal/audit-1008/venv/lib/python3.12/site-packages systemd-run --user --scope -q -p MemoryMax=20G -p MemorySwapMax=0 \
      /data/mal/venv/bin/python -s scripts/research/r4b_live_equiv.py --out <dir>
Exit 0 iff both md5s are equal.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tools import r4b_live as lv  # noqa: E402

CLEAN = Path("/data/mal/clean-view/explore-0814")
WINDOWS = ("w7", "w6", "w5")
H0, H1 = "2026-08-14T12", "2026-08-19T12"
W0, E = "2026-08-16T12", "2026-08-19T12"  # 48 h ramp from H0 (as EXP-027: H0 -> W0), 72 entry marks [W0, E - 1 h]
POOL_V = Path("/data/mal/pumpswap-virtual/pool_v_0909.json")
CLEAN_MOUNT, CLEAN_UNDER = Path("/data/mal/clean-view"), Path("/data2/mal/clean-view")  # bind mount: link() cannot cross mount points
MERGED_PARENT = Path("/data2/mal/scratch/r4b-live")


def memory_cap_problem(max_bytes: int = 24 * 10**9) -> str | None:
    try:
        path = next(ln.split(":", 2)[2].strip() for ln in open("/proc/self/cgroup") if ln.startswith("0::"))
        cur = Path("/sys/fs/cgroup") / path.lstrip("/")
        while True:
            f = cur / "memory.max"
            if f.is_file() and f.read_text().strip() != "max":
                return None if int(f.read_text().strip()) <= max_bytes else "cgroup MemoryMax above 24 GB"
            if cur == Path("/sys/fs/cgroup") or cur.parent == cur:
                break
            cur = cur.parent
    except (OSError, StopIteration, ValueError):
        pass
    return "no MemoryMax found: run inside systemd-run --user --scope -p MemoryMax=20G (scripts/research/r4b-live-equiv.sh)"


def link_merged(dst: Path, hours: list[str]) -> None:
    """One walker-layout dir over the windows (hard links; the clean-view files are never written)."""
    for sub in ("trades", "creates", "migrations"):
        (dst / sub).mkdir(parents=True, exist_ok=True)
        for h in hours:
            name = f"{sub}-{h}.jsonl.zst"
            src = next((CLEAN / w / sub / name for w in WINDOWS if (CLEAN / w / sub / name).is_file()), None)
            if src is None:
                raise SystemExit(f"hour {h}: no {sub} file in {WINDOWS}")
            if lv.path_problem(src):
                raise SystemExit(f"refused: {lv.path_problem(src)}")
            under = Path(str(src).replace(str(CLEAN_MOUNT), str(CLEAN_UNDER), 1))  # the same inode on /data2 (clean-view is a bind mount of it)
            a, b = os.stat(src), os.stat(under)
            if (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino):
                raise SystemExit(f"refused: {under} is not the same file as {src}")
            if not (dst / sub / name).exists():
                os.link(under, dst / sub / name)  # hard link (zstd refuses symlinks); no copy, the source is not modified


def line_count_verify(merged: Path, hours: list[str]) -> dict:
    from tools import tape_lines

    return {h: {"content": {s: {"rows": tape_lines.scan_file(merged / s / f"{s}-{h}.jsonl.zst").lines} for s in ("trades", "creates", "migrations")}} for h in hours}


def batch(out: Path, boosters, smap, umin: int, progress) -> dict:
    from tools import r4b_book as bk
    from tools import r4b_layout as rl
    from tools import r4b_read as rr
    from tools import r4b_score as rs

    hours = rl.hour_range(H0, H1)
    ext = rl.hour_range(H1, lv.hour_key(datetime.fromtimestamp(lv.hour_epoch(H1) + 7200, tz=timezone.utc)))
    merged = out / "merged"  # out must be on /data2 (the clean-view filesystem and mount)
    link_merged(merged, hours + ext)
    ver = line_count_verify(merged, hours + ext)
    t = time.monotonic()
    stats = rl.build(merged, H0, H1, out / "adapter", v_source="static_map", static_map=smap, problems_fn=lambda *_: [], verify=ver, memory_limit="6GB",
                     threads=4, tmp_dir=out / "tmp")
    progress(f"batch layout done {time.monotonic() - t:.0f}s")
    plan = SimpleNamespace(w0=lv.hour_dt(W0), e=lv.hour_dt(E))
    days = sorted({datetime.fromtimestamp(lv.hour_epoch(H0) + 86400 * i, tz=timezone.utc).strftime("%Y-%m-%d") for i in range(9)})
    R, rep, con = rr.build_rows(out / "adapter", plan, {"delta_slots": 0.0}, {d: 0.4 for d in days}, SimpleNamespace(threads=4, memory_limit="6GB"), umin)
    meta = dict(con.sql(f"select mid, mint from read_parquet('{out / 'adapter'}/meta.parquet')").fetchall())
    con.close()
    del R.ent, R.marks, R.spot  # entry / mark states: not used, not written
    mid = np.concatenate(R.mid)
    T = np.concatenate(R.T)
    sd = np.concatenate(R.sd)
    X = np.vstack(R.X)
    score = rs.bag_score(boosters, X)
    b = bk.xs_book(T, mid, score)
    trades = sorted(((meta[int(m)], int(a), int(c), str(r)) for m, a, c, r in zip(b["mid"], b["T0"], b["T1"], b["reason"])), key=lambda x: (x[1], x[0]))
    mints = [meta[int(m)] for m in mid]
    progress(f"batch done {time.monotonic() - t:.0f}s rows {len(mints)}")
    per_mark = {}
    for m, t_ in zip(mints, T.tolist()):
        per_mark[t_] = per_mark.get(t_, 0) + 1
    return {"rows_md5": lv.rows_md5(mints, T, sd, X, score), "trades_md5": lv.trades_md5(trades), "n_rows": len(mints), "n_trades": len(trades),
            "d3_dropped": int(rep["d3_dropped"]), "members": int(stats["members"]), "rows_per_mark": per_mark, "trades": trades,
            "reasons": {r: sum(1 for x in trades if x[3] == r) for r in ("rank", "gap", "end")}, "seconds": time.monotonic() - t}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--h0", default=H0)
    ap.add_argument("--h1", default=H1)
    ap.add_argument("--w0", default=W0)
    ap.add_argument("--e", default=E)
    ap.add_argument("--keep-scratch", action="store_true")
    ap.add_argument("--trigger", choices=("guard", "certified"), default="guard", help="the live engine's decide trigger (P-a1: certified)")
    a = ap.parse_args()
    globals().update(H0=a.h0, H1=a.h1, W0=a.w0, E=a.e)
    if not (a.h0 <= a.w0 < a.e <= a.h1 and a.h1 <= "2026-08-20T10"):
        raise SystemExit("refused: need h0 <= w0 < e <= h1 inside explore-0814 w7..w5 (h1 <= 2026-08-20T10)")
    out = Path(a.out)
    for s in lv.SEALED_PATTERNS:
        if s in os.path.realpath(str(out)):
            raise SystemExit(f"refused: out dir matches {s!r}")
    why = memory_cap_problem()
    if why:
        raise SystemExit(why)
    if not os.path.realpath(str(out)).startswith(str(MERGED_PARENT) + "/"):
        raise SystemExit(f"refused: --out must be under {MERGED_PARENT} (hard links to clean-view)")
    out.mkdir(parents=True, exist_ok=True)
    prog_path = os.environ.get("MISCUSI_PROGRESS")

    def progress(msg: str) -> None:
        print(f"{datetime.now(timezone.utc):%H:%M:%SZ} {msg}", flush=True)
        if prog_path:
            Path(prog_path).write_text(json.dumps({"note": msg[:200]}))

    from tools import r4b_layout as rl
    from tools import r4b_score as rs

    boosters = rs.load_boosters()
    smap = rl.load_static_map(POOL_V)
    umin = lv.hour_epoch(H0)
    t0 = time.monotonic()
    live = lv.replay_walker([CLEAN / w for w in WINDOWS], H0, H1, lv.hour_epoch(W0), lv.hour_epoch(E), static_map=smap, universe_min_mbt=umin,
                            boosters=boosters, log_root=out / "declog", progress=progress, trigger=a.trigger)
    live_s = time.monotonic() - t0
    progress(f"live replay done {live_s:.0f}s rows {live.n_rows}")
    bat = batch(out, boosters, smap, umin, progress)
    rows_eq, trades_eq = live.rows_md5 == bat["rows_md5"], live.trades_md5 == bat["trades_md5"]
    diag = {}
    if not trades_eq:
        lt, bt = set(live.trades), set(bat["trades"])
        diag["trades_only_live"], diag["trades_only_batch"] = len(lt - bt), len(bt - lt)
        diffm = sorted({x[1] for x in lt ^ bt})
        diag["first_differing_t0"] = diffm[0] if diffm else None
    lpm = {r.t: len(r.mints) for r in live.marks}
    diag["marks_with_row_count_diff"] = sorted(t for t in set(lpm) | set(bat["rows_per_mark"]) if lpm.get(t, 0) != bat["rows_per_mark"].get(t, 0))[:10]
    sec = np.array(live.seconds)
    rep = {
        "schema": "r4b_live_equiv_v1", "trigger": a.trigger, "block": str(CLEAN), "windows": list(WINDOWS), "hours": [H0, H1], "w0": W0, "e": E, "marks": len(live.marks),
        "verdict": "PASS" if (rows_eq and trades_eq) else "FAIL", "rows_md5_equal": rows_eq, "trades_md5_equal": trades_eq,
        "live": {"rows_md5": live.rows_md5, "trades_md5": live.trades_md5, "n_rows": live.n_rows, "n_trades": len(live.trades), "counters": live.counters,
                 "declog_md5": live.log_md5, "seconds_total": round(live_s, 1),
                 "decide_seconds": {"p50": float(np.median(sec)), "p90": float(np.percentile(sec, 90)), "max": float(sec.max())} if len(sec) else None},
        "batch": {k: v for k, v in bat.items() if k not in ("trades", "rows_per_mark")}, "diag": diag,
        "git_head": os.popen(f"git -C {REPO} rev-parse HEAD").read().strip(),
        "note": "md5s, counts and timings only; no price, P&L, fill or exit state. Exploration block explore-0814 (hunt23/RULES.md).",
    }
    if not a.keep_scratch:  # spill: the adapter parquet, the hard links and DuckDB temp go; report.json and declog/ stay
        import shutil

        for d in (out / "adapter", out / "tmp", out / "merged"):
            shutil.rmtree(d, ignore_errors=True)
    (out / "report.json").write_text(json.dumps(rep, indent=1, sort_keys=True, default=str) + "\n")
    print(json.dumps({k: rep[k] for k in ("verdict", "rows_md5_equal", "trades_md5_equal", "marks")}, sort_keys=True))
    res = os.environ.get("MISCUSI_RESULT")
    if res:
        Path(res).write_text(json.dumps({"ok": True, "verdict": rep["verdict"], "summary": f"r4b live vs pinned batch on explore-0814 {H0}..{H1}: rows_md5_equal={rows_eq} trades_md5_equal={trades_eq}",
                                         "metrics": {"rows": live.n_rows, "trades": len(live.trades), "marks": len(live.marks),
                                                     "decide_p50_s": rep["live"]["decide_seconds"]["p50"] if len(sec) else None}}))
    return 0 if rows_eq and trades_eq else 1


if __name__ == "__main__":
    raise SystemExit(main())
