#!/usr/bin/env python3
"""hv7_tip_equiv: the HV7 F1 proof legs on the fast-0 TIP source (QP-RULING-1101-arena-tip-reader section 4; DEC-031 Amendment 23 item 8, "Equivalence"). A NEW pinned file:
`tools/drill_hv7_real_layout.py`, `tools/hv7_p0_count.py` and `scripts/ops/hv7-real-layout-replay.sh` keep their bytes and their public-tape refusals. PAPER only: StubModel (no API),
fake chain (no RPC), no network, no key.

INPUT. The pinned WRITER's output built from EXPLORATION walker hours (explore-0814, `scripts/research/arena_tip_equiv.py`'s loader) through a FAKE tip dir and state dir (the qp-1165 arm-T
pattern): walker rows -> trades-/creates-/migrations-<hour> files -> `tools.arena_tip_reader.TipReader` -> `ArenaTipSource` -> `tools.arena_tip_tape_writer.Writer` (in process, an injected
clock, the production code path: hour files, closing, the counts row) -> a derived tape dir. The exploration walker rows carry no PumpSwap V; the harness stamps the registered
V on every PumpSwap trade row that lacks one (a disclosed fixture value, `V_LAMPORTS`), as the tip decoder's event V would. Nothing sealed is read: refusals are in `check_scratch`, `check_tape`
and (hours, clean dir) in `scripts/research/arena_tip_equiv.py`.

LEGS (each prints md5s, counts, RSS and timings only; never a pool, mint, signature, price, P&L or per-pool row)
  EQUIV_1D  the HV7 runner over the WRITER's hour files against the same runner over the adapter's rows written offline (no writer, no hour closing, no compaction): the decision-log md5, the
            cards md5 and the entry counts must be EQUAL, and the run must have at least `--min-periods` periods with >= 3 screened candidates (an equivalence of two empty runs is not a PASS).
            This is the book-level version of the writer's own byte test: the writer, its hour files and its closing change no HV7 decision.
  F_3D      the runner over the writer's output three times, each in its OWN process (PYTHONHASHSEED 0, 0, 12345): the drill's PASS rule (`drill_hv7_real_layout.verdict`: equal md5s, the stub
            invariants in each run, RSS and timing caps), unchanged.
  E1        restart/resume: the runner killed at a pinned instant and resumed on the same dirs gives the md5 of the uninterrupted run (`Hv7Runner.resume`), on the tip source.
  MD5 DECISION-EQUIVALENCE REPLAY: the three legs above are the replay; `report.json` holds every md5.
The min-decisions floor (`--min-periods`) is pinned in a notebook entry BEFORE each job; below it is not a PASS. Rule U2 is part of the runner and is exercised by these runs.
Pre-clock structure, not evidence: no outcome is printed. Run as a MiScusi job (scripts/ops/hv7-tip-equiv.sh), research-0, memory-capped.
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import contextlib
import importlib.util
import json
import os
import resource
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable, Iterator

os.environ.setdefault("MAL_ARENA_TEST", "1")

from tools import arena_tip_adapter as ta  # noqa: E402
from tools import arena_tip_reader as tr  # noqa: E402
from tools import arena_tip_tape_writer as aw  # noqa: E402
from tools import drill_hv7_real_layout as D  # noqa: E402
from tools import hv7_runner as R  # noqa: E402
from tools.drill_hv6_item6 import cards_md5, log_md5  # noqa: E402

_spec = importlib.util.spec_from_file_location("arena_tip_equiv", Path(__file__).resolve().parents[1] / "scripts" / "research" / "arena_tip_equiv.py")
eq = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(eq)

SCRATCH_PARENT = Path("/data2/mal/scratch/hv7-tip-equiv")
V_LAMPORTS = 17_600_000_000              # the registered PumpSwap virtual quote reserve (V ~ 17.58 SOL); exploration rows carry none
GRACE_MS = 60_000
HASHSEEDS = ("0", "0", "12345")
TAGS = ("tip-a", "tip-b", "tip-c")
EXTRA_FORBIDDEN = ("arena-tip-feed", "arena-tipfeed", "/data/mal/arena", "mirror", "tip-tape-archive")
TEST_PARENTS = (tempfile.gettempdir().rstrip("/"),)


class Refused(Exception):
    pass


def _tokens(p: str | os.PathLike) -> None:
    for form in (os.fspath(p), os.path.abspath(p), os.path.realpath(p)):
        low = form.lower()
        for w in (*eq.FORBIDDEN, *EXTRA_FORBIDDEN):
            if w in low:
                raise Refused(f"path contains {w!r}")


def check_scratch(out: str | os.PathLike, parents: Iterable[str | os.PathLike] = (SCRATCH_PARENT, *TEST_PARENTS)) -> Path:
    """The scratch tree (fake tip dir, state dir, derived tape, drill roots): a NEW dir under /data2/mal/scratch/hv7-tip-equiv (or, for tests, the system temp dir); never the live receiver
    copy, the sealed tip dir, the archive, the public arena tape or the mirror."""
    p = Path(out)
    if not p.is_absolute():
        raise Refused("scratch_not_absolute")
    _tokens(p)
    real = os.path.realpath(p)
    if not any(real.startswith(os.path.realpath(par) + os.sep) for par in parents):
        raise Refused("scratch_not_allowed")
    if p.exists() and any(p.iterdir()):
        raise Refused("scratch_not_empty")
    return p


def check_tape(tape: str | os.PathLike, scratch: Path) -> Path:
    """The derived tape the legs read: the one this harness built under its own scratch tree, nothing else."""
    _tokens(tape)
    real = os.path.realpath(tape)
    if not real.startswith(os.path.realpath(scratch) + os.sep):
        raise Refused("tape_not_under_scratch")
    return Path(real)


def with_v(seq: Iterable[tuple[str, dict]], v: int = V_LAMPORTS) -> Iterator[tuple[str, dict]]:
    """Stamp the registered V on PumpSwap trade rows that carry none (a disclosed fixture value)."""
    for kind, row in seq:
        if kind == "trade" and row.get("venue") == "pumpswap" and row.get("virtual_quote_reserves") is None:
            row = {**row, "virtual_quote_reserves": v}
        yield kind, row


class Clock:
    def __init__(self, ms: int) -> None:
        self.ms = ms

    def __call__(self) -> int:
        return self.ms


def _source(tip: Path, state: Path, first_hour: str, clock: Any) -> ta.ArenaTipSource:
    reader = tr.TipReader(tip, state, start_ms=tr.hour_epoch(first_hour) * 1000, catch_up_hours=0, min_hour="2026-08-01T00", lookback_h=None, fadvise=False)
    return ta.ArenaTipSource(reader, state, now_ms=clock, io_hook=lambda: True, require_io_idle=False)


def build_derived(seq: Iterable[tuple[str, dict]], scratch: Path, first_hour: str, *, compress: bool = False, skip_every: int = 0) -> dict[str, Any]:
    """Walker rows -> fake tip dir -> the pinned Writer (in process) -> <scratch>/derived; and the adapter's rows written offline to <scratch>/offline (the EQUIV_1D reference)."""
    tip, state, out, off = scratch / "tip", scratch / "state", scratch / "derived", scratch / "offline"
    built = eq.build_tip_dir(seq, tip, state, skip_every=skip_every)
    clock = Clock(built["status_ts_ms"])
    src = _source(tip, state, first_hour, clock)
    w = aw.Writer(src, out, start_ms=tr.hour_epoch(first_hour) * 1000, catch_up_hours=0, grace_ms=GRACE_MS, clock=clock, out_roots=[str(scratch)], compress=compress,
                  snapshot_s=0.0, status_s=0.0)
    w.start()
    try:
        for _ in range(100_000):
            n = w.step()
            if n == 0 and src.caught_up:
                break
            if src.problem:
                raise Refused(f"writer_source_problem_{src.problem}")
        else:
            raise Refused("writer_did_not_catch_up")
    finally:
        w.close()
    off.mkdir(parents=True, exist_ok=True)
    fh: dict[str, Any] = {}
    src2 = _source(tip, state, first_hour, Clock(built["status_ts_ms"]))
    src2.start()
    n_off = 0
    while True:
        rows = src2.poll(50_000)
        for r in rows:
            h = aw.hour_key(r["t_recv_ms"])
            f = fh.get(h) or fh.setdefault(h, open(off / f"tape-{h}.jsonl", "ab"))
            f.write(aw.row_bytes(r))
            n_off += 1
        if not rows and src2.caught_up:
            break
    for f in fh.values():
        f.close()
    hours = sorted(p.name for p in out.iterdir() if p.name.startswith("tape-"))
    return {"rows": built["rows"], "walker_rows_no_tx_index": built["walker_rows_no_tx_index"], "derived_hour_files": len(hours), "offline_rows": n_off, "offline_hour_files": len(fh),
            "writer_totals": dict(sorted(w.totals.items())) if hasattr(w, "totals") else {}}


