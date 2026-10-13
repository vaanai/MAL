#!/usr/bin/env python3
"""H5 canary, sent count of a finished ws-pass trigger-print RPC check, COUNTS ONLY (quant-proof, PR #610 comment 6156017843 section 2; DEC-024 Amendment 9).

DEC-024 Amendment 9 stops the H5 canary when a finished ws-pass trigger-print RPC check (tools/h5_wspass_print_rpc.py) shows 1 or more ws-pass trigger
prints on which the executor sent a buy and which the public RPC did not return. This tool joins that finished check to the live executor's `decision`
rows and prints how many ws-pass prints were sent, by the print's RPC status. It prints no pool, mint, signature, slot or time of a print.

  python3 -m tools.h5_wspass_sent_count --rpc-out-dir O --shadow-dir D --since-ms T0 --until-ms T1 --expect-code-sha SHA40 \\
      --ledger PATH [PATH ...] --out FILE

It reads (nothing else; it makes no network call, no ssh, and opens no tape, tip file, gate log, CAP-PICK file, paper twin, wallet or fill record):
  1. the RPC out-dir O: `wspass_report.json` (must exist: the check is finished), the plan file, the state file, by the names the RPC tool gives them;
  2. the shadow hour files in D, for the `pool` and `slot` of the trigger records of the plan's ws-pass signatures, and nothing of any other record;
  3. the live executor ledger, the `decision` rows only (see THE LEDGER).

THE LEDGER (fixed against tools/h5_executor.py and tools/probe_executor.py at the head this tool was written on).
  File: the executor writes `<state_dir>/<run_mode>/h5-ledger.jsonl` (h5_executor.py line 1031 and 1033, `H5Ledger(sd / "h5-ledger.jsonl", self.run_mode)` at line
  1152; run_mode is "dryrun" or "live", line 98). The live ledger is the one under `<state_dir>/live/`. `--ledger` takes one or more paths; a path that is a
  directory stands for `<dir>/h5-ledger.jsonl` (that exact name, not searched for, not recursive). The same file named twice is a usage error.
  Row writer: `H5Ledger.write` (h5_executor.py 888 to 892) calls `FillLog.write` (probe_executor.py 1020 to 1029), which builds
  `{"schema": ..., "mode": self.mode, **row}`; the executor's `_log` (probe_executor.py 1100 to 1101) adds `kind`, `ts_ms`, `mint`, `book`. So every row
  carries `mode`, which is the run-mode marker: "live" or "dryrun". (The `start` row also has a `run_mode` key; a `decision` row does not, and this tool does
  not read it.)
  A `decision` row of a LIVE buy is written once, after the send, at h5_executor.py 1714 to 1717:
      self._log("decision", trg.mint, **trg.log_fields(), ..., sent_ms=p.get("first_send_ms"), ...)
  `trg.log_fields()` (h5_executor.py 441 to 442) is every field of the H5Trigger except `mint` (`H5Trigger`, lines 408 to 436), so the row carries `pool` and
  `trigger_slot`. `trigger_slot` is the shadow trigger record's `slot` (h5_executor.py 674: `"trigger_slot": row["slot"]`), `pool` is its `pool`.
  `sent_ms` is `first_send_ms` of the pending buy: an integer once the transaction was sent, null if it never was.
  The other `decision` row (h5_executor.py 1751 to 1754, `_dry_buy`) is written only by a dry run: `mode` is "dryrun", it has `sent=False` and no `sent_ms`.
  Keys read, and only these (LEDGER_KEYS): `kind` (of every row, read first), then for a `decision` row `mode`, and for a LIVE `decision` row `ts_ms`,
  `sent_ms` and, when `sent_ms` is an integer, `pool` and `trigger_slot`. A row of any other `kind` is skipped after `kind` is read. Never read: buy or sell
  state, landing, `expected_tokens`, `min_out`, `q_out`, a fill, an exit, a wallet delta, the twin, P&L, the `signature` or `mint` of a ledger row.

SENT. A print is SENT when a LIVE `decision` row (`mode` exactly "live") has the same `pool` and `trigger_slot` as one of the print's trigger records and an
  integer `sent_ms` (a bool is not an integer; null, a string, a float and a missing key are not). The H5 trigger carries no print signature and the rule is
  one trade per pool, so the join is (pool, trigger slot). A `decision` row without `mode` is counted (`decision_rows_without_marker`) and is NOT live; one
  whose `mode` is "dryrun" is counted and is not live; one with any other `mode` value is counted (`decision_rows_marker_other`) and is not live. Two live
  sent rows for one (pool, trigger slot) count once and are reported (`live_sent_keys_with_more_than_one_row_n`). `ts_ms` of a live row is read only to
  count a row where it is not an integer; it is not part of the definition of sent.

RPC STATUS of a print is the state of its signature in the RPC out-dir, by the tool's own rule (tools.h5_tipauth_census `states_by_sig` / `classify`, the same
  mapping as `tally` of the RPC tool): returned (found), not_returned (null twice, at least 600,000 ms apart), error, pending (never asked, or one null).
  The per-status totals of the plan's ws-pass prints must equal the report's ws_pass `returned`, `not_returned`, `error`, `pending` and `planned`; if not the
  tool refuses (exit 8). The state file's sha256 and the plan file's sha256 must equal the ones the report names; if not it refuses (exit 9).

SELECTION. The shadow selection is the plan's own: `tools.h5_wspass_print_rpc.select` is run again with the plan's flags; its ws-pass signature set, its
  `triggers_n`, and the `plan_selection_sha256` recomputed from the re-selected sets and the plan's `tape_absent` must all equal the plan's (else exit 5).
  `select` returns signatures only, so a second pass with the same window, origin, sealed and `pv` filters (and the same snapshot rule) maps each ws-pass
  signature to the (pool, slot) of its unsealed pv trigger records. That pass counts the unsealed pv trigger records and the count must equal the sum of the
  `triggers_n` that `select` returned (else exit 5). It reads `pool` and `slot` only on a record whose signature is in the plan's ws-pass set; a record whose
  `pool` is not a non-empty string or whose `slot` is not an integer refuses the run (exit 5): the print could not be joined and K could be too low. A
  signature may be recorded in several classes; the join uses its records of every class (the plan asks it once, as ws-pass).
  Keys read from shadow records stay within tools.h5_tipauth_trigger_print.ALLOWED_KEYS. No outcome, fill, exit, ladder or P&L key is read.

OUTPUT (one JSON object on stdout and, once, in --out; counts and hashes only):
  `ws_pass`: planned, sent, not_sent, and sent / not_sent by RPC status (returned, not_returned, error, pending), so the rows add up to the report's counts.
  `dec024_am9`: three booleans by the amendment's item 3. `evaluated`: the report's `reading` is not "no conclusion". `fires`: evaluated and sent-and-
  not_returned is 1 or more. `owner_at_once`: sent-and-not_returned is 1 or more, whatever the reading. `sentence` (top level): "Of the N ws-pass prints the
  public RPC did not return, the bot sent a buy on K." (N = the report's ws-pass not_returned, K = sent and not_returned), built from the counts alone.
  `am9_reading`: a fixed sentence that says "not evaluated" for a check that ended "no conclusion" (it is not "did not fire" and not a pass).
  `report`: `reading_is_no_conclusion` (the report's `reading` is copied only as that boolean) and the report's ws-pass counts.
  `shadow`, `ledger`: scan blocks. `hashes`: tool, report, plan, state (as the report names it, recomputed here), plan_selection, and each ledger file read.
  `sent_count_may_be_low`: true if a live sent row could not be joined (its pool or trigger slot is unusable): K is then a lower bound.
  No pool, mint, signature, slot or time of a print anywhere in the output, the log or an error message. The instants --since-ms, --until-ms and the seal
  instant, which come from the command line or the DEC, are printed.

RUN RULES. One run per finished RPC check, after its report; the tool cannot see whether an earlier run was made (the operator keeps that rule, and --out is
  written once). It takes no CAP-PICK pick, gate log or paper twin. It refuses an --until-ms at or after SEAL_MS, 2026-10-16T01:00:00Z (exit 7); a first run
  of this tool or of the RPC tool after that instant needs quant-proof's ruling on the seal first. DEC-024 Amendment 9 item 2: a LAB_STATE line before the
  first run; the Reviewer's verdict and QP OK on the exact head.

THE OUTPUT FILE. --out is created exclusively (never overwritten; mode 0600) BEFORE the analysis reads the shadow or the ledger; the result is written into it
  at the end, before stdout. A file this run created is removed again when the run ends in a refusal after its creation.

EXIT CODES: 0 the tool ran (NOT a verdict; read `dec024_am9`). Non-zero prints a JSON object with a fixed message and no counts:
  2 usage (argparse); 3 out-dir or shadow directory missing, the plan or `wspass_report.json` missing or not readable, the plan not of this layout;
  4 --since-ms, --until-ms or --expect-code-sha differs from the plan's; 5 the shadow selection differs from the plan's, or a ws-pass record cannot be joined;
  6 --out exists, or cannot be created or written; 7 --until-ms at or after the seal instant; 8 the ws-pass status totals differ from the report's, or the state
  file cannot be read as the RPC tool read it; 9 the plan or state file differs from the hash the report names; 10 a ledger file is unreadable;
  11 a ledger file holds no row (an empty ledger is never "sent 0"); 12 the ledger holds `decision` rows and none is live (the wrong file, or a changed key).

H5 is an execution measurement; EXP-024 ended NOT_DECIDABLE on 2026-10-13 with no look read; not a book. H5 has not cleared the promotion gate.
"""
from __future__ import annotations

