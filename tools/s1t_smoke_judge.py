#!/usr/bin/env python3
"""Counts-only judge of the s1t early smoke (DEC-031 Amendment 24 items 6 and 7). Paper only. Reads files only; sends nothing.

The smoke is tools/h5_shadow_tip.py (S*') run with --smoke: S*'s H5 Look-2 seal holds on every pool, so its trigger and pool records are flagged
`h5_look2_sealed` and no outcome, outcome_bx10 or strip record exists. QP's binding rule (Amendment 24 item 6, QP-NOTE-1102-tip-readers): before
s1t's first read, NO count of the smoke's trigger, pool or decision records is printed or notebooked as a number. This tool prints flags
(PASS|FAIL, yes|no), the trigger-age p50, p90 and max in ms, and the side feed's reconnect and gap counts (the only other numbers allowed).

READING RULE. Each line of `<out-dir>/h5-shadow-<UTC hour>.jsonl` gets a BYTE-LEVEL type test first (a regex on the line's first key, no JSON parse).
A line of type outcome, outcome_bx10 or strip is never parsed: its presence alone fails SEAL_HELD. Only these are parsed: tip_input_start,
tip_input_stats, tip_input_stop (their t_ms; the start's `smoke` flag), trigger (`t_detect_ms`, `block_time`, `h5_look2_sealed`, `synthetic`,
`synthetic_src` and nothing else) and hb/stop (`syn_class` only, for the side feed's counters). `start`, `tip_input_halted` and every other type
are seen by their type bytes alone. Nothing is ever counted into the output: the report has no trigger, pool or decision count.

FLAGS (one line each, `S1T_SMOKE_<NAME> PASS|FAIL`):
  A_STARTED         a `start` record and a `tip_input_start` record exist.
  B_DECISION_ROWS   at least one trigger record, or tip_input_stats/stop records present through the run (no gap above --stats-gap-max-s).
  C_NO_CRASH        --exit-code is 0 and process.log holds no 'Traceback'.
  D_MEMORY          least-squares slope of rss over the run x plan hours + the first sample <= the cap; also no sample above the cap. The series
                    of the pid with the most samples is used.
  DURATION          the first and last tip_input_stats/stop records are >= --min-hours apart (default 24).
  SEAL_HELD         no outcome/outcome_bx10/strip line, no tip_input_halted, the tip_input_start says smoke true, and every trigger record
                    (also one that does not parse) is flagged h5_look2_sealed true.
  AGE_BAR           share of triggers with t_detect_ms - block_time*1000 > --age-limit-ms is <= --age-share-max. A trigger whose age cannot be
                    measured (a missing field, an unparseable line) counts as over the limit; no triggers at all fails. Prints
                    `S1T_SMOKE_AGE_MS p50=<int> p90=<int> max=<int>` (nearest rank, over the measurable ages; NA when there are none).
INFORMATION (not in the verdict):
  S1T_SMOKE_CLASS_UNKNOWN yes|no   yes if any trigger's synthetic class (`synthetic` is not false, or `synthetic_src` missing/unknown) is unknown.
  S1T_SMOKE_SIDE_FEED reconnects=<int> gaps=<int>   reconnects = the classifier's ws_restarts, gaps = its ws_feed_errors (restarts that followed an
                    exception), both from the last hb/stop record that carries `syn_class`; `unavailable` when no record does.
Then `S1T_SMOKE_VERDICT PASS|FAIL`. Exit 0 PASS, 1 FAIL, 2 usage.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Sequence

MIB = 1024 * 1024
SEALED_TYPES = frozenset({"outcome", "outcome_bx10", "strip"})
_HEAD = re.compile(rb'\A\s*\{\s*"type"\s*:\s*"([A-Za-z0-9_]{1,40})"')
_SEALED_ANY = re.compile(rb'"type"\s*:\s*"(?:outcome|outcome_bx10|strip)"')
UNKNOWN_SRC = frozenset({"", "unknown", "unclassified", "none"})
FLAGS = ("A_STARTED", "B_DECISION_ROWS", "C_NO_CRASH", "D_MEMORY", "DURATION", "SEAL_HELD", "AGE_BAR")


def classify(line: bytes) -> str:
    """The line's type from its bytes alone. A line whose first key is not `type` is `sealed` when a sealed type appears anywhere in it (conservative)."""
    m = _HEAD.match(line)
    if m is None:
        return "sealed" if _SEALED_ANY.search(line) else "other"
    t = m.group(1).decode("ascii")
    return "sealed" if t in SEALED_TYPES else t


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def _parse(line: bytes) -> dict | None:
    try:
        obj = json.loads(line)
    except (ValueError, UnicodeDecodeError):
        return None
    return obj if isinstance(obj, dict) else None


