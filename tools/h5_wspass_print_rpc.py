#!/usr/bin/env python3
"""H5 shadow, report-only live-exposure check: do the ws-pass and refused TRIGGER PRINTS exist on the finalized chain (public RPC)?

Spec: quant-proof rulings on PR #610: comment 6127657282 section 3 (the +6 h interim read, 1 of 5 ws-pass prints absent from the tip tape), on PR #619:
comment 6127897922 (edits 1 to 3, sections D and E, checks R1 to R5), and on PR #610: comment 6156017843 section 1 (the reading rule for a read with more
than one print without a tape row, error classes, the write-once report). It is a COUNT. It is a live-exposure check outside items 1 to 10: it can count
against phase 2 and against the live rule, never for either. It makes no book claim.

  python3 -m tools.h5_wspass_print_rpc plan     --shadow-dir D --since-ms T0 --until-ms T1 --expect-code-sha SHA40 --out-dir O
                                                --tape-absent-ws N --tape-absent-refused N --tape-absent-would-pass N
  python3 -m tools.h5_wspass_print_rpc ask      --out-dir O
  python3 -m tools.h5_wspass_print_rpc reask    --out-dir O
  python3 -m tools.h5_wspass_print_rpc report   --out-dir O
  python3 -m tools.h5_wspass_print_rpc errclass --out-dir O

LAUNCH: from the repo root, as `/usr/bin/python3 -m tools.h5_wspass_print_rpc ...`, WITHOUT `-I`. With `-I` the cwd is not on sys.path, the `tools`
package cannot be imported and the launch fails before any line of this tool runs (that was job #829 for the trigger-print tool). The CLI is sh-safe
(plain flags, no shell syntax, nothing read from stdin).

WHAT IT READS (nothing else): the shadow hour files `h5-shadow-*.jsonl` in --shadow-dir (via tools.h5_tipauth_item4 hour_files / snapshot / iter_records)
and its own out-dir. Selection reuses tools.h5_tipauth_trigger_print: `classify` (ws_pass / would_pass / refused) and the origin-start handling, and
tools.h5_tipauth_item4: `flag` (sealed records are skipped and counted before any other key is read), `on_schedule`, `SETTLE_MS`. Keys read from a
trigger stay within the trigger-print tool's ALLOWED_KEYS (signature, variant, t_ms, type, the sealed flags and what `classify` reads). No outcome, fill,
exit, ladder or P&L key is read. It opens NO tip file, no item-4 or item-8 out file and no executor ledger; it has no tip-dir argument. No underscore
(private) name is imported from the two tools. The three tape-absent counts are typed in by the operator from the LAB_STATE record of the trigger-print
run of the same until-ms; the tool does not read them from any file.

PLAN FLAGS: `--tape-absent-ws`, `--tape-absent-refused`, `--tape-absent-would-pass` are required integers, each from 0 to the `triggers_n` of its class.
They are the numbers of prints without a tape row at the trigger-print run of the same until-ms (ws-pass, refused, would-pass). They are stored in the plan
(`tape_absent`) and inside `plan_selection_sha256`. The tool still opens no out file of the trigger-print or item-4 tools.

WHAT IT ASKS: `getTransaction` at `finalized`, `maxSupportedTransactionVersion` 0, through tools.h5_tipauth_census `cmd_ask` / `cmd_reask` unchanged
(its transport, its fixed public RPC URL with no environment override and no key, 0 Helius credits, at most 2 rps, REASK_MAX 3 rounds, a re-ask only
>= 600,000 ms after the last ask). The plan file is written in the census plan layout (census_plan.json; sets A = ws_pass, B = refused, C = would_pass)
so those functions run on it as they are; the class map, the tape-absent counts and this tool's own hash sit beside the census keys. The state file and
the request log hold, per signature, only: the signature (local files, mode 0600), found / null / error, slot of a found answer, error class, asked_ms.
The tool never prints a signature, pool, slot, URL, exception message or response body.

SIDE FILE (plans made by this layout only): `wspass_codes.jsonl`, mode 0600. A wrapper inside this tool around the unchanged census transport appends,
for every call, `asked_ms` (the same value the state row carries), `sig`, `http_status` (the integer status of an HTTP error; null when no HTTP error was
raised) and `rpc_code` (the integer `error.code` of a JSON-RPC error answer; null otherwise), then returns or re-raises the transport's result unchanged.
It never reads or stores `error.message`, `error.data`, a response body or an exception message. The report prints counts per class by `http_status` and by
`rpc_code`, and whether the side file rows match the state rows. A side file that is missing or does not match changes no `reading`.

CLASSES: every unsealed pv trigger print in [origin start, until] is classed by `classify`. Each distinct signature is asked once, in the highest class
it has (ws_pass, then would_pass, then refused); a signature in two classes is counted in `cross_class_duplicates`. A trigger without a non-empty string
signature cannot be asked: it is counted `unaskable` and blocks any conclusion.

REPORT (one JSON object on stdout and, once, in `wspass_report.json` by exclusive create; a second `report` is refused; no signature, pool or slot): per
class asked, returned, not_returned, error, pending; `error_classes` (signatures in state error, by the error class of their last answer) and
`error_answers` (all error answers, by error class), with the printable names `rpc_error`, `malformed`, `malformed_result`, an exception class name that
matches ^[A-Za-z_][A-Za-z0-9_]{0,63}$, or `other`; the side-file counts; `state_sha256`, `first_asked_ms`, `last_asked_ms`. not_returned = null twice with
the two asks >= 600,000 ms apart; an error is never not_returned; a signature not yet asked, or with one null, is pending. `report` is served only for a
plan of this layout; for a plan of the older layout (no tape-absent counts, no side file) it refuses and `errclass` is the command. `report` refuses, writes
nothing and prints nothing while a signature is pending, never asked or in error and fewer than 3 reask rounds ran; after 3 reask rounds it always runs.

READING RULE (quant-proof, PR #610 comment 6156017843 section 1), declared before any ask of the read it is first used on. Per class: N = not_returned,
A = the number of prints without a tape row at the trigger-print run of the same until-ms (the three plan flags).
Preconditions for any sentence. One failure gives "no conclusion", and the report lists the failed check in `failed_checks`:
  - the tool sha256 equals the plan's tool sha256; `counts` equals `triggers_n` in every class (so unaskable 0 and cross_class_duplicates 0);
  - in every class error 0, pending 0, asked equal to planned equal to returned + not_returned + error + pending; `state_torn_lines_n` 0;
    no state row for a signature outside the plan;
  - A for the ws-pass class at least 1 (the check is for a read on which the ws-pass absent count rose).
  The operator checks that the tool cannot see are listed in the report as `operator_checks`: R1 to R5 of PR #619 comment 6127897922 section D (R5 as
  amended: `interim` false is expected at the +36 h read; the label stays "interim, not the deciding read"), plan `triggers_n` equal to the class counts
  of the trigger-print run, `tape_absent` equal to the absent counts of that run as recorded in LAB_STATE, `plan_sha256` equal to the one printed by the plan
  job, and that run showing differ 0 and undetermined 0 in every class. The report carries `reading_condition`: a sentence other than "no conclusion" holds
  only if every line of `operator_checks` holds.
`reading` is exactly one of the following. At the +36 h read the ws-pass tape-absent count is 2 of 15, the refused 3 of 20, the would-pass 0 of 1:
  S0 (every ws-pass print returned, whatever the refused and would-pass not_returned counts are; a return needs no control):
      "all 15 were returned at finalized commitment; the 2 missing rows are on the tape's side; it is not shown that the prints' reserves were right"
  S1 and S2 also need N at most A in every class (refused not_returned at most 3, would-pass not_returned 0), at least 1 ws-pass print returned and at
  least 1 refused print returned:
  S1 (ws-pass 14 returned and 1 not_returned):
      "1 of 15 was not returned at finalized commitment: consistent with a live-feed print for a transaction not in a finalized block; not proof; the count does not name which print and does not show that it is one of the 2 without a tape row. At least 1 of the 2 without a tape row was returned, so at least 1 missing row is on the tape's side"
  S2 (ws-pass 13 returned and 2 not_returned):
      "2 of 15 were not returned at finalized commitment, as many as have no tape row: consistent with 2 live-feed prints for transactions not in a finalized block; not proof; the count does not name which prints and does not show that they are the 2 without a tape row"
  Anything else: "no conclusion". That includes a ws-pass print not returned while N is above A in any class: the RPC then did not return a print whose row
  the tape holds, so a not-returned answer cannot be read.
Fixed extra sentence, in `reading_extra`, with S0, S1 or S2 when K refused prints are not returned (K at least 1):
      "K of the 20 refused prints were not returned (3 of the 20 have no tape row); the cause is not determined."
At other counts the numbers come from the plan: S1 for N from 1 to A minus 1 (the sentence for N = 1 below, with the plural form for N of 2 or more), S2 for N
equal to A. At tape-absent counts (1, 0, 0) the tool prints the two +6 h sentences, unchanged (they are the forms for a ws-pass A of 1):
      "all {n} were returned at finalized commitment; the missing row is on the tape's side; it is not shown that the print's reserves were right"
      "consistent with a live-feed print for a transaction not in a finalized block; not proof; the count does not name which print"
The general forms, the constants of this file (P = ws-pass prints planned, A = ws-pass tape-absent count, N = ws-pass not_returned, R = A minus N):
  S0, A of 2 or more:
      "all {n} were returned at finalized commitment; the {a} missing rows are on the tape's side; it is not shown that the prints' reserves were right"
  S1, N of 1:
      "1 of {p} was not returned at finalized commitment: consistent with a live-feed print for a transaction not in a finalized block; not proof; the count does not name which print and does not show that it is one of the {a} without a tape row"
  S1, N of 2 or more:
      "{n} of {p} were not returned at finalized commitment: consistent with {n} live-feed prints for transactions not in a finalized block; not proof; the count does not name which prints and does not show that they are among the {a} without a tape row"
  S1, second sentence, R of 1:
      "At least {r} of the {a} without a tape row was returned, so at least {r} missing row is on the tape's side"
  S1, second sentence, R of 2 or more:
      "At least {r} of the {a} without a tape row were returned, so at least {r} missing rows are on the tape's side"
  S2, A of 2 or more:
      "{a} of {p} were not returned at finalized commitment, as many as have no tape row: consistent with {a} live-feed prints for transactions not in a finalized block; not proof; the count does not name which prints and does not show that they are the {a} without a tape row"
  fixed extra sentence:
      "{k} of the {r} refused prints were not returned ({a} of the {r} have no tape row); the cause is not determined."
Disclosure: this rule is written after this read's tape counts (2 of 15, 3 of 20) and the +6 h RPC split (2 returned, 3 error) were known, and before any RPC
answer for this read. The check can count against phase 2 and against the live rule, never for either.
The interim fields: `interim` is true when until minus since is under 24 h; `interim_label` is "interim, not the deciding read" when `interim` is true
and "not the deciding read unless item 4's duration line is met at this until-ms" when it is false; `interim_note` is always the latter.

ERRCLASS (`errclass --out-dir D`): for a finished out-dir of the older layout (a plan made by an older tool sha). No network call, nothing written in D.
It prints the per-class counts, `error_classes`, `error_answers`, `plan_sha256`, `state_sha256`, `first_asked_ms`, `last_asked_ms`, and no `reading`.
It refuses while a signature is pending (or never asked), or while fewer than 3 reask rounds ran and an error is left. It refuses a plan of this layout
(use `report`).

One plan per scheduled read whose ws_pass absent count rose, on that read's until-ms, never re-made (a second `plan` into an out-dir that holds a plan is
refused). No ask runs before the Reviewer's verdict and QP OK on the exact head are on the PR. `ask` and `reask` refuse a plan of the older layout.

Exit codes: 0 the command ran (NOT a verdict); 1 a refusal (message on stderr, no signature in it); 2 no start record at or after --since-ms;
3 missing directory or no plan; 4 refused instant (off schedule or not yet 120 s past); 5 the out-dir already holds a plan; 6 --expect-code-sha does not
match the origin start record; 7 a --tape-absent-* count is outside 0..triggers_n of its class.
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
import urllib.error
from pathlib import Path
from typing import Callable

try:
    from tools import h5_tipauth_census as C
    from tools import h5_tipauth_item4 as I4
    from tools import h5_tipauth_trigger_print as TP
except ImportError:  # run as a script: tools/ is sys.path[0]
    import h5_tipauth_census as C  # type: ignore
    import h5_tipauth_item4 as I4  # type: ignore
    import h5_tipauth_trigger_print as TP  # type: ignore

TOOL = "h5_wspass_print_rpc"
SPEC = {"ruling": "PR #610 comment 6127657282 section 3", "edits": "PR #619 comment 6127897922", "rule2": "PR #610 comment 6156017843 section 1"}
DAY_MS = I4.DAY_MS
CLASS_SETS = (("ws_pass", "A"), ("refused", "B"), ("would_pass", "C"))  # class -> census set letter
CLASS_ORDER = ("ws_pass", "would_pass", "refused")  # precedence when a signature is in two classes
FLAG_OF = {"ws_pass": "--tape-absent-ws", "refused": "--tape-absent-refused", "would_pass": "--tape-absent-would-pass"}

# ---- the reading sentences: constants, or built from constants. The docstring and the LAB_STATE declaration carry the same words. ----
READING_ALL = "all {n} were returned at finalized commitment; the missing row is on the tape's side; it is not shown that the print's reserves were right"
READING_ALL_MANY = "all {n} were returned at finalized commitment; the {a} missing rows are on the tape's side; it is not shown that the prints' reserves were right"
READING_ABSENT = "consistent with a live-feed print for a transaction not in a finalized block; not proof; the count does not name which print"
READING_S1_ONE = ("1 of {p} was not returned at finalized commitment: consistent with a live-feed print for a transaction not in a finalized block; not proof; "
                  "the count does not name which print and does not show that it is one of the {a} without a tape row")
READING_S1_MANY = ("{n} of {p} were not returned at finalized commitment: consistent with {n} live-feed prints for transactions not in a finalized block; not proof; "
                   "the count does not name which prints and does not show that they are among the {a} without a tape row")
READING_S1_TAIL_ONE = "At least {r} of the {a} without a tape row was returned, so at least {r} missing row is on the tape's side"
READING_S1_TAIL_MANY = "At least {r} of the {a} without a tape row were returned, so at least {r} missing rows are on the tape's side"
READING_S2 = ("{a} of {p} were not returned at finalized commitment, as many as have no tape row: consistent with {a} live-feed prints for transactions not in a "
              "finalized block; not proof; the count does not name which prints and does not show that they are the {a} without a tape row")
READING_NONE = "no conclusion"
EXTRA_REFUSED = "{k} of the {r} refused prints were not returned ({a} of the {r} have no tape row); the cause is not determined."
INTERIM_LABEL = "interim, not the deciding read"
INTERIM_NOTE = "not the deciding read unless item 4's duration line is met at this until-ms"
NOTE = "a live-exposure check outside items 1 to 10: it can count against phase 2 and against the live rule, never for either; exit 0 is not a verdict"
READING_CONDITION = 'a sentence other than "no conclusion" holds only if every line of operator_checks holds; one failure gives "no conclusion", with the counts and the failed check'
OPERATOR_CHECKS = [
    "every job exited 0; tool_sha256 equals the merged tool sha recorded at merge",
    "plan_sha256 equals the plan_sha256 printed by the plan job",
    "plan triggers_n equals the class counts of the trigger-print run of the same until-ms as recorded in LAB_STATE",
    "tape_absent equals the absent counts of that trigger-print run as recorded in LAB_STATE",
    "that trigger-print record shows differ 0 and undetermined 0 in every class",
    "wc -l of the state file equals the sum of the calls of the ask and reask jobs",
    "the last reask printed calls 0, or reask_rounds_n is 3",
]
OLD_LAYOUT_REFUSAL = "refused: this plan is of the older layout (no tape-absent counts, no side file): `report` serves plans of the current layout only; use `errclass`"
CURRENT_LAYOUT_REFUSAL = "refused: this plan is of the current layout: `errclass` serves older-layout out-dirs only; use `report`"

META_KEY = "wspass"
OWN_FILE_MODE = 0o600
SIDE_NAME, REPORT_NAME = "wspass_codes.jsonl", "wspass_report.json"
HASH_KEYS_OLD = ("since_ms", "until_ms", "expect_code_sha", "class_sigs", "unaskable", "cross_class_duplicates")
HASH_KEYS = HASH_KEYS_OLD + ("tape_absent",)
ERR_BUILTIN = frozenset({"rpc_error", "malformed", "malformed_result"})  # the census `interpret` names
ERR_CLASS_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")  # the census `ask_one` stores type(e).__name__


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def tool_sha() -> str:
    return sha256_bytes(Path(__file__).read_bytes())


def select(shadow_dir: Path, since_ms: int, until_ms: int, expect_code_sha: str, object_hook: Callable | None = None) -> dict:
    """Unsealed pv trigger prints of the run in [origin, until_ms], by class. Sealed records are skipped (counted) before any other key is read."""
    counts: collections.Counter = collections.Counter()
    snap = I4.snapshot(I4.hour_files(shadow_dir, since_ms, until_ms))
    starts = []
    for rec in I4.iter_records(snap, collections.Counter(), object_hook):
        if rec.get("type") == "start":
            t = rec.get("t_ms")
            if I4.isint(t) and since_ms <= t <= until_ms:
                starts.append((t, rec.get("code_sha")))
    starts.sort(key=lambda s: (s[0], str(s[1])))
    out: dict = {"n_starts": len(starts), "origin_t_ms": starts[-1][0] if starts else None, "origin_code_sha": starts[-1][1] if starts else None,
                 "code_sha_matches": bool(starts) and starts[-1][1] == expect_code_sha, "sigs": {c: [] for c in CLASS_ORDER},
                 "triggers_n": {c: 0 for c in CLASS_ORDER}, "unaskable": {c: 0 for c in CLASS_ORDER}, "cross_class_duplicates": 0,
                 "skipped_sealed_trigger": 0}
    if not starts:
        return out
    origin = starts[-1][0]
    seen: dict[str, str] = {}
    for rec in I4.iter_records(snap, counts, object_hook):
        t, typ = rec.get("t_ms"), rec.get("type")
        if not I4.isint(t) or t < origin or t > until_ms or typ != "trigger":
            continue
        if I4.flag(rec.get("sealed")) or I4.flag(rec.get("h5_look2_sealed")):
            out["skipped_sealed_trigger"] += 1
            continue
        if rec.get("variant") != "pv":
            continue
        cls = TP.classify(rec)
        out["triggers_n"][cls] += 1
        sig = rec.get("signature")
        if not (isinstance(sig, str) and sig):
            out["unaskable"][cls] += 1
            continue
        prev = seen.get(sig)
        if prev is None:
            seen[sig] = cls
        elif prev != cls:
            out["cross_class_duplicates"] += 1
            if CLASS_ORDER.index(cls) < CLASS_ORDER.index(prev):
                seen[sig] = cls
    for sig, cls in seen.items():
        out["sigs"][cls].append(sig)
    for c in CLASS_ORDER:
        out["sigs"][c] = sorted(out["sigs"][c])
    return out


def selection_hash(body: dict) -> str:
    """The plan's own hash. A meta block with `tape_absent` is of the current layout and hashes it; a block without it is of the older layout."""
    keys = HASH_KEYS if "tape_absent" in body else HASH_KEYS_OLD
    return sha256_bytes(C.dumps({k: body[k] for k in keys}).encode("utf-8"))


