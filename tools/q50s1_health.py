#!/usr/bin/env python3
"""Counts-only health reads for the Q50 successor `h5v1q50-boostfloor-s1` (DEC-031 Amendment 18 item 8; Amendment 6 item 1.3; QP-RULING-1024-successor R10.2). PAPER ONLY. Stdlib only.

Run as `python3 -I tools/q50s1_health.py <mode> ...` (no repo import, so any python3 works). Three modes:

  smoke-judge  The Amendment 6 item 1.3 blocking list for the s1 smoke (scripts/ops/q50s1-smoke.sh), from the smoke's own files: (a) one start row with S*'s start-record fields,
               (b) heartbeat rows (the rule can be silent for 2 h, so (b) is judged on `hb` rows, one per 60 s), (c) no early exit, no restart, no traceback, (d) memory: R0 + slope x
               PLAN_H <= CAP from the process's own RSS samples (R0 = first sample at or after start + 30 min; slope = OLS over those samples; the HV4 smoke rule, QP-RULING-1022-hv4-am6),
               and the smoke length (>= 2 h). Plus, REPORT-ONLY: gap rows by UTC hour, `kind` and `flags_pools` (coverage-losing = `flags_pools` not literally false) and the
               pending pre-clock gap rule (<= 2 coverage-losing gap records per hour); warning-line counts.
  clean-log    "At least 1 h of clean log (no restart loop) before the clock" (R10.2) for the s1 shadow WITHOUT reading its sealed job log: only lines that START with the start
               script's own prefix `q50s1-shadow: ` are printed verbatim; every other line is only matched against fixed patterns and counted (tracebacks, ERROR/CRITICAL, the
               shadow's known WARNING messages). From the book's out dir: start/stop/error/hb row counts by fixed prefix, the hb rows' trailing `t_ms` only, gap rows' `kind` and
               `flags_pools` only, the status file's mtime and the errors file's size. Never a trigger, outcome, pool, mint, price or count of decisions.
  dry-filter   The s1 wrapper's score JSON (a file) -> structure fields only: registered, refused dirs and their reasons, the s1 read-instant record state, dry_run, shadow dir
               names. Never attempts, counted, excluded, refused or any other count (the Q50 / s1 seal: attempt counts are not opened).

What it NEVER does: json-parse a shadow row, print a row, or read a ledger, bots.json, score output other than the one JSON handed to dry-filter, or any P&L.
Exit codes: 0 PASS / CLEAN / OK, 1 FAIL / NOT CLEAN / NOT OK, 2 usage or an input that cannot be read.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

S_SHA = "5c2ab7c64b21358619e37068c6a53e4d183512ea"
S1_ID = "h5v1q50-boostfloor-s1"
# Start-record fields of the s1 process (DEC-031 Amendment 18 item 8), as h5_shadow at S* serializes them (json.dumps, separators (",", ":")). Fixed substrings of the start row.
START_FIELDS = (
    f'"code_sha":"{S_SHA}"',
    '"rule":"H5-BOOSTFLOOR-Q50 v1"',
    '"rule_sha256":"b3f8ecbf8731dc322a035a0b3996b7ed3f0b8c508cc6947014e3de1aa9524fd4"',
    '"q_star_sol":50.0',
    '"fresh_forward":"ARENA"',
    '"dirty":false',
    '"tip_union":"off"',
    '"end_only_ms":1796083200000',
    '"bx10":false',
)
CAP_BYTES = 1500 * 1024 * 1024  # the shadow's scope MemoryMax (scripts/ops/q50s1-shadow-start.sh NEW_MAX_MB=1500) = the smoke's scope MemoryMax
PLAN_H = 341  # 2026-11-05T20:00Z (earliest shadow start) to 2026-11-20T01:00Z (E_s1 plus the last v1 exits): the planned window of item 1.3(d)
WARM_S = 1800  # R0 and the slope start 30 min after the process start (HV4 smoke rule)
MIN_DUR_S = 7200  # Amendment 6 item 1.3: at least 2 h
MIN_FIT_SAMPLES = 10
HB_EVERY_S = 60  # h5_shadow housekeeping(hb_s=60.0)
HB_MIN_SHARE = 0.9
GAP_RULE_LIMIT = 2  # pending pre-clock rule: <= 2 coverage-losing gap records per hour (audit-rules 2026-10-30 item 2); REPORT-ONLY until adopted
STATUS_MAX_AGE_S = 180
CLEAN_MIN_S = 3600

HOUR_FILE = re.compile(r"^h5-shadow-(\d{4}-\d{2}-\d{2}T\d{2})\.jsonl$")
GAP_KIND = re.compile(r'^\{"type":"gap","kind":"([A-Za-z0-9_]+)",')
GAP_FLAG = re.compile(r',"flags_pools":(true|false)[,}]')
TAIL_T_MS = re.compile(r',"t_ms":(\d+)\}\s*$')
PREFIX = {"start": '{"type":"start",', "stop": '{"type":"stop",', "hb": '{"type":"hb",', "error": '{"type":"error",', "gap": '{"type":"gap",'}
TRACEBACK = "Traceback (most recent call last)"
LOG_TIME = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}):\d{2}:\d{2},\d{3} (DEBUG|INFO|WARNING|ERROR|CRITICAL) ")
# the WARNING messages tools/h5_shadow.py at S* can log in a live run (log.warning call sites), matched as fixed substrings after the logger name
KNOWN_WARNINGS = (
    ("feed_error", "mal.h5_shadow feed error "),
    ("source_restart", "mal.h5_shadow source ended; restarting"),
    ("pump_feed_error", "mal.h5_shadow pump side feed error "),
    ("syn_rpc_fallback", "mal.h5_shadow syn rpc fallback "),
    ("pool_close_failed", "mal.h5_shadow pool close failed"),
)
START_SCRIPT_PREFIX = "q50s1-shadow: "


def _hour_files(out_dir: Path) -> list[tuple[str, Path]]:
    files = []
    for p in sorted(out_dir.iterdir()):
        m = HOUR_FILE.match(p.name)
        if m and p.is_file():
            files.append((m.group(1), p))
    return files


def scan_rows(out_dir: Path, since_ms: int | None = None) -> dict:
    """Prefix counts, gap (kind, flag) per hour and hb trailing t_ms. Reads every line as text; never json-parses one."""
    counts: collections.Counter = collections.Counter()
    gaps: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    hb_t: list[int] = []
    start_lines: list[str] = []
    for hour, path in _hour_files(out_dir):
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith(PREFIX["gap"]):
                    counts["gap"] += 1
                    k = GAP_KIND.match(line)
                    f = GAP_FLAG.search(line)
                    gaps[hour][(k.group(1) if k else "unparsed", f.group(1) if f else "missing")] += 1
                elif line.startswith(PREFIX["hb"]):
                    counts["hb"] += 1
                    t = TAIL_T_MS.search(line)
                    if t:
                        hb_t.append(int(t.group(1)))
                    else:
                        counts["hb_no_t_ms"] += 1
                elif line.startswith(PREFIX["start"]):
                    counts["start"] += 1
                    start_lines.append(line)
                elif line.startswith(PREFIX["stop"]):
                    counts["stop"] += 1
                elif line.startswith(PREFIX["error"]):
                    counts["error"] += 1
    return {"counts": counts, "gaps": gaps, "hb_t": hb_t, "start_lines": start_lines, "hours": [h for h, _ in _hour_files(out_dir)]}


def start_fields_problem(start_lines: list[str]) -> str | None:
    if len(start_lines) != 1:
        return f"start rows {len(start_lines)} != 1"
    missing = [f.split(":", 1)[0] for f in START_FIELDS if f not in start_lines[0]]
    return ("start row lacks " + " ".join(missing)) if missing else None


def gap_lines(gaps: dict[str, collections.Counter], tag: str) -> tuple[list[str], int, str | None, int]:
    """Per-hour lines and (max coverage-losing per hour, its hour, total coverage-losing)."""
    out, worst, worst_h, total = [], 0, None, 0
    for hour in sorted(gaps):
        losing = 0
        for (kind, flag), n in sorted(gaps[hour].items()):
            out.append(f"{tag} hour={hour} kind={kind} flags_pools={flag} n={n}")
            if flag != "false":
                losing += n
        total += losing
        if losing > worst:
            worst, worst_h = losing, hour
    return out, worst, worst_h, total


def ols(points: list[tuple[float, float]]) -> float:
    n = len(points)
    mt = sum(t for t, _ in points) / n
    my = sum(y for _, y in points) / n
    sxx = sum((t - mt) ** 2 for t, _ in points)
    if sxx == 0:
        return 0.0
    return sum((t - mt) * (y - my) for t, y in points) / sxx


def read_rss(path: Path) -> list[tuple[int, int | None, str]]:
    """rss.tsv rows written by q50s1-smoke.sh: epoch_s <TAB> rss_bytes|- <TAB> pids (comma list or -). Anything else is unreadable (exit 2)."""
    rows = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3 or not parts[0].isdigit() or not (parts[1] == "-" or parts[1].isdigit()):
            raise ValueError(f"{path.name} line {i} is malformed")
        rows.append((int(parts[0]), None if parts[1] == "-" else int(parts[1]), parts[2]))
    return rows


def count_log(path: Path, *, print_prefix: str | None = None) -> tuple[collections.Counter, dict[str, collections.Counter], list[str]]:
    """Fixed-pattern counts of a process or job log. Returns (totals, per-UTC-hour counts, verbatim lines that start with print_prefix)."""
    tot: collections.Counter = collections.Counter()
    per_hour: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    shown: list[str] = []
    if not path.exists():
        tot["log_missing"] = 1
        return tot, per_hour, shown
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if print_prefix and line.startswith(print_prefix):
                shown.append(line.rstrip("\n")[:2000])
                continue
            if TRACEBACK in line:
                tot["traceback"] += 1
            m = LOG_TIME.match(line)
            if not m:
                continue
            hour, level = f"{m.group(1)}T{m.group(2)}", m.group(3)
            key = level.lower()
            if level == "WARNING":
                key = next((k for k, pat in KNOWN_WARNINGS if pat in line), "warning_other")
            tot[key] += 1
            per_hour[hour][key] += 1
    return tot, per_hour, shown


def _iso(epoch_s: int) -> str:
    return datetime.fromtimestamp(epoch_s, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------------------------------------ smoke-judge
def smoke_judge(a: argparse.Namespace) -> int:
    out_dir, proc_log, rss_path = Path(a.out_dir), Path(a.proc_log), Path(a.rss)
    if not out_dir.is_dir():
        print(f"SMOKE_UNREADABLE out dir {out_dir} missing")
        return 2
    try:
        rss = read_rss(rss_path)
    except (OSError, ValueError) as e:
        print(f"SMOKE_UNREADABLE rss samples: {e}")
        return 2
    start, end = int(a.start_epoch), int(a.end_epoch)
    warm, plan_h, cap, min_dur = int(a.warm_s), float(a.plan_h), int(a.cap_bytes), MIN_DUR_S
    rows = scan_rows(out_dir)
    c = rows["counts"]
    failed: list[str] = []
    if a.test_overrides:
        failed.append("test_overrides")
        print("SMOKE_NOTE test overrides were in effect (short duration, sample or warm-up): this verdict is never a pass")

    dur = end - start
    ok = dur >= min_dur
    print(f"SMOKE_ITEM duration {'PASS' if ok else 'FAIL'} duration_s={dur} min_s={min_dur} from={_iso(start)} to={_iso(end)}")
    if not ok:
        failed.append("duration")

    sp = start_fields_problem(rows["start_lines"])
    print(f"SMOKE_ITEM a {'PASS' if sp is None else 'FAIL'} start_rows={c['start']} start_fields={'ok' if sp is None else sp} (H5 shadow: no registry call; the start row is item (a))")
    if sp is not None:
        failed.append("a")

    need_hb = int(HB_MIN_SHARE * dur / HB_EVERY_S)
    ok = c["hb"] >= need_hb and c["hb"] > 0
    print(f"SMOKE_ITEM b {'PASS' if ok else 'FAIL'} hb_rows={c['hb']} need>={need_hb} (the rule can be silent for 2 h: (b) is judged on hb rows, one per {HB_EVERY_S} s)")
    if not ok:
        failed.append("b")

    ltot, _, _ = count_log(proc_log)
    errlog = out_dir / "h5-shadow-errors.log"
    err_bytes = errlog.stat().st_size if errlog.exists() else 0
    err_tb = count_log(errlog)[0]["traceback"] if errlog.exists() else 0
    pid_sets = [p for _, r, p in rss if r is not None]
    pid_changes = sum(1 for x, y in zip(pid_sets, pid_sets[1:]) if x != y)
    gone = [t for t, r, _ in rss if r is None]
    alive = a.alive_at_end == "yes"
    c_bad = {"alive_at_end": not alive, "proc_log_missing": bool(ltot["log_missing"]), "proc_log_tracebacks": ltot["traceback"] > 0,
             "error_or_critical_lines": (ltot["error"] + ltot["critical"]) > 0, "pid_changes": pid_changes > 0, "samples_without_process": bool(gone),
             "errors_log_tracebacks": err_tb > 0, "error_rows": c["error"] > 0}
    print(f"SMOKE_ITEM c {'PASS' if not any(c_bad.values()) else 'FAIL'} alive_at_end={a.alive_at_end} proc_log_tracebacks={ltot['traceback']} "
          f"error_lines={ltot['error']} critical_lines={ltot['critical']} pid_changes={pid_changes} samples_without_process={len(gone)} "
          f"errors_log_bytes={err_bytes} errors_log_tracebacks={err_tb} error_rows={c['error']} stop_rows={c['stop']} failing=[{','.join(k for k, v in c_bad.items() if v)}]")
    if any(c_bad.values()):
        failed.append("c")

    fit = [(t, float(r)) for t, r, _ in rss if r is not None and t >= start + warm]
    if len(fit) < MIN_FIT_SAMPLES:
        print(f"SMOKE_ITEM d FAIL fit_samples={len(fit)} need>={MIN_FIT_SAMPLES} (a metric that cannot be computed fails)")
        failed.append("d")
    else:
        r0 = fit[0][1]
        slope_h = ols(fit) * 3600.0
        proj = r0 + slope_h * plan_h
        ok = proj <= cap
        half = len(fit) // 2
        s1h = ols(fit[:half]) * 3600.0 if half >= 2 else float("nan")
        s2h = ols(fit[half:]) * 3600.0 if len(fit) - half >= 2 else float("nan")
        print(f"SMOKE_ITEM d {'PASS' if ok else 'FAIL'} r0_bytes={int(r0)} slope_bytes_per_h={slope_h:.1f} fit_samples={len(fit)} plan_h={plan_h:g} projected_bytes={int(proj)} cap_bytes={cap} "
              f"(R0 + slope x plan_h <= cap; R0 = first sample at or after start + {warm} s)")
        print(f"SMOKE_REPORT_ONLY d_halves first_half_slope_bytes_per_h={s1h:.1f} second_half_slope_bytes_per_h={s2h:.1f} max_rss_bytes={int(max(r for _, r in fit))}")
        if not ok:
            failed.append("d")
    print("SMOKE_ITEM e CITED (md5 decision-equivalence at S*: the tag q50-succ-base-1 message and DEC-031 Amendment 18 item 2.3; not run by this smoke)")
    print("SMOKE_ITEM f N/A (not an LLM book)")

    glines, worst, worst_h, total = gap_lines(rows["gaps"], "SMOKE_GAPS")
    for ln in glines:
        print(ln)
    hours = max(dur / 3600.0, 1e-9)
    print(f"SMOKE_GAP_RULE coverage_losing_max_per_hour={worst} hour_of_max={worst_h or '-'} coverage_losing_total={total} gap_rows_total={c['gap']} "
          f"per_hour_overall={total / hours:.2f} limit={GAP_RULE_LIMIT} result={'WITHIN' if worst <= GAP_RULE_LIMIT else 'ABOVE'} "
          "(PENDING pre-clock rule; report-only, not on the Amendment 6 blocking list until adopted)")
    print("SMOKE_REPORT_ONLY warnings " + " ".join(f"{k}={ltot[k]}" for k, _ in KNOWN_WARNINGS) + f" warning_other={ltot['warning_other']}")
    print(f"SMOKE_VERDICT {'PASS' if not failed else 'FAIL'} failed=[{','.join(failed)}]")
    return 0 if not failed else 1


# ------------------------------------------------------------------------------------------------ clean-log
def clean_log(a: argparse.Namespace) -> int:
    out_dir, job_log = Path(a.out_dir), Path(a.job_log)
    now = int(a.now_epoch) if a.now_epoch is not None else int(time.time())
    if not out_dir.is_dir():
        print(f"CLEAN_UNREADABLE out dir {out_dir} missing")
        return 2
    if not job_log.is_file():
        print(f"CLEAN_UNREADABLE job log {job_log} missing")
        return 2
    tot, per_hour, shown = count_log(job_log, print_prefix=START_SCRIPT_PREFIX)
    for ln in shown:
        print(f"CLEAN_START_SCRIPT {ln}")
    starting = sum(1 for ln in shown if ln.startswith(START_SCRIPT_PREFIX + "starting rule "))
    rows = scan_rows(out_dir)
    c = rows["counts"]
    hb_recent = sum(1 for t in rows["hb_t"] if t >= (now - CLEAN_MIN_S) * 1000)
    status = out_dir / "h5-shadow-status.json"
    st_age = now - int(status.stat().st_mtime) if status.exists() else None
    errlog = out_dir / "h5-shadow-errors.log"
    since = int(a.scope_since_epoch) if a.scope_since_epoch else None
    up = now - since if since else None
    need_hb = int(HB_MIN_SHARE * CLEAN_MIN_S / HB_EVERY_S)
    checks = {
        "scope_active": a.scope_active == "yes",
        "scope_up_1h": up is not None and up >= CLEAN_MIN_S,
        "one_starting_line": starting == 1,
        "one_start_row": c["start"] == 1,
        "no_stop_row": c["stop"] == 0,
        "no_traceback": tot["traceback"] == 0,
        "no_error_critical": tot["error"] + tot["critical"] == 0,
        "hb_last_hour": hb_recent >= need_hb,
        "status_fresh": st_age is not None and st_age <= STATUS_MAX_AGE_S,
    }
    print(f"CLEAN_CHECKS " + " ".join(f"{k}={'ok' if v else 'NO'}" for k, v in checks.items()))
    print(f"CLEAN_FIGURES scope_up_s={up if up is not None else '-'} starting_lines={starting} start_rows={c['start']} stop_rows={c['stop']} tracebacks={tot['traceback']} "
          f"error_lines={tot['error']} critical_lines={tot['critical']} hb_rows_last_hour={hb_recent} need>={need_hb} status_age_s={st_age if st_age is not None else '-'}")
    print(f"CLEAN_REPORT_ONLY error_rows={c['error']} errors_log_bytes={errlog.stat().st_size if errlog.exists() else 0} "
          + " ".join(f"{k}={tot[k]}" for k, _ in KNOWN_WARNINGS) + f" warning_other={tot['warning_other']}")
    for hour in sorted(per_hour)[-int(a.hours):]:  # the newest hours that have a timestamped log line
        print(f"CLEAN_LOG_HOUR hour={hour} " + " ".join(f"{k}={v}" for k, v in sorted(per_hour[hour].items())))
    last_hours = set(rows["hours"][-int(a.hours):])
    recent = {h: g for h, g in rows["gaps"].items() if h in last_hours}
    glines, worst, worst_h, _ = gap_lines(recent, "CLEAN_GAPS")
    for ln in glines:
        print(ln)
    print(f"CLEAN_GAP_RULE coverage_losing_max_per_hour={worst} hour_of_max={worst_h or '-'} limit={GAP_RULE_LIMIT} over_last_hours={a.hours} (report-only)")
    ok = all(checks.values())
    print(f"CLEAN_VERDICT {'CLEAN' if ok else 'NOT_CLEAN'} at={_iso(now)} failing=[{','.join(k for k, v in checks.items() if not v)}]")
    return 0 if ok else 1


# ------------------------------------------------------------------------------------------------ dry-filter
def dry_filter(a: argparse.Namespace) -> int:
    try:
        doc = json.loads(Path(a.json).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"DRY_UNREADABLE {type(e).__name__}")
        return 2
    books = doc.get("books") if isinstance(doc, dict) else None
    if not isinstance(books, dict):
        print("DRY_UNREADABLE no books object")
        return 2
    print(f"DRY_RUN dry_run={doc.get('dry_run')} shadow_dirs={','.join(os.path.basename(str(d)) for d in doc.get('shadow_dirs') or []) or '-'} books={','.join(sorted(books)) or '-'}")
    b = books.get(S1_ID) or {}
    reg = b.get("registered") is True
    refused = b.get("refused_dirs") or {}
    inst = b.get("q50s1_read_instant") or {}
    for d, why in sorted(refused.items()):
        print(f"DRY_REFUSED_DIR dir={os.path.basename(str(d))} reason={why}")
    print(f"DRY_S1 registered={reg} refused_dirs={len(refused)} instant_record={inst.get('record', '-')}")
    want_dry = a.mode == "dry"
    ok = reg and not refused and set(books) == {S1_ID} and (doc.get("dry_run") is True) == want_dry
    print(f"DRY_VERDICT {'OK' if ok else 'NOT_OK'}")
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("smoke-judge")
    s.add_argument("--out-dir", required=True)
    s.add_argument("--proc-log", required=True)
    s.add_argument("--rss", required=True)
    s.add_argument("--start-epoch", required=True, type=int)
    s.add_argument("--end-epoch", required=True, type=int)
    s.add_argument("--alive-at-end", required=True, choices=("yes", "no"))
    s.add_argument("--warm-s", type=int, default=WARM_S)
    s.add_argument("--plan-h", type=float, default=PLAN_H)
    s.add_argument("--cap-bytes", type=int, default=CAP_BYTES)
    s.add_argument("--test-overrides", action="store_true")
    c = sub.add_parser("clean-log")
    c.add_argument("--out-dir", required=True)
    c.add_argument("--job-log", required=True)
    c.add_argument("--scope-active", required=True, choices=("yes", "no"))
    c.add_argument("--scope-since-epoch", default=None)
    c.add_argument("--now-epoch", default=None)
    c.add_argument("--hours", type=int, default=3)
    d = sub.add_parser("dry-filter")
    d.add_argument("--json", required=True)
    d.add_argument("--mode", choices=("dry", "score"), default="dry")
    a = ap.parse_args(argv)
    if a.cmd == "smoke-judge":
        if (a.warm_s, a.plan_h, a.cap_bytes) != (WARM_S, PLAN_H, CAP_BYTES):
            a.test_overrides = True
        return smoke_judge(a)
    if a.cmd == "clean-log":
        if a.scope_since_epoch is not None and not str(a.scope_since_epoch).isdigit():
            a.scope_since_epoch = None
        return clean_log(a)
    return dry_filter(a)


if __name__ == "__main__":
    sys.exit(main())
