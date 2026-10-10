#!/usr/bin/env python3
"""H5 BOOST day check: DEC-024 section 5.1 as amended by Amendment 4 (2026-10-10T07:27:25Z), read from the shadow's pool-close records.

Run as `/usr/bin/python3 -I tools/h5_boost_day_check.py ...`. Stdlib only (with -I the repo is not on sys.path).

WHAT IT MEASURES. The UTC-day median of the BOOST last slice after s0 (first PumpSwap print), over the pools the shadow closed in that
day's hourly files, for two groups:
  ALL    every kept pool (the executor's own count includes synthetic-migration pools);
  PLAIN  synthetic is False AND synthetic_src == "rpc" (the pools H5 can trade).
SYN (synthetic is True) and OTHER (anything else) are reported too. Timing by class is a structure field, never an outcome (EXP-024 Am.4 D1).

READING (the seal). Files <shadow-dir>/h5-shadow-<day>THH.jsonl, HH = 00..23. A line whose leading key says a type other than "pool" is
counted as "other" and never parsed (`lines_by_type` is only {"pool": n, "other": m}: no outcome, trigger or strip count is printed). A pool line (or a line with no leading type key) is parsed with an object hook that keeps ONLY the
keys in ALLOWED_KEYS as each object closes; no other field (outcome, trigger, min_q, triggered, fill, P&L) is kept, printed or logged.
`gap` and `boost_src` are in the list: they are structure fields (the executor's own filter reads them), not outcomes.

THE EXECUTOR'S FILTER, applied to every group (tools/h5_executor.py, the `rtype == "pool"` branch of the feed reader and on_boost_row):
  a pool record counts only if reason == "horizon", gap is False (the literal) and boost_src in ("pda", "event_authority");
  then sec = boost_last_slice_s, else boost_last_slice_s_blocktime, else boost_last_slice_s_recv: the FIRST that is a number (bool excluded),
  kept only if finite and 0 < sec < 2000 (no fall-through on a bad first value), rounded to 3 decimals; a mint counts once per day, on its
  first kept record (file-hour, then line order). Output carries "executor_filter_applied": true.
Two guards the executor does not have, both fail-closed and counted in `skipped`: a record whose `sealed` is not exactly false (a sealed
pool's close record carries no `gap`, so the executor's filter drops it too) and a record with no mint (the executor would key it under "").

DAY KEY. "day_key": "file_hour". A pool belongs to the UTC day of the hourly FILE that holds its record. The executor keys a pool by the
time it READS the record, so pools written near 00:00Z can land on different days there. Disclosed, not corrected.

JUDGING (DEC-024 Am.4 item 2, item 4, and the manager's stricter addition). Each group is judged on its own count (n >= 30):
  --mode day      a COMPLETED UTC day D > FIRST_STRIKE_DAY (2026-10-10; the 10-10T07:11Z fire is the first strike).
                  halt_due if (n_all >= 30 and all median < 337 s) or (n_plain >= 30 and plain median < 337 s).
                  "evaluated" is n_all >= 30; `plain_judged` (n_plain >= 30) is reported separately. A day that is not evaluated
                  (including no files: fail closed) when the previous day was not evaluated either -> halt_due. The previous day is
                  the latest `--mode day` line for it in --log (its evaluated is all.n >= 30, the same definition; lines written
                  under MAL_BDC_NOW are ignored); with no such line it is recomputed from the files.
  --mode running  today so far. halt_due if (n_plain >= 30 and plain median < 335 s) or (n_all >= 30 and all median < 335 s).
  --mode resume   FIRST_STRIKE_DAY only (any other --day is refused), once complete. resume_ok iff all 24 hour files are present and read,
                  n_plain >= 30, n_all >= 30, plain median >= 337 s and all median >= 337 s, and the clock is not overridden. It does not
                  check the other section 5 rules or stops (the manager does). It never places STOP.
--place-stop: only with this flag, and only when halt_due: `sudo -n touch /var/lib/mal-live/h5/STOP`, then
`sudo -n test -e /var/lib/mal-live/h5/STOP`; both return codes are logged. This tool NEVER removes STOP.

NO HIDDEN MISSING DATA. An hour path that exists (a symlink, a directory or any non-regular file included) but cannot be read raises
InputError("unreadable_file"): exit 2, no verdict from partial data. Missing hour files are listed in `hours_missing`; resume refuses them. In day and running modes
"hours_missing" is also added to `reasons`, informational only (no verdict or exit-code change).

CLOCK. The CLI has no clock flag. Tests call main(argv, now=...). A subprocess test may set the env var MAL_BDC_NOW (ISO-8601); the run is
then stamped "now_overridden": true and resume_ok is impossible. Real runs use the system clock ("now_overridden": false).

OUTPUT. One compact JSON line on stdout with every number, the verdict and the reasons; the same line is appended to --log (created if
missing; a path under /data/mal/structure-monitor/ is refused). Exit codes:
  0 no_halt / resume_ok          3 halt_due (STOP verified present, or no --place-stop)
  2 input error                  4 resume not ok
  5 unevaluated (day read, n_all < 30, no halt)      6 halt_due with --place-stop and STOP NOT verified present
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import stat
import statistics
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable

TOOL = "h5_boost_day_check"
VERSION = 2  # v2: per-group judging, executor filter, exit codes 5 and 6; v1 log lines are still read (evaluated is derived from all.n)
ALLOWED_KEYS = frozenset({
    "type", "reason", "sealed", "pool", "mint", "s0", "s0_block_time", "boost_last_slice_s", "boost_last_slice_s_blocktime",
    "boost_last_slice_s_recv", "boost_slices", "synthetic", "synthetic_src", "gap", "boost_src",
})
EXEC_BOOST_SRC = ("pda", "event_authority")  # tools/h5_executor.py, the pool branch of the feed reader
SEC_KEYS = ("boost_last_slice_s", "boost_last_slice_s_blocktime", "boost_last_slice_s_recv")  # the executor's order
SEC_MAX = 2000.0
MIN_POOLS = 30  # DEC-024 Am.4 item 2 (the executor's BOOST_MEDIAN_MIN_POOLS)
TWICE_S = 337.0  # below this on a completed day: the second strike (Am.4 item 2) / no resume (item 4)
HALT_S = 335.0  # below this on today's running median: halt
FIRST_STRIKE_DAY = "2026-10-10"  # pinned: the 10-10T07:11Z fire. Not a flag.
NOW_ENV = "MAL_BDC_NOW"  # tests only; a run that uses it is stamped now_overridden and cannot be resume_ok
STOP_PATH = "/var/lib/mal-live/h5/STOP"
FORBIDDEN_LOG_PREFIX = "/data/mal/structure-monitor"
LEAD_TYPE_RE = re.compile(rb'^\{\s*"type"\s*:\s*"([A-Za-z0-9_\-]{1,40})"')
EXIT_OK, EXIT_INPUT, EXIT_HALT, EXIT_RESUME_NOT_OK, EXIT_UNEVALUATED, EXIT_HALT_STOP_UNVERIFIED = 0, 2, 3, 4, 5, 6


class InputError(Exception):
    pass


def _keep_allowed(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """The JSON object hook: every object, as it closes, keeps only the allowed keys. Nothing else outlives the parse."""
    return {k: v for k, v in pairs if k in ALLOWED_KEYS}


def parse_pool_line(raw: bytes) -> dict[str, Any] | None:
    """One line -> the allowed keys of a JSON object, or None when it is not a JSON object."""
    try:
        obj = json.loads(raw, object_pairs_hook=_keep_allowed)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return None
    return obj if isinstance(obj, dict) else None


def pick_seconds(rec: dict[str, Any]) -> float | None:
    """The executor's choice: the first of SEC_KEYS whose value is a number (not a bool), then 0 < sec < 2000 and finite."""
    for k in SEC_KEYS:
        v = rec.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            try:
                sec = float(v)
            except (OverflowError, ValueError, TypeError):  # an int too large for a float (10**400): a bad value, not a crash
                return None
            if not math.isfinite(sec) or not (0.0 < sec < SEC_MAX):
                return None
            return round(sec, 3)
    return None