def read_out_dir(out_dir: Path) -> dict:
    """One pass over the hourly files. Returns facts, never counts for output: the lists here stay inside the judge."""
    f: dict[str, Any] = {"start": False, "tip_start": False, "smoke_ok": True, "sealed_line": False, "halted": False, "triggers_unsealed": False,
                         "ages": [], "unmeasurable": 0, "n_trig": 0, "stats_ts": [], "class_unknown": False, "side": None}
    for path in sorted(out_dir.glob("h5-shadow-????-??-??T??.jsonl")):
        with open(path, "rb") as fh:
            for line in fh:
                kind = classify(line)
                if kind == "sealed":
                    f["sealed_line"] = True  # never parsed
                elif kind == "start":
                    f["start"] = True
                elif kind == "tip_input_halted":
                    f["halted"] = True
                elif kind == "tip_input_start":
                    f["tip_start"] = True
                    rec = _parse(line)
                    if rec is None or rec.get("smoke") is not True:
                        f["smoke_ok"] = False
                elif kind in ("tip_input_stats", "tip_input_stop"):
                    rec = _parse(line)
                    t = _num(rec.get("t_ms")) if rec else None
                    if t is not None:
                        f["stats_ts"].append(t)
                elif kind == "trigger":
                    f["n_trig"] += 1
                    rec = _parse(line)
                    if rec is None:
                        f["triggers_unsealed"] = True
                        f["unmeasurable"] += 1
                        f["class_unknown"] = True
                        continue
                    if rec.get("h5_look2_sealed") is not True:
                        f["triggers_unsealed"] = True
                    td, bt = _num(rec.get("t_detect_ms")), _num(rec.get("block_time"))
                    if td is None or bt is None:
                        f["unmeasurable"] += 1
                    else:
                        f["ages"].append(int(round(td - bt * 1000)))
                    src = rec.get("synthetic_src")
                    if rec.get("synthetic") is not False or not isinstance(src, str) or src.strip().lower() in UNKNOWN_SRC:
                        f["class_unknown"] = True
                elif kind in ("hb", "stop"):
                    rec = _parse(line)
                    clf = rec.get("syn_class") if rec else None
                    if isinstance(clf, dict):
                        f["side"] = (int(_num(clf.get("ws_restarts")) or 0), int(_num(clf.get("ws_feed_errors")) or 0))
    return f


def nearest_rank(sorted_vals: Sequence[int], q: float) -> int:
    return sorted_vals[max(1, math.ceil(q * len(sorted_vals))) - 1]


def read_rss(path: Path) -> list[tuple[float, float]]:
    """(epoch_s, rss_bytes) of the pid with the most samples; malformed lines are skipped."""
    by_pid: dict[str, list[tuple[float, float]]] = collections.defaultdict(list)
    if not path.is_file():
        return []
    for raw in path.read_text(errors="replace").splitlines():
        parts = raw.split("\t")
        if len(parts) < 2:
            continue
        try:
            by_pid[parts[2].strip() if len(parts) > 2 else ""].append((float(parts[0]), float(parts[1])))
        except ValueError:
            continue
    return sorted(max(by_pid.values(), key=len)) if by_pid else []


def memory_ok(samples: list[tuple[float, float]], cap_mib: float, plan_hours: float) -> bool:
    if len(samples) < 2:
        return False
    t0 = samples[0][0]
    ts, rs = [s[0] - t0 for s in samples], [s[1] for s in samples]
    tm, rm = sum(ts) / len(ts), sum(rs) / len(rs)
    den = sum((t - tm) ** 2 for t in ts)
    if den <= 0:
        return False
    slope = sum((t - tm) * (r - rm) for t, r in zip(ts, rs)) / den  # bytes per second
    cap = cap_mib * MIB
    return slope * plan_hours * 3600 + rs[0] <= cap and max(rs) <= cap