import argparse
import collections
import datetime
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import h5_tipauth_census as C  # noqa: E402
from tools import h5_tipauth_item4 as I4  # noqa: E402
from tools import h5_wspass_print_rpc as W  # noqa: E402

TOOL = "h5_wspass_sent_count"
SPEC = {"sent_count": "PR #610 quant-proof comment 6156017843, section 2", "rule": "DEC-024 Amendment 9"}
LABEL = ("H5 is an execution measurement; EXP-024 ended NOT_DECIDABLE on 2026-10-13 with no look read; not a book. "
         "H5 has not cleared the promotion gate. Counts only.")
SEAL_MS = int(datetime.datetime(2026, 10, 16, 1, 0, 0, tzinfo=datetime.timezone.utc).timestamp() * 1000)  # 2026-10-16T01:00:00Z
LEDGER_NAME = "h5-ledger.jsonl"  # h5_executor.py lines 1033 and 1152
OWN_FILE_MODE = 0o600

# ---- the ledger keys (the whole read surface of a ledger row) ----------------------------------------------------------------------------------
KEY_KIND, KEY_MARKER, KEY_TS, KEY_SENT, KEY_POOL, KEY_SLOT = "kind", "mode", "ts_ms", "sent_ms", "pool", "trigger_slot"
LEDGER_KEYS = frozenset({KEY_KIND, KEY_MARKER, KEY_TS, KEY_SENT, KEY_POOL, KEY_SLOT})
DECISION, MODE_LIVE, MODE_DRYRUN = "decision", "live", "dryrun"
STATUSES = ("returned", "not_returned", "error", "pending")
ABSENT = object()