def pool_class(rec: dict[str, Any]) -> str:
    syn = rec.get("synthetic")
    if syn is False and rec.get("synthetic_src") == "rpc":
        return "plain"
    if syn is True:
        return "syn"
    return "other"


def hour_files(shadow_dir: str, day: str) -> list[tuple[int, str]]:
    return [(h, os.path.join(shadow_dir, f"h5-shadow-{day}T{h:02d}.jsonl")) for h in range(24)]


def _lines(fh: Any) -> Iterable[bytes]:
    try:
        yield from fh
    except OSError:
        raise InputError("unreadable_file") from None


def read_day(shadow_dir: str, day: str, parse: Callable[[bytes], dict[str, Any] | None] | None = None) -> dict[str, Any]:
    """Read one UTC day's hourly files. Returns counts and the kept seconds per mint. Raises InputError on a missing dir, no files, or an
    hour path that exists but cannot be read (a symlink, a non-regular file, an open or read error): no verdict from partial data."""
    parse = parse or parse_pool_line
    if not os.path.isdir(shadow_dir):
        raise InputError("shadow_dir_missing")
    hours_read: list[int] = []
    lines_by_type = {"pool": 0, "other": 0}  # seal hygiene: no per-type (outcome, trigger, strip) count is kept or printed
    skipped ={"sealed": 0, "executor_filter": 0, "no_mint": 0, "bad_sec": 0, "dup_mint": 0, "bad_line": 0}
    filter_fail = {"reason": 0, "gap": 0, "boost_src": 0}  # each failing condition counted on its own; a record can fail several
    seen: dict[str, tuple[str, float]] = {}  # mint -> (class, sec)
    pool_records = 0
    for h, path in hour_files(shadow_dir, day):
        if not os.path.lexists(path):
            continue  # absent: listed in hours_missing by the caller
        try:
            if not stat.S_ISREG(os.lstat(path).st_mode):  # a symlink, a directory, a fifo: exists but is not read
                raise InputError("unreadable_file")
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        except OSError:
            raise InputError("unreadable_file") from None
        with os.fdopen(fd, "rb") as fh:
            for raw in _lines(fh):
                if not raw.strip():
                    continue
                m = LEAD_TYPE_RE.match(raw)
                if m is not None and m.group(1) != b"pool":
                    lines_by_type["other"] += 1
                    continue  # counted as "other" by its leading type, never parsed
                rec = parse(raw)
                if rec is None:
                    skipped["bad_line"] += 1
                    continue
                if rec.get("type") != "pool":
                    lines_by_type["other"] += 1
                    continue
                lines_by_type["pool"] += 1
                pool_records += 1
                if rec.get("sealed") is not False:
                    skipped["sealed"] += 1
                    continue
                # the executor's filter, verbatim: reason == "horizon", gap is False, boost_src in (pda, event_authority)
                bad_reason, bad_gap, bad_src = rec.get("reason") != "horizon", rec.get("gap") is not False, rec.get("boost_src") not in EXEC_BOOST_SRC
                if bad_reason or bad_gap or bad_src:
                    skipped["executor_filter"] += 1
                    filter_fail["reason"] += bad_reason
                    filter_fail["gap"] += bad_gap
                    filter_fail["boost_src"] += bad_src
                    continue
                mint = rec.get("mint")
                if not isinstance(mint, str) or not mint:
                    skipped["no_mint"] += 1
                    continue
                sec = pick_seconds(rec)
                if sec is None:
                    skipped["bad_sec"] += 1
                    continue
                if mint in seen:
                    skipped["dup_mint"] += 1
                    continue
                seen[mint] = (pool_class(rec), sec)
        hours_read.append(h)
    if not hours_read:
        raise InputError("no_files")
    return {"hours_read": hours_read, "lines_by_type": lines_by_type, "pool_records": pool_records,
            "skipped": skipped, "executor_filter_fail": filter_fail, "seen": seen}