def judge(out_dir: Path, rep_dir: Path, exit_code: int, cap_mib: float = 3500, plan_hours: float = 24, age_limit_ms: float = 3500,
          age_share_max: float = 0.025, min_hours: float = 24, stats_gap_max_s: float = 600) -> dict:
    f = read_out_dir(out_dir)
    log = rep_dir / "process.log"
    traceback = True  # an unreadable log cannot show a clean run
    if log.is_file():
        with open(log, "rb") as fh:
            traceback = any(b"Traceback" in ln for ln in fh)
    ts = sorted(f["stats_ts"])
    span_h = (ts[-1] - ts[0]) / 3.6e6 if len(ts) >= 2 else 0.0
    through = len(ts) >= 2 and max(b - a for a, b in zip(ts, ts[1:])) <= stats_gap_max_s * 1000
    ages = sorted(f["ages"])
    over = f["unmeasurable"] + sum(1 for a in ages if a > age_limit_ms)
    flags = {
        "A_STARTED": f["start"] and f["tip_start"],
        "B_DECISION_ROWS": f["n_trig"] > 0 or through,
        "C_NO_CRASH": exit_code == 0 and not traceback,
        "D_MEMORY": memory_ok(read_rss(rep_dir / "rss.tsv"), cap_mib, plan_hours),
        "DURATION": span_h >= min_hours,
        "SEAL_HELD": not (f["sealed_line"] or f["halted"] or f["triggers_unsealed"] or not f["smoke_ok"]),
        "AGE_BAR": f["n_trig"] > 0 and over / f["n_trig"] <= age_share_max,
    }
    return {"flags": flags, "verdict": all(flags.values()),
            "age": (nearest_rank(ages, 0.5), nearest_rank(ages, 0.9), ages[-1]) if ages else None,
            "class_unknown": f["class_unknown"], "side": f["side"]}


def report_lines(res: dict) -> list[str]:
    pf = lambda ok: "PASS" if ok else "FAIL"  # noqa: E731
    out = []
    for name in FLAGS:
        out.append(f"S1T_SMOKE_{name} {pf(res['flags'][name])}")
        if name == "AGE_BAR":
            a = res["age"]
            out.append("S1T_SMOKE_AGE_MS p50=NA p90=NA max=NA" if a is None else f"S1T_SMOKE_AGE_MS p50={a[0]} p90={a[1]} max={a[2]}")
    out.append(f"S1T_SMOKE_CLASS_UNKNOWN {'yes' if res['class_unknown'] else 'no'}")
    out.append("S1T_SMOKE_SIDE_FEED unavailable" if res["side"] is None else f"S1T_SMOKE_SIDE_FEED reconnects={res['side'][0]} gaps={res['side'][1]}")
    out.append(f"S1T_SMOKE_VERDICT {pf(res['verdict'])}")
    return out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Counts-only judge of the s1t smoke (DEC-031 Amendment 24 items 6-7). Prints flags and age ms only.")
    ap.add_argument("--out-dir", required=True, type=Path, help="the smoke's scratch out dir (h5-shadow-<UTC hour>.jsonl)")
    ap.add_argument("--rep-dir", required=True, type=Path, help="holds process.log and rss.tsv (epoch_s TAB rss_bytes TAB pid)")
    ap.add_argument("--exit-code", required=True, type=int)
    ap.add_argument("--cap-mib", type=float, default=3500)
    ap.add_argument("--plan-hours", type=float, default=24)
    ap.add_argument("--age-limit-ms", type=float, default=3500)
    ap.add_argument("--age-share-max", type=float, default=0.025)
    ap.add_argument("--min-hours", type=float, default=24, help="DURATION bar (default 24)")
    ap.add_argument("--stats-gap-max-s", type=float, default=600, help="B_DECISION_ROWS: the largest allowed gap between stats records")
    args = ap.parse_args(argv)  # a usage error exits 2
    for d in (args.out_dir, args.rep_dir):
        if not d.is_dir():
            print(f"usage: not a directory: {d}", file=sys.stderr)
            return 2
    res = judge(args.out_dir, args.rep_dir, args.exit_code, args.cap_mib, args.plan_hours, args.age_limit_ms, args.age_share_max, args.min_hours,
                args.stats_gap_max_s)
    print("\n".join(report_lines(res)))
    return 0 if res["verdict"] else 1


if __name__ == "__main__":
    sys.exit(main())