def _rss_mb() -> float:
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)


def _days(first_hour: str, last_hour: str) -> list[str]:
    from datetime import datetime, timedelta

    a = datetime.strptime(first_hour[:10], "%Y-%m-%d")
    b = datetime.strptime(last_hour[:10], "%Y-%m-%d")
    out = []
    while a <= b:
        out.append(a.strftime("%Y-%m-%d"))
        a += timedelta(days=1)
    return out


@contextlib.contextmanager
def _roots(scratch: Path):
    """The runner's path allowlist is HV7_ROOT (writes) and the public tape (reads); the harness's trees are elsewhere, so for the duration of a leg both are narrowed to THIS scratch tree
    (a harness-only setting: nothing outside the tree, the live receiver copy included, becomes readable)."""
    old = (R.READ_ROOTS, R.WRITE_ROOTS)
    R.READ_ROOTS = R.WRITE_ROOTS = (str(Path(scratch).resolve()),)
    try:
        yield
    finally:
        R.READ_ROOTS, R.WRITE_ROOTS = old


def _fresh(base: Path, *tags: str) -> None:
    """A leg is re-runnable: its own drill dirs (logs, ledgers, cards) are removed first, so a second run in the same scratch tree never appends to the first one's ledger."""
    for t in tags:
        shutil.rmtree(base / t, ignore_errors=True)
        (base / f"{t}.json").unlink(missing_ok=True)


