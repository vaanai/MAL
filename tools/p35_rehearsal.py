#!/usr/bin/env python3
"""p35_rehearsal: the A-P1 live rehearsal of the v2 feed takeover (docs/research/p35-longbook-handoff/DESIGN.md 2.3, 2.4). PAPER only. No keys.

Runs ONLY on a scratch root: never under /data/mal, never on the production tape. On mal-research-0 it refuses to start before
2026-11-11T14:00Z: its public websockets would share the IP of HV4's and HV6's in-clock feed (DESIGN 2.4). The intended host is mal-fast-0.

  rates  --tape-a A --tape-b B --from T --to T [--window-s 120]
         Two independent continuous v1 tapes over one window. For each program and each window, it gives the disagreement rate in both directions,
         only_a / n_a and only_b / n_b, where a data row is matched by (signature, type, event_index). It also gives the data rows per window of each
         tape. It prints the PROPOSED thresholds by the fixed rule below, with every input. Counts only.
  run    --root DIR [--phase1-s 10800] [--phase2-s 21600] [--seam-gap-s 7200] [--warm-s 1200] [--plan-only]
         Phase 1, [start, start + phase1): two v1 feeds, ctrl/ and ho/, both continuous; the `rates` of the two feeds fix the thresholds.
         Phase 2: a v2 standby takes ho/ over from the v1 writer (seam 1). A second v2 standby takes it over from that v2 writer seam-gap later
         (seam 2). Everything is stopped at the phase-2 end. Then the proofs: `feed_handover_proof seam` on [seam - 10 min, seam + 10 min] of each
         seam (expect_takeover, the phase-1 thresholds), and `parity` ho/ vs ctrl/ over [seam - 1 h, seam + 1 h], split seam/control.
         It writes <root>/summary.json (counts, verdicts, thresholds, instants) and prints it. Child logs go to <root>/logs/.
         It reads no production tape, ledger or job state.

THRESHOLD RULE (proposed here, pinned by QP from the rehearsal's output, never from a production seam; QP-RULING-1026 Q1):
  T_p              = max(0.001, 2 x the 99th percentile over the windows and both directions of only / n) for program p
  MIN_OVERLAP_ROWS = floor(0.5 x the 5th percentile of the data rows per window of program p, over both tapes)
The window is the overlap floor, 120 s.

A-P1 PASS (DESIGN 2.3): both seams PASS, no data_loss gap row in either seam window, and seam-window miss rate <= the control windows'.
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import json
import math
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Sequence

from tools import feed_handover_proof as P

PROGRAMS = ("pumpswap", "pumpfun")
EARLIEST_RESEARCH0_MS = 1_794_405_600_000  # 2026-11-11T14:00:00Z (= tools.arena_feed_v2.EARLIEST_PRODUCTION_MS; a test pins it)
FORBIDDEN_ROOTS = ("/data/mal",)
SEAM_PAD_S = 600
PARITY_HALF_S = 3600  # parity per seam over [seam - 1 h, seam + 1 h]: the seam bucket is +-10 min, the rest is the control (memory: one index of 2 h per tape)
WINDOW_S_DEFAULT = 120


class Refused(Exception):
    pass


def check_root(root: str) -> Path:
    r = str(Path(root).resolve())
    for f in FORBIDDEN_ROOTS:
        if r == f or r.startswith(f + "/") or str(root).startswith(f + "/"):
            raise Refused(f"root {root!r} is under {f}: a rehearsal runs on a scratch root only")
    return Path(root)


def check_host(now_ms: int, hostname: str | None = None) -> None:
    h = (hostname or socket.gethostname()).lower()
    if "research" in h and now_ms < EARLIEST_RESEARCH0_MS:
        raise Refused("on mal-research-0 a rehearsal is refused before 2026-11-11T14:00Z (shared public-RPC IP with in-clock feeds; DESIGN 2.4)")


def _pct(xs: Sequence[float], q: float) -> float | None:
    s = sorted(xs)
    if not s:
        return None
    k = (len(s) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def _index(tape: Path, lo: int, hi: int) -> dict[tuple, tuple[int, str]]:
    """key -> (t_recv_ms, prog) of every data row with t in [lo, hi). Hours selected by name; streamed line by line (no row list is held)."""
    out: dict[tuple, tuple[int, str]] = {}
    for _, p in P.select_hours(tape, lo - P.HOUR_MS, hi + P.HOUR_MS):
        for ln in P.iter_lines(p):
            if b'"signature":null' in ln or not ln.strip():
                continue
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if not isinstance(r, dict) or not isinstance(r.get("t_recv_ms"), int):
                continue
            k = P.data_key(r)
            if k is not None and lo <= r["t_recv_ms"] < hi and k not in out:
                out[k] = (r["t_recv_ms"], str(r.get("prog")))
    return out


def rates(tape_a: Path, tape_b: Path, t0: int, t1: int, window_s: int = WINDOW_S_DEFAULT, pad_s: int = 60) -> dict[str, Any]:
    pad, w = pad_s * 1000, window_s * 1000
    ia, ib = _index(tape_a, t0 - pad, t1 + pad), _index(tape_b, t0 - pad, t1 + pad)
    nwin = max(0, (t1 - t0) // w)
    per: dict[str, list[dict[str, int]]] = {p: [{"n_a": 0, "n_b": 0, "only_a": 0, "only_b": 0} for _ in range(nwin)] for p in PROGRAMS}
    for src, other, n_key, only_key in ((ia, ib, "n_a", "only_a"), (ib, ia, "n_b", "only_b")):
        for k, (t, prog) in src.items():
            if prog not in per or not (t0 <= t < t0 + nwin * w):
                continue
            c = per[prog][(t - t0) // w]
            c[n_key] += 1
            if k not in other:
                c[only_key] += 1
    out: dict[str, Any] = {"schema": "p35_rehearsal_rates_v1", "window": {"from_ms": t0, "to_ms": t1, "window_s": window_s, "windows": nwin}, "programs": {}}
    for p in PROGRAMS:
        rs = [c["only_a"] / c["n_a"] for c in per[p] if c["n_a"]] + [c["only_b"] / c["n_b"] for c in per[p] if c["n_b"]]
        ns = [c["n_a"] for c in per[p]] + [c["n_b"] for c in per[p]]
        p99, p05 = _pct(rs, 0.99), _pct(ns, 0.05)
        out["programs"][p] = {
            "rows_a": sum(c["n_a"] for c in per[p]), "rows_b": sum(c["n_b"] for c in per[p]),
            "only_a": sum(c["only_a"] for c in per[p]), "only_b": sum(c["only_b"] for c in per[p]),
            "rate_max": max(rs) if rs else None, "rate_p99": p99, "rate_median": _pct(rs, 0.5),
            "rows_per_window_p05": p05, "rows_per_window_min": min(ns) if ns else None,
            "proposed_max_disagree_rate": None if p99 is None else max(0.001, round(2 * p99, 6)),
            "proposed_min_overlap_rows": None if p05 is None else int(math.floor(0.5 * p05)),
        }
    return out


def proposed(r: dict[str, Any]) -> tuple[dict[str, float], dict[str, int]]:
    mdr = {p: r["programs"][p]["proposed_max_disagree_rate"] for p in PROGRAMS}
    mor = {p: r["programs"][p]["proposed_min_overlap_rows"] for p in PROGRAMS}
    if any(v is None for v in mdr.values()) or any(v is None or v < 1 for v in mor.values()):
        raise Refused(f"phase 1 gave no usable thresholds: {mdr} {mor}")
    return mdr, mor


def plan(start_ms: int, phase1_s: int, phase2_s: int, seam_gap_s: int, warm_s: int) -> dict[str, int]:
    """The instants of a run. Seam k's standby starts warm_s before its takeover instant; its deadline is takeover + warm_s."""
    p1_end = start_ms + phase1_s * 1000
    s1 = p1_end + warm_s * 1000
    s2 = s1 + seam_gap_s * 1000
    end = p1_end + phase2_s * 1000
    if s2 + warm_s * 1000 + SEAM_PAD_S * 1000 > end:
        raise Refused("phase 2 is too short for two seams and their proof windows")
    return {"start_ms": start_ms, "rates_from_ms": start_ms + warm_s * 1000, "phase1_end_ms": p1_end, "standby1_ms": p1_end, "takeover1_ms": s1,
            "standby2_ms": s2 - warm_s * 1000, "takeover2_ms": s2, "end_ms": end, "warm_s": warm_s}


