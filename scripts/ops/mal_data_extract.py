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
                  Optional missed counts by cause on the same line: `missed lane_budget 3, lane_max_open 2` (or `missed: cause=n`). The format of missed counts is an ASSUMPTION; absent = {}.

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
E2_END_MS = calendar.timegm((2026, 11, 26, 0, 0, 0)) * 1000
DEFAULT_BOT = "floor-lane-1"
TRADE_FIELDS = ("entry_time", "exit_time", "stake_sol", "realized_sol")  # the whitelist: the only keys a trade row may carry
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


def sol(lamports: int) -> float:
    return lamports / 1e9


# ------------------------------------------------------------------------------------------------------------------ item 1: closed trades
def closed_trades(rows: Sequence[Any], bot_id: str) -> list[dict[str, int]]:
    """Fold one bot's ledger rows to closed trades (lamports, ms). A trade closes on exit, writeoff or a failed buy fill (failed sends and failed landings are
    included at real cost, as in lane_daily_report). Open positions, rows with no send_intent and other bots' rows are never returned."""
    pos: dict[str, dict[str, int]] = {}
    out: list[dict[str, int]] = []
    seen: set[str] = set()
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
            continue  # no intent in this ledger: no stake to report, never guessed
        p["cash"] += net
        closed = False
        if ev == "fill":
            if r.get("ok") is True:
                p["filled"] = 1
            else:
                closed = True
        elif ev in ("exit", "writeoff"):
            closed = True
        if closed:
            out.append({"entry_ms": p["entry_ms"], "exit_ms": ts, "stake": p["stake"], "realized": p["cash"]})
            del pos[key]
    return out


def trade_rows(trades: Sequence[dict[str, int]], now_ms: int, release_e2: bool) -> list[dict[str, Any]]:
    """Apply the 24 h exit lag and the E2 hold-back, then project to the whitelist."""
    keep = []
    for t in trades:
        if t["exit_ms"] > now_ms - LAG_MS:
            continue
        if not release_e2 and E2_START_MS <= t["entry_ms"] < E2_END_MS:
            continue
        keep.append(t)
    keep.sort(key=lambda t: (t["exit_ms"], t["entry_ms"]))
    rows = [{"entry_time": iso_ms(t["entry_ms"]), "exit_time": iso_ms(t["exit_ms"]), "stake_sol": sol(t["stake"]), "realized_sol": sol(t["realized"])} for t in keep]
    for r in rows:
        assert tuple(r) == TRADE_FIELDS
    return rows


def read_ledger(path: Path) -> list[Any]:
    out = []
    for x in path.read_text().splitlines():
        if x.strip():
            try:
                out.append(json.loads(x))
            except ValueError as exc:
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
    """Book A (the control) and its twins: role control, the strict/twin ids of any pair that names one, and --e2-book ids."""
    held = {str(b.get("id")) for b in board.get("books", []) if b.get("role") == "control"} | set(extra)
    for p in board.get("pairs", []) or []:
        ids = {str(p.get(k)) for k in ("twin", "strict") if p.get(k)}
        if ids & held:
            held |= ids
    return held


def _e2_day(day: str) -> bool:
    # whole UTC days that overlap the E2 entry window (conservative: 2026-11-18 .. 2026-11-25)
    return "2026-11-18" <= str(day)[:10] <= "2026-11-25"


def filter_board(board: dict[str, Any], md: str, release_e2: bool, deny: Sequence[str], e2_extra: Sequence[str]) -> tuple[dict[str, Any], str]:
    """Remove sealed-book rows (json books/pairs and every md line naming them); unless release_e2, withhold held-book trade, per-day and open rows touching E2."""
    sealed = {str(b.get("id")) for b in board.get("books", []) if is_sealed_book(b.get("id", ""), deny)}
    held = set() if release_e2 else e2_books(board, e2_extra)
    drop_tids: set[str] = set()
    out = json.loads(json.dumps(board))
    out["books"] = [b for b in out.get("books", []) if str(b.get("id")) not in sealed]
    if "pairs" in out:
        out["pairs"] = [p for p in out["pairs"] if not ({str(p.get(k)) for k in ("twin", "strict")} & sealed)]
    for b in out["books"]:
        if str(b.get("id")) not in held:
            continue
        for r in b.get("rows", []):
            keep = []
            for t in r.get("trades", []):
                if E2_START_MS <= parse_utc(t["entry_utc"]) < E2_END_MS:
                    drop_tids.add(str(t.get("tid")))
                else:
                    keep.append(t)
            r["trades"] = keep
            r["per_day"] = [x for x in r.get("per_day", []) if not _e2_day(x.get("day", ""))]
            for o in r.get("open", []):
                if E2_START_MS <= parse_utc(o["entry_utc"]) < E2_END_MS:
                    drop_tids.add(str(o.get("tid")))
            r["open"] = [o for o in r.get("open", []) if str(o.get("tid")) not in drop_tids]
    lines: list[str] = []
    section_book = None
    skipping = False
    for ln in md.split("\n"):
        m = re.match(r"## (\S+?), stake", ln)
        if ln.startswith("## "):
            section_book = m.group(1).rstrip(",") if m else None
            skipping = section_book in sealed  # a sealed book's whole section, tables and bullets included
        if skipping:
            continue
        if any(re.search(r"(?<![A-Za-z0-9_.-])" + re.escape(s) + r"(?![A-Za-z0-9_.])", ln) for s in sealed) and not ln.startswith("# "):
            continue
        cell0 = re.match(r"\| (\S+) \|", ln)
        if cell0 and cell0.group(1) in drop_tids:
            continue
        if cell0 and section_book in held and re.fullmatch(r"\d{4}-\d\d-\d\d", cell0.group(1)) and _e2_day(cell0.group(1)):
            continue
        if any(ln.startswith("- %s entered" % t) for t in drop_tids):
            continue
        lines.append(ln)
    return out, "\n".join(lines)