def _median(xs: list[float]) -> float | None:
    return statistics.median(xs) if xs else None


def summarize(read: dict[str, Any]) -> dict[str, Any]:
    seen = read["seen"]
    groups: dict[str, list[float]] = {"all": [], "plain": [], "syn": [], "other": []}
    for cls, sec in seen.values():
        groups["all"].append(sec)
        groups[cls].append(sec)
    out: dict[str, Any] = {}
    for g, xs in groups.items():
        med = _median(xs)
        out[g] = {"n": len(xs), "median_s": None if med is None else round(med, 4)}
    out["_exact"] = {g: _median(xs) for g, xs in groups.items()}
    return out


def parse_day(s: str) -> date:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s or ""):
        raise ValueError(s)
    return date.fromisoformat(s)


def resolve_now(now: datetime | None, environ: Any = None) -> tuple[datetime, bool]:
    """(the clock, overridden). `now` is the in-process argument tests pass to main(); MAL_BDC_NOW is the subprocess override and is the
    only one that counts as overridden. With neither, the system clock."""
    if now is not None:
        return (now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)), False
    raw = (os.environ if environ is None else environ).get(NOW_ENV)
    if raw is None:
        return datetime.now(timezone.utc), False
    try:
        dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        raise InputError("bad_now_env") from None
    return (dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)), True


