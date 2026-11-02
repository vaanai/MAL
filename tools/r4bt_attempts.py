"""r4bt_attempts: the OUTCOME-BLIND attempt counter of the arena book r4-b-T (`r4bt-a1`; the r4-b-T DEC-031 amendment of PR #1187, DRAFT, items 15, 16
and 26). Prints COUNTS, dates and file hashes only. PAPER: no key, no network, no price. Pure stdlib.

What it reads, and nothing else
  The r4-b DRY / decision host's hash-chained decision log (`tools.arena_declog` layout): `<declog_root>/decisions/<arm>/<UTC day>.jsonl`, one arm
  (the frozen code's `r4b-exp027`). Each day file is read whole under a shared flock (the writer appends under an exclusive one), then split into lines.
  * Counting fields. Every line is parsed with a JSON `object_pairs_hook` that KEEPS ONLY the keys `kind`, `ms`, `t_mark` (counting) and `seq`, `prev`,
    `sha` (the chain check) of every object; every other key (mint, pool, meta, rank, score, reason, decision_slot, gap spans, ...) is dropped while
    parsing and never becomes a Python value. Only `kind`, `ms` and `t_mark` leave the reader.
  * Chain check (QP-RULING-1102-r4b-a3-record Addendum 1, A1.2 item 8: "a pinned counter that reads only entry-row timestamps"). The arena_declog rule is
    sha = sha256(prev + canon(row without sha)), canon = sorted keys, compact separators. A stored line IS canon(row with sha), so canon(row without sha)
    is the line's own text with its single top-level `"sha":"<hex>"` pair (and one adjacent comma) cut out. The check hashes that text; it does not parse
    any other field. prev must equal the previous row's sha (genesis 64 zeros) and seq must step by one, across day files in name order. A line that is
    not canonical in this sense fails the check (fail closed). A torn last line, an unparseable line or a broken link exits 3.
  An attempt is an `entry` row (DEC-031 §2: every entry decision, guarded reverts included). Exit, gap and start rows are not counted.

Subcommands
  p0      P0 (DEC-031 Amendment 4 item 2.1) on whole closed UTC days: entry rows per day (by the UTC day of `ms`), the mean, the floor (mean >= 2.0 a
          day and no day at 0) and H = min(90, max(14, ceil(150 / rate))) days (item 2.2), in integers: ceil(150 * n_days / total).
  count   Counted attempts of a registered clock (`ms` >= clock_start_ms, `t_mark` >= the clock in seconds, `ms` < clock + H) per UTC date. The pinned
          Amendment 6 hour table (`--hour-table`, JSON {"tabled_from": "YYYY-MM-DDTHH", "tabled_through": "YYYY-MM-DDTHH", "excluded_hours": [...]},
          excluded hours inside the tabled range) removes the attempts decided in excluded hours and reports them toward the 10% cap. The DEC-031 §2
          boundary is the first 00:00Z b (clock < b <= min(clock + H, as_of)) with at least 100 counted attempts (ms < b) on at least 5 UTC dates. It is
          PRINTED only when the table covers every hour from the clock's hour to the hour before b; otherwise the status is `pending_hour_table` and the
          boundary is withheld (an untabled hour may still be excluded, which can only move the boundary later). Without any table the boundary is never
          printed. `ended_not_decidable` (Amendment 4 item 2.3) is printed only when clock + H has passed and no boundary exists even with every untabled
          hour counted (more exclusions can never create one).
  day-sha The sha256 of each closed day file of the arm (for §1.2's daily decision-log sha256 notebook entry). Names and hashes only.

Refusals (exit 2): a declog root, or any day file, under /data/mal/r4b (EXP-027's outputs stay closed to r4-b-T until its read), under
/var/lib/mal/sealed (the tip tape) or under a tip-tape archive, checked on the path as given and as resolved (no symlink route); a P0 day not closed at
as_of; a bad clock, H or hour table. Exit 3: the log cannot be trusted. Nothing here opens a tip file, a tape, a ledger, a fill or a P&L. No book has
cleared the promotion gate.
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import fcntl
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

ARM_DEFAULT = "r4b-exp027"  # tools/r4b_live.BOT_ID at the install sha bc65c088 (the frozen code's arm name; not imported: no tools/r4b_* import here)
COUNT_KEYS = ("kind", "ms", "t_mark")
CHAIN_KEYS = ("seq", "prev", "sha")
KEEP_KEYS = COUNT_KEYS + CHAIN_KEYS
GENESIS = "0" * 64  # tools/arena_declog.GENESIS (a test asserts it)
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
SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class Refused(RuntimeError):
    """A refusal: nothing is counted."""


class Untrusted(RuntimeError):
    """The log cannot be trusted (broken chain, torn or unparseable line): nothing is counted."""


def utc_day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")


def utc_hour(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H")


def day_start_ms(day: str) -> int:
    if not DAY_RE.match(day):
        raise Refused(f"bad day {day!r} (YYYY-MM-DD)")
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def hour_start_ms(hour: str) -> int:
    if not HOUR_RE.match(hour):
        raise Refused(f"bad hour {hour!r} (YYYY-MM-DDTHH)")
    return int(datetime.strptime(hour, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp() * 1000)


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _forbidden(path_str: str) -> str | None:
    for bad in FORBIDDEN_ROOTS:
        if path_str == bad or path_str.startswith(bad + "/"):
            return bad
    return FORBIDDEN_MARK if FORBIDDEN_MARK in path_str else None


def check_path(p: str | Path, what: str = "declog root") -> Path:
    """Refuse a path under the closed roots, checked both as given (made absolute, not resolved) and resolved (symlinks followed)."""
    for s in (os.path.normpath(os.path.abspath(str(p))), str(Path(p).resolve())):
        bad = _forbidden(s)
        if bad:
            raise Refused(f"{what} {s} is under {bad}: refused (the r4-b-T DEC-031 amendment, item 1; SEALS-BRIEF)")
    return Path(p).resolve()


def check_root(declog_root: str | Path) -> Path:
    return check_path(declog_root)


def _keep_only(pairs: Sequence[tuple[str, Any]]) -> dict:
    """object_pairs_hook: every JSON object keeps only kind, ms, t_mark, seq, prev and sha; every other key is dropped while parsing."""
    return {k: v for k, v in pairs if k in KEEP_KEYS}


def arm_files(root: Path, arm: str) -> list[Path]:
    if not arm or "/" in arm or arm.startswith("."):
        raise Refused(f"bad arm {arm!r}")
    d = root / "decisions" / arm
    files = sorted(d.glob("*.jsonl")) if d.is_dir() else []
    for f in files:
        check_path(f, "day file")
    return files


def _read_locked(f: Path) -> bytes:
    """The whole file under a shared flock: the writer (arena_declog.append) holds an exclusive one while it appends a line."""
    fd = os.open(str(f), os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_SH)
        chunks = []
        while True:
            b = os.read(fd, 1 << 20)
            if not b:
                break
            chunks.append(b)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _strip_sha(text: str, sha: str) -> str | None:
    """canon(row without sha) from canon(row with sha): cut the single top-level "sha":"<hex>" pair and one adjacent comma. None if not exactly once."""
    tok = f'"sha":"{sha}"'
    if text.count(tok) != 1:
        return None
    i = text.index(tok)
    j = i + len(tok)
    if j < len(text) and text[j] == ",":
        return text[:i] + text[j + 1:]
    if i > 0 and text[i - 1] == ",":
        return text[:i - 1] + text[j:]
    return text[:i] + text[j:]


@dataclass
class Read:
    rows: list  # [{kind, ms, t_mark}] in log order
    head: tuple[str, int] | None  # (head sha, last seq) when verified


def read_log(root: Path, arm: str, *, verify: bool = True) -> Read:
    """Stream every row of the arm. Keeps only the counting fields; checks the hash chain on the raw line text when verify is set."""
    prev, seq = GENESIS, -1
    rows = []
    for f in arm_files(root, arm):
        data = _read_locked(f)
        if data and not data.endswith(b"\n"):
            raise Untrusted(f"torn last line in {f.name}")
        for n, line in enumerate(data.split(b"\n"), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line, object_pairs_hook=_keep_only)
            except ValueError:
                raise Untrusted(f"unparseable line {n} in {f.name}") from None
            if not isinstance(row, dict) or not _is_int(row.get("ms")):
                raise Untrusted(f"row without an int ms at line {n} in {f.name}")
            if verify:
                sha = row.get("sha")
                if row.get("prev") != prev or row.get("seq") != seq + 1 or not isinstance(sha, str) or not SHA_RE.match(sha):
                    raise Untrusted(f"chain broken at seq {seq + 1} in {f.name}")
                try:
                    text = line.decode("utf-8")
                except UnicodeDecodeError:
                    raise Untrusted(f"line {n} in {f.name} is not UTF-8") from None
                body = _strip_sha(text, sha)
                if body is None or hashlib.sha256((prev + body).encode("utf-8")).hexdigest() != sha:
                    raise Untrusted(f"chain broken at seq {seq + 1} in {f.name}")
                prev, seq = sha, row["seq"]
            rows.append({k: row[k] for k in COUNT_KEYS if k in row})
    return Read(rows, (prev, seq) if verify else None)


def entry_times(rows: Sequence[dict]) -> list[tuple[int, Any]]:
    """(ms, t_mark) of every entry row."""
    return [(int(r["ms"]), r.get("t_mark")) for r in rows if r.get("kind") == "entry"]


def h_days(total: int, n_days: int) -> int | None:
    """H = min(90, max(14, ceil(150 / rate))), rate = total / n_days, exactly: ceil(150 * n_days / total). None when total is 0."""
    if total <= 0:
        return None
    return min(H_MAX_DAYS, max(H_MIN_DAYS, -(-(H_NUM * n_days) // total)))


def _chain_out(out: dict, head: tuple[str, int] | None) -> dict:
    out["chain_verified"] = head is not None
    if head is not None:
        out["chain_head"], out["chain_seq"] = head
    return out


def p0(root: Path, arm: str, days: Sequence[str], as_of_ms: int, *, verify: bool = True) -> dict:
    if len(days) < 3:
        raise Refused("P0 needs at least 3 full closed UTC days (DEC-031 Amendment 4 item 2.1)")
    if len(set(days)) != len(days):
        raise Refused("P0 days repeat")
    for d in days:
        if day_start_ms(d) + DAY_MS > as_of_ms:
            raise Refused(f"P0 day {d} is not closed at {iso(as_of_ms)}")
    lg = read_log(root, arm, verify=verify)
    per = {d: 0 for d in sorted(days)}
    for ms, _t in entry_times(lg.rows):
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
    return _chain_out(out, lg.head)


@dataclass(frozen=True)
class HourTable:
    tabled_from_ms: int  # start of the first tabled hour
    tabled_through_ms: int  # start of the last tabled hour (inclusive)
    excluded: frozenset
    sha256: str | None

    def covers(self, lo_ms: int, hi_ms: int) -> bool:
        """Every hour whose start lies in [lo_ms, hi_ms) is tabled."""
        return hi_ms <= lo_ms or (self.tabled_from_ms <= (lo_ms // HOUR_MS) * HOUR_MS and ((hi_ms - 1) // HOUR_MS) * HOUR_MS <= self.tabled_through_ms)


def load_hour_table(path: str | Path | None) -> HourTable | None:
    """The pinned Amendment 6 hour table: JSON {"tabled_from", "tabled_through", "excluded_hours"} (extra keys ignored)."""
    if path is None:
        return None
    data = Path(path).read_bytes()
    try:
        obj = json.loads(data)
    except ValueError:
        raise Refused(f"hour table {path} is not JSON") from None
    if not isinstance(obj, dict):
        raise Refused("hour table must be a JSON object")
    fr, th, hours = obj.get("tabled_from"), obj.get("tabled_through"), obj.get("excluded_hours")
    if not isinstance(fr, str) or not isinstance(th, str):
        raise Refused("hour table needs tabled_from and tabled_through (YYYY-MM-DDTHH): the hours it covers")
    lo, hi = hour_start_ms(fr), hour_start_ms(th)
    if hi < lo:
        raise Refused("hour table: tabled_through is before tabled_from")
    if not isinstance(hours, list) or not all(isinstance(h, str) and HOUR_RE.match(h) for h in hours):
        raise Refused("hour table needs a list excluded_hours of YYYY-MM-DDTHH strings")
    for h in hours:
        if not lo <= hour_start_ms(h) <= hi:
            raise Refused(f"hour table: excluded hour {h} lies outside the tabled range")
    return HourTable(lo, hi, frozenset(hours), hashlib.sha256(data).hexdigest())


def count(root: Path, arm: str, clock_ms: int, h: int, as_of_ms: int, table: HourTable | None = None, *, verify: bool = True) -> dict:
    if not _is_int(clock_ms) or clock_ms <= 0 or clock_ms % HOUR_MS:
        raise Refused("clock_start_ms must be a positive epoch ms on an hour mark")
    if not _is_int(h) or not H_MIN_DAYS <= h <= H_MAX_DAYS:
        raise Refused(f"H must be an int number of days in [{H_MIN_DAYS}, {H_MAX_DAYS}]")
    end_ms = clock_ms + h * DAY_MS
    clock_s = clock_ms // 1000
    excluded = table.excluded if table is not None else frozenset()
    lg = read_log(root, arm, verify=verify)
    kept: list[int] = []
    excl: list[int] = []
    before_clock = after_end = bad_mark = 0
    for ms, t_mark in entry_times(lg.rows):
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
    # the first 00:00Z candidate b with clock < b <= min(clock + H, as_of) holding >= 100 counted attempts on >= 5 dates (ms < b). Untabled hours count
    # here, so the candidate is the earliest the boundary can be; it is printed only when the table covers [clock, b).
    candidate = None
    b = (clock_ms // DAY_MS + 1) * DAY_MS
    while b <= min(end_ms, as_of_ms):
        before = [ms for ms in kept if ms < b]
        if len(before) >= GATE_ATTEMPTS and len({utc_day(ms) for ms in before}) >= GATE_DATES:
            candidate = b
            break
        b += DAY_MS
    boundary = None
    if candidate is not None:
        if table is not None and table.covers(clock_ms, candidate):
            boundary, status = candidate, "boundary"
        else:
            status = "pending_hour_table"
    elif as_of_ms >= end_ms:
        status = "ended_not_decidable"  # Amendment 4 item 2.3: no boundary by clock + H, even with every untabled hour counted
    else:
        status = "pending"
    out: dict[str, Any] = {
        "tool": "r4bt_attempts count", "arm": arm, "clock_start_ms": clock_ms, "clock_start": iso(clock_ms), "h_days": h, "window_end_ms": end_ms,
        "window_end": iso(end_ms), "as_of": iso(as_of_ms), "counted_per_date": per_date, "counted_total": len(kept),
        "excluded_hour_per_date": per_date_excl, "excluded_hour_total": len(excl), "not_counted_before_clock": before_clock,
        "not_counted_after_window": after_end, "not_counted_mark_before_clock": bad_mark, "status": status,
        "boundary_ms": boundary, "boundary": iso(boundary) if boundary is not None else None,
        "hour_table": None if table is None else {"tabled_from": utc_hour(table.tabled_from_ms), "tabled_through": utc_hour(table.tabled_through_ms),
                                                  "excluded_hours": len(table.excluded), "sha256": table.sha256},
    }
    if boundary is not None:
        k = sum(1 for ms in kept if ms < boundary)
        x = sum(1 for ms in excl if ms < boundary)
        out["attempts_before_boundary"] = k
        out["dates_before_boundary"] = len({utc_day(ms) for ms in kept if ms < boundary})
        out["excluded_hour_before_boundary"] = x
        out["excluded_share"] = x / (k + x) if (k + x) else 0.0
        out["excluded_cap_exceeded"] = out["excluded_share"] > EXCLUDED_CAP
    return _chain_out(out, lg.head)


def day_sha(root: Path, arm: str, as_of_ms: int) -> dict:
    """sha256 of each CLOSED day file (a day whose end is at or before as_of). Names and hashes only."""
    out = {}
    for f in arm_files(root, arm):
        d = f.stem
        if DAY_RE.match(d) and day_start_ms(d) + DAY_MS <= as_of_ms:
            out[d] = hashlib.sha256(_read_locked(f)).hexdigest()
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
    c.add_argument("--hour-table", default=None,
                   help="the pinned Amendment 6 hour table (JSON: tabled_from, tabled_through, excluded_hours); without it no boundary is printed")
    return ap


def main(argv: Sequence[str] | None = None, *, now_ms: int | None = None) -> int:
    a = build_parser().parse_args(argv)
    as_of = int(time.time() * 1000) if now_ms is None else int(now_ms)
    try:
        root = check_root(a.declog_root)
        if a.cmd == "p0":
            out = p0(root, a.arm, [d.strip() for d in a.days.split(",") if d.strip()], as_of, verify=not a.no_verify)
        elif a.cmd == "count":
            out = count(root, a.arm, a.clock_ms, a.h_days, as_of, load_hour_table(a.hour_table), verify=not a.no_verify)
        else:
            out = day_sha(root, a.arm, as_of)
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