def build_plan(sel: dict, since_ms: int, until_ms: int, expect_code_sha: str, tape_absent: dict) -> dict:
    sets = {letter: list(sel["sigs"][cls]) for cls, letter in CLASS_SETS}
    cap = sum(len(v) for v in sets.values())
    meta = {"tool": TOOL, "spec": SPEC, "tool_sha256": tool_sha(), "since_ms": since_ms, "until_ms": until_ms, "expect_code_sha": expect_code_sha,
            "origin_t_ms": sel["origin_t_ms"], "class_letters": dict(CLASS_SETS), "class_sigs": {c: sel["sigs"][c] for c in CLASS_ORDER},
            "triggers_n": sel["triggers_n"], "unaskable": sel["unaskable"], "cross_class_duplicates": sel["cross_class_duplicates"],
            "counts": {c: len(sel["sigs"][c]) for c in CLASS_ORDER}, "tape_absent": {c: tape_absent[c] for c in CLASS_ORDER}}
    meta["plan_selection_sha256"] = selection_hash(meta)
    # the census plan layout, so C.cmd_ask / C.cmd_reask / C.load_plan run on it unchanged
    plan = {"tool": C.TOOL, "spec": C.SPEC, "item4_sha256": "none: no item-4 input", "window": {"origin_t_ms": sel["origin_t_ms"], "until_ms": until_ms, "since_ms": since_ms},
            "interim": (until_ms - since_ms) < DAY_MS, "sets": sets, "sizes": {k: len(v) for k, v in sets.items()}, "call_cap": cap, "total_call_cap": C.CALL_ROUNDS * cap,
            "malformed_entries_n": 0, "slots": {}, "triggers": [], META_KEY: meta}
    plan["selection_sha256"] = C.selection_hash(plan)
    return plan


