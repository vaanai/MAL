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
own cheap job: `sh a3-daily.sh deadman`). Per new-rule book the anchor is the later of the newest applied run and the book's registered_at_ms; with NO applied log it is the
registration instant alone, so a lost or never-created log ends the books 48 h after registration (the exit stays 0 so the daily can create the log). If this job never runs
at all, nothing on fast-0 runs either: the dead-man is only as alive as the host-side schedule.

SUCCESSOR BOOKS, FEES-PROGRAM CHECK (QP-RULING-1024-redeploy item (2); opt-in). The books file MAY carry a third key, "fees_check_bot_ids": [...] (unique strings, each also in
new_rule_bot_ids, never one of FEES_CHECK_REFUSED_IDS). Without it (or with an empty list) everything below is byte-identical to the rule as merged. With it, and only when the verdict
carries a valid `fees_check` sub-verdict (tools.a3_redeploy_check adds one only when the fees deploy slot is the record's ONLY change), the books of that list follow the sub-verdict
instead of the main HALT: PASS = no end (record only); FAIL / ERROR = end at fees_check.halt_at_ms (never after the flag's instant). Every other new-rule book keeps the main HALT.
A verdict without the sub-verdict ends the listed books with the main HALT, as now. A malformed sub-verdict is refused (nothing written).
DEPLOY ORDER (QP, PR #894 comment 6388090983 (1)): the books file is SHARED ($HOME/data/arena/a3-books.json). An older a3_halt.py refuses a file with the third key (exit 2) and then
applies NO halt to ANY book. Add `fees_check_bot_ids` only after Q50 has ended and its A3 loop and dead-man jobs run at a pin that contains this file (or after the full R9.2 replay).

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
FEES_KEY = "fees_check_bot_ids"  # optional; successor books only
FEES_CHECK_REFUSED_IDS = ("h5v1q50-boostfloor-a1", "h5v1-boostfloor-a1", "h5v2-boostclock-a1", "h5v1-boostfloor-a2", "h5v2-boostclock-a2")  # registered before this rule
FEES_VERDICTS = ("PASS", "FAIL", "ERROR")
DEADMAN_S = 48 * 3600
MIN_MS = 1_600_000_000_000
FUTURE_SLACK_MS = 5 * 60 * 1000


class Invalid(ValueError):
    """Input refused; nothing was written."""


class PartialApply(Invalid):
    """The halts that could be applied were written, then an applicable rule had an empty list: nonzero exit, no applied-log line."""

    def __init__(self, msg: str, result: dict[str, Any]) -> None:
        super().__init__(msg)
        self.result = result


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
    if not isinstance(books, dict) or set(books) not in (set(BOOKS_KEYS), set(BOOKS_KEYS) | {FEES_KEY}):
        raise Invalid(f"books file must be an object with exactly the keys {list(BOOKS_KEYS)} (and optionally {FEES_KEY})")
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
    if FEES_KEY in books:
        fees = books[FEES_KEY]
        if not isinstance(fees, list) or not all(isinstance(x, str) and x for x in fees) or len(set(fees)) != len(fees):
            raise Invalid(f"{FEES_KEY} must be a list of unique non-empty strings")
        if set(fees) - set(new):
            raise Invalid(f"{FEES_KEY} ids not in new_rule_bot_ids: {sorted(set(fees) - set(new))}")
        if set(fees) & set(FEES_CHECK_REFUSED_IDS):
            raise Invalid(f"{FEES_KEY} may not list books registered before the fees-program check: {sorted(set(fees) & set(FEES_CHECK_REFUSED_IDS))}")
    return legacy, new


def fees_ids(books: Mapping[str, Any]) -> list[str]:
    """The (validated) fees_check_bot_ids, [] when absent."""
    return list(books.get(FEES_KEY) or [])


def fees_decision(verdict: Mapping[str, Any], d: Mapping[str, Any], now_ms: int) -> dict[str, Any] | None:
    """The validated fees_check sub-verdict ({verdict, halt_at_ms}) or None when the verdict has none. Refused (Invalid) when malformed: it must sit beside a main HALT
    with a flag instant, be PASS (no instant) or FAIL / ERROR (an epoch-ms instant in range, not after the flag's instant)."""
    if "fees_check" not in verdict:
        return None
    f = verdict["fees_check"]
    if not isinstance(f, Mapping) or f.get("verdict") not in FEES_VERDICTS or "halt_at_ms" not in f:
        raise Invalid("fees_check sub-verdict malformed")
    if d["verdict"] != "HALT" or d["flag_halt_ms"] is None:
        raise Invalid("fees_check sub-verdict without a main HALT")
    h = f["halt_at_ms"]
    if f["verdict"] == "PASS":
        if h is not None:
            raise Invalid("fees_check PASS carries halt_at_ms")
    elif not isinstance(h, int) or isinstance(h, bool) or not MIN_MS <= h <= now_ms + FUTURE_SLACK_MS or h > d["flag_halt_ms"]:
        raise Invalid("fees_check halt_at_ms is not an epoch-ms integer in range at or before the flag's instant")
    return {"verdict": f["verdict"], "halt_at_ms": h}


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


def plan(d: Mapping[str, Any], legacy: Sequence[str], new: Sequence[str], fees: Sequence[str] = (), fd: Mapping[str, Any] | None = None
         ) -> tuple[list[tuple[int, list[str], str]], list[str]]:
    """([(at_ms, bot_ids, rule)] to apply, [errors]) from a VALIDATED decision. Pure. An applicable rule with an empty list is an error (a halt that ends nothing),
    but it does not stop the other rule's halt from being written. `fees` / `fd` (successor books, fees-program check): with both, the `fees` books follow fd instead of
    the main HALT; with either missing the plan is the merged rule's."""
    out: list[tuple[int, list[str], str]] = []
    errs: list[str] = []
    if d["flag_halt_ms"] is not None:
        if legacy:
            out.append((int(d["flag_halt_ms"]), list(legacy), "legacy"))
        else:
            errs.append("an A3 flag was set but legacy_bot_ids is empty: that halt ends no book")
    if d["verdict"] in HARD:
        if not new:
            errs.append(f"verdict {d['verdict']} but new_rule_bot_ids is empty: that halt ends no book")
        elif fees and fd is not None:
            rest = [b for b in new if b not in set(fees)]
            if rest:
                out.append((int(d["halt_at_ms"]), rest, "new_rule"))
            if fd["verdict"] in HARD:
                out.append((int(fd["halt_at_ms"]), list(fees), "fees_check"))
        else:
            out.append((int(d["halt_at_ms"]), list(new), "new_rule"))
    return out, errs


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
    fees = fees_ids(books)
    fd = fees_decision(verdict, d, now) if fees else None
    steps, errs = plan(d, legacy, new, fees, fd)
    done = [{"rule": rule, "at_ms": at_ms, **halt_books(registry, at_ms, ids)} for at_ms, ids, rule in steps]
    result = {"run_utc": d["run_utc"], "verdict": d["verdict"], "applied": done}
    if fd is not None:
        result["fees_check"] = fd["verdict"]
    if errs:
        raise PartialApply("; ".join(errs), result)
    return result


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


def _registered_ms(entry: Mapping[str, Any]) -> int | None:
    for k in ("registered_at_ms", "clock_start_ms"):
        if isinstance(entry.get(k), int):
            return int(entry[k])
    return None


def deadman(applied: Path, books: Any, registry: Path, now_ms: int | None = None) -> dict[str, Any]:
    """Each new-rule book's anchor is the LATER of the newest applied run and its own registration instant (a book registered after the last applied run is not dead
    on arrival). With NO applied log (first run, or the file lost or A3_OUT changed) the anchor is the registration instant alone: the log can never be missing quietly.
    A book whose anchor is more than DEADMAN_S old is ended at anchor + DEADMAN_S. Exit stays 0 so the daily can continue and create the log."""
    now = int(time.time() * 1000) if now_ms is None else now_ms
    legacy, new = _check(books, registry)
    bots = sc.load_registry(registry)
    last = last_applied_run_ms(applied)
    if not new:
        return {"deadman": "no new-rule books", "applied": []}
    out: dict[str, Any] = {"deadman": "alive", "applied": []}
    if last is not None:
        out["last_run_utc"] = datetime.fromtimestamp(last / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        out["anchor"] = "no applied log: anchored on each book's registration instant"
    due: dict[int, list[str]] = {}
    for bot_id in new:
        reg = _registered_ms(bots[bot_id])
        if reg is None and last is None:
            raise Invalid(f"{bot_id} has no registered_at_ms or clock_start_ms and there is no applied log: no dead-man anchor")
        anchor = max(x for x in (last, reg) if x is not None)
        if now - anchor > DEADMAN_S * 1000:
            due.setdefault(anchor + DEADMAN_S * 1000, []).append(bot_id)
    for at, ids in sorted(due.items()):
        out["applied"].append({"rule": "deadman", "at_ms": at, **halt_books(registry, at, ids)})
    if due:
        out["deadman"] = "FIRED"
    return out


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
    except PartialApply as exc:
        print(json.dumps(exc.result, sort_keys=True))
        print(f"A3_HALT_PARTIAL {exc}", file=sys.stderr)
        return 2
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