def day_complete(d: date, now: datetime) -> bool:
    return now >= datetime(d.year, d.month, d.day, tzinfo=timezone.utc) + timedelta(days=1)


def prev_day_from_log(log_path: str, day: str) -> bool | None:
    """The latest `--mode day` line for `day` in the log: was that day evaluated? Same definition as evaluated_of: all.n >= MIN_POOLS, so a
    v1 line (whose `evaluated` also needed plain.n >= 30) reads the same as a v2 line. A line with no `all` block (an error line, such as
    no_files) falls back to its `evaluated` bool. Lines written under MAL_BDC_NOW are ignored. None if the log has no such line or cannot
    be read."""
    try:
        fh = open(log_path, "rb")
    except OSError:
        return None
    found: bool | None = None
    with fh:
        for raw in fh:
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            if not (isinstance(rec, dict) and rec.get("tool") == TOOL and rec.get("mode") == "day" and rec.get("day") == day
                    and isinstance(rec.get("evaluated"), bool)) or rec.get("now_overridden") is True:
                continue
            all_n = rec["all"].get("n") if isinstance(rec.get("all"), dict) else None
            found = (all_n >= MIN_POOLS) if isinstance(all_n, int) and not isinstance(all_n, bool) else rec["evaluated"]
    return found


def evaluated_of(summary: dict[str, Any] | None) -> bool:
    """A day is evaluated when ALL has MIN_POOLS pools. PLAIN is judged on its own count (plain_judged_of)."""
    return summary is not None and summary["all"]["n"] >= MIN_POOLS


def plain_judged_of(summary: dict[str, Any] | None) -> bool:
    return summary is not None and summary["plain"]["n"] >= MIN_POOLS


def place_stop(run: Callable[..., Any]) -> dict[str, Any]:
    """Touch STOP, then test that it exists. Never removes it."""
    res: dict[str, Any] = {"path": STOP_PATH}
    for key, cmd in (("touch_rc", ["sudo", "-n", "touch", STOP_PATH]), ("test_rc", ["sudo", "-n", "test", "-e", STOP_PATH])):
        try:
            res[key] = run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=False).returncode
        except (OSError, subprocess.SubprocessError) as exc:
            res[key] = None
            res[key.replace("_rc", "_error")] = type(exc).__name__
    res["present"] = res.get("test_rc") == 0
    return res


def append_log(path: str, line: str) -> bool:
    try:
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o644)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return True
    except OSError:
        return False


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="H5 BOOST day-median check (DEC-024 5.1, Amendment 4). Never removes STOP.")
    p.add_argument("--shadow-dir", required=True)
    p.add_argument("--mode", required=True, choices=("day", "running", "resume"))
    p.add_argument("--day", help="YYYY-MM-DD: the completed day (day mode) or the resume day (resume mode: only FIRST_STRIKE_DAY, the default)")
    p.add_argument("--log", required=True, help="append one JSON line per run (created if missing)")
    p.add_argument("--place-stop", action="store_true", help="when halt_due: sudo -n touch the H5 STOP, then test it")
    return p