def _write_private(path: Path, data: bytes) -> None:
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, OWN_FILE_MODE)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.chmod(path, OWN_FILE_MODE)


def _touch_private(path: Path) -> None:
    if not path.exists():
        _write_private(path, b"")
    os.chmod(path, OWN_FILE_MODE)


def cmd_plan(shadow_dir: Path, out_dir: Path, since_ms: int, until_ms: int, expect_code_sha: str, tape_absent: dict, now_ms: int,
             object_hook: Callable | None = None) -> tuple[int, dict]:
    def err(msg: str, code: str, n: int) -> tuple[int, dict]:
        return n, {"tool": TOOL, "error": msg, "blocking_reasons": [code]}

    if not I4.on_schedule(since_ms, until_ms):
        return err("--until-ms is not a scheduled instant for --since-ms", "until_ms_not_on_schedule", 4)
    if now_ms < until_ms + I4.SETTLE_MS:
        return err("--until-ms is not yet 120000 ms in the past", "until_ms_not_reached", 4)
    if (out_dir / C.PLAN_NAME).exists():
        return err("the out-dir already holds a plan: one plan per read, never re-made", "plan_exists", 5)
    if not shadow_dir.is_dir():
        return err("shadow directory missing", "missing_directory", 3)
    sel = select(shadow_dir, since_ms, until_ms, expect_code_sha, object_hook)
    if not sel["n_starts"]:
        return err("no start record at or after --since-ms", "no_start_record", 2)
    if not sel["code_sha_matches"]:
        return err("--expect-code-sha does not match the origin start record", "code_sha_mismatch", 6)
    for c in CLASS_ORDER:
        v = tape_absent.get(c)
        if not (C.isint(v) and 0 <= v <= sel["triggers_n"][c]):
            return err(f"{FLAG_OF[c]} is not an integer from 0 to the triggers_n of its class", "tape_absent_out_of_range", 7)
    out_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(out_dir, 0o700)
    plan = build_plan(sel, since_ms, until_ms, expect_code_sha, tape_absent)
    text = C.dumps(plan)
    _write_private(out_dir / C.PLAN_NAME, text.encode("utf-8"))
    _touch_private(out_dir / C.STATE_NAME)
    _touch_private(out_dir / C.LOG_NAME)
    _touch_private(out_dir / SIDE_NAME)
    m = plan[META_KEY]
    return 0, {"tool": TOOL, "planned": True, "counts": m["counts"], "triggers_n": m["triggers_n"], "unaskable": m["unaskable"], "tape_absent": m["tape_absent"],
               "interim": plan["interim"], "plan_selection_sha256": m["plan_selection_sha256"], "tool_sha256": m["tool_sha256"],
               "plan_sha256": sha256_bytes(text.encode("utf-8"))}


