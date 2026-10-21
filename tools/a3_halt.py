#!/usr/bin/env python3
"""A3 halt application (QP ruling 2026-10-21 Q4/C-4/C-5): turn an a3_redeploy_check verdict into arena window ends, with no manager in the loop. Paper only; registry writes only.
Everything runs on mal-fast-0, where the arena registry is (scripts/ops/a3-daily.sh: dead-man check -> monitor -> check -> `apply`). No decision line crosses hosts.

WHY NOT PLAIN `arena_h5_score halt`. The scorer's halt (set_window_end) ends EVERY bot_id whose name starts with h5v1-boostfloor- or h5v2-boostclock-, at one instant. Under the
new rule a record-only redeploy PASS must NOT end the new-rule books, while the old rule still ends the books registered under it, and a book with another prefix (Q50) is not
reached at all. tools/arena_h5_score.py is pinned (sha256 dfe7f018... DEC-031 Am.1), so the selection lives here: an explicit BOOKS FILE (written before each clock):
  {"legacy_bot_ids": [...],   books under the OLD A3 rule: any A3 halt flag ends them at the flag's instant (verdict.flag_halt_ms = the run's run_utc)
   "new_rule_bot_ids": [...]} books under the Q3 rule: a HALT / FAIL / ERROR verdict ends them at verdict.halt_at_ms (the redeploy instant, or the flag's when earlier)
The write is the scorer's own: same registry lock, same atomic write, same semantics as set_window_end (window_end_ms and stopped_ms set once, an earlier end is never moved
later, lines are never removed).

STRICT, NOTHING WRITTEN ON ANY DOUBT (nonzero exit): the books file must have exactly those two keys, each a list of unique strings, no id in both; every listed id must be registered;
every registered bot_id that starts with "h5" must be in one of the lists (a book with no A3 coverage is an error); when a rule's instant applies and its list is empty it is an error.
The verdict is reduced to the DECISION_KEYS and validated exactly (known verdict; NONE has no instants; PASS has no halt_at_ms; HALT/FAIL/ERROR have halt_at_ms; halt_at_ms is not
after flag_halt_ms; instants are epoch ms in a sane range and not in the future). A hand-edited or inconsistent file is refused.

DEAD-MAN (C-4). `apply` appends one line to the APPLIED log for every decision it applied (also NONE/PASS). `deadman` reads it: if the newest applied run_utc is more than
DEADMAN_S (48 h, pinned) older than now, the new-rule books end at (that run_utc + DEADMAN_S). It needs no RPC and no daily record, and runs first in the job (and can run as its
own cheap job: `sh a3-daily.sh deadman`). It cannot fire before the first applied decision exists (there is no anchor); the manager's first run creates it. If this job never runs
at all, nothing on fast-0 runs either: the dead-man is only as alive as the host-side schedule.

Exit codes: 0 applied / nothing to apply; 2 invalid input (nothing written); 12 registry or log write failed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools import arena_h5_score as sc

DECISION_KEYS = ("run_utc", "verdict", "flag_halt_ms", "halt_at_ms", "reasons", "redeploys")
VERDICTS = ("NONE", "PASS", "HALT", "FAIL", "ERROR")
HARD = ("HALT", "FAIL", "ERROR")
BOOKS_KEYS = ("legacy_bot_ids", "new_rule_bot_ids")
DEADMAN_S = 48 * 3600
MIN_MS = 1_600_000_000_000
FUTURE_SLACK_MS = 5 * 60 * 1000


class Invalid(ValueError):
    """Input refused; nothing was written."""


def utc_ms(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() * 1000)


# ------------------------------------------------------------------------------------------------------------------------------ decisions
def decision_from_verdict(verdict: Mapping[str, Any]) -> dict[str, Any]:
    """The verdict reduced to exactly DECISION_KEYS. A verdict missing any of them is refused."""
    if not isinstance(verdict, Mapping):
        raise Invalid("verdict is not an object")
    missing = [k for k in DECISION_KEYS if k not in verdict]
    if missing:
        raise Invalid(f"verdict lacks {missing}")
    return {k: verdict[k] for k in DECISION_KEYS}


def validate_decision(d: Mapping[str, Any], now_ms: int) -> dict[str, Any]:
    if set(d) != set(DECISION_KEYS):
        raise Invalid(f"decision keys {sorted(d)} != {sorted(DECISION_KEYS)}")
    if d["verdict"] not in VERDICTS:
        raise Invalid(f"unknown verdict {d['verdict']!r}")
    try:
        run_ms = utc_ms(d["run_utc"])
    except (TypeError, ValueError) as exc:
        raise Invalid("run_utc is not YYYY-MM-DDTHH:MM:SSZ") from exc
    if run_ms > now_ms + FUTURE_SLACK_MS:
        raise Invalid("run_utc is in the future")
    for k in ("flag_halt_ms", "halt_at_ms"):
        v = d[k]
        if v is not None and (not isinstance(v, int) or isinstance(v, bool) or not MIN_MS <= v <= now_ms + FUTURE_SLACK_MS):
            raise Invalid(f"{k} is not an epoch-ms integer in range")
    f, h = d["flag_halt_ms"], d["halt_at_ms"]
    if d["verdict"] == "NONE" and (f is not None or h is not None):
        raise Invalid("NONE carries an instant")
    if d["verdict"] == "PASS" and h is not None:
        raise Invalid("PASS carries halt_at_ms")
    if d["verdict"] in HARD and h is None:
        raise Invalid(f"{d['verdict']} needs halt_at_ms")
    if f is not None and h is not None and h > f:
        raise Invalid("halt_at_ms is after flag_halt_ms")
    if not isinstance(d["reasons"], list) or not all(isinstance(x, str) for x in d["reasons"]) or not isinstance(d["redeploys"], list):
        raise Invalid("reasons / redeploys malformed")
    return dict(d)


# ------------------------------------------------------------------------------------------------------------------------------ books
def validate_books(books: Any, registry_ids: Sequence[str]) -> tuple[list[str], list[str]]:
    if not isinstance(books, dict) or set(books) != set(BOOKS_KEYS):
        raise Invalid(f"books file must be an object with exactly the keys {list(BOOKS_KEYS)}")
    lists = {}
    for k in BOOKS_KEYS:
        v = books[k]
        if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v) or len(set(v)) != len(v):
            raise Invalid(f"{k} must be a list of unique non-empty strings")
        lists[k] = v
    legacy, new = lists["legacy_bot_ids"], lists["new_rule_bot_ids"]
    if set(legacy) & set(new):
        raise Invalid(f"ids in both lists: {sorted(set(legacy) & set(new))}")
    reg = set(registry_ids)
    unregistered = sorted((set(legacy) | set(new)) - reg)
    if unregistered:
        raise Invalid(f"listed ids not registered: {unregistered}")
    uncovered = sorted(b for b in reg if b.startswith("h5") and b not in set(legacy) | set(new))
    if uncovered:
        raise Invalid(f"registered H5 books in neither list: {uncovered}")
    return legacy, new


def halt_books(registry: Path, at_ms: int, bot_ids: Sequence[str]) -> dict[str, Any]:
    """set_window_end semantics for an explicit list of bot_ids. Returns {ended: {id: window_end_ms}, unchanged: [...], unregistered: [...]}."""
    result: dict[str, Any] = {"ended": {}, "unchanged": [], "unregistered": []}

    def go():
        bots = sc.load_registry(registry)
        if not bots:
            raise Invalid("no books are registered")
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


def plan(d: Mapping[str, Any], legacy: Sequence[str], new: Sequence[str]) -> list[tuple[int, list[str], str]]:
    """[(at_ms, bot_ids, rule)] to apply, from a VALIDATED decision. Pure. An applicable rule with an empty list is an error (a halt that ends nothing)."""
    out: list[tuple[int, list[str], str]] = []
    if d["flag_halt_ms"] is not None:
        if not legacy:
            raise Invalid("an A3 flag was set but legacy_bot_ids is empty: the halt would end no book")
        out.append((int(d["flag_halt_ms"]), list(legacy), "legacy"))
    if d["verdict"] in HARD:
        if not new:
            raise Invalid(f"verdict {d['verdict']} but new_rule_bot_ids is empty: the halt would end no book")
        out.append((int(d["halt_at_ms"]), list(new), "new_rule"))
    return out


def _check(books: Any, registry: Path) -> tuple[list[str], list[str]]:
    bots = sc.load_registry(registry)
    if not bots:
        raise Invalid("no books are registered")
    return validate_books(books, list(bots))


def append_applied(path: Path, entry: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def apply(verdict: Mapping[str, Any], books: Any, registry: Path, now_ms: int | None = None) -> dict[str, Any]:
    """Validate everything first; write only if all of it is valid."""
    now = int(time.time() * 1000) if now_ms is None else now_ms
    d = validate_decision(decision_from_verdict(verdict), now)
    legacy, new = _check(books, registry)
    steps = plan(d, legacy, new)
    done = [{"rule": rule, "at_ms": at_ms, **halt_books(registry, at_ms, ids)} for at_ms, ids, rule in steps]
    return {"run_utc": d["run_utc"], "verdict": d["verdict"], "applied": done}


# ------------------------------------------------------------------------------------------------------------------------------ dead-man
def last_applied_run_ms(applied: Path) -> int | None:
    if not applied.exists():
        return None
    best = None
    for line in applied.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            ms = utc_ms(json.loads(line)["run_utc"])
        except (ValueError, KeyError, TypeError):
            continue
        best = ms if best is None or ms > best else best
    return best


def deadman(applied: Path, books: Any, registry: Path, now_ms: int | None = None) -> dict[str, Any]:
    now = int(time.time() * 1000) if now_ms is None else now_ms
    legacy, new = _check(books, registry)
    last = last_applied_run_ms(applied)
    if last is None:
        return {"deadman": "no anchor: no applied decision yet", "applied": []}
    if now - last <= DEADMAN_S * 1000:
        return {"deadman": "alive", "last_run_utc": datetime.fromtimestamp(last / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "applied": []}
    if not new:
        raise Invalid("dead-man due but new_rule_bot_ids is empty: it would end no book")
    at = last + DEADMAN_S * 1000
    return {"deadman": "FIRED", "end_ms": at, "applied": [{"rule": "deadman", "at_ms": at, **halt_books(registry, at, new)}]}


# ------------------------------------------------------------------------------------------------------------------------------ CLI
def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Apply an A3 verdict to the arena registry (explicit, validated book list), or run the dead-man check.")
    sub = p.add_subparsers(dest="cmd", required=True)
    a_ = sub.add_parser("apply")
    a_.add_argument("--verdict", required=True, help="the verdict JSON written by tools.a3_redeploy_check --verdict-out")
    a_.add_argument("--applied", required=True, help="append-only log of applied decisions (the dead-man's anchor)")
    d_ = sub.add_parser("deadman")
    d_.add_argument("--applied", required=True)
    for sp in (a_, d_):
        sp.add_argument("--books", required=True)
        sp.add_argument("--arena-root", required=True)
    a = p.parse_args(argv)
    registry = Path(a.arena_root) / "bots.json"
    try:
        books = json.loads(Path(a.books).read_text(encoding="utf-8"))
        if a.cmd == "deadman":
            out = deadman(Path(a.applied), books, registry)
        else:
            verdict = json.loads(Path(a.verdict).read_text(encoding="utf-8"))
            out = apply(verdict, books, registry)
    except (OSError, ValueError, KeyError) as exc:  # Invalid is a ValueError
        print(f"A3_HALT_REFUSED {type(exc).__name__}: {str(exc)[:300]}", file=sys.stderr)
        return 2
    try:
        if a.cmd == "apply":
            append_applied(Path(a.applied), {"run_utc": out["run_utc"], "verdict": out["verdict"], "applied_ms": int(time.time() * 1000), "ended": {x["rule"]: x.get("ended", {}) for x in out["applied"]}})
    except OSError as exc:
        print(f"A3_HALT_LOG_FAILED {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)
        return 12
    print(json.dumps(out, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
