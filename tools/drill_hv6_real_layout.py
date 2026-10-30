#!/usr/bin/env python3
"""HV6 real-layout replay (QP comment 6402447648 (b)): the runner over the hour files of /data/mal/arena/tape for whole closed UTC days, twice.

PAPER only. StubModel (no API), a fake chain stamped before each scan (no RPC). Read-only on the tape; writes only under <HV6_ROOT>/drill/<drill-id>/.
The day list is an argument and is written down by the manager first (ledger check recorded in the PR). Refused: any day outside [2026-10-20, 2026-10-25)
(so 2026-10-19 and every hour at or after 2026-10-25T00Z), a non-contiguous list, any tape directory other than /data/mal/arena/tape, a drill id with a path part.

The runner's tape reader is filtered to the chosen days' hour files, so other hours are listed by name (a directory glob) but never opened. Pools created
before the first chosen day are unknown to the replay (structure test, not a trading test).

PRINTS ONLY: both decision-log md5s and whether they are equal, counts (cards, refusals by class, entries per arm, closes), peak RSS, scan wall-time p50/p95.
Never a pool, mint, signature, price, P&L or per-pool row. The engine ledger sits in the drill root and is never read by a person. Pre-clock structure, not evidence.
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import json
import re
import hashlib
import os
import resource
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools import hv6_agents as A
from tools import hv6_cards as H
from tools import drill_hv6_item6 as D
from tools.arena_zst import open_zst

REAL_TAPE = H.TAPE_ROOT
FIRST_DAY = "2026-10-20"
END_EXCLUSIVE = "2026-10-25"  # no hour at or after 2026-10-25T00Z, ever (G6)
DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
HOUR_RE = re.compile(r"tape-(\d{4}-\d{2}-\d{2})T(\d{2})\.jsonl(\.zst)?$")


class Refused(Exception):
    pass


def parse_days(arg: str) -> list[str]:
    days = [d.strip() for d in arg.split(",") if d.strip()]
    if not days or len(set(days)) != len(days):
        raise Refused("days_empty_or_duplicate")
    for d in days:
        if not DAY_RE.fullmatch(d):
            raise Refused("day_format")
        if not (FIRST_DAY <= d < END_EXCLUSIVE):
            raise Refused("day_outside_window")
    dts = [datetime.strptime(d, "%Y-%m-%d") for d in days]
    if dts != sorted(dts) or any((b - a) != timedelta(days=1) for a, b in zip(dts, dts[1:])):
        raise Refused("days_not_contiguous_ascending")
    return days


def day_ms(day: str) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def hour_scan(src: Path, days: list[str]) -> dict:
    """Names only (a directory listing): which hour files of the chosen days exist. No file is opened."""
    want, present = set(days), set()
    for p in sorted(src.iterdir()):
        m = HOUR_RE.fullmatch(p.name)
        if m and m.group(1) in want:
            key = f"{m.group(1)}T{m.group(2)}"
            if key >= END_EXCLUSIVE:
                raise Refused("hour_at_or_after_cutoff")
            present.add(key)
    return {"hours_present": len(present), "hours_expected": 24 * len(days), "hours_missing": 24 * len(days) - len(present)}


PROG_RE = re.compile(rb'"prog"\s*:\s*"([A-Za-z0-9_-]{1,32})"')
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,80}")
RSS_CAP_MB = 12_288.0
SCAN_P95_CAP_MS = 1000.0
SCAN_P95_TICK_TRIGGER_MS = 500.0   # fixed 2026-10-26, before any outcome at the merged sha: at or above this scan p95 the :02 whole-tick p95 must also be < TICK_P95_CAP_MS
TICK_P95_CAP_MS = 1000.0
RUNS = ("real-a", "real-b", "real-c")  # real-c runs fast_scan=False (the stream path): its decision-log and cards md5 must equal real-a's; its timing is printed, never tested
PASS_RULE = ("PASS iff md5_equal (run1 == run2, and run3 [fast_scan=False] decision-log md5 and cards md5 == run1's) AND peak_rss_mb (max of the three processes) < rss_cap_mb "
             "AND scan_ms_p95 (max of runs 1 and 2; run 3 excluded) < 1000 AND (if that scan p95 >= 500: the :02 whole-tick p95 (max of runs 1 and 2) < 1000; "
             "otherwise the whole-tick figures are report-only) AND (only when --expect-log-md5 / --expect-cards-md5 are given) run1's log md5 and cards md5 equal them")

# Drill-only equivalence switches for the HV6 fixes A/B/C sha (2026-10-30). --legacy-constants on puts back, in the replay process only, the two values fixes A
# and B changed on purpose (entry/re-check max_tokens 400/120 and BOARD_SIZE 24, as at 8cd8fe54): the replay must then reproduce the 8cd8fe54 baseline md5s
# (#1683, filter off: log ac767dbdd6ed48eb0871f6c3902fe5b5, cards 120dbe2aba1c4e66e44f5825b2df589a), which shows every other code change is decision-neutral on
# the real layout. --concurrent on runs the entry calls through the live concurrent path (fix B) in the replay. Production never sets either (the runner job
# and the smoke do not call this tool).
LEGACY_MAX_TOKENS = {"entry": 400, "recheck": 120}
LEGACY_BOARD_SIZE = 24


@contextmanager
def legacy_constants(on: bool):
    if not on:
        yield
        return
    saved_tokens, saved_board = dict(A.MAX_TOKENS), H.BOARD_SIZE
    A.MAX_TOKENS.update(LEGACY_MAX_TOKENS)
    H.BOARD_SIZE = LEGACY_BOARD_SIZE
    try:
        yield
    finally:
        A.MAX_TOKENS.clear()
        A.MAX_TOKENS.update(saved_tokens)
        H.BOARD_SIZE = saved_board


def count_programs(src: Path, days: list[str]) -> dict:
    """Row counts by `prog` per chosen day (counts only). Opens exactly the chosen days' hour files, like the reader (the .jsonl if it exists, else the .zst)."""
    want = set(days)
    files: dict[str, dict[str, Path]] = {}
    for p in sorted(src.iterdir()):
        m = HOUR_RE.fullmatch(p.name)
        if m and m.group(1) in want:
            files.setdefault(f"{m.group(1)}T{m.group(2)}", {})["zst" if m.group(3) else "jsonl"] = p
    out: dict[str, dict[str, int]] = {d: {} for d in days}
    for key in sorted(files):
        f = files[key].get("jsonl") or files[key]["zst"]
        counts = out[key[:10]]
        if f.name.endswith(".zst"):
            r = open_zst(str(f))
            try:
                carry = b""
                while True:
                    b = r.read(1 << 22)
                    if not b:
                        break
                    data = carry + b
                    cut = data.rfind(b"\n")
                    carry, data = (data[cut + 1:], data[:cut]) if cut >= 0 else (data, b"")
                    for m in PROG_RE.finditer(data):
                        k = m.group(1).decode()
                        counts[k] = counts.get(k, 0) + 1
                for m in PROG_RE.finditer(carry):
                    k = m.group(1).decode()
                    counts[k] = counts.get(k, 0) + 1
            finally:
                r.close()
        else:
            with open(f, "rb") as fh:
                for line in fh:
                    for m in PROG_RE.finditer(line):
                        k = m.group(1).decode()
                        counts[k] = counts.get(k, 0) + 1
    return {d: dict(sorted(c.items())) for d, c in out.items()}