def _meta(plan: dict) -> dict:
    """This tool's meta block of a loaded plan, or a refusal. Either layout; the caller looks at `tape_absent`."""
    meta = plan.get(META_KEY)
    try:
        ok = isinstance(meta, dict) and meta.get("plan_selection_sha256") == selection_hash(meta)
    except (KeyError, TypeError):
        ok = False
    if not ok:
        raise SystemExit("refused: plan is not this tool's, or its selection hash does not match")
    ta = meta.get("tape_absent")
    if ta is not None and not (isinstance(ta, dict) and set(ta) == set(CLASS_ORDER) and all(C.isint(v) and 0 <= v <= meta["triggers_n"][c] for c, v in ta.items())):
        raise SystemExit("refused: the plan's tape-absent counts are malformed")
    return meta


def _lock_files(out_dir: Path) -> None:
    for name in (C.STATE_NAME, C.LOG_NAME, SIDE_NAME):
        if (out_dir / name).exists():
            os.chmod(out_dir / name, OWN_FILE_MODE)


def _current_layout_or_refuse(out_dir: Path) -> None:
    if "tape_absent" not in _meta(C.load_plan(out_dir)):
        raise SystemExit("refused: this plan is of the older layout: no ask, no re-ask; `errclass` reads it")


def side_transport(inner: Callable, side_path: Path, current_asked_ms: Callable) -> Callable:
    """Wrap the census transport. Appends asked_ms, sig, http_status, rpc_code to the side file, then returns or re-raises the transport's own result.
    Never reads error.message, error.data, a response body or an exception message. A failed append changes nothing the census sees."""
    def write(sig: object, status: object, code: object) -> None:
        try:
            C.append_jsonl(side_path, {"asked_ms": current_asked_ms(), "sig": sig, "http_status": status, "rpc_code": code})
        except Exception:  # noqa: BLE001  (best effort; the report compares the side rows with the state rows)
            pass

    def wrapped(url: str, payload: dict, timeout: float) -> object:
        sig = payload["params"][0]
        try:
            resp = inner(url, payload, timeout)
        except Exception as e:  # noqa: BLE001
            status = e.code if isinstance(e, urllib.error.HTTPError) and C.isint(e.code) else None
            write(sig, status, None)
            raise
        code = None
        if isinstance(resp, dict) and isinstance(resp.get("error"), dict) and C.isint(resp["error"].get("code")):
            code = resp["error"]["code"]
        write(sig, None, code)
        return resp

    return wrapped


