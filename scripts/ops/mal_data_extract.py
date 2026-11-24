#!/usr/bin/env python3
"""mal_data_extract: daily release directory for the private repo vaanai/MAL-data.

Controlled by QP-RULING-1124-external-extract.md (sha256 cfe5d5681ff8b732fbb63b23dd04d7584cdcc223f2bab5f04f54b838a8e33f41), items 1 and 2 only.
No price prints, no launch stream (items 3, 4). The manager pushes the output; this tool never touches git remotes. PAPER-LANE NOTE: nothing here says a book makes money.

  python3 scripts/ops/mal_data_extract.py --ledger LEDGER.jsonl --snapshot-dir DIR --totals-file TXT --out-dir OUT [--now-ms N] [--release-e2-window]
                                          [--bot-id floor-lane-1] [--e2-book ID ...] [--deny-book ID ...]

INPUTS (each named on the command line; the tool opens nothing else)
  --ledger        the lane ledger JSONL (row format: tools/h5_lane_executor.py LaneLedger.record/append, folded as tools/lane_daily_report.bot_report does:
                  ev send_intent / fill / exit / fee / writeoff; key = bot_id + mint; sol_in_lamports - sol_out_lamports per row; stake_lamports on send_intent).
  --snapshot-dir  osb_board.py (scripts/research/osb_board.py write_board) OVERWRITES board.json and board.md in its out dir and keeps NO history. The manager therefore copies
                  both files hourly into <snapshot-dir>/<any name>/board.json + board.md (byte copies). The newest snapshot whose board.json generated_utc is at least 24 h
                  before the run instant is released.
  --totals-file   text lines like lane_daily_report.summary(): `2026-11-24 floor-lane-1: sends 8 landed 8 failed 0 (cost X SOL), exits 7, realized X SOL after all fees, open 9 (basis Y SOL)`.
                  Only per-UTC-day sends, landed, failed (+cost), exits and realized SOL are kept, for complete UTC days; open count, basis and any missed counts are dropped.

  --disclosures   manager-written JSON {book_id: [verbatim Am.9 disclosure lines]}, pinned by --disclosures-sha256; the run refuses if a released book has no entry.

E2 HOLD (ruling item 1; QP + reviewer on PR #1495): entries in [2026-11-18T12:00Z, 2026-12-03T00:00Z) are withheld by default (the extension end). `--e2-first-read-decided`
narrows the hold to [2026-11-26T00:00Z, 2026-12-03T00:00Z) once the first E2 read is written and decided; `--release-e2-window` releases everything after the final read.
The board hold is by book, not by day: while ANY hold is on, book A and its twins (role starting "control", pair partners, --e2-book) are replaced by number-free rows, their pairs
are dropped, and the control-derived fields of the variants (vs_control, control_forward, paired_difference, floor_holds) are nulled; board.md has the matching lines removed.
(Cumulative windows released daily could be differenced into per-day E2 rows, so no partial redaction is attempted.)
The row_id dedupe in closed_trades is as in h5_lane_executor.lane_fold (lane_daily_report.bot_report does not dedupe; harmless).

OUTPUTS in --out-dir: floor-closed-trades.jsonl, board.json, board.md, daily-totals.json, MANIFEST.json (sha256 of each other file, extractor git commit, run instant, ruling sha).
"""
from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Sequence

RULING_NAME = "QP-RULING-1124-external-extract.md"
RULING_SHA256 = "cfe5d5681ff8b732fbb63b23dd04d7584cdcc223f2bab5f04f54b838a8e33f41"
LAG_MS = 24 * 3600 * 1000
E2_START_MS = calendar.timegm((2026, 11, 18, 12, 0, 0)) * 1000
E2_END_MS = calendar.timegm((2026, 11, 26, 0, 0, 0)) * 1000  # end of the first E2 read window
E2_EXT_END_MS = calendar.timegm((2026, 12, 3, 0, 0, 0)) * 1000  # end of the extended window
DEFAULT_BOT = "floor-lane-1"
WITHHELD = "withheld: E2 hold-back"
LIVE_WITHHELD = "withheld in external copy"
TRADE_FIELDS = ("entry_time", "exit_time", "stake_sol", "realized_sol", "failed")  # the whitelist: the only keys a trade row may carry
TRADES_NAME = "floor-closed-trades.jsonl"
TRADES_NOTE = "Real-fill lane trades; not a gate read; floor-lane-1 has not cleared the promotion gate."
BOARD_NOTE = "in-sample; open simulation; not a gate read"
# Ruling item 2: never add these (matched on the book id, lower case, substring).
SEALED_BOOK_MARKERS = ("r4bt", "r4-b", "r4b", "hv4", "q50", "s1t", "dbc", "floor-cpmm", "cpmm")


