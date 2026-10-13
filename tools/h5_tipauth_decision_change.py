#!/usr/bin/env python3
"""H5 tipauth path: the parser differential and the decision-change account, COUNTS ONLY (quant-proof, PR #614: round 1 comment 6121641942,
section 4 item 5.5 and section 5 rows 3 and 4 (i); round 3 comment 6153106117, "Item 4 (i)"; PR #638 round 2: reviewer comment 6153739735 B1-B3,
quant-proof comment 6153922480 R1-R4).

For every unsealed `type: trigger` record of the counted window of the H5 shadow's hourly files it computes three results from the record alone
(no executor state, no network, no ledger, no tip tape):
  A  the BASE parser    (the installed build's tools/h5_executor.py):  parse_shadow_trigger(row, variant)
  B  the HEAD parser, path OFF, twice:  parse_shadow_trigger(row, variant)  and  parse_shadow_trigger(row, variant, tipauth=None)
  C  the HEAD parser, path ON and available:  parse_shadow_trigger(row, variant, tipauth=<availability>)
and prints only counts: A against B (required 0 differences in (pass or refuse, reason, trigger fields)), A against C as a transition table, the
refusals that become passes (split by the A reason), the passes that become refusals (required 0), any other change (required 0), the agreement of the
executor with the item-4 tool's would_pass predicate (required 0 disagreements), the reconciliation of item 4's would-pass count with the changes, and
whether the whole window was read.

  python3 tools/h5_tipauth_decision_change.py --shadow-dir DIR --since-ms T0 --until-ms T1 --expect-code-sha SHA40 \\
      (--base-file PATH | --base-git-sha SHA40) [--head-file PATH] [--expect-base-sha256 HEX64] [--expect-head-sha256 HEX64] --out FILE

THE VERDICT has seven booleans and nothing else; every one must be true, and none is a statement about trades or profit:
  a_vs_b_default_zero_differences, a_vs_b_tipauth_none_zero_differences, passes_to_refusals_zero, other_changes_zero, would_pass_disagreements_zero,
  item4_count_reconciles (item 4's would-pass count in its selection = refusals that become passes + those a later check of the parser refuses + those a
  check before the websocket gate refuses in both builds, remainder 0, and the becomes-pass count equals the total of refusals to passes),
  read_complete (no unreadable or dropped hour file, no expected hour file missing, every newline-terminated line that matches the trigger pattern parsed
  as a trigger record, no trigger without an integer t_ms, at least one unsealed trigger read).
A false read_complete means the five comparison booleans were computed on a partial read: explain it, do not read past it.

HOW (C) PUTS THE PATH "ON" THROUGH THE HEAD'S OWN API. parse_shadow_trigger takes `tipauth`: None = path off; "" = path on and available; any other
string = on but unavailable (that string is the refusal). The caller (H5Executor.intent_tick) takes that string from H5Executor._tipauth_availability().
This tool does not guess "": it calls the head's own H5Executor._tipauth_availability (the unbound function) on a stub `self` that says "nothing
latched, no look pending, watch ok, nothing at risk, rule v1, variant pv" (exit_mode, trigger_variant, counters.tipauth = {}, h5cfg = {}, and
_tipauth_check_budget / _tipauth_watch_check / _tipauth_at_risk replaced by their all-clear answers) and passes the string it returns. If the head has no
such function, it raises on the stub, or it answers anything but "", that is an input error (exit 7): there is no fallback (quant-proof R4).
The gate itself (tipauth_gate) is never re-implemented here: C is the head's own parse.

SCHEDULE (quant-proof R1). This tool is a second reader of item 4's would-pass count, so it follows item 4's peeking rule: --until-ms must satisfy
item4.on_schedule(since, until) and the run must start at or after until + item4.SETTLE_MS (both imported, not copied); otherwise exit 8, no counts,
no output file. `main(argv, now_ms=None)` takes the clock as an argument so tests do not sleep.

WHAT IS REUSED FROM tools/h5_tipauth_item4.py (imported, not copied): on_schedule, SETTLE_MS, HOUR_MS, schedule horizon, hour_files, snapshot (the file
list and sizes, taken once, read only up to those sizes), iter_records (the start-record pass), isint, flag (the sealed rule: anything but absent, null or
the literal False is sealed), would_pass (the one predicate), and the strict-JSON hooks _bad_const / _parse_float. WHAT IS MIRRORED (analyze() has no
callable for it): the origin = the LAST start record with since <= t_ms <= until; a record belongs to the window if its integer t_ms is >= origin and
<= until; sealed (`sealed` or `h5_look2_sealed`) records are excluded and counted, never parsed, and for them only `type`, `t_ms` and the two flags are
read. WHAT DIFFERS FROM item 4, on purpose, because the executor reads those lines: a line with a non-string pool, another variant, NaN/Infinity, or a
non-UTF-8 byte is still parsed (the executor's tail_lines decodes with errors="replace" and json.loads accepts NaN); `records.in_item4_selection` counts
the lines item 4 keeps and must equal its `records.triggers`.

EXIT CODE: 0 means only that the tool ran, whatever the counts. Read `verdict` in the JSON. Non-zero is an input error and prints a JSON error with no
counts: 2 usage (argparse), 3 shadow directory missing, 4 no start record at or after --since-ms, 5 --expect-code-sha does not match the origin start
record's code_sha, 6 --out exists or its directory is missing (it is never overwritten), 7 an executor file cannot be loaded or has the wrong shape (also:
base and head are the same bytes, the base parser has a `tipauth` parameter, an --expect-*-sha256 does not match, the head's availability cannot be asked),
8 --until-ms is not an item-4 scheduled instant, or the run is earlier than that instant plus the settle time.
The two executor files are executed as modules (they import `tools.*` and `solders` from the running checkout's environment, so run this where the
executor's tests run). Nothing outside --shadow-dir and the two executor files is opened. Memory: each hour file is read whole and split; run it as a
capped job.
"""
from __future__ import annotations

