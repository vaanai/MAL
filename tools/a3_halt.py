#!/usr/bin/env python3
"""A3 halt chaining (QP ruling 2026-10-21 Q4): turn an a3_redeploy_check verdict into arena window ends, with no manager in the loop. Paper only; registry writes only.

WHY NOT PLAIN `arena_h5_score halt`. The scorer's halt (set_window_end) ends EVERY bot_id whose name starts with h5v1-boostfloor- or h5v2-boostclock-, at one instant. Under the
new rule a record-only redeploy PASS must NOT end the new-rule books, while the old rule still ends the books registered under it, and a book with another prefix (Q50) is not
reached at all. tools/arena_h5_score.py is pinned (sha256 dfe7f018... DEC-031 Am.1), so the selection lives here: an explicit book list file (pinned before the clock):
  {"legacy_bot_ids": [...],   books under the OLD A3 rule: any A3 halt flag ends them at the flag's instant (verdict.flag_halt_ms = the run's run_utc)
   "new_rule_bot_ids": [...]} books under the Q3 rule: only a HALT verdict ends them, at verdict.halt_at_ms (the redeploy instant for a FAIL/ERROR/timeout)
The write is the scorer's own: same registry lock, same atomic write, same semantics as set_window_end (window_end_ms and stopped_ms set once, an earlier end is never moved
later, lines are never removed). An id in the list that is not registered yet is reported and skipped (a book registered later must be in the list before its clock).
CROSS-HOST. The A3 monitor and check run on mal-research-0; the arena registry is on mal-fast-0. The research-0 job prints ONE line `A3_HALT_DECISION <json>` (decision_line: run_utc,
verdict, the two instants in epoch ms, reasons, redeploys) and nothing else crosses. The fast-0 job takes that line (or its JSON) as an argument: `--decision`. Why this and not
re-deriving: re-running the decision on fast-0 would need the monitor pins, the state file (PASS/FAIL memory) and public RPC there, and a second evaluation could disagree with the
first (a different sample, a later clock). One evaluation, one printed decision, strictly validated here (parse_decision), applied idempotently (an earlier end is never moved later,
so applying the same line twice, or an older line after a newer one, changes nothing). A line that is malformed or hand-edited to a non-epoch instant is refused (exit 2).
Prints one JSON line. Exit 0 whatever was applied; 2 for an unreadable verdict, list or registry.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools import arena_h5_score as sc


def halt_books(registry: Path, at_ms: int, bot_ids: Sequence[str]) -> dict[str, Any]:
    """set_window_end semantics for an explicit list of bot_ids. Returns {ended: {id: window_end_ms}, unchanged: [...], unregistered: [...]}."""
    result: dict[str, Any] = {"ended": {}, "unchanged": [], "unregistered": []}

    def go():
        bots = sc.load_registry(registry)
        if not bots:
            raise ValueError("no books are registered")
        for bot_id in bot_ids:
            e = bots.get(bot_id)
            if e is None:
                result["unregistered"].append(bot_id)
                continue
            cur = e.get("window_end_ms")
            if cur is None or at_ms < cur:
                e["window_end_ms"] = int(at_ms)
                e["stopped_ms"] = int(at_ms)
                result["ended"][bot_id] = int(at_ms)
            else:
                result["unchanged"].append(bot_id)
        sc._atomic_write_json(registry, {"bots": bots})

    sc._with_registry_lock(registry, go)
    return result


def plan(verdict: Mapping[str, Any], books: Mapping[str, Any]) -> list[tuple[int, list[str], str]]:
    """[(at_ms, bot_ids, rule)] to apply. Pure."""
    out: list[tuple[int, list[str], str]] = []
    legacy, new = list(books.get("legacy_bot_ids") or []), list(books.get("new_rule_bot_ids") or [])
    if verdict.get("flag_halt_ms") is not None and legacy:
        out.append((int(verdict["flag_halt_ms"]), legacy, "legacy"))
    if verdict.get("verdict") in ("HALT", "FAIL", "ERROR") and verdict.get("halt_at_ms") is not None and new:
        out.append((int(verdict["halt_at_ms"]), new, "new_rule"))
    return out


DECISION_PREFIX = "A3_HALT_DECISION "
DECISION_KEYS = ("run_utc", "verdict", "flag_halt_ms", "halt_at_ms", "reasons", "redeploys")


def decision_from_verdict(verdict: Mapping[str, Any]) -> dict[str, Any]:
    """The small, self-contained halt decision that crosses hosts (research-0 -> fast-0). Instants are epoch ms; books are chosen on the arena host from its own books file."""
    return {"run_utc": verdict.get("run_utc"), "verdict": verdict.get("verdict"), "flag_halt_ms": verdict.get("flag_halt_ms"), "halt_at_ms": verdict.get("halt_at_ms"),
            "reasons": [str(r)[:300] for r in (verdict.get("reasons") or [])][:5], "redeploys": verdict.get("redeploys") or []}


def needs_halt(decision: Mapping[str, Any]) -> bool:
    return decision.get("flag_halt_ms") is not None or decision.get("halt_at_ms") is not None


def decision_line(verdict: Mapping[str, Any]) -> str:
    return DECISION_PREFIX + json.dumps(decision_from_verdict(verdict), sort_keys=True)


def parse_decision(text: str) -> dict[str, Any]:
    """Accepts the bare JSON or the whole `A3_HALT_DECISION <json>` line (a job log line copied as is). Strict: anything malformed raises ValueError, nothing is guessed."""
    t = text.strip()
    if DECISION_PREFIX.strip() in t:
        t = t.split(DECISION_PREFIX.strip(), 1)[1].strip()
    d = json.loads(t)
    if not isinstance(d, dict) or d.get("verdict") not in ("NONE", "PASS", "HALT", "FAIL", "ERROR"):
        raise ValueError("not an A3 decision")
    for k in ("flag_halt_ms", "halt_at_ms"):
        v = d.get(k)
        if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v < 1_600_000_000_000):
            raise ValueError(f"{k} is not an epoch-ms integer")
    if d["verdict"] in ("HALT", "FAIL", "ERROR") and d.get("halt_at_ms") is None:
        raise ValueError("a hard verdict needs halt_at_ms")
    return d


def apply(verdict: Mapping[str, Any], books: Mapping[str, Any], registry: Path) -> dict[str, Any]:
    done = []
    for at_ms, ids, rule in plan(verdict, books):
        done.append({"rule": rule, "at_ms": at_ms, **halt_books(registry, at_ms, ids)})
    return {"run_utc": verdict.get("run_utc"), "verdict": verdict.get("verdict"), "applied": done}


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Apply an A3 decision to the arena registry (explicit book list), or print the decision line for it.")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--verdict", help="verdict JSON file (same host as the check): apply it")
    src.add_argument("--decision", help="the A3_HALT_DECISION line (or its JSON) printed by the research-0 job: apply it (the cross-host route)")
    src.add_argument("--emit-decision", metavar="VERDICT_FILE", help="research-0 side: print the A3_HALT_DECISION line; exit 20 if a halt is needed, 0 if not, 2 if unreadable")
    p.add_argument("--books")
    p.add_argument("--arena-root")
    a = p.parse_args(argv)
    if a.emit_decision:
        try:
            verdict = json.loads(Path(a.emit_decision).read_text(encoding="utf-8"))
            line = decision_line(verdict)
            need_halt = needs_halt(decision_from_verdict(verdict))
        except (OSError, ValueError) as exc:
            print(f"A3_HALT_ERROR {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)
            return 2
        print(line)
        return 20 if need_halt else 0
    try:
        if not a.books or not a.arena_root:
            raise ValueError("--books and --arena-root are required to apply")
        verdict = json.loads(Path(a.verdict).read_text(encoding="utf-8")) if a.verdict else parse_decision(a.decision)
        books = json.loads(Path(a.books).read_text(encoding="utf-8"))
        out = apply(verdict, books, Path(a.arena_root) / "bots.json")
    except (OSError, ValueError, KeyError) as exc:
        print(f"A3_HALT_ERROR {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)
        return 2
    print(json.dumps(out, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