def _run_census(fn: Callable, out_dir: Path, transport: Callable, sleep: Callable, now_ms: Callable) -> int:
    _current_layout_or_refuse(out_dir)
    last: list = [None]

    def clock() -> int:
        last[0] = now_ms()
        return last[0]

    _touch_private(out_dir / SIDE_NAME)
    n = fn(out_dir, side_transport(transport, out_dir / SIDE_NAME, lambda: last[0]), sleep, clock)
    _lock_files(out_dir)
    return n


def cmd_ask(out_dir: Path, transport: Callable = C.http_transport, sleep: Callable = time.sleep,
            now_ms: Callable = lambda: int(time.time() * 1000)) -> int:
    return _run_census(C.cmd_ask, out_dir, transport, sleep, now_ms)


def cmd_reask(out_dir: Path, transport: Callable = C.http_transport, sleep: Callable = time.sleep,
              now_ms: Callable = lambda: int(time.time() * 1000)) -> int:
    return _run_census(C.cmd_reask, out_dir, transport, sleep, now_ms)


def err_name(v: object) -> str:
    """A printable error class: the census names, an exception class name, or `other`. Never a message."""
    if isinstance(v, str) and (v in ERR_BUILTIN or ERR_CLASS_RE.fullmatch(v)):
        return v
    return "other"


