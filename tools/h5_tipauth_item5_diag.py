#!/usr/bin/env python3
"""H5 shadow, tipauth path: the item-5 table and the gap-record table, COUNTS ONLY (quant-proof, PR #610: comment 6156017843 section 3 (a) and (d)
for the item-5 table; comment 6156163571 sections 1 and 2 for the gap-record table).

It is a diagnostic read of the H5 shadow's hourly files (h5-shadow-YYYY-MM-DDTHH.jsonl). It prints counts and labels. It opens no tip file, no
ledger, no executor file and no network connection. It is not run on its own schedule: it runs only with the flags of an item-4 scheduled run
that has already been made, at most once per such instant (see RUN RULES). It changes nothing in item 4, item 5, the cap or the schedule.

  python3 tools/h5_tipauth_item5_diag.py --shadow-dir DIR --since-ms T0 --until-ms T1 --expect-code-sha SHA40 \\
      --expect-n-ok N --expect-incomplete-n N --expect-plain-pool-closes N --expect-triggers-pv N \\
      --expect-gap-records-by-kind '{"silence":16,"slot_jump":762,"socket_reconnect":1973}' --out FILE

The five --expect-* values are copied by the operator from the printed values of that item-4 run (`item5_tip_incompleteness.plain_pool_closes`
`n_ok` and `incomplete_n`; `records.plain_pool_closes`, `records.triggers_pv`, `records.gap_records_by_kind`). The tool opens no item-4 out file.

TABLE A, item 5 (comment 6156017843, 3 (a) and 3 (d) item 3). Population: unsealed plain pool-close records with `gap` exactly False, the
live-feed count `base_breaks_unresolved_settled` exactly 0, `base_tipauth.status` "ok" and an integer tipauth value (`n_ok`). Group `incomplete`:
those with the tipauth value at least 1 or `base_tipauth.orphaned_by_drop_n` at least 1 (`incomplete_n`). Group `other_n_ok`: the rest of `n_ok`.
For each group, one table per dimension (no dimension is crossed with another): the UTC hour of `t_ms`; the arm (tipauth at least 1 only,
orphaned at least 1 only, both, neither); the `dropped_n` class (0, 1, 2 to 5, 6 to 20, above 20); and, for each of `kept_ws_only_before_cover`,
`kept_ws_only_above_cover`, `kept_ws_only_sig_on_tip`, `dropped_boost_n`, `dropped_sells_n`, whether it is at least 1. If the found `n_ok` and
`incomplete_n` differ from the flags, `item5_table.result` is null. Keys read: only those in `tools/h5_tipauth_item4.ALLOWED_KEYS`
(ITEM5_KEYS below); the three keys of the second list are never read by this table.

TABLE B, gap records (comment 6156163571 sections 1 and 2). From `type: gap` records: the count by kind (the seven kinds named, anything else
`other`); for `slot_jump` the count by `missed_slots` class (30 to 59, 60 to 149, 150 to 599, 600 and more; below 30, and not an integer, in cells
of their own); for `socket_reconnect` the count by `flags_pools` (true, false, missing or not a boolean). From unsealed plain pool closes and
unsealed pv triggers: the count with `gap` exactly True split by the set of kinds in `gaps` (the entries' `kind` only), and separately the count
of records whose `gaps` list has exactly 5 entries (the record holds `p.gaps[:5]` and no total, so for those the set of kinds is a lower bound;
their sets are in their own table). If the found `gap_records_by_kind`, `plain_pool_closes` and `triggers_pv` differ from the flags,
`gap_table.result` is null. Wherever a kind is printed (the table, `totals.found`, `totals.expected` and the echoed `--expect-gap-records-by-kind`)
only the seven names are printed; every other string, every non-string kind and the string `null` print as `other`. The comparison with the flags
stays on the raw counters. Keys read: `GAP_TABLE_KEYS` below, which is item 4's list widened by exactly `missed_slots`, `flags_pools`, `gaps` and
narrowed to the keys named in the comment. `from_slot`, `to_slot`, `silent_ms`, `open_pools`, `reason`, `at_ms`, `sockets_down_recent`, the
`missed_slots` inside a `gaps` entry and every key of an `outcome` record are never read; a record of `type` `outcome` is not read past `type`.

Both tables are checked on their own against item 4's totals (`totals_equal_item4`). One of them can have a result while the other has none.

RUN RULES (comment 6156017843 3 (d) items 1, 2 and 4; comment 6156163571 sections 1 to 3):
  1. --until-ms must satisfy item4.on_schedule(since, until) and the run must start at or after until + item4.SETTLE_MS (both imported). Otherwise
     exit 8, no counts, no output file. One run per scheduled instant, after that instant's item-4 run. The tool cannot see whether an earlier
     run was made; the operator keeps that rule.
  2. Banned uses: a share "excluding" an hour or a class; a recount of any item-4 line under another gap rule; any change to item 5, the cap or the
     schedule; a restart or config change of shadow #817 or of the tip follower made on the strength of a table before the deciding read, unless
     LAB_STATE first records "origin 1791797643640 ended without a deciding read; item 5 False at +24 h and +36 h (n_ok 192 and 295); no phase-2
     proposal from this origin" (and every later interim read at which item 5 was False). A threshold read off the gap table would be in-sample
     selection.
  3. Not in this tool and not to be added without a ruling: a cell that crosses a gap kind with `ws`, a tipauth field, would-pass or incomplete
     status; a per-trigger breakdown by the `missed_slots` of its `gaps` entries; any percentage or ratio.

REUSED FROM tools/h5_tipauth_item4.py (imported, not copied): iter_records, snapshot, hour_files (the file list and sizes are taken once; both
tables read the same bytes), view_record, tip_ok, flag (the sealed rule: anything but absent, null or the literal False is sealed), isint,
on_schedule, SETTLE_MS, HOUR_MS, ALLOWED_KEYS. MIRRORED, because analyze() has no callable for it: the origin (the LAST start record with
since <= t_ms <= until), the window (integer t_ms in [origin, until]), and the three predicates of item 4's nested incomplete() (see I3).
The origin pass is shared by both tables: it reads `type` on every record and `t_ms` and `code_sha` on `start` records (all inside item 4's
ALLOWED_KEYS; `code_sha` is not in GAP_TABLE_KEYS, which lists only what table B itself reads).

THE OUTPUT FILE. --out is created exclusively (never overwritten) BEFORE the analysis, so a run whose output cannot be kept does not read the
files; the result is written into it at the end, before it goes to stdout. If it cannot be created or written (it exists, its directory is
missing or not writable, the disk is full) the exit code is 6 with a fixed message and no traceback, and no counts are printed. A file this run
created is removed again when the run ends in an error (codes 3, 4, 5) or when the write fails.

EXIT CODE: 0 means the tool ran and both tables' totals equal the flags. Non-zero prints JSON: 2 usage (argparse), 3 shadow directory missing,
4 no start record at or after --since-ms, 5 --expect-code-sha does not match the origin start record's code_sha, 6 --out cannot be created or
written (it exists, which is never overwritten, or its directory is missing or not writable), 8 --until-ms is not an item-4 scheduled instant or
the run is earlier than that instant plus the settle time, 9 at least one table's totals differ from the flags (that table's `result` is null; its
expected and found totals are printed; the output file is still written). Codes 2 to 8 print no counts and write no output file.

H5 is an execution measurement; EXP-024 ended NOT_DECIDABLE on 2026-10-13 with no look read; not a book. H5 has not cleared the promotion gate.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import h5_tipauth_item4 as I4  # noqa: E402

TOOL = "h5_tipauth_item5_diag"
SPEC = {"item5_table": "PR #610 quant-proof comment 6156017843, section 3 (a) and (d)",
        "gap_table": "PR #610 quant-proof comment 6156163571, sections 1 and 2"}
LABEL = ("H5 is an execution measurement; EXP-024 ended NOT_DECIDABLE on 2026-10-13 with no look read; not a book. "
         "H5 has not cleared the promotion gate. Counts only; not a result for any acceptance line.")
HORIZON_MS = 24 * 14 * I4.HOUR_MS  # item 4's schedule() horizon: a longer window is not one of its reads

EXIT_USAGE, EXIT_NO_DIR, EXIT_NO_START, EXIT_CODE_SHA, EXIT_OUT, EXIT_SCHEDULE, EXIT_TOTALS = 2, 3, 4, 5, 6, 8, 9

# Keys the item-5 table may read: item 4's list, nothing more.
ITEM5_KEYS = I4.ALLOWED_KEYS
# The three names by which the gap table widens item 4's list.
WIDENED_KEYS = frozenset({"missed_slots", "flags_pools", "gaps"})
# Keys the gap table may read (comment 6156163571, section 1 item 2). `kind` is also the only key read inside a `gaps` entry.
GAP_TABLE_KEYS = frozenset({"type", "t_ms", "kind", "missed_slots", "flags_pools", "sealed", "h5_look2_sealed", "variant", "pool", "gap", "gaps"})
ORIGIN_KEYS = frozenset({"type", "t_ms", "code_sha"})  # the start-record pass (shared by both tables)

# ---- table A labels ---------------------------------------------------------------------------------------------------------------------------
ARMS = ("tipauth_only", "orphaned_only", "both", "neither")
DROPPED_CLASSES = ("0", "1", "2_to_5", "6_to_20", "above_20", "not_int_or_negative")
FIELDS = ("kept_ws_only_before_cover", "kept_ws_only_above_cover", "kept_ws_only_sig_on_tip", "dropped_boost_n", "dropped_sells_n")

# ---- table B labels ---------------------------------------------------------------------------------------------------------------------------
# The seven kinds the shadow appends (tools/h5_shadow.py: _gap() for the first four; p.gaps.append for the last three). Anything else is `other`.
SEVEN_KINDS = ("slot_jump", "silence", "feed_restart", "socket_reconnect", "ambiguous_mint", "announced_before_gap", "slot_below_s0")
KIND_LABELS = SEVEN_KINDS + ("other",)
MISSED_BOUNDS = (30, 60, 150, 600)  # the declared missed_slots class bounds (comment 6156163571, 1 item 5); not a gap rule
MISSED_CLASSES = ("below_30", "30_to_59", "60_to_149", "150_to_599", "600_and_more", "not_int_or_missing")
FLAGS_POOLS_LABELS = ("true", "false", "missing_or_not_bool")
NO_ENTRIES = "(no_entries)"
GAP_LEN_LIMIT = 5  # the record holds p.gaps[:5]

INTERPRETATIONS = [
    "I1 hour buckets: the calendar UTC hour of the close's t_ms, labelled YYYY-MM-DDTHH (the hour file's own naming). No fold of the hours of the day over days is printed. "
    "The hour labels are the only times taken from pool, trigger or gap records. The output also prints the origin start record's t_ms (window.origin_t_ms), the operator's two "
    "instants (--since-ms, --until-ms) and the hour file names and sizes.",
    "I2 population of table A: unsealed plain pool closes only (the line tip_incompleteness_le_5pct reads item5_tip_incompleteness.plain_pool_closes). The trigger side of "
    "item5_tip_incompleteness is not tabulated.",
    "I3 item 4's incomplete() is a closure inside analyze(), so it cannot be imported. Its three predicates are restated here over item 4's own view_record, tip_ok and isint: "
    "base = gap exactly False and base_breaks_unresolved_settled an integer equal to 0; n_ok = base and tip_ok; incomplete = n_ok and (tipauth value >= 1 or orphaned_by_drop_n an "
    "integer >= 1). Table A reads a pool record through item 4's view_record, so it reads every key that view reads (all inside ALLOWED_KEYS, a few unused here, for example `slot`). "
    "A test pins n_ok and incomplete_n to analyze() on a deterministic and a seeded random fixture.",
    "I4 each dimension is its own one-way table per group; no two dimensions are crossed. The arm cell `neither` exists so the arm table sums to the group size (it is 0 in "
    "`incomplete` and the whole group in `other_n_ok`).",
    "I5 dropped_n class: 0, 1, 2_to_5, 6_to_20, above_20; a value that is not an integer (null, bool, string) or is negative is `not_int_or_negative`, so the table sums to the group size.",
    "I6 for each of the five fields: `at_least_1` is an integer >= 1; `not_at_least_1` is everything else (0, negative, null, missing, not an integer).",
    "I7 sealed: `sealed` and `h5_look2_sealed` are read first, through item 4's flag(). A sealed pool or trigger record is skipped and counted under item 4's names; nothing else of it is read. "
    "Gap records carry no sealed flag and are not tested for one, as in item 4.",
    "I8 gap kind: item 4's gap_records_by_kind key is the raw string, or `null` for a non-string kind. The totals check compares that raw counter to the flag. The printed table names "
    "the seven kinds; any other string and any non-string kind is `other`. Wherever a kind is printed (the table, totals.found, totals.expected and the echoed "
    "inputs.expect.gap_records_by_kind) only the seven names are printed; every other string, every non-string kind and the string `null` print as `other`, with their counts "
    "merged. The comparison with the flags stays on the raw counters (totals_equal_by_total.gap_records_by_kind carries the difference).",
    "I9 missed_slots is read only on kind == slot_jump. A value below 30 (not producible while the shadow's threshold is 30, since a jump is recorded only above it) is counted in "
    "its own cell `below_30`: it is neither dropped nor merged into 30_to_59, so the class cells sum to the slot_jump count. A missing value, a bool, a string, a float or a "
    "value beyond item 4's integer bound is `not_int_or_missing`. Bounds: 30 and 59 in the first class, 60 and 149 in the second, 150 and 599 in the third, 600 and more in the last.",
    "I10 flags_pools is read only on kind == socket_reconnect: the literal True is `true`, the literal False is `false`, anything else is `missing_or_not_bool`.",
    "I11 set of kinds: for a record with gap exactly True, the sorted distinct kinds of its `gaps` entries joined by `+`; an entry that is not an object, has no string kind or has a "
    "kind outside the seven counts as `other`. A record with gap True whose `gaps` is not a list or is empty is `(no_entries)`. `gaps` is read only when gap is exactly True.",
    "I12 exactly 5 entries: `gaps_len_5_n` counts gap-true records whose `gaps` list has exactly 5 entries; their sets are in `gap_true_gaps_len_5_by_kind_set_lower_bound` and are "
    "not mixed into `gap_true_by_kind_set`, so no cell of the latter hides a truncated list.",
    "I13 gap other than the literals True and False is its own cell `gap_not_bool`, so gap_true + gap_false + gap_not_bool = n.",
    "I14 populations of table B follow item 4's `records` block: plain pool closes are all unsealed pool records with a string pool (no variant read); triggers are unsealed trigger "
    "records with a string pool and variant == pv (fv triggers are read as far as `variant` and are not counted).",
    "I15 window: origin = the last start record with since <= t_ms <= until (all starts in range are counted); records with an integer t_ms in [origin, until]. `type` is read first; "
    "a record whose type the table does not use (outcome, hb, ...) is not read past `type`, not even for t_ms, so records_without_int_t_ms counts only the types the table uses "
    "and is not comparable to item 4's.",
    "I16 totals gate: each table is checked alone. A mismatch gives result null, expected and found totals, exit 9, and the output file is still written. A wrong flag therefore "
    "prints no cell of that table.",
    "I17 --out is required (stricter than the decision-change tool, where it is optional): a run whose output is not kept cannot be shown to be the one run of its instant. "
    "The file is created exclusively before the analysis and written at the end, before stdout; any OSError on creating or writing it is exit 6 with a fixed message and no counts.",
    "I18 scan counts: `lines` = parsed objects + bad lines (non-blank lines); each table reports its own pass over the same snapshot of the file list and sizes.",
    "I19 the tool cannot tell whether an item-4 run was made at the instant or whether this tool already ran at it; both are the operator's rules (the tool checks only the "
    "schedule, the settle time and the flags).",
]


class InputError(Exception):
    def __init__(self, msg: str, code: int):
        super().__init__(msg)
        self.msg = msg
        self.code = code


# ---- shared reading ---------------------------------------------------------------------------------------------------------------------------

def find_origin(snap: list, since_ms: int, until_ms: int, object_hook: Callable | None = None) -> tuple[list[dict], dict | None]:
    """item-4 analyze(): the starts with since <= t_ms <= until; the origin is the LAST one. Reads `type`, `t_ms`, `code_sha` of start records only."""
    starts: list[dict] = []
    for rec in I4.iter_records(snap, collections.Counter(), object_hook):
        if rec.get("type") == "start":
            t = rec.get("t_ms")
            if I4.isint(t) and since_ms <= t <= until_ms:
                starts.append({"t_ms": t, "code_sha": rec.get("code_sha")})
    starts.sort(key=lambda s: (s["t_ms"], str(s["code_sha"])))
    return starts, (starts[-1] if starts else None)


def window_records(snap: list, counts: collections.Counter, object_hook: Callable | None, origin_ms: int, until_ms: int, types: tuple):
    """Yield (type, t_ms, record) for records of the given types with an integer t_ms in [origin, until]. Any other type is read only as far as `type`."""
    for rec in I4.iter_records(snap, counts, object_hook):
        counts["records_parsed"] += 1
        typ = rec.get("type")
        if typ not in types:  # tuple membership: safe for an unhashable value
            continue
        t = rec.get("t_ms")
        if not I4.isint(t):
            counts["records_without_int_t_ms"] += 1
            continue
        if t < origin_ms or t > until_ms:
            counts["records_outside_window"] += 1
            continue
        yield typ, t, rec


def sealed_skip(rec: dict, typ: str, counts: collections.Counter) -> bool:
    """Flags read first. True (and counted under item 4's names) when the record is sealed; nothing else of it is then read."""
    sl, h5 = I4.flag(rec.get("sealed")), I4.flag(rec.get("h5_look2_sealed"))
    if not (sl or h5):
        return False
    counts[f"skipped_{typ}_sealed" if sl else f"skipped_{typ}_h5_look2_sealed"] += 1
    if sl and h5:
        counts[f"skipped_{typ}_both_flags"] += 1
    return True


def scan_out(counts: collections.Counter) -> dict:
    parsed, bad = counts["records_parsed"], counts["bad_line"]
    return {"lines": parsed + bad, "records_parsed": parsed, "bad_line": bad, "bad_line_not_utf8": counts["bad_line_not_utf8"],
            "unreadable_file": counts["unreadable_file"], "records_without_int_t_ms": counts["records_without_int_t_ms"],
            "records_outside_window": counts["records_outside_window"], "record_pool_not_str": counts["record_pool_not_str"]}


def skipped_out(counts: collections.Counter, types: tuple) -> dict:
    out: dict = {}
    for t in types:
        out[f"sealed_{t}"] = counts[f"skipped_{t}_sealed"]
        out[f"h5_look2_sealed_{t}"] = counts[f"skipped_{t}_h5_look2_sealed"]
    out["both_flags"] = sum(counts[f"skipped_{t}_both_flags"] for t in types)
    return out


def label(k: Any) -> str:
    """A printable kind label: one of the seven named kinds, otherwise `other` (also for the string `null` and for any non-string)."""
    return k if (isinstance(k, str) and k in SEVEN_KINDS) else "other"


def label_counts(raw: dict) -> dict:
    out: collections.Counter = collections.Counter()
    for k, n in raw.items():
        out[label(k)] += n
    return dict(sorted(out.items()))


# ---- table A: item 5 --------------------------------------------------------------------------------------------------------------------------

def dropped_class(x: Any) -> str:
    if not I4.isint(x) or x < 0:
        return "not_int_or_negative"
    if x == 0:
        return "0"
    if x == 1:
        return "1"
    if x <= 5:
        return "2_to_5"
    if x <= 20:
        return "6_to_20"
    return "above_20"


def at_least_1(x: Any) -> bool:
    return I4.isint(x) and x >= 1


def _new_group() -> dict:
    return {"n": 0, "hours": collections.Counter(), "arm": collections.Counter(), "dn": collections.Counter(), "fld": collections.Counter()}


def _group_out(g: dict, hours: list[str]) -> dict:
    return {"n": g["n"],
            "by_utc_hour": {h: g["hours"].get(h, 0) for h in hours},
            "by_arm": {a: g["arm"].get(a, 0) for a in ARMS},
            "by_dropped_n_class": {c: g["dn"].get(c, 0) for c in DROPPED_CLASSES},
            "by_field_at_least_1": {f: {"at_least_1": g["fld"].get(f, 0), "not_at_least_1": g["n"] - g["fld"].get(f, 0)} for f in FIELDS}}


def hour_label(t_ms: int) -> str:
    return time.strftime("%Y-%m-%dT%H", time.gmtime((t_ms // I4.HOUR_MS) * 3600))


def item5_table(snap: list, origin_ms: int, until_ms: int, expect_n_ok: int, expect_incomplete_n: int, object_hook: Callable | None = None) -> dict:
    """Table A. Reads pool records only (keys: ITEM5_KEYS); other record types are read as far as `type`."""
    counts: collections.Counter = collections.Counter()
    groups = {"incomplete": _new_group(), "other_n_ok": _new_group()}
    closes = 0
    for _typ, t, rec in window_records(snap, counts, object_hook, origin_ms, until_ms, ("pool",)):
        if sealed_skip(rec, "pool", counts):
            continue
        if not isinstance(rec.get("pool"), str):
            counts["record_pool_not_str"] += 1
            continue
        v = I4.view_record(rec, "pool")
        closes += 1
        # I3: the predicates of item 4's nested incomplete(), over item 4's own view
        if not (v["gap"] is False and I4.isint(v["ws"]) and v["ws"] == 0 and I4.tip_ok(v)):
            continue
        bt = v["bt"]
        a = v["tval"] >= 1
        b = at_least_1(bt.get("orphaned_by_drop_n"))
        g = groups["incomplete" if (a or b) else "other_n_ok"]
        g["n"] += 1
        g["hours"][hour_label(t)] += 1
        g["arm"]["both" if (a and b) else ("tipauth_only" if a else ("orphaned_only" if b else "neither"))] += 1
        g["dn"][dropped_class(bt.get("dropped_n"))] += 1
        for f in FIELDS:
            if at_least_1(bt.get(f)):
                g["fld"][f] += 1
    found = {"n_ok": groups["incomplete"]["n"] + groups["other_n_ok"]["n"], "incomplete_n": groups["incomplete"]["n"]}
    expected = {"n_ok": expect_n_ok, "incomplete_n": expect_incomplete_n}
    equal = found == expected
    hours = sorted(set(groups["incomplete"]["hours"]) | set(groups["other_n_ok"]["hours"]))
    return {"population": "unsealed plain pool closes: gap exactly false, base_breaks_unresolved_settled exactly 0, base_tipauth.status ok, integer tipauth value (n_ok)",
            "keys_allowed": sorted(ITEM5_KEYS), "totals_equal_item4": equal, "totals": {"expected": expected, "found": found},
            "scan": scan_out(counts), "skipped": skipped_out(counts, ("pool",)), "unsealed_plain_pool_closes_read": closes,
            "result": ({"incomplete": _group_out(groups["incomplete"], hours), "other_n_ok": _group_out(groups["other_n_ok"], hours)} if equal else None)}


# ---- table B: gap records ---------------------------------------------------------------------------------------------------------------------

def missed_class(m: Any) -> str:
    if not I4.isint(m):
        return "not_int_or_missing"
    if m < MISSED_BOUNDS[0]:
        return "below_30"
    if m < MISSED_BOUNDS[1]:
        return "30_to_59"
    if m < MISSED_BOUNDS[2]:
        return "60_to_149"
    if m < MISSED_BOUNDS[3]:
        return "150_to_599"
    return "600_and_more"


def kind_set(gaps: Any) -> tuple[str, bool]:
    """(label, exactly-5-entries). Reads the `kind` of each entry and nothing else of it."""
    if not isinstance(gaps, list) or not gaps:
        return NO_ENTRIES, False
    kinds = set()
    for e in gaps:
        k = e.get("kind") if isinstance(e, dict) else None
        kinds.add(k if (isinstance(k, str) and k in SEVEN_KINDS) else "other")
    return "+".join(sorted(kinds)), len(gaps) == GAP_LEN_LIMIT


def _new_pop() -> dict:
    return {"n": 0, "gap_true": 0, "gap_false": 0, "gap_not_bool": 0, "len5": 0, "sets": collections.Counter(), "sets5": collections.Counter()}


def _pop_out(p: dict) -> dict:
    return {"n": p["n"], "gap_true": p["gap_true"], "gap_false": p["gap_false"], "gap_not_bool": p["gap_not_bool"], "gaps_len_5_n": p["len5"],
            "gap_true_by_kind_set": dict(sorted(p["sets"].items())),
            "gap_true_gaps_len_5_by_kind_set_lower_bound": dict(sorted(p["sets5"].items()))}


def _note_population(p: dict, rec: dict) -> None:
    p["n"] += 1
    g = rec.get("gap")
    if g is True:
        p["gap_true"] += 1
        lab, five = kind_set(rec.get("gaps"))
        if five:
            p["len5"] += 1
            p["sets5"][lab] += 1
        else:
            p["sets"][lab] += 1
    elif g is False:
        p["gap_false"] += 1
    else:
        p["gap_not_bool"] += 1


def gap_table(snap: list, origin_ms: int, until_ms: int, expect_kinds: dict, expect_pool_closes: int, expect_triggers_pv: int,
              object_hook: Callable | None = None) -> dict:
    """Table B. Keys read: GAP_TABLE_KEYS; inside a `gaps` entry only `kind`. An `outcome` record is read as far as `type`."""
    counts: collections.Counter = collections.Counter()
    raw_kinds: collections.Counter = collections.Counter()
    named: collections.Counter = collections.Counter()
    missed: collections.Counter = collections.Counter()
    flags: collections.Counter = collections.Counter()
    pools, trigs = _new_pop(), _new_pop()
    for typ, _t, rec in window_records(snap, counts, object_hook, origin_ms, until_ms, ("gap", "pool", "trigger")):
        if typ == "gap":
            k = rec.get("kind")
            raw_kinds[k if isinstance(k, str) else "null"] += 1  # item 4's key
            named[k if (isinstance(k, str) and k in SEVEN_KINDS) else "other"] += 1
            if k == "slot_jump":
                missed[missed_class(rec.get("missed_slots"))] += 1
            elif k == "socket_reconnect":
                fp = rec.get("flags_pools")
                flags["true" if fp is True else ("false" if fp is False else "missing_or_not_bool")] += 1
            continue
        if sealed_skip(rec, typ, counts):
            continue
        if not isinstance(rec.get("pool"), str):
            counts["record_pool_not_str"] += 1
            continue
        if typ == "trigger":
            if rec.get("variant") != "pv":
                continue
            _note_population(trigs, rec)
        else:
            _note_population(pools, rec)
    found = {"gap_records_by_kind": dict(sorted(raw_kinds.items())), "plain_pool_closes": pools["n"], "triggers_pv": trigs["n"]}
    expected = {"gap_records_by_kind": dict(sorted(expect_kinds.items())), "plain_pool_closes": expect_pool_closes, "triggers_pv": expect_triggers_pv}
    eq = {k: found[k] == expected[k] for k in found}
    equal = all(eq.values())
    result = None
    if equal:
        result = {"gap_records_by_kind": {k: named.get(k, 0) for k in KIND_LABELS},
                  "slot_jump_by_missed_slots": {c: missed.get(c, 0) for c in MISSED_CLASSES},
                  "socket_reconnect_by_flags_pools": {c: flags.get(c, 0) for c in FLAGS_POOLS_LABELS},
                  "unsealed_plain_pool_closes": _pop_out(pools), "unsealed_pv_triggers": _pop_out(trigs)}
    printable = {"gap_records_by_kind": label_counts(found["gap_records_by_kind"]), "plain_pool_closes": found["plain_pool_closes"], "triggers_pv": found["triggers_pv"]}
    printable_exp = {"gap_records_by_kind": label_counts(expected["gap_records_by_kind"]), "plain_pool_closes": expect_pool_closes, "triggers_pv": expect_triggers_pv}
    return {"keys_allowed": sorted(GAP_TABLE_KEYS), "totals_equal_item4": equal, "totals_equal_by_total": eq, "totals": {"expected": printable_exp, "found": printable},
            "scan": scan_out(counts), "skipped": skipped_out(counts, ("pool", "trigger")), "result": result}


# ---- the run ----------------------------------------------------------------------------------------------------------------------------------

def analyze(shadow_dir: Path, since_ms: int, until_ms: int, expect_code_sha: str, expect: dict, object_hook: Callable | None = None) -> dict:
    files = I4.hour_files(shadow_dir, since_ms, until_ms)
    snap = I4.snapshot(files)  # taken once; both tables read only up to these sizes
    starts, origin = find_origin(snap, since_ms, until_ms, object_hook)
    if origin is None:
        raise InputError("no start record at or after --since-ms", EXIT_NO_START)
    if origin["code_sha"] != expect_code_sha:
        raise InputError("the origin start record's code_sha does not match --expect-code-sha", EXIT_CODE_SHA)
    t0 = origin["t_ms"]
    a = item5_table(snap, t0, until_ms, expect["n_ok"], expect["incomplete_n"], object_hook)
    b = gap_table(snap, t0, until_ms, expect["gap_records_by_kind"], expect["plain_pool_closes"], expect["triggers_pv"], object_hook)
    return {"tool": TOOL, "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "spec": SPEC, "label": LABEL,
            "inputs": {"since_ms": since_ms, "until_ms": until_ms, "expect_code_sha": expect_code_sha,
                       "expect": {"n_ok": expect["n_ok"], "incomplete_n": expect["incomplete_n"], "plain_pool_closes": expect["plain_pool_closes"],
                                  "triggers_pv": expect["triggers_pv"], "gap_records_by_kind": label_counts(expect["gap_records_by_kind"])}},
            "window": {"n_starts_in_range": len(starts), "origin_t_ms": t0, "origin_code_sha": origin["code_sha"], "origin_code_sha_matches": True},
            "files": {"names": [f.name for f, _ in snap], "sizes": {f.name: n for f, n in snap}},
            "item5_table": a, "gap_table": b, "interpretations": INTERPRETATIONS}


def dumps(res: dict) -> str:
    return json.dumps(res, sort_keys=True, indent=1, allow_nan=False) + "\n"


def error_result(err: InputError) -> dict:
    return {"tool": TOOL, "error": err.msg, "exit_code": err.code}


OUT_EXISTS_MSG = "--out exists; it is never overwritten"
OUT_FAIL_MSG = "--out could not be created or written (its directory is missing or not writable, or the disk is full); no counts were printed"


def create_out(path: str):
    """The only open() of this tool: the exclusive create of --out (it never overwrites)."""
    return open(path, "x", encoding="utf-8")


def discard_out(fh, path: str) -> None:
    """Close and remove the file THIS run created, when the run ends in an error. Never raises."""
    try:
        fh.close()
    except OSError:
        pass
    try:
        os.unlink(path)
    except OSError:
        pass


def _nonneg_int(s: str) -> int:
    try:
        v = int(s)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a non-negative integer") from None
    if v < 0:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    return v


def _kinds_object(s: str) -> dict:
    msg = 'must be a JSON object of non-negative integers, such as {"silence":16,"slot_jump":762}'
    try:
        o = json.loads(s)
    except (ValueError, RecursionError):
        raise argparse.ArgumentTypeError(msg) from None
    if not isinstance(o, dict) or any(not I4.isint(v) or v < 0 for v in o.values()):
        raise argparse.ArgumentTypeError(msg)
    return o


def main(argv: list[str] | None = None, now_ms: int | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], epilog="EXIT CODE: 0 the tool ran and both tables' totals equal the flags. 2 usage, 3 shadow dir missing, "
                                 "4 no start record, 5 --expect-code-sha mismatch, 6 --out cannot be created or written (exists, or its directory is missing or not writable), 7 unused, 8 --until-ms off the item-4 schedule or "
                                 "the run is too early, 9 a table's totals differ from the flags (its result is null; the output file is written).")
    ap.add_argument("--shadow-dir", required=True)
    ap.add_argument("--since-ms", type=int, required=True)
    ap.add_argument("--until-ms", type=int, required=True, help="an item-4 scheduled instant (item4.on_schedule); the run must start at least item4.SETTLE_MS later")
    ap.add_argument("--expect-code-sha", required=True, help="40 hex: the origin (last) start record's code_sha must equal it")
    ap.add_argument("--expect-n-ok", type=_nonneg_int, required=True, help="item 4 item5_tip_incompleteness.plain_pool_closes.n_ok of the run at --until-ms")
    ap.add_argument("--expect-incomplete-n", type=_nonneg_int, required=True, help="item 4 item5_tip_incompleteness.plain_pool_closes.incomplete_n")
    ap.add_argument("--expect-plain-pool-closes", type=_nonneg_int, required=True, help="item 4 records.plain_pool_closes")
    ap.add_argument("--expect-triggers-pv", type=_nonneg_int, required=True, help="item 4 records.triggers_pv")
    ap.add_argument("--expect-gap-records-by-kind", type=_kinds_object, required=True, help='item 4 records.gap_records_by_kind as a JSON object, e.g. \'{"silence":16}\'')
    ap.add_argument("--out", required=True, help="JSON file, created exclusively; an existing file is never overwritten (exit 6)")
    a = ap.parse_args(argv)
    if not re.fullmatch(r"[0-9a-f]{40}", a.expect_code_sha):
        ap.error("--expect-code-sha must be 40 lowercase hex characters")
    if a.until_ms < a.since_ms:
        ap.error("--until-ms is before --since-ms")
    now = int(time.time() * 1000) if now_ms is None else now_ms
    expect = {"n_ok": a.expect_n_ok, "incomplete_n": a.expect_incomplete_n, "plain_pool_closes": a.expect_plain_pool_closes, "triggers_pv": a.expect_triggers_pv,
              "gap_records_by_kind": a.expect_gap_records_by_kind}
    fh = None
    try:
        if not I4.on_schedule(a.since_ms, a.until_ms) or a.until_ms - a.since_ms > HORIZON_MS:
            raise InputError(f"--until-ms {a.until_ms} is not an item-4 scheduled instant for --since-ms {a.since_ms} (+6 h, +24 h, +36 h, +48 h, then every 12 h)", EXIT_SCHEDULE)
        if now < a.until_ms + I4.SETTLE_MS:
            raise InputError(f"the run starts at {now}, earlier than --until-ms plus {I4.SETTLE_MS} ms (item 4's settle time)", EXIT_SCHEDULE)
        try:
            fh = create_out(a.out)  # exclusive, before the analysis: a result that cannot be kept is never computed
        except FileExistsError:
            raise InputError(OUT_EXISTS_MSG, EXIT_OUT) from None
        except OSError:
            raise InputError(OUT_FAIL_MSG, EXIT_OUT) from None
        d = Path(a.shadow_dir)
        if not d.is_dir():
            raise InputError("shadow directory missing", EXIT_NO_DIR)
        res = analyze(d, a.since_ms, a.until_ms, a.expect_code_sha, expect)
        text = dumps(res)
        try:
            fh.write(text)
            fh.close()
        except OSError:
            raise InputError(OUT_FAIL_MSG, EXIT_OUT) from None
    except InputError as err:
        if fh is not None:
            discard_out(fh, a.out)  # the file this run created; an existing --out was never opened by it
        sys.stdout.write(dumps(error_result(err)))
        return err.code
    except Exception:
        if fh is not None:
            discard_out(fh, a.out)
        raise
    sys.stdout.write(text)
    return 0 if (res["item5_table"]["totals_equal_item4"] and res["gap_table"]["totals_equal_item4"]) else EXIT_TOTALS


if __name__ == "__main__":
    raise SystemExit(main())