EXIT_USAGE, EXIT_NO_INPUT, EXIT_FLAGS, EXIT_SELECTION, EXIT_OUT, EXIT_SEAL, EXIT_TOTALS, EXIT_HASH = 2, 3, 4, 5, 6, 7, 8, 9
EXIT_LEDGER_UNREADABLE, EXIT_LEDGER_EMPTY, EXIT_LEDGER_NO_LIVE = 10, 11, 12

SENTENCE = "Of the {n} ws-pass prints the public RPC did not return, the bot sent a buy on {k}."
AM9_NOT_EVALUATED = ("not evaluated: the RPC check ended \"no conclusion\", so DEC-024 Amendment 9 fires nothing; this is not \"did not fire\" and not a pass; "
                     "if the count of sent and not_returned is 1 or more the owner is told at once")
AM9_EVALUATED = "evaluated: the RPC check ended with a sentence other than \"no conclusion\""
OUT_EXISTS_MSG = "--out exists; it is never overwritten"
OUT_FAIL_MSG = "--out could not be created or written (its directory is missing or not writable, or the disk is full); no counts were printed"


class InputError(Exception):
    def __init__(self, msg: str, code: int):
        super().__init__(msg)
        self.msg = msg
        self.code = code


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def tool_sha() -> str:
    return sha256_bytes(Path(__file__).read_bytes())


