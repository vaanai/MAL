#!/usr/bin/env python3
"""Counts-only judge of the s1t early smoke (DEC-031 Amendment 24 items 6 and 7, QP-NOTE-1102-tip-readers). Paper only. Reads files only; sends nothing.

The smoke is tools/h5_shadow_tip.py (S*', commit CODE_SHA) run with --smoke: S*'s H5 Look-2 seal holds on every pool, so its trigger and pool records are
flagged `h5_look2_sealed` and no outcome, outcome_bx10 or strip record exists. QP's binding rule (Amendment 24 item 6): before s1t's first read, NO count
of the smoke's trigger, pool or decision records is printed or notebooked as a number. This tool prints flags (PASS|FAIL, yes|no), the trigger-age p50,
p90 and max in ms, the side feed's counters, and report-only lines of tape row counts, dropped-row counts by code and the RSS peak. Never a trigger,
pool or decision count.

READ SET (exact; every other field of every record is left unread):
  Files: `<out-dir>/h5-shadow-????-??-??T??.jsonl` (not h5-shadow-status.json, not h5-shadow-errors.log), `<rep-dir>/process.log` (a byte search for
  'Traceback'), `<rep-dir>/rss.tsv` (epoch_s TAB rss_bytes TAB pid), and the --exit-code argument.
  Byte tests only (no JSON parse): the type of every line (a regex on its first key); on `start` lines, the fixed substrings START_PINS and the endpoint
  pin; on `excluded` lines, `"reason":"unclassified"`; `outcome`, `outcome_bx10` and `strip` lines are never parsed (their presence fails SEAL_HELD);
  `tip_input_halted` by its type bytes.
  Parsed: `tip_input_start` (`smoke`); `tip_input_stats` and `tip_input_stop` (`t_ms`, `driver.rows_bad_*`, `source.rows`, `source.reader_loss_bad_lines`,
  `source.reader_loss_zst_errors`, `source.reader_loss_apply_errors`); `trigger` (`t_detect_ms`, `block_time`, `h5_look2_sealed`); `hb` and `stop`
  (`syn_class.ws_restarts`, `syn_class.ws_feed_errors`, `syn_class.ws_decode_errors`).

FLAGS (one line each, `S1T_SMOKE_<NAME> PASS|FAIL`):
  A_STARTED         every `start` record carries the pins (code_sha, dirty false, fresh_forward ARENA, q_star_sol 50.0, end_only_ms, and the endpoints list
                    equal to --ws-endpoint, which must itself equal the pinned WS_ENDPOINT), there is at least one, and a `tip_input_start` exists.
  B_DECISION_ROWS   at least one trigger record, or tip_input_stats/stop records present through the run (no gap above --stats-gap-max-s).
  C_NO_CRASH        --exit-code is 0, process.log holds no 'Traceback', there is no second `start` or `tip_input_start` record, and the pid in
                    rss.tsv never changes (a restart). Booleans only.
  D_MEMORY          Am.6 item 1.3(d): R0 + slope x --plan-hours <= --cap-mib. R0 is the first rss sample at or after WARM_S (1800 s) past the first sample,
                    the slope is the least-squares fit over those samples, fewer than MIN_FIT_SAMPLES (10) fails, and no sample may exceed the cap.
  DURATION          the first and last tip_input_stats/stop records are >= --min-hours apart (default 24).
  SEAL_HELD         no outcome/outcome_bx10/strip line, no tip_input_halted, the tip_input_start says smoke true, and every trigger record (also one that
                    does not parse) is flagged h5_look2_sealed true.
  AGE_BAR           share of triggers with t_detect_ms - block_time*1000 > --age-limit-ms is <= --age-share-max. A trigger whose age cannot be measured
                    counts as over the limit; no triggers fails. Prints `S1T_SMOKE_AGE_MS p50=<int> p90=<int> max=<int>` (nearest rank; NA when none).
INFORMATION (not in the verdict):
  S1T_SMOKE_CLASS_UNKNOWN yes|no   yes if any line is an `excluded` record with reason `unclassified` (S*'s _exclude; a trigger is only written for a known
                    class, so an unknown one shows only as `excluded`).
  S1T_SMOKE_SIDE_FEED reconnects=<int> feed_errors=<int> decode_errors=<int>   the classifier's ws_restarts, ws_feed_errors and ws_decode_errors, from the
                    last hb/stop record carrying `syn_class`; `unavailable` when no record does.
  S1T_SMOKE_REPORT dropped_rows <code>=<int> ...   Am.23 item 8, report-only: the tape reader's loss codes and the driver's rows_bad_<code> counters from
                    the last stats/stop record.
  S1T_SMOKE_REPORT rows_per_min=<x>   tape rows read (source.rows) per minute between the first and last stats record. It is a row count of the tape,
                    not a trigger, pool or decision count; the driver's per-type counters (rows_pool_create and the like) are never read.
  S1T_SMOKE_REPORT rss_peak_mib=<x>   the largest rss.tsv sample (anon only if the sampler wrote RssAnon).
Then `S1T_SMOKE_VERDICT PASS|FAIL`. Exit 0 PASS, 1 FAIL, 2 usage.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Sequence

MIB = 1024 * 1024
CODE_SHA = "afa4971d9dd8b0bca2e73a758c22f4539f675d63"  # S*' (tag q50-succ-tip-1): code_sha of the smoke's start record
WS_ENDPOINT = "wss://rpc.solanatracker.io/public"  # the class side feed's pinned endpoint (the start record's `endpoints`)
START_PINS = tuple(s.encode() for s in (  # S*'s start record, serialized as h5_shadow does: json.dumps(separators=(",", ":"))
    f'"code_sha":"{CODE_SHA}"', '"dirty":false', '"fresh_forward":"ARENA"', '"q_star_sol":50.0', '"end_only_ms":1796083200000'))
WARM_S = 1800  # R0 and the slope start 30 min after the first rss sample (the HV4 / s1 smoke rule, tools/q50s1_health.py)
MIN_FIT_SAMPLES = 10
LOSS_CODES = ("bad_lines", "zst_errors", "apply_errors")  # source.reader_loss_<code>
BAD_CODES = ("trade_fields", "trade_venue", "pool_create_fields")  # driver.rows_bad_<code>, always printed
SEALED_TYPES = frozenset({"outcome", "outcome_bx10", "strip"})
_HEAD = re.compile(rb'\A\s*\{\s*"type"\s*:\s*"([A-Za-z0-9_]{1,40})"')
_SEALED_ANY = re.compile(rb'"type"\s*:\s*"(?:outcome|outcome_bx10|strip)"')
_UNCLASSIFIED = re.compile(rb'"reason"\s*:\s*"unclassified"')
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


def _int_map(d: Any, keys: Sequence[str] | None = None, prefix: str = "") -> dict[str, int]:
    """Integer counters of `d` named `prefix`+key (all keys with the prefix when `keys` is None)."""
    if not isinstance(d, dict):
        return {}
    names = [prefix + k for k in keys] if keys is not None else [k for k in d if isinstance(k, str) and k.startswith(prefix)]
    return {k[len(prefix):]: int(_num(d.get(k)) or 0) for k in names if k in d}


def read_out_dir(out_dir: Path, ws_endpoint: str = WS_ENDPOINT) -> dict:
    """One pass over the hourly files. Returns facts, never counts for output: the counters here stay inside the judge."""
    pins = START_PINS + (f'"endpoints":["{ws_endpoint}"]'.encode(),)
    f: dict[str, Any] = {"n_start": 0, "start_pinned": True, "n_tip_start": 0, "smoke_ok": True, "sealed_line": False, "halted": False,
                         "triggers_unsealed": False, "ages": [], "unmeasurable": 0, "n_trig": 0, "stats": [], "class_unknown": False, "side": None,
                         "last_stats": None}
    for path in sorted(out_dir.glob("h5-shadow-????-??-??T??.jsonl")):
        with open(path, "rb") as fh:
            for line in fh:
                kind = classify(line)
                if kind == "sealed":
                    f["sealed_line"] = True  # never parsed
                elif kind == "start":
                    f["n_start"] += 1
                    if not all(p in line for p in pins):
                        f["start_pinned"] = False
                elif kind == "excluded":
                    if _UNCLASSIFIED.search(line):
                        f["class_unknown"] = True
                elif kind == "tip_input_halted":
                    f["halted"] = True
                elif kind == "tip_input_start":
                    f["n_tip_start"] += 1
                    rec = _parse(line)
                    if rec is None or rec.get("smoke") is not True:
                        f["smoke_ok"] = False
                elif kind in ("tip_input_stats", "tip_input_stop"):
                    rec = _parse(line)
                    t = _num(rec.get("t_ms")) if rec else None
                    if t is not None:
                        f["stats"].append(t)
                        if f["last_stats"] is None or t >= f["last_stats"][0]:
                            src, drv = rec.get("source"), rec.get("driver")
                            f["last_stats"] = (t, _num(src.get("rows")) if isinstance(src, dict) else None,
                                               {**_int_map(src, LOSS_CODES, "reader_loss_"), **_int_map(drv, None, "rows_bad_")})
                elif kind == "trigger":
                    f["n_trig"] += 1
                    rec = _parse(line)
                    if rec is None:
                        f["triggers_unsealed"] = True
                        f["unmeasurable"] += 1
                        continue
                    if rec.get("h5_look2_sealed") is not True:
                        f["triggers_unsealed"] = True
                    td, bt = _num(rec.get("t_detect_ms")), _num(rec.get("block_time"))
                    if td is None or bt is None:
                        f["unmeasurable"] += 1
                    else:
                        f["ages"].append(int(round(td - bt * 1000)))
                elif kind in ("hb", "stop"):
                    rec = _parse(line)
                    clf = rec.get("syn_class") if rec else None
                    if isinstance(clf, dict):
                        f["side"] = tuple(int(_num(clf.get(k)) or 0) for k in ("ws_restarts", "ws_feed_errors", "ws_decode_errors"))
    return f


def nearest_rank(sorted_vals: Sequence[int], q: float) -> int:
    return sorted_vals[max(1, math.ceil(q * len(sorted_vals))) - 1]


def read_rss(path: Path) -> list[tuple[float, float | None, str]]:
    """(epoch_s, rss_bytes or None for '-', pid text) sorted by time; malformed lines are skipped."""
    rows: list[tuple[float, float | None, str]] = []
    if not path.is_file():
        return rows
    for raw in path.read_text(errors="replace").splitlines():
        parts = raw.split("\t")
        if len(parts) < 2:
            continue
        try:
            rows.append((float(parts[0]), None if parts[1].strip() == "-" else float(parts[1]), parts[2].strip() if len(parts) > 2 else ""))
        except ValueError:
            continue
    return sorted(rows, key=lambda r: r[0])


def pid_changed(rows: Sequence[tuple[float, float | None, str]]) -> bool:
    pids = [r[2] for r in rows]
    return any(a != b for a, b in zip(pids, pids[1:]))


def ols(points: Sequence[tuple[float, float]]) -> float | None:
    """Least-squares slope (y per x unit), None when the x values do not vary."""
    tm, rm = sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points)
    den = sum((t - tm) ** 2 for t, _ in points)
    return None if den <= 0 else sum((t - tm) * (r - rm) for t, r in points) / den


def memory_ok(rows: Sequence[tuple[float, float | None, str]], cap_mib: float, plan_hours: float) -> bool:
    pts = [(t, r) for t, r, _ in rows if r is not None]
    if not pts:
        return False
    cap = cap_mib * MIB
    fit = [(t, r) for t, r in pts if t >= pts[0][0] + WARM_S]
    if len(fit) < MIN_FIT_SAMPLES:  # a metric that cannot be computed fails
        return False
    slope = ols(fit)
    return slope is not None and fit[0][1] + slope * plan_hours * 3600 <= cap and max(r for _, r in pts) <= cap


def judge(out_dir: Path, rep_dir: Path, exit_code: int, cap_mib: float, plan_hours: float, age_limit_ms: float = 3500, age_share_max: float = 0.025,
          min_hours: float = 24, stats_gap_max_s: float = 600, ws_endpoint: str = WS_ENDPOINT) -> dict:
    f = read_out_dir(out_dir, ws_endpoint)
    log = rep_dir / "process.log"
    traceback = True  # an unreadable log cannot show a clean run
    if log.is_file():
        with open(log, "rb") as fh:
            traceback = any(b"Traceback" in ln for ln in fh)
    rss = read_rss(rep_dir / "rss.tsv")
    ts = sorted(f["stats"])
    span_h = (ts[-1] - ts[0]) / 3.6e6 if len(ts) >= 2 else 0.0
    through = len(ts) >= 2 and max(b - a for a, b in zip(ts, ts[1:])) <= stats_gap_max_s * 1000
    ages = sorted(f["ages"])
    over = f["unmeasurable"] + sum(1 for a in ages if a > age_limit_ms)
    restarted = f["n_start"] > 1 or f["n_tip_start"] > 1 or pid_changed(rss)
    flags = {
        "A_STARTED": f["n_start"] >= 1 and f["start_pinned"] and f["n_tip_start"] >= 1,
        "B_DECISION_ROWS": f["n_trig"] > 0 or through,
        "C_NO_CRASH": exit_code == 0 and not traceback and not restarted,
        "D_MEMORY": memory_ok(rss, cap_mib, plan_hours),
        "DURATION": span_h >= min_hours,
        "SEAL_HELD": not (f["sealed_line"] or f["halted"] or f["triggers_unsealed"] or not f["smoke_ok"]),
        "AGE_BAR": f["n_trig"] > 0 and over / f["n_trig"] <= age_share_max,
    }
    last = f["last_stats"]
    rows_pm = None if last is None or last[1] is None or span_h <= 0 else last[1] / (span_h * 60)
    peak = [r for _, r, _ in rss if r is not None]
    return {"flags": flags, "verdict": all(flags.values()),
            "age": (nearest_rank(ages, 0.5), nearest_rank(ages, 0.9), ages[-1]) if ages else None,
            "class_unknown": f["class_unknown"], "side": f["side"],
            "dropped": None if last is None else {**{c: 0 for c in LOSS_CODES + BAD_CODES}, **last[2]},
            "rows_per_min": rows_pm, "rss_peak_mib": max(peak) / MIB if peak else None}


def report_lines(res: dict) -> list[str]:
    pf = lambda ok: "PASS" if ok else "FAIL"  # noqa: E731
    out = []
    for name in FLAGS:
        out.append(f"S1T_SMOKE_{name} {pf(res['flags'][name])}")
        if name == "AGE_BAR":
            a = res["age"]
            out.append("S1T_SMOKE_AGE_MS p50=NA p90=NA max=NA" if a is None else f"S1T_SMOKE_AGE_MS p50={a[0]} p90={a[1]} max={a[2]}")
    out.append(f"S1T_SMOKE_CLASS_UNKNOWN {'yes' if res['class_unknown'] else 'no'}")
    s = res["side"]
    out.append("S1T_SMOKE_SIDE_FEED unavailable" if s is None else f"S1T_SMOKE_SIDE_FEED reconnects={s[0]} feed_errors={s[1]} decode_errors={s[2]}")
    d = res["dropped"]
    out.append("S1T_SMOKE_REPORT dropped_rows unavailable" if d is None else "S1T_SMOKE_REPORT dropped_rows " + " ".join(f"{k}={v}" for k, v in sorted(d.items())))
    out.append("S1T_SMOKE_REPORT rows_per_min=" + ("unavailable" if res["rows_per_min"] is None else f"{res['rows_per_min']:.1f}"))
    out.append("S1T_SMOKE_REPORT rss_peak_mib=" + ("unavailable" if res["rss_peak_mib"] is None else f"{res['rss_peak_mib']:.1f}"))
    out.append(f"S1T_SMOKE_VERDICT {pf(res['verdict'])}")
    return out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Counts-only judge of the s1t smoke (DEC-031 Amendment 24 items 6-7). Prints flags, age ms and report-only lines.")
    ap.add_argument("--out-dir", required=True, type=Path, help="the smoke's scratch out dir (h5-shadow-<UTC hour>.jsonl)")
    ap.add_argument("--rep-dir", required=True, type=Path, help="holds process.log and rss.tsv (epoch_s TAB rss_bytes TAB pid)")
    ap.add_argument("--exit-code", required=True, type=int)
    ap.add_argument("--ws-endpoint", required=True, help=f"the class side feed endpoint; must equal the pinned {WS_ENDPOINT}")
    ap.add_argument("--plan-hours", required=True, type=float,
                    help="Am.6 item 1.3(d)'s planned window: s1t runs from the shadow start 2026-11-06T23:00Z to SUCC_END 2026-12-01T00:00Z (577 h) plus the "
                         "run-out of the last exits, about 578 h. Pinned in the notebook before the smoke; no default")
    ap.add_argument("--cap-mib", required=True, type=float, help="the shadow scope's MemoryMax in MiB, pinned in the notebook before the smoke; no default")
    ap.add_argument("--age-limit-ms", type=float, default=3500)
    ap.add_argument("--age-share-max", type=float, default=0.025)
    ap.add_argument("--min-hours", type=float, default=24, help="DURATION bar (default 24)")
    ap.add_argument("--stats-gap-max-s", type=float, default=600, help="B_DECISION_ROWS: the largest allowed gap between stats records")
    args = ap.parse_args(argv)  # a usage error exits 2
    if args.ws_endpoint != WS_ENDPOINT:
        print("usage: --ws-endpoint must equal the pinned side feed endpoint", file=sys.stderr)
        return 2
    for d in (args.out_dir, args.rep_dir):
        if not d.is_dir():
            print(f"usage: not a directory: {d}", file=sys.stderr)
            return 2
    res = judge(args.out_dir, args.rep_dir, args.exit_code, args.cap_mib, args.plan_hours, args.age_limit_ms, args.age_share_max, args.min_hours,
                args.stats_gap_max_s, args.ws_endpoint)
    print("\n".join(report_lines(res)))
    return 0 if res["verdict"] else 1


if __name__ == "__main__":
    sys.exit(main())