def _public(summary: dict[str, Any] | None) -> dict[str, Any]:
    if summary is None:
        return {}
    return {k: v for k, v in summary.items() if not k.startswith("_")}


def _emit(rec: dict[str, Any], log: str) -> None:
    line = json.dumps(rec, separators=(",", ":"), allow_nan=False)
    if not append_log(log, line):
        print(json.dumps({"tool": TOOL, "warning": "log_write_failed"}, separators=(",", ":")), file=sys.stderr)
    print(line)


def main(argv: list[str] | None = None, now: datetime | None = None, run: Callable[..., Any] | None = None) -> int:
    """`now` and `run` are for tests (the clock; a stand-in for subprocess.run). The CLI has neither."""
    args = build_parser().parse_args(argv)
    if any(p == FORBIDDEN_LOG_PREFIX or p.startswith(FORBIDDEN_LOG_PREFIX + "/") for p in (os.path.abspath(args.log), os.path.realpath(args.log))):
        print(json.dumps({"tool": TOOL, "error": "log_path_forbidden"}, separators=(",", ":")))
        return EXIT_INPUT
    first_strike = parse_day(FIRST_STRIKE_DAY)
    try:
        now_dt, now_overridden = resolve_now(now)
    except InputError as exc:
        _emit({"tool": TOOL, "v": VERSION, "mode": args.mode, "error": str(exc), "halt_due": False, "exit": EXIT_INPUT}, args.log)
        return EXIT_INPUT
    stamp = {"now_utc": now_dt.strftime("%Y-%m-%dT%H:%M:%SZ"), "now_overridden": now_overridden}
    try:
        today = now_dt.date()
        if args.mode == "running":
            d = parse_day(args.day) if args.day else today
            if d != today:
                raise InputError("running_day_not_today")
        elif args.mode == "resume":
            d = parse_day(args.day) if args.day else first_strike
            if d != first_strike:
                raise InputError("resume_day_not_first_strike_day")
        else:
            if not args.day:
                raise InputError("day_required")
            d = parse_day(args.day)
            if d <= first_strike:
                raise InputError("day_not_after_first_strike_day")
            if not day_complete(d, now_dt):
                raise InputError("day_not_complete")
    except ValueError:
        _emit({"tool": TOOL, "v": VERSION, "mode": args.mode, **stamp, "error": "bad_date", "halt_due": False, "exit": EXIT_INPUT}, args.log)
        return EXIT_INPUT
    except InputError as exc:
        _emit({"tool": TOOL, "v": VERSION, "mode": args.mode, "day": args.day, **stamp, "error": str(exc), "halt_due": False,
               "exit": EXIT_INPUT}, args.log)
        return EXIT_INPUT

    day = d.isoformat()
    rec: dict[str, Any] = {"tool": TOOL, "v": VERSION, "mode": args.mode, "day": day, **stamp, "day_complete": day_complete(d, now_dt),
                           "first_strike_day": FIRST_STRIKE_DAY, "executor_filter_applied": True, "day_key": "file_hour",
                           "thresholds": {"min_pools": MIN_POOLS, "twice_s": TWICE_S, "halt_s": HALT_S}}
    reasons: list[str] = []
    summary: dict[str, Any] | None = None
    input_error: str | None = None
    try:
        rd = read_day(args.shadow_dir, day)
        summary = summarize(rd)
        rec.update({"hours_read": rd["hours_read"], "hours_missing": [h for h in range(24) if h not in rd["hours_read"]],
                    "lines_by_type": rd["lines_by_type"], "pool_records": rd["pool_records"], "skipped": rd["skipped"],
                    "executor_filter_fail": rd["executor_filter_fail"]})
        rec.update(_public(summary))
    except InputError as exc:
        input_error = str(exc)
        rec["error"] = input_error
        reasons.append(input_error)
    ex = summary["_exact"] if summary is not None else None
    n_all = summary["all"]["n"] if summary is not None else 0
    n_plain = summary["plain"]["n"] if summary is not None else 0
    rec["plain_judged"] = plain_judged_of(summary)

    halt_due = False
    resume_ok = False
    if args.mode == "day":
        evaluated = evaluated_of(summary)
        rec["evaluated"] = evaluated
        breach: list[str] = []
        if summary is not None:
            if n_plain >= MIN_POOLS and ex["plain"] < TWICE_S:  # type: ignore[index]
                breach.append("plain_median_lt_337")
            if n_all >= MIN_POOLS and ex["all"] < TWICE_S:  # type: ignore[index]
                breach.append("all_median_lt_337")
        if breach:
            halt_due = True
            reasons += breach + ["second_strike_after_first_strike_day"]
        elif not evaluated:
            if input_error is None:
                reasons.append("unevaluated_lt_30_pools")
            prev = (d - timedelta(days=1)).isoformat()
            prev_eval = prev_day_from_log(args.log, prev)
            src = "log"
            if prev_eval is None:
                src = "files"
                try:
                    prev_eval = evaluated_of(summarize(read_day(args.shadow_dir, prev)))
                except InputError:
                    prev_eval = False
            rec["prev_day"] = {"day": prev, "evaluated": prev_eval, "source": src}
            if not prev_eval:
                halt_due = True
                reasons.append("two_consecutive_unevaluated_days")
        rec["verdict"] = "halt_due" if halt_due else ("no_halt" if evaluated else "unevaluated")
    elif args.mode == "running":
        if summary is not None:
            rec["judged"] = {"plain": n_plain >= MIN_POOLS, "all": n_all >= MIN_POOLS}
            if n_plain >= MIN_POOLS and ex["plain"] < HALT_S:  # type: ignore[index]
                reasons.append("plain_median_lt_335")
            if n_all >= MIN_POOLS and ex["all"] < HALT_S:  # type: ignore[index]
                reasons.append("all_median_lt_335")
            halt_due = bool(reasons)
        rec["verdict"] = "halt_due" if halt_due else ("input_error" if input_error else "no_halt")
    else:  # resume: never places STOP, never removes it
        if summary is not None:
            if not rec["day_complete"]:
                reasons.append("day_not_complete")
            if rec["hours_missing"]:
                reasons.append("hours_missing")
            if now_overridden:
                reasons.append("now_overridden")
            if n_plain < MIN_POOLS:
                reasons.append("plain_lt_30_pools")
            if n_all < MIN_POOLS:
                reasons.append("all_lt_30_pools")
            if ex["plain"] is not None and ex["plain"] < TWICE_S:  # type: ignore[index]
                reasons.append("plain_median_lt_337")
            if ex["all"] is not None and ex["all"] < TWICE_S:  # type: ignore[index]
                reasons.append("all_median_lt_337")
            resume_ok = not reasons
        rec["evaluated"] = evaluated_of(summary)
        rec["resume_ok"] = resume_ok
        if resume_ok:
            reasons.append("other_section5_rules_and_stops_not_checked_here")
        rec["verdict"] = "resume_ok" if resume_ok else ("input_error" if input_error else "resume_not_ok")

    if args.mode != "resume" and rec.get("hours_missing"):
        # informational only, and appended AFTER the verdict is settled (running mode derives halt_due from `reasons`): no verdict or exit change
        reasons.append("hours_missing")
    rec["halt_due"] = halt_due
    rec["reasons"] = reasons
    rec["place_stop_flag"] = bool(args.place_stop)
    rec["stop"] = place_stop(run if run is not None else subprocess.run) if (halt_due and args.place_stop and args.mode != "resume") else None
    if halt_due:
        exit_code = EXIT_HALT_STOP_UNVERIFIED if (rec["stop"] is not None and not rec["stop"]["present"]) else EXIT_HALT
    elif args.mode == "resume":
        exit_code = EXIT_OK if resume_ok else (EXIT_INPUT if input_error else EXIT_RESUME_NOT_OK)
    elif input_error:
        exit_code = EXIT_INPUT
    elif args.mode == "day" and not rec["evaluated"]:
        exit_code = EXIT_UNEVALUATED
    else:
        exit_code = EXIT_OK
    rec["exit"] = exit_code
    _emit(rec, args.log)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
