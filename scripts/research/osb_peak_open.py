"""Counts-only peak simultaneous open count of one open-sim book at one stake rung (QP-RULING-1126 Q4). Standalone; osb_board.py is imported, not changed.
Reads only the entry and exit instants (and the open/closed status) of each position at the rung, through the board's own book lookup and pricer. Prints day lines and one max line, nothing else.
Hard ceiling in code: any day after 2026-11-25 is refused (exit 2); the pricer reads the whole ledger in memory, and the output is made independent of rows at or after the ceiling by the rules in spans_of (exits and entries cut, exclusions of unknown timing near the ceiling refused);
the 2026-11-07 cut-over day is refused; the first data day may be partial and prints as a full day; 2026-11-26..2026-12-01 is holdout H, sealed until 2026-12-02T02:00Z. Exit codes: 0 ok, 2 setup error."""
import argparse, math, re, sys, time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

CEILING_DAY = "2026-11-25"  # last day this script may report; not a flag
DAY_MS = 86_400_000
CEILING_MS = 1_795_651_200_000  # 2026-11-26T00:00:00Z: every read is truncated here (holdout H starts); asserted against CEILING_DAY below
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class Refusal(Exception):
    """exit 2"""


def day_start_ms(day):
    if not DAY_RE.match(day):
        raise Refusal("bad day")
    try:
        return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
    except ValueError:
        raise Refusal("bad day")


def day_str(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def check_days(first, last):
    """-> (first_ms, last_ms) or Refusal. The ceiling is checked before anything is read."""
    a, b = day_start_ms(first), day_start_ms(last)
    if b > day_start_ms(CEILING_DAY) or a > day_start_ms(CEILING_DAY):
        raise Refusal("day after ceiling")
    if a > b:
        raise Refusal("empty range")
    if a <= day_start_ms(CUTOVER_DAY) <= b:
        raise Refusal("range includes the cut-over day")
    return a, b


HOLD_BOUND_MS = 86_400_000 + 1_900 + 1_000 + 2 * 3_600_000  # max hold (arena_rule_l1 HOLD_MS) + host tick + 2 h for an exit-side exclusion to resolve (floor_nov_pnl exit-completeness comment)
ENTRY_SIDE_REASON = "feed_gap_entry"  # the only exclusion reason decided at the entry (floor_gap_range_engine); every other reason is exit-side or of unknown timing
CUTOVER_DAY = "2026-11-07"  # floor_nov_pnl drops the 2026-11-07T18Z cut-over hour's entries, so counts for that day would be low: refused
assert CEILING_MS == int(datetime(2026, 11, 26, tzinfo=timezone.utc).timestamp() * 1000)


def day_counts(spans, d0):
    """spans: [(t0, t1_or_None)] with None = open to the end of the data (an open interval [t0, t1)). -> (peak_open, positions_opened) for the UTC day starting d0.
    Positions open at d0 (carried in) count toward the peak."""
    d1 = d0 + DAY_MS
    cur, opened, ev = 0, 0, []
    for t0, t1 in spans:
        end = d1 if t1 is None else max(t1, t0)
        if t0 >= d1 or end < d0 or (end == d0 and end > t0):
            continue
        if t0 < d0:
            cur += 1
        else:
            opened += 1
            ev.append((t0, 1, 1))  # entry
            if end == t0:
                ev.append((t0, 2, -1))  # zero-length: counted at its instant, removed after every entry of that instant
                continue
        if end < d1:
            ev.append((end, 0, -1))  # exit sorts before entries at the same instant
    ev.sort()
    peak = cur
    for _, kind, k in ev:
        cur += k
        if kind == 1:
            peak = max(peak, cur)
    return peak, opened


def spans_of(pnl, stake, end_ms):
    """-> (spans, excluded_t0s, first_t0). Output is made independent of rows at or after CEILING_MS (the pricer itself still reads them in memory): an entry at or after it is dropped, an exit at or after it (or still open) is open to the end.
    A closed entry with no exit instant is a setup error. engine_excluded entries are not opens; their entry instants are returned for the per-day excluded count."""
    sk, out, exc, first = str(float(stake)), [], [], None
    end_ms = min(end_ms, CEILING_MS)
    for p in pnl.get("positions", []):
        st = p["status"].get(sk)
        t0, t1 = p["t0"], p.get("t1")
        first = t0 if first is None else min(first, t0)
        if t0 >= end_ms:
            continue
        if st == "closed":
            if t1 is None:
                raise Refusal("closed entry without exit instant")
            out.append((t0, None if t1 >= end_ms else t1))
        elif st == "open_at_tape_end":
            out.append((t0, None))
        elif st == "engine_excluded":
            # the pricer keeps no instant for the exclusion row. Entry-side (decided at the entry): counted. Anything else could have been decided in H for an entry still open at the ceiling: refuse
            reason = (p.get("reason") or {}).get(sk)
            if reason != ENTRY_SIDE_REASON and t0 >= CEILING_MS - HOLD_BOUND_MS:
                raise Refusal("exclusion of unknown timing near the ceiling")
            exc.append(t0)
    return out, exc, first


def rung_of(stake_arg, stakes):
    """the rung must equal, exactly as text, str(float(s)) of one of the manifest's stakes_sol; nan, inf, aliases and absent rungs are refused. -> that fixed text"""
    for s in stakes:
        if isinstance(s, (int, float)) and not isinstance(s, bool) and math.isfinite(s) and stake_arg == str(float(s)):
            return str(float(s))
    raise Refusal("stake not a manifest rung")


def run(a, B):
    first_ms, last_ms = check_days(a.first_day, a.last_day)
    books, _live = B.load_books_file(a.books)
    hit = [b for b in books if b["m"]["book_id"] == a.book]
    if not hit:
        raise Refusal("book not found")
    rung = rung_of(a.stake, hit[0]["m"]["stakes_sol"])
    info = B.inspect_book(hit[0], int(time.time() * 1000), None)
    pnl, end = info.get("pnl"), info.get("end")
    if pnl is None or end is None:
        raise Refusal("no ledger")
    last_complete = B.complete_last_day(end)
    out, mx = [], 0
    spans, exc, first = spans_of(pnl, rung, end)
    if first is None or first_ms < first // DAY_MS * DAY_MS:
        raise Refusal("day before the book's data start")
    for d0 in range(first_ms, last_ms + 1, DAY_MS):
        if day_str(d0) > last_complete:
            continue
        peak, opened = day_counts(spans, d0)
        mx = max(mx, peak)
        nex = sum(1 for t in exc if d0 <= t < d0 + DAY_MS)
        out.append("day=%s book=%s stake=%s peak_open=%d positions_opened=%d excluded=%d" % (day_str(d0), a.book, rung, peak, opened, nex))
    if not out:
        raise Refusal("no complete day")
    for line in out:
        print(line)
    print("max_peak_open=%d" % mx)


def main(argv=None, board=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("book")
    ap.add_argument("--books", required=True, help="BOOKS.json path (the board's input file)")
    ap.add_argument("--stake", default="0.25")
    ap.add_argument("--from", dest="first_day", required=True)
    ap.add_argument("--to", dest="last_day", required=True)
    a = ap.parse_args(argv)
    try:
        if board is None:
            import osb_board as board  # noqa: E402 (after the ceiling check would be better, but run() checks it before any read)
        run(a, board)
        return 0
    except Exception as e:  # noqa: BLE001
        print("osb_peak_open: refused (%s)" % type(e).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
