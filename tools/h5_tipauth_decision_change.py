#!/usr/bin/env python3
"""H5 tipauth path: the parser differential and the decision-change account, COUNTS ONLY (quant-proof, PR #614: round 1 comment 6121641942,
section 4 item 5.5 and section 5 rows 3 and 4 (i); round 3 comment 6153106117, "Item 4 (i)").

For every unsealed `type: trigger` record of the counted window of the H5 shadow's hourly files it computes three results from the record alone
(no executor state, no network, no ledger, no tip tape):
  A  the BASE parser    (the installed build's tools/h5_executor.py):  parse_shadow_trigger(row, variant)
  B  the HEAD parser, path OFF, twice:  parse_shadow_trigger(row, variant)  and  parse_shadow_trigger(row, variant, tipauth=None)
  C  the HEAD parser, path ON and available:  parse_shadow_trigger(row, variant, tipauth=<availability>)
and prints only counts: A against B (required 0 differences in (pass or refuse, reason, trigger fields)), A against C as a transition table, the
refusals that become passes (split by the A reason), the passes that become refusals (required 0), any other change (required 0), and the
agreement of the executor with the item-4 tool's would_pass predicate (required 0 disagreements).

  python3 tools/h5_tipauth_decision_change.py --shadow-dir DIR --since-ms T0 --until-ms T1 --expect-code-sha SHA40 \\
      (--base-file PATH | --base-git-sha SHA40) [--head-file PATH] --out FILE

HOW (C) PUTS THE PATH "ON" THROUGH THE HEAD'S OWN API. parse_shadow_trigger takes `tipauth`: None = path off; "" = path on and available; any other
string = on but unavailable (that string is the refusal). The caller (H5Executor.intent_tick) takes that string from H5Executor._tipauth_availability().
This tool does not guess "": it calls the head's own H5Executor._tipauth_availability (the unbound function) on a stub `self` that says "nothing
latched, no look pending, watch ok, nothing at risk, rule v1, variant pv" (exit_mode, trigger_variant, counters.tipauth = {}, h5cfg = {}, and
_tipauth_check_budget / _tipauth_watch_check / _tipauth_at_risk replaced by their all-clear answers) and passes the string it returns. If that
string is not "", or the head has no such function, the output says which (`availability.source`) and a non-empty answer is an input error.
The gate itself (tipauth_gate) is never re-implemented here: C is the head's own parse.

WHAT IS REUSED FROM tools/h5_tipauth_item4.py (imported, not copied): hour_files, snapshot (the file list and sizes, taken once, read only up to those
sizes), iter_records (the start-record pass), isint, flag (the sealed rule: anything but absent, null or the literal False is sealed), would_pass (the
one predicate), and the strict-JSON hooks _bad_const / _parse_float. WHAT IS MIRRORED (analyze() has no callable for it): the origin = the LAST start
record with since <= t_ms <= until; a record belongs to the window if its integer t_ms is >= origin and <= until; sealed (`sealed` or
`h5_look2_sealed`) records are excluded and counted, never parsed, and for them only `type`, `t_ms` and the two flags are read; a record whose pool is
not a string is counted. INTERPRETATIONS (also in the PR body): see INTERPRETATIONS below.

EXIT CODE: 0 means only that the tool ran, whatever the counts. Read `verdict` in the JSON. Non-zero is an input error and prints a JSON error with no
counts: 2 usage (argparse), 3 shadow directory missing, 4 no start record at or after --since-ms, 5 --expect-code-sha does not match the origin start
record's code_sha, 6 --out exists (it is never overwritten), 7 an executor file cannot be loaded or has the wrong shape.
The two executor files are executed as modules (they import `tools.*` and `solders` from the running checkout's environment, so run this where the
executor's tests run). Nothing outside --shadow-dir and the two executor files is opened.
"""
from __future__ import annotations

import argparse
import collections
import dataclasses
import hashlib
import importlib.util
import inspect
import json
import os
import re
import subprocess
import sys
import types
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import h5_tipauth_item4 as item4  # noqa: E402

TOOL = "h5_tipauth_decision_change"
SPEC = {"round1": "PR #614 quant-proof comment 6121641942 (section 4 item 5.5; section 5 rows 3, 4 (i))",
        "round3": "PR #614 quant-proof comment 6153106117 (Item 4 (i))"}