# ------------------------------------------------------------------------------------------------------------------ daily totals
TOTALS_RE = re.compile(r"^(\d{4}-\d\d-\d\d) (\S+): sends (\d+) landed (\d+) failed (\d+)(?: \(cost (-?[\d.]+) SOL\))?, exits (\d+), realized (-?[\d.]+) SOL after all fees, open (\d+) \(basis (-?[\d.]+) SOL\)(.*)$")
MISSED_RE = re.compile(r"missed:?\s+(.*)$")


def parse_totals(text: str, bot_id: str) -> list[dict[str, Any]]:
    out = []
    for ln in text.splitlines():
        m = TOTALS_RE.match(ln.strip())
        if not m or m.group(2) != bot_id:
            continue
        missed: dict[str, int] = {}
        mm = MISSED_RE.search(m.group(11))
        if mm:
            for part in re.split(r"[,;]\s*", mm.group(1).strip().rstrip(".")):
                k = re.fullmatch(r"([A-Za-z0-9_:.-]+?)(?:=|\s+)(\d+)", part.strip())
                if k:
                    missed[k.group(1)] = int(k.group(2))
        out.append({"day": m.group(1), "bot_id": m.group(2), "sends": int(m.group(3)), "landed": int(m.group(4)), "failed": int(m.group(5)),
                    "failed_cost_sol": m.group(6), "exits": int(m.group(7)), "realized_sol": m.group(8), "open": int(m.group(9)), "open_basis_sol": m.group(10),
                    "missed_by_cause": missed})
    return sorted(out, key=lambda r: r["day"])


# ------------------------------------------------------------------------------------------------------------------ run
def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def git_commit() -> str:
    try:
        return subprocess.run(["git", "-C", str(Path(__file__).resolve().parent), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def run(ledger: Path, snapshot_dir: Path, totals_file: Path, out_dir: Path, now_ms: int, release_e2: bool = False, bot_id: str = DEFAULT_BOT,
        e2_extra: Sequence[str] = (), deny: Sequence[str] = (), commit: str | None = None) -> dict[str, Any]:
    rows = trade_rows(closed_trades(read_ledger(ledger), bot_id), now_ms, release_e2)
    snap, board = pick_snapshot(snapshot_dir, now_ms)
    board_out, md_out = filter_board(board, (snap / "board.md").read_text(), release_e2, deny, e2_extra)
    totals = parse_totals(totals_file.read_text(), bot_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / TRADES_NAME).write_text("".join(json.dumps(r, sort_keys=False) + "\n" for r in rows))
    (out_dir / "board.json").write_text(json.dumps(board_out, indent=1) + "\n")
    (out_dir / "board.md").write_text(md_out)
    (out_dir / "daily-totals.json").write_text(json.dumps({"note": TRADES_NOTE, "bot_id": bot_id, "days": totals}, indent=1) + "\n")
    (out_dir / "NOTES.txt").write_text("floor-closed-trades.jsonl, daily-totals.json: %s\nboard.json, board.md: %s\nE2 window (entries in [2026-11-18T12:00:00Z, 2026-11-26T00:00:00Z)) %s.\n"
                                       % (TRADES_NOTE, BOARD_NOTE, "RELEASED" if release_e2 else "withheld"))
    names = [TRADES_NAME, "board.json", "board.md", "daily-totals.json", "NOTES.txt"]
    manifest = {"files": {n: sha256_file(out_dir / n) for n in names}, "extractor_commit": commit or git_commit(), "run_utc": iso_ms(now_ms),
                "ruling": RULING_NAME, "ruling_sha256": RULING_SHA256, "board_snapshot_generated_utc": board["generated_utc"], "e2_window_released": release_e2,
                "n_trades": len(rows)}
    (out_dir / "MANIFEST.json").write_text(json.dumps(manifest, indent=1) + "\n")
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="daily MAL-data release directory (QP-RULING-1124 items 1 and 2)")
    ap.add_argument("--ledger", type=Path, required=True)
    ap.add_argument("--snapshot-dir", type=Path, required=True)
    ap.add_argument("--totals-file", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--now-ms", type=int, default=None)
    ap.add_argument("--release-e2-window", action="store_true", help="manager only, after the brake E2 read is written on main")
    ap.add_argument("--bot-id", default=DEFAULT_BOT)
    ap.add_argument("--e2-book", action="append", default=[], help="extra book id held for E2 (book A and its pair twins are found from the board)")
    ap.add_argument("--deny-book", action="append", default=[], help="extra book id to remove")
    a = ap.parse_args(argv)
    now_ms = a.now_ms if a.now_ms is not None else int(time.time() * 1000)
    try:
        m = run(a.ledger, a.snapshot_dir, a.totals_file, a.out_dir, now_ms, a.release_e2_window, a.bot_id, a.e2_book, a.deny_book)
    except (ExtractError, OSError) as e:
        print("mal_data_extract: refused: %s" % e, file=sys.stderr)
        return 2
    print("mal_data_extract: %d trades, board %s, files %s" % (m["n_trades"], m["board_snapshot_generated_utc"], ", ".join("%s=%s" % (k, v[:12]) for k, v in m["files"].items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
