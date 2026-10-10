#!/usr/bin/env python3
"""EXP-025 (C1-NF) section 10 P6 item 3 / section 11.4 R1: the raw-JSONL cross-check of forward-1002ev against forward-1002.

    /data/mal/audit-1008/venv/bin/python tools/exp025_r1_crosscheck.py        # no arguments: every path is fixed in code

It writes the one record tools/exp025_look.py reads as R1_CROSSCHECK (/data/mal/exp025/r1_crosscheck/forward-1002ev.json, schema
exp025_r1_crosscheck_v1, loader exp025_look.load_crosscheck), once, after the DEC-016 FINAL.

WHAT P6 ITEM 3 SAYS. "forward-1002 is the cross-check, joined on the raw JSONL ..., requiring equal sol_lamports, token_raw and both reserves.
An hour whose 1:1 match rate is below 99.5% is a bad hour (R1)." R1 adds: an hour of forward-1002ev that was not walked is bad. The hours are
exp025_look.XCHECK_RANGE = [2026-10-09T00, 2026-10-16T01), the forward-1002ev rows of Look 1's allowlist.

HOW. Each hour runs through tools/forward_v_join.check_hour, imported, not copied or edited (its blob 8b60e5bf is pinned by EXP-024): the key is
(slot, signature, event_index); a pair is matched 1:1 when the key is once on each side, the venue is equal and sol_lamports, token_raw,
quote_reserve and base_reserve are equal; rate = matched / max(rows forward-1002, rows forward-1002ev), over all trade rows and over the PumpSwap
rows. Both walks must be sealed in checkpoint.json with a last clean verify.jsonl line whose sha256 matches the file bytes (forward_v_join's
hour_state). Per hour the record holds
    match_rate = min(rate, rate_pumpswap) when forward_v_join's reason is "ok" or "below_threshold" (a computed 1:1 rate), else null;
    verdict    = "ok" when match_rate >= exp025_look.XCHECK_MIN (0.995), else "bad".
A null match_rate is a bad hour for load_crosscheck/hour_status: an hour not walked, not sealed or not verified on either side, a sha256
mismatch, a bad line, a zstd read error, a duplicate key (no 1:1 rate exists) or an hour with no rows at all. "ok" here is therefore exactly
forward_v_join's usable hour, the same hours EXP-024 accepts V from.

SEAL. Before anything else, the tool refuses (exit 2) unless the DEC-016 FINAL holds by both of the repo's existing checks: the marker file
exp025_read.FINAL_MARKER exists (exp025_read.check_read_time's first condition) and forward_v_join.final_marker finds the EXP-012 FINAL line
(final=true, no test_window) in exp025_read.FINAL_LEDGER. No walk directory, trade file, checkpoint or verify file is opened before that.
check_read_time's time condition is required too (Look 1's last allowlisted hour, 10-17T02Z, has ended): the record is write-once, and
forward-1002ev's last hour 10-16T00 is walked and verified only after 10-16T01Z, so an earlier run could record a still-walking hour as not walked.

ONE READ. No CLI argument: the walk dirs, the FINAL marker and ledger, the hour range and the output path are fixed in code. The record is
written whole or not at all (temp file + link) and never overwritten: a second run refuses (exit 2). exp025_look's `lock` event records its
sha256.

COUNTS ONLY. stdout and the record carry per-hour counts, rates and a reason from forward_v_join.REASONS; no key, signature, pool, mint, row
value, pick, label, fill, exit or P&L. An exception prints its class name only and writes no record.

Exit: 0 record written and every hour ok, 1 record written and some hour bad (informational), 2 refusal (seal, pin, record exists, usage, crash).
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence, TextIO

TOOLS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(TOOLS)
for _p in (REPO, TOOLS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import exp025_look as K  # noqa: E402
import exp025_read as R  # noqa: E402
import tools.forward_v_join as FVJ  # noqa: E402

SCHEMA = K.CROSSCHECK_SCHEMA
OUT = K.R1_CROSSCHECK
HOURS = tuple(FVJ.hour_list(*K.XCHECK_RANGE))
MIN_RATE = K.XCHECK_MIN
BASE_DIR, EV_DIR = FVJ.DEFAULT_BASE, FVJ.DEFAULT_EV          # forward-1002 (the reference), forward-1002ev (the tape)
FINAL_MARKER, FINAL_LEDGER = R.FINAL_MARKER, R.FINAL_LEDGER
FVJ_BLOB = "8b60e5bf5fc8a45b28a587b96a01aa20a07df2ad"   # tools/forward_v_join.py as pinned by EXP-024 (v_join); R1's rule is its check_hour
RATED = ("ok", "below_threshold")                             # forward_v_join reasons with a computed 1:1 rate
COUNT_FIELDS = ("rows_base", "rows_ev", "pumpswap_base", "pumpswap_ev", "matched_1to1", "pumpswap_matched", "rate", "rate_pumpswap",
                "field_mismatch", "unkeyed", "v_missing", "bad_lines_base", "bad_lines_ev", "md5_match")

assert MIN_RATE == FVJ.MIN_MATCH_RATE, "exp025_look.XCHECK_MIN and forward_v_join.MIN_MATCH_RATE must agree"
assert str(FVJ.DEFAULT_LEDGER) == R.FINAL_LEDGER, "forward_v_join and exp025_read must read the same FINAL ledger"


def final_gate(marker: str, ledger: str, now: int | None = None) -> dict[str, Any]:
    """exp025_read.check_read_time(1, ...) (the LookGuard condition: FINAL marker, ledger entry, Look 1's last allowlisted hour 10-17T02 ended), then forward_v_join.final_marker; FVJ.Refused otherwise. Opens the marker path and the ledger only."""
    try:
        R.check_read_time(1, now, marker, ledger)
    except R.Refusal as e:
        raise FVJ.Refused(f"sealed until the DEC-016 FINAL and the end of Look 1's last allowlisted hour: {e}") from None
    return FVJ.final_marker(Path(ledger))


def hour_entry(r: "FVJ.HourResult") -> dict[str, Any]:
    pub = r.public()
    e: dict[str, Any] = {k: pub[k] for k in COUNT_FIELDS}
    mr = None
    if r.reason in RATED and r.rate is not None and r.rate_ps is not None:
        mr = min(r.rate, r.rate_ps)
    e["match_rate"] = mr
    e["reason"] = r.reason
    e["verdict"] = "ok" if (mr is not None and mr >= MIN_RATE) else "bad"
    if (e["verdict"] == "ok") != (r.reason == "ok"):       # the record and forward_v_join's usable hour never disagree
        raise AssertionError("verdict disagrees with forward_v_join")
    return e


def write_once(path: str, doc: dict[str, Any]) -> None:
    """Whole or nothing, never over an existing record."""
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".r1_crosscheck.", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1, sort_keys=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.link(tmp, path)                                  # FileExistsError if a record appeared meanwhile
    finally:
        os.unlink(tmp)


def run(*, marker: str, ledger: str, base_dir: Path, ev_dir: Path, out_path: str, hours: Sequence[str], out: TextIO = sys.stdout,
        now: int | None = None) -> int:
    try:
        final_gate(marker, ledger, now)                     # before any walk dir is touched
        FVJ.require_pins()
        if FVJ.git_blob_sha(Path(FVJ.__file__)) != FVJ_BLOB:
            raise FVJ.Refused("tools/forward_v_join.py is not blob 8b60e5bf (EXP-024 v_join pin): R1's rule would differ")
        if os.path.exists(out_path):
            raise FVJ.Refused(f"{out_path} exists: the R1 cross-check record is written once")
    except FVJ.Refused as e:
        print(json.dumps({"refused": True, "reason": str(e)}), file=out)
        return 2
    try:
        entries: dict[str, dict[str, Any]] = {}
        for h in hours:
            r = FVJ.check_hour(Path(base_dir), Path(ev_dir), h)
            r.fallback = []                                 # keys stay in this process; the record keeps counts only
            entries[h] = hour_entry(r)
        by_reason: dict[str, int] = {}
        for e in entries.values():
            by_reason[e["reason"]] = by_reason.get(e["reason"], 0) + 1
        bad = [h for h, e in entries.items() if e["verdict"] != "ok"]
        doc = {
            "schema": SCHEMA, "pin": "EXP-025 section 10 P6 item 3; section 11.4 R1",
            "reference": "forward-1002", "subject": "forward-1002ev",
            "key": list(FVJ.KEY_FIELDS), "equal_fields": ["venue", *FVJ.MATCH_FIELDS],
            "rate_rule": "match_rate = min(rate, rate_pumpswap); rate = matched_1to1 / max(rows_base, rows_ev); null when no 1:1 rate exists",
            "min_rate": MIN_RATE, "range": [hours[0], R.hour_str(R.ep(hours[-1]) + 3600)] if hours else [],
            "tool_blob": FVJ.git_blob_sha(Path(__file__)), "forward_v_join_blob": FVJ.git_blob_sha(Path(FVJ.__file__)),
            "final_ledger_sha256": FVJ.sha256_file(Path(ledger)),
            "written_utc": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "summary": {"hours": len(entries), "ok": len(entries) - len(bad), "bad": len(bad), "by_reason": dict(sorted(by_reason.items()))},
            "hours": entries,
        }
        write_once(out_path, doc)
    except FileExistsError:
        print(json.dumps({"refused": True, "reason": "record appeared during the run; not overwritten"}), file=out)
        return 2
    except Exception as e:  # noqa: BLE001 - counts-only: the class name, never a message that could carry a value
        print(json.dumps({"refused": True, "crash": type(e).__name__}), file=out)
        return 2
    for h, e in entries.items():
        print(json.dumps({"hour": h, "verdict": e["verdict"], "reason": e["reason"], "rows_base": e["rows_base"], "rows_ev": e["rows_ev"],
                          "matched_1to1": e["matched_1to1"], "rate": e["rate"], "rate_pumpswap": e["rate_pumpswap"]}), file=out)
    print(json.dumps({"written": out_path, **doc["summary"]}), file=out)
    return 1 if bad else 0


def main(argv: Sequence[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv != ["-h"] and argv != ["--help"]:
        print(json.dumps({"refused": True, "reason": "no arguments: every path is fixed in code"}))
        return 2
    if argv:
        print(__doc__)
        return 0
    return run(marker=FINAL_MARKER, ledger=FINAL_LEDGER, base_dir=BASE_DIR, ev_dir=EV_DIR, out_path=OUT, hours=HOURS)


if __name__ == "__main__":
    sys.exit(main())