def _run(base: Path, tag: str, tape: Path, days: list[str], start: int, end: int, sha: str) -> dict:
    prefixes = D.PRINT_COUNTER_PREFIXES
    D.PRINT_COUNTER_PREFIXES = (*prefixes, "u2_")          # report-only: the drill's printed-counter list gains U2's refusal counter (its bytes are unchanged)
    try:
        res = D.one_run(base, tag, sha, days, tape, start_ms=start, end_ms=end)
    finally:
        D.PRINT_COUNTER_PREFIXES = prefixes
    res["peak_rss_mb"] = _rss_mb()
    return res


def leg_equiv_1d(scratch: Path, days: list[str], start: int, end: int, sha: str, min_periods: int) -> dict:
    _fresh(scratch / "drill", "equiv-writer", "equiv-offline")
    with _roots(scratch):
        w = _run(scratch / "drill", "equiv-writer", scratch / "derived", days, start, end, sha)
        o = _run(scratch / "drill", "equiv-offline", scratch / "offline", days, start, end, sha)
    equal = {"log_md5": w["log_md5"] == o["log_md5"], "cards_md5": w["cards_md5"] == o["cards_md5"], "entries": w["entries"] == o["entries"], "periods": w["periods"] == o["periods"]}
    ok = all(equal.values()) and w["periods_ge3"] >= min_periods
    return {"leg": "EQUIV_1D", "verdict": "PASS" if ok else "FAIL", "equal": equal, "min_periods": min_periods, "periods_ge3": w["periods_ge3"], "periods": w["periods"],
            "log_md5": {"writer": w["log_md5"], "offline": o["log_md5"]}, "cards_md5": {"writer": w["cards_md5"], "offline": o["cards_md5"]}, "entries": w["entries"],
            "u2_age_unknown": w["counters"].get("u2_age_unknown", 0)}


def child_one_run(tag: str, tape: str, days: list[str], start: int, end: int, sha: str, base: str, scratch: str) -> int:
    """One replay in ITS OWN process; writes <base>/<tag>.json."""
    b = Path(base)
    try:
        check_tape(tape, Path(scratch))
        with _roots(Path(scratch)):
            res = _run(b, tag, Path(tape), days, start, end, sha)
        res["hashseed"] = os.environ.get("PYTHONHASHSEED")
    except Exception as e:  # noqa: BLE001 - the class name only
        res = {"error_class": type(e).__name__}
    (b / f"{tag}.json").write_text(json.dumps(res, sort_keys=True, separators=(",", ":")) + "\n")
    return 0 if "error_class" not in res else 3


