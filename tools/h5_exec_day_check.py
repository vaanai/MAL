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
                     ONLY days[D].boost_median and len(days[D].boost_s). Nothing else in the file (trades, realized, halts, plans) is kept,
                     printed or logged.
                     halt_due iff the median is a finite number, the pool count is >= 30 and the median < 337.0 s.
                     The count: len(boost_s) when the executor still holds the list; it drops the list for days older than its last 3 day
                     keys (H5Counters.save), and then the executor's own invariant stands in (it sets boost_median only once 30 pools
                     closed): "pools_source": "boost_s" | "executor_invariant".
                     The median is the executor's stored figure (rounded to 3 decimals), the same value its own twice-latch compares.
                     No entry, no median, or fewer than 30 pools: "unevaluated" (exit 5). The two-consecutive-unevaluated rule is judged
                     by h5_boost_day_check.py's day mode on the shadow's ALL count, not here.
--mode decided --day D --tool NAME
                     Does --log already hold a FINAL day verdict for D written by NAME (h5_boost_day_check or h5_exec_day_check)?
                     Final: exit 0 or 5, or exit 3 with STOP verified present (`stop.present` true). Lines with exit 2 or 6, a halt
                     logged without --place-stop, or lines written under MAL_BDC_NOW are not final. Prints one JSON line; exit 0 when
                     decided, 1 when not. The loop uses it so that a catch-up after an outage never re-places STOP for a day whose
                     verdict is already recorded (a STOP the manager removed after an owner decision stays removed).
--place-stop: only with this flag and only when halt_due: `sudo -n touch /var/lib/mal-live/h5/STOP`, then `sudo -n test -e` it. This
tool NEVER removes STOP.

OUTPUT (day mode). One compact JSON line on stdout, appended to --log (a path under /data/mal/structure-monitor/ is refused). Exit codes,
the same as h5_boost_day_check.py's:
  0 no_halt        2 input error (sudo or dd failed, file too big, not JSON, wrong schema, bad or incomplete day)
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
VERSION = 1
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
    if rec.get("now_overridden") is True:
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
    p.add_argument("--mode", required=True, choices=("day", "decided"))
    p.add_argument("--day", required=True, help="YYYY-MM-DD: a completed UTC day after 2026-10-10")
    p.add_argument("--log", required=True, help="day: append one JSON line per run (created if missing); decided: the log to search")
    p.add_argument("--tool", choices=DECIDED_TOOLS, help="decided mode: whose day lines to look for")
    p.add_argument("--place-stop", action="store_true", help="day mode, when halt_due: sudo -n touch the H5 STOP, then test it")
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


def main(argv: list[str] | None = None, now: datetime | None = None, run: Callable[..., Any] | None = None) -> int:
    """`now` and `run` are for tests (the clock; a stand-in for subprocess.run). The CLI has neither."""
    args = build_parser().parse_args(argv)
    if args.mode == "decided":
        if not args.tool:
            print(json.dumps({"tool": TOOL, "mode": "decided", "error": "tool_required"}, separators=(",", ":")))
            return EXIT_INPUT
        found = decided(args.log, args.tool, args.day)
        print(json.dumps({"tool": TOOL, "mode": "decided", "of": args.tool, "day": args.day, "decided": found is not None,
                          "line": found}, separators=(",", ":")))
        return 0 if found is not None else 1
    if any(p == FORBIDDEN_LOG_PREFIX or p.startswith(FORBIDDEN_LOG_PREFIX + "/") for p in (os.path.abspath(args.log), os.path.realpath(args.log))):
        print(json.dumps({"tool": TOOL, "error": "log_path_forbidden"}, separators=(",", ":")))
        return EXIT_INPUT
    run = run if run is not None else subprocess.run
    now_dt = (now if now is not None else datetime.now(timezone.utc)).astimezone(timezone.utc)
    rec: dict[str, Any] = {"tool": TOOL, "v": VERSION, "mode": "day", "day": args.day, "now_utc": now_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                           "now_overridden": now is not None, "measure": "executor_boost_median", "source": COUNTERS_PATH,
                           "first_strike_day": FIRST_STRIKE_DAY, "thresholds": {"min_pools": MIN_POOLS, "twice_s": TWICE_S}}
    try:
        try:
            d = _parse_day(args.day)
        except ValueError:
            raise InputError("bad_date") from None
        if d <= _parse_day(FIRST_STRIKE_DAY):
            raise InputError("day_not_after_first_strike_day")
        if now_dt < datetime(d.year, d.month, d.day, tzinfo=timezone.utc) + timedelta(days=1):
            raise InputError("day_not_complete")
        fig = day_figures(read_counters(run), d.isoformat())
    except InputError as exc:
        rec.update({"error": str(exc), "verdict": "input_error", "halt_due": False, "stop": None, "exit": EXIT_INPUT})
        _emit(rec, args.log)
        return EXIT_INPUT
    med, pools = fig["median_s"], fig["pools"]
    judged = med is not None and (pools is None or pools >= MIN_POOLS)
    halt_due = judged and med < TWICE_S  # type: ignore[operator]
    reasons: list[str] = []
    if halt_due:
        reasons += ["executor_median_lt_337", "second_strike_after_first_strike_day"]
    elif not fig["entry"]:
        reasons.append("no_executor_day_entry")
    elif not judged:
        reasons.append("unevaluated_lt_30_pools")
    rec.update({"executor": fig, "evaluated": judged, "halt_due": halt_due, "reasons": reasons,
                "verdict": "halt_due" if halt_due else ("no_halt" if judged else "unevaluated"), "place_stop_flag": bool(args.place_stop)})
    rec["stop"] = place_stop(run) if (halt_due and args.place_stop) else None
    if halt_due:
        code = EXIT_HALT_STOP_UNVERIFIED if (rec["stop"] is not None and not rec["stop"]["present"]) else EXIT_HALT
    else:
        code = EXIT_OK if judged else EXIT_UNEVALUATED
    rec["exit"] = code
    _emit(rec, args.log)
    return code


if __name__ == "__main__":
    sys.exit(main())
