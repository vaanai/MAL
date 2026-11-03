"""slowbot live-intent rows. PAPER side only: no wallet, no key, no send.

When the runner's H arm opens a paper BUY and the flag is on, one row is appended to OUT/live-intents.jsonl (fsynced). The flag is off by
default; with it off nothing here is called and the runner's files are byte-identical to the paper-only runner. The live executor on
mal-fast-0 (tools/slowbot/live_executor.py) reads the pushed copy. Each row has a stable `intent_id` (mint + decision ts) so a push that
repeats a row, or an executor that re-reads it, can dedupe.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

SCHEMA = "slowbot-live-intent.v1"
FILENAME = "live-intents.jsonl"


def intent_id(mint: str, decision_ts: float) -> str:
    return f"{mint}:{int(decision_ts)}"


def build_intent(candidate: dict, pos: dict, *, decision_ts: float, conviction, now: float) -> dict:
    """`pos` is the paper H position PositionManager.open returned (its entry quote is the paper fill)."""
    return {
        "schema": SCHEMA, "intent_id": intent_id(candidate["mint"], decision_ts), "arm": "H",
        "mint": candidate["mint"], "symbol": candidate.get("symbol"), "pool": candidate.get("pool"), "venue": candidate.get("venue"),
        "candidate_id": candidate.get("candidate_id"), "decision_ts": decision_ts, "written_ts": now, "conviction": conviction,
        "paper_position_id": pos.get("id"),
        "paper_entry": {"in_lamports": pos.get("cost_lamports"), "out_tokens": pos.get("tokens"), "entry_unit": pos.get("entry_unit"),
                        "liq_usd": pos.get("entry_liq_usd"), "opened_t": pos.get("opened_t")},
    }


def append_intent(out_dir, row: dict) -> Path:
    path = Path(out_dir) / FILENAME
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, (json.dumps(row, sort_keys=True) + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    return path


def read_intents(path) -> list[dict]:
    """Complete JSON lines only: a torn final line (no newline yet) and any bad line are skipped."""
    try:
        raw = Path(path).read_bytes()
    except FileNotFoundError:
        return []
    out = []
    for line in raw.split(b"\n")[:-1]:  # the last element is "" or a torn partial line
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict) and r.get("schema") == SCHEMA and isinstance(r.get("mint"), str):
            out.append(r)
    return out
