#!/usr/bin/env python3
"""P-C2 of QP-RULING-1031-r4b-history: the explore-0814 md5 replay THROUGH A TIP-FORMAT READER (certified trigger).

  1. The walker rows of explore-0814 (hunt23/RULES.md exploration block: /data/mal/clean-view/explore-0814 w7, w6, w5) are rewritten in the follower's output format:
     t_recv_ms = block_time * 1000 + LAG_MS (1,800 ms, the tip's measured block lag), source "tip", the singular virtual_quote_reserve stamp (null), bucketed into
     `trades-/creates-/migrations-<hour of t_recv_ms>.jsonl` by that hour, so rows cross the hour files exactly at the marks. A checkpoint.json says everything is done.
  2. tools/r4b_tip_reader.TipReader reads them (the hour floor is lowered to the exploration hours: harness only, never on /var/lib/mal/sealed), the rows go through
     tools/r4b_host_glue.TipAdapter + tools/r4b_feed_adapter.apply (Engine.ingest_trade / _create / _migration, one row at a time, bond_mints None as live), and every
     mark in [W0, E - 1 h] is decided with decide(T, certified_sd=...) once the stream holds a row with block_time >= T + --decide-after-s.
  3. rows md5 and trades md5 must equal the pinned ones (--expect-rows / --expect-trades) and, with --compare-replay, tools/r4b_live.replay_walker's on the same window.
Output: md5s, counts, timings. No price, P&L, fill or exit state. Exploration block only; no October hour; no sealed path. Exit 0 iff the md5s are equal.
Run as a MiScusi job on mal-research-0 inside systemd-run --user --scope -p MemoryMax=20G (scripts/research/r4b-tip-equiv.sh).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tools import r4b_feed_adapter as fa  # noqa: E402
from tools import r4b_host_glue as hg  # noqa: E402
from tools import r4b_layout as rl  # noqa: E402
from tools import r4b_live as lv  # noqa: E402
from tools import r4b_tip_reader as tr  # noqa: E402

CLEAN = Path("/data/mal/clean-view/explore-0814")
WINDOWS = ("w7", "w6", "w5")
POOL_V = Path("/data/mal/pumpswap-virtual/pool_v_0909.json")
SCRATCH_PARENT = Path("/data2/mal/scratch/r4b-tip-equiv")
LAG_MS = 1800
MIN_HOUR = "2026-08-01T00"


def hkey_ms(ms: int) -> str:
    return tr.hour_key(ms // 1000)


def build_tip_dir(con, tip: Path, state: Path, h0: str, h1: str, extra_hours: int, progress) -> dict:
    """Walker hours [h0, h1) (migrations, creates) and [h0, h1 + extra) (trades) -> tip-format files. Rows are sorted (slot, tx_index, event_index) inside a walker hour."""
    tip.mkdir(parents=True, exist_ok=True)
    state.mkdir(parents=True, exist_ok=True)
    hours = rl.hour_range(h0, h1)
    trade_hours = hours + [lv.hour_key(lv.hour_dt(h1) + timedelta(hours=i)) for i in range(extra_hours)]
    fh: dict = {}
    n = {"trades": 0, "creates": 0, "migrations": 0}
    last = {"slot": 0, "t": 0}

    def src(sub: str, h: str) -> Path:
        for w in WINDOWS:
            p = rl._hour_file(CLEAN / w / sub, sub, h)
            if p is not None:
                why = lv.path_problem(p)
                if why:
                    raise SystemExit(f"refused: {why}")
                return p
        raise SystemExit(f"hour {h}: no {sub} file")

    def put(sub: str, row: dict) -> None:
        bt = row.get("block_time")
        t = (int(bt) * 1000 + LAG_MS) if bt is not None else last["t"]
        last["t"] = max(last["t"], t)
        row = {**row, "t_recv_ms": last["t"], "source": "tip"}
        if sub == "trades":
            row["virtual_quote_reserve"] = None
        name = f"{sub}-{hkey_ms(last['t'])}.jsonl"
        f = fh.get(name)
        if f is None:
            f = fh[name] = open(tip / name, "ab")
        f.write(json.dumps(row, separators=(",", ":")).encode() + b"\n")
        n[sub] += 1
        last["slot"] = max(last["slot"], int(row["slot"]))

    key = lambda r: (r["slot"], -1 if r.get("tx_index") is None else r["tx_index"], -1 if r.get("event_index") is None else r["event_index"])  # noqa: E731
    for h in trade_hours:
        if h < h1:
            for sub, cols in (("migrations", rl.MIG_COLS), ("creates", rl.CREATE_COLS)):
                for r in sorted(lv._read_rows(con, src(sub, h), cols), key=lambda r: (r["slot"], r.get("block_time") or 0)):
                    put(sub, r)
        for r in sorted(lv._read_rows(con, src("trades", h), rl.TRADE_COLS), key=key):
            put("trades", r)
        progress(f"tip-format {h}: {n}")
    for f in fh.values():
        f.close()
    (state / "checkpoint.json").write_text(json.dumps({"last_done": last["slot"], "last_t_recv_ms": last["t"], "gaps": 0, "skipped": 0, "writing": None}))
    return {"rows": n, "files": len(fh), "last_slot": last["slot"], "trade_hours": trade_hours}


def run_stream(a, con, smap, boosters, tip: Path, state: Path, progress) -> dict:
    h0, h1 = a.h0, a.h1
    w0, e = lv.hour_epoch(a.w0), lv.hour_epoch(a.e)
    eng = lv.Engine(universe_min_mbt=lv.hour_epoch(h0), v_source="static_map", static_map=smap, mode="replay", boosters=boosters, bond_mints=None,
                    hash_fn=lambda s: int(con.execute("select hash(?::varchar)", [s]).fetchone()[0]))
    reader = tr.TipReader(tip, state, start_ms=lv.hour_epoch(h0) * 1000, catch_up_hours=0, min_hour=MIN_HOUR, lookback_h=None, fadvise=False)
    adapter = hg.TipAdapter()
    pending = list(range(w0, e, lv.MARK_S))
    last_entry = e - lv.MARK_S
    results, actions = [], []
    rows_in, t0, hour_seen = 0, time.monotonic(), None

    def decide_due(bt: float) -> None:
        while pending and bt >= pending[0] + a.decide_after_s:
            T = pending.pop(0)
            cp = eng.clock_point(T, provisional=True) if a.trigger == "certified" else None
            if a.trigger == "certified" and cp is None:
                raise SystemExit(f"certified replay: no clock second at or after {T}")
            res = eng.decide(T, w0=w0, last_entry=last_entry, certified_sd=int(cp[0]) if cp else None)
            results.append(res)
            actions.extend(res.actions)
            eng.prune(T)
            progress(f"mark {lv.hour_key(datetime.fromtimestamp(T, tz=timezone.utc))}: rows {len(res.mints)} actions {len(res.actions)} {res.seconds:.2f}s")

    while True:
        items = reader.poll(50_000)
        if not items and reader.caught_up:
            break
        if not items and reader.problem:
            raise SystemExit(f"reader problem: {reader.problem}")
        for kind, x in items:
            if kind in ("gap", "skip"):
                raise SystemExit(f"unexpected {kind} item in a synthetic tip dir: {x}")
            bt = x.get("block_time")
            if bt is not None:
                decide_due(bt)
            fa.apply(eng, adapter.feed({"type": "tip", "tip_kind": kind, "w": x}))
            rows_in += 1
        if rows_in and rows_in % 2_000_000 < len(items):
            progress(f"ingested {rows_in} rows {time.monotonic() - t0:.0f}s hour {tr.hour_key(reader.h)}")
    decide_due(float("inf"))
    actions.extend(eng.book.close_all("end"))
    allm = [m for r in results for m in r.mints]
    T = np.concatenate([r.T for r in results]) if results else np.zeros(0, np.int64)
    sd = np.concatenate([r.sdv for r in results]) if results else np.zeros(0, np.int64)
    X = np.vstack([r.X for r in results]) if results else np.zeros((0, lv.FZ.N_FEATURES), np.float32)
    sc = np.concatenate([r.score for r in results]) if results else np.zeros(0)
    trades = lv.trades_from_actions(actions)
    counters = dict(eng.counters)
    counters["d3_dropped"] = int(sum(r.d3_dropped for r in results))
    return {"rows_md5": lv.rows_md5(allm, T, sd, X, sc), "trades_md5": lv.trades_md5(trades), "n_rows": len(allm), "n_trades": len(trades), "marks": len(results),
            "rows_ingested": rows_in, "counters": counters, "reader": dict(reader.stats), "seconds": round(time.monotonic() - t0, 1),
            "decide_seconds": {"p50": float(np.median([r.seconds for r in results])), "max": float(max(r.seconds for r in results))} if results else None}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--h0", default="2026-08-14T12")
    ap.add_argument("--h1", default="2026-08-19T12")
    ap.add_argument("--w0", default="2026-08-16T12")
    ap.add_argument("--e", default="2026-08-19T12")
    ap.add_argument("--trigger", choices=("guard", "certified"), default="certified")
    ap.add_argument("--decide-after-s", type=int, default=7200, help="decide mark T once a row with block_time >= T + this is read (7200 = the replay's hour(T)+1 loaded)")
    ap.add_argument("--expect-rows", default=None)
    ap.add_argument("--expect-trades", default=None)
    ap.add_argument("--compare-replay", action="store_true", help="also run tools.r4b_live.replay_walker on the same window and compare")
    ap.add_argument("--keep-scratch", action="store_true")
    a = ap.parse_args()
    out = Path(a.out)
    if not os.path.realpath(str(out)).startswith(str(SCRATCH_PARENT) + "/"):
        raise SystemExit(f"refused: --out must be under {SCRATCH_PARENT}")
    if not (a.h0 <= a.w0 < a.e <= a.h1 and a.h1 <= "2026-08-20T10") or a.h0 < "2026-08-14T12":
        raise SystemExit("refused: need h0 <= w0 < e <= h1 inside explore-0814 (2026-08-14T12 .. 2026-08-20T10)")
    out.mkdir(parents=True, exist_ok=True)
    prog_path = os.environ.get("MISCUSI_PROGRESS")

    def progress(msg: str) -> None:
        print(f"{datetime.now(timezone.utc):%H:%M:%SZ} {msg}", flush=True)
        if prog_path:
            Path(prog_path).write_text(json.dumps({"note": msg[:200]}))

    import duckdb

    from tools import r4b_score as rs

    con = duckdb.connect()
    con.sql("SET memory_limit='6GB'; SET threads=4; SET enable_progress_bar=false; SET preserve_insertion_order=true")
    boosters, smap = rs.load_boosters(), rl.load_static_map(POOL_V)
    tip, state = out / "tip", out / "state"
    built = build_tip_dir(con, tip, state, a.h0, a.h1, 2, progress)
    progress(f"tip-format dir built: {built['rows']} files {built['files']}")
    res = run_stream(a, con, smap, boosters, tip, state, progress)
    rep = {"schema": "r4b_tip_equiv_v1", "trigger": a.trigger, "window": [a.h0, a.h1, a.w0, a.e], "lag_ms": LAG_MS, "decide_after_s": a.decide_after_s, "built": {k: v for k, v in built.items() if k != "trade_hours"},
           "tip_stream": res, "git_head": os.popen(f"git -C {REPO} rev-parse HEAD").read().strip(),
           "note": "md5s, counts and timings only; no price, P&L, fill or exit state. Exploration block explore-0814 (hunt23/RULES.md)."}
    ok = True
    if a.expect_rows:
        rep["rows_md5_equal_pinned"] = res["rows_md5"] == a.expect_rows
        ok &= rep["rows_md5_equal_pinned"]
    if a.expect_trades:
        rep["trades_md5_equal_pinned"] = res["trades_md5"] == a.expect_trades
        ok &= rep["trades_md5_equal_pinned"]
    if a.compare_replay:
        r = lv.replay_walker([CLEAN / w for w in WINDOWS], a.h0, a.h1, lv.hour_epoch(a.w0), lv.hour_epoch(a.e), static_map=smap, universe_min_mbt=lv.hour_epoch(a.h0), boosters=boosters,
                             progress=progress, trigger=a.trigger)
        rep["replay"] = {"rows_md5": r.rows_md5, "trades_md5": r.trades_md5, "n_rows": r.n_rows, "n_trades": len(r.trades)}
        rep["equal_replay"] = r.rows_md5 == res["rows_md5"] and r.trades_md5 == res["trades_md5"]
        ok &= rep["equal_replay"]
    rep["verdict"] = "PASS" if ok and (a.expect_rows or a.expect_trades or a.compare_replay) else ("FAIL" if not ok else "NO_REFERENCE")
    if not a.keep_scratch:
        shutil.rmtree(tip, ignore_errors=True)
        shutil.rmtree(state, ignore_errors=True)
    (out / "report.json").write_text(json.dumps(rep, indent=1, sort_keys=True, default=str) + "\n")
    print(json.dumps({k: rep[k] for k in ("verdict",)} | {"rows_md5": res["rows_md5"], "trades_md5": res["trades_md5"], "marks": res["marks"]}, sort_keys=True))
    mres = os.environ.get("MISCUSI_RESULT")
    if mres:
        Path(mres).write_text(json.dumps({"ok": rep["verdict"] == "PASS", "verdict": rep["verdict"], "summary": f"r4b tip-format replay {a.h0}..{a.h1}: {rep['verdict']}",
                                          "metrics": {"rows": res["n_rows"], "trades": res["n_trades"], "marks": res["marks"], "rows_ingested": res["rows_ingested"]}}))
    return 0 if rep["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