BASE_MODNAME, HEAD_MODNAME = "_h5_exec_base", "_h5_exec_head"
WS_REFUSAL_DEFAULT = "bad_intent:base_breaks_unresolved_settled"  # the websocket refusal; the head's BASE_BREAKS_WS_REFUSAL is used when it has one
HEAD_DEFAULT = Path(__file__).resolve().with_name("h5_executor.py")  # this checkout's executor
GIT_SHA_RE = re.compile(r"[0-9a-f]{40}")
TRIGGER_PATTERN = re.compile(rb'"type"\s*:\s*"trigger"')
MAX_CLASSES = 20  # difference / other-change classes listed
MAX_TRANSITIONS = 500
LABEL_CLEAN = re.compile(r"[^A-Za-z0-9_:.\-]")
VERDICT_KEYS = ("a_vs_b_default_zero_differences", "a_vs_b_tipauth_none_zero_differences", "passes_to_refusals_zero", "other_changes_zero",
                "would_pass_disagreements_zero")

INTERPRETATIONS = [
    "I1 record selection: the item-4 tool's (origin = last start record in [since, until]; integer t_ms in [origin, until]; sealed or h5_look2_sealed "
    "anything but absent/null/False excluded and counted). Unlike item-4, a trigger line with a non-string pool, a variant other than pv/fv, or a "
    "non-finite JSON constant (NaN, Infinity, an overflowing float) is NOT dropped: the executor's json.loads parses it and parse_shadow_trigger sees it. "
    "`records.in_item4_selection` counts the lines item-4 keeps and must equal item-4's records.triggers.",
    "I2 outcomes: PASS (a trigger came back), a refusal (the reason string), IGNORED ((None, None) or an empty reason: no trigger and no skip row), or "
    "raise:<ExceptionType> (the parser raised; intent_tick has no handler around it).",
    "I3 'trigger fields' are dataclasses.asdict of the returned trigger. A field present on one side only counts as equal iff it is None (the head's "
    "gate_path default); the one-sided names are listed.",
    "I4 refusals that become passes: A refused and C passes. Allowed only when the A reason is the websocket refusal and C's trigger has gate_path "
    "'tipauth'; anything else is also an 'other change'.",
    "I5 a websocket refusal that stays a refusal is not a decision change: a `tipauth:*` reason is the websocket refusal at intent_tick (the skip row keeps "
    "main's reason, the cause rides in tipauth_reason); any other reason is a later check of the parser, listed by reason.",
    "I6 passes that become refusals counts any pass that stops being a pass (a refusal, IGNORED or an exception). A refusal that becomes an exception is an "
    "other change.",
    "I7 the variant passed to the parsers is the head's TRIGGER_VARIANT ('pv'), as intent_tick passes self.trigger_variant.",
    "I8 would_pass disagreement: (1) C passes via the gate but the item-4 predicate is False; (2) the item-4 predicate is True but the gate refused; "
    "(3) the gate refused and the item-4 predicate is False with a different first reason. Reasons compare after the `tipauth:` prefix.",
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


def check_shapes(base: Loaded, head: Loaded) -> None:
    for what, ld in (("base", base), ("head", head)):
        fn = getattr(ld.module, "parse_shadow_trigger", None)
        if not callable(fn):
            raise InputError(f"{what} executor has no parse_shadow_trigger", 7)
    if has_param(head.module.parse_shadow_trigger, "tipauth") is None:
        raise InputError("head parse_shadow_trigger has no readable signature", 7)
    if not has_param(head.module.parse_shadow_trigger, "tipauth"):
        raise InputError("head parse_shadow_trigger has no `tipauth` parameter: it is not a build that carries the path", 7)


def head_availability(head: types.ModuleType, variant: str) -> tuple[str, str]:
    """(the `tipauth` argument for C, where it came from). The head's own _tipauth_availability on an all-clear stub; see the module docstring."""
    cls = getattr(head, "H5Executor", None)
    if not (inspect.isclass(cls) and "_tipauth_availability" in vars(cls)):
        cls = next((o for o in vars(head).values() if inspect.isclass(o) and "_tipauth_availability" in vars(o)), None)
    if cls is None:
        return "", "literal empty string (parse_shadow_trigger docstring: '' = on and available); the head has no _tipauth_availability to ask"
    stub = types.SimpleNamespace(exit_mode=getattr(head, "EXIT_MODE_V1", "v1"), trigger_variant=variant, counters=types.SimpleNamespace(tipauth={}), h5cfg={},
                                 _tipauth_check_budget=lambda: None, _tipauth_watch_check=lambda: "", _tipauth_at_risk=lambda: 0)
    try:
        ans = vars(cls)["_tipauth_availability"](stub)
    except Exception as exc:  # the stub does not model this head: say so, do not guess silently
        return "", f"literal empty string; {cls.__name__}._tipauth_availability raised {type(exc).__name__} on the all-clear stub"
    if ans != "":
        raise InputError(f"the head's {cls.__name__}._tipauth_availability answers {str(ans)[:60]!r} on an all-clear state, not the empty string", 7)
    return ans, (f"{cls.__name__}._tipauth_availability on a stub: exit_mode={stub.exit_mode}, trigger_variant={variant}, counters.tipauth={{}} (nothing latched, "
                 "no look pending), h5cfg={} (no look ack), watch check -> '', budget check -> no-op, at-risk -> 0")


# ---- one parse ---------------------------------------------------------------------------------------------------------------------------------

Outcome = collections.namedtuple("Outcome", "kind label fields")  # kind: pass | refuse | ignore | raise; fields: dict for a pass, else None
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
        return Outcome("raise", "raise:JSONDecodeError", None)
    try:
        res = fn(row, variant, **kw)
    except Exception as exc:  # RecursionError and OverflowError included
        return Outcome("raise", "raise:" + clean(type(exc).__name__), None)
    if not (isinstance(res, tuple) and len(res) == 2):
        return Outcome("raise", "raise:BadReturnShape", None)
    trg, bad = res
    if trg is not None:
        try:
            return Outcome("pass", "PASS", fields_of(trg))
        except Exception as exc:
            return Outcome("raise", "raise:" + clean(type(exc).__name__), None)
    if not bad:
        return Outcome("ignore", "IGNORED", None)
    return Outcome("refuse", clean(bad) if isinstance(bad, str) else "non_str_reason:" + clean(type(bad).__name__), None)


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
    return a.kind == b.kind and a.label == b.label and (a.kind != "pass" or not field_diff(a.fields, b.fields))


class Diff:
    """A against one other outcome, record by record."""

    def __init__(self) -> None:
        self.compared = 0
        self.differ = 0
        self.classes: collections.Counter = collections.Counter()
        self.field_names: collections.Counter = collections.Counter()

    def add(self, a: Outcome, b: Outcome) -> None:
        self.compared += 1
        if same(a, b):
            return
        self.differ += 1
        if a.kind == "pass" and b.kind == "pass" and a.label == b.label:
            self.classes[(a.label, b.label + ":fields_differ")] += 1
            self.field_names.update(field_diff(a.fields, b.fields))
        else:
            self.classes[(a.label, b.label)] += 1

    def out(self) -> dict:
        ranked = sorted(self.classes.items(), key=lambda kv: (-kv[1], kv[0]))
        return {"records_compared": self.compared, "records_differ": self.differ,
                "difference_classes_total": len(ranked), "difference_classes": [{"reason_a": k[0], "reason_b": k[1], "n": n} for k, n in ranked[:MAX_CLASSES]],
                "difference_classes_truncated": len(ranked) > MAX_CLASSES, "differing_trigger_field_names": dict(sorted(self.field_names.items()))}


def classify_ac(a: Outcome, c: Outcome, ws: str) -> str:
    if same(a, c):
        return "unchanged"
    a_ws = a.kind == "refuse" and a.label == ws
    if a.kind == "pass":
        return "pass_to_nonpass" if c.kind != "pass" else "other"  # a pass that stays a pass with other fields is an other change
    if c.kind == "pass":  # a is not a pass here
        ok = a_ws and c.fields.get("gate_path") == "tipauth"
        return "refusal_to_pass_ok" if ok else "refusal_to_pass_other"
    if a_ws and c.kind == "refuse":
        return "ws_to_tipauth_refusal" if c.label.startswith("tipauth:") else "ws_to_later_refusal"
    return "other"


def ranked(counter: collections.Counter, cap: int = MAX_CLASSES) -> list[dict]:
    items = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"a": k[0], "c": k[1], "n": n} for k, n in items[:cap]]


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


