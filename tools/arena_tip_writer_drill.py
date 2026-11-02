#!/usr/bin/env python3
"""arena_tip_writer_drill: proof P1 of QP's verdict on #1183 (the writer-restart drill) on a FIXED tip dir.

PAPER only. No key, no RPC, no network, 0 credits. It runs tools/arena_tip_tape_writer.Writer (unchanged) over a tip dir twice per case and compares the derived copy:

  reference   one writer, run from the start to the end of the tip dir without a stop.
  killed      the same writer config, stopped after `kill` steps (its files are closed, nothing else is saved), then a NEW writer on the same out dir started `downtime` seconds later
              on the clock (the resume age check and the heartbeat see it), which resumes from .writer/state.json and runs to the end.

PASS for a case: the per-hour sha256 of the derived copy (every hour file, open or closed) is equal to the reference's, no gap row has src "writer", `tip_gap_writer_hole` never appears, and the new
writer reports `resumed`. A downtime at or over the 60 h resume bound is expected to be REFUSED (`state_older_than_60h`), and the case says so instead of passing.
The W2 catch-up deadline (120 min from the first start, exit 8) is switched off in the drill: it tests RESUME, and a static tip dir keeps the writer catching up for the whole run, which a live writer
does not do at a hand-off; W2 itself is tested in tools/test_arena_tip_tape_writer.py (test_c6). The report holds names, counts and md5s only (hour keys, row counts, the md5 over the list of per-hour sha256).

The tip dir is built, for the real drill, from EXPLORATION walker hours by scripts/research/arena_tip_equiv.py's arm-T builder (`main` below; the hours must lie inside a pinned exploration list,
tools/arena_restart_equiv.EXPLORATION). The core, `run_drill`, takes any tip dir, and tools/test_arena_tip_writer_restart.py runs it on a synthetic one. NOT run on real data when written.
The receiver's verification of every closed hour (the P1 pass condition on the engine host) is arena_tip_push's own check and is not repeated here: the per-hour sha256 equality is what the
receiver compares.
"""

from __future__ import annotations

if __package__ in (None, ""):  # `python3 tools/arena_tip_writer_drill.py` from the repo root
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Sequence

os.environ.setdefault("MAL_ARENA_TEST", "1")

from tools import arena_tip_adapter as ta
from tools import arena_tip_reader as tr
from tools import arena_tip_tape_writer as aw

SCHEMA = "arena_tip_writer_drill_v1"
GRACE_MS = 60_000


class Clock:
    def __init__(self, ms: int) -> None:
        self.ms = ms

    def __call__(self) -> int:
        return self.ms


def make_writer(tip: Path, state: Path, out: Path, clock: Clock, *, first_hour: str, start_ms: int, poll_limit: int) -> aw.Writer:
    reader = tr.TipReader(tip, state, start_ms=start_ms, catch_up_hours=0, min_hour=first_hour, lookback_h=None, fadvise=False)  # the floor is lowered to the drill's hours: harness only
    src = ta.ArenaTipSource(reader, state, now_ms=clock, io_hook=lambda: True)
    return aw.Writer(src, out, start_ms=start_ms, catch_up_hours=0, grace_ms=GRACE_MS, clock=clock, out_roots=[str(out.parent)], compress=False, snapshot_s=0.0, status_s=0.0, catch_up_deadline_min=10**6,
                     poll_limit=poll_limit)


def _healthy(state: Path, ms: int) -> None:
    from tools import test_arena_tip_adapter as TA  # the pinned, event-V status the adapter's tests write (test helper: stdlib + pytest)

    TA.healthy(state, ms)


def _run(w: aw.Writer, clock: Clock, state: Path, end_ms: int, max_steps: int, stop_after: int | None = None) -> int:
    steps = 0
    while steps < max_steps:
        clock.ms = min(end_ms, clock.ms + 1000)
        _healthy(state, clock.ms)
        n = w.step()
        steps += 1
        if stop_after is not None and steps >= stop_after:
            break
        if n == 0 and w.src.caught_up:
            break
    return steps


def hour_digests(out: Path) -> dict[str, str]:
    res: dict[str, str] = {}
    for p in sorted(out.iterdir()):
        if p.name.startswith("tape-") and p.name.endswith(".jsonl"):
            res[p.name[5:18]] = hashlib.sha256(p.read_bytes()).hexdigest()
    return res


def writer_gap_rows(out: Path) -> tuple[int, int]:
    n = hole = 0
    for p in sorted(out.glob("tape-*.jsonl")):
        for ln in p.read_bytes().splitlines():
            r = json.loads(ln)
            if r.get("type") == "gap" and (r.get("src") == "writer" or r.get("kind") == aw.KIND_WRITER_HOLE):
                n += 1
                hole += r.get("kind") == aw.KIND_WRITER_HOLE
    return n, hole