def _asked_key(a: dict) -> int:
    t = a.get("asked_ms")
    return t if C.isint(t) else -1


def tally(plan: dict, meta: dict, rows: list[dict]) -> tuple[dict, dict, dict]:
    """Per class: counts, `error_classes` (signatures in state error, by the error class of their last answer), `error_answers` (all error answers)."""
    by = C.states_by_sig(rows)
    per: dict = {}
    ecls: dict = {}
    eans: dict = {}
    for cls in CLASS_ORDER:
        d = {"asked": 0, "returned": 0, "not_returned": 0, "error": 0, "pending": 0}
        ec: collections.Counter = collections.Counter()
        ea: collections.Counter = collections.Counter()
        sigs = plan["sets"][meta["class_letters"][cls]]
        for sig in sigs:
            ans = by.get(sig, [])
            if not ans:
                d["pending"] += 1
                continue
            d["asked"] += 1
            st = C.classify(ans)  # found | not_found | pending | error; not_found only after two nulls >= 600,000 ms apart
            d[{"found": "returned", "not_found": "not_returned", "pending": "pending", "error": "error"}[st]] += 1
            for a in ans:
                if a.get("result") == "error":
                    ea[err_name(a.get("err"))] += 1
            if st == "error":
                ec[err_name(sorted(ans, key=_asked_key)[-1].get("err"))] += 1
        d["planned"] = len(sigs)
        d["unaskable"] = meta["unaskable"][cls]
        per[cls] = d
        ecls[cls] = dict(sorted(ec.items()))
        eans[cls] = dict(sorted(ea.items()))
    return per, ecls, eans


def _plural(r: int, one: str, many: str) -> str:
    return one if r == 1 else many


