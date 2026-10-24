#!/usr/bin/env python3
"""dbc_m10_gapfix: procedure G of QP-RULING-1024-read-hardening.md item 4. WRITE-ONLY gap-hour refetch for the DBC m10 forward tape. PAPER ONLY. Stdlib only.

    python3 tools/dbc_m10_gapfix.py identify                       G1: print ONLY the sorted labels whose main-checkpoint entry has "gap": true
    python3 tools/dbc_m10_gapfix.py pass --pass N --wt <clean worktree of tag dbc-m10-forward-v1-am1> [--budget 300000]

G1 source: ONLY the main checkpoint <main-out>/checkpoint-meteora_dbc.json. Nothing else is read: no tape file is opened, sized, hashed or decompressed; no txs, rows,
pages or per-hour credits are printed. Hours whose NAMES are missing are not G (resume the main fetch with the same out dir).
G2: every listed gap hour, in label order, by the pinned tool from the clean tag worktree:
    python3 -m tools.venue_backfill_dbc run --start H --end H+1h --out-dir <stage-root>-p<N> --credit-cap <G budget left>
    into a FRESH staging dir per pass (same filesystem). The tool's per-hour progress lines (tx counts) are not passed through; MISCUSI_PROGRESS is unset for it.
G3: success = the staging checkpoint has the label and its entry has no "gap": true. Then ONE rename(2) (os.rename: never a copy, EXDEV fails) of
    <stage>/meteora_dbc/tape-H.jsonl.zst over <main-out>/meteora_dbc/tape-H.jsonl.zst. On failure the gap file stays (a bad hour).
G4: only an hour whose MAIN checkpoint entry still has "gap": true is replaced (re-read just before the rename). The main checkpoint is never written, so it keeps
    "gap": true after a fix: G-STATE.json "replaced" records every replaced hour and a later pass never fetches or replaces it again.
G5: at most 3 passes per hour, at least 30 min apart (state in <ops>/G-STATE.json); refuses while any venue_backfill_dbc process runs (main fetch or another pass).
G6: G-LOG.md in <ops>: pass, job id, hour labels, rc, G credits of the pass, every mv line, date -u.
The G budget is cumulative over all passes (sum of the staging checkpoints' credits_used); a pass gets what is left.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import dbc_m10_read_ops as O  # noqa: E402

VENUE = "meteora_dbc"
CP_NAME = "checkpoint-meteora_dbc.json"
MAIN_OUT = "/data2/mal/venue-tape-backfill/fwdv1"
STAGE_ROOT = "/data2/mal/venue-tape-backfill/fwdv1-gapfix"
OPS_DIR = "/data/mal/hunt-1008/push-1016/hunt29/dbc/fwd-read-am1-ops"
G_BUDGET = 300_000
MAX_PASSES_PER_HOUR = 3
MIN_GAP_S = 1800
EXIT_SOME_FAILED, EXIT_CAP, EXIT_PINS, EXIT_REFUSED = 1, 5, 10, 12
GAPFIX_PINS = {k: O.PINS[k] for k in ("tools/venue_backfill_dbc.py", "tools/venue_backfill.py", "tools/dbc_decode.py")}


def utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def window_labels() -> set[str]:
    return {n[len("tape-"):-len(".jsonl.zst")] for n in O.expected_names()}


def gap_labels(main_cp: str) -> list[str]:
    """G1. Sorted labels with "gap": true in the main checkpoint, inside the window. Reads nothing else."""
    cp = json.loads(Path(main_cp).read_text(encoding="utf-8"))
    win = window_labels()
    return sorted(k for k, v in (cp.get("hours") or {}).items() if isinstance(v, dict) and v.get("gap") is True and k in win)


def next_hour(label: str) -> str:
    return (datetime.strptime(label, "%Y-%m-%dT%H") + timedelta(hours=1)).strftime("%Y-%m-%dT%H")


def tool_runner(wt: str) -> Callable[[list[str]], int]:
    def run(args: list[str]) -> int:
        env = {k: v for k, v in os.environ.items() if k != "MISCUSI_PROGRESS"}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        p = subprocess.Popen(["python3", "-m", "tools.venue_backfill_dbc", *args], cwd=wt, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        assert p.stdout is not None
        for ln in p.stdout:
            if "txs=" not in ln:  # per-hour tx counts are not passed through
                print("  tool: " + ln.rstrip()[:200], flush=True)
        return p.wait()

    return run


def load_state(path: Path) -> dict[str, Any]:
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"passes": [], "hour_passes": {}, "credits_total": 0, "replaced": {}}


def save_state(path: Path, st: dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def run_pass(n: int, *, main_out: str = MAIN_OUT, stage_root: str = STAGE_ROOT, ops: str = OPS_DIR, budget: int = G_BUDGET, job: str = "unknown",
             runner: Callable[[list[str]], int], pins_ok: Callable[[], tuple[bool, str]], writers: Callable[[], list[int]],
             now: Callable[[], float] = time.time, log: Callable[[str], None] = print) -> int:
    ok, why = pins_ok()
    if not ok:
        log(f"REFUSED (pins): {why}")
        return EXIT_PINS
    if writers():
        log("REFUSED: a venue_backfill_dbc process is running (main fetch or another G pass); run G after the day's fetch has ended")
        return EXIT_REFUSED
    opsd = Path(ops)
    opsd.mkdir(parents=True, exist_ok=True)
    stp, glog = opsd / "G-STATE.json", opsd / "G-LOG.md"
    st = load_state(stp)
    st.setdefault("replaced", {})
    if any(p["pass"] == n for p in st["passes"]):
        log(f"REFUSED: pass {n} is already recorded in {stp}")
        return EXIT_REFUSED
    stage = Path(f"{stage_root}-p{n}")
    if os.path.lexists(stage):
        log(f"REFUSED: staging dir {stage} exists (a fresh dir per pass)")
        return EXIT_REFUSED
    left = int(budget) - int(st["credits_total"])
    if left <= 0:
        log(f"REFUSED: G budget {budget} spent")
        return EXIT_CAP
    main_cp = os.path.join(main_out, CP_NAME)
    labels = gap_labels(main_cp)
    t = now()
    todo, skipped = [], []
    for h in labels:
        past = st["hour_passes"].get(h, [])
        if h in st["replaced"]:  # the main checkpoint keeps "gap": true after a fix; a replaced hour is never fetched or replaced again
            skipped.append(f"{h} (already replaced in pass {st['replaced'][h]})")
        elif len(past) >= MAX_PASSES_PER_HOUR:
            skipped.append(f"{h} (3 passes done)")
        elif past and t - max(past) < MIN_GAP_S:
            skipped.append(f"{h} (last pass < 30 min ago)")
        else:
            todo.append(h)
    t0 = utc()
    log(f"G pass {n} job {job} start {t0}: gap labels {labels}; to fetch {todo}; skipped {skipped}; G budget left {left}")
    rec: dict[str, Any] = {"pass": n, "job": job, "start": t0, "labels": labels, "fetched": todo, "skipped": skipped, "results": {}, "mv": [], "rc": 0}
    stage.mkdir(parents=True)
    scp = stage / CP_NAME
    rc_pass = 0
    for i, h in enumerate(todo):
        st["hour_passes"].setdefault(h, []).append(now())
        save_state(stp, st)
        rc = runner(["run", "--start", h, "--end", next_hour(h), "--out-dir", str(stage), "--credit-cap", str(left)])
        ent = (json.loads(scp.read_text(encoding="utf-8")).get("hours") or {}).get(h) if scp.is_file() else None
        if rc == 0 and isinstance(ent, dict) and ent.get("gap") is not True:
            main_ent = (json.loads(Path(main_cp).read_text(encoding="utf-8")).get("hours") or {}).get(h)
            if not (isinstance(main_ent, dict) and main_ent.get("gap") is True):
                rec["results"][h] = "not replaced: main checkpoint entry is not a gap"
                rc_pass = rc_pass or EXIT_SOME_FAILED
            else:
                src = stage / VENUE / f"tape-{h}.jsonl.zst"
                dst = Path(main_out) / VENUE / f"tape-{h}.jsonl.zst"
                try:
                    os.rename(src, dst)
                    line = f"mv {src} {dst}"
                    rec["mv"].append(line)
                    rec["results"][h] = "replaced"
                    st["replaced"][h] = n
                    save_state(stp, st)
                    log(line)
                except OSError as e:
                    rec["results"][h] = f"rename failed: {type(e).__name__}"
                    rc_pass = rc_pass or EXIT_SOME_FAILED
        else:
            rec["results"][h] = f"still gap (tool rc {rc})"
            rc_pass = rc_pass or EXIT_SOME_FAILED
        if rc == EXIT_CAP:
            rec["results"].update({x: "not attempted (G cap)" for x in todo[i + 1:]})
            rc_pass = EXIT_CAP
            break
    spent = int(json.loads(scp.read_text(encoding="utf-8")).get("credits_used", 0)) if scp.is_file() else 0
    st["credits_total"] = int(st["credits_total"]) + spent
    rec.update({"rc": rc_pass, "credits": spent, "credits_total": st["credits_total"], "end": utc()})
    st["passes"].append(rec)
    save_state(stp, st)
    md = [f"## G pass {n} (job {job}) {rec['start']} -> {rec['end']}", f"- gap labels (main checkpoint): {', '.join(labels) or 'none'}",
          f"- fetched: {', '.join(todo) or 'none'}; skipped: {', '.join(skipped) or 'none'}", f"- rc {rc_pass}; G credits this pass {spent}; G credits total {st['credits_total']} of {budget}"]
    md += [f"- {h}: {r}" for h, r in rec["results"].items()]
    md += [f"- `{m}`" for m in rec["mv"]]
    with open(glog, "a", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n\n")
    log("\n".join(md))
    return rc_pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("identify")
    p.add_argument("--main-out", default=MAIN_OUT)
    p = sub.add_parser("pass")
    p.add_argument("--pass", dest="n", type=int, required=True)
    p.add_argument("--wt", required=True)
    p.add_argument("--budget", type=int, default=G_BUDGET)
    p.add_argument("--main-out", default=MAIN_OUT)
    p.add_argument("--stage-root", default=STAGE_ROOT)
    p.add_argument("--ops", default=OPS_DIR)
    ns = ap.parse_args(argv)
    if ns.cmd == "identify":
        for h in gap_labels(os.path.join(ns.main_out, CP_NAME)):
            print(h)
        return 0
    return run_pass(ns.n, main_out=ns.main_out, stage_root=ns.stage_root, ops=ns.ops, budget=ns.budget, job=os.environ.get("MISCUSI_JOB_ID", "unknown"),
                    runner=tool_runner(ns.wt), pins_ok=lambda: O.check_pins(ns.wt, GAPFIX_PINS), writers=lambda: O.procs_matching("venue_backfill_dbc"),
                    log=lambda s: print(s, flush=True))


if __name__ == "__main__":
    sys.exit(main())