class ExtractError(ValueError):
    pass


def iso_ms(ms: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ms // 1000)) + ".%03dZ" % (ms % 1000)


def parse_utc(s: str) -> int:
    m = re.fullmatch(r"(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(?:\.(\d+))?Z", str(s))
    if not m:
        raise ExtractError("unreadable UTC instant %r" % (s,))
    y, mo, d, h, mi, se = (int(m.group(i)) for i in range(1, 7))
    return calendar.timegm((y, mo, d, h, mi, se)) * 1000 + (int((m.group(7) + "000")[:3]) if m.group(7) else 0)


def _int(v: Any, key: str) -> int:
    if v is None:
        return 0
    if isinstance(v, bool) or not isinstance(v, int):
        raise ExtractError("ledger field %r is not an integer" % key)
    return v


def sol(lamports: int) -> str:
    """Exact 9-decimal SOL string (integer arithmetic), as tools/lane_daily_report.sol."""
    sign = "-" if lamports < 0 else ""
    a = abs(int(lamports))
    return "%s%d.%09d" % (sign, a // 10**9, a % 10**9)


def hold_range(first_read_decided: bool, release: bool) -> tuple[int, int] | None:
    if release:
        return None
    return (E2_END_MS if first_read_decided else E2_START_MS, E2_EXT_END_MS)


# ------------------------------------------------------------------------------------------------------------------ item 1: closed trades
def closed_trades(rows: Sequence[Any], bot_id: str, unmatched: list[tuple[int, int]] | None = None) -> list[dict[str, int]]:
    """Fold one bot's ledger rows to closed trades (lamports, ms). A trade closes on exit, writeoff or a failed buy fill (failed sends and failed landings are
    included at real cost, as in lane_daily_report). Open positions, rows with no send_intent and other bots' rows are never returned."""
    pos: dict[str, dict[str, int]] = {}
    out: list[dict[str, int]] = []
    seen: set[str] = set()
    last_closed: dict[str, dict[str, int]] = {}
    for r in rows:
        if not isinstance(r, dict) or not isinstance(r.get("ev"), str):
            raise ExtractError("ledger row is not an object with an ev")
        rid = r.get("row_id")
        if isinstance(rid, str):
            if rid in seen:
                continue
            seen.add(rid)
        ev = r["ev"]
        if ev in ("start", "snap") or r.get("bot_id") != bot_id:
            continue
        key = str(r.get("mint"))
        ts = _int(r.get("ts_ms"), "ts_ms")
        net = _int(r.get("sol_in_lamports"), "sol_in_lamports") - _int(r.get("sol_out_lamports"), "sol_out_lamports")
        if ev == "send_intent":
            pos[key] = {"entry_ms": ts, "stake": _int(r.get("stake_lamports"), "stake_lamports"), "cash": net, "filled": 0}
            continue
        p = pos.get(key)
        if p is None:
            if ev == "fee":  # a fee booked after the close (superseded / prior_unresolved sell): a real cost, never dropped
                if key in last_closed:
                    last_closed[key]["realized"] += net
                elif unmatched is not None:
                    unmatched.append((ts, net))
            continue  # no intent in this ledger: no stake to report, never guessed
        p["cash"] += net
        closed = False
        failed = False
        if ev == "fill":
            if r.get("ok") is True:
                p["filled"] = 1
            else:
                closed = failed = True  # buy landed with an error
        elif ev in ("exit", "writeoff"):
            closed = True
            failed = ev == "writeoff" and not p["filled"]  # intent never filled (not sent / expired): failed send
        if closed:
            t = {"entry_ms": p["entry_ms"], "exit_ms": ts, "stake": p["stake"], "realized": p["cash"], "failed": int(failed)}
            out.append(t)
            last_closed[key] = t
            del pos[key]
    return out


def trade_rows(trades: Sequence[dict[str, int]], now_ms: int, hold: tuple[int, int] | None) -> list[dict[str, Any]]:
    """Apply the 24 h exit lag and the E2 hold-back (by ENTRY time, [start, end)), then project to the whitelist."""
    keep = []
    for t in trades:
        if t["exit_ms"] > now_ms - LAG_MS:
            continue
        if hold is not None and hold[0] <= t["entry_ms"] < hold[1]:
            continue
        keep.append(t)
    keep.sort(key=lambda t: (t["exit_ms"], t["entry_ms"]))
    rows = [{"entry_time": iso_ms(t["entry_ms"]), "exit_time": iso_ms(t["exit_ms"]), "stake_sol": sol(t["stake"]), "realized_sol": sol(t["realized"]), "failed": bool(t["failed"])} for t in keep]
    for r in rows:
        if tuple(r) != TRADE_FIELDS:
            raise ExtractError("trade row keys outside the whitelist")
    return rows


def read_ledger(path: Path) -> list[Any]:
    text = path.read_text()
    lines = text.split("\n")
    out = []
    for i, x in enumerate(lines):
        if x.strip():
            try:
                out.append(json.loads(x))
            except ValueError as exc:
                if i == len(lines) - 1:
                    continue  # unterminated final line: a writer mid-append; its row is younger than 24 h and cannot close a releasable trade
                raise ExtractError("unreadable ledger line: %s" % exc) from None
    return out


# ------------------------------------------------------------------------------------------------------------------ item 2: board
def is_sealed_book(book_id: str, extra: Sequence[str] = ()) -> bool:
    low = str(book_id).lower()
    return low in {e.lower() for e in extra} or any(m in low for m in SEALED_BOOK_MARKERS)


def pick_snapshot(snapshot_dir: Path, now_ms: int) -> tuple[Path, dict[str, Any]]:
    """Newest snapshot (a sub-directory holding board.json and board.md) whose generated_utc is at least 24 h before the run."""
    best = None
    for d in sorted(p for p in snapshot_dir.iterdir() if p.is_dir()):
        try:
            b = json.loads((d / "board.json").read_text())
            gen = parse_utc(b["generated_utc"])
        except (OSError, ValueError, KeyError, TypeError, ExtractError):
            continue
        if not (d / "board.md").is_file() or gen > now_ms - LAG_MS:
            continue
        if best is None or gen > best[0]:
            best = (gen, d, b)
    if best is None:
        raise ExtractError("no board snapshot at least 24 h old in %s" % snapshot_dir)
    return best[1], best[2]


def e2_books(board: dict[str, Any], extra: Sequence[str]) -> set[str]:
    """Book A (the control) and its twins: role starting "control" (osb_board renames twins "control (incl. gap-in-hold twin)" / "control (latency twin ..)"),
    the strict/twin ids of any pair that names one, and --e2-book ids."""
    held = {str(b.get("id")) for b in board.get("books", []) if str(b.get("role", "")).startswith("control")} | set(extra)
    for p in board.get("pairs", []) or []:
        ids = {str(p.get(k)) for k in ("twin", "strict") if p.get(k)}
        if ids & held:
            held |= ids
    return held


def _id_re(ids: Sequence[str]) -> "re.Pattern[str] | None":
    if not ids:
        return None
    return re.compile(r"(?<![A-Za-z0-9_.-])(?:" + "|".join(re.escape(i) for i in sorted(ids)) + r")(?![A-Za-z0-9_.])")


def check_disclosures(books: Sequence[dict[str, Any]], disclosures: dict[str, Any]) -> None:
    for b in books:
        v = disclosures.get(str(b.get("id")))
        if not (isinstance(v, list) and v and all(isinstance(x, str) and x for x in v)):
            raise ExtractError("no Am.9 disclosure lines for released book %s" % b.get("id"))


def filter_board(board: dict[str, Any], md: str, hold_on: bool, deny: Sequence[str], e2_extra: Sequence[str], disclosures: dict[str, Any] | None = None) -> tuple[dict[str, Any], str]:
    """Sealed-book removal always. While a hold is on: book A and its twins become number-free rows, their pairs go, the variants' control-derived fields are nulled,
    and board.md loses every line naming a held book (except a placeholder table row) and every figure derived from the control. "live" is never passed through."""
    sealed = {str(b.get("id")) for b in board.get("books", []) if is_sealed_book(b.get("id", ""), deny)}
    held = e2_books(board, e2_extra) - sealed if hold_on else set()
    out = json.loads(json.dumps(board))
    out["books"] = [b for b in out.get("books", []) if str(b.get("id")) not in sealed]
    pairs = [p for p in out.get("pairs", []) or [] if not ({str(p.get(k)) for k in ("twin", "strict")} & sealed)]
    if "pairs" in out:
        out["pairs"] = [p for p in pairs if not ({str(p.get(k)) for k in ("twin", "strict")} & held)]
    out["live"] = LIVE_WITHHELD
    for b in out["books"]:
        if str(b.get("id")) in held:
            b["rows"] = []
            b["state"] = WITHHELD
            b.pop("sim_off", None)
        elif held:
            for r in b.get("rows", []):
                r["vs_control"] = None
                d = r.get("disclosure")
                if isinstance(d, dict):
                    for k in ("control_forward", "paired_difference", "floor_holds"):
                        if k in d:
                            d[k] = None
    if disclosures is not None:
        check_disclosures(out["books"], disclosures)
        out["label"] = BOARD_NOTE
        out["am9_disclosures"] = {str(b["id"]): disclosures[str(b["id"])] for b in out["books"]}
    seal_re, held_re = _id_re(sorted(sealed)), _id_re(sorted(held))
    lines: list[str] = []
    skipping = False
    for ln in md.split("\n"):
        if ln.startswith("## "):
            m = re.match(r"## (\S+?), stake", ln)
            sec_book = m.group(1).rstrip(",") if m else None
            skipping = bool((sec_book in sealed or sec_book in held) or (seal_re and seal_re.search(ln)) or (held_re and held_re.search(ln)))
        if skipping:
            continue
        if ln.startswith("- Live:"):
            ln = "- Live: %s." % LIVE_WITHHELD
        if seal_re and seal_re.search(ln) and not ln.startswith("# "):
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")] if ln.startswith("| ") else []
        if held and cells and len(cells) == 12 and cells[0] not in ("book", "---"):
            if cells[0] in held:
                ln = "| %s | %s | %s |" % (cells[0], cells[1], WITHHELD)
            else:
                ln = "| " + " | ".join(cells[:10] + [WITHHELD, cells[11]]) + " |"  # the QP-floor cell derives from the control
        elif held_re and held_re.search(ln):
            continue
        if held and ln.startswith("- control, same stake:"):
            ln = "- control, same stake: %s." % WITHHELD
        if held and ln.startswith("## ") and "forward disclosure (QP floor:" in ln:
            ln = re.sub(r"\(QP floor: .*\)\s*$", "(QP floor: %s)" % WITHHELD, ln)
        lines.append(ln)
    text = "\n".join(lines)
    if disclosures is not None:
        head = ["# Open sim board (external copy)", "", "> " + BOARD_NOTE, ""]
        for b in out["books"]:
            head += ["- Am.9 disclosure, %s: %s" % (b["id"], x) for x in disclosures[str(b["id"])]]
        text = "\n".join(head) + "\n\n" + text
    return out, text


# ------------------------------------------------------------------------------------------------------------------ daily totals
TOTALS_RE = re.compile(r"^(\d{4}-\d\d-\d\d) (\S+): sends (\d+) landed (\d+) failed (\d+)(?: \(cost (-?[\d.]+) SOL\))?, exits (\d+), realized (-?[\d.]+) SOL after all fees, open (\d+) \(basis (-?[\d.]+) SOL\)(.*)$")
DAY_MS = 86_400_000


def parse_totals(text: str, bot_id: str, now_ms: int) -> list[dict[str, Any]]:
    """Per-UTC-day trade counts and realized SOL only (QP r2 on PR #1495; DEC-035 section 5 already reports exactly this set). The open count, the open basis and any
    missed-by-cause counts are parsed past and never written (open positions are never released; missed counts are live-host decision aggregates). A day that is not
    complete at the run instant is left out."""
    out = []
    for ln in text.splitlines():
        m = TOTALS_RE.match(ln.strip())
        if not m or m.group(2) != bot_id:
            continue
        if parse_utc(m.group(1) + "T00:00:00Z") + DAY_MS > now_ms:
            continue
        out.append({"day": m.group(1), "bot_id": m.group(2), "sends": int(m.group(3)), "landed": int(m.group(4)), "failed": int(m.group(5)),
                    "failed_cost_sol": m.group(6), "exits": int(m.group(7)), "realized_sol": m.group(8)})
    return sorted(out, key=lambda r: r["day"])


# ------------------------------------------------------------------------------------------------------------------ run
def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def git_commit() -> str:
    try:
        return subprocess.run(["git", "-C", str(Path(__file__).resolve().parent), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def load_disclosures(path: Path, sha: str) -> dict[str, Any]:
    if sha256_file(path) != sha.lower():
        raise ExtractError("disclosures file sha256 does not match --disclosures-sha256")
    d = json.loads(path.read_text())
    if not isinstance(d, dict):
        raise ExtractError("disclosures file must be an object {book_id: [lines]}")
    return d


def run(ledger: Path, snapshot_dir: Path, totals_file: Path, out_dir: Path, now_ms: int, release_e2: bool = False, bot_id: str = DEFAULT_BOT,
        e2_extra: Sequence[str] = (), deny: Sequence[str] = (), commit: str | None = None, first_read_decided: bool = False,
        disclosures: dict[str, Any] | None = None) -> dict[str, Any]:
    hold = hold_range(first_read_decided, release_e2)
    unmatched: list[tuple[int, int]] = []
    rows = trade_rows(closed_trades(read_ledger(ledger), bot_id, unmatched), now_ms, hold)
    snap, board = pick_snapshot(snapshot_dir, now_ms)
    board_out, md_out = filter_board(board, (snap / "board.md").read_text(), hold is not None, deny, e2_extra, disclosures)
    totals = parse_totals(totals_file.read_text(), bot_id, now_ms)
    unm: dict[str, int] = {}
    for ts, net in unmatched:
        if ts <= now_ms - LAG_MS:
            day = iso_ms(ts)[:10]
            unm[day] = unm.get(day, 0) + net
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / TRADES_NAME).write_text(json.dumps({"note": TRADES_NOTE}) + "\n" + "".join(json.dumps(r, sort_keys=False) + "\n" for r in rows))
    (out_dir / "board.json").write_text(json.dumps(board_out, indent=1) + "\n")
    (out_dir / "board.md").write_text(md_out)
    (out_dir / "daily-totals.json").write_text(json.dumps({"note": TRADES_NOTE, "bot_id": bot_id, "days": totals,
                                                            "unmatched_fee_sol_by_day": {d: sol(v) for d, v in sorted(unm.items())}}, indent=1) + "\n")
    hold_txt = "released" if hold is None else "withheld: entries in [%s, %s)" % (iso_ms(hold[0]), iso_ms(hold[1]))
    (out_dir / "NOTES.txt").write_text("%s\nfloor-closed-trades.jsonl line 1 is a note object; every other line has exactly the keys %s.\nboard.json, board.md: %s\nE2 hold: %s.\n"
                                       "Fees booked after a close are added to that trade's realized_sol; with no earlier trade they are in daily-totals.json unmatched_fee_sol_by_day.\n"
                                       % (TRADES_NOTE, ", ".join(TRADE_FIELDS), BOARD_NOTE, hold_txt))
    names = [TRADES_NAME, "board.json", "board.md", "daily-totals.json", "NOTES.txt"]
    manifest = {"files": {n: sha256_file(out_dir / n) for n in names}, "extractor_commit": commit or git_commit(), "extractor_sha256": sha256_file(Path(__file__).resolve()),
                "run_utc": iso_ms(now_ms), "ruling": RULING_NAME, "ruling_sha256": RULING_SHA256, "board_snapshot_generated_utc": board["generated_utc"],
                "e2_hold": None if hold is None else [iso_ms(hold[0]), iso_ms(hold[1])], "n_trades": len(rows)}
    (out_dir / "MANIFEST.json").write_text(json.dumps(manifest, indent=1) + "\n")
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="daily MAL-data release directory (QP-RULING-1124 items 1 and 2)")
    ap.add_argument("--ledger", type=Path, required=True)
    ap.add_argument("--snapshot-dir", type=Path, required=True)
    ap.add_argument("--totals-file", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--disclosures", type=Path, required=True)
    ap.add_argument("--disclosures-sha256", required=True)
    ap.add_argument("--now-ms", type=int, default=None)
    ap.add_argument("--e2-first-read-decided", action="store_true", help="manager only: pass ONLY after a DECIDED (not NOT_DECIDABLE) first E2 read is written; narrows the hold to [2026-11-26, 2026-12-03). If the read was NOT_DECIDABLE do not pass it; wait for the extended read and use --release-e2-window")
    ap.add_argument("--release-e2-window", action="store_true", help="manager only: final E2 read written; releases the whole hold")
    ap.add_argument("--bot-id", default=DEFAULT_BOT)
    ap.add_argument("--e2-book", action="append", default=[], help="extra book id held for E2 (book A and its twins are found from the board)")
    ap.add_argument("--deny-book", action="append", default=[], help="extra book id to remove")
    a = ap.parse_args(argv)
    now_ms = a.now_ms if a.now_ms is not None else int(time.time() * 1000)
    try:
        m = run(a.ledger, a.snapshot_dir, a.totals_file, a.out_dir, now_ms, a.release_e2_window, a.bot_id, a.e2_book, a.deny_book, None, a.e2_first_read_decided,
                load_disclosures(a.disclosures, a.disclosures_sha256))
    except (ExtractError, OSError, ValueError) as e:
        print("mal_data_extract: refused: %s" % e, file=sys.stderr)
        return 2
    print("mal_data_extract: %d trades, board %s, files %s" % (m["n_trades"], m["board_snapshot_generated_utc"], ", ".join("%s=%s" % (k, v[:12]) for k, v in m["files"].items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