def analyze(shadow_dir: Path, since_ms: int, until_ms: int, expect_code_sha: str, base: Loaded, head: Loaded, out_path: str | None = None) -> dict:
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
    cat: collections.Counter = collections.Counter()
    ref_to_pass_by_a: collections.Counter = collections.Counter()
    pass_to_non_by_c: collections.Counter = collections.Counter()
    ws_tipauth_by_c: collections.Counter = collections.Counter()
    ws_later_by_c: collections.Counter = collections.Counter()
    other_classes: collections.Counter = collections.Counter()
    wp_by_c: collections.Counter = collections.Counter()
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
        for raw in data.split(b"\n"):
            raw = raw.strip()
            if not raw:
                continue
            c["lines"] += 1
            if TRIGGER_PATTERN.search(raw):
                c["trigger_pattern_matches"] += 1
            try:
                line = raw.decode("utf-8")
            except UnicodeDecodeError:
                c["bad_line"] += 1
                c["bad_line_not_utf8"] += 1
                continue
            strict = True
            try:
                rec = json.loads(line, parse_constant=item4._bad_const, parse_float=item4._parse_float)
            except (ValueError, RecursionError):
                strict = False
                try:
                    rec = json.loads(line)  # what the executor does
                except (ValueError, RecursionError):
                    c["bad_line"] += 1
                    continue
            if not isinstance(rec, dict):
                c["bad_line"] += 1
                continue
            if rec.get("type") != "trigger":
                continue
            c["trigger_records_parsed"] += 1
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
            if not strict:
                c["unsealed_not_strict_json"] += 1
            in_sel = strict and pool_ok and v in ("pv", "fv")
            if in_sel:
                c["in_item4_selection"] += 1

            a = evaluate(base_parse, line, variant)
            b_def = evaluate(head_parse, line, variant)
            b_none = evaluate(head_parse, line, variant, tipauth=None)
            cc = evaluate(head_parse, line, variant, tipauth=avail)
            ab_default.add(a, b_def)
            ab_none.add(a, b_none)
            transitions[(a.label, cc.label)] += 1
            if a.kind == "pass":
                c["a_passes"] += 1
                base_names.update(a.fields)
            if cc.kind == "pass":
                c["c_passes"] += 1
                head_names.update(cc.fields)
            if a.kind == "refuse" and a.label == ws_label:
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
            if a.kind == "refuse" and a.label == ws_label and cc.kind == "raise":
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
                if in_sel:
                    c["would_pass_true_in_item4_selection"] += 1
            if a.kind == "refuse" and cc.kind == "pass":
                if wp:
                    c["became_pass_item4_true"] += 1
                else:
                    c["dis_became_pass_item4_false"] += 1
            if a.kind == "refuse" and a.label == ws_label and cc.kind == "refuse" and cc.label.startswith("tipauth:"):
                if wp:
                    c["dis_item4_true_gate_refused"] += 1
                elif wp_reason != cc.label[len("tipauth:"):]:
                    c["dis_gate_reason_differs"] += 1

    n = cat.total()
    dis = c["dis_became_pass_item4_false"] + c["dis_item4_true_gate_refused"] + c["dis_gate_reason_differs"]
    ref_total = sum(ref_to_pass_by_a.values())
    other_total = cat["other"] + cat["refusal_to_pass_other"]
    ordered = sorted(transitions.items(), key=lambda kv: (-kv[1], kv[0]))
    only_head, only_base = sorted(head_names - base_names), sorted(base_names - head_names)
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
                    "pool_not_str": c["unsealed_pool_not_str"], "not_strict_json": c["unsealed_not_strict_json"], "in_item4_selection": c["in_item4_selection"]},
        "scan": {"files": [p.name for p, _ in snap], "file_sizes": {p.name: s for p, s in snap}, "lines": c["lines"], "bad_line": c["bad_line"],
                 "bad_line_not_utf8": c["bad_line_not_utf8"], "unreadable_file": c["unreadable_file"],
                 "files_last_line_without_newline": c["files_last_line_without_newline"],
                 "trigger_pattern_matches": c["trigger_pattern_matches"], "trigger_records_parsed": c["trigger_records_parsed"],
                 "trigger_pattern_matches_equal_trigger_records_parsed": c["trigger_pattern_matches"] == c["trigger_records_parsed"],
                 "trigger_records_without_int_t_ms": c["trigger_records_without_int_t_ms"], "trigger_records_outside_window": c["trigger_records_outside_window"]},
        "a_vs_b": {"head_path_off_default_call": ab_default.out(), "head_path_off_tipauth_none": ab_none.out()},
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
        "would_pass": {"item4_predicate_true": c["would_pass_true"], "item4_predicate_true_in_item4_selection": c["would_pass_true_in_item4_selection"],
                       "item4_predicate_true_by_c_outcome": dict(sorted(wp_by_c.items())), "refusals_to_passes": ref_total,
                       "refusals_to_passes_item4_true": c["became_pass_item4_true"],
                       "disagreements": {"became_pass_item4_false": c["dis_became_pass_item4_false"], "item4_true_gate_refused": c["dis_item4_true_gate_refused"],
                                         "gate_reason_differs": c["dis_gate_reason_differs"], "total": dis},
                       "predicate_raised": c["would_pass_predicate_raised"]},
    }
    res["verdict"] = {"a_vs_b_default_zero_differences": ab_default.differ == 0, "a_vs_b_tipauth_none_zero_differences": ab_none.differ == 0,
                      "passes_to_refusals_zero": cat["pass_to_nonpass"] == 0, "other_changes_zero": other_total == 0,
                      "would_pass_disagreements_zero": dis == 0 and c["would_pass_predicate_raised"] == 0}
    assert tuple(res["verdict"]) == VERDICT_KEYS
    return res


