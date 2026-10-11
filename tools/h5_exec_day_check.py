#!/usr/bin/env python3
"""H5 executor day check: DEC-024 section 5.1 on the EXECUTOR'S OWN completed-day median (Amendment 4 item 2; Amendment 5 item 6).

Run as `/usr/bin/python3 -I tools/h5_exec_day_check.py ...`. Stdlib only (with -I the repo is not on sys.path).

WHY. Amendment 4 item 2 makes the executor's UTC-day median of the BOOST last slice (over at least 30 pools) the owner's measure. The
executor cannot count the 10-10T07:11Z fire as the first "below 337 s", so its own `boost_median_lt_337_twice` latch needs two more low
days. Amendment 5 item 6: every scheduled run also reads that figure, and a completed UTC day from 10-11 on below 337 s is the second
strike. `tools/h5_boost_day_check.py` judges the shadow's figures (ALL and PLAIN); this tool judges the executor's. The enforce loop
(scripts/mal-fast/h5-enforce-loop.sh) runs both on every day slot.

--mode day --day D   D must be a COMPLETED UTC day after FIRST_STRIKE_DAY (2026-10-10). Reads, through the allowlisted
                     `sudo -n /usr/bin/dd iflag=nofollow status=none if=/var/lib/mal-live/h5/live/h5-counters.json` (never cat; 8 MB cap),
                     ONLY days[D].boost_median and len(days[D].boost_s) (and the same two for D-1). Nothing else in the file (trades,
                     realized, halts, plans) is kept, printed or logged.
                     A day is EVALUATED when its median is a finite number and its pool count is >= 30. The count: len(boost_s) when the
                     executor still holds the list; it drops the list for days older than its last 3 day keys (H5Counters.save), and then
                     the executor's own invariant stands in (it sets boost_median only once 30 pools closed): "pools_source": "boost_s" |
                     "executor_invariant". The median is the executor's stored figure (rounded to 3 decimals), the same value its own
                     twice-latch compares.
                     A day is UNEVALUATED when it has an entry without a median or with fewer than 30 pools, or when it has NO entry while
                     the executor was running that day. "Running that day" comes from the unit's own transition times
                     (`systemctl show mal-h5-executor.service`, no sudo): its last active span overlaps D -> ran; its last inactive span
                     covers all of D -> did not run ("not_running", not unevaluated); anything else (unit unreadable, not loaded, history
                     not covering D) counts as ran, so the doubt errs toward STOP.
                     halt_due when (a) D is evaluated and its median < 337.0 s (reason executor_median_lt_337), or (b) D is unevaluated
                     and D-1 is also unevaluated, with D-1 after FIRST_STRIKE_DAY (reason two_consecutive_unevaluated_days: DEC-024 Am.4
                     item 2's "two consecutive unevaluated days halt", on the executor's measure). The two reasons stay distinct: DEC-028
                     section 3.5 says (b) is not an Option B trigger. D = 10-11 can only halt on (a): 10-10 is not after the first-strike
                     day. Otherwise D unevaluated is exit 5, D evaluated at or above 337.0 s is exit 0.
--mode running --day TODAY
                     REPORT ONLY: the executor's running median for today (days[TODAY].boost_median, set once 30 pools closed) and its pool
                     count. The running 335 s rule is the executor's own latch (boost_median_lt_335); this mode never places STOP and
                     refuses --place-stop. Exit 0 (read and reported) or 2 (input error).
--dry-read           (with --mode day or running) reads the counters exactly as an enforcing run does, for ANY valid day (also 10-10 and
                     earlier, also today), prints the would-be verdict ("verdict": "dry_read", "would_verdict", "would_halt_due"), places
                     no STOP (refuses --place-stop), never writes --log (so it never counts as a final line) and exits 0 when the read
                     worked, 2 when it did not. It lets the manager prove the sudo dd path before the first enforcing read: the first
                     enforcing (day mode, --place-stop) counters read of the enforce loop is the 2026-10-12T00:21Z slot, for day 10-11.
--mode decided --day D --tool NAME
                     Does --log already hold a FINAL day verdict for D written by NAME (h5_boost_day_check or h5_exec_day_check)?
                     Final: exit 0 or 5, or exit 3 with STOP verified present (`stop.present` true). Lines with exit 2 or 6, a halt
                     logged without --place-stop, dry-read lines, or lines written under MAL_BDC_NOW are not final. Prints one JSON line;
                     exit 0 when decided, 1 when not. The loop uses it so that a catch-up after an outage never re-places STOP for a day
                     whose verdict is already recorded (a STOP the manager removed after an owner decision stays removed).
--place-stop: only with this flag, only in day mode and only when halt_due: `sudo -n touch /var/lib/mal-live/h5/STOP`, then
`sudo -n test -e` it. This tool NEVER removes STOP.

OUTPUT (day and running mode). One compact JSON line on stdout, appended to --log (a path under /data/mal/structure-monitor/ is refused).
Exit codes, the same as h5_boost_day_check.py's:
  0 no_halt / report   2 input error (sudo or dd failed, file too big, not JSON, wrong schema, bad or incomplete day)
  3 halt_due (STOP verified present, or no --place-stop)        5 unevaluated        6 halt_due with --place-stop and STOP NOT verified
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

TOOL = "h5_exec_day_check"
VERSION = 2
COUNTERS_PATH = "/var/lib/mal-live/h5/live/h5-counters.json"
DD_ARGV = ("sudo", "-n", "/usr/bin/dd", "iflag=nofollow", "status=none", f"if={COUNTERS_PATH}")  # the allowlisted read, exactly
STOP_PATH = "/var/lib/mal-live/h5/STOP"
MAX_READ = 8 * 1024 * 1024
MIN_POOLS = 30  # the executor's BOOST_MEDIAN_MIN_POOLS
TWICE_S = 337.0  # the executor's BOOST_MEDIAN_TWICE_S: below this on a completed day from 10-11 on is the second strike
FIRST_STRIKE_DAY = "2026-10-10"  # pinned: the 10-10T07:11Z fire. Not a flag.
COUNTERS_SCHEMA = "h5_counters_v1"
FORBIDDEN_LOG_PREFIX = "/data/mal/structure-monitor"
DECIDED_TOOLS = ("h5_boost_day_check", TOOL)
EXIT_OK, EXIT_INPUT, EXIT_HALT, EXIT_UNEVALUATED, EXIT_HALT_STOP_UNVERIFIED = 0, 2, 3, 5, 6


UNIT = "mal-h5-executor.service"
SYSTEMCTL_ARGV = ("/usr/bin/systemctl", "show", "--no-pager",
                  "--property=LoadState,ActiveState,ActiveEnterTimestamp,ActiveExitTimestamp,InactiveEnterTimestamp,InactiveExitTimestamp", UNIT)
SYSTEMCTL_ENV = {"PATH": "/usr/bin:/bin", "TZ": "UTC", "LC_ALL": "C", "SYSTEMD_PAGER": "", "SYSTEMD_COLORS": "0"}  # timestamps in UTC
RUNNING_LATCH_S = 335.0  # the executor's BOOST_MEDIAN_HALT_S: its own running latch; reported, never enforced here
PAIR_REASON = "two_consecutive_unevaluated_days"


class InputError(Exception):
    pass


def read_counters(run: Callable[..., Any]) -> dict[str, Any]:
    """The whole counters file through the allowlisted sudo dd. Any failure is an input error, never "absent"."""
    try:
        cp = run(list(DD_ARGV), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        raise InputError("sudo_dd_failed") from None
    if cp.returncode != 0:
        raise InputError("sudo_dd_failed")
    raw = cp.stdout or b""
    if len(raw) > MAX_READ:
        raise InputError("counters_too_big")
    if not raw:
        raise InputError("counters_empty")
    try:
        obj = json.loads(raw)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise InputError("counters_not_json") from None
    if not isinstance(obj, dict) or obj.get("schema") != COUNTERS_SCHEMA or not isinstance(obj.get("days"), dict):
        raise InputError("counters_schema")
    return obj


def day_figures(counters: dict[str, Any], day: str) -> dict[str, Any]:
    """Only the two fields this rule needs. Everything else in the day entry (trades, realized) is dropped here."""
    entry = counters["days"].get(day)
    if not isinstance(entry, dict):
        return {"entry": False, "median_s": None, "pools": None, "pools_source": None}
    med = entry.get("boost_median")
    if isinstance(med, bool) or not isinstance(med, (int, float)) or not math.isfinite(float(med)):
        med = None
    bs = entry.get("boost_s")
    if isinstance(bs, dict):
        pools, src = len(bs), "boost_s"
    else:
        pools, src = None, ("executor_invariant" if med is not None else None)
    return {"entry": True, "median_s": None if med is None else float(med), "pools": pools, "pools_source": src}


def evaluated(fig: dict[str, Any]) -> bool:
    return fig["median_s"] is not None and (fig["pools"] is None or fig["pools"] >= MIN_POOLS)


def _sd_ts(value: str | None) -> float | None:
    """systemctl's `Sun 2026-10-11 07:11:00 UTC` (TZ=UTC) -> epoch seconds; None when empty, n/a or not UTC."""
    parts = (value or "").split()
    if len(parts) == 4:
        parts = parts[1:]
    if len(parts) != 3 or parts[2] not in ("UTC", "GMT"):
        return None
    try:
        return datetime.strptime(f"{parts[0]} {parts[1]}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def unit_props(run: Callable[..., Any]) -> dict[str, str] | None:
    """The executor unit's state and transition times (systemctl show: no sudo, read only). None when it cannot be read."""
    try:
        cp = run(list(SYSTEMCTL_ARGV), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30, check=False,
                 env=dict(SYSTEMCTL_ENV))
    except (OSError, subprocess.SubprocessError):
        return None
    if cp.returncode != 0:
        return None
    props: dict[str, str] = {}
    for line in (cp.stdout or b"").decode("utf-8", "replace").splitlines():
        k, sep, v = line.partition("=")
        if sep:
            props[k.strip()] = v.strip()
    return props


def ran_on(props: dict[str, str] | None, d: date, now_s: float) -> tuple[str, str]:
    """Was the executor unit up during UTC day d? ("yes" | "no" | "unknown", why). Only "no" spares a day without an entry; "unknown"
    is treated as "yes" by the caller (the doubt errs toward STOP)."""
    if props is None:
        return "unknown", "unit_unreadable"
    if props.get("LoadState") != "loaded":
        return "unknown", "unit_not_loaded"
    ds = datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()
    de = ds + 86400
    st = props.get("ActiveState", "")
    ae, ax = _sd_ts(props.get("ActiveEnterTimestamp")), _sd_ts(props.get("ActiveExitTimestamp"))
    ie, ix = _sd_ts(props.get("InactiveEnterTimestamp")), _sd_ts(props.get("InactiveExitTimestamp"))
    span = None  # the last active span
    if st in ("active", "reloading") and ae is not None:
        span = (ae, now_s)
    elif ae is not None and ax is not None and ax >= ae:
        span = (ae, ax)
    if span is not None and span[0] < de and span[1] > ds:
        return "yes", "unit_active_during_day"
    off = None  # the last inactive (or failed) span
    if st in ("inactive", "failed") and ie is not None:
        off = (ie, now_s)
    elif ie is not None and ix is not None and ix >= ie:
        off = (ie, ix)
    if off is not None and off[0] <= ds and off[1] >= de:
        return "no", "unit_inactive_all_day"
    return "unknown", "unit_history_does_not_cover_day"


def classify(counters: dict[str, Any], d: date, unit: Callable[[], dict[str, str] | None], now_s: float) -> dict[str, Any]:
    """One completed day on the executor's measure: evaluated | unevaluated | not_running (no entry, unit down all day)."""
    fig = day_figures(counters, d.isoformat())
    out: dict[str, Any] = {"day": d.isoformat(), "executor": fig}
    if evaluated(fig):
        out["status"] = "evaluated"
    elif fig["entry"]:
        out.update({"status": "unevaluated", "why": "unevaluated_lt_30_pools"})
    else:
        ran, why = ran_on(unit(), d, now_s)
        out.update({"executor_ran": ran, "ran_why": why})
        out.update({"status": "not_running", "why": "executor_not_running_on_day"} if ran == "no" else
                   {"status": "unevaluated", "why": "no_executor_day_entry"})
    return out


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


def is_final(rec: Any, tool: str, day: str) -> bool:
    """A day line that settles day D for `tool` (see the module docstring)."""
    if not (isinstance(rec, dict) and rec.get("tool") == tool and rec.get("mode") == "day" and rec.get("day") == day):
        return False
    if rec.get("now_overridden") is True or rec.get("dry_read") is True:
        return False
    ex = rec.get("exit")
    if isinstance(ex, bool) or not isinstance(ex, int):
        return False
    if ex in (EXIT_OK, EXIT_UNEVALUATED):
        return True
    stop = rec.get("stop")
    return ex == EXIT_HALT and isinstance(stop, dict) and stop.get("present") is True


def decided(log_path: str, tool: str, day: str) -> dict[str, Any] | None:
    """The first final line for (tool, day) in the log, reduced to structure fields; None if there is none or the log cannot be read."""
    try:
        fh = open(log_path, "rb")
    except OSError:
        return None
    with fh:
        for raw in fh:
            try:
                rec = json.loads(raw)
            except (ValueError, UnicodeDecodeError, RecursionError):
                continue
            if is_final(rec, tool, day):
                return {"exit": rec["exit"], "verdict": rec.get("verdict"), "now_utc": rec.get("now_utc")}
    return None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="H5 executor completed-day BOOST median check (DEC-024 5.1, Am.4 item 2, Am.5 item 6). Never removes STOP.")
    p.add_argument("--mode", required=True, choices=("day", "running", "decided"))
    p.add_argument("--day", required=True, help="YYYY-MM-DD: day mode, a completed UTC day after 2026-10-10; running mode, today")
    p.add_argument("--log", help="day/running: append one JSON line per run (created if missing; required unless --dry-read); "
                                 "decided: the log to search")
    p.add_argument("--tool", choices=DECIDED_TOOLS, help="decided mode: whose day lines to look for")
    p.add_argument("--place-stop", action="store_true", help="day mode, when halt_due: sudo -n touch the H5 STOP, then test it")
    p.add_argument("--dry-read", action="store_true", help="read and report the would-be verdict for any day; no STOP, no log line")
    return p