def evaluate(per: dict, meta: dict, torn: int, outside_plan_rows: int, same_tool_sha: bool) -> tuple[str, list[str], list[str]]:
    """(reading, reading_extra, failed_checks). `reading` is exactly one sentence; failed_checks names what failed (empty when a sentence is given)."""
    ta, tn = meta["tape_absent"], meta["triggers_n"]
    failed: list[str] = []
    if not same_tool_sha:
        failed.append("tool_sha256_differs_from_plan_tool_sha256")
    if meta["cross_class_duplicates"]:
        failed.append("cross_class_duplicates_above_0")
    if torn:
        failed.append("state_torn_lines_above_0")
    if outside_plan_rows:
        failed.append("state_rows_outside_the_plan")
    for c in CLASS_ORDER:
        d = per[c]
        if d["planned"] != tn[c]:
            failed.append(f"{c}_counts_differ_from_triggers_n")
        for k in ("unaskable", "error", "pending"):
            if d[k]:
                failed.append(f"{c}_{k}_above_0")
        if not d["asked"] == d["planned"] == d["returned"] + d["not_returned"] + d["error"] + d["pending"]:
            failed.append(f"{c}_asked_planned_sum_mismatch")
    if ta["ws_pass"] < 1:
        failed.append("ws_pass_tape_absent_is_0")
    if failed:
        return READING_NONE, [], failed
    ws, rf = per["ws_pass"], per["refused"]
    p, a, n = ws["planned"], ta["ws_pass"], ws["not_returned"]
    if n == 0:  # S0 (the +6 h sentence when A is 1)
        reading = READING_ALL.format(n=p) if a == 1 else READING_ALL_MANY.format(n=p, a=a)
    else:
        for c in CLASS_ORDER:
            if per[c]["not_returned"] > ta[c]:
                failed.append(f"{c}_not_returned_above_tape_absent")
        if ws["returned"] < 1:
            failed.append("no_ws_pass_print_returned")
        if rf["returned"] < 1:
            failed.append("no_refused_print_returned")
        if failed:
            return READING_NONE, [], failed
        if n == a:  # S2 (the +6 h sentence when A is 1)
            reading = READING_ABSENT if a == 1 else READING_S2.format(a=a, p=p)
        else:  # S1, 1 <= N <= A - 1
            r = a - n
            head = READING_S1_ONE.format(p=p, a=a) if n == 1 else READING_S1_MANY.format(n=n, p=p, a=a)
            tail = _plural(r, READING_S1_TAIL_ONE, READING_S1_TAIL_MANY).format(r=r, a=a)
            reading = head + ". " + tail
    extra = [EXTRA_REFUSED.format(k=rf["not_returned"], r=rf["planned"], a=ta["refused"])] if rf["not_returned"] >= 1 else []
    return reading, extra, []


def _code_key(v: object) -> str:
    if v is None:
        return "null"
    return str(v) if C.isint(v) else "other"


def side_summary(out_dir: Path, plan: dict, meta: dict, rows: list[dict]) -> tuple[dict, dict]:
    """Counts per class by http_status and by rpc_code, from the side file. Integers or null only; no signature, message or body."""
    cls_of = {s: c for c in CLASS_ORDER for s in plan["sets"][meta["class_letters"][c]]}
    path = out_dir / SIDE_NAME
    side, torn = [], 0
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                v = json.loads(line)
                if not isinstance(v, dict):
                    raise ValueError("not an object")
                side.append(v)
            except ValueError:
                torn += 1
    codes = {c: {"http_status": collections.Counter(), "rpc_code": collections.Counter(), "rows_n": 0} for c in CLASS_ORDER}
    unknown = 0
    for r in side:
        c = cls_of.get(r.get("sig"))
        if c is None:
            unknown += 1
            continue
        codes[c]["rows_n"] += 1
        codes[c]["http_status"][_code_key(r.get("http_status"))] += 1
        codes[c]["rpc_code"][_code_key(r.get("rpc_code"))] += 1
    state_n = collections.Counter(cls_of[r["sig"]] for r in rows if r.get("sig") in cls_of)
    matches = torn == 0 and unknown == 0 and all(codes[c]["rows_n"] == state_n.get(c, 0) for c in CLASS_ORDER)
    out = {c: {"http_status": dict(sorted(v["http_status"].items())), "rpc_code": dict(sorted(v["rpc_code"].items())), "rows_n": v["rows_n"]}
           for c, v in codes.items()}
    return out, {"present": path.exists(), "rows_n": len(side), "torn_lines_n": torn, "unknown_sig_rows_n": unknown, "matches_state": matches}


def _state(out_dir: Path) -> tuple[list[dict], int, str | None]:
    rows, torn = C.read_jsonl_ex(out_dir / C.STATE_NAME)
    sha = sha256_bytes((out_dir / C.STATE_NAME).read_bytes()) if (out_dir / C.STATE_NAME).exists() else None
    return rows, torn, sha


def _asked_span(rows: list[dict]) -> tuple[int | None, int | None]:
    t = [r["asked_ms"] for r in rows if C.isint(r.get("asked_ms"))]
    return (min(t) if t else None, max(t) if t else None)