# ---- CLI ---------------------------------------------------------------------------------------------------------------------------------------

def dumps(res: dict) -> str:
    return json.dumps(res, sort_keys=True, indent=1, allow_nan=False) + "\n"


def error_result(err: InputError) -> dict:
    return {"tool": TOOL, "error": err.msg, "exit_code": err.code}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], epilog="EXIT CODE: 0 means only that the tool ran. Read `verdict` in the JSON. "
                                 "2 usage, 3 shadow dir missing, 4 no start record, 5 --expect-code-sha mismatch, 6 --out exists, 7 executor file problem.")
    ap.add_argument("--shadow-dir", required=True)
    ap.add_argument("--since-ms", type=int, required=True)
    ap.add_argument("--until-ms", type=int, required=True)
    ap.add_argument("--expect-code-sha", required=True, help="40 hex: the origin (last) start record's code_sha must equal it")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--base-file", help="the INSTALLED build's tools/h5_executor.py")
    g.add_argument("--base-git-sha", help="40 hex; loaded from `git show SHA:tools/h5_executor.py` (the installed sha is b7ddeb60aa4ced9243211775cf3d5a22ccc42dff)")
    ap.add_argument("--head-file", default=str(HEAD_DEFAULT), help="default: this checkout's tools/h5_executor.py")
    ap.add_argument("--out", default=None, help="JSON file, created exclusively; an existing file is never overwritten (exit 6)")
    a = ap.parse_args(argv)
    if not re.fullmatch(r"[0-9a-f]{40}", a.expect_code_sha):
        ap.error("--expect-code-sha must be 40 lowercase hex characters")
    if a.base_git_sha is not None and not GIT_SHA_RE.fullmatch(a.base_git_sha):
        ap.error("--base-git-sha must be 40 lowercase hex characters")
    if a.until_ms < a.since_ms:
        ap.error("--until-ms is before --since-ms")
    try:
        if a.out and os.path.lexists(a.out):
            raise InputError(f"--out {a.out} exists; it is never overwritten", 6)
        d = Path(a.shadow_dir)
        if not d.is_dir():
            raise InputError("shadow directory missing", 3)
        base = load_file(Path(a.base_file), BASE_MODNAME) if a.base_file else load_git(a.base_git_sha, BASE_MODNAME)
        head = load_file(Path(a.head_file), HEAD_MODNAME)
        check_shapes(base, head)
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