def leg_f_3d(scratch: Path, days: list[str], start: int, end: int, sha: str, rss_cap_mb: float = D.RSS_CAP_MB) -> dict:
    base = scratch / "drill"
    base.mkdir(parents=True, exist_ok=True)
    _fresh(base, *TAGS)
    runs = []
    for tag, seed in zip(TAGS, HASHSEEDS):
        r = subprocess.run([sys.executable, "-B", "-m", "tools.hv7_tip_equiv", "--one-run", tag, "--tape", str(scratch / "derived"), "--days", ",".join(days), "--start-ms", str(start),
                            "--end-ms", str(end), "--sha", sha, "--base", str(base), "--scratch", str(scratch)], cwd=str(Path(__file__).resolve().parent.parent), env={**os.environ, "PYTHONHASHSEED": seed},
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        f = base / f"{tag}.json"
        if r.returncode != 0 or not f.exists():
            raise Refused(f"child_{tag}_rc{r.returncode}")
        res = json.loads(f.read_text())
        if "error_class" in res:
            raise Refused("child_error_" + str(res["error_class"])[:40])
        runs.append(res)
    a, b, c = runs
    v = D.verdict(a, b, c, max(x["peak_rss_mb"] for x in runs), rss_cap_mb)
    return {"leg": "F_3D", "verdict": v.get("result", "FAIL"), "detail": v,
            "log_md5": [x["log_md5"] for x in runs], "cards_md5": [x["cards_md5"] for x in runs], "periods_ge3": a["periods_ge3"]}


def leg_e1(scratch: Path, days: list[str], start: int, end: int, sha: str, kill_at: int) -> dict:
    """The uninterrupted run against the run killed at kill_at and resumed by a NEW runner on the same dirs (the shape of tools/test_hv7_resume.py::interrupted), on the tip source."""
    base, tape = scratch / "drill", scratch / "derived"
    _fresh(base, "e1-whole", "e1-cut")
    with _roots(scratch):
        one = D.new_run(base, "e1-whole", sha, days, tape, start_ms=start, end_ms=end)
        one.run(start, end)
        first = D.new_run(base, "e1-cut", sha, days, tape, start_ms=start, end_ms=end)
        first.run(start, kill_at)
        second = D.new_run(base, "e1-cut", sha, days, tape, start_ms=start, end_ms=end)
        nxt = second.resume(kill_at)
        second.run(nxt, end)
        for x in (*R.ARM_ORDER, "SCAN"):
            one.log.verify(x)
            second.log.verify(x)
        w, c = (log_md5(Path(one.cfg.root)), cards_md5(Path(one.cfg.root))), (log_md5(Path(second.cfg.root)), cards_md5(Path(second.cfg.root)))
        ok = w == c and D.summarize(one)["periods_ge3"] > 0
    return {"leg": "E1", "verdict": "PASS" if ok else "FAIL", "log_md5": {"whole": w[0], "resumed": c[0]}, "cards_md5": {"whole": w[1], "resumed": c[1]},
            "periods_ge3": D.summarize(one)["periods_ge3"], "kill_at_ms": kill_at}


def run_all(seq: Iterable[tuple[str, dict]], scratch: Path, first_hour: str, last_hour: str, sha: str, *, start_ms: int, end_ms: int, kill_at: int, min_periods: int,
            legs: Iterable[str] = ("equiv", "f3d", "e1"), keep: bool = False) -> dict[str, Any]:
    t0 = time.monotonic()
    scratch.mkdir(parents=True, exist_ok=True)
    built = build_derived(with_v(seq), scratch, first_hour)
    days = _days(first_hour, last_hour)
    reports = []
    if "equiv" in legs:
        reports.append(leg_equiv_1d(scratch, days, start_ms, end_ms, sha, min_periods))
    if "f3d" in legs:
        reports.append(leg_f_3d(scratch, days, start_ms, end_ms, sha))
    if "e1" in legs:
        reports.append(leg_e1(scratch, days, start_ms, end_ms, sha, kill_at))
    rep = {"schema": "hv7_tip_equiv_v1", "sha": sha, "built": built, "legs": reports, "verdict": "PASS" if reports and all(r["verdict"] == "PASS" for r in reports) else "FAIL",
           "v_fixture_lamports": V_LAMPORTS, "peak_rss_mb": _rss_mb(), "seconds": round(time.monotonic() - t0, 1),
           "note": "md5s, counts, timings and RSS only; no price, P&L, fill or exit state is reported. Exploration hours; V stamped from a fixture value."}
    if not keep:
        for d in ("tip", "state", "derived", "offline", "drill"):
            shutil.rmtree(scratch / d, ignore_errors=True)
    return rep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="HV7 F1 proof legs on the tip source (EQUIV_1D, F_3D, E1) over exploration hours. Paper only.")
    ap.add_argument("--one-run", default=None)
    ap.add_argument("--tape")
    ap.add_argument("--days")
    ap.add_argument("--start-ms", type=int)
    ap.add_argument("--end-ms", type=int)
    ap.add_argument("--kill-at-ms", type=int)
    ap.add_argument("--sha")
    ap.add_argument("--base")
    ap.add_argument("--scratch")
    ap.add_argument("--out")
    ap.add_argument("--hours", default="2026-08-16T12..2026-08-17T12", help="h0..h1 (h1 exclusive), inside the exploration block")
    ap.add_argument("--clean", default=str(eq.CLEAN))
    ap.add_argument("--min-periods", type=int, default=None, help="the min-decisions floor, pinned in a notebook entry BEFORE the job (required)")
    ap.add_argument("--legs", default="equiv,f3d,e1")
    ap.add_argument("--keep-scratch", action="store_true")
    a = ap.parse_args(argv)
    if a.one_run is not None:
        return child_one_run(a.one_run, a.tape, a.days.split(","), a.start_ms, a.end_ms, a.sha, a.base, a.scratch)
    try:
        if a.min_periods is None or a.min_periods < 1:
            raise Refused("min_periods_required")
        h0, h1 = a.hours.split("..")
        eq.check_hours(h0, h1)
        eq.check_path(a.clean, "--clean")
        if os.path.realpath(a.clean) != os.path.realpath(str(eq.CLEAN)):
            raise Refused(f"--clean must be {eq.CLEAN}")
        scratch = check_scratch(a.out or "")
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(Path(__file__).resolve().parent.parent), capture_output=True, text=True).stdout.strip()
        if subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=str(Path(__file__).resolve().parent.parent), capture_output=True, text=True).stdout.strip():
            raise Refused("dirty_tree")
    except (Refused, eq.Refused) as e:
        print(json.dumps({"hv7_tip_equiv": "refused", "why": str(e)}), file=sys.stderr)
        return 2
    from datetime import datetime, timezone

    ms = lambda h: int(datetime.strptime(h, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp() * 1000)  # noqa: E731
    start, end = ms(h0) + 2 * 3_600_000, ms(h1)                    # the first two hours are state warm-up (screens start at H0 + 2 h)
    kill_at = start + (end - start) // 2
    kill_at -= kill_at % 60_000
    rep = run_all(eq.load_walker(Path(a.clean), eq.WINDOWS, h0, h1), scratch, h0, ms_to_hour(end - 1), sha[:13], start_ms=start, end_ms=end, kill_at=kill_at,
                  min_periods=a.min_periods, legs=a.legs.split(","), keep=a.keep_scratch)
    (scratch / "report.json").write_text(json.dumps(rep, indent=1, sort_keys=True, default=str) + "\n")
    print(json.dumps({"hv7_tip_equiv": "done", "verdict": rep["verdict"], "legs": {r["leg"]: r["verdict"] for r in rep["legs"]}, "peak_rss_mb": rep["peak_rss_mb"], "seconds": rep["seconds"]},
                     sort_keys=True))
    mres = os.environ.get("MISCUSI_RESULT")
    if mres:
        Path(mres).write_text(json.dumps({"ok": rep["verdict"] == "PASS", "verdict": rep["verdict"], "summary": f"HV7 tip-source proof legs {h0}..{h1}: {rep['verdict']}"}))
    return 0 if rep["verdict"] == "PASS" else 1


def ms_to_hour(ms: int) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ms // 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H")


if __name__ == "__main__":
    raise SystemExit(main())