def pct(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * len(s)))]


class RealDrill(D.Drill):
    """The runner's ArenaState reads the real tape directory read-only, but its hour list is filtered to the chosen days: any other hour (10-19, anything at or
    after 2026-10-25T00Z) is listed by name only (a glob) and never opened. (A symlink view does not work: the zstd CLI skips symlinks.)"""

    def __init__(self, base: Path, tape_dir: Path, sha: str, days: list[str]) -> None:
        self.sha, self.base, self.tape, self.days = sha, Path(base), Path(tape_dir), frozenset(days)

    def new_run(self, tag, **kw):
        run = super().new_run(tag, **kw)
        orig, days = run.state._hour_files, self.days

        def hour_files():
            return [t for t in orig() if t[0][:10] in days and t[0] < END_EXCLUSIVE]

        run.state._hour_files = hour_files
        return run


def stats(xs: list[float]) -> dict:
    return {"p50": round(pct(xs, 0.5), 2), "p95": round(pct(xs, 0.95), 2), "max": round(max(xs), 2) if xs else 0.0, "n": len(xs)}


def one_run(rd: RealDrill, tag: str, start: int, end: int, fast: bool = True, phantom: bool = True, legacy: bool = False, concurrent: bool = False) -> dict:
    with legacy_constants(legacy):
        res = _one_run(rd, tag, start, end, fast, phantom, concurrent)
    res["legacy_constants"], res["concurrent_calls"] = legacy, concurrent
    return res


