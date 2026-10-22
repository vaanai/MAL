#!/usr/bin/env python3
"""Read wrapper for DEC-031 Amendment 6 books (QP ruling on PR #801): the pinned path from the exclusions tool to the scorer.

    python -m tools.arena_am6_read --ledger-root DIR --registry FILE --bot BOT --health HOURS.jsonl --health-sha SHA --as-of-ms MS --restarts FILE [--excl-out FILE] [--json FILE] [--record --reads-dir DIR]

Run at the read instant. Steps (each failure is exit 2 and prints nothing that looks like a score):
 1. Hour table: tools/arena_am6_exclusions.load_health RECOMPUTES the sha256 of the tools/arena_hour_health.py output and checks the hours count; with
    --health-sha it must equal the sha256 notebooked for that table. (The table is produced earlier by arena_hour_health; this wrapper verifies it.)
 2. Cut: tools/arena_am6_cut over the bot's ledger rows (the rows arena_score reads), attempts decided up to --as-of-ms.
 3. Exclusions, pass 1 over all attempts: gives the attempts DECIDED IN EXCLUDED HOURS (item 2.4 / 3). Those neither count nor are read, so the read boundary B
    is arena_score.blind_read_instant over the other attempts' decision instants (decision timestamps only, never an exit). No B: NOT_READY, exit 0.
 4. Exclusions, pass 2 over the attempts decided before B: THE exclusions output (its sha256 line is printed and --excl-out gets every line).
    verdict_cap NOT_DECIDABLE_*: printed, exit 0, the scorer is never called.
 5. Parity guard (arena_am6_cut.parity_guard): the scorer's own exclusion counts on the unmodified rows must equal the cutter's s14 mirror, else exit 2.
 6. Scored set. arena_score.py has no exclusion-list input and its sha is pinned, so the wrapper filters the rows it passes to arena_score.score_bot:
      - every row (open, refused, trade, excluded, late_print) of an attempt decided in an excluded hour is dropped (not counted, not read, absent from the boundary);
      - a CLOSED attempt excluded for a span overlap or an open-at-restart is turned into the scorer's own `excluded` row at every stake (kind "excluded",
        exclusion_reason am6_span_overlap_pricepath / am6_open_at_restart, legs and calc removed), so it keeps counting as an attempt for the boundary (by decision
        instant) and as a section 1.4 exclusion in the scorer's 5% share, never as a priced trade;
      - an UNCLOSED restart attempt is left as is: the scorer's own 24 h settle rule (orphan -> NOT_DECIDABLE) stays in force;
      - s14 attempts are already the scorer's own exclusions and are left as is.
    The scorer's boundary on the filtered rows must equal B or the wrapper exits 2. Then arena_score.score_bot and arena_score.render run on the filtered rows.
Reconciliation of the two 5% checks: after the filter the scorer's denominator is the attempts that remain after the 10% class (= n - n10 of the exclusions tool)
and its numerator holds the s14, span and closed-restart exclusions plus data refusals, so the scorer's own NOT_DECIDABLE share test is the exclusions tool's
5% cap on the same population; the exclusions tool's cap is the one printed. Both are conservative against an unclosed restart attempt only in that the
exclusions tool counts it and the scorer does not.
--record writes THE read once through arena_score.record_read on the filtered result (a NOT_DECIDABLE_* verdict of the exclusions tool is recorded as a final
NOT_DECIDABLE), with an `am6` block: boundary B, cut / exclusions / merged-health / restarts sha256 and every table sha256. A second record is refused.
arena_score must NEVER be run directly on a covered bot (not with --record, not with --json, not as an all-bot run without --bot): it scores the unfiltered rows
at the unfiltered boundary, an outcome look outside the one read. Every read of a covered book goes through this wrapper only.
Inputs: --health FILE (repeatable, the notebooked daily tables) with a required --health-sha for each (no re-run of arena_hour_health at read time; an hour in two
tables must be identical); --restarts FILE required (empty = none; its sha256 is printed); an engine start row after the clock, other than the first, outside a
declared restart gap -> exit 2. The parity guard runs before any verdict is printed or recorded, and a scorer result without books fails closed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from tools import arena_am6_cut as C
from tools import arena_am6_exclusions as X
from tools import arena_score as S
from tools.arena_paper import Ledger, load_registry

DAY_MS = S.DAY_MS


class Refused(Exception):
    pass


def _totals(lines: Sequence[str]) -> dict:
    return next(json.loads(x) for x in lines if json.loads(x).get("type") == "totals")


def _by_id(lines: Sequence[str]) -> dict[str, dict]:
    return {r["attempt_id"]: r for r in map(json.loads, lines) if r.get("type") == "attempt"}


def asof_rows(rows: Sequence[Mapping[str, Any]], clock_ms: int, as_of_ms: int) -> list[Mapping[str, Any]]:
    """The ledger as it stood at --as-of-ms: no row decided after it, and a close row whose exit is after it is not yet written (the attempt is open)."""
    out = []
    for r in rows:
        if r.get("kind") != "start" and r["decision_ms"]["entry"] > as_of_ms:
            continue
        if r.get("kind") in ("trade", "excluded") and (r["decision_ms"].get("exit") or 0) > as_of_ms:
            continue
        out.append(r)
    return out


def filter_rows(rows: Sequence[Mapping[str, Any]], status: Mapping[str, str], clock_ms: int, as_of_ms: int) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        pid = r.get("position_id")
        if r.get("kind") != "start" and r["decision_ms"]["entry"] >= clock_ms and r["decision_ms"]["entry"] > as_of_ms:
            continue  # rows decided after the cut instant are not part of this read
        st = status.get(pid)
        if st == X.S_10:
            continue
        if r.get("kind") == "trade" and st in (X.S_SPAN, X.S_RESTART):
            reason = "am6_span_overlap_pricepath" if st == X.S_SPAN else "am6_open_at_restart"
            r = dict(r, kind="excluded", exclusion_reason=reason, legs=None, calc=None)
        out.append(dict(r))
    return out


def merge_health(texts: Sequence[str], expected_shas: Sequence[str]) -> tuple[str, str, list[str]]:
    """The notebooked daily hour tables (never a re-run, DEC-031 Am.6 item 2.6). Each table's recomputed sha256 must be in the expected set and the set must be
    exactly the tables given. An hour in two tables must be identical. Returns (merged table text in arena_hour_health's format, its sha256, table shas)."""
    if not texts or not expected_shas:
        raise Refused("at least one --health table and its --health-sha are required")
    merged: dict[int, tuple[bool, frozenset]] = {}
    shas = []
    for t in texts:
        table, sha = X.load_health(t)
        shas.append(sha)
        for h, v in table.items():
            if h in merged and merged[h] != v:
                raise Refused(f"hour {h} differs between two health tables")
            merged[h] = v
    if sorted(shas) != sorted(expected_shas):
        raise Refused(f"health table sha256 {sorted(shas)} != expected {sorted(expected_shas)}")
    lines = [C.canon({"hour": datetime.fromtimestamp(h / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "excluded": ex, "labels_failing": sorted(lb)})
             for h, (ex, lb) in sorted(merged.items())]
    sha = hashlib.sha256("".join(ln + "\n" for ln in lines).encode()).hexdigest()
    return "\n".join(lines + [C.canon({"type": "sha256", "hours": len(lines), "sha256": sha})]) + "\n", sha, shas


def check_restart_starts(rows: Sequence[Mapping[str, Any]], restarts: Sequence[tuple[int, int]], clock_ms: int) -> None:
    """Every engine start row after the clock except the first must lie in a declared restart gap (rounded outward to whole UTC hours). Timestamps only."""
    gap = set()
    for st, en in restarts:
        h = st // X.HOUR_MS * X.HOUR_MS
        while h <= max(st // X.HOUR_MS * X.HOUR_MS, (en - 1) // X.HOUR_MS * X.HOUR_MS):
            gap.add(h)
            h += X.HOUR_MS
    starts = sorted(int(r["decision_ms"]["entry"]) for r in rows if r.get("kind") == "start" and int(r["decision_ms"]["entry"]) >= clock_ms)
    for t in starts[1:]:
        if t // X.HOUR_MS * X.HOUR_MS not in gap:
            raise Refused(f"engine start row at {t} (after the first) is outside every declared restart gap: declare it in --restarts")


def run(ledger_root: str, registry: str, bot: str, health_texts: Sequence[str] | str, as_of_ms: int, restarts_text: str, expect_health_shas: Sequence[str],
        excl_out: str | None = None, record: bool = False, reads_dir: str | None = None) -> tuple[int, list[str], dict | None]:
    """(exit code, output lines, the score result or None). With record=True the read is written ONCE via arena_score.record_read (a second one is refused)."""
    reg = load_registry(registry)
    if bot not in reg:
        raise Refused(f"{bot}: not in the registry")
    clock = reg[bot]["clock_start_ms"]
    stake = reg[bot].get("deciding_stake")
    if stake is None:
        raise Refused(f"{bot}: registry line lacks deciding_stake")
    if restarts_text is None:
        raise Refused("--restarts is required (an empty file means no restarts)")
    health_text, hsha, table_shas = merge_health([health_texts] if isinstance(health_texts, str) else list(health_texts), expect_health_shas)
    restarts_sha = hashlib.sha256(restarts_text.encode()).hexdigest()
    led = Ledger(ledger_root)
    rows, problems = led.scan_bot(bot)
    incidents, inc_problems = led.read_incidents(bot)
    restarts = X.load_restarts(restarts_text)
    check_restart_starts(rows, restarts, clock)
    out = [f"AM6 READ  bot {bot}  as_of_ms {as_of_ms}  health_tables {','.join(table_shas)}  health_merged_sha256 {hsha}  restarts_sha256 {restarts_sha}"]
    if problems or inc_problems:
        out.append(f"NOT_DECIDABLE_LEDGER: {len(problems) + len(inc_problems)} torn or unparseable ledger line(s): scorer not called")
        return 0, out, None
    attempts = C.attempts_from_rows(rows, clock, stake, as_of_ms)
    cut_lines, cut_sha = C.render_cut(attempts)
    out.append(f"cut {cut_sha}")
    # pass 1: which attempts were decided in excluded hours -> the outcome-blind boundary
    text1 = "".join(ln + "\n" for ln in cut_lines)
    p1 = _by_id(X.run(health_text, text1, restarts_text, as_of_ms))
    keep_ms = [a["decided_ms"] for a in attempts if p1[a["attempt_id"]]["status"] != X.S_10]
    b = S.blind_read_instant(keep_ms, clock)
    if b is None or as_of_ms < b:
        out.append("NOT_READY: the book has not reached 100 attempts over 5 UTC dates (excluded-hour attempts removed) at a 00:00Z boundary before --as-of-ms")
        return 0, out, None
    out.append(f"read_boundary_ms {b}")
    # pass 2: the attempts decided before B
    sub = [a for a in attempts if a["decided_ms"] < b]
    ex_lines = X.run(health_text, "".join(C.canon(a) + "\n" for a in sub), restarts_text, as_of_ms)
    if excl_out:
        with open(excl_out, "w") as fh:
            fh.write("".join(ln + "\n" for ln in ex_lines))
    tot, shaline = _totals(ex_lines), ex_lines[-1]
    out.append("exclusions " + shaline)
    out.append("exclusions_totals " + json.dumps(tot, sort_keys=True, separators=(",", ":")))
    rows = asof_rows(rows, clock, as_of_ms)
    dropped = [aid for aid, r in p1.items() if r["status"] == X.S_10]
    g = C.parity_guard(bot, reg, rows, as_of_ms, stake, dropped)  # the rows actually scored (S_10 attempts removed), through the wrapper's B
    if g.get("boundary_ms") != b:
        raise Refused(f"parity guard boundary {g.get('boundary_ms')} != wrapper boundary {b}")
    if not g["ok"]:
        raise Refused(f"s14 parity with arena_score.py failed: scorer {g['scorer']} != cutter {g['cutter']}")
    am6 = {"read_boundary_ms": b, "cut_sha256": json.loads(cut_sha)["sha256"], "exclusions_sha256": json.loads(shaline)["sha256"], "health_table_sha256": table_shas,
           "health_merged_sha256": hsha, "restarts_sha256": restarts_sha}
    if tot["verdict_cap"] != "OK":
        out.append(f"{tot['verdict_cap']}: final for the bot_id; the scorer is not called")
        res_nd = {"bot_id": bot, "status": "NOT_DECIDABLE", "reasons": [tot["verdict_cap"]], "read_boundary_ms": b, "boundary_source": "natural", "attempts": tot["n_attempts"],
                  "exclusions_totals": tot, "am6": am6}
        if record:
            out.append("recorded: " + str(S.record_read(reads_dir or S.READS_DIR, res_nd)))
        return 0, out, res_nd if record else None
    status = {aid: r["status"] for aid, r in _by_id(ex_lines).items()}
    # attempts at or after B are outside this read: keep their rows out of the filter's status map but also out of the scored rows
    kept = [r for r in filter_rows(rows, status, clock, as_of_ms)]
    res_bot = S.score_bot(bot, reg, kept, as_of_ms, None, S.SETTLE_MS, incidents=incidents, ledger_problems=problems + inc_problems)
    if res_bot.get("natural_boundary_ms") != b:
        raise Refused(f"scorer boundary {res_bot.get('natural_boundary_ms')} != wrapper boundary {b} on the filtered rows")
    res_bot["am6"] = am6
    existed = sum(1 for e in reg.values() if e["clock_start_ms"] < as_of_ms)
    res = {"now_ms": as_of_ms, "arena_books_with_clock_before_read": existed, "arena_books_registered": len(reg),
           "arena_books_stopped": sum(1 for e in reg.values() if e.get("stopped_at_ms")), "cells_existed": existed * 3 * len(S.MODELS) * len(S.LATS),
           "books_read": len(res_bot.get("books", {})), "cells_read": len(res_bot.get("books", {})) * len(S.MODELS) * len(S.LATS), "bots_refused": {}, "bots": {bot: res_bot}}
    out.append(f"scored rows: {len(kept)} of {len(rows)}; exclusions tool counted {tot['n_counted']} of {tot['n_attempts']} attempts")
    out.append(S.render(res))
    if record:
        out.append("recorded: " + str(S.record_read(reads_dir or S.READS_DIR, res_bot)))
    return 0, out, res


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ledger-root", default=S.LEDGER_ROOT)
    ap.add_argument("--registry", default=S.REGISTRY_PATH)
    ap.add_argument("--bot", required=True)
    ap.add_argument("--health", required=True, action="append", help="a notebooked daily hour table (repeatable); never re-run arena_hour_health at read time")
    ap.add_argument("--health-sha", required=True, action="append", help="the notebooked sha256 of each --health table (repeatable)")
    ap.add_argument("--as-of-ms", required=True, type=int)
    ap.add_argument("--restarts", required=True, help="restart-gap JSONL from the notebook; an empty file means no restarts")
    ap.add_argument("--record", action="store_true", help="write the one read via arena_score.record_read (refused if already recorded)")
    ap.add_argument("--reads-dir", default=S.READS_DIR)
    ap.add_argument("--excl-out")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    try:
        code, lines, res = run(a.ledger_root, a.registry, a.bot, [open(h).read() for h in a.health], a.as_of_ms, open(a.restarts).read(),
                               a.health_sha, a.excl_out, a.record, a.reads_dir)
    except Exception as ex:  # noqa: BLE001 - any failure is a refusal (exit 2), never a traceback exit 1
        print(f"REFUSED: {ex}", file=sys.stderr)
        return 2
    print("\n".join(lines))
    if a.json and res is not None:
        with open(a.json, "w") as fh:
            json.dump(res, fh, indent=1, sort_keys=True, allow_nan=False)
    return code


if __name__ == "__main__":
    sys.exit(main())