def dumps(res: dict) -> str:
    return json.dumps(res, sort_keys=True, indent=1, allow_nan=False) + "\n"


def _bad_const(_name: str) -> Any:
    raise ValueError("NaN / Infinity is not JSON")


# ---- the finished RPC check -------------------------------------------------------------------------------------------------------------------

REPORT_WS_KEYS = ("planned", "asked", "returned", "not_returned", "error", "pending", "unaskable")


def load_check(rpc_dir: Path, since_ms: int, until_ms: int, expect_code_sha: str) -> dict:
    """The finished RPC check: plan, report, state rows. Every refusal here is made before --out is created. Returns what the later steps need."""
    if not rpc_dir.is_dir():
        raise InputError("the RPC out-dir is missing", EXIT_NO_INPUT)
    report_path, plan_path, state_path = rpc_dir / W.REPORT_NAME, rpc_dir / C.PLAN_NAME, rpc_dir / C.STATE_NAME
    if not report_path.is_file():
        raise InputError("the RPC check is not finished: wspass_report.json is missing", EXIT_NO_INPUT)
    try:
        report_bytes, plan_bytes = report_path.read_bytes(), plan_path.read_bytes()
        report = json.loads(report_bytes.decode("utf-8"), parse_constant=_bad_const)
        plan = json.loads(plan_bytes.decode("utf-8"), parse_constant=_bad_const)
    except (OSError, ValueError, RecursionError):
        raise InputError("the plan or the report in the RPC out-dir is missing or not readable", EXIT_NO_INPUT) from None
    meta = plan.get(W.META_KEY) if isinstance(plan, dict) else None
    try:
        ok = (isinstance(meta, dict) and "tape_absent" in meta and meta.get("tool") == W.TOOL and meta.get("plan_selection_sha256") == W.selection_hash(meta)
              and isinstance(meta["class_sigs"]["ws_pass"], list) and all(isinstance(s, str) and s for s in meta["class_sigs"]["ws_pass"])
              and plan["sets"]["A"] == meta["class_sigs"]["ws_pass"] and set(meta["triggers_n"]) == set(W.CLASS_ORDER))
    except (KeyError, TypeError):
        ok = False
    if not ok:
        raise InputError("the plan is not of the current layout of the RPC tool, or its selection hash does not match", EXIT_NO_INPUT)
    if not isinstance(report, dict) or report.get("tool") != W.TOOL or not isinstance(report.get("reading"), str):
        raise InputError("the report is not this RPC tool's, or has no reading", EXIT_NO_INPUT)
    ws = (report.get("classes") or {}).get("ws_pass") if isinstance(report.get("classes"), dict) else None
    if not (isinstance(ws, dict) and all(I4.isint(ws.get(k)) and ws[k] >= 0 for k in REPORT_WS_KEYS)):
        raise InputError("the report's ws-pass counts are missing or malformed", EXIT_NO_INPUT)
    if (meta["since_ms"], meta["until_ms"], meta["expect_code_sha"]) != (since_ms, until_ms, expect_code_sha):
        raise InputError("--since-ms, --until-ms or --expect-code-sha differs from the plan's", EXIT_FLAGS)
    plan_sha = sha256_bytes(plan_bytes)
    if report.get("plan_sha256") != plan_sha or report.get("plan_selection_sha256") != meta["plan_selection_sha256"]:
        raise InputError("the plan file differs from the hash the report names", EXIT_HASH)
    try:
        state_bytes = state_path.read_bytes()
    except OSError:
        raise InputError("the state file is missing or not readable", EXIT_NO_INPUT) from None
    state_sha = sha256_bytes(state_bytes)
    if report.get("state_sha256") != state_sha:
        raise InputError("the state file differs from the hash the report names", EXIT_HASH)
    return {"plan": plan, "meta": meta, "report": report, "report_ws": {k: ws[k] for k in REPORT_WS_KEYS},
            "hashes": {"report_sha256": sha256_bytes(report_bytes), "plan_sha256": plan_sha, "state_sha256": state_sha}}