import argparse
import collections
import dataclasses
import hashlib
import inspect
import json
import os
import re
import subprocess
import sys
import time
import types
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import h5_tipauth_item4 as item4  # noqa: E402

TOOL = "h5_tipauth_decision_change"
SPEC = {"round1": "PR #614 quant-proof comment 6121641942 (section 4 item 5.5; section 5 rows 3, 4 (i))",
        "round3": "PR #614 quant-proof comment 6153106117 (Item 4 (i))",
        "pr638_round2": "reviewer comment 6153739735 (B1-B3); quant-proof comment 6153922480 (R1-R4, run rules)"}
BASE_MODNAME, HEAD_MODNAME = "_h5_exec_base", "_h5_exec_head"
WS_REFUSAL_DEFAULT = "bad_intent:base_breaks_unresolved_settled"  # the websocket refusal; the head's BASE_BREAKS_WS_REFUSAL is used when it has one
HEAD_DEFAULT = Path(__file__).resolve().with_name("h5_executor.py")  # this checkout's executor
GIT_SHA_RE = re.compile(r"[0-9a-f]{40}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
TRIGGER_PATTERN = re.compile(rb'"type"\s*:\s*"trigger"')
MAX_CLASSES = 20  # difference / other-change classes listed
MAX_TRANSITIONS = 500
HORIZON_MS = 24 * 14 * item4.HOUR_MS  # item 4's schedule() horizon: a longer window is not one of its reads
LABEL_CLEAN = re.compile(r"[^A-Za-z0-9_:.\-]")
EXIT_SCHEDULE = 8
VERDICT_KEYS = ("a_vs_b_default_zero_differences", "a_vs_b_tipauth_none_zero_differences", "passes_to_refusals_zero", "other_changes_zero",
                "would_pass_disagreements_zero", "item4_count_reconciles", "read_complete")

INTERPRETATIONS = [
    "I1 record selection: the item-4 tool's (origin = last start record in [since, until]; integer t_ms in [origin, until]; sealed or h5_look2_sealed "
    "anything but absent/null/False excluded and counted). Unlike item-4, a trigger line with a non-string pool, a variant other than pv/fv, a "
    "non-finite JSON constant (NaN, Infinity, an overflowing float) or a non-UTF-8 byte is NOT dropped: the executor's json.loads and tail_lines read it "
    "and parse_shadow_trigger sees it. `records.in_item4_selection` counts the lines item-4 keeps and must equal its records.triggers.",
    "I2 outcomes: PASS (a trigger came back), a refusal (the reason string), IGNORED ((None, None) or an empty reason: no trigger and no skip row), or "
    "raise:<ExceptionType> (the parser raised; intent_tick has no handler around it). Reasons are compared raw; the printed label is cleaned.",
    "I3 'trigger fields' are dataclasses.asdict of the returned trigger. A field present on one side only counts as equal iff it is None (the head's "
    "gate_path default); the one-sided names are listed.",
    "I4 refusals that become passes: A refused and C passes. Allowed only when the A reason is the websocket refusal and C's trigger has gate_path "
    "'tipauth'; anything else is also an 'other change'.",
    "I5 a websocket refusal that stays a refusal is not a decision change: a `tipauth:*` reason is the websocket refusal at intent_tick (the skip row keeps "
    "main's reason, the cause rides in tipauth_reason); any other reason is a later check of the parser, listed by reason.",
    "I6 passes that become refusals counts any pass that stops being a pass (a refusal, IGNORED or an exception). A refusal that becomes an exception is an "
    "other change.",
    "I7 the variant passed to the parsers is the head's TRIGGER_VARIANT ('pv'), as intent_tick passes self.trigger_variant.",
    "I8 would_pass disagreements, five kinds: (1) C passes via the gate but the item-4 predicate is False; (2) the predicate is True and the gate refused "
    "(`tipauth:*`); (3) the gate refused and the predicate is False with a different first reason (compared after the `tipauth:` prefix); (4) the "
    "installed build gives the websocket refusal, C is a refusal that is neither `tipauth:*` nor the websocket refusal (the gate admitted it) and the "
    "predicate is False; (5) the installed build gives the websocket refusal, the setting is an integer >= 1 and C is that same refusal (the path did not "
    "engage).",
    "I9 reconciliation (item-4 selection only): predicate-true = became-pass + refused by a later check (A websocket refusal, C a refusal that is neither "
    "tipauth:* nor the websocket refusal) + refused before the websocket gate in both builds (A and C the same non-pass outcome, not the websocket "
    "refusal) + remainder. item4_count_reconciles = remainder 0 and became-pass equals refusals_to_passes.total over every unsealed record.",
    "I10 read_complete: expected hour files are every hour from the hour of --since-ms to the hour of --until-ms inclusive; a newline-terminated line "
    "matching the trigger pattern must have parsed as a trigger record; an unterminated last line is read when it parses and, when it matches the pattern "
    "and does not parse, counted on its own (the executor acts only on terminated lines).",
]


class InputError(Exception):
    def __init__(self, msg: str, code: int):
        super().__init__(msg)
        self.msg, self.code = msg, code


# ---- loading the two executor files --------------------------------------------------------------------------------------------------------------

Loaded = collections.namedtuple("Loaded", "module sha256 source")


def load_executor(data: bytes, name: str, label: str) -> types.ModuleType:
    """Execute exactly these bytes as module `name` (registered in sys.modules first: dataclasses look their module up there)."""
    mod = types.ModuleType(name)
    mod.__file__ = label
    sys.modules[name] = mod
    try:
        exec(compile(data, label, "exec", dont_inherit=True), mod.__dict__)  # noqa: S102 - the operator names the executor files; no __future__ flags of this tool leak in
    except (Exception, SystemExit) as exc:
        sys.modules.pop(name, None)
        raise InputError(f"cannot load {label}: {type(exc).__name__}: {str(exc)[:200]}", 7) from exc
    return mod


def load_file(path: Path, name: str) -> Loaded:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise InputError(f"cannot read executor file {path}: {type(exc).__name__}", 7) from exc
    return Loaded(load_executor(data, name, str(path)), hashlib.sha256(data).hexdigest(), f"file:{path}")


def load_git(sha: str, name: str) -> Loaded:
    if not GIT_SHA_RE.fullmatch(sha):
        raise InputError("--base-git-sha must be 40 lowercase hex characters", 2)
    try:
        p = subprocess.run(["git", "-C", str(ROOT), "show", f"{sha}:tools/h5_executor.py"], capture_output=True, check=False, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        raise InputError(f"git show failed to run: {type(exc).__name__}", 7) from exc
    if p.returncode != 0:
        raise InputError(f"git object {sha}:tools/h5_executor.py is not available in this clone", 7)
    return Loaded(load_executor(p.stdout, name, f"git:{sha}:tools/h5_executor.py"), hashlib.sha256(p.stdout).hexdigest(), f"git:{sha}")


def has_param(fn: Callable, name: str) -> bool | None:
    try:
        return name in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return None


def check_shapes(base: Loaded, head: Loaded, expect_base: str | None, expect_head: str | None) -> None:
    for what, ld in (("base", base), ("head", head)):
        if not callable(getattr(ld.module, "parse_shadow_trigger", None)):
            raise InputError(f"{what} executor has no parse_shadow_trigger", 7)
    if has_param(head.module.parse_shadow_trigger, "tipauth") is None:
        raise InputError("head parse_shadow_trigger has no readable signature", 7)
    if not has_param(head.module.parse_shadow_trigger, "tipauth"):
        raise InputError("head parse_shadow_trigger has no `tipauth` parameter: it is not a build that carries the path", 7)
    if base.sha256 == head.sha256:
        raise InputError("base and head are the same bytes (same sha256): the installed build is the whole point of A", 7)
    if has_param(base.module.parse_shadow_trigger, "tipauth"):
        raise InputError("the base parse_shadow_trigger has a `tipauth` parameter: it already carries the path, so it is not the installed build", 7)
    if expect_base is not None and base.sha256 != expect_base:
        raise InputError("base sha256 does not match --expect-base-sha256", 7)
    if expect_head is not None and head.sha256 != expect_head:
        raise InputError("head sha256 does not match --expect-head-sha256", 7)


def head_availability(head: types.ModuleType, variant: str) -> tuple[str, str]:
    """(the `tipauth` argument for C, where it came from). The head's own _tipauth_availability on an all-clear stub; see the module docstring. No fallback."""
    cls = getattr(head, "H5Executor", None)
    if not (inspect.isclass(cls) and "_tipauth_availability" in vars(cls)):
        cls = next((o for o in vars(head).values() if inspect.isclass(o) and "_tipauth_availability" in vars(o)), None)
    if cls is None:
        raise InputError("the head has no _tipauth_availability to ask: the 'on' state cannot be verified (no fallback)", 7)
    stub = types.SimpleNamespace(exit_mode=getattr(head, "EXIT_MODE_V1", "v1"), trigger_variant=variant, counters=types.SimpleNamespace(tipauth={}), h5cfg={},
                                 _tipauth_check_budget=lambda: None, _tipauth_watch_check=lambda: "", _tipauth_at_risk=lambda: 0)
    try:
        ans = vars(cls)["_tipauth_availability"](stub)
    except Exception as exc:  # the stub does not model this head: stop, do not guess
        raise InputError(f"{cls.__name__}._tipauth_availability raised {type(exc).__name__} on the all-clear stub: the 'on' state cannot be verified (no fallback)", 7) from exc
    if ans != "":
        raise InputError(f"the head's {cls.__name__}._tipauth_availability answers {str(ans)[:60]!r} on an all-clear state, not the empty string", 7)
    return ans, (f"{cls.__name__}._tipauth_availability on a stub: exit_mode={stub.exit_mode}, trigger_variant={variant}, counters.tipauth={{}} (nothing latched, "
                 "no look pending), h5cfg={} (no look ack), watch check -> '', budget check -> no-op, at-risk -> 0")


# ---- one parse ---------------------------------------------------------------------------------------------------------------------------------

Outcome = collections.namedtuple("Outcome", "kind label fields raw")  # kind: pass | refuse | ignore | raise; fields: dict for a pass, else None; raw: uncleaned
_MISSING = object()


def clean(s: Any) -> str:
    return LABEL_CLEAN.sub("?", str(s))[:80]


def fields_of(trg: Any) -> dict:
    if dataclasses.is_dataclass(trg) and not isinstance(trg, type):
        return dataclasses.asdict(trg)
    if hasattr(trg, "__dict__"):
        return dict(vars(trg))
    return {"_repr": repr(trg)}


def evaluate(fn: Callable, line: str, variant: str, **kw: Any) -> Outcome:
    """One parser call on a FRESH json.loads of the line (as intent_tick does); never raises."""
    try:
        row = json.loads(line)
    except (ValueError, RecursionError):
        return Outcome("raise", "raise:JSONDecodeError", None, "raise:JSONDecodeError")
    try:
        res = fn(row, variant, **kw)
    except Exception as exc:  # RecursionError and OverflowError included
        lab = "raise:" + clean(type(exc).__name__)
        return Outcome("raise", lab, None, "raise:" + type(exc).__qualname__)
    if not (isinstance(res, tuple) and len(res) == 2):
        return Outcome("raise", "raise:BadReturnShape", None, "raise:BadReturnShape")
    trg, bad = res
    if trg is not None:
        try:
            return Outcome("pass", "PASS", fields_of(trg), "PASS")
        except Exception as exc:
            return Outcome("raise", "raise:" + clean(type(exc).__name__), None, "raise:" + type(exc).__qualname__)
    if not bad:
        return Outcome("ignore", "IGNORED", None, "IGNORED")
    if isinstance(bad, str):
        return Outcome("refuse", clean(bad), None, bad)
    lab = "non_str_reason:" + clean(type(bad).__name__)
    return Outcome("refuse", lab, None, lab)


def field_diff(fa: dict, fb: dict) -> list[str]:
    """Names of the trigger fields that differ. A field on one side only is equal iff it is None."""
    out = []
    for k in sorted(set(fa) | set(fb), key=str):
        va, vb = fa.get(k, _MISSING), fb.get(k, _MISSING)
        if va is _MISSING:
            ok = vb is None
        elif vb is _MISSING:
            ok = va is None
        else:
            ok = repr(va) == repr(vb)
        if not ok:
            out.append(str(k))
    return out


def same(a: Outcome, b: Outcome) -> bool:
    """Equal on the RAW reason (not the cleaned, cut label), and on the trigger fields for a pass."""
    return a.kind == b.kind and a.raw == b.raw and (a.kind != "pass" or not field_diff(a.fields, b.fields))


class Diff:
    """A against one other outcome, record by record."""

    def __init__(self) -> None:
        self.compared = 0
        self.differ = 0
        self.classes: collections.Counter = collections.Counter()
        self.field_names: collections.Counter = collections.Counter()
        self.reasons_b: collections.Counter = collections.Counter()
        self.both_raise: collections.Counter = collections.Counter()

    def add(self, a: Outcome, b: Outcome) -> None:
        self.compared += 1
        self.reasons_b[b.label] += 1
        if a.kind == "raise" and b.kind == "raise" and a.raw == b.raw:
            self.both_raise[a.label] += 1
        if same(a, b):
            return
        self.differ += 1
        if a.kind == "pass" and b.kind == "pass":
            self.classes[(a.label, b.label + ":fields_differ")] += 1
            self.field_names.update(field_diff(a.fields, b.fields))
        elif a.label == b.label:  # the same printed label, different raw reasons
            self.classes[(a.label, b.label + ":raw_reason_differs")] += 1
        else:
            self.classes[(a.label, b.label)] += 1

    def out(self) -> dict:
        ranked_ = sorted(self.classes.items(), key=lambda kv: (-kv[1], kv[0]))
        return {"records_compared": self.compared, "records_differ": self.differ,
                "difference_classes_total": len(ranked_), "difference_classes": [{"reason_a": k[0], "reason_b": k[1], "n": n} for k, n in ranked_[:MAX_CLASSES]],
                "difference_classes_truncated": len(ranked_) > MAX_CLASSES, "differing_trigger_field_names": dict(sorted(self.field_names.items())),
                "reasons_b": dict(sorted(self.reasons_b.items())), "both_raise_by_type": dict(sorted(self.both_raise.items()))}


def classify_ac(a: Outcome, c: Outcome, ws: str) -> str:
    if same(a, c):
        return "unchanged"
    a_ws = a.kind == "refuse" and a.raw == ws
    if a.kind == "pass":
        return "pass_to_nonpass" if c.kind != "pass" else "other"  # a pass that stays a pass with other fields is an other change
    if c.kind == "pass":  # a is not a pass here
        ok = a_ws and c.fields.get("gate_path") == "tipauth"
        return "refusal_to_pass_ok" if ok else "refusal_to_pass_other"
    if a_ws and c.kind == "refuse":
        return "ws_to_tipauth_refusal" if c.raw.startswith("tipauth:") else "ws_to_later_refusal"
    return "other"


def ranked(counter: collections.Counter, cap: int = MAX_CLASSES) -> list[dict]:
    items = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"a": k[0], "c": k[1], "n": n} for k, n in items[:cap]]