def _emit(rec: dict[str, Any], log: str | None) -> None:
    line = json.dumps(rec, separators=(",", ":"), allow_nan=False)
    if log is not None and not append_log(log, line):
        print(json.dumps({"tool": TOOL, "warning": "log_write_failed"}, separators=(",", ":")), file=sys.stderr)
    print(line)


def _parse_day(s: str) -> date:
    if len(s) != 10 or s[4] != "-" or s[7] != "-":
        raise ValueError(s)
    return date.fromisoformat(s)


def _fail(rec: dict[str, Any], err: str, log: str | None) -> int:
    rec.update({"error": err, "verdict": "input_error", "halt_due": False, "stop": None, "exit": EXIT_INPUT})
    _emit(rec, log)
    return EXIT_INPUT


def main(argv: list[str] | None = None, now: datetime | None = None, run: Callable[..., Any] | None = None) -> int:
    """`now` and `run` are for tests (the clock; a stand-in for subprocess.run). The CLI has neither."""
    args = build_parser().parse_args(argv)
    if args.mode == "decided":
        if not args.tool or not args.log:
            print(json.dumps({"tool": TOOL, "mode": "decided", "error": "tool_and_log_required"}, separators=(",", ":")))
            return EXIT_INPUT
        found = decided(args.log, args.tool, args.day)
        print(json.dumps({"tool": TOOL, "mode": "decided", "of": args.tool, "day": args.day, "decided": found is not None,
                          "line": found}, separators=(",", ":")))
        return 0 if found is not None else 1
    dry = bool(args.dry_read)
    log = None if dry else args.log  # a dry read never writes the log, so it can never settle a day
    if not dry and not args.log:
        print(json.dumps({"tool": TOOL, "error": "log_required"}, separators=(",", ":")))
        return EXIT_INPUT
    if log is not None and any(p == FORBIDDEN_LOG_PREFIX or p.startswith(FORBIDDEN_LOG_PREFIX + "/")
                               for p in (os.path.abspath(log), os.path.realpath(log))):
        print(json.dumps({"tool": TOOL, "error": "log_path_forbidden"}, separators=(",", ":")))
        return EXIT_INPUT
    run = run if run is not None else subprocess.run
    now_dt = (now if now is not None else datetime.now(timezone.utc)).astimezone(timezone.utc)
    rec: dict[str, Any] = {"tool": TOOL, "v": VERSION, "mode": args.mode, "day": args.day, "now_utc": now_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                           "now_overridden": now is not None, "measure": "executor_boost_median", "source": COUNTERS_PATH,
                           "first_strike_day": FIRST_STRIKE_DAY, "thresholds": {"min_pools": MIN_POOLS, "twice_s": TWICE_S}}
    if dry:
        rec["dry_read"] = True
    try:
        d = _parse_day(args.day)
    except ValueError:
        return _fail(rec, "bad_date", log)
    if dry and args.place_stop:
        return _fail(rec, "dry_read_never_places_stop", log)
    if args.mode == "running" and args.place_stop:
        return _fail(rec, "place_stop_not_in_running_mode", log)  # the running 335 s rule is the executor's own latch
    if not dry:
        if args.mode == "running" and d != now_dt.date():
            return _fail(rec, "running_day_not_today", log)
        if args.mode == "day" and d <= _parse_day(FIRST_STRIKE_DAY):
            return _fail(rec, "day_not_after_first_strike_day", log)
        if args.mode == "day" and now_dt < datetime(d.year, d.month, d.day, tzinfo=timezone.utc) + timedelta(days=1):
            return _fail(rec, "day_not_complete", log)
    try:
        counters = read_counters(run)
    except InputError as exc:
        return _fail(rec, str(exc), log)

    if args.mode == "running":
        fig = day_figures(counters, d.isoformat())
        rec.update({"executor": fig, "report_only": True, "executor_latch_s": RUNNING_LATCH_S,
                    "running_lt_latch": None if fig["median_s"] is None else fig["median_s"] < RUNNING_LATCH_S,
                    "verdict": "dry_read" if dry else "report", "halt_due": False, "stop": None, "exit": EXIT_OK})
        _emit(rec, log)
        return EXIT_OK

    _unit: list[dict[str, str] | None] = []

    def unit() -> dict[str, str] | None:  # at most one systemctl call, and only when a day has no entry
        if not _unit:
            _unit.append(unit_props(run))
        return _unit[0]

    now_s = now_dt.timestamp()
    cur = classify(counters, d, unit, now_s)
    fig = cur["executor"]
    halt_due, reasons = False, []
    if cur["status"] == "evaluated":
        if fig["median_s"] < TWICE_S:
            halt_due, reasons = True, ["executor_median_lt_337", "second_strike_after_first_strike_day"]
    else:
        reasons.append(cur["why"])
        prev = d - timedelta(days=1)
        if cur["status"] == "unevaluated" and prev > _parse_day(FIRST_STRIKE_DAY):
            pc = classify(counters, prev, unit, now_s)
            rec["prev_day"] = pc
            if pc["status"] == "unevaluated":
                halt_due, reasons = True, [PAIR_REASON]
    rec["executor"] = fig
    for k in ("executor_ran", "ran_why"):
        if k in cur:
            rec[k] = cur[k]
    if _unit and _unit[0] is not None:
        rec["unit"] = {k: _unit[0].get(k) for k in ("LoadState", "ActiveState", "ActiveEnterTimestamp", "ActiveExitTimestamp",
                                                     "InactiveEnterTimestamp", "InactiveExitTimestamp")}
    judged = cur["status"] == "evaluated"
    verdict = "halt_due" if halt_due else ("no_halt" if judged else "unevaluated")
    rec.update({"day_status": cur["status"], "evaluated": judged, "reasons": reasons, "place_stop_flag": bool(args.place_stop)})
    if dry:
        rec.update({"verdict": "dry_read", "would_verdict": verdict, "would_halt_due": halt_due, "halt_due": False, "stop": None,
                    "exit": EXIT_OK})
        _emit(rec, None)
        return EXIT_OK
    rec.update({"halt_due": halt_due, "verdict": verdict})
    rec["stop"] = place_stop(run) if (halt_due and args.place_stop) else None
    if halt_due:
        code = EXIT_HALT_STOP_UNVERIFIED if (rec["stop"] is not None and not rec["stop"]["present"]) else EXIT_HALT
    else:
        code = EXIT_OK if judged else EXIT_UNEVALUATED
    rec["exit"] = code
    _emit(rec, log)
    return code


if __name__ == "__main__":
    sys.exit(main())