def statuses(check: dict, state_path: Path) -> dict[str, str]:
    """RPC status of every planned ws-pass signature, by the RPC tool's own mapping. Raises InputError(8) if the state cannot be read as that tool read it."""
    try:
        rows, _torn = C.read_jsonl_ex(state_path)
        by = C.states_by_sig(rows)
        out: dict[str, str] = {}
        for sig in check["meta"]["class_sigs"]["ws_pass"]:
            ans = by.get(sig, [])
            out[sig] = "pending" if not ans else {"found": "returned", "not_found": "not_returned", "pending": "pending", "error": "error"}[C.classify(ans)]
    except (OSError, KeyError, TypeError, ValueError, SystemExit):
        raise InputError("the state file cannot be read as the RPC tool read it", EXIT_TOTALS) from None
    tot = collections.Counter(out.values())
    rep = check["report_ws"]
    if len(out) != rep["planned"] or any(tot[s] != rep[s] for s in STATUSES):
        raise InputError("the ws-pass status totals differ from the report's counts", EXIT_TOTALS)
    return out


# ---- the shadow: the plan's own selection, then pool and slot of the ws-pass records ----------------------------------------------------------

def reselect(shadow_dir: Path, check: dict, since_ms: int, until_ms: int, expect_code_sha: str, hook: Callable | None) -> dict:
    """Run the RPC tool's `select` again with the plan's flags and require the plan's own ws-pass set, triggers_n and selection hash."""
    meta = check["meta"]
    sel = W.select(shadow_dir, since_ms, until_ms, expect_code_sha, hook)
    fresh = {"since_ms": since_ms, "until_ms": until_ms, "expect_code_sha": expect_code_sha, "class_sigs": {c: sel["sigs"][c] for c in W.CLASS_ORDER},
             "unaskable": sel["unaskable"], "cross_class_duplicates": sel["cross_class_duplicates"], "tape_absent": meta["tape_absent"]}
    if (not sel["n_starts"] or not sel["code_sha_matches"] or sel["sigs"]["ws_pass"] != meta["class_sigs"]["ws_pass"] or sel["triggers_n"] != meta["triggers_n"]
            or W.selection_hash(fresh) != meta["plan_selection_sha256"]):
        raise InputError("the shadow selection differs from the plan's", EXIT_SELECTION)
    return sel


def ws_join_keys(shadow_dir: Path, since_ms: int, until_ms: int, ws_sigs: set[str], pv_total: int, hook: Callable | None) -> tuple[dict[str, set], dict]:
    """signature -> {(pool, slot)} over the unsealed pv trigger records of the run window whose signature is in `ws_sigs`. Mirrors `W.select`: the same
    snapshot rule, origin (the LAST start record in [since, until]), window [origin, until], sealed skip before any other key, `pv` only."""
    counts: collections.Counter = collections.Counter()
    snap = I4.snapshot(I4.hour_files(shadow_dir, since_ms, until_ms))
    starts = []
    for rec in I4.iter_records(snap, collections.Counter(), hook):
        if rec.get("type") == "start":
            t = rec.get("t_ms")
            if I4.isint(t) and since_ms <= t <= until_ms:
                starts.append((t, rec.get("code_sha")))
    if not starts:
        raise InputError("the shadow selection differs from the plan's", EXIT_SELECTION)
    starts.sort(key=lambda s: (s[0], str(s[1])))
    origin = starts[-1][0]
    keys: dict[str, set] = {s: set() for s in ws_sigs}
    pv_n = 0
    for rec in I4.iter_records(snap, counts, hook):
        t, typ = rec.get("t_ms"), rec.get("type")
        if not I4.isint(t) or t < origin or t > until_ms or typ != "trigger":
            continue
        if I4.flag(rec.get("sealed")) or I4.flag(rec.get("h5_look2_sealed")):
            continue
        if rec.get("variant") != "pv":
            continue
        pv_n += 1
        sig = rec.get("signature")
        if not (isinstance(sig, str) and sig in ws_sigs):
            continue
        pool, slot = rec.get("pool"), rec.get("slot")
        if not (isinstance(pool, str) and pool and I4.isint(slot)):
            raise InputError("a ws-pass trigger record has no usable join key (pool, slot): the sent count could be too low", EXIT_SELECTION)
        keys[sig].add((pool, slot))
    if pv_n != pv_total or any(not v for v in keys.values()):
        raise InputError("the shadow selection differs from the plan's", EXIT_SELECTION)
    return keys, {"bad_line": counts["bad_line"], "unreadable_file": counts["unreadable_file"], "pv_trigger_records": pv_n}