def cmd_report(out_dir: Path) -> dict:
    plan = C.load_plan(out_dir)
    meta = _meta(plan)
    if "tape_absent" not in meta:
        raise SystemExit(OLD_LAYOUT_REFUSAL)
    if (out_dir / REPORT_NAME).exists():
        raise SystemExit("refused: a report already exists in this out-dir (write-once)")
    rows, torn, state_sha = _state(out_dir)
    per, ecls, eans = tally(plan, meta, rows)
    rounds = len({r.get("round") for r in rows if str(r.get("round")).startswith("reask")})
    if rounds < C.REASK_MAX and any(per[c]["pending"] or per[c]["error"] for c in CLASS_ORDER):
        raise SystemExit("refused: the asks are not finished (a signature is pending, never asked or in error, and fewer than 3 reask rounds ran): no report is written")
    in_plan = {s for k in ("A", "B", "C") for s in plan["sets"][k]}
    outside = sum(1 for r in rows if r.get("sig") not in in_plan)
    plan_tool = meta["tool_sha256"]
    reading, extra, failed = evaluate(per, meta, torn, outside, tool_sha() == plan_tool)
    codes, side = side_summary(out_dir, plan, meta, rows)
    first, last = _asked_span(rows)
    interim = (meta["until_ms"] - meta["since_ms"]) < DAY_MS
    rep = {"tool": TOOL, "spec": SPEC, "classes": per, "error_classes": ecls, "error_answers": eans, "codes": codes, "side_file": side,
           "tape_absent": meta["tape_absent"], "triggers_n": meta["triggers_n"], "interim": interim, "interim_label": INTERIM_LABEL if interim else INTERIM_NOTE, "interim_note": INTERIM_NOTE,
           "cross_class_duplicates": meta["cross_class_duplicates"], "tool_sha256": tool_sha(), "plan_tool_sha256": plan_tool,
           "plan_selection_sha256": meta["plan_selection_sha256"], "plan_sha256": sha256_bytes((out_dir / C.PLAN_NAME).read_bytes()),
           "state_sha256": state_sha, "first_asked_ms": first, "last_asked_ms": last,
           "reask_rounds_n": rounds, "state_torn_lines_n": torn,
           "reading": reading, "reading_extra": extra, "failed_checks": failed, "reading_condition": READING_CONDITION, "operator_checks": OPERATOR_CHECKS, "note": NOTE}
    try:
        _write_private(out_dir / REPORT_NAME, C.dumps(rep).encode("utf-8"))  # exclusive create
    except FileExistsError:
        raise SystemExit("refused: a report already exists in this out-dir (write-once)") from None
    return rep


def cmd_errclass(out_dir: Path) -> dict:
    """Error classes of a finished out-dir of the older layout. No network, nothing written, no reading."""
    plan = C.load_plan(out_dir)
    meta = _meta(plan)
    if "tape_absent" in meta:
        raise SystemExit(CURRENT_LAYOUT_REFUSAL)
    rows, torn, state_sha = _state(out_dir)
    per, ecls, eans = tally(plan, meta, rows)
    rounds = len({r.get("round") for r in rows if str(r.get("round")).startswith("reask")})
    if any(per[c]["pending"] for c in CLASS_ORDER):
        raise SystemExit("refused: a signature is pending or was never asked: the check is not finished")
    if rounds < C.REASK_MAX and any(per[c]["error"] for c in CLASS_ORDER):
        raise SystemExit("refused: fewer than 3 reask rounds ran and an error is left: the check is not finished")
    first, last = _asked_span(rows)
    return {"tool": TOOL, "spec": SPEC, "layout": "older", "classes": per, "error_classes": ecls, "error_answers": eans,
            "plan_sha256": sha256_bytes((out_dir / C.PLAN_NAME).read_bytes()), "state_sha256": state_sha, "first_asked_ms": first, "last_asked_ms": last,
            "reask_rounds_n": rounds, "state_torn_lines_n": torn, "plan_tool_sha256": meta["tool_sha256"], "tool_sha256": tool_sha(),
            "note": "error classes only: no reading is given; " + NOTE}


def main(argv: list[str] | None = None, transport: Callable = C.http_transport, sleep: Callable = time.sleep, now_ms: int | None = None,
         object_hook: Callable | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["plan", "ask", "reask", "report", "errclass"])
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--shadow-dir")
    ap.add_argument("--since-ms", type=int)
    ap.add_argument("--until-ms", type=int)
    ap.add_argument("--expect-code-sha")
    for flag in FLAG_OF.values():
        ap.add_argument(flag, type=int)
    a = ap.parse_args(argv)
    out = Path(a.out_dir)
    if a.cmd == "plan":
        absent = {"ws_pass": a.tape_absent_ws, "refused": a.tape_absent_refused, "would_pass": a.tape_absent_would_pass}
        for name, val in (("--shadow-dir", a.shadow_dir), ("--since-ms", a.since_ms), ("--until-ms", a.until_ms), ("--expect-code-sha", a.expect_code_sha),
                          *((FLAG_OF[c], absent[c]) for c in CLASS_ORDER)):
            if val is None:
                ap.error(f"{name} is required")
        if not re.fullmatch(r"[0-9a-f]{40}", a.expect_code_sha):
            ap.error("--expect-code-sha must be 40 lowercase hex characters")
        now = int(time.time() * 1000) if now_ms is None else now_ms
        code, res = cmd_plan(Path(a.shadow_dir), out, a.since_ms, a.until_ms, a.expect_code_sha, absent, now, object_hook)
    else:
        if any(getattr(a, k) is not None for k in ("tape_absent_ws", "tape_absent_refused", "tape_absent_would_pass")):
            ap.error("the --tape-absent-* flags belong to `plan` only")
        if not (out / C.PLAN_NAME).exists():
            sys.stdout.write(C.dumps({"tool": TOOL, "error": "no plan in the out-dir", "blocking_reasons": ["no_plan"]}))
            return 3
        if a.cmd == "report":
            code, res = 0, cmd_report(out)
        elif a.cmd == "errclass":
            code, res = 0, cmd_errclass(out)
        else:
            n = (cmd_ask if a.cmd == "ask" else cmd_reask)(out, transport, sleep)
            code, res = 0, {"tool": TOOL, "calls": n}
    sys.stdout.write(C.dumps(res))
    return code


if __name__ == "__main__":
    sys.exit(main())
