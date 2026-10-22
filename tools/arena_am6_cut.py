#!/usr/bin/env python3
"""Attempt-timing cutter for DEC-031 Amendment 6 (QP ruling on PR #801: the pinned path from a book's ledger to tools/arena_am6_exclusions.py).

    python -m tools.arena_am6_cut --ledger-root DIR --registry FILE --bot BOT --as-of-ms MS --out cut.jsonl

Reads the SAME ledger rows tools/arena_score.py reads for that bot (Ledger.scan_bot) and writes ONE line per attempt with EXACTLY these keys, nothing else
(no price, stake, fill, leg, P&L or mint is carried over; the schema is the allowlist of arena_am6_exclusions.ALLOWED):
    attempt_id       the position_id (open rows) or the refused row's position_id
    decided_ms       decision_ms.entry (the engine's write instant, the scorer's own clock)
    exit_ms          decision_ms.exit of the position's trade / excluded rows (null while no close row exists); a refused buy has exit_ms = decided_ms
    open_at_restart  true when the position's close row is an excluded row with exclusion_reason engine_restart / process_restart (DEC-031 Am.6 item 4);
                     the restart gap instants themselves come from the notebook entry (--restarts of arena_am6_exclusions), not from the ledger
    s14_excluded     the scorer's section 1.4 exclusion of that attempt AT THE REGISTERED deciding_stake (see below)
Rows decided before the registered clock or after --as-of-ms are not attempts of the read. Output order: (decided_ms, attempt_id). The CLI writes the
attempt lines to --out and prints {"type":"sha256",...} (sha256 over the attempt lines, each + "\\n") to stdout; the file itself holds only attempt lines so that
arena_am6_exclusions reads it unchanged.

s14_excluded and arena_score.py. arena_score.py (pinned by sha256) has no function that returns a per-attempt exclusion: its step 3 lives inline in _score_bot.
It cannot be factored without changing the pinned file, so this module mirrors those four conditions on the deciding stake's rows and takes the two constants
from the scorer itself (DATA_REFUSALS is imported, not copied). The mirror is NOT trusted: parity_guard() runs the scorer's own score_bot on the same rows and
compares the scorer's per-reason excluded counts and its data-refusal count with this module's, and the wrapper (tools/arena_am6_read.py) refuses to score on any
difference. The four conditions (arena_score._score_bot, "3. DEC-031 exclusions"):
    a trade row of the position whose late_print row has gap_overlap true                         (late_print_gap)
    a trade row with "none" in v_src.entry / v_src.exit / v_src.span                               (missing_v)
    a row of kind "excluded" at the deciding stake                                                  (engine exclusion_reason)
    a refused buy whose reason is in arena_score.DATA_REFUSALS                                      (no_prints / no_feed_rows / clock_not_ready)
Exit codes: 0 ok, 2 refused (torn ledger lines, unknown bot, a registry without deciding_stake, duplicate attempt).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from typing import Any, Mapping, Sequence

from tools import arena_score as S
from tools.arena_paper import Ledger, load_registry

SCHEMA = "arena_am6_cut_v1"
RESTART_REASONS = ("engine_restart", "process_restart")


class Refused(Exception):
    pass


def canon(o: Any) -> str:
    return json.dumps(o, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _entry(r: Mapping[str, Any]) -> int:
    return int(r["decision_ms"]["entry"])


def s14_reasons(rows: Sequence[Mapping[str, Any]], stake: float) -> dict[str, str]:
    """{position_id or refused id: the scorer's section 1.4 exclusion reason at `stake`} for the rows given (mirror of arena_score step 3; see the module docstring)."""
    late_gap = {r["position_id"] for r in rows if r["kind"] == "late_print" and r.get("gap_overlap")}
    out: dict[str, str] = {}
    for r in rows:
        k = r["kind"]
        if k == "trade" and r["stake_sol"] == stake:
            vs = r.get("v_src") or {}
            if r["position_id"] in late_gap:
                out[r["position_id"]] = "late_print_gap"
            elif "none" in (vs.get("entry"), vs.get("exit")) or "none" in (vs.get("span") or {}):
                out[r["position_id"]] = "missing_v"
        elif k == "excluded" and r["stake_sol"] == stake:
            out[r["position_id"]] = r["exclusion_reason"]
        elif k == "refused" and r["reason"] in S.DATA_REFUSALS:
            out[r["position_id"]] = r["reason"]
    return out


def attempts_from_rows(rows: Sequence[Mapping[str, Any]], clock_ms: int, stake: float, as_of_ms: int | None = None) -> list[dict]:
    rows = [r for r in rows if r.get("kind") in ("open", "refused", "trade", "excluded", "late_print") and _entry(r) >= clock_ms]
    s14 = s14_reasons(rows, stake)
    closes: dict[str, list[Mapping[str, Any]]] = {}
    for r in rows:
        if r["kind"] in ("trade", "excluded"):
            closes.setdefault(r["position_id"], []).append(r)
    out, seen = [], set()
    for r in rows:
        if r["kind"] not in ("open", "refused"):
            continue
        pid = r["position_id"]
        if pid in seen:
            raise Refused(f"duplicate attempt {pid}")
        seen.add(pid)
        d = _entry(r)
        if as_of_ms is not None and d > as_of_ms:
            continue
        if r["kind"] == "refused":
            e: int | None = d
            restart = False
        else:
            exits = [c["decision_ms"].get("exit") for c in closes.get(pid, [])]
            e = None if not exits or any(x is None for x in exits) else max(int(x) for x in exits)
            if e is not None and as_of_ms is not None and e > as_of_ms:
                e = None  # closed after the cut instant: open as of --as-of-ms, so an earlier read can be re-run
            restart = any(c["kind"] == "excluded" and c.get("exclusion_reason") in RESTART_REASONS for c in closes.get(pid, []))
        out.append({"attempt_id": pid, "decided_ms": d, "exit_ms": e, "open_at_restart": restart, "s14_excluded": pid in s14})
    return sorted(out, key=lambda a: (a["decided_ms"], a["attempt_id"]))


def render_cut(attempts: Sequence[Mapping[str, Any]]) -> tuple[list[str], str]:
    """(attempt lines, sha256 line). The sha covers the attempt lines, each + newline."""
    lines = [canon(a) for a in attempts]
    sha = hashlib.sha256("".join(ln + "\n" for ln in lines).encode()).hexdigest()
    return lines, canon({"type": "sha256", "schema": SCHEMA, "lines": len(lines), "sha256": sha})


def parity_guard(bot_id: str, reg: Mapping[str, Mapping[str, Any]], rows: Sequence[Mapping[str, Any]], now_ms: int, stake: float,
                 drop_ids: Sequence[str] = ()) -> dict[str, Any]:
    """Run the scorer's OWN score_bot (natural boundary, same rows) and compare its exclusions at `stake` with s14_reasons over the attempts it covers.
    The boundary is the scorer's natural boundary on the rows given (the wrapper passes the rows it scores, so it is the wrapper's B and the wrapper checks that). Returns {"ok": bool, "scorer": {...}, "cutter": {...}}. Not ok means the mirror drifted from the pinned scorer: the read must not proceed."""
    drop = set(drop_ids)  # attempts decided in excluded hours: the wrapper never scores them, so the guard does not either
    rows = [r for r in rows if r.get("position_id") not in drop]
    res = S.score_bot(bot_id, reg, rows, now_ms, None, S.SETTLE_MS)
    b = res.get("read_boundary_ms")
    if b is None or "books" not in res:
        return {"ok": True, "note": "scorer has no boundary yet", "scorer": {}, "cutter": {}}
    clock = reg[bot_id]["clock_start_ms"]
    covered = [r for r in rows if r.get("kind") in ("open", "refused", "trade", "excluded", "late_print") and clock <= _entry(r) < b]
    allm = Counter(s14_reasons(covered, stake).values())
    n_ref = sum(allm.pop(reason, 0) for reason in S.DATA_REFUSALS)
    mine = allm
    bk = res["books"].get(str(stake), {"excluded": {}, "refused": 0})
    theirs = Counter(bk["excluded"])
    ok = dict(mine) == dict(theirs) and n_ref == bk["refused"]
    return {"ok": ok, "boundary_ms": b, "scorer": {"excluded": dict(theirs), "refused_data": bk["refused"]}, "cutter": {"excluded": dict(mine), "refused_data": n_ref}}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ledger-root", default=S.LEDGER_ROOT)
    ap.add_argument("--registry", default=S.REGISTRY_PATH)
    ap.add_argument("--bot", required=True)
    ap.add_argument("--as-of-ms", required=True, type=int)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    try:
        reg = load_registry(a.registry)
        if a.bot not in reg:
            raise Refused(f"{a.bot}: not in the registry")
        e = reg[a.bot]
        if e.get("deciding_stake") is None:
            raise Refused(f"{a.bot}: registry line lacks deciding_stake")
        rows, problems = Ledger(a.ledger_root).scan_bot(a.bot)
        if problems:
            raise Refused(f"{a.bot}: {len(problems)} torn or unparseable ledger line(s): no cut")
        lines, shaline = render_cut(attempts_from_rows(rows, e["clock_start_ms"], e["deciding_stake"], a.as_of_ms))
    except Exception as ex:  # noqa: BLE001 - any failure is a refusal (exit 2), never a traceback exit 1
        print(f"REFUSED: {ex}", file=sys.stderr)
        return 2
    with open(a.out, "w") as fh:
        fh.write("".join(ln + "\n" for ln in lines))
    print(shaline)
    return 0


if __name__ == "__main__":
    sys.exit(main())