def _one_run(rd: RealDrill, tag: str, start: int, end: int, fast: bool, phantom: bool, concurrent: bool) -> dict:
    run = rd.new_run(tag)
    run.cfg.fast_scan = fast
    run.cfg.concurrent_calls = bool(concurrent)  # drill-only: False is the replay default (None resolves to the serial path when not live)
    if not phantom:  # drill-only switch (the runner's hook reads run.phantom on every row); production keeps RunnerConfig.phantom_filter = True
        run.phantom = None
    walls: list[float] = []
    tick_walls: list[float] = []
    scan_tick_walls: list[float] = []   # the :02 tick of each minute: version refresh, scan, watches, re-checks, regime (everything tick() does at the scan second)
    orig_tick = run.tick

    def tick(T):
        t = time.perf_counter()
        try:
            return orig_tick(T)
        finally:
            ms = (time.perf_counter() - t) * 1000.0
            tick_walls.append(ms)
            if (T // 1000) % 60 == H.SCAN_SECOND_MS // 1000:
                scan_tick_walls.append(ms)

    run.tick = tick
    orig = run.scan

    def scan(T):
        t = time.perf_counter()
        try:
            return orig(T)
        finally:
            walls.append((time.perf_counter() - t) * 1000.0)

    run.scan = scan
    run.run(start, end)
    for x in (*D.R.ARM_ORDER, "SCAN"):
        run.log.verify(x)
    pos = D.positions(run)
    return {"log_md5": D.log_md5(run.cfg.root), "cards_md5": D.cards_md5(run.cfg.root), "cards": D.n_cards(run), "entries": D.entries(run),
            "closes": pos["distinct_closed"], "positions": pos["positions"], "open_at_end": pos["open_now"], "refusals_by_class": D.refusal_kinds(run),
            "counters": dict(sorted(run.counters.items())), "scans": len(walls), "scan_ms_p50": round(pct(walls, 0.5), 2), "scan_ms_p95": round(pct(walls, 0.95), 2),
            "tick_ms": stats(tick_walls), "scan_tick_ms": stats(scan_tick_walls), "fast_scan": fast,
            "phantom_filter": phantom, "phantom_drops": dict(run.phantom.counts) if run.phantom is not None else None}


def child_one_run(tag: str, days: list[str], drill_id: str, sha: str, phantom: bool = True, legacy: bool = False, concurrent: bool = False) -> int:
    """One replay in ITS OWN process (run1 and run2 never share a heap: QP/OOM #1596). Writes <drill root>/<tag>.json (counts, md5s, this process's peak RSS)."""
    base = Path(H.HV6_ROOT) / "drill" / drill_id
    if not ID_RE.fullmatch(drill_id) or tag not in RUNS or not D.RR.is_drill_root(base):
        return 2
    start, end = day_ms(days[0]), day_ms(days[-1]) + 86_400_000
    assert end <= day_ms(END_EXCLUSIVE)
    rd = RealDrill(base, Path(REAL_TAPE), sha, days)
    try:
        res = one_run(rd, tag, start, end, fast=(tag != "real-c"), phantom=phantom, legacy=legacy, concurrent=concurrent)
        res["peak_rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)
    except Exception as e:  # noqa: BLE001 - the class name only
        res = {"error_class": type(e).__name__}
    (base / f"{tag}.json").write_text(json.dumps(res, sort_keys=True, separators=(",", ":")) + "\n")
    return 0 if "error_class" not in res else 3


def _isolated_run(tag: str, days: list[str], drill_id: str, sha: str, base: Path, phantom: bool = True, legacy: bool = False, concurrent: bool = False) -> dict:
    import subprocess

    root = Path(__file__).resolve().parent.parent
    r = subprocess.run([sys.executable, "-m", "tools.drill_hv6_real_layout", "--one-run", tag, "--days", ",".join(days), "--drill-id", drill_id, "--sha", sha,
                        "--phantom-filter", "on" if phantom else "off", "--legacy-constants", "on" if legacy else "off",
                        "--concurrent", "on" if concurrent else "off"],
                       cwd=str(root), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    f = base / f"{tag}.json"
    if r.returncode != 0 or not f.exists():
        raise RuntimeError(f"child_{tag}_rc{r.returncode}")  # a fixed string: the class and return code only
    res = json.loads(f.read_text())
    if "error_class" in res:
        raise RuntimeError("child_error_" + str(res["error_class"])[:40])
    return res


def run_real(days: list[str], drill_id: str, sha: str, *, rss_cap_mb: float = RSS_CAP_MB, tape_dir: str | None = None, base: Path | None = None, _window: tuple[int, int] | None = None,
             isolate: bool = False, phantom: bool = True, compare_off: str | None = None, legacy: bool = False, concurrent: bool = False,
             expect_log_md5: str | None = None, expect_cards_md5: str | None = None) -> dict:
    tape_dir = REAL_TAPE if tape_dir is None else tape_dir
    if tape_dir != REAL_TAPE:
        raise Refused("tape_dir_not_allowed")
    if not isinstance(drill_id, str) or not ID_RE.fullmatch(drill_id) or drill_id in (".", ".."):
        raise Refused("drill_id")
    base = Path(base) if base is not None else Path(H.HV6_ROOT) / "drill" / drill_id
    if not D.RR.is_drill_root(base):
        raise Refused("not_a_drill_root")
    H.check(base / "x", "w")
    src = Path(tape_dir)
    view_info = hour_scan(src, days)
    if view_info["hours_present"] == 0:
        raise Refused("no_hour_files_for_the_days")
    start, end = _window or (day_ms(days[0]), day_ms(days[-1]) + 86_400_000)  # _window: tests only
    assert end <= day_ms(END_EXCLUSIVE)
    rd = RealDrill(base, src, sha, days)
    D.out({"real_layout": "start", "sha": sha, "days": len(days), "first_day": days[0], "last_day": days[-1], **view_info, "model": "StubModel", "api_calls": 0,
           "chain": "fake_stamped_before_scan", "legacy_constants": legacy, "concurrent_calls": concurrent,
           "rows_by_program": count_programs(src, days), "orders_only_on": "pumpswap", "pass_rule": PASS_RULE, "rss_cap_mb": rss_cap_mb})
    if isolate:  # production: each replay in its own subprocess; the peak is the largest of the three processes' peaks
        a = _isolated_run("real-a", days, drill_id, sha, base, phantom, legacy, concurrent)
        b = _isolated_run("real-b", days, drill_id, sha, base, phantom, legacy, concurrent)
        c = _isolated_run("real-c", days, drill_id, sha, base, phantom, legacy, concurrent)
        rss_mb = max(a.pop("peak_rss_mb"), b.pop("peak_rss_mb"), c.pop("peak_rss_mb"))
    else:
        a = one_run(rd, "real-a", start, end, phantom=phantom, legacy=legacy, concurrent=concurrent)
        b = one_run(rd, "real-b", start, end, phantom=phantom, legacy=legacy, concurrent=concurrent)
        c = one_run(rd, "real-c", start, end, fast=False, phantom=phantom, legacy=legacy, concurrent=concurrent)
        rss_mb = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)
    equal12 = a["log_md5"] == b["log_md5"] and a["cards_md5"] == b["cards_md5"] and a["entries"] == b["entries"]
    equal3 = c["log_md5"] == a["log_md5"] and c["cards_md5"] == a["cards_md5"]  # fast_scan on and off give the same decisions on the real layout
    equal = equal12 and equal3
    p95 = max(a["scan_ms_p95"], b["scan_ms_p95"])  # run 3 (the slow path) is never part of this test
    tick_p95 = max(a["scan_tick_ms"]["p95"], b["scan_tick_ms"]["p95"])
    tick_rule_applies = p95 >= SCAN_P95_TICK_TRIGGER_MS
    tick_ok = (tick_p95 < TICK_P95_CAP_MS) if tick_rule_applies else True
    ok = equal and rss_mb < rss_cap_mb and p95 < SCAN_P95_CAP_MS and tick_ok
    drops = {k: max(a["phantom_drops"][k], b["phantom_drops"][k]) for k in a["phantom_drops"]} if a.get("phantom_drops") else None
    total_drops = sum(drops.values()) if drops else 0
    expect = None
    if expect_log_md5 is not None or expect_cards_md5 is not None:
        expect = {"expect_log_md5": expect_log_md5, "expect_cards_md5": expect_cards_md5,
                  "log_md5_equal": expect_log_md5 is None or a["log_md5"] == expect_log_md5,
                  "cards_md5_equal": expect_cards_md5 is None or a["cards_md5"] == expect_cards_md5}
        expect["ok"] = expect["log_md5_equal"] and expect["cards_md5_equal"]
        ok = ok and expect["ok"]
    cmp_res = None
    if compare_off is not None:
        cmp_res = compare_with_off(compare_off, a, rss_mb, total_drops)
        ok = ok and cmp_res["compare_ok"]
    res = {"real_layout": "end", "result": "PASS" if ok else "FAIL", "pass_rule": PASS_RULE, "md5_equal": equal, "rss_ok": rss_mb < rss_cap_mb, "scan_p95_ok": p95 < SCAN_P95_CAP_MS,
           "md5_equal_run1_run2": equal12, "md5_equal_run3_fast_scan_off": equal3, "tick_rule_applies": tick_rule_applies, "scan_tick_p95_ok": tick_ok,
           "run1_log_md5": a["log_md5"], "run2_log_md5": b["log_md5"], "run3_log_md5": c["log_md5"], "run3_cards_md5": c["cards_md5"],
           "run3_timing_report_only": {"scan_ms_p50": c["scan_ms_p50"], "scan_ms_p95": c["scan_ms_p95"], "tick_ms": c["tick_ms"], "scan_tick_ms": c["scan_tick_ms"]},
           "whole_tick_ms": {"run1": {"tick": a["tick_ms"], "scan_tick": a["scan_tick_ms"]}, "run2": {"tick": b["tick_ms"], "scan_tick": b["scan_tick_ms"]}},
           "run1": {k: v for k, v in a.items() if k != "log_md5"},
           "run2_counts_equal": {k: a[k] == b[k] for k in ("cards", "entries", "closes", "refusals_by_class")}, "peak_rss_mb": rss_mb, "rss_cap_mb": rss_cap_mb,
           # QP 930: the watch-set cap (WATCH_MAX_POOLS) is a changed value unless it never bound on these days
           "max_candidates_per_scan": max(a["counters"].get("max_candidates_per_scan", 0), b["counters"].get("max_candidates_per_scan", 0)),
           "scans_cap_bound": max(a["counters"].get("scans_cap_bound", 0), b["counters"].get("scans_cap_bound", 0)),
           "phantom_filter": "on" if phantom else "off", "phantom_drops_by_kind": drops, "phantom_drops_total": total_drops,
           "run1_cards_md5": a["cards_md5"], "compare_with_off": cmp_res, "legacy_constants": legacy, "concurrent_calls": concurrent, "expect_md5": expect}
    D.out(res)
    # Seal: the result file goes into the drill root, then the whole root is made read-only (priced pre-clock hours, never read) and the file's sha256 is printed.
    rf = base / "real-layout-result.json"
    rf.write_text(json.dumps(res, sort_keys=True, separators=(",", ":")) + "\n")
    digest = hashlib.sha256(rf.read_bytes()).hexdigest()
    seal_read_only(base)
    D.out({"real_layout": "sealed", "root_read_only": True, "result_file": "real-layout-result.json", "result_sha256": digest})
    return res


def compare_with_off(off_id: str, a: dict, rss_mb: float, total_drops: int) -> dict:
    """Filter-ON run vs the sealed result of an earlier filter-OFF run of the same days (counts only). FAIL if the decision-log md5 differs while the total drop
    count is 0 (a filter that drops nothing must decide as the off run). Differences are on minus off."""
    if not ID_RE.fullmatch(off_id) or off_id in (".", ".."):
        raise Refused("compare_off_id")
    f = Path(H.HV6_ROOT) / "drill" / off_id / "real-layout-result.json"
    off = json.loads(f.read_text())
    if off.get("phantom_filter") != "off":
        raise Refused("compare_off_not_an_off_run")
    r1 = off["run1"]
    md5_same = off["run1_log_md5"] == a["log_md5"]
    out = {"off_drill_id": off_id, "log_md5_equal_to_off": md5_same, "cards_md5_equal_to_off": off.get("run1_cards_md5") == a["cards_md5"],
           "entries_equal_to_off": r1["entries"] == a["entries"], "entries_on": a["entries"], "entries_off": r1["entries"],
           "cards_diff": a["cards"] - r1["cards"], "closes_diff": a["closes"] - r1["closes"], "positions_diff": a["positions"] - r1["positions"],
           "entries_diff": {k: a["entries"].get(k, 0) - r1["entries"].get(k, 0) for k in sorted(set(a["entries"]) | set(r1["entries"]))} if isinstance(a["entries"], dict) else None,
           "peak_rss_mb_diff": round(rss_mb - off["peak_rss_mb"], 1), "total_drops": total_drops,
           "differences_explained_by_drops": md5_same or total_drops > 0}
    out["compare_ok"] = md5_same or total_drops > 0   # md5 differs with zero drops -> FAIL
    return out


def seal_read_only(base: Path) -> None:
    for dp, dns, fns in os.walk(base):
        for n in (*dns, *fns):
            q = os.path.join(dp, n)
            if not os.path.islink(q):
                os.chmod(q, os.stat(q).st_mode & ~0o222)
    os.chmod(base, os.stat(base).st_mode & ~0o222)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="HV6 real-layout replay: StubModel, fake chain, read-only on /data/mal/arena/tape. Prints md5 and counts only.")
    ap.add_argument("--days", required=True, help="comma list of contiguous UTC days in [2026-10-20, 2026-10-25), ledger-confirmed free by the manager")
    ap.add_argument("--drill-id", required=True)
    ap.add_argument("--sha", required=True)
    ap.add_argument("--rss-cap-mb", type=float, default=RSS_CAP_MB)
    ap.add_argument("--phantom-filter", choices=("on", "off"), default="on", help="drill-only: off turns the HV6 phantom-row filter off in the replay (equivalence proof against aa270efc)")
    ap.add_argument("--compare-off", default=None, metavar="DRILL_ID", help="with --phantom-filter on: the drill id of an earlier OFF run of the same days; prints on-vs-off counts, FAIL if md5 differs with 0 drops")
    ap.add_argument("--legacy-constants", choices=("on", "off"), default="off", help="drill-only: entry/re-check max_tokens 400/120 and board 24 (the 8cd8fe54 values) in the replay")
    ap.add_argument("--concurrent", choices=("on", "off"), default="off", help="drill-only: the entry calls take the live concurrent path (fix B) in the replay")
    ap.add_argument("--expect-log-md5", default=None, help="PASS also needs run1's decision-log md5 to equal this (an equivalence proof)")
    ap.add_argument("--expect-cards-md5", default=None, help="PASS also needs run1's cards md5 to equal this")
    ap.add_argument("--one-run", default=None, help="internal: run one replay (real-a or real-b) in this process and write its result file")
    a = ap.parse_args(argv)
    try:
        days = parse_days(a.days)
        for v in (a.expect_log_md5, a.expect_cards_md5):
            if v is not None and not re.fullmatch(r"[0-9a-f]{32}", v):
                raise Refused("expect_md5_not_32_hex")
        if a.one_run:
            return child_one_run(a.one_run, days, a.drill_id, a.sha, a.phantom_filter == "on", a.legacy_constants == "on", a.concurrent == "on")
        res = run_real(days, a.drill_id, a.sha, rss_cap_mb=a.rss_cap_mb, isolate=True, phantom=a.phantom_filter == "on", compare_off=a.compare_off,
                       legacy=a.legacy_constants == "on", concurrent=a.concurrent == "on", expect_log_md5=a.expect_log_md5, expect_cards_md5=a.expect_cards_md5)
    except Refused as e:
        print(json.dumps({"real_layout": "refused", "why": str(e)}), file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001 - the class name only: a traceback or message could echo a row value
        print(json.dumps({"real_layout": "error", "class": type(e).__name__}), file=sys.stderr)
        return 3
    return 0 if res["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