# ---- the ledger -------------------------------------------------------------------------------------------------------------------------------

def ledger_files(args: list[str]) -> list[Path]:
    files: list[Path] = []
    seen: set[str] = set()
    for a in args:
        p = Path(a)
        p = p / LEDGER_NAME if p.is_dir() else p
        key = os.path.realpath(p)
        if key in seen:
            raise InputError("the same ledger file is named twice", EXIT_USAGE)
        seen.add(key)
        files.append(p)
    return files


def read_ledger_file(path: Path, hook: Callable | None, sent: collections.Counter, total: collections.Counter) -> dict:
    """Stream one ledger file once: hash its bytes as they are read and parse each line. Updates `total` and `sent` ((pool, slot) -> live sent rows)."""
    h = hashlib.sha256()
    c: collections.Counter = collections.Counter()
    try:
        with open(path, "rb") as fh:
            for raw in fh:
                h.update(raw)
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw.decode("utf-8"), object_hook=hook, parse_constant=_bad_const)
                except (UnicodeDecodeError, ValueError, RecursionError):
                    c["bad_lines"] += 1
                    continue
                if not isinstance(row, dict):
                    c["bad_lines"] += 1
                    continue
                c["records_parsed"] += 1
                if row.get(KEY_KIND) != DECISION:  # every other kind is skipped after `kind`
                    c["other_kind_rows"] += 1
                    continue
                c["decision_rows"] += 1
                marker = row.get(KEY_MARKER, ABSENT)
                if marker is ABSENT:
                    c["decision_rows_without_marker"] += 1
                    continue
                if marker == MODE_DRYRUN:
                    c["decision_rows_dryrun"] += 1
                    continue
                if marker != MODE_LIVE:
                    c["decision_rows_marker_other"] += 1
                    continue
                c["live_decision_rows"] += 1
                if not I4.isint(row.get(KEY_TS)):
                    c["live_decision_rows_ts_not_int"] += 1
                sent_ms = row.get(KEY_SENT, ABSENT)
                if not I4.isint(sent_ms):
                    c["live_decision_rows_sent_ms_not_int"] += 1
                    if sent_ms is not ABSENT and sent_ms is not None:
                        c["live_decision_rows_sent_ms_present_not_int"] += 1
                    continue
                c["live_sent_rows"] += 1
                pool, slot = row.get(KEY_POOL), row.get(KEY_SLOT)
                if not (isinstance(pool, str) and pool and I4.isint(slot)):
                    c["live_sent_rows_join_key_unusable"] += 1
                    continue
                sent[(pool, slot)] += 1
    except OSError:
        raise InputError("a ledger file is unreadable", EXIT_LEDGER_UNREADABLE) from None
    if c["records_parsed"] == 0:
        raise InputError("a ledger file holds no row: an empty or unparsable ledger is never \"sent 0\"", EXIT_LEDGER_EMPTY)
    total.update(c)
    return {"sha256": h.hexdigest(), "records_parsed": c["records_parsed"], "bad_lines": c["bad_lines"]}


def read_ledgers(paths: list[Path], hook: Callable | None) -> tuple[collections.Counter, collections.Counter, list[dict]]:
    sent: collections.Counter = collections.Counter()
    total: collections.Counter = collections.Counter()
    files = []
    for p in paths:
        if not p.is_file():
            raise InputError("a ledger file is unreadable", EXIT_LEDGER_UNREADABLE)
        info = read_ledger_file(p, hook, sent, total)
        files.append({"index": len(files), "sha256": info["sha256"], "records_parsed": info["records_parsed"], "bad_lines": info["bad_lines"]})
    if total["decision_rows"] and not total["live_decision_rows"]:
        raise InputError("the ledger holds decision rows and none is live: the wrong file, or the run-mode key changed", EXIT_LEDGER_NO_LIVE)
    return sent, total, files