def run_drill(tip: Path, state: Path, work: Path, *, first_hour: str, start_ms: int, end_ms: int, kills: Sequence[int], downtimes_s: Sequence[int], poll_limit: int = 2000,
              max_steps: int = 100_000) -> dict[str, Any]:
    """Reference and killed runs over the tip dir; the clock starts at start_ms and may advance to end_ms plus the downtimes."""
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    clock = Clock(start_ms)
    ref_out = work / "ref" / "out"
    ref_out.parent.mkdir()
    _healthy(state, clock.ms)
    w = make_writer(tip, state, ref_out, clock, first_hour=first_hour, start_ms=start_ms, poll_limit=poll_limit)
    w.start()
    n_steps = _run(w, clock, state, end_ms, max_steps)
    w.close()
    ref = hour_digests(ref_out)
    cases = []
    for k in kills:
        for dt in downtimes_s:
            out = work / f"k{k}-d{dt}" / "out"
            out.parent.mkdir()
            clock = Clock(start_ms)
            _healthy(state, clock.ms)
            w1 = make_writer(tip, state, out, clock, first_hour=first_hour, start_ms=start_ms, poll_limit=poll_limit)
            w1.start()
            _run(w1, clock, state, end_ms, max_steps, stop_after=k)
            w1.close()
            clock.ms += dt * 1000
            _healthy(state, clock.ms)
            w2 = make_writer(tip, state, out, clock, first_hour=first_hour, start_ms=start_ms, poll_limit=poll_limit)
            try:
                w2.start()
            except aw.Refused as exc:
                cases.append({"kill_step": k, "downtime_s": dt, "refused": str(exc)[:60], "equal": False})
                w2.close()
                continue
            _run(w2, clock, state, max(end_ms, clock.ms), max_steps)
            resumed = bool(w2.status().get("resumed"))
            w2.close()
            got, (gaps, holes) = hour_digests(out), writer_gap_rows(out)
            cases.append({"kill_step": k, "downtime_s": dt, "hours": len(got), "hours_equal": got == ref, "writer_gap_rows": gaps, "hole_rows": holes, "resumed": resumed,
                          "md5_hours": hashlib.md5("\n".join(f"{h}:{d}" for h, d in sorted(got.items())).encode()).hexdigest(),
                          "equal": bool(got == ref and gaps == 0 and holes == 0 and resumed)})
    return {"schema": SCHEMA, "reference_steps": n_steps, "reference_hours": len(ref), "reference_md5_hours": hashlib.md5("\n".join(f"{h}:{d}" for h, d in sorted(ref.items())).encode()).hexdigest(),
            "cases": cases, "resume_max_age_h": aw.RESUME_MAX_AGE_H,
            "all_equal": all(c["equal"] for c in cases if not c.get("refused")) and all(c.get("refused") for c in cases if c["downtime_s"] >= aw.RESUME_MAX_AGE_H * 3600)}


def main(argv: Sequence[str] | None = None) -> int:
    """The proof job's entry point (scripts/ops/arena-tip-writer-drill-job.sh): builds the tip dir from exploration walker hours with scripts/research/arena_tip_equiv.py's arm-T builder, then
    run_drill. NOT run on real data when written; the builder call below follows that script's own main() and is untested here."""
    from tools import arena_restart_equiv as E

    ap = argparse.ArgumentParser(description="writer-restart drill on exploration hours (P1)")
    ap.add_argument("--exploration", required=True)
    ap.add_argument("--allow-hours")
    ap.add_argument("--hours", required=True, help="h0..h1 (h1 exclusive), inside the pinned list")
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--kills", default="3,10,25,60,120", help="kill after this many writer steps (5 or more points)")
    ap.add_argument("--downtimes-s", default="0,300,7200,36000,212400", help="0, minutes, over 1 h, under 60 h (the last is 59 h)")
    ap.add_argument("--poll-limit", type=int, default=2000)
    ap.add_argument("--max-items", type=int, default=None)
    a = ap.parse_args(argv)
    h0, _, h1 = a.hours.partition("..")
    try:
        work, out = E.check_path(a.work_dir, "work-dir"), E.check_path(a.out, "out")
        lo, hi = E.parse_allow(a.allow_hours, a.exploration) or ("", "")
        if not (lo <= h0 < h1 <= hi):
            raise E.Refused(f"--hours {a.hours} is not inside the allow-list [{lo}, {hi})")
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "research"))
        import arena_tip_equiv as EQ  # noqa: PLC0415

        EQ.check_hours(h0, h1)
    except (E.Refused, ImportError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    work.mkdir(parents=True, exist_ok=True)
    tip, state = work / "tipdir", work / "tipstate"
    seq = EQ.load_walker(EQ.CLEAN, EQ.WINDOWS, h0, h1, a.max_items)
    built = EQ.build_tip_dir(seq, tip, state)
    first_hour = h0
    start_ms = tr.hour_epoch(h0) * 1000
    end_ms = tr.hour_epoch(h1) * 1000 + 3_600_000
    rep = run_drill(tip, state, work / "drill", first_hour=first_hour, start_ms=start_ms, end_ms=end_ms, kills=[int(x) for x in a.kills.split(",") if x],
                    downtimes_s=[int(x) for x in a.downtimes_s.split(",") if x != ""], poll_limit=a.poll_limit)
    rep.update({"hours": [h0, h1], "exploration": a.exploration, "tip_rows": {k: v for k, v in built.items() if isinstance(v, int)}})
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=1, sort_keys=True) + "\n")
    print(f"writer drill: {sum(1 for c in rep['cases'] if c['equal'])}/{len(rep['cases'])} cases equal; all_equal={rep['all_equal']}; report {out}")
    return 0 if rep["all_equal"] else 1


if __name__ == "__main__":
    sys.exit(main())