def by_reason(counter: collections.Counter) -> dict:
    return {"total": sum(counter.values()), "by_reason": dict(sorted(counter.items()))}


def expected_hour_names(since_ms: int, until_ms: int) -> set[str]:
    """Every hour file the window needs: the hour of --since-ms to the hour of --until-ms inclusive (hour_files() keeps a file whose hour starts <= until)."""
    return {"h5-shadow-" + time.strftime("%Y-%m-%dT%H", time.gmtime(h * item4.HOUR_MS // 1000)) + ".jsonl"
            for h in range(since_ms // item4.HOUR_MS, until_ms // item4.HOUR_MS + 1)}


# ---- the scan ----------------------------------------------------------------------------------------------------------------------------------

def find_origin(snap: list, since_ms: int, until_ms: int) -> tuple[list[dict], dict | None]:
    """item-4 analyze(): the starts at or after T0 (and <= until); the origin is the LAST one."""
    starts: list[dict] = []
    for rec in item4.iter_records(snap, collections.Counter(), None):
        if rec.get("type") == "start":
            t = rec.get("t_ms")
            if item4.isint(t) and t >= since_ms and t <= until_ms:
                starts.append({"t_ms": t, "code_sha": rec.get("code_sha")})
    starts.sort(key=lambda s: (s["t_ms"], str(s["code_sha"])))
    return starts, (starts[-1] if starts else None)


def analyze(shadow_dir: Path, since_ms: int, until_ms: int, expect_code_sha: str, base: Loaded, head: Loaded) -> dict:
    variant = getattr(head.module, "TRIGGER_VARIANT", "pv")
    if not isinstance(variant, str):
        variant = "pv"
    avail, avail_source = head_availability(head.module, variant)
    ws_label = getattr(head.module, "BASE_BREAKS_WS_REFUSAL", WS_REFUSAL_DEFAULT)
    base_parse, head_parse = base.module.parse_shadow_trigger, head.module.parse_shadow_trigger

    files = item4.hour_files(shadow_dir, since_ms, until_ms)
    snap = item4.snapshot(files)
    starts, origin = find_origin(snap, since_ms, until_ms)
    if origin is None:
        raise InputError("no start record at or after --since-ms", 4)
    if origin["code_sha"] != expect_code_sha:
        raise InputError("the origin start record's code_sha does not match --expect-code-sha", 5)
    t0 = origin["t_ms"]

    c: collections.Counter = collections.Counter()
    ab_default, ab_none, transitions = Diff(), Diff(), collections.Counter()
    reasons_a: collections.Counter = collections.Counter()
    cat: collections.Counter = collections.Counter()
    ref_to_pass_by_a: collections.Counter = collections.Counter()
    pass_to_non_by_c: collections.Counter = collections.Counter()
    ws_tipauth_by_c: collections.Counter = collections.Counter()
    ws_later_by_c: collections.Counter = collections.Counter()
    other_classes: collections.Counter = collections.Counter()
    wp_by_c: collections.Counter = collections.Counter()
    rc_later: collections.Counter = collections.Counter()
    rc_before: collections.Counter = collections.Counter()
    rc_outside: collections.Counter = collections.Counter()
    by_variant: collections.Counter = collections.Counter()
    base_names: set = set()
    head_names: set = set()

    for f, size in snap:
        try:
            with open(f, "rb") as fh:
                data = fh.read(size)
        except OSError:
            c["unreadable_file"] += 1
            continue
        if data and not data.endswith(b"\n"):
            c["files_last_line_without_newline"] += 1
        parts = data.split(b"\n")
        last = len(parts) - 1
        for idx, raw in enumerate(parts):
            raw = raw.strip()
            if not raw:
                continue
            term = idx < last  # the executor acts only on newline-terminated lines
            c["lines"] += 1
            matches = TRIGGER_PATTERN.search(raw) is not None
            if matches:
                c["trigger_pattern_matches" if term else "unterminated_trigger_pattern_matches"] += 1
            utf8 = True
            try:
                line = raw.decode("utf-8")
            except UnicodeDecodeError:
                utf8 = False
                c["bad_line_not_utf8"] += 1
                line = raw.decode("utf-8", "replace")  # as the executor's tail_lines does
            strict = utf8
            try:
                rec = json.loads(line, parse_constant=item4._bad_const, parse_float=item4._parse_float) if utf8 else None
            except (ValueError, RecursionError):
                rec, strict = None, False
            if rec is None:
                strict = False
                try:
                    rec = json.loads(line)  # what the executor does
                except (ValueError, RecursionError):
                    rec = None
            if not isinstance(rec, dict):
                c["bad_line"] += 1
                if matches and not term:
                    c["unterminated_last_line_trigger_unparsed"] += 1
                continue
            if rec.get("type") != "trigger":
                continue
            c["trigger_records_parsed"] += 1
            if term:
                c["trigger_records_parsed_terminated"] += 1
            t = rec.get("t_ms")
            if not item4.isint(t):
                c["trigger_records_without_int_t_ms"] += 1
                continue
            if t < t0 or t > until_ms:
                c["trigger_records_outside_window"] += 1
                continue
            c["trigger_records_in_window"] += 1
            sl, h5 = item4.flag(rec.get("sealed")), item4.flag(rec.get("h5_look2_sealed"))
            if sl or h5:  # excluded: nothing else of this record is read
                c["sealed_excluded_sealed" if sl else "sealed_excluded_h5_look2_sealed_only"] += 1
                if sl and h5:
                    c["sealed_excluded_both_flags"] += 1
                continue
            v = rec.get("variant")
            by_variant[v if v in ("pv", "fv") else "other"] += 1
            pool_ok = isinstance(rec.get("pool"), str)
            if not pool_ok:
                c["unsealed_pool_not_str"] += 1
            if rec.get("suppressed"):
                c["unsealed_suppressed_stubs"] += 1
            if not utf8:
                c["unsealed_not_utf8"] += 1
            if not strict:
                c["unsealed_not_strict_json"] += 1
            in_sel = strict and pool_ok and v in ("pv", "fv")
            if in_sel:
                c["in_item4_selection"] += 1
            ws_val = rec.get("base_breaks_unresolved_settled")
            ws_int = isinstance(ws_val, int) and not isinstance(ws_val, bool) and ws_val >= 1
            if ws_int:
                c["ws_integer_ge_1"] += 1

            a = evaluate(base_parse, line, variant)
            b_def = evaluate(head_parse, line, variant)
            b_none = evaluate(head_parse, line, variant, tipauth=None)
            cc = evaluate(head_parse, line, variant, tipauth=avail)
            ab_default.add(a, b_def)
            ab_none.add(a, b_none)
            reasons_a[a.label] += 1
            transitions[(a.label, cc.label)] += 1
            if a.kind == "pass":
                c["a_passes"] += 1
                base_names.update(a.fields)
            if cc.kind == "pass":
                c["c_passes"] += 1
                head_names.update(cc.fields)
            a_ws_ref = a.kind == "refuse" and a.raw == ws_label
            if a_ws_ref:
                c["a_ws_refusals"] += 1

            k = classify_ac(a, cc, ws_label)
            cat[k] += 1
            if a.kind == "refuse" and cc.kind == "pass":
                ref_to_pass_by_a[a.label] += 1
                if cc.fields.get("gate_path") == "tipauth":
                    c["refusals_to_passes_gate_path_tipauth"] += 1
            if k == "pass_to_nonpass":
                pass_to_non_by_c[cc.label] += 1
            elif k == "ws_to_tipauth_refusal":
                ws_tipauth_by_c[cc.label] += 1
            elif k == "ws_to_later_refusal":
                ws_later_by_c[cc.label] += 1
            elif k in ("other", "refusal_to_pass_other"):
                other_classes[(a.label, cc.label + (":fields_differ" if a.kind == cc.kind == "pass" else ""))] += 1
            if a_ws_ref and cc.kind == "raise":
                c["ws_refusals_that_become_exceptions"] += 1

            # the item-4 predicate on a fresh parse of the same line
            try:
                wp, wp_reason = item4.would_pass(json.loads(line))
            except Exception:  # would_pass never raises; a failure here is counted, not hidden
                wp, wp_reason = False, "predicate_raised"
                c["would_pass_predicate_raised"] += 1
            if wp:
                c["would_pass_true"] += 1
                wp_by_c[cc.label] += 1
            if a.kind == "refuse" and cc.kind == "pass":
                if wp:
                    c["became_pass_item4_true"] += 1
                else:
                    c["dis_became_pass_item4_false"] += 1  # kind 1
            if a_ws_ref and cc.kind == "refuse":
                if cc.raw.startswith("tipauth:"):
                    if wp:
                        c["dis_item4_true_gate_refused"] += 1  # kind 2
                    elif wp_reason != cc.raw[len("tipauth:"):]:
                        c["dis_gate_reason_differs"] += 1  # kind 3
                elif cc.raw == ws_label:
                    if ws_int:
                        c["dis_path_not_engaged"] += 1  # kind 5: an integer setting >= 1 cannot stay the websocket refusal with the path on
                elif not wp:
                    c["dis_gate_admitted_later_refused"] += 1  # kind 4: the gate let it through, the predicate says it should not have
            # the reconciliation with 5.1 (item-4 selection only)
            if wp and in_sel:
                c["rc_predicate_true"] += 1
                if a.kind == "refuse" and cc.kind == "pass":
                    c["rc_became_pass"] += 1
                elif a_ws_ref and cc.kind == "refuse" and not cc.raw.startswith("tipauth:") and cc.raw != ws_label:
                    rc_later[cc.label] += 1
                elif same(a, cc) and a.kind != "pass" and not a_ws_ref:
                    rc_before[a.label] += 1
            elif wp:
                c["rc_outside_predicate_true"] += 1
                rc_outside[cc.label] += 1

    n = cat.total()
    dis = (c["dis_became_pass_item4_false"] + c["dis_item4_true_gate_refused"] + c["dis_gate_reason_differs"] + c["dis_gate_admitted_later_refused"]
           + c["dis_path_not_engaged"])
    ref_total = sum(ref_to_pass_by_a.values())
    other_total = cat["other"] + cat["refusal_to_pass_other"]
    ordered = sorted(transitions.items(), key=lambda kv: (-kv[1], kv[0]))
    only_head, only_base = sorted(head_names - base_names), sorted(base_names - head_names)
    rc_remainder = c["rc_predicate_true"] - c["rc_became_pass"] - sum(rc_later.values()) - sum(rc_before.values())
    reconciles = rc_remainder == 0 and c["rc_became_pass"] == ref_total
    expected = expected_hour_names(since_ms, until_ms)
    snap_names = {p.name for p, _ in snap}
    missing = len(expected - snap_names)
    dropped = len(files) - len(snap)
    pattern_equal = c["trigger_pattern_matches"] == c["trigger_records_parsed_terminated"]
    read_complete = (c["unreadable_file"] == 0 and dropped == 0 and missing == 0 and pattern_equal and c["trigger_records_without_int_t_ms"] == 0 and n >= 1)
    res: dict = {
        "tool": TOOL, "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "spec": SPEC,
        "inputs": {"since_ms": since_ms, "until_ms": until_ms, "expect_code_sha": expect_code_sha, "variant": variant},
        "base": {"source": base.source, "sha256": base.sha256, "parse_has_tipauth_parameter": has_param(base_parse, "tipauth")},
        "head": {"source": head.source, "sha256": head.sha256, "trigger_fields_only_in_head": only_head, "trigger_fields_only_in_base": only_base},
        "availability": {"tipauth_argument_for_C": avail, "source": avail_source},
        "window": {"n_starts_in_range": len(starts), "origin_t_ms": t0, "origin_code_sha": origin["code_sha"], "origin_code_sha_matches": True},
        "records": {"trigger_records_in_window": c["trigger_records_in_window"],
                    "sealed_excluded": {"sealed": c["sealed_excluded_sealed"], "h5_look2_sealed_only": c["sealed_excluded_h5_look2_sealed_only"],
                                        "both_flags": c["sealed_excluded_both_flags"],
                                        "total": c["sealed_excluded_sealed"] + c["sealed_excluded_h5_look2_sealed_only"]},
                    "unsealed_read": n, "by_variant": {k: by_variant[k] for k in ("pv", "fv", "other")}, "suppressed_stubs": c["unsealed_suppressed_stubs"],
                    "pool_not_str": c["unsealed_pool_not_str"], "not_strict_json": c["unsealed_not_strict_json"], "not_utf8": c["unsealed_not_utf8"],
                    "ws_integer_ge_1": c["ws_integer_ge_1"], "in_item4_selection": c["in_item4_selection"]},
        "scan": {"files": [p.name for p, _ in snap], "file_sizes": {p.name: s for p, s in snap}, "lines": c["lines"], "bad_line": c["bad_line"],
                 "bad_line_not_utf8": c["bad_line_not_utf8"], "unreadable_file": c["unreadable_file"],
                 "expected_hour_files": len(expected), "missing_hour_files": missing, "snapshot_dropped_files": dropped,
                 "files_last_line_without_newline": c["files_last_line_without_newline"],
                 "trigger_pattern_matches": c["trigger_pattern_matches"], "trigger_records_parsed": c["trigger_records_parsed"],
                 "trigger_records_parsed_terminated": c["trigger_records_parsed_terminated"],
                 "trigger_pattern_matches_equal_trigger_records_parsed": pattern_equal,
                 "unterminated_last_line_trigger_unparsed": c["unterminated_last_line_trigger_unparsed"],
                 "trigger_records_without_int_t_ms": c["trigger_records_without_int_t_ms"], "trigger_records_outside_window": c["trigger_records_outside_window"]},
        "a_vs_b": {"reasons_a": dict(sorted(reasons_a.items())), "head_path_off_default_call": ab_default.out(), "head_path_off_tipauth_none": ab_none.out()},
        "a_vs_c": {"records": n, "a_passes": c["a_passes"], "c_passes": c["c_passes"], "a_ws_refusals": c["a_ws_refusals"], "unchanged": cat["unchanged"],
                   "refusals_to_passes": {"total": ref_total, "by_a_reason": dict(sorted(ref_to_pass_by_a.items())),
                                          "gate_path_tipauth": c["refusals_to_passes_gate_path_tipauth"]},
                   "ws_refusals_still_refused": {"by_tipauth_reason_decision_unchanged": dict(sorted(ws_tipauth_by_c.items())),
                                                 "by_later_check_reason": dict(sorted(ws_later_by_c.items())),
                                                 "total": sum(ws_tipauth_by_c.values()) + sum(ws_later_by_c.values())},
                   "ws_refusals_that_become_exceptions": c["ws_refusals_that_become_exceptions"],
                   "passes_to_refusals": {"total": cat["pass_to_nonpass"], "by_c_reason": dict(sorted(pass_to_non_by_c.items()))},
                   "other_changes": {"total": other_total, "classes_total": len(other_classes), "classes": ranked(other_classes),
                                     "classes_truncated": len(other_classes) > MAX_CLASSES},
                   "transitions_total_rows": len(ordered), "transitions": [{"a": k[0], "c": k[1], "n": v} for k, v in ordered[:MAX_TRANSITIONS]]},
        "would_pass": {"item4_predicate_true": c["would_pass_true"], "item4_predicate_true_in_item4_selection": c["rc_predicate_true"],
                       "item4_predicate_true_by_c_outcome_all_unsealed": dict(sorted(wp_by_c.items())), "refusals_to_passes": ref_total,
                       "refusals_to_passes_item4_true": c["became_pass_item4_true"],
                       "disagreements": {"became_pass_item4_false": c["dis_became_pass_item4_false"], "item4_true_gate_refused": c["dis_item4_true_gate_refused"],
                                         "gate_reason_differs": c["dis_gate_reason_differs"],
                                         "gate_admitted_item4_false_later_refused": c["dis_gate_admitted_later_refused"],
                                         "path_not_engaged": c["dis_path_not_engaged"], "total": dis},
                       "predicate_raised": c["would_pass_predicate_raised"]},
        "reconciliation": {"scope": "records in the item-4 selection only", "item4_predicate_true": c["rc_predicate_true"], "became_pass": c["rc_became_pass"],
                           "refused_by_later_check": by_reason(rc_later), "refused_before_websocket_gate_unchanged": by_reason(rc_before),
                           "remainder": rc_remainder, "refusals_to_passes_all_unsealed": ref_total,
                           "outside_selection": {"item4_predicate_true": c["rc_outside_predicate_true"], "by_c_outcome": dict(sorted(rc_outside.items()))}},
    }
    res["verdict"] = {"a_vs_b_default_zero_differences": ab_default.differ == 0, "a_vs_b_tipauth_none_zero_differences": ab_none.differ == 0,
                      "passes_to_refusals_zero": cat["pass_to_nonpass"] == 0, "other_changes_zero": other_total == 0,
                      "would_pass_disagreements_zero": dis == 0 and c["would_pass_predicate_raised"] == 0,
                      "item4_count_reconciles": reconciles, "read_complete": read_complete}
    assert tuple(res["verdict"]) == VERDICT_KEYS
    return res


# ---- CLI ---------------------------------------------------------------------------------------------------------------------------------------

def dumps(res: dict) -> str:
    return json.dumps(res, sort_keys=True, indent=1, allow_nan=False) + "\n"


def error_result(err: InputError) -> dict:
    return {"tool": TOOL, "error": err.msg, "exit_code": err.code}


def main(argv: list[str] | None = None, now_ms: int | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], epilog="EXIT CODE: 0 means only that the tool ran. Read `verdict` in the JSON (seven booleans, all must be true). "
                                 "2 usage, 3 shadow dir missing, 4 no start record, 5 --expect-code-sha mismatch, 6 --out exists or its directory is missing, "
                                 "7 executor file problem, 8 --until-ms off the item-4 schedule or the run is too early.")
    ap.add_argument("--shadow-dir", required=True)
    ap.add_argument("--since-ms", type=int, required=True)
    ap.add_argument("--until-ms", type=int, required=True, help="an item-4 scheduled instant (item4.on_schedule); the run must start at least item4.SETTLE_MS later")
    ap.add_argument("--expect-code-sha", required=True, help="40 hex: the origin (last) start record's code_sha must equal it")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--base-file", help="the INSTALLED build's tools/h5_executor.py")
    g.add_argument("--base-git-sha", help="40 hex; loaded from `git show SHA:tools/h5_executor.py` (the installed sha is b7ddeb60aa4ced9243211775cf3d5a22ccc42dff)")
    ap.add_argument("--head-file", default=str(HEAD_DEFAULT), help="default: this checkout's tools/h5_executor.py")
    ap.add_argument("--expect-base-sha256", default=None, help="64 hex; exit 7 if the base file's sha256 differs")
    ap.add_argument("--expect-head-sha256", default=None, help="64 hex; exit 7 if the head file's sha256 differs")
    ap.add_argument("--out", default=None, help="JSON file, created exclusively; an existing file is never overwritten (exit 6)")
    a = ap.parse_args(argv)
    if not re.fullmatch(r"[0-9a-f]{40}", a.expect_code_sha):
        ap.error("--expect-code-sha must be 40 lowercase hex characters")
    if a.base_git_sha is not None and not GIT_SHA_RE.fullmatch(a.base_git_sha):
        ap.error("--base-git-sha must be 40 lowercase hex characters")
    for flag_name, val in (("--expect-base-sha256", a.expect_base_sha256), ("--expect-head-sha256", a.expect_head_sha256)):
        if val is not None and not SHA256_RE.fullmatch(val):
            ap.error(f"{flag_name} must be 64 lowercase hex characters")
    if a.until_ms < a.since_ms:
        ap.error("--until-ms is before --since-ms")
    now = int(time.time() * 1000) if now_ms is None else now_ms
    try:
        # R1: item 4's peeking rule. The schedule and the settle time are item 4's own, imported.
        if not item4.on_schedule(a.since_ms, a.until_ms) or a.until_ms - a.since_ms > HORIZON_MS:
            raise InputError(f"--until-ms {a.until_ms} is not an item-4 scheduled instant for --since-ms {a.since_ms} (+6 h, +24 h, +36 h, +48 h, then every 12 h)", EXIT_SCHEDULE)
        if now < a.until_ms + item4.SETTLE_MS:
            raise InputError(f"the run starts at {now}, earlier than --until-ms plus {item4.SETTLE_MS} ms (item 4's settle time)", EXIT_SCHEDULE)
        if a.out:
            if os.path.lexists(a.out):
                raise InputError(f"--out {a.out} exists; it is never overwritten", 6)
            if not Path(a.out).resolve().parent.is_dir():
                raise InputError(f"the directory of --out {a.out} does not exist", 6)
        d = Path(a.shadow_dir)
        if not d.is_dir():
            raise InputError("shadow directory missing", 3)
        base = load_file(Path(a.base_file), BASE_MODNAME) if a.base_file else load_git(a.base_git_sha, BASE_MODNAME)
        head = load_file(Path(a.head_file), HEAD_MODNAME)
        check_shapes(base, head, a.expect_base_sha256, a.expect_head_sha256)
        res = analyze(d, a.since_ms, a.until_ms, a.expect_code_sha, base, head)
    except InputError as err:
        sys.stdout.write(dumps(error_result(err)))
        return err.code
    text = dumps(res)
    sys.stdout.write(text)
    if a.out:
        try:
            with open(a.out, "x", encoding="utf-8") as fh:  # exclusive: never overwrites
                fh.write(text)
        except FileExistsError:
            sys.stdout.write(dumps(error_result(InputError(f"--out {a.out} appeared during the run; not overwritten", 6))))
            return 6
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
