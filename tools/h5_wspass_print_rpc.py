#!/usr/bin/env python3
"""H5 shadow, report-only live-exposure check: do the ws-pass and refused TRIGGER PRINTS exist on the finalized chain (public RPC)?

Spec: quant-proof ruling on PR #610, comment 6127657282, section 3 (the +6 h interim trigger-print read, 1 of 5 ws-pass prints absent from the tip
tape). It is a COUNT. It is a live-exposure check outside items 1 to 10: it can count against phase 2, never for it. It makes no book claim.

  python3 -m tools.h5_wspass_print_rpc plan   --shadow-dir D --since-ms T0 --until-ms T1 --expect-code-sha SHA40 --out-dir O
  python3 -m tools.h5_wspass_print_rpc ask    --out-dir O
  python3 -m tools.h5_wspass_print_rpc reask  --out-dir O
  python3 -m tools.h5_wspass_print_rpc report --out-dir O

LAUNCH: from the repo root, as `/usr/bin/python3 -m tools.h5_wspass_print_rpc ...`, WITHOUT `-I`. With `-I` the cwd is not on sys.path, the `tools`
package cannot be imported and the launch fails before any line of this tool runs (that was job #829 for the trigger-print tool). The CLI is sh-safe
(plain flags, no shell syntax, nothing read from stdin).

WHAT IT READS (nothing else): the shadow hour files `h5-shadow-*.jsonl` in --shadow-dir (via tools.h5_tipauth_item4 hour_files / snapshot / iter_records)
and its own out-dir. Selection reuses tools.h5_tipauth_trigger_print: `classify` (ws_pass / would_pass / refused) and the origin-start handling, and
tools.h5_tipauth_item4: `flag` (sealed records are skipped and counted before any other key is read), `on_schedule`, `SETTLE_MS`. Keys read from a
trigger stay within the trigger-print tool's ALLOWED_KEYS (signature, variant, t_ms, type, the sealed flags and what `classify` reads). No outcome, fill,
exit, ladder or P&L key is read. It opens NO tip file, no item-4 or item-8 out file and no executor ledger; it has no tip-dir argument. No underscore
(private) name is imported from the two tools.

WHAT IT ASKS: `getTransaction` at `finalized`, `maxSupportedTransactionVersion` 0, through tools.h5_tipauth_census `cmd_ask` / `cmd_reask` unchanged
(its transport, its fixed public RPC URL with no environment override and no key, 0 Helius credits, at most 2 rps, REASK_MAX 3 rounds, a re-ask only
>= 600,000 ms after the last ask). The plan file is written in the census plan layout (census_plan.json; sets A = ws_pass, B = refused, C = would_pass)
so those functions run on it as they are; the class map and this tool's own hash sit beside the census keys. The state file and the request log hold,
per signature, only: the signature (local files, mode 0600), found / null / error, slot of a found answer, error class, asked_ms. The tool never prints
a signature, pool or slot.

CLASSES: every unsealed pv trigger print in [origin start, until] is classed by `classify`. Each distinct signature is asked once, in the highest class
it has (ws_pass, then would_pass, then refused); a signature in two classes is counted in `cross_class_duplicates`. A trigger without a non-empty string
signature cannot be asked: it is counted `unaskable` and blocks any conclusion.

REPORT (one JSON object on stdout; no signature, pool or slot): per class asked, returned, not_returned, error, pending. not_returned = null twice with
the two asks >= 600,000 ms apart; an error is never not_returned; a signature not yet asked, or with one null, is pending.

READING RULE, fixed before any ask (ruling table; `reading` is exactly one of these three sentences, chosen only by the conditions below; the first has
the ws_pass count in place of {n}):
  (a) every ws_pass returned (at least 1), no error and no pending in any class, nothing unaskable:
      "all {n} were returned at finalized commitment; the missing row is on the tape's side; it is not shown that the print's reserves were right"
  (b) ws_pass not_returned >= 1, every other ws_pass returned, every refused returned, no error and no pending in any class, nothing unaskable:
      "consistent with a live-feed print for a transaction not in a finalized block; not proof; the count does not name which print"
  (c) anything else:
      "no conclusion"

One plan per scheduled read whose ws_pass absent count rose, on that read's until-ms; never re-made (a second `plan` into an out-dir that holds a plan is
refused). No ask runs before the Reviewer's verdict and QP OK on the exact head are on the PR.

Exit codes: 0 the command ran (NOT a verdict); 1 a census refusal (message on stderr, no signature in it); 2 no start record at or after --since-ms;
3 missing directory or no plan; 4 refused instant (off schedule or not yet 120 s past); 5 the out-dir already holds a plan; 6 --expect-code-sha does not
match the origin start record.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import os
import re
import sys
import time
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
SPEC = {"ruling": "PR #610 comment 6127657282 section 3"}
DAY_MS = I4.DAY_MS
CLASS_SETS = (("ws_pass", "A"), ("refused", "B"), ("would_pass", "C"))  # class -> census set letter
CLASS_ORDER = ("ws_pass", "would_pass", "refused")  # precedence when a signature is in two classes
READING_ALL = "all {n} were returned at finalized commitment; the missing row is on the tape's side; it is not shown that the print's reserves were right"
READING_ABSENT = "consistent with a live-feed print for a transaction not in a finalized block; not proof; the count does not name which print"
READING_NONE = "no conclusion"
META_KEY = "wspass"
OWN_FILE_MODE = 0o600


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
    keys = ("since_ms", "until_ms", "expect_code_sha", "class_sigs", "unaskable", "cross_class_duplicates")
    return sha256_bytes(C.dumps({k: body[k] for k in keys}).encode("utf-8"))


def build_plan(sel: dict, since_ms: int, until_ms: int, expect_code_sha: str) -> dict:
    sets = {letter: list(sel["sigs"][cls]) for cls, letter in CLASS_SETS}
    cap = sum(len(v) for v in sets.values())
    meta = {"tool": TOOL, "spec": SPEC, "tool_sha256": tool_sha(), "since_ms": since_ms, "until_ms": until_ms, "expect_code_sha": expect_code_sha,
            "origin_t_ms": sel["origin_t_ms"], "class_letters": dict(CLASS_SETS), "class_sigs": {c: sel["sigs"][c] for c in CLASS_ORDER},
            "triggers_n": sel["triggers_n"], "unaskable": sel["unaskable"], "cross_class_duplicates": sel["cross_class_duplicates"],
            "counts": {c: len(sel["sigs"][c]) for c in CLASS_ORDER}}
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


def cmd_plan(shadow_dir: Path, out_dir: Path, since_ms: int, until_ms: int, expect_code_sha: str, now_ms: int, object_hook: Callable | None = None) -> tuple[int, dict]:
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
    out_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(out_dir, 0o700)
    plan = build_plan(sel, since_ms, until_ms, expect_code_sha)
    text = C.dumps(plan)
    _write_private(out_dir / C.PLAN_NAME, text.encode("utf-8"))
    _touch_private(out_dir / C.STATE_NAME)
    _touch_private(out_dir / C.LOG_NAME)
    m = plan[META_KEY]
    return 0, {"tool": TOOL, "planned": True, "counts": m["counts"], "triggers_n": m["triggers_n"], "unaskable": m["unaskable"],
               "interim": plan["interim"], "plan_selection_sha256": m["plan_selection_sha256"], "tool_sha256": m["tool_sha256"],
               "plan_sha256": sha256_bytes(text.encode("utf-8"))}


def _lock_files(out_dir: Path) -> None:
    for name in (C.STATE_NAME, C.LOG_NAME):
        if (out_dir / name).exists():
            os.chmod(out_dir / name, OWN_FILE_MODE)


def cmd_ask(out_dir: Path, transport: Callable = C.http_transport, sleep: Callable = time.sleep,
            now_ms: Callable = lambda: int(time.time() * 1000)) -> int:
    n = C.cmd_ask(out_dir, transport, sleep, now_ms)
    _lock_files(out_dir)
    return n


def cmd_reask(out_dir: Path, transport: Callable = C.http_transport, sleep: Callable = time.sleep,
              now_ms: Callable = lambda: int(time.time() * 1000)) -> int:
    n = C.cmd_reask(out_dir, transport, sleep, now_ms)
    _lock_files(out_dir)
    return n


def cmd_report(out_dir: Path) -> dict:
    plan = C.load_plan(out_dir)
    meta = plan.get(META_KEY)
    if not isinstance(meta, dict) or meta.get("plan_selection_sha256") != selection_hash(meta):
        raise SystemExit("refused: plan is not this tool's, or its selection hash does not match")
    rows = C.read_jsonl(out_dir / C.STATE_NAME)
    by = C.states_by_sig(rows)
    per: dict = {}
    for cls in CLASS_ORDER:
        d = {"asked": 0, "returned": 0, "not_returned": 0, "error": 0, "pending": 0}
        for sig in plan["sets"][meta["class_letters"][cls]]:
            ans = by.get(sig, [])
            if not ans:
                d["pending"] += 1
                continue
            d["asked"] += 1
            st = C.classify(ans)  # found | not_found | pending | error; not_found only after two nulls >= 600,000 ms apart
            d[{"found": "returned", "not_found": "not_returned", "pending": "pending", "error": "error"}[st]] += 1
        d["planned"] = len(plan["sets"][meta["class_letters"][cls]])
        d["unaskable"] = meta["unaskable"][cls]
        per[cls] = d
    ws, rf = per["ws_pass"], per["refused"]
    open_ = sum(per[c]["error"] + per[c]["pending"] + per[c]["unaskable"] for c in CLASS_ORDER)
    if ws["planned"] >= 1 and ws["returned"] == ws["planned"] and open_ == 0:
        reading = READING_ALL.format(n=ws["planned"])
    elif ws["not_returned"] >= 1 and ws["returned"] + ws["not_returned"] == ws["planned"] and rf["returned"] == rf["planned"] and open_ == 0:
        reading = READING_ABSENT
    else:
        reading = READING_NONE
    interim = (meta["until_ms"] - meta["since_ms"]) < DAY_MS
    return {"tool": TOOL, "spec": SPEC, "classes": per, "interim": interim, "interim_label": "interim, not the deciding read" if interim else None,
            "cross_class_duplicates": meta["cross_class_duplicates"], "tool_sha256": tool_sha(), "plan_tool_sha256": meta["tool_sha256"],
            "plan_selection_sha256": meta["plan_selection_sha256"], "plan_sha256": sha256_bytes((out_dir / C.PLAN_NAME).read_bytes()),
            "reask_rounds_n": len({r.get("round") for r in rows if str(r.get("round")).startswith("reask")}), "reading": reading,
            "note": "a live-exposure check outside items 1 to 10: it can count against phase 2, never for it; exit 0 is not a verdict"}


def main(argv: list[str] | None = None, transport: Callable = C.http_transport, sleep: Callable = time.sleep, now_ms: int | None = None,
         object_hook: Callable | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["plan", "ask", "reask", "report"])
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--shadow-dir")
    ap.add_argument("--since-ms", type=int)
    ap.add_argument("--until-ms", type=int)
    ap.add_argument("--expect-code-sha")
    a = ap.parse_args(argv)
    out = Path(a.out_dir)
    if a.cmd == "plan":
        for name, val in (("--shadow-dir", a.shadow_dir), ("--since-ms", a.since_ms), ("--until-ms", a.until_ms), ("--expect-code-sha", a.expect_code_sha)):
            if val is None:
                ap.error(f"{name} is required")
        if not re.fullmatch(r"[0-9a-f]{40}", a.expect_code_sha):
            ap.error("--expect-code-sha must be 40 lowercase hex characters")
        now = int(time.time() * 1000) if now_ms is None else now_ms
        code, res = cmd_plan(Path(a.shadow_dir), out, a.since_ms, a.until_ms, a.expect_code_sha, now, object_hook)
    else:
        if not (out / C.PLAN_NAME).exists():
            sys.stdout.write(C.dumps({"tool": TOOL, "error": "no plan in the out-dir", "blocking_reasons": ["no_plan"]}))
            return 3
        if a.cmd == "report":
            code, res = 0, cmd_report(out)
        else:
            n = (cmd_ask if a.cmd == "ask" else cmd_reask)(out, transport, sleep)
            code, res = 0, {"tool": TOOL, "calls": n}
    sys.stdout.write(C.dumps(res))
    return code


if __name__ == "__main__":
    sys.exit(main())