# ---- the count --------------------------------------------------------------------------------------------------------------------------------

def count(status_of: dict[str, str], keys: dict[str, set], sent: collections.Counter) -> dict:
    sent_by = {s: 0 for s in STATUSES}
    not_sent_by = {s: 0 for s in STATUSES}
    owner: collections.Counter = collections.Counter()
    for sig, ks in keys.items():
        for k in ks:
            owner[k] += 1
    for sig, st in status_of.items():
        (sent_by if any(k in sent for k in keys[sig]) else not_sent_by)[st] += 1
    n_sent = sum(sent_by.values())
    return {"planned": len(status_of), "sent": n_sent, "not_sent": len(status_of) - n_sent, "sent_by_rpc_status": sent_by, "not_sent_by_rpc_status": not_sent_by,
            "prints_sharing_join_key_n": sum(1 for ks in keys.values() if any(owner[k] > 1 for k in ks)),
            "prints_with_several_join_keys_n": sum(1 for ks in keys.values() if len(ks) > 1)}


def am9(no_conclusion: bool, n_not_returned: int, k: int) -> tuple[dict, str, str]:
    evaluated = not no_conclusion
    block = {"evaluated": evaluated, "fires": bool(evaluated and k >= 1), "owner_at_once": bool(k >= 1)}
    return block, SENTENCE.format(n=n_not_returned, k=k), (AM9_EVALUATED if evaluated else AM9_NOT_EVALUATED)


def analyze(rpc_dir: Path, shadow_dir: Path, ledgers: list[Path], since_ms: int, until_ms: int, expect_code_sha: str, check: dict,
            shadow_hook: Callable | None = None, ledger_hook: Callable | None = None) -> dict:
    meta = check["meta"]
    if not shadow_dir.is_dir():
        raise InputError("shadow directory missing", EXIT_NO_INPUT)
    status_of = statuses(check, rpc_dir / C.STATE_NAME)
    sel = reselect(shadow_dir, check, since_ms, until_ms, expect_code_sha, shadow_hook)
    keys, shadow_scan = ws_join_keys(shadow_dir, since_ms, until_ms, set(meta["class_sigs"]["ws_pass"]), sum(sel["triggers_n"].values()), shadow_hook)
    sent, total, files = read_ledgers(ledgers, ledger_hook)
    ws = count(status_of, keys, sent)
    k = ws["sent_by_rpc_status"]["not_returned"]
    rep_ws = check["report_ws"]
    no_conclusion = check["report"]["reading"] == W.READING_NONE
    block, sentence, am9_text = am9(no_conclusion, rep_ws["not_returned"], k)
    dup_keys = sum(1 for n in sent.values() if n > 1)
    return {
        "tool": TOOL, "spec": SPEC, "label": LABEL,
        "inputs": {"since_ms": since_ms, "until_ms": until_ms, "expect_code_sha": expect_code_sha, "seal_ms": SEAL_MS, "ledger_files_n": len(ledgers)},
        "hashes": {"tool_sha256": tool_sha(), **check["hashes"], "plan_selection_sha256": meta["plan_selection_sha256"], "ledger_files": files},
        "report": {"reading_is_no_conclusion": no_conclusion, "ws_pass": rep_ws},
        "ws_pass": ws,
        "dec024_am9": block, "sentence": sentence, "am9_reading": am9_text,
        "sent_count_may_be_low": bool(total["live_sent_rows_join_key_unusable"]),
        "shadow": {"ws_pass_signatures_reselected_n": len(sel["sigs"]["ws_pass"]), "selection_equals_plan": True, "pv_trigger_records_n": shadow_scan["pv_trigger_records"],
                   "bad_line": shadow_scan["bad_line"], "unreadable_file": shadow_scan["unreadable_file"]},
        "ledger": {"lines": sum(f["records_parsed"] + f["bad_lines"] for f in files), "records_parsed": total["records_parsed"], "bad_lines": total["bad_lines"],
                   "other_kind_rows": total["other_kind_rows"], "decision_rows": total["decision_rows"], "live_decision_rows": total["live_decision_rows"],
                   "decision_rows_dryrun": total["decision_rows_dryrun"], "decision_rows_without_marker": total["decision_rows_without_marker"],
                   "decision_rows_marker_other": total["decision_rows_marker_other"], "live_decision_rows_ts_not_int": total["live_decision_rows_ts_not_int"],
                   "live_decision_rows_sent_ms_not_int": total["live_decision_rows_sent_ms_not_int"],
                   "live_decision_rows_sent_ms_present_not_int": total["live_decision_rows_sent_ms_present_not_int"],
                   "live_sent_rows": total["live_sent_rows"], "live_sent_rows_join_key_unusable": total["live_sent_rows_join_key_unusable"],
                   "live_sent_join_keys_n": len(sent), "live_sent_keys_with_more_than_one_row_n": dup_keys,
                   "live_sent_extra_rows_n": sum(n - 1 for n in sent.values() if n > 1)},
    }


