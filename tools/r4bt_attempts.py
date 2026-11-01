"""r4bt_attempts: the OUTCOME-BLIND attempt counter of the arena book r4-b-T (`r4bt-a1`; DEC-031 Amendment 25 items 15, 16 and 26, DRAFT).
Prints COUNTS, dates and file hashes only. PAPER: no key, no network, no price.

What it reads, and nothing else
  The r4-b DRY / decision host's hash-chained decision log (`tools.arena_declog` layout): `<declog_root>/decisions/<arm>/<UTC day>.jsonl`, one arm
  (the frozen code's `r4b-exp027`). Rows are parsed with a JSON hook that KEEPS ONLY the keys `kind`, `ms` and `t_mark` of every object, so no other field
  (mint, pool, meta, rank, score, reason, decision_slot, gap spans, ...) is ever materialised by this tool (QP-RULING-1102-r4b-a3-record Addendum 1, A1.2
  item 8: "a pinned counter that reads only entry-row timestamps. Counts only: no guard, fill or exit field"). The hash chain is verified with
  `tools.arena_declog.DecisionLog.verify` (it raises on a broken chain and returns only the head sha and seq; nothing of a row is printed).
  An attempt is an `entry` row (DEC-031 §2: every entry decision, guarded reverts included). Exit, gap and start rows are not counted.

Subcommands
  p0      P0 of DEC-031 Amendment 4 item 2.1 on whole closed UTC days: entry rows per day (by the UTC day of `ms`), the mean rate, the floor
          (mean >= 2.0 a day and no day at 0) and H = min(90, max(14, ceil(150 / rate))) days (item 2.2), computed exactly with integers.
  count   The counted attempts of a registered clock (`ms` >= clock_start_ms, `t_mark` >= the clock in seconds, `ms` < clock + H) per UTC date; attempts
          decided in an Amendment 6 EXCLUDED hour (a pinned hour table, optional) are removed from the count and reported toward the 10% cap; and the
          DEC-031 §2 boundary: the first 00:00Z instant b (clock < b <= clock + H, and b <= as_of) at which at least 100 counted attempts with ms < b lie
          on at least 5 UTC dates. Without a boundary by clock + H the book ENDS NOT_DECIDABLE (Amendment 4 item 2.3).
  day-sha The sha256 of each closed day file of the arm (for §1.2's daily decision-log sha256 notebook entry). Names and hashes only.

Refusals (exit 2): a declog root under /data/mal/r4b (EXP-027's outputs stay closed to r4-b-T until its read, Amendment 25 item 1), under /var/lib/mal/sealed
(the tip tape) or under a tip-tape archive; a P0 day that is not closed at as_of; a bad clock, H or hour table. A broken chain or an unparseable line
exits 3 (the count is not trusted). Nothing here opens a tip file, a tape, a ledger, a fill or a P&L. No book has cleared the promotion gate.
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ARM_DEFAULT = "r4b-exp027"  # tools/r4b_live.BOT_ID at the install sha bc65c088 (the frozen code's arm name; not imported: no tools/r4b_* import here)
KEEP_KEYS = ("kind", "ms", "t_mark")
GATE_ATTEMPTS = 100  # DEC-031 §2
GATE_DATES = 5  # DEC-031 §2
EXCLUDED_CAP = 0.10  # DEC-031 Amendment 6 item 3 (more than 10% -> NOT_DECIDABLE)
P0_FLOOR_MEAN = 2.0  # DEC-031 Amendment 4 item 2.1
H_MIN_DAYS, H_MAX_DAYS, H_NUM = 14, 90, 150  # DEC-031 Amendment 4 item 2.2
DAY_MS = 86_400_000
HOUR_MS = 3_600_000
FORBIDDEN_ROOTS = ("/data/mal/r4b", "/var/lib/mal/sealed")
FORBIDDEN_MARK = "tip-tape-archive"
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HOUR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}$")


class Refused(RuntimeError):
    """A refusal: nothing is counted."""


class Untrusted(RuntimeError):
    """The log cannot be trusted (broken chain, unparseable line): nothing is counted."""


def utc_day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")


def utc_hour(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H")


def day_start_ms(day: str) -> int:
    if not DAY_RE.match(day):
        raise Refused(f"bad day {day!r} (YYYY-MM-DD)")
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def check_root(declog_root: str | Path) -> Path:
    """Refuse the roots Option Z keeps closed (EXP-027's /data/mal/r4b) and anything under the sealed tip tape or a tip-tape archive."""
    p = Path(declog_root).resolve()
    s = str(p)
    for bad in FORBIDDEN_ROOTS:
        if s == bad or s.startswith(bad + "/"):
            raise Refused(f"declog root {s} is under {bad}: refused (DEC-031 Amendment 25 item 1; SEALS-BRIEF)")
    if FORBIDDEN_MARK in s:
        raise Refused(f"declog root {s} is inside a tip-tape archive: refused")
    return p


def _keep_only(pairs: Sequence[tuple[str, Any]]) -> dict:
    """object_pairs_hook: every JSON object keeps only kind, ms and t_mark; every other key is dropped while parsing."""
    return {k: v for k, v in pairs if k in KEEP_KEYS}


def arm_files(root: Path, arm: str) -> list[Path]:
    if not arm or "/" in arm or arm.startswith("."):
        raise Refused(f"bad arm {arm!r}")
    d = root / "decisions" / arm
    return sorted(d.glob("*.jsonl")) if d.is_dir() else []


def verify_chain(root: Path, arm: str) -> tuple[str, int]:
    """The arena_declog hash chain over the whole arm (raises Untrusted). Returns (head sha, last seq) only."""
    from tools.arena_declog import DecisionLog, LogChainError

    try:
        return DecisionLog(root).verify(arm)
    except LogChainError as e:
        raise Untrusted(f"decision log chain: {e}") from None


def slim_rows(root: Path, arm: str) -> Iterable[dict]:
    """Yield {kind, ms, t_mark} per row, in file and line order. A line that does not parse, or a row without an int ms, raises Untrusted."""
    for f in arm_files(root, arm):
        for n, line in enumerate(f.read_bytes().split(b"\n"), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line, object_pairs_hook=_keep_only)
            except ValueError:
                raise Untrusted(f"unparseable line {n} in {f.name}") from None
            if not isinstance(row, dict) or not _is_int(row.get("ms")):
                raise Untrusted(f"row without an int ms at line {n} in {f.name}")
            yield row


def entry_times(root: Path, arm: str) -> list[tuple[int, Any]]:
    """(ms, t_mark) of every entry row."""
    return [(int(r["ms"]), r.get("t_mark")) for r in slim_rows(root, arm) if r.get("kind") == "entry"]


def h_days(total: int, n_days: int) -> int | None:
    """H = min(90, max(14, ceil(150 / rate))), rate = total / n_days, exactly: ceil(150 * n_days / total). None when total is 0."""
    if total <= 0:
        return None
    return min(H_MAX_DAYS, max(H_MIN_DAYS, -(-(H_NUM * n_days) // total)))


def p0(root: Path, arm: str, days: Sequence[str], as_of_ms: int, *, verify: bool = True) -> dict:
    if len(days) < 3:
        raise Refused("P0 needs at least 3 full closed UTC days (DEC-031 Amendment 4 item 2.1)")
    if len(set(days)) != len(days):
        raise Refused("P0 days repeat")
    starts = {d: day_start_ms(d) for d in days}
    for d, s in starts.items():
        if s + DAY_MS > as_of_ms:
            raise Refused(f"P0 day {d} is not closed at {iso(as_of_ms)}")
    head = verify_chain(root, arm) if verify else None
    per = {d: 0 for d in sorted(days)}
    for ms, _t in entry_times(root, arm):
        d = utc_day(ms)
        if d in per:
            per[d] += 1
    total = sum(per.values())
    n = len(per)
    out = {
        "tool": "r4bt_attempts p0", "arm": arm, "days": per, "total": total, "n_days": n, "mean_per_day": total / n,
        "min_day": min(per.values()), "floor_ok": (total / n) >= P0_FLOOR_MEAN and min(per.values()) > 0,
        "h_days": h_days(total, n), "as_of": iso(as_of_ms),
    }
    if head is not None:
        out["chain_head"], out["chain_seq"] = head[0], head[1]
    return out


def load_excluded_hours(path: str | Path | None) -> tuple[set[str], str | None]:
    """The Amendment 6 hour table, pinned: JSON {"excluded_hours": ["YYYY-MM-DDTHH", ...]} (extra keys ignored). Returns (hours, sha256 of the file)."""
    if path is None:
        return set(), None
    data = Path(path).read_bytes()
    try:
        obj = json.loads(data)
    except ValueError:
        raise Refused(f"hour table {path} is not JSON") from None
    hours = obj.get("excluded_hours") if isinstance(obj, dict) else None
    if not isinstance(hours, list) or not all(isinstance(h, str) and HOUR_RE.match(h) for h in hours):
        raise Refused("hour table needs a list excluded_hours of YYYY-MM-DDTHH strings")
    return set(hours), hashlib.sha256(data).hexdigest()


def count(root: Path, arm: str, clock_ms: int, h: int, as_of_ms: int, excluded: set[str] | frozenset = frozenset(), *, verify: bool = True) -> dict:
    if not _is_int(clock_ms) or clock_ms <= 0 or clock_ms % HOUR_MS:
        raise Refused("clock_start_ms must be a positive epoch ms on an hour mark")
    if not _is_int(h) or not H_MIN_DAYS <= h <= H_MAX_DAYS:
        raise Refused(f"H must be an int number of days in [{H_MIN_DAYS}, {H_MAX_DAYS}]")
    end_ms = clock_ms + h * DAY_MS
    clock_s = clock_ms // 1000
    head = verify_chain(root, arm) if verify else None
    kept: list[int] = []
    excl: list[int] = []
    before_clock = after_end = bad_mark = 0
    for ms, t_mark in entry_times(root, arm):
        if ms < clock_ms:
            before_clock += 1
            continue
        if ms >= end_ms:
            after_end += 1
            continue
        if not _is_int(t_mark) or t_mark < clock_s:
            bad_mark += 1  # decided after the clock for a mark before it: never counted
            continue
        (excl if utc_hour(ms) in excluded else kept).append(ms)
    kept.sort()
    excl.sort()
    per_date: dict[str, int] = {}
    for ms in kept:
        per_date[utc_day(ms)] = per_date.get(utc_day(ms), 0) + 1
    per_date_excl: dict[str, int] = {}
    for ms in excl:
        per_date_excl[utc_day(ms)] = per_date_excl.get(utc_day(ms), 0) + 1
    # the boundary: the first 00:00Z b with clock < b <= min(clock + H, as_of) holding >= 100 counted attempts on >= 5 dates (ms < b)
    boundary = None
    b = (clock_ms // DAY_MS + 1) * DAY_MS
    while b <= min(end_ms, as_of_ms):
        before = [ms for ms in kept if ms < b]
        if len(before) >= GATE_ATTEMPTS and len({utc_day(ms) for ms in before}) >= GATE_DATES:
            boundary = b
            break
        b += DAY_MS
    if boundary is not None:
        status = "boundary"
    elif as_of_ms >= end_ms:
        status = "ended_not_decidable"  # Amendment 4 item 2.3: no boundary by clock + H
    else:
        status = "pending"
    out: dict[str, Any] = {
        "tool": "r4bt_attempts count", "arm": arm, "clock_start_ms": clock_ms, "clock_start": iso(clock_ms), "h_days": h, "window_end_ms": end_ms,
        "window_end": iso(end_ms), "as_of": iso(as_of_ms), "counted_per_date": per_date, "counted_total": len(kept),
        "excluded_hour_per_date": per_date_excl, "excluded_hour_total": len(excl), "not_counted_before_clock": before_clock,
        "not_counted_after_window": after_end, "not_counted_mark_before_clock": bad_mark, "status": status,
        "boundary_ms": boundary, "boundary": iso(boundary) if boundary is not None else None,
    }
    if boundary is not None:
        k = sum(1 for ms in kept if ms < boundary)
        x = sum(1 for ms in excl if ms < boundary)
        out["attempts_before_boundary"] = k
        out["dates_before_boundary"] = len({utc_day(ms) for ms in kept if ms < boundary})
        out["excluded_hour_before_boundary"] = x
        out["excluded_share"] = x / (k + x) if (k + x) else 0.0
        out["excluded_cap_exceeded"] = out["excluded_share"] > EXCLUDED_CAP
    if head is not None:
        out["chain_head"], out["chain_seq"] = head[0], head[1]
    return out


def day_sha(root: Path, arm: str, as_of_ms: int) -> dict:
    """sha256 of each CLOSED day file (a day whose end is at or before as_of). Names and hashes only."""
    out = {}
    for f in arm_files(root, arm):
        d = f.stem
        if DAY_RE.match(d) and day_start_ms(d) + DAY_MS <= as_of_ms:
            out[d] = hashlib.sha256(f.read_bytes()).hexdigest()
    return {"tool": "r4bt_attempts day-sha", "arm": arm, "as_of": iso(as_of_ms), "day_sha256": out}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="r4-b-T outcome-blind attempt counter (counts, dates and hashes only).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("p0", "count", "day-sha"):
        s = sub.add_parser(name)
        s.add_argument("--declog-root", required=True, help="the host's declog root (the dir holding decisions/<arm>/)")
        s.add_argument("--arm", default=ARM_DEFAULT)
        s.add_argument("--out", default=None, help="also write the JSON here (refused if it exists)")
        if name != "day-sha":
            s.add_argument("--no-verify", action="store_true", help="skip the hash-chain check (tests only; recorded in the output)")
    sub.choices["p0"].add_argument("--days", required=True, help="comma-separated UTC days, e.g. 2026-11-04,2026-11-05,2026-11-06")
    c = sub.choices["count"]
    c.add_argument("--clock-ms", type=int, required=True)
    c.add_argument("--h-days", type=int, required=True)
    c.add_argument("--excluded-hours", default=None, help="the pinned Amendment 6 hour table (JSON with excluded_hours)")
    return ap


def main(argv: Sequence[str] | None = None, *, now_ms: int | None = None) -> int:
    a = build_parser().parse_args(argv)
    as_of = int(time.time() * 1000) if now_ms is None else int(now_ms)
    try:
        root = check_root(a.declog_root)
        if a.cmd == "p0":
            out = p0(root, a.arm, [d.strip() for d in a.days.split(",") if d.strip()], as_of, verify=not a.no_verify)
        elif a.cmd == "count":
            hours, sha = load_excluded_hours(a.excluded_hours)
            out = count(root, a.arm, a.clock_ms, a.h_days, as_of, hours, verify=not a.no_verify)
            out["excluded_hours_sha256"] = sha
        else:
            out = day_sha(root, a.arm, as_of)
        if getattr(a, "no_verify", False):
            out["chain_verified"] = False
    except Refused as e:
        print(f"r4bt_attempts: REFUSED: {e}", file=sys.stderr)
        return 2
    except Untrusted as e:
        print(f"r4bt_attempts: UNTRUSTED: {e}", file=sys.stderr)
        return 3
    text = json.dumps(out, sort_keys=True)
    if a.out:
        p = Path(a.out)
        if p.exists():
            print(f"r4bt_attempts: REFUSED: {p} exists", file=sys.stderr)
            return 2
        p.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