def _sleep_until(ms: int, now: Callable[[], int] = lambda: time.time_ns() // 1_000_000) -> None:
    while True:
        d = ms - now()
        if d <= 0:
            return
        time.sleep(min(d / 1000, 30))


def _spawn(py: str, args: list[str], log: Path, cwd: Path) -> subprocess.Popen:
    fh = open(log, "ab")
    return subprocess.Popen([py, *args], stdout=fh, stderr=subprocess.STDOUT, cwd=str(cwd), start_new_session=True)


def _lock_ms(log: Path) -> int | None:
    try:
        for ln in log.read_text(errors="replace").splitlines():
            if ln.startswith('{"takeover"'):
                return int(json.loads(ln)["takeover"]["lock_acquired_ms"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return None


def run(root: Path, *, phase1_s: int, phase2_s: int, seam_gap_s: int, warm_s: int, py: str = sys.executable, plan_only: bool = False) -> dict[str, Any]:
    now = time.time_ns() // 1_000_000
    check_host(now)
    pl = plan(now, phase1_s, phase2_s, seam_gap_s, warm_s)
    if plan_only:
        return {"plan": pl}
    repo = Path(__file__).resolve().parent.parent
    ctrl, ho, logs = root / "ctrl", root / "ho", root / "logs"
    for d in (ctrl, ho, logs):
        d.mkdir(parents=True, exist_ok=True)
    (root / "plan.json").write_text(json.dumps(pl, indent=1))
    procs: dict[str, subprocess.Popen] = {"ctrl": _spawn(py, ["-m", "tools.arena_feed", "--out-dir", str(ctrl)], logs / "ctrl.log", repo),
                                          "ho_v1": _spawn(py, ["-m", "tools.arena_feed", "--out-dir", str(ho)], logs / "ho_v1.log", repo)}
    summary: dict[str, Any] = {"schema": "p35_rehearsal_v1", "plan": pl, "host": socket.gethostname()}
    try:
        _sleep_until(pl["phase1_end_ms"])
        r = rates(ctrl, ho, pl["rates_from_ms"], pl["phase1_end_ms"])
        summary["phase1_rates"] = r
        mdr, mor = proposed(r)
        summary["thresholds"] = {"max_disagree_rate": mdr, "min_overlap_rows": mor}
        thr = ["--max-disagree-rate", ",".join(f"{p}={mdr[p]}" for p in PROGRAMS), "--min-overlap-rows", ",".join(f"{p}={mor[p]}" for p in PROGRAMS)]
        seams = []
        for k, (sb_ms, to_ms) in enumerate(((pl["standby1_ms"], pl["takeover1_ms"]), (pl["standby2_ms"], pl["takeover2_ms"])), start=1):
            _sleep_until(sb_ms)
            log = logs / f"standby{k}.log"
            procs[f"v2_{k}"] = _spawn(py, ["-m", "tools.arena_feed_v2", "--standby", "--out-dir", str(ho), "--takeover-at-ms", str(to_ms),
                                           "--takeover-deadline-ms", str(to_ms + warm_s * 1000), *thr], log, repo)
            _sleep_until(to_ms + warm_s * 1000 + 60_000)
            seams.append({"k": k, "lock_ms": _lock_ms(log), "standby_rc": procs[f"v2_{k}"].poll()})
        summary["seams"] = seams
        _sleep_until(pl["end_ms"])
    finally:
        for name, pr in procs.items():
            if pr.poll() is None:
                os.killpg(pr.pid, signal.SIGTERM)
        for name, pr in procs.items():
            try:
                pr.wait(timeout=90)
            except subprocess.TimeoutExpired:
                os.killpg(pr.pid, signal.SIGKILL)
        summary["exit_codes"] = {n: pr.returncode for n, pr in procs.items()}
    proofs = []
    for s in summary.get("seams", []):
        if s["lock_ms"] is None:
            proofs.append({"k": s["k"], "verdict": "NO_TAKEOVER"})
            continue
        lo, hi = s["lock_ms"] - SEAM_PAD_S * 1000, s["lock_ms"] + SEAM_PAD_S * 1000
        # QP N3: n_old is recomputed only over the rows read ([lo - pad, hi + pad)); the standby's cover_from_ms is about warm_s before the
        # takeover, so the read pad must reach back past it, or the check fails closed on a mismatch.
        sm = P.seam_report(ho, lo, hi, expect_takeover=True, max_disagree_rate=mdr, min_overlap_rows=mor, pad_s=float(max(3600, warm_s + 2 * SEAM_PAD_S)))
        par = P.parity_report(ho, ctrl, s["lock_ms"] - PARITY_HALF_S * 1000, s["lock_ms"] + PARITY_HALF_S * 1000, seam_ms=s["lock_ms"], seam_pad_s=SEAM_PAD_S)
        proofs.append({"k": s["k"], "seam_verdict": sm["verdict"], "seam_failed": sm["failed"], "hard_gaps": sm["checks"]["hard_gaps"]["value"],
                       "parity_buckets": par["buckets"], "seam_miss_rate_not_above_control": par["seam_miss_rate_not_above_control"]})
    summary["proofs"] = proofs
    summary["a_p1"] = "PASS" if (len(proofs) == 2 and all(p.get("seam_verdict") == "PASS" and p.get("hard_gaps") == 0
                                                         and p.get("seam_miss_rate_not_above_control") is True for p in proofs)) else "FAIL"
    (root / "summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True))
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P35 A-P1 rehearsal of the v2 feed takeover (scratch root only; counts only)")
    sub = ap.add_subparsers(dest="mode", required=True)
    a = sub.add_parser("rates")
    a.add_argument("--tape-a", required=True)
    a.add_argument("--tape-b", required=True)
    a.add_argument("--from", dest="t_from", required=True)
    a.add_argument("--to", dest="t_to", required=True)
    a.add_argument("--window-s", type=int, default=WINDOW_S_DEFAULT)
    b = sub.add_parser("run")
    b.add_argument("--root", required=True)
    b.add_argument("--phase1-s", type=int, default=10_800)
    b.add_argument("--phase2-s", type=int, default=21_600)
    b.add_argument("--seam-gap-s", type=int, default=7_200)
    b.add_argument("--warm-s", type=int, default=1_200)
    b.add_argument("--plan-only", action="store_true")
    args = ap.parse_args(argv)
    try:
        if args.mode == "rates":
            ta, tb = check_root(args.tape_a), check_root(args.tape_b)
            out = rates(ta, tb, P.parse_instant(args.t_from), P.parse_instant(args.t_to), args.window_s)
        else:
            out = run(check_root(args.root), phase1_s=args.phase1_s, phase2_s=args.phase2_s, seam_gap_s=args.seam_gap_s, warm_s=args.warm_s,
                      plan_only=args.plan_only)
    except (Refused, ValueError) as exc:
        print(json.dumps({"refused": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(out, sort_keys=True))
    if args.mode == "rates" or set(out) == {"plan"}:
        return 0
    return 0 if out.get("a_p1") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