# ---- the run ----------------------------------------------------------------------------------------------------------------------------------

def create_out(path: str):
    """The exclusive create of --out (never overwrites), mode 0600."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, OWN_FILE_MODE)
    try:
        os.chmod(path, OWN_FILE_MODE)
    except OSError:
        pass
    return os.fdopen(fd, "w", encoding="utf-8")


def discard_out(fh, path: str) -> None:
    try:
        fh.close()
    except OSError:
        pass
    try:
        os.unlink(path)
    except OSError:
        pass


def main(argv: list[str] | None = None, shadow_hook: Callable | None = None, ledger_hook: Callable | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], epilog="EXIT CODE: 0 the tool ran (not a verdict). 2 usage, 3 input directory or file missing, 4 flags differ from "
                                 "the plan, 5 shadow selection differs from the plan, 6 --out exists or cannot be written, 7 --until-ms at or after the seal instant, 8 status "
                                 "totals differ from the report, 9 plan or state hash differs from the report, 10 ledger unreadable, 11 ledger empty, 12 no live decision row "
                                 "among decision rows.")
    ap.add_argument("--rpc-out-dir", required=True, help="the finished RPC check's out-dir (wspass_report.json must exist)")
    ap.add_argument("--shadow-dir", required=True)
    ap.add_argument("--since-ms", type=int, required=True)
    ap.add_argument("--until-ms", type=int, required=True)
    ap.add_argument("--expect-code-sha", required=True, help="40 hex: must equal the plan's")
    ap.add_argument("--ledger", nargs="+", required=True, help="the live executor ledger: one or more h5-ledger.jsonl paths, or directories holding that file")
    ap.add_argument("--out", required=True, help="JSON file, created exclusively; an existing file is never overwritten (exit 6)")
    a = ap.parse_args(argv)
    if not re.fullmatch(r"[0-9a-f]{40}", a.expect_code_sha):
        ap.error("--expect-code-sha must be 40 lowercase hex characters")
    fh = None
    try:
        if a.until_ms >= SEAL_MS:
            raise InputError("--until-ms is at or after the seal instant 2026-10-16T01:00:00Z (ms %d): no run; quant-proof rules on the seal first" % SEAL_MS, EXIT_SEAL)
        rpc_dir, shadow_dir = Path(a.rpc_out_dir), Path(a.shadow_dir)
        check = load_check(rpc_dir, a.since_ms, a.until_ms, a.expect_code_sha)
        paths = ledger_files(a.ledger)
        try:
            fh = create_out(a.out)  # exclusive, before the shadow or the ledger is read
        except FileExistsError:
            raise InputError(OUT_EXISTS_MSG, EXIT_OUT) from None
        except OSError:
            raise InputError(OUT_FAIL_MSG, EXIT_OUT) from None
        res = analyze(rpc_dir, shadow_dir, paths, a.since_ms, a.until_ms, a.expect_code_sha, check, shadow_hook, ledger_hook)
        text = dumps(res)
        try:
            fh.write(text)
            fh.close()
        except OSError:
            raise InputError(OUT_FAIL_MSG, EXIT_OUT) from None
    except InputError as err:
        if fh is not None:
            discard_out(fh, a.out)
        sys.stdout.write(dumps({"tool": TOOL, "error": err.msg, "exit_code": err.code}))
        return err.code
    except BaseException:
        if fh is not None:
            discard_out(fh, a.out)
        raise
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
