#!/usr/bin/env python3
"""s1t BOOST-timing bar checker (DEC-031 Amendment 29 B1 and B2; QP-RULING-1106-boost-shift 2(a)).

Structure only: reads ONE jsonl of A3 structure-monitor records (the --daily path, REQUIRED, no default: a call that leaves
it out is a usage error, so no automatic run can read another loop's file) and nothing else. Opens no tape, ledger, bots.json, shadow, smoke dir or job view.
It reads only the fields `run_utc`, `status` and `boost.last_slice_after_migrate_s.{median,n}` of each line.

  b1 --registration-utc ISO --daily PATH   B1 pre-clock bar; PATH = /data/mal/structure-monitor/daily.jsonl (s1t's own file
                                            does not exist before the registration)
  b2 --after-run-utc ISO    --daily PATH   B2 in-window hard halt; ISO = the REGISTRATION instant (not the clock); PATH = s1t's
                                            own A3 file $HOME/data/structure-monitor-s1t/daily.jsonl

Which rows are records: a line whose `status` is "ok" or "partial" (the monitor evaluates its A3 flags identically on both;
`rec["status"] = "ok" if not errors else "partial"`). Any other status is not a record and is ignored.
A record with no usable `boost.last_slice_after_migrate_s` (missing, median not a number, n not an integer >= 1) is
unevaluable: it is a record that FAILS B1 (fail closed), and it is skipped by B2 (it neither halts nor resets a pair).
A median that is NaN or +-Infinity (json.loads accepts them) is not a number here: unevaluable.
Records are ordered by parsed run_utc; equal run_utc keep file order.

B1: DAILY records: among the records with run_utc strictly BEFORE the registration instant, keep one per UTC date, the one with
    the latest run_utc (a later file line wins an equal run_utc). The two latest such dates each have median >= 330 and n >= 6.
    Fewer than two dates is a FAIL.
B2: EVERY record counts (no per-date collapse): over records with run_utc strictly AFTER the given instant, in run_utc order, skipping unevaluable ones: the first two
    consecutive evaluable records that both have median < 330 give HALT at the second record's run_utc. (No n floor in B2:
    the ruling text has none.)

Exit codes: 0 B1 PASS / B2 OK;  3 B1 FAIL / B2 HALT;  2 input error (bad argument, unreadable file, malformed json line,
non-object line, record without a parseable run_utc). Output is the verdict line plus the run_utc, median and n used.
"""
import argparse
import json
import math
import sys
from datetime import datetime, timezone

MEDIAN_BAR_S = 330
B1_MIN_N = 6
STATUSES = ("ok", "partial")
EXIT_OK, EXIT_INPUT, EXIT_FAIL = 0, 2, 3


class InputError(Exception):
    pass


def parse_utc(s):
    if not isinstance(s, str):
        raise InputError("not a string: %r" % (s,))
    t = s.strip()
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    try:
        d = datetime.fromisoformat(t)
    except ValueError:
        raise InputError("bad ISO instant: %r" % (s,))
    if d.tzinfo is None:
        raise InputError("instant has no zone (use Z): %r" % (s,))
    return d.astimezone(timezone.utc)


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def load_records(path):
    """Records (status ok/partial), sorted by run_utc: list of (datetime, run_utc_str, median|None, n|None)."""
    out = []
    try:
        fh = open(path, "r", encoding="utf-8")
    except OSError as e:
        raise InputError("cannot open %s: %s" % (path, e.strerror))
    with fh:
        for i, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except ValueError:
                raise InputError("line %d is not json" % i)
            if not isinstance(r, dict):
                raise InputError("line %d is not an object" % i)
            if r.get("status") not in STATUSES:
                continue
            ru = r.get("run_utc")
            try:
                when = parse_utc(ru)
            except InputError as e:
                raise InputError("line %d run_utc: %s" % (i, e))
            b = r.get("boost")
            ls = b.get("last_slice_after_migrate_s") if isinstance(b, dict) else None
            med = n = None
            if isinstance(ls, dict) and _num(ls.get("median")) and isinstance(ls.get("n"), int) \
                    and not isinstance(ls.get("n"), bool) and ls.get("n") >= 1:
                med, n = ls["median"], ls["n"]
            out.append((when, ru, med, n))
    out.sort(key=lambda t: t[0])
    return out


def _fmt(rec):
    _, ru, med, n = rec
    return "run_utc=%s median=%s n=%s" % (ru, "NA" if med is None else med, "NA" if n is None else n)


def b1(records, registration):
    per_day = {}
    for r in records:  # records are sorted by run_utc; the last one per UTC date wins
        if r[0] < registration:
            per_day[r[0].date()] = r
    before = [per_day[d] for d in sorted(per_day)][-2:]
    used = "; ".join(_fmt(r) for r in before) or "none"
    good = len(before) == 2 and all(r[2] is not None and r[2] >= MEDIAN_BAR_S and r[3] >= B1_MIN_N for r in before)
    return good, used


def b2(records, after):
    prev = None
    seen = 0
    newest = None
    for r in records:
        if r[0] <= after:
            continue
        newest = r[1]
        if r[2] is None:
            continue
        seen += 1
        if r[2] < MEDIAN_BAR_S:
            if prev is not None:
                return True, r[1], "%s; %s" % (_fmt(prev), _fmt(r))
            prev = r
        else:
            prev = None
    return False, None, "newest_run_utc=%s evaluated_records=%d" % (newest, seen)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("b1")
    p1.add_argument("--registration-utc", required=True)
    p1.add_argument("--daily", required=True)
    p2 = sub.add_parser("b2")
    p2.add_argument("--after-run-utc", required=True)
    p2.add_argument("--daily", required=True)
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return EXIT_INPUT if e.code else EXIT_OK
    try:
        if a.cmd == "b1":
            reg = parse_utc(a.registration_utc)
            ok, used = b1(load_records(a.daily), reg)
            print("B1 %s registration_utc=%s bar=median>=%d,n>=%d records: %s"
                  % ("PASS" if ok else "FAIL", a.registration_utc, MEDIAN_BAR_S, B1_MIN_N, used))
            return EXIT_OK if ok else EXIT_FAIL
        after = parse_utc(a.after_run_utc)
        halt, at, used = b2(load_records(a.daily), after)
        if halt:
            print("B2 HALT halt_run_utc=%s after_run_utc=%s bar=median<%d twice consecutively records: %s"
                  % (at, a.after_run_utc, MEDIAN_BAR_S, used))
            return EXIT_FAIL
        print("B2 OK after_run_utc=%s bar=median<%d twice consecutively %s" % (a.after_run_utc, MEDIAN_BAR_S, used))
        return EXIT_OK
    except InputError as e:
        print("INPUT ERROR: %s" % e, file=sys.stderr)
        return EXIT_INPUT


if __name__ == "__main__":
    sys.exit(main())
