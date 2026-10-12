#!/usr/bin/env python3
"""H5 shadow, report-only tipauth field: the frozen ITEM-4 COUNT LIST (PR #610, quant-proof round 3, comment 6121123397; C5 and B' items from
comment 6120949717; ruling B and the first list from comment 6120633640; the newest comment governs).

COUNT-ONLY. It reads decision-time and bookkeeping fields of start / hb / trigger / pool / gap records of the H5 shadow's hourly files
(h5-shadow-YYYY-MM-DDTHH.jsonl) and nothing else. No outcome, no fill, no P&L, no join to any pick set. No network call (the RPC census
of item 7 is a later step: this tool only lists the signatures to look up). It is written before the data exists and may only be tightened.
Where a definition in the comments is ambiguous it takes the STRICTER reading; the choices are listed in AMBIGUITIES below (all RULED).

The read is restricted by ALLOWED_KEYS (a flat allow-list of every key name the tool ever looks at, on any nesting level). The test
tools/test_h5_tipauth_item4.py wraps every parsed object in a dict that records accesses and fails on any key outside the list. That test is a
floor, not a proof: it cannot see C-level bypasses (str(rec), ==, dict.get(rec, k), update, |) or a branch its fixture does not exercise.

  python3 tools/h5_tipauth_item4.py --shadow-dir DIR --since-ms T0 --until-ms T1 --expect-code-sha SHA40 [--out FILE] [--table]
  python3 tools/h5_tipauth_item4.py --schedule

Round 2 (PR #613, quant-proof comment 6121547427, changes 1-12; reviewer comment 6121321849): the verdict key is `tool_rules_clear` (NOT a
proposal verdict: the RPC census, the trigger print, decision-to-send and C6 are outside this tool); --until-ms and --expect-code-sha are
required; the file list and sizes are snapshotted once and read only up to those sizes, so a record appended during the run is not seen;
bytes that are not UTF-8 are counted under bad_line and the line is skipped; a malformed record never raises (it is counted or undetermined).

THE PEEKING RULE (quant-proof comment 6121547427, section 4; the tool is not run exactly once, it is run on a fixed schedule and one read decides):
  1. Scheduled instants, as --until-ms: +6 h (interim), +24 h, +36 h, +48 h, then every 12 h, each counted from --since-ms (T0); `--schedule` prints them.
     Each run starts after its instant. No other run.
  2. The deciding read is the first scheduled instant from +24 h at which the tool's own duration line is met. Nobody chooses it.
  3. Exactly-0 rules (consistency, any would-pass left D, census found) are cumulative from the origin. A failure at any read, interim included, is
     final. A counterexample survives a restart: "no proposal on this definition" holds until the definition is tightened by amendment.
  4. Share rules (90%, 5%, 1%) are judged at the deciding read only. A fail there is final for this restart. No extension to wash it out. A new
     count needs a recorded code or config change that addresses the cause; the same head restarted to try again is not a fresh count.
  5. A null (short) result extends to the next scheduled instant.
  6. Every output is saved with --out, its sha256 goes in the notebook, none is deleted, and an interim one is labelled "interim, not the deciding read".
  7. After the first read of any kind, the tool, the thresholds, C1 to C6, the margin and this schedule change only by tightening, through a PR with QP.
(T0 for this count = 1791797643640, job #817, code_sha 5db78a66...; an earlier start at 1791796005847, job #810, lies before T0 and is outside the window.)

Round 3 (PR #613; quant-proof comment 6121641942 on PR #614, "Required changes" R1, R2, R12): C1-C7 here are the executor gate's conditions.
  R1 C3 allowlist: boost_src in ("pda", "event_authority"); "none" and everything else fail C3.
  R2 C7 record self-consistency (a margin, b slots, c cover, d dropped_list, e union_ne_ws, f sealed + pv only), each part reported alone and together.
  R12 one predicate: would_pass(trigger_record) -> (bool, reason) below is a pure function; its reason strings and their ORDER are those of the executor's
  tipauth_gate (origin/claude/h5-exec-tipauth-gate tools/h5_executor.py) so a later cross-test can assert 0 disagreements on (bool, reason).
  would-pass is pv-only (C7 f); fv triggers that pass everything else are reported separately as "not eligible: variant".
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import re
import statistics
import sys
from pathlib import Path
from typing import Any, Callable

TOOL = "h5_tipauth_item4"
SPEC = {"round3": "PR #610 comment 6121123397", "round2": "PR #610 comment 6120949717", "round1": "PR #610 comment 6120633640",
        "pr613_round2": "PR #613 comment 6121547427 (quant-proof), 6121321849 (reviewer)"}

TIPAUTH_FIELD = "base_breaks_unresolved_settled_tipauth"
UNION_FIELD = "base_breaks_unresolved_settled_union"
WS_FIELD = "base_breaks_unresolved_settled"
DROPPED_LIST_MAX = 20  # TIPAUTH_DROPPED_LIST_MAX in tools/h5_shadow.py
RSS_LIMIT_BYTES = 1_000_000_000  # the 1.0 GB re-read rule (stricter of 1e9 and 2**30)
HOUR_MS = 3_600_000
DAY_MS = 86_400_000
BOUNDARY_MS = 120_000  # "within 2 minutes of a UTC hour boundary"
MIN_HOURS, MIN_NOGAP_PV_WS1 = 24.0, 10
COVERAGE_MIN, INCOMPLETE_MAX, LEFT_D_MAX, LEFT_D_MIN_TESTED = 0.90, 0.05, 0.01, 100
INCOMPLETE_MIN_OK = 100  # item 5: fewer ok closes than this is a null (short), not a pass
HB_HOLE_MS = 600_000  # a heartbeat hole longer than this is not covered time
INT_BOUND = 2 ** 63  # a counter / slot / index beyond this is not an integer for this tool (keeps every sum and median finite)
SCHEDULE_OFFSETS_H = (6, 24, 36, 48)  # +6 h is the interim read; then every 12 h after +48 h
SCHEDULE_HELP = ("Print the fixed --until-ms instants computed from --since-ms (T0) and exit (quant-proof comment 6121547427, section 4): "
                 "T0+6h (interim, not the deciding read), T0+24h, T0+36h, T0+48h, then every 12 h. Each run starts after its instant. No other run. "
                 "The tool REFUSES (exit 4, JSON error) any --until-ms that is not one of these instants. The deciding read is the first instant from +24h "
                 "at which the duration line is met. See the module docstring for the peeking rule.")


def schedule(since_ms: int, horizon_h: int = 24 * 14) -> list[int]:
    hs = list(SCHEDULE_OFFSETS_H) + list(range(60, horizon_h + 1, 12))
    return [since_ms + h * HOUR_MS for h in hs]


def on_schedule(since_ms: int, until_ms: int) -> bool:
    d = until_ms - since_ms
    if d % HOUR_MS:
        return False
    h = d // HOUR_MS
    return h == 6 or (h >= 24 and (h - 24) % 12 == 0)


# Every key name this tool ever reads, on any level (top level of a record, base_union, base_tipauth, hb counters / tip_union / mem).
# No outcome, price, fill or P&L key is, or may be, in this set.
ALLOWED_KEYS = frozenset({
    # all records
    "type", "t_ms", "sealed", "h5_look2_sealed",
    # start
    "code_sha", "rule",
    # gap
    "kind",
    # trigger / pool (decision-time and bookkeeping)
    "variant", "pool", "gap", "slot", "boost_src", "t_detect_ms", WS_FIELD, UNION_FIELD, TIPAUTH_FIELD, "base_union", "base_tipauth",
    # base_union
    "status", "tip_only", "both_diff", "tip_first_slot", "tip_hw_slot_global",
    # base_tipauth
    "dropped_n", "dropped_first_slot", "dropped_last_slot", "cover_lo", "cover_hi", "kept_ws_only_before_cover", "kept_ws_only_above_cover",
    "kept_ws_only_sig_on_tip", "dropped_boost_n", "dropped_sells_n", "orphaned_by_drop_n", "tipauth_nosettle", "dropped", "margin_slots",
    # hb
    "counters", "tip_union", "mem", "union_errors", "rss_kb", "rss_anon_kb", "hwm_kb", "est_mb", "est_tipauth_mb", "tipauth_wsonly",
    "tipauth_tip_sigs", "union_members",
})

BU_KEYS = ("status", "tip_only", "both_diff", "tip_first_slot", "tip_hw_slot_global")
BT_KEYS = ("status", "dropped_n", "dropped_first_slot", "dropped_last_slot", "cover_lo", "cover_hi", "kept_ws_only_before_cover",
           "kept_ws_only_above_cover", "kept_ws_only_sig_on_tip", "dropped_boost_n", "dropped_sells_n", "orphaned_by_drop_n", "tipauth_nosettle",
           "dropped", "margin_slots")
HB_MEM_KEYS = ("rss_kb", "rss_anon_kb", "hwm_kb", "est_mb", "est_tipauth_mb", "tipauth_wsonly", "tipauth_tip_sigs", "union_members")

AMBIGUITIES = [
    ("A01", "Which start record, when more than one has t_ms >= T0",
     "Round 3 / round 1: 'Counted only from the shadow restart ... (start record code_sha = that merge commit)'. Chosen: the LAST start record at or after T0 "
     "is the run origin (stricter: a later restart resets the 24 h); all start t_ms and code_sha are reported; n_starts_after_t0 > 1 blocks tool_rules_clear. "
     "A rerun with --since-ms at the later start is a new clock and needs its own clean-checkout record. The origin's code_sha must equal --expect-code-sha (change 1)."),
    ("A02", "Elapsed and covered hours (replaced by change 10)",
     "Item 1 uses covered_h: window_end = min(--until-ms, largest used t_ms); a hole is any interval longer than 600,000 ms between consecutive points of "
     "[origin, hb t_ms..., window_end]; covered_h = (window_end - origin - sum(holes)) / 3,600,000. elapsed_h, covered_h, hb_max_gap_ms and n_holes are printed."),
    ("A03", "'trigger records' in the 90% coverage (item 2)",
     "Item 2: 'on at least 90% of trigger records'. Chosen: pv and fv triggers; both the share over ALL triggers and the share over no-gap pv triggers with ws >= 1 "
     "must reach 90% (stricter). 'ok' needs status == 'ok' AND a non-null integer value. A known share below 90% is a fail even if the other share is null; null "
     "only when no known share fails and one is unknown (change 7)."),
    ("A04", "no-gap", "'gap false' is read as gap is exactly False; a missing or null gap is not no-gap."),
    ("A05", "Union ok (item 2)", "Item 2: 'base_union.status ok on at least 90%'. Chosen: status == 'ok' and the union value base_breaks_unresolved_settled_union a non-null integer."),
    ("A06", "Would-pass variants",
     "Round 3 (R2 C7 f): would-pass is pv-only, by construction (would_pass_n == would_pass_pv_n). A fv trigger that passes everything else is counted separately as "
     "fv_not_eligible_variant_n ('not eligible: variant'). The executor acts on pv only. The pv plus fv sum is never called trades."),
    ("A07", "C3 boost_src (R1, round 3)",
     "boost_src must be one of 'pda', 'event_authority'. 'none' (no BOOST identity known, so dropped_boost_n is 0 by construction), 'behavioural', any other string and a "
     "missing key fail C3. would_pass_by_boost_src stays reported."),
    ("A08", "C6 'v1 only'", "Not decidable from records. The start record 'rule' ids are reported; C6 is NOT applied and is listed as outside_tool_pending."),
    ("A09", "'removes' per C condition (item 6)",
     "Reported three ways: sequential funnel C1..C4, C5a, C5b, C5c; leave-one-out (passes all others, fails this one); and for C5 parts among C1-C4 triggers: "
     "failing the part at all, failing only that part, failing all three, failing any."),
    ("A10", "Tip incompleteness share (item 5) (replaced by change 2)",
     "Share of ws-0 no-gap plain pool closes with tipauth >= 1 OR orphaned_by_drop_n >= 1, over closes with base_tipauth.status ok and a non-null value (n_ok). "
     "met is null when n_ok < 100 (INCOMPLETE_MIN_OK), else share_of_ok <= 5%. Both shares stay reported."),
    ("A11", "Consistency checks apply to",
     "Every unsealed trigger and pool record that carries a base_tipauth dict. 'a base_tipauth without the dropped key' is counted literally (an exception record "
     "counts here and in union_errors). A malformed dropped entry (not [str, None-or-int, int slot]) is counted as its own violation. A missing trigger slot or "
     "margin_slots when the trigger bound must be checked is a violation. union_errors = largest hb counters.union_errors in the run; an hb whose counters dict lacks "
     "the key is a 0-valued sample, an hb whose value is present but not an integer counts as 1 (fail closed). A trigger or pool record whose pool is not a string is "
     "left out of every count and counted in record_pool_not_str (must be 0)."),
    ("A12", "'dropped_n <= 20 with last entry slot != dropped_last_slot'", "Also applied to an empty list: dropped_first_slot / dropped_last_slot must then be null (status ok)."),
    ("A13", "Margin test (item 10) (amended by changes 5 and 8)",
     "'Tested' = entries classified still dropped or left D. Undetermined entries are reported and excluded from the denominator. The close used is the FIRST close of "
     "the pool with t_ms >= the trigger's; if it is not status ok with integer cover_hi >= the trigger slot, all that trigger's entries are undetermined (no skipping "
     "ahead). Distinct entries are keyed (pool, signature, event_index) (class: left D if any row is, else still dropped if any, else undetermined). "
     "fire(t, l) = (l >= 1) if t < 100 else (l / t > 0.01); met is false if fire on the per-row counts or on the distinct counts."),
    ("A14", "'Fully enumerated' on a would-pass trigger",
     "dropped is a list and len(dropped) == dropped_n on the TRIGGER record (round 3 definition). Any failure is a consistency failure. With no would-pass trigger the "
     "threshold is null (change 4)."),
    ("A15", "Hour-boundary share", "Triggers (pv and fv, all statuses) with t_ms within 120 s of a UTC hour boundary; share with status tip_pending. Reported only; no threshold is declared."),
    ("A16", "Item 8 (trigger print on the tip, equal pre-trade q and base) and item 9 decision-to-send ms",
     "Item 8 and decision-to-send need the tip tape and the executor's records, outside what this tool may read: null, in outside_tool_pending. Change 9 adds the "
     "shadow-side lag t_ms - t_detect_ms (report only)."),
    ("A17", "RSS rule", "Item 9: 'rss against the 1.0 GB re-read rule'. Chosen: met only if the largest hb rss_kb * 1024 < 1,000,000,000."),
    ("A18", "tool_rules_clear (replaces the old verdict key, change 3)",
     "Computed from the rules this tool owns only. true does NOT mean a proposal is allowed: outside_tool_pending (RPC census, item 8, decision-to-send, C6) is still "
     "open and listed. No empty class passes (change 4)."),
    ("A19", "Run window for non-start records", "A record belongs to the run if its integer t_ms is >= the origin start t_ms and <= --until-ms; records without an integer t_ms are counted and ignored."),
]


def isint(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool) and -INT_BOUND < x < INT_BOUND


def flag(x: Any) -> bool:
    """A sealed flag fails closed: anything but absent / null / False counts as sealed."""
    return not (x is None or x is False)


def entry_ok(e: Any) -> bool:
    return isinstance(e, list) and len(e) == 3 and isinstance(e[0], str) and (e[1] is None or isint(e[1])) and isint(e[2])


def bucket(x: Any) -> str:
    if not isint(x):
        return "null"
    return "0" if x == 0 else ("1+" if x > 0 else "neg")


def share(a: int, b: int) -> float | None:
    return None if b == 0 else round(a / b, 6)


def summ(vals: list) -> dict:
    ints = sorted(v for v in vals if isint(v))
    n = len(ints)
    out: dict = {"n": n, "non_int": len(vals) - n, "min": None, "median": None, "p90": None, "max": None, "sum": None}
    if n:
        out.update({"min": ints[0], "median": statistics.median(ints), "p90": ints[max(0, math.ceil(0.9 * n) - 1)], "max": ints[-1], "sum": sum(ints)})
    return out


def finite_num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and abs(x) < 1e300


# ---- reading --------------------------------------------------------------------------------------------------------------------------------

_FILE_RE = re.compile(r"^h5-shadow-(\d{4})-(\d{2})-(\d{2})T(\d{2})\.jsonl$")


def hour_files(shadow_dir: Path, since_ms: int, until_ms: int | None) -> list[Path]:
    import calendar
    out = []
    for p in sorted(shadow_dir.iterdir()):
        m = _FILE_RE.match(p.name)
        if not m:
            continue
        y, mo, d, h = (int(g) for g in m.groups())
        start = calendar.timegm((y, mo, d, h, 0, 0)) * 1000
        # one hour of slack before T0 (a file is named by its hour of writing) and 24 h more for the report-only pre-origin lag window
        if start + HOUR_MS <= since_ms - DAY_MS - HOUR_MS or (until_ms is not None and start > until_ms):
            continue
        out.append(p)
    return out


def snapshot(files: list[Path]) -> list[tuple[Path, int]]:
    """File names and sizes, taken ONCE. Both passes read only up to these sizes."""
    snap = []
    for f in files:
        try:
            snap.append((f, f.stat().st_size))
        except OSError:
            continue
    return snap


def _bad_const(x: str) -> Any:
    raise ValueError("non-finite JSON constant")


def _parse_float(x: str) -> float:
    f = float(x)
    if not math.isfinite(f):
        raise ValueError("non-finite float")
    return f


def iter_records(snap: list[tuple[Path, int]], counts: collections.Counter, object_hook: Callable | None) -> Any:
    for f, size in snap:
        try:
            with open(f, "rb") as fh:
                data = fh.read(size)
        except OSError:
            counts["unreadable_file"] += 1
            continue
        for raw in data.split(b"\n"):
            raw = raw.strip()
            if not raw:
                continue
            try:
                line = raw.decode("utf-8")
            except UnicodeDecodeError:
                # errors="replace" would let a corrupt line parse; instead it is counted as a bad line and skipped
                raw.decode("utf-8", errors="replace")
                counts["bad_line"] += 1
                counts["bad_line_not_utf8"] += 1
                continue
            try:
                rec = json.loads(line, object_hook=object_hook, parse_constant=_bad_const, parse_float=_parse_float)  # NaN / Infinity: a bad line
            except (ValueError, RecursionError):
                counts["bad_line"] += 1
                continue
            if not isinstance(rec, dict):
                counts["bad_line"] += 1
                continue
            yield rec


def view_sub(d: Any, keys: tuple, has_dropped: bool = False) -> dict | None:
    if not isinstance(d, dict):
        return None
    v = {k: d.get(k) for k in keys}
    if has_dropped:
        v["_has_dropped"] = "dropped" in d
    return v


def view_record(rec: dict, typ: str) -> dict:
    """The only place a trigger / pool record is read. Plain dict out."""
    return {"type": typ, "pool": rec.get("pool"), "variant": rec.get("variant"), "t_ms": rec.get("t_ms"), "gap": rec.get("gap"), "slot": rec.get("slot"),
            "boost_src": rec.get("boost_src"), "t_detect_ms": rec.get("t_detect_ms"), "ws": rec.get(WS_FIELD), "uval": rec.get(UNION_FIELD),
            "tval": rec.get(TIPAUTH_FIELD), "bu": view_sub(rec.get("base_union"), BU_KEYS), "bt": view_sub(rec.get("base_tipauth"), BT_KEYS, True),
            # the one predicate, evaluated on the raw record here (the only place it is read): the set of failing GATE_REASONS names
            "fails": gate_fails(rec) if typ == "trigger" else None}


# ---- conditions -----------------------------------------------------------------------------------------------------------------------------

def bt_get(v: dict, k: str) -> Any:
    return None if v["bt"] is None else v["bt"].get(k)


TIPAUTH_MARGIN_SLOTS = 4  # C7 (a): a change of margin is a new rule
TIPAUTH_DROPPED_MAX = 5  # C4
BOOST_SRC_OK = ("pda", "event_authority")  # C3 allowlist (R1)

# The executor's order (tipauth_gate on origin/claude/h5-exec-tipauth-gate, C7 inserted after C5 as R2 says; C6 "rule v1" is not decidable from records:
# the tool does not test it, a cross-test must feed rule v1). would_pass() returns the FIRST failing reason in this order.
GATE_REASONS = ("not_a_record", "ws_not_ge1", "gap", "union_missing", "tipauth_missing", "union_status", "tipauth_status", "value_not_zero",
                "tip_only", "both_diff", "dropped_boost", "boost_src", "dropped_n", "kept_ws_only_sig_on_tip", "orphaned_by_drop", "nosettle",
                "dropped_first_slot", "margin", "slots", "cover", "dropped_list", "union_ne_ws", "sealed_record", "variant_not_pv")
# condition -> reasons (the funnel / leave-one-out groups)
GROUPS = {"C1": ("not_a_record", "ws_not_ge1", "gap", "union_missing", "tipauth_missing", "union_status", "tipauth_status", "value_not_zero"),
          "C2": ("tip_only", "both_diff"), "C3": ("dropped_boost", "boost_src"), "C4": ("dropped_n", "kept_ws_only_sig_on_tip"),
          "C5a": ("orphaned_by_drop",), "C5b": ("nosettle",), "C5c": ("dropped_first_slot",),
          "C7a": ("margin",), "C7b": ("slots",), "C7c": ("cover",), "C7d": ("dropped_list",), "C7e": ("union_ne_ws",),
          "C7f": ("sealed_record", "variant_not_pv")}
COND_NAMES = list(GROUPS)
C7_NAMES = ["C7a", "C7b", "C7c", "C7d", "C7e", "C7f"]


def _i(x: Any) -> bool:
    """An integer, not a bool, no magnitude bound (same as the executor's _is_int)."""
    return isinstance(x, int) and not isinstance(x, bool)


def _d0(x: Any) -> bool:
    return _i(x) and x == 0


def _dropped_list_ok(bt: dict, dn: Any, first: Any, last: Any) -> bool:
    dl = bt.get("dropped")
    if not isinstance(dl, list) or not _i(dn) or len(dl) != dn or not dl or not _i(first) or not _i(last):
        return False
    seen = set()
    for e in dl:
        if not (isinstance(e, list) and len(e) == 3 and isinstance(e[0], str) and e[0] != "" and (e[1] is None or _i(e[1])) and _i(e[2])):
            return False
        if not first <= e[2] <= last:
            return False
        if (e[0], e[1]) in seen:
            return False
        seen.add((e[0], e[1]))
    return dl[0][2] == first and dl[-1][2] == last


def gate_fails(rec: Any) -> frozenset:
    """Every GATE_REASONS name whose condition fails on a trigger record. Pure, fail closed (a missing key, a null, a bool where an integer is wanted,
    a non-dict anywhere is a failure), never raises. Reads only ALLOWED_KEYS."""
    if not isinstance(rec, dict):
        return frozenset(GATE_REASONS)
    f: set = set()
    ws, slot = rec.get(WS_FIELD), rec.get("slot")
    bu, bt = rec.get("base_union"), rec.get("base_tipauth")
    bu_d, bt_d = isinstance(bu, dict), isinstance(bt, dict)
    g = (lambda k: bu.get(k)) if bu_d else (lambda k: None)
    h = (lambda k: bt.get(k)) if bt_d else (lambda k: None)
    if not (_i(ws) and ws >= 1):
        f.add("ws_not_ge1")
    if rec.get("gap") is not False:
        f.add("gap")
    if not bu_d:
        f.add("union_missing")
    if not bt_d:
        f.add("tipauth_missing")
    if g("status") != "ok":
        f.add("union_status")
    if h("status") != "ok":
        f.add("tipauth_status")
    if not _d0(rec.get(TIPAUTH_FIELD)):
        f.add("value_not_zero")
    if not _d0(g("tip_only")):
        f.add("tip_only")
    if not _d0(g("both_diff")):
        f.add("both_diff")
    if not _d0(h("dropped_boost_n")):
        f.add("dropped_boost")
    src = rec.get("boost_src")
    if not (isinstance(src, str) and src in BOOST_SRC_OK):
        f.add("boost_src")
    dn = h("dropped_n")
    if not (_i(dn) and 1 <= dn <= TIPAUTH_DROPPED_MAX):
        f.add("dropped_n")
    if not _d0(h("kept_ws_only_sig_on_tip")):
        f.add("kept_ws_only_sig_on_tip")
    if not _d0(h("orphaned_by_drop_n")):
        f.add("orphaned_by_drop")
    if not _d0(h("tipauth_nosettle")):
        f.add("nosettle")
    first, last, lo, hi = h("dropped_first_slot"), h("dropped_last_slot"), h("cover_lo"), h("cover_hi")
    if not (_i(first) and _i(lo) and first > lo):
        f.add("dropped_first_slot")
    # C7 (a)-(e)
    if not (_i(h("margin_slots")) and h("margin_slots") == TIPAUTH_MARGIN_SLOTS):
        f.add("margin")
    if not (all(_i(x) for x in (slot, lo, hi, first, last)) and 0 <= lo < first <= last <= hi <= slot - TIPAUTH_MARGIN_SLOTS):
        f.add("slots")
    tfs, hw = g("tip_first_slot"), g("tip_hw_slot_global")
    if not (_i(lo) and _i(tfs) and tfs == lo and _i(hw) and _i(slot) and _i(hi) and hi == min(hw, slot) - TIPAUTH_MARGIN_SLOTS):
        f.add("cover")
    if not (bt_d and _dropped_list_ok(bt, dn, first, last)):
        f.add("dropped_list")
    uv = rec.get(UNION_FIELD)
    if not (_i(uv) and _i(ws) and uv == ws):
        f.add("union_ne_ws")
    # C7 (f). The tool also skips sealed records before it gets here (flag(): anything but absent / null / False) and counts them.
    if rec.get("h5_look2_sealed") is True or rec.get("sealed") is True:
        f.add("sealed_record")
    if rec.get("variant") != "pv":
        f.add("variant_not_pv")
    return frozenset(f)


def would_pass(trigger_record: Any) -> tuple[bool, str]:
    """The one would-pass predicate (C1-C5 and C7; C6 "rule v1" is not decidable from records). Pure, fail closed, never raises.
    Returns (True, "ok") or (False, <first failing reason>), the reasons checked IN THIS ORDER (GATE_REASONS, the executor's tipauth_gate order,
    C7 after C5): not_a_record, ws_not_ge1, gap, union_missing, tipauth_missing, union_status, tipauth_status, value_not_zero, tip_only, both_diff,
    dropped_boost, boost_src, dropped_n, kept_ws_only_sig_on_tip, orphaned_by_drop, nosettle, dropped_first_slot, margin, slots, cover, dropped_list,
    union_ne_ws, sealed_record, variant_not_pv."""
    fails = gate_fails(trigger_record)
    for r in GATE_REASONS:
        if r in fails:
            return False, r
    return True, "ok"


def conditions(v: dict) -> dict[str, bool]:
    """C1..C5 and C7a..C7f from the view's `fails` (C6 is not decidable from records). True = the condition holds."""
    fails = v.get("fails")
    if fails is None:
        fails = frozenset(GATE_REASONS)
    return {name: not any(r in fails for r in rs) for name, rs in GROUPS.items()}


def would_pass_view(c: dict) -> bool:
    return all(c.values())


def fully_enumerated(v: dict) -> bool:
    bt = v["bt"]
    return bt is not None and isinstance(bt.get("dropped"), list) and isint(bt.get("dropped_n")) and len(bt["dropped"]) == bt["dropped_n"]


# ---- consistency (item 4) -------------------------------------------------------------------------------------------------------------------

CONSIST_NAMES = [
    "dropped_n0_tipauth_differs_from_union", "nonnull_tipauth_status_not_ok", "union_errors", "nosettle_below_tipauth", "orphaned_ge1_with_dropped_n0",
    "ok_with_null_orphaned_nosettle_or_dropped_n", "notok_with_nonnull_orphaned_nosettle_or_dropped_n", "ok_dropped_not_list_of_min_dropped_n_20",
    "notok_with_nonnull_dropped", "base_tipauth_without_dropped_key", "entry_slot_outside_cover", "trigger_entry_above_slot_minus_margin",
    "first_entry_slot_ne_dropped_first_slot", "last_entry_slot_ne_dropped_last_slot", "repeated_signature_event_index", "dropped_entry_malformed",
    "would_pass_trigger_not_fully_enumerated", "record_pool_not_str"]


def consistency_record(v: dict, wp: bool) -> list[str]:
    bt = v["bt"]
    if bt is None:
        return []
    bad: list[str] = []
    st, dn = bt.get("status"), bt.get("dropped_n")
    ok = st == "ok"
    if dn == 0 and isint(dn) and v["tval"] != v["uval"]:
        bad.append("dropped_n0_tipauth_differs_from_union")
    if v["tval"] is not None and not ok:
        bad.append("nonnull_tipauth_status_not_ok")
    if isint(bt.get("tipauth_nosettle")) and isint(v["tval"]) and bt["tipauth_nosettle"] < v["tval"]:
        bad.append("nosettle_below_tipauth")
    if isint(bt.get("orphaned_by_drop_n")) and bt["orphaned_by_drop_n"] >= 1 and dn == 0 and isint(dn):
        bad.append("orphaned_ge1_with_dropped_n0")
    trio = (bt.get("orphaned_by_drop_n"), bt.get("tipauth_nosettle"), dn)
    if ok and any(x is None for x in trio):
        bad.append("ok_with_null_orphaned_nosettle_or_dropped_n")
    if not ok and any(x is not None for x in trio):
        bad.append("notok_with_nonnull_orphaned_nosettle_or_dropped_n")
    dl = bt.get("dropped")
    if ok and not (isinstance(dl, list) and isint(dn) and len(dl) == min(dn, DROPPED_LIST_MAX)):
        bad.append("ok_dropped_not_list_of_min_dropped_n_20")
    if not ok and dl is not None:
        bad.append("notok_with_nonnull_dropped")
    if not bt.get("_has_dropped"):
        bad.append("base_tipauth_without_dropped_key")
    if isinstance(dl, list):
        slots, keys, malformed = [], [], False
        for e in dl:
            if entry_ok(e):
                slots.append(e[2])
                keys.append((e[0], e[1]))
            else:
                malformed = True
        if malformed:
            bad.append("dropped_entry_malformed")
        lo, hi = bt.get("cover_lo"), bt.get("cover_hi")
        if ok and slots and (not (isint(lo) and isint(hi)) or any(s < lo or s > hi for s in slots)):
            bad.append("entry_slot_outside_cover")
        if ok and v["type"] == "trigger" and slots:
            mg = bt.get("margin_slots")
            if not (isint(v["slot"]) and isint(mg)) or any(s > v["slot"] - mg for s in slots):
                bad.append("trigger_entry_above_slot_minus_margin")
        if ok and not malformed:
            if (slots[0] if slots else None) != bt.get("dropped_first_slot"):
                bad.append("first_entry_slot_ne_dropped_first_slot")
            if isint(dn) and dn <= DROPPED_LIST_MAX and (slots[-1] if slots else None) != bt.get("dropped_last_slot"):
                bad.append("last_entry_slot_ne_dropped_last_slot")
        if len(set(keys)) != len(keys):
            bad.append("repeated_signature_event_index")
    if wp and not fully_enumerated(v):
        bad.append("would_pass_trigger_not_fully_enumerated")
    return bad


# ---- the analysis ---------------------------------------------------------------------------------------------------------------------------

def tri_cross() -> dict:
    cells: collections.Counter = collections.Counter()
    for ws in ("0", "1+"):
        for un in ("0", "1+", "null"):
            for ta in ("0", "1+", "null"):
                for g in ("F", "T"):
                    cells[f"ws={ws}|union={un}|tipauth={ta}|gap={g}"] = 0
                    if un != "null":
                        cells[f"ws={ws}|union={un}|tipauth={ta}|gap={g}|tip_first_slot=null"] = 0
                        cells[f"ws={ws}|union={un}|tipauth={ta}|gap={g}|tip_first_slot=set"] = 0
    return cells


def cross_key(v: dict) -> list[str]:
    g = "F" if v["gap"] is False else ("T" if v["gap"] is True else "null")
    ub = bucket(v["uval"])
    k = f"ws={bucket(v['ws'])}|union={ub}|tipauth={bucket(v['tval'])}|gap={g}"
    out = [k]
    if ub != "null":
        tfs = None if v["bu"] is None else v["bu"].get("tip_first_slot")
        out.append(k + ("|tip_first_slot=null" if tfs is None else "|tip_first_slot=set"))
    return out


def status_of(v: dict) -> str:
    if v["bt"] is None:
        return "missing"
    s = v["bt"].get("status")
    return "null" if s is None else str(s)


def tip_ok(v: dict) -> bool:
    return v["bt"] is not None and v["bt"].get("status") == "ok" and isint(v["tval"])


def union_ok(v: dict) -> bool:
    return v["bu"] is not None and v["bu"].get("status") == "ok" and isint(v["uval"])


def covered(origin: int, window_end: int, hb_ts: list[int]) -> dict:
    """Item 1 covered hours: every interval longer than HB_HOLE_MS between consecutive points of [origin, hb..., window_end] is a hole."""
    pts = [origin] + sorted(t for t in hb_ts if origin <= t <= window_end) + [window_end]
    gaps = [b - a for a, b in zip(pts, pts[1:])]
    holes = [g for g in gaps if g > HB_HOLE_MS]
    cov_ms = (window_end - origin) - sum(holes)
    return {"elapsed_h": round((window_end - origin) / HOUR_MS, 4), "covered_h": round(cov_ms / HOUR_MS, 4), "hb_max_gap_ms": max(gaps) if gaps else 0,
            "n_holes": len(holes), "hole_ms_total": sum(holes), "hb_hole_ms": HB_HOLE_MS}


def classify_margin(trig: list[dict], pools: list[dict], wp_ids: set) -> dict:
    closes_by_pool: dict[str, list[dict]] = collections.defaultdict(list)
    for v in pools:
        closes_by_pool[v["pool"]].append(v)
    for lst in closes_by_pool.values():
        lst.sort(key=lambda v: v["t_ms"])
    rows, tcount = [], collections.Counter()
    dist: dict[tuple, str] = {}
    rank = {"undetermined": 0, "still_dropped": 1, "left_D": 2}
    for v in trig:
        bt = v["bt"]
        if v["gap"] is not False or bt is None or bt.get("status") != "ok" or not isint(bt.get("dropped_n")) or bt["dropped_n"] < 1 or not isinstance(bt.get("dropped"), list):
            continue
        # the FIRST close at or after the trigger; if it is unusable the entries are undetermined (no skipping ahead)
        close = next((c for c in closes_by_pool.get(v["pool"], []) if c["t_ms"] >= v["t_ms"]), None)
        usable = (close is not None and close["bt"] is not None and close["bt"].get("status") == "ok" and isint(close["bt"].get("cover_hi"))
                  and isint(v["slot"]) and close["bt"]["cover_hi"] >= v["slot"])
        cl = close["bt"].get("dropped") if usable else None
        cdn = close["bt"].get("dropped_n") if usable else None
        ckeys = {(e[0], e[1]) for e in cl if entry_ok(e)} if isinstance(cl, list) else set()
        is_wp = id(v) in wp_ids
        for e in bt["dropped"]:
            good = entry_ok(e)
            delta = (v["slot"] - e[2]) if (good and isint(v["slot"])) else None
            if not good or not isint(v["slot"]) or not usable or not isinstance(cl, list) or not isint(cdn):
                cls = "undetermined"
            elif (e[0], e[1]) in ckeys:
                cls = "still_dropped"
            elif cdn <= DROPPED_LIST_MAX or (cl and entry_ok(cl[-1]) and e[2] < cl[-1][2]):
                cls = "left_D"
            else:
                cls = "undetermined"
            tcount[cls] += 1
            if is_wp:
                tcount["wp_" + cls] += 1
            if good:
                k = (v["pool"], e[0], e[1])
                if k not in dist or rank[cls] > rank[dist[k]]:
                    dist[k] = cls
            rows.append({"pool": v["pool"], "variant": v["variant"], "would_pass": is_wp, "signature": e[0] if good else None,
                         "event_index": e[1] if good else None, "trigger_slot_minus_entry_slot": delta, "class": cls})
    rows.sort(key=lambda r: (str(r["pool"]), str(r["variant"]), str(r["signature"]), str(r["event_index"])))
    dc = collections.Counter(dist.values())
    return {"rows": rows, "t": tcount, "distinct": dc, "distinct_n": len(dist)}


def analyze(shadow_dir: Path, since_ms: int, until_ms: int | None = None, object_hook: Callable | None = None, expect_code_sha: str | None = None) -> dict:
    counts: collections.Counter = collections.Counter()
    files = hour_files(shadow_dir, since_ms, until_ms)
    snap = snapshot(files)
    # pass 1: starts (t_ms >= T0). pass 2: the run's records. Both read the same snapshot (same bytes), because the origin is the LAST start.
    starts: list[dict] = []
    for rec in iter_records(snap, collections.Counter(), object_hook):
        if rec.get("type") == "start":
            t = rec.get("t_ms")
            if isint(t) and t >= since_ms and (until_ms is None or t <= until_ms):
                starts.append({"t_ms": t, "code_sha": rec.get("code_sha"), "rule": rec.get("rule")})
    starts.sort(key=lambda s: (s["t_ms"], str(s["code_sha"])))
    res: dict = {"tool": TOOL, "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "spec": SPEC, "since_ms": since_ms, "until_ms": until_ms,
                 "expect_code_sha": expect_code_sha, "files": [f.name for f, _ in snap], "file_sizes": {f.name: n for f, n in snap}}
    res["starts"] = {"n_starts_after_t0": len(starts), "starts": starts, "origin_t_ms": starts[-1]["t_ms"] if starts else None,
                     "origin_code_sha": starts[-1]["code_sha"] if starts else None,
                     "distinct_code_sha": sorted({str(s["code_sha"]) for s in starts}), "rules": sorted({str(s["rule"]) for s in starts})}
    res["ambiguities"] = [{"id": a, "status": "RULED", "topic": t, "choice": c} for a, t, c in AMBIGUITIES]
    if not starts:
        res["error"] = "no start record at or after --since-ms"
        res["verdict"] = {"tool_rules_clear": False, "blocking_reasons": ["no_start_record"], "thresholds": {}, "outside_tool_pending": OUTSIDE}
        return res
    origin = starts[-1]["t_ms"]

    trig: list[dict] = []
    pools: list[dict] = []
    gap_kinds: collections.Counter = collections.Counter()
    hbs: list[dict] = []
    pre_lag: list = []
    max_t = origin
    for rec in iter_records(snap, counts, object_hook):
        t = rec.get("t_ms")
        typ = rec.get("type")
        if not isint(t):
            counts["records_without_int_t_ms"] += 1
            continue
        if t < origin:
            # report-only: the 24 h before the origin, unsealed pv triggers, keys type / t_ms / sealed / h5_look2_sealed / variant / t_detect_ms only
            if typ == "trigger" and t >= origin - DAY_MS and not (flag(rec.get("sealed")) or flag(rec.get("h5_look2_sealed"))) and rec.get("variant") == "pv":
                td = rec.get("t_detect_ms")
                pre_lag.append(t - td if isint(td) else None)
            continue
        if until_ms is not None and t > until_ms:
            continue
        if typ not in ("trigger", "pool", "gap", "hb"):
            continue
        max_t = max(max_t, t)
        if typ in ("trigger", "pool"):
            sl, h5 = flag(rec.get("sealed")), flag(rec.get("h5_look2_sealed"))
            if sl or h5:
                counts[f"skipped_{typ}_sealed" if sl else f"skipped_{typ}_h5_look2_sealed"] += 1
                if sl and h5:
                    counts[f"skipped_{typ}_both_flags"] += 1
                continue
            if not isinstance(rec.get("pool"), str):
                counts["record_pool_not_str"] += 1
                continue
            (trig if typ == "trigger" else pools).append(view_record(rec, typ))
        elif typ == "gap":
            k = rec.get("kind")
            gap_kinds[k if isinstance(k, str) else "null"] += 1
        else:  # hb
            ctr, tu = rec.get("counters"), rec.get("tip_union")
            mem = tu.get("mem") if isinstance(tu, dict) else None
            hbs.append({"t_ms": t, "has_counters": isinstance(ctr, dict), "union_errors": ctr.get("union_errors") if isinstance(ctr, dict) else None,
                        **{k: (mem.get(k) if isinstance(mem, dict) else None) for k in HB_MEM_KEYS}})
    window_end = max_t if until_ms is None else min(until_ms, max_t)
    cov = covered(origin, window_end, [h["t_ms"] for h in hbs])
    res["window"] = {"origin_t_ms": origin, "last_t_ms": window_end, **cov}
    res["skipped"] = {"sealed_trigger": counts["skipped_trigger_sealed"], "h5_look2_sealed_trigger": counts["skipped_trigger_h5_look2_sealed"],
                      "sealed_pool": counts["skipped_pool_sealed"], "h5_look2_sealed_pool": counts["skipped_pool_h5_look2_sealed"],
                      "both_flags": counts["skipped_trigger_both_flags"] + counts["skipped_pool_both_flags"], "bad_line": counts["bad_line"],
                      "bad_line_not_utf8": counts["bad_line_not_utf8"], "unreadable_file": counts["unreadable_file"],
                      "records_without_int_t_ms": counts["records_without_int_t_ms"], "record_pool_not_str": counts["record_pool_not_str"]}
    variant_other = sum(1 for v in trig if not (v["variant"] == "pv" or v["variant"] == "fv"))
    trig = [v for v in trig if v["variant"] == "pv" or v["variant"] == "fv"]
    res["records"] = {"triggers": len(trig), "triggers_pv": sum(1 for v in trig if v["variant"] == "pv"), "triggers_fv": sum(1 for v in trig if v["variant"] == "fv"),
                      "triggers_variant_other": variant_other, "plain_pool_closes": len(pools), "hb": len(hbs), "gap_records_by_kind": dict(sorted(gap_kinds.items()))}

    pv = [v for v in trig if v["variant"] == "pv"]
    nogap_pv_ws1 = [v for v in pv if v["gap"] is False and isint(v["ws"]) and v["ws"] >= 1]
    # item 1
    res["item1_duration"] = {"elapsed_h": cov["elapsed_h"], "covered_h": cov["covered_h"], "hb_max_gap_ms": cov["hb_max_gap_ms"], "n_holes": cov["n_holes"],
                             "n_nogap_pv_ws_ge1": len(nogap_pv_ws1), "min_hours": MIN_HOURS, "min_triggers": MIN_NOGAP_PV_WS1}
    # item 2
    st_all = collections.Counter(status_of(v) for v in trig)
    st_sel = collections.Counter(status_of(v) for v in nogap_pv_ws1)
    bnd = [v for v in trig if isint(v["t_ms"]) and min(v["t_ms"] % HOUR_MS, HOUR_MS - v["t_ms"] % HOUR_MS) <= BOUNDARY_MS]
    ust_all = collections.Counter("missing" if v["bu"] is None else str(v["bu"].get("status")) for v in trig)
    res["item2_coverage"] = {
        "tipauth_status_all_triggers": dict(sorted(st_all.items())), "tipauth_status_nogap_pv_ws_ge1": dict(sorted(st_sel.items())),
        "union_status_all_triggers": dict(sorted(ust_all.items())),
        "tipauth_ok_n_all": sum(1 for v in trig if tip_ok(v)), "tipauth_ok_share_all": share(sum(1 for v in trig if tip_ok(v)), len(trig)),
        "tipauth_ok_n_nogap_pv_ws_ge1": sum(1 for v in nogap_pv_ws1 if tip_ok(v)),
        "tipauth_ok_share_nogap_pv_ws_ge1": share(sum(1 for v in nogap_pv_ws1 if tip_ok(v)), len(nogap_pv_ws1)),
        "union_ok_n_all": sum(1 for v in trig if union_ok(v)), "union_ok_share_all": share(sum(1 for v in trig if union_ok(v)), len(trig)),
        "boundary_triggers_n": len(bnd), "boundary_tip_pending_n": sum(1 for v in bnd if status_of(v) == "tip_pending"),
        "boundary_tip_pending_share": share(sum(1 for v in bnd if status_of(v) == "tip_pending"), len(bnd)),
        "tip_pending_n_all": st_all.get("tip_pending", 0)}
    # item 3
    xt = {"pv_triggers": tri_cross(), "fv_triggers": tri_cross(), "plain_pool_closes": tri_cross()}
    for v in trig:
        for k in cross_key(v):
            xt["pv_triggers" if v["variant"] == "pv" else "fv_triggers"][k] += 1
    for v in pools:
        for k in cross_key(v):
            xt["plain_pool_closes"][k] += 1
    res["item3_crosstab"] = {k: dict(sorted(c.items())) for k, c in xt.items()}
    # conditions
    conds = [(v, conditions(v)) for v in trig]
    wp = [v for v, c in conds if would_pass_view(c)]  # == [v for v in trig if would_pass(raw)[0]]; pv only by construction (C7 f)
    fv_not_eligible = sum(1 for v, c in conds if v["fails"] == {"variant_not_pv"})
    c14 = [(v, c) for v, c in conds if c["C1"] and c["C2"] and c["C3"] and c["C4"]]
    sub = collections.Counter()
    for v, c in c14:
        bt = v["bt"]
        dfs = None if not (isint(bt.get("dropped_first_slot")) and isint(bt.get("cover_lo"))) else bt["dropped_first_slot"] == bt["cover_lo"]
        sub[f"orphaned={bucket(bt.get('orphaned_by_drop_n'))}|nosettle={bucket(bt.get('tipauth_nosettle'))}|dropped_first_slot_eq_cover_lo={dfs}"] += 1
    res["item3_c1_c4_subtab"] = {"n_c1_c4": len(c14), "cells": dict(sorted(sub.items()))}
    names = COND_NAMES
    funnel, cur = [{"after": "all_triggers", "n": len(conds)}], conds
    for nm in names:
        cur = [(v, c) for v, c in cur if c[nm]]
        funnel.append({"after": nm, "n": len(cur)})
    loo = {nm: sum(1 for v, c in conds if all(c[o] for o in names if o != nm) and not c[nm]) for nm in names}
    c5 = {"n_c1_c4": len(c14),
          "fail_5a": sum(1 for v, c in c14 if not c["C5a"]), "fail_5b": sum(1 for v, c in c14 if not c["C5b"]), "fail_5c": sum(1 for v, c in c14 if not c["C5c"]),
          "fail_only_5a": sum(1 for v, c in c14 if not c["C5a"] and c["C5b"] and c["C5c"]), "fail_only_5b": sum(1 for v, c in c14 if c["C5a"] and not c["C5b"] and c["C5c"]),
          "fail_only_5c": sum(1 for v, c in c14 if c["C5a"] and c["C5b"] and not c["C5c"]),
          "fail_all_three": sum(1 for v, c in c14 if not (c["C5a"] or c["C5b"] or c["C5c"])), "fail_any": sum(1 for v, c in c14 if not (c["C5a"] and c["C5b"] and c["C5c"]))}
    c15 = [(v, c) for v, c in c14 if c["C5a"] and c["C5b"] and c["C5c"]]
    c7 = {"n_c1_c5": len(c15), "fail_any": sum(1 for v, c in c15 if not all(c[n] for n in C7_NAMES)),
          "fail_all_six": sum(1 for v, c in c15 if not any(c[n] for n in C7_NAMES))}
    for n in C7_NAMES:
        c7["fail_" + n] = sum(1 for v, c in c15 if not c[n])
        c7["fail_only_" + n] = sum(1 for v, c in c15 if not c[n] and all(c[o] for o in C7_NAMES if o != n))
    res["item6_would_pass"] = {"would_pass_n": len(wp), "would_pass_pv_n": sum(1 for v in wp if v["variant"] == "pv"),
                               "fv_not_eligible_variant_n": fv_not_eligible,
                               "would_pass_by_boost_src": dict(sorted(collections.Counter(str(v["boost_src"]) for v in wp).items())),
                               "would_pass_pv_by_boost_src": dict(sorted(collections.Counter(str(v["boost_src"]) for v in wp if v["variant"] == "pv").items())),
                               "c6_applied": False, "conditions": COND_NAMES, "gate_reasons": list(GATE_REASONS), "funnel": funnel, "leave_one_out_removes": loo,
                               "c5_parts": c5, "c7_parts": c7}
    dist = lambda f, rows: summ([None if v["bt"] is None else v["bt"].get(f) for v in rows])  # noqa: E731
    closes0 = [v for v in pools if tip_ok(v) and v["tval"] == 0]
    fields = ["dropped_n", "dropped_sells_n", "dropped_boost_n", "kept_ws_only_before_cover", "kept_ws_only_above_cover", "kept_ws_only_sig_on_tip"]
    res["item6_dropped_prints"] = {"on_would_pass_triggers": {f: dist(f, wp) for f in fields}, "on_tipauth0_closes": {"n": len(closes0), **{f: dist(f, closes0) for f in fields}},
                                   "c1_c4_orphaned_by_drop_n": dict(sorted(collections.Counter(str((v["bt"] or {}).get("orphaned_by_drop_n")) for v, _ in c14).items())),
                                   "c1_c4_tipauth_nosettle": dict(sorted(collections.Counter(str((v["bt"] or {}).get("tipauth_nosettle")) for v, _ in c14).items()))}
    # item 4 consistency
    wp_ids = {id(v) for v in wp}
    cons: dict[str, dict] = {n: {"n": 0, "pools": []} for n in CONSIST_NAMES}
    for v in trig + pools:
        for b in consistency_record(v, id(v) in wp_ids):
            cons[b]["n"] += 1
            if len(cons[b]["pools"]) < 5:
                cons[b]["pools"].append(str(v["pool"]))
    cons["record_pool_not_str"]["n"] = counts["record_pool_not_str"]
    # union_errors: an hb with a counters dict is one sample; an absent counter is a 0-valued sample; a present non-integer counts as 1 (fail closed)
    ue = []
    for h in hbs:
        if h["has_counters"]:
            u = h["union_errors"]
            ue.append(0 if u is None else (u if isint(u) else 1))
    cons["union_errors"]["n"] = max(ue) if ue else 0
    res["item4_consistency"] = {"checks": cons, "total": sum(c["n"] for c in cons.values()), "union_errors_hb_samples": len(ue),
                                "union_errors_hb_absent_counter_as_0": sum(1 for h in hbs if h["has_counters"] and h["union_errors"] is None),
                                "error_status_records": sum(1 for v in trig + pools if status_of(v) == "error")}

    # item 5
    def incomplete(rows: list[dict]) -> dict:
        base = [v for v in rows if v["gap"] is False and isint(v["ws"]) and v["ws"] == 0]
        okr = [v for v in base if tip_ok(v)]
        inc = [v for v in okr if v["tval"] >= 1 or (isint(v["bt"].get("orphaned_by_drop_n")) and v["bt"]["orphaned_by_drop_n"] >= 1)]
        dn1 = [v for v in okr if isint(v["bt"].get("dropped_n")) and v["bt"]["dropped_n"] >= 1]
        return {"n_ws0_nogap": len(base), "n_ok": len(okr), "incomplete_n": len(inc), "incomplete_share_of_ok": share(len(inc), len(okr)),
                "incomplete_share_of_all_ws0_nogap": share(len(inc), len(base)), "dropped_n_ge1_n": len(dn1), "dropped_n_ge1_share_of_ok": share(len(dn1), len(okr))}
    res["item5_tip_incompleteness"] = {"plain_pool_closes": incomplete(pools), "triggers": incomplete(trig), "cap": INCOMPLETE_MAX, "min_ok": INCOMPLETE_MIN_OK}
    # item 7 (list only)
    wp_rows = [{"pool": v["pool"], "variant": v["variant"], "t_ms": v["t_ms"], "slot": v["slot"], "dropped_n": v["bt"].get("dropped_n"), "fully_enumerated": fully_enumerated(v),
                "dropped": v["bt"].get("dropped")} for v in wp]
    wp_rows.sort(key=lambda r: (str(r["pool"]), r["t_ms"], str(r["variant"])))
    wp_sigs = sorted({e[0] for r in wp_rows if isinstance(r["dropped"], list) for e in r["dropped"] if isinstance(e, list) and e and isinstance(e[0], str)})
    closes_ws1 = [v for v in pools if v["gap"] is False and isint(v["ws"]) and v["ws"] >= 1 and tip_ok(v) and v["tval"] == 0 and isinstance(v["bt"].get("dropped"), list)]
    close_rows = sorted(({"pool": v["pool"], "t_ms": v["t_ms"], "dropped_n": v["bt"].get("dropped_n"), "partial": isint(v["bt"].get("dropped_n")) and v["bt"]["dropped_n"] > DROPPED_LIST_MAX,
                          "dropped": v["bt"]["dropped"]} for v in closes_ws1), key=lambda r: (str(r["pool"]), r["t_ms"]))
    fe = sum(1 for r in wp_rows if r["fully_enumerated"])
    res["item7_census_input"] = {"network_calls": 0, "would_pass_n": len(wp_rows), "would_pass_fully_enumerated_n": fe, "would_pass_distinct_signatures_n": len(wp_sigs),
                                 "would_pass_signatures": wp_sigs, "would_pass_triggers": wp_rows,
                                 "tipauth0_ws_ge1_nogap_closes_n": len(close_rows), "tipauth0_ws_ge1_nogap_closes": close_rows,
                                 "census_performed": False, "on_tip_controls": "not taken from records (outside the allow-list)"}
    # item 10
    mg = classify_margin(trig, pools, wp_ids)
    tc, dc = mg["t"], mg["distinct"]
    tested = tc["still_dropped"] + tc["left_D"]
    tested_d = dc["still_dropped"] + dc["left_D"]
    res["item10_margin_test"] = {"entries": len(mg["rows"]), "still_dropped": tc["still_dropped"], "left_D": tc["left_D"], "undetermined": tc["undetermined"],
                                 "tested": tested, "left_D_share_of_tested": share(tc["left_D"], tested),
                                 "left_D_share_of_all_entries": share(tc["left_D"], len(mg["rows"])), "would_pass_left_D": tc["wp_left_D"],
                                 "would_pass_entries": sum(tc["wp_" + k] for k in ("still_dropped", "left_D", "undetermined")),
                                 "would_pass_tested": tc["wp_still_dropped"] + tc["wp_left_D"], "would_pass_undetermined": tc["wp_undetermined"],
                                 "distinct_entries": mg["distinct_n"], "tested_distinct": tested_d, "left_D_distinct": dc["left_D"],
                                 "still_dropped_distinct": dc["still_dropped"], "undetermined_distinct": dc["undetermined"],
                                 "left_D_share_of_tested_distinct": share(dc["left_D"], tested_d), "rows": mg["rows"]}
    # item 9
    rss = [h["rss_kb"] for h in hbs if isint(h["rss_kb"])]

    def col(k: str) -> list:
        return [h[k] for h in hbs if finite_num(h[k])]
    lag = lambda rows: summ([v["t_ms"] - v["t_detect_ms"] if isint(v["t_detect_ms"]) else None for v in rows])  # noqa: E731
    pvs = [v for v in trig if v["variant"] == "pv"]
    res["item9_timing_memory"] = {"hb_n": len(hbs), "rss_kb_max": max(rss) if rss else None, "rss_kb_last": rss[-1] if rss else None,
                                  "rss_limit_bytes": RSS_LIMIT_BYTES, "rss_anon_kb_max": max(col("rss_anon_kb"), default=None), "hwm_kb_max": max(col("hwm_kb"), default=None),
                                  "est_tipauth_mb_max": max(col("est_tipauth_mb"), default=None), "est_tipauth_mb_last": (col("est_tipauth_mb") or [None])[-1],
                                  "est_mb_max": max(col("est_mb"), default=None), "tipauth_wsonly_max": max(col("tipauth_wsonly"), default=None),
                                  "tipauth_tip_sigs_max": max(col("tipauth_tip_sigs"), default=None), "union_members_max": max(col("union_members"), default=None),
                                  "decision_to_send_ms": None,
                                  "emit_lag_ms": {"report_only": True, "all_pv": lag(pvs), "tipauth_ok": lag([v for v in pvs if tip_ok(v)]),
                                                  "tipauth_not_ok": lag([v for v in pvs if not tip_ok(v)])},
                                  "emit_lag_ms_24h_before_origin": {"report_only": True, **summ(pre_lag)}}
    res["item8_trigger_print"] = {"computed": False, "reason": "needs the tip tape; outside the shadow records this tool may read"}
    res["verdict"] = verdict(res)
    return res


OUTSIDE = ["item7_rpc_census", "item8_trigger_print", "item9_decision_to_send_ms", "C6_v1_only"]


def fire(t: int, left: int) -> bool:
    return (left >= 1) if t < LEFT_D_MIN_TESTED else (left / t > LEFT_D_MAX)


def verdict(r: dict) -> dict:
    th: dict[str, dict] = {}
    block: list[str] = []

    def put(name: str, met: bool | None, **nums: Any) -> None:
        th[name] = {"met": met, **nums}
    i1, i2, i5, i9, i10, i4 = r["item1_duration"], r["item2_coverage"], r["item5_tip_incompleteness"], r["item9_timing_memory"], r["item10_margin_test"], r["item4_consistency"]
    put("duration_24h_and_10_nogap_pv_ws_ge1", i1["covered_h"] >= MIN_HOURS and i1["n_nogap_pv_ws_ge1"] >= MIN_NOGAP_PV_WS1, covered_h=i1["covered_h"],
        elapsed_h=i1["elapsed_h"], n=i1["n_nogap_pv_ws_ge1"])
    exp, got = r.get("expect_code_sha"), r["starts"].get("origin_code_sha")
    put("origin_code_sha_matches", None if exp is None else got == exp, expected=exp, origin_code_sha=got)

    def ge(name: str, *shares: float | None) -> None:
        known = [s for s in shares if s is not None]
        met = False if any(s < COVERAGE_MIN for s in known) else (None if len(known) < len(shares) else True)
        put(name, met, shares=list(shares), min=COVERAGE_MIN)
    ge("tipauth_ok_ge_90pct", i2["tipauth_ok_share_all"], i2["tipauth_ok_share_nogap_pv_ws_ge1"])
    ge("union_ok_ge_90pct", i2["union_ok_share_all"])
    put("consistency_all_zero", i4["total"] == 0, total=i4["total"], failing=sorted(k for k, c in i4["checks"].items() if c["n"]))
    sh = i5["plain_pool_closes"]
    inc = sh["incomplete_share_of_ok"]
    put("tip_incompleteness_le_5pct", None if sh["n_ok"] < INCOMPLETE_MIN_OK else inc <= INCOMPLETE_MAX, share=inc,
        share_of_all_ws0_nogap=sh["incomplete_share_of_all_ws0_nogap"], cap=INCOMPLETE_MAX, n_ok=sh["n_ok"], min_ok=INCOMPLETE_MIN_OK)
    wpn = r["item6_would_pass"]["would_pass_pv_n"]
    put("would_pass_pv_n_ge_1", True if wpn >= 1 else None, would_pass_pv_n=wpn)
    wl, wt = i10["would_pass_left_D"], i10["would_pass_tested"]
    put("margin_no_would_pass_left_D", False if wl >= 1 else (None if wt == 0 else True), would_pass_left_D=wl, would_pass_tested=wt,
        would_pass_undetermined=i10["would_pass_undetermined"], would_pass_entries=i10["would_pass_entries"])
    tested, left, tested_d, left_d = i10["tested"], i10["left_D"], i10["tested_distinct"], i10["left_D_distinct"]
    m = False if (fire(tested, left) or fire(tested_d, left_d)) else (None if tested_d == 0 else True)
    put("margin_left_D_share_le_1pct", m, tested=tested, left_D=left, share=i10["left_D_share_of_tested"], tested_distinct=tested_d, left_D_distinct=left_d,
        undetermined=i10["undetermined"])
    wp = r["item7_census_input"]
    put("would_pass_all_fully_enumerated", None if wp["would_pass_n"] == 0 else wp["would_pass_fully_enumerated_n"] == wp["would_pass_n"], n=wp["would_pass_n"],
        fully_enumerated=wp["would_pass_fully_enumerated_n"])
    put("rss_below_1gb", None if i9["rss_kb_max"] is None else i9["rss_kb_max"] * 1024 < RSS_LIMIT_BYTES, rss_kb_max=i9["rss_kb_max"])
    put("single_start_after_t0", r["starts"]["n_starts_after_t0"] == 1, n=r["starts"]["n_starts_after_t0"], distinct_code_sha=r["starts"]["distinct_code_sha"])
    for name, t in th.items():
        if t["met"] is False:
            block.append(name)
        elif t["met"] is None:
            block.append(name + ":short")
    return {"thresholds": th, "tool_rules_clear": not block, "blocking_reasons": sorted(block), "outside_tool_pending": OUTSIDE,
            "note": "A count of mechanism fields, not a result and NOT a proposal verdict. tool_rules_clear true means none of the rules this tool owns blocks; "
                    "outside_tool_pending is still open."}


def table(r: dict) -> str:
    if "error" in r:
        return r["error"]
    v = r["verdict"]
    rows = [f"starts after T0: {r['starts']['n_starts_after_t0']}  origin={r['starts']['origin_t_ms']}  elapsed_h={r['window']['elapsed_h']}  covered_h={r['window']['covered_h']}  "
            f"triggers={r['records']['triggers']}  closes={r['records']['plain_pool_closes']}  would-pass={r['item6_would_pass']['would_pass_n']}",
            f"{'threshold':44s} {'met':6s} numbers"]
    for k, t in sorted(v["thresholds"].items()):
        rows.append(f"{k:44s} {str(t['met']):6s} " + json.dumps({a: b for a, b in t.items() if a != 'met'}, sort_keys=True))
    rows.append(f"tool_rules_clear={v['tool_rules_clear']} (NOT a proposal verdict) blocking={v['blocking_reasons']} open outside this tool={v['outside_tool_pending']}")
    return "\n".join(rows)


def dumps(res: dict) -> str:
    return json.dumps(res, sort_keys=True, indent=1, allow_nan=False) + "\n"


def error_result(msg: str, code: str) -> dict:
    return {"tool": TOOL, "error": msg, "verdict": {"tool_rules_clear": False, "blocking_reasons": [code], "thresholds": {}, "outside_tool_pending": OUTSIDE}}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--shadow-dir")
    ap.add_argument("--since-ms", type=int)
    ap.add_argument("--until-ms", type=int, help="REQUIRED. Upper bound of the read (also the scheduled instant); see --schedule.")
    ap.add_argument("--expect-code-sha", help="REQUIRED. 40 hex: the origin start record's code_sha must equal it (this count: 5db78a663a0617527daacb22f5ce6c1a7e92dfa3).")
    ap.add_argument("--out", default=None)
    ap.add_argument("--table", action="store_true", help="short human table on stderr")
    ap.add_argument("--schedule", action="store_true", help=SCHEDULE_HELP)
    a = ap.parse_args(argv)
    if a.schedule:
        if a.since_ms is None:
            ap.error("--schedule needs --since-ms")
        sys.stdout.write(json.dumps({"since_ms": a.since_ms, "until_ms_instants": schedule(a.since_ms), "interim": schedule(a.since_ms)[0]}, indent=1) + "\n")
        return 0
    for name, val in (("--shadow-dir", a.shadow_dir), ("--since-ms", a.since_ms), ("--until-ms", a.until_ms), ("--expect-code-sha", a.expect_code_sha)):
        if val is None:
            ap.error(f"{name} is required")
    if not on_schedule(a.since_ms, a.until_ms):
        res = error_result(f"--until-ms {a.until_ms} is not a scheduled instant for --since-ms {a.since_ms} (use --schedule)", "until_ms_not_on_schedule")
        sys.stdout.write(dumps(res))
        return 4
    if not re.fullmatch(r"[0-9a-f]{40}", a.expect_code_sha):
        ap.error("--expect-code-sha must be 40 lowercase hex characters")
    d = Path(a.shadow_dir)
    if not d.is_dir():
        res = error_result(f"shadow directory missing: {a.shadow_dir}", "missing_directory")
        code = 3
    else:
        res = analyze(d, a.since_ms, a.until_ms, expect_code_sha=a.expect_code_sha)
        code = 2 if "error" in res else 0
    text = dumps(res)
    sys.stdout.write(text)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    if a.table:
        sys.stderr.write(table(res) + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
