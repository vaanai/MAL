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
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
PASS_RULE = "PASS iff md5_equal AND peak_rss_mb < rss_cap_mb AND scan_ms_p95 (max of both runs) < 1000"


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


def one_run(rd: RealDrill, tag: str, start: int, end: int) -> dict:
    run = rd.new_run(tag)
    walls: list[float] = []
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
            "counters": dict(sorted(run.counters.items())), "scans": len(walls), "scan_ms_p50": round(pct(walls, 0.5), 2), "scan_ms_p95": round(pct(walls, 0.95), 2)}


def child_one_run(tag: str, days: list[str], drill_id: str, sha: str) -> int:
    """One replay in ITS OWN process (run1 and run2 never share a heap: QP/OOM #1596). Writes <drill root>/<tag>.json (counts, md5s, this process's peak RSS)."""
    base = Path(H.HV6_ROOT) / "drill" / drill_id
    if not ID_RE.fullmatch(drill_id) or tag not in ("real-a", "real-b") or not D.RR.is_drill_root(base):
        return 2
    start, end = day_ms(days[0]), day_ms(days[-1]) + 86_400_000
    assert end <= day_ms(END_EXCLUSIVE)
    rd = RealDrill(base, Path(REAL_TAPE), sha, days)
    try:
        res = one_run(rd, tag, start, end)
        res["peak_rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)
    except Exception as e:  # noqa: BLE001 - the class name only
        res = {"error_class": type(e).__name__}
    (base / f"{tag}.json").write_text(json.dumps(res, sort_keys=True, separators=(",", ":")) + "\n")
    return 0 if "error_class" not in res else 3


def _isolated_run(tag: str, days: list[str], drill_id: str, sha: str, base: Path) -> dict:
    import subprocess

    root = Path(__file__).resolve().parent.parent
    r = subprocess.run([sys.executable, "-m", "tools.drill_hv6_real_layout", "--one-run", tag, "--days", ",".join(days), "--drill-id", drill_id, "--sha", sha],
                       cwd=str(root), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    f = base / f"{tag}.json"
    if r.returncode != 0 or not f.exists():
        raise RuntimeError(f"child_{tag}_rc{r.returncode}")  # a fixed string: the class and return code only
    res = json.loads(f.read_text())
    if "error_class" in res:
        raise RuntimeError("child_error_" + str(res["error_class"])[:40])
    return res


def run_real(days: list[str], drill_id: str, sha: str, *, rss_cap_mb: float = RSS_CAP_MB, tape_dir: str | None = None, base: Path | None = None, _window: tuple[int, int] | None = None,
             isolate: bool = False) -> dict:
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
           "chain": "fake_stamped_before_scan",
           "rows_by_program": count_programs(src, days), "orders_only_on": "pumpswap", "pass_rule": PASS_RULE, "rss_cap_mb": rss_cap_mb})
    if isolate:  # production: each replay in its own subprocess; the peak is the larger of the two processes' peaks
        a = _isolated_run("real-a", days, drill_id, sha, base)
        b = _isolated_run("real-b", days, drill_id, sha, base)
        rss_mb = max(a.pop("peak_rss_mb"), b.pop("peak_rss_mb"))
    else:
        a = one_run(rd, "real-a", start, end)
        b = one_run(rd, "real-b", start, end)
        rss_mb = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)
    equal = a["log_md5"] == b["log_md5"] and a["cards_md5"] == b["cards_md5"] and a["entries"] == b["entries"]
    p95 = max(a["scan_ms_p95"], b["scan_ms_p95"])
    ok = equal and rss_mb < rss_cap_mb and p95 < SCAN_P95_CAP_MS
    res = {"real_layout": "end", "result": "PASS" if ok else "FAIL", "pass_rule": PASS_RULE, "md5_equal": equal, "rss_ok": rss_mb < rss_cap_mb, "scan_p95_ok": p95 < SCAN_P95_CAP_MS,
           "run1_log_md5": a["log_md5"], "run2_log_md5": b["log_md5"], "run1": {k: v for k, v in a.items() if k != "log_md5"},
           "run2_counts_equal": {k: a[k] == b[k] for k in ("cards", "entries", "closes", "refusals_by_class")}, "peak_rss_mb": rss_mb, "rss_cap_mb": rss_cap_mb}
    D.out(res)
    # Seal: the result file goes into the drill root, then the whole root is made read-only (priced pre-clock hours, never read) and the file's sha256 is printed.
    rf = base / "real-layout-result.json"
    rf.write_text(json.dumps(res, sort_keys=True, separators=(",", ":")) + "\n")
    digest = hashlib.sha256(rf.read_bytes()).hexdigest()
    seal_read_only(base)
    D.out({"real_layout": "sealed", "root_read_only": True, "result_file": "real-layout-result.json", "result_sha256": digest})
    return res


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
    ap.add_argument("--one-run", default=None, help="internal: run one replay (real-a or real-b) in this process and write its result file")
    a = ap.parse_args(argv)
    try:
        days = parse_days(a.days)
        if a.one_run:
            return child_one_run(a.one_run, days, a.drill_id, a.sha)
        res = run_real(days, a.drill_id, a.sha, rss_cap_mb=a.rss_cap_mb, isolate=True)
    except Refused as e:
        print(json.dumps({"real_layout": "refused", "why": str(e)}), file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001 - the class name only: a traceback or message could echo a row value
        print(json.dumps({"real_layout": "error", "class": type(e).__name__}), file=sys.stderr)
        return 3
    return 0 if res["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
