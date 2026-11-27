"""Counts-only peak simultaneous open count of one open-sim book at one stake rung (QP-RULING-1126 Q4). Standalone; osb_board.py is imported, not changed.
Reads only the entry and exit instants (and the open/closed status) of each position at the rung, through the board's own book lookup and pricer. Prints day lines and one max line, nothing else.
Hard ceiling in code: any day after 2026-11-25 is refused (exit 2); 2026-11-26..2026-12-01 is holdout H, sealed until 2026-12-02T02:00Z. Exit codes: 0 ok, 2 setup error."""
import argparse, re, sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

CEILING_DAY = "2026-11-25"  # last day this script may report; not a flag
DAY_MS = 86_400_000
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
    return a, b


def day_counts(spans, d0):
    """spans: [(t0, t1_or_None)] with None = open to the end of the data (an open interval [t0, t1)). -> (peak_open, positions_opened) for the UTC day starting d0.
    Positions open at d0 (carried in) count toward the peak."""
    d1 = d0 + DAY_MS
    cur, opened, ev = 0, 0, []
    for t0, t1 in spans:
        end = d1 if t1 is None else max(t1, t0)
        if t0 >= d1 or end <= d0 or end == t0:
            continue
        if t0 < d0:
            cur += 1
        else:
            opened += 1
            ev.append((t0, 1))
        if end < d1:
            ev.append((end, -1))
    ev.sort()  # at an equal instant the exit (-1) sorts first
    peak = cur
    for _, k in ev:
        cur += k
        peak = max(peak, cur)
    return peak, opened


def spans_of(pnl, stake, end_ms):
    """entry/exit instants at the rung: closed with an exit before the data end, or still open at the end. Rows with no exit instant or excluded by the engine are not counted."""
    sk, out = str(float(stake)), []
    for p in pnl.get("positions", []):
        st = p["status"].get(sk)
        t0, t1 = p["t0"], p.get("t1")
        if t0 >= end_ms:
            continue
        if st == "closed" and t1 is not None:
            out.append((t0, None if t1 >= end_ms else t1))
        elif st == "open_at_tape_end":
            out.append((t0, None))
    return out


def run(a, B):
    first_ms, last_ms = check_days(a.first_day, a.last_day)
    books, _live = B.load_books_file(a.books)
    hit = [b for b in books if b["m"]["book_id"] == a.book]
    if not hit:
        raise Refusal("book not found")
    info = B.inspect_book(hit[0], None, None)
    pnl, end = info.get("pnl"), info.get("end")
    if pnl is None or end is None:
        raise Refusal("no ledger")
    last_complete = B.complete_last_day(end)
    out, mx = [], 0
    spans = spans_of(pnl, a.stake, end)
    for d0 in range(first_ms, last_ms + 1, DAY_MS):
        if day_str(d0) > last_complete:
            continue
        peak, opened = day_counts(spans, d0)
        mx = max(mx, peak)
        out.append("day=%s book=%s stake=%s peak_open=%d positions_opened=%d" % (day_str(d0), a.book, a.stake, peak, opened))
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
        float(a.stake)
        if board is None:
            import osb_board as board  # noqa: E402 (after the ceiling check would be better, but run() checks it before any read)
        run(a, board)
        return 0
    except Exception as e:  # noqa: BLE001
        print("osb_peak_open: refused (%s)" % type(e).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
