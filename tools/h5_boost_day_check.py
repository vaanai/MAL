#!/usr/bin/env python3
"""H5 BOOST day check: DEC-024 section 5.1 as amended by Amendment 4 (2026-10-10T07:27:25Z), read from the shadow's pool-close records.

Run as `/usr/bin/python3 -I tools/h5_boost_day_check.py ...`. Stdlib only (with -I the repo is not on sys.path).

WHAT IT MEASURES. The UTC-day median of the BOOST last slice after s0 (first PumpSwap print), over the pools the shadow closed in that
day's hourly files, for two groups:
  ALL    every kept pool (the executor's own count includes synthetic-migration pools);
  PLAIN  synthetic is False AND synthetic_src == "rpc" (the pools H5 can trade).
SYN (synthetic is True) and OTHER (anything else) are reported too. Timing by class is a structure field, never an outcome (EXP-024 Am.4 D1).

READING (the seal). Files <shadow-dir>/h5-shadow-<day>THH.jsonl, HH = 00..23; the day key is the file's hour (the executor keys a pool by
the time it reads the record). A line whose leading key says a type other than "pool" is counted by that type and never parsed. A pool
line (or a line with no leading type key) is parsed with an object hook that keeps ONLY the keys in ALLOWED_KEYS as each object closes;
no other field (outcome, trigger, min_q, triggered, fill, P&L) is kept, printed or logged. Records that are not type "pool", are sealed
(`sealed` is not exactly false) or have no mint are skipped.
  sec = boost_last_slice_s, else boost_last_slice_s_blocktime, else boost_last_slice_s_recv: the first that is a number (bool excluded),
  as tools/h5_executor.py does; then kept only if finite and 0 < sec < 2000 (no fall-through on a bad first value, as in the executor).
  sec is rounded to 3 decimals, as the executor does. A mint counts once per day, on its first kept record (file-hour, then line order).

NOT the executor's filter. The executor also drops pool records whose reason is not "horizon", whose `gap` is not false, or whose
`boost_src` is not pda/event_authority. `gap` and `boost_src` are outside the seal's field list, so this tool cannot apply them;
`n_not_horizon` reports how many kept pools have another reason.

JUDGING (DEC-024 Am.4 item 2, item 4, and the manager's stricter addition):
  --mode day      a COMPLETED UTC day D > --first-strike-day (default 2026-10-10; the 10-10T07:11Z fire is the first strike).
                  evaluated iff n_plain >= 30 and n_all >= 30. halt_due if the plain median < 337 s or the all median < 337 s.
                  Unevaluated (including no files: fail closed) and the previous day also unevaluated -> halt_due. The previous day
                  is read from the latest `--mode day` line for it in --log; when the log has none, it is recomputed from the files.
  --mode running  today so far. halt_due if (n_plain >= 30 and plain median < 335 s) or (n_all >= 30 and all median < 335 s).
  --mode resume   UTC day --first-strike-day only (default 10-10), once complete. resume_ok iff n_plain >= 30, n_all >= 30, plain
                  median >= 337 s and all median >= 337 s. It does not check the other section 5 rules or stops (the manager does).
                  It never places STOP.
--place-stop: only with this flag, and only when halt_due: `sudo -n touch /var/lib/mal-live/h5/STOP`, then
`sudo -n test -e /var/lib/mal-live/h5/STOP`; both return codes are logged. This tool NEVER removes STOP.

OUTPUT. One compact JSON line on stdout with every number, the verdict and the reasons; the same line is appended to --log (created if
missing; a path under /data/mal/structure-monitor/ is refused). Exit codes: 0 no halt / resume_ok; 3 halt_due; 4 resume not ok;
2 input error (missing dir or no files; in day mode that is also an unevaluated day, and a second one in a row is halt_due, exit 3).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable

TOOL = "h5_boost_day_check"
VERSION = 1
ALLOWED_KEYS = frozenset({
    "type", "reason", "sealed", "pool", "mint", "s0", "s0_block_time", "boost_last_slice_s", "boost_last_slice_s_blocktime",
    "boost_last_slice_s_recv", "boost_slices", "synthetic", "synthetic_src",
})
SEC_KEYS = ("boost_last_slice_s", "boost_last_slice_s_blocktime", "boost_last_slice_s_recv")  # the executor's order
SEC_MAX = 2000.0
MIN_POOLS = 30  # DEC-024 Am.4 item 2 (the executor's BOOST_MEDIAN_MIN_POOLS)
TWICE_S = 337.0  # below this on a completed day: the second strike (Am.4 item 2) / no resume (item 4)
HALT_S = 335.0  # below this on today's running median: halt
FIRST_STRIKE_DAY = "2026-10-10"
STOP_PATH = "/var/lib/mal-live/h5/STOP"
FORBIDDEN_LOG_PREFIX = "/data/mal/structure-monitor"
LEAD_TYPE_RE = re.compile(rb'^\{\s*"type"\s*:\s*"([A-Za-z0-9_\-]{1,40})"')
EXIT_OK, EXIT_INPUT, EXIT_HALT, EXIT_RESUME_NOT_OK = 0, 2, 3, 4


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
            sec = float(v)
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
        raise InputError("read_failed") from None


def read_day(shadow_dir: str, day: str, parse: Callable[[bytes], dict[str, Any] | None] | None = None) -> dict[str, Any]:
    """Read one UTC day's hourly files. Returns counts and the kept seconds per mint. Raises InputError on a missing dir, no files or a
    read error part-way through a file (fail closed)."""
    parse = parse or parse_pool_line
    if not os.path.isdir(shadow_dir):
        raise InputError("shadow_dir_missing")
    hours_read: list[int] = []
    lines_by_type: dict[str, int] = {}
    skipped = {"sealed": 0, "no_mint": 0, "bad_sec": 0, "dup_mint": 0, "bad_line": 0, "unreadable_file": 0}
    seen: dict[str, tuple[str, float, bool]] = {}  # mint -> (class, sec, reason == "horizon")
    pool_records = 0
    for h, path in hour_files(shadow_dir, day):
        if os.path.islink(path) or not os.path.isfile(path):
            continue
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except OSError:
            skipped["unreadable_file"] += 1
            continue
        hours_read.append(h)
        with os.fdopen(fd, "rb") as fh:
            for raw in _lines(fh):
                if not raw.strip():
                    continue
                m = LEAD_TYPE_RE.match(raw)
                if m is not None and m.group(1) != b"pool":
                    t = m.group(1).decode("ascii")
                    lines_by_type[t] = lines_by_type.get(t, 0) + 1
                    continue  # counted by its leading type only, never parsed
                rec = parse(raw)
                if rec is None:
                    skipped["bad_line"] += 1
                    continue
                t = rec.get("type")
                t = t if isinstance(t, str) and 0 < len(t) <= 40 else "?"
                lines_by_type[t] = lines_by_type.get(t, 0) + 1
                if t != "pool":
                    continue
                pool_records += 1
                if rec.get("sealed") is not False:
                    skipped["sealed"] += 1
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
                seen[mint] = (pool_class(rec), sec, rec.get("reason") == "horizon")
    if not hours_read:
        raise InputError("no_files")
    return {"hours_read": hours_read, "lines_by_type": dict(sorted(lines_by_type.items())), "pool_records": pool_records,
            "skipped": skipped, "seen": seen}


def _median(xs: list[float]) -> float | None:
    return statistics.median(xs) if xs else None


def summarize(read: dict[str, Any]) -> dict[str, Any]:
    seen = read["seen"]
    groups: dict[str, list[float]] = {"all": [], "plain": [], "syn": [], "other": []}
    not_horizon = 0
    for cls, sec, horizon in seen.values():
        groups["all"].append(sec)
        groups[cls].append(sec)
        not_horizon += 0 if horizon else 1
    out: dict[str, Any] = {}
    for g, xs in groups.items():
        med = _median(xs)
        out[g] = {"n": len(xs), "median_s": None if med is None else round(med, 4)}
    out["n_not_horizon"] = not_horizon
    out["_exact"] = {g: _median(xs) for g, xs in groups.items()}
    return out


def parse_day(s: str) -> date:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s or ""):
        raise ValueError(s)
    return date.fromisoformat(s)


def parse_now(s: str | None) -> datetime:
    if s is None:
        return datetime.now(timezone.utc)
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def day_complete(d: date, now: datetime) -> bool:
    return now >= datetime(d.year, d.month, d.day, tzinfo=timezone.utc) + timedelta(days=1)


def prev_day_from_log(log_path: str, day: str) -> bool | None:
    """The latest `--mode day` line for `day` in the log: its `evaluated`. None if the log has none (or cannot be read)."""
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
            if (isinstance(rec, dict) and rec.get("tool") == TOOL and rec.get("mode") == "day" and rec.get("day") == day
                    and isinstance(rec.get("evaluated"), bool)):
                found = rec["evaluated"]
    return found


def evaluated_of(summary: dict[str, Any] | None) -> bool:
    return summary is not None and summary["plain"]["n"] >= MIN_POOLS and summary["all"]["n"] >= MIN_POOLS


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
    p.add_argument("--day", help="YYYY-MM-DD: the completed day (day mode) or the resume day (resume mode; default --first-strike-day)")
    p.add_argument("--log", required=True, help="append one JSON line per run (created if missing)")
    p.add_argument("--place-stop", action="store_true", help="when halt_due: sudo -n touch the H5 STOP, then test it")
    p.add_argument("--first-strike-day", default=FIRST_STRIKE_DAY)
    p.add_argument("--now-utc", help=argparse.SUPPRESS)  # tests only
    return p


def _public(summary: dict[str, Any] | None) -> dict[str, Any]:
    if summary is None:
        return {}
    return {k: v for k, v in summary.items() if not k.startswith("_")}


def main(argv: list[str] | None = None, run: Callable[..., Any] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if any(p == FORBIDDEN_LOG_PREFIX or p.startswith(FORBIDDEN_LOG_PREFIX + "/") for p in (os.path.abspath(args.log), os.path.realpath(args.log))):
        print(json.dumps({"tool": TOOL, "error": "log_path_forbidden"}, separators=(",", ":")))
        return EXIT_INPUT
    try:
        now = parse_now(args.now_utc)
        first_strike = parse_day(args.first_strike_day)
        today = now.date()
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
            if not day_complete(d, now):
                raise InputError("day_not_complete")
    except ValueError:
        rec = {"tool": TOOL, "v": VERSION, "mode": args.mode, "error": "bad_date", "halt_due": False, "exit": EXIT_INPUT}
        line = json.dumps(rec, separators=(",", ":"))
        append_log(args.log, line)
        print(line)
        return EXIT_INPUT
    except InputError as exc:
        rec = {"tool": TOOL, "v": VERSION, "mode": args.mode, "day": args.day, "now_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
               "error": str(exc), "halt_due": False, "exit": EXIT_INPUT}
        line = json.dumps(rec, separators=(",", ":"))
        append_log(args.log, line)
        print(line)
        return EXIT_INPUT

    day = d.isoformat()
    rec: dict[str, Any] = {"tool": TOOL, "v": VERSION, "mode": args.mode, "day": day, "now_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                           "day_complete": day_complete(d, now), "first_strike_day": first_strike.isoformat(),
                           "thresholds": {"min_pools": MIN_POOLS, "twice_s": TWICE_S, "halt_s": HALT_S}}
    reasons: list[str] = []
    summary: dict[str, Any] | None = None
    input_error: str | None = None
    try:
        rd = read_day(args.shadow_dir, day)
        summary = summarize(rd)
        rec.update({"hours_read": rd["hours_read"], "hours_missing": [h for h in range(24) if h not in rd["hours_read"]],
                    "lines_by_type": rd["lines_by_type"], "pool_records": rd["pool_records"], "skipped": rd["skipped"]})
        rec.update(_public(summary))
    except InputError as exc:
        input_error = str(exc)
        rec["error"] = input_error
        reasons.append(input_error)

    halt_due = False
    exit_code = EXIT_OK
    if args.mode == "day":
        evaluated = input_error is None and evaluated_of(summary)
        rec["evaluated"] = evaluated
        if evaluated:
            ex = summary["_exact"]  # type: ignore[index]
            if ex["plain"] < TWICE_S:
                reasons.append("plain_median_lt_337")
            if ex["all"] < TWICE_S:
                reasons.append("all_median_lt_337")
            halt_due = bool(reasons)
            if halt_due:
                reasons.append("second_strike_after_first_strike_day")
        else:
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
        exit_code = EXIT_HALT if halt_due else (EXIT_INPUT if input_error else EXIT_OK)
        rec["verdict"] = "halt_due" if halt_due else ("no_halt" if evaluated else "unevaluated")
    elif args.mode == "running":
        if input_error is None:
            ex = summary["_exact"]  # type: ignore[index]
            rec["judged"] = {"plain": summary["plain"]["n"] >= MIN_POOLS, "all": summary["all"]["n"] >= MIN_POOLS}  # type: ignore[index]
            if summary["plain"]["n"] >= MIN_POOLS and ex["plain"] < HALT_S:  # type: ignore[index]
                reasons.append("plain_median_lt_335")
            if summary["all"]["n"] >= MIN_POOLS and ex["all"] < HALT_S:  # type: ignore[index]
                reasons.append("all_median_lt_335")
            halt_due = bool(reasons)
        exit_code = EXIT_HALT if halt_due else (EXIT_INPUT if input_error else EXIT_OK)
        rec["verdict"] = "halt_due" if halt_due else ("input_error" if input_error else "no_halt")
    else:  # resume: never places STOP, never removes it
        ok = False
        if input_error is None:
            ex = summary["_exact"]  # type: ignore[index]
            if not rec["day_complete"]:
                reasons.append("day_not_complete")
            if summary["plain"]["n"] < MIN_POOLS:  # type: ignore[index]
                reasons.append("plain_lt_30_pools")
            if summary["all"]["n"] < MIN_POOLS:  # type: ignore[index]
                reasons.append("all_lt_30_pools")
            if ex["plain"] is not None and ex["plain"] < TWICE_S:
                reasons.append("plain_median_lt_337")
            if ex["all"] is not None and ex["all"] < TWICE_S:
                reasons.append("all_median_lt_337")
            ok = not reasons
        rec["evaluated"] = input_error is None and evaluated_of(summary)
        rec["resume_ok"] = ok
        if ok:
            reasons.append("other_section5_rules_and_stops_not_checked_here")
        exit_code = EXIT_OK if ok else (EXIT_INPUT if input_error else EXIT_RESUME_NOT_OK)
        rec["verdict"] = "resume_ok" if ok else ("input_error" if input_error else "resume_not_ok")

    rec["halt_due"] = halt_due
    rec["reasons"] = reasons
    if args.mode != "resume" and halt_due and args.place_stop:
        rec["stop"] = place_stop(run if run is not None else subprocess.run)
    else:
        rec["stop"] = None
    rec["place_stop_flag"] = bool(args.place_stop)
    rec["exit"] = exit_code
    line = json.dumps(rec, separators=(",", ":"), allow_nan=False)
    if not append_log(args.log, line):
        print(json.dumps({"tool": TOOL, "warning": "log_write_failed"}, separators=(",", ":")), file=sys.stderr)
    print(line)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
