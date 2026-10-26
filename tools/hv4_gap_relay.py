#!/usr/bin/env python3
"""hv4_gap_relay: the PRODUCER of the book's own tape gap records for the HV4 live C0 bridge (DEC-032 s5.2). PAPER ONLY: no RPC, no key, no wallet.

The frozen HV4 runner (9ed4c5ff) writes no gap row and must not change, so this separate read-only relay (research-0) turns the arena TAPE's gap and restart records into
a per-book stream `<out-dir>/transport/<bot_id>.tapegaps.jsonl` of {"kind", "ms"} rows that the forwarder can carry and the bridge reads (feed_break latches on them).

SEAL (QP ruling): KIND AND INSTANT ONLY. No tape line is ever parsed as a whole. Each line is matched with two regexes that extract only `type` and `t_recv_ms`; only a line
whose type is `gap` or `start` is JSON-parsed, and from it only `kind`, `data_loss` and `t_recv_ms` are read (the gap kinds are defined by tools/arena_paper.py: HARD_GAP_KINDS
process_restart / feed_restart / feed_start, or a row with data_loss true; a `start` row is a feed restart). A trade row contributes ONLY its instant, for the
gap_silence_ms rule (consecutive trade instants more than 60,000 ms apart = a `gap_silence` at the later instant, as the engine defines it). No outcome, price, mint,
reserve or slot field is read, and nothing is joined to any outcome. The engine's slot-jump rule (gap_slots) needs the slot field and is NOT covered here: that is a
declared residual. `takeover` is emitted when a gap row carries that kind.

Output rows (sorted by ms, deduplicated, rewritten atomically on each pass; idempotent): {"kind": <one of RELAY_KINDS>, "ms": int}.
Until this relay is installed and its output is carried to the bridge host, DEC-032 precondition 7 is NOT met for tape gaps.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from tools import hv4_live_c0 as c0

GAP_SILENCE_MS = 60_000  # tools/arena_paper.py ArenaConfig.gap_silence_ms
HARD_GAP_KINDS = ("process_restart", "feed_restart", "feed_start")  # tools/arena_paper.py HARD_GAP_KINDS (a test compares them)
RELAY_KINDS = ("data_loss", "process_restart", "feed_restart", "feed_start", "start", "takeover", "gap_silence")
TYPE_RE = re.compile(rb'"type"\s*:\s*"([a-z_]+)"')
MS_RE = re.compile(rb'"t_recv_ms"\s*:\s*(\d+)')
OUT_NAME = "{bot_id}.tapegaps.jsonl"


def scan(paths: list[Path]) -> list[dict[str, Any]]:
    """Kind and instant of every gap / restart record and silence breach across the day files, in order. Only gap and start lines are parsed."""
    out: dict[tuple[str, int], dict[str, Any]] = {}
    last_trade_ms: int | None = None
    for p in paths:
        with open(p, "rb") as fh:
            for line in fh:
                if not line.endswith(b"\n"):
                    break  # a write in progress
                tm = TYPE_RE.search(line)
                typ = tm.group(1).decode() if tm else "trade"
                mm = MS_RE.search(line)
                ms = int(mm.group(1)) if mm else None
                if typ in ("gap", "start"):
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(row, dict):
                        continue
                    at = row.get("t_recv_ms")
                    if isinstance(at, bool) or not isinstance(at, int):
                        continue
                    if typ == "start":
                        kind = "start"
                    else:
                        k = str(row.get("kind"))
                        kind = k if k in HARD_GAP_KINDS or k == "takeover" else ("data_loss" if row.get("data_loss") else None)
                    if kind:
                        out[(kind, at)] = {"kind": kind, "ms": at}
                elif typ == "trade" and ms is not None:
                    if last_trade_ms is not None and ms - last_trade_ms > GAP_SILENCE_MS:
                        out[("gap_silence", ms)] = {"kind": "gap_silence", "ms": ms}
                    last_trade_ms = ms if last_trade_ms is None else max(last_trade_ms, ms)
    return sorted(out.values(), key=lambda r: (r["ms"], r["kind"]))


def write_stream(path: Path, rows: list[dict[str, Any]]) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hv4_gap_relay", allow_abbrev=False)
    ap.add_argument("--tape-dir", required=True)
    ap.add_argument("--bot-id", required=True)
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args(argv)
    why = c0.bot_id_problem(a.bot_id) or c0.arm_refusal(a.bot_id)
    if why:
        print(f"hv4_gap_relay: REFUSED {why}", file=sys.stderr)
        return 2
    files = sorted(p for p in Path(a.tape_dir).glob("*.jsonl") if p.is_file() and not p.is_symlink())
    rows = scan(files)
    d = Path(a.out_dir) / "transport"
    d.mkdir(parents=True, exist_ok=True)
    write_stream(d / OUT_NAME.format(bot_id=a.bot_id), rows)
    print(json.dumps({"relay": "hv4_gap_relay", "bot_id": a.bot_id, "gap_rows": len(rows)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
