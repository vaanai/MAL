"""r4bt_a3_writes: the A3 classifier with PER-WRITE decode results (QP-RULING-1102-r4bt Addendum 1 item A). PAPER ONLY. No RPC, no key. New file (r4bt_*): it edits no existing file.

`tools/r4bt_a3.classify` (in W) judges a program-only redeploy by ONE result for the program's newest slot and dates a failure at the program's earliest write. Addendum 1: every
upgrade write since the pin with no PASS is checked on the interval when its version was live (`tools/r4bt_decode_evidence.check_all`), and a FAIL, ERROR or timeout on any write
halts at THAT write's instant (for the earliest failing write: the earliest unreviewed write); record-only needs a PASS on every write. This module wraps `r4bt_a3.classify` (imported,
not copied) without changing it:
  1. `r4bt_a3.classify` runs with a neutral PASS for each program whose writes are enumerated (so its own single-result rule cannot halt or clear them), and so still applies every
     other rule unchanged: account pins, the fees program, follower decoder pin, dead-man, unknown flags, programs with no enumerated writes;
  2. each enumerated pump / pumpswap write (the `tools/r4bt_writes.py` file) is then judged by ITS OWN results (`--decode-checks`, one or more `r4bt_decode_check_v1` files):
       any non-PASS result for it                              HALT at that write's instant (at_ms)
       a PASS only after 24 h from the earliest write           HALT at that write's instant (timeout)
       no result and 24 h from the earliest write have passed   HALT at that write's instant (a missing result is no PASS: fail closed)
       no result yet, inside the 24 h                           NEEDS_CHECK (the verdict carries the deadline)
       a PASS within 24 h (mode sample, population or vacuous)  record-only
  3. the final verdict is the EARLIEST halt among the two, else NEEDS_CHECK, else RECORD_ONLY (with the per-write records and `repin_advice`), else classify's own verdict.
The verdict keeps schema `r4bt_a3_v1`, so `python -m tools.r4bt_a3 apply` accepts it unchanged; it adds `per_write` (name, slot, status, mode, slot_range) and `repin_advice`.

    python -m tools.r4bt_a3_writes classify --record REC --pins PINS --writes W.json --decode-checks D1.json [--decode-checks D2.json ...] [the r4bt_a3 classify flags] [--out V]

After a RECORD_ONLY verdict, re-pin the monitor's pins file promptly by a records PR (QP OK and reviewer): until then the earliest write since the old pin dates later writes too,
which is conservative but over-halts.
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools import a3_redeploy_check as ARC
from tools import r4bt_a3 as A
from tools import r4bt_decode_evidence as DE
from tools import r4bt_gate as G

REPIN = ("every pump / pumpswap write since the pin has a PASS: re-pin the monitor pins file by a records PR (QP OK and reviewer), then take a fresh clean monitor run "
         "against the new pins before the next --last-clean-run-utc")


def _int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def merge_checks(files: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """Several `r4bt_decode_check_v1` files -> one. A file of another profile makes the whole input that profile (the classifier then refuses it)."""
    if not files:
        return None
    out: dict[str, Any] = {"profile": A.PROFILE, "redeploys": []}
    for f in files:
        if f.get("profile") != A.PROFILE:
            return dict(f)
        out["redeploys"] += list(f.get("redeploys") or [])
    return out


def classify_all(rec: Mapping[str, Any], pins: Mapping[str, Any], *, now_ms: int, writes: Sequence[Mapping[str, Any]] | None, decode_checks: Mapping[str, Any] | None,
                 **kw: Any) -> dict[str, Any]:
    """Pure (now_ms is the job's clock). kw: last_clean_run_utc, follower_samples, decoder_ref, history (as `r4bt_a3.classify`)."""
    if decode_checks is not None and decode_checks.get("profile") != A.PROFILE:
        return A.classify(rec, pins, now_ms=now_ms, writes=writes, decode_checks=decode_checks, **kw)  # the ERROR of the profile rule
    try:
        diff_programs = {d["name"] for d in ARC.pins_diff(rec, pins) if d["kind"] == "program" and d["name"] in A.RECORD_PROGRAMS}
        newest = {d["name"]: d["new"] for d in ARC.pins_diff(rec, pins) if d["kind"] == "program"}
    except Exception:  # noqa: BLE001 - a broken record: classify returns the ERROR
        return A.classify(rec, pins, now_ms=now_ms, writes=writes, decode_checks=decode_checks, **kw)
    checked = [w for w in DE.write_intervals(writes or []) if w["name"] in diff_programs]
    covered = {w["name"] for w in checked}
    neutral = [{"name": n, "slot": newest[n], "verdict": "PASS", "at_ms": 0} for n in sorted(covered)]
    other = [r for r in ((decode_checks or {}).get("redeploys") or []) if r.get("name") not in covered]
    v = dict(A.classify(rec, pins, now_ms=now_ms, writes=writes, decode_checks={"profile": A.PROFILE, "redeploys": neutral + other}, **kw))
    if v["verdict"] == "ERROR":
        return v
    by_key: dict[tuple, list[Mapping]] = {}
    for r in ((decode_checks or {}).get("redeploys") or []):
        by_key.setdefault((r.get("name"), r.get("slot")), []).append(r)
    deadline = checked[0]["deploy_ms"] + A.CHECK_WINDOW_MS if checked else None
    hard: list[tuple[int, str]] = []
    pending: list[dict] = []
    per_write: list[dict] = []
    passed: list[dict] = []
    for w in checked:
        rs = by_key.get((w["name"], w["slot"]), [])
        bad = [r for r in rs if r.get("verdict") != "PASS"]
        ok = [r for r in rs if r.get("verdict") == "PASS" and _int(r.get("at_ms")) and r["at_ms"] <= deadline]
        e = {"name": w["name"], "slot": w["slot"], "at_ms": w["at_ms"]}
        label = f"{w['name']} write at slot {w['slot']}"
        if bad:
            status = str(bad[0].get("verdict") or "FAIL")
            hard.append((w["at_ms"], f"decode check {status} for the {label} (halt at that write)"))
        elif ok:
            status = "PASS"
            passed.append(dict(e, mode=ok[0].get("mode"), slot_range=ok[0].get("slot_range")))
        elif rs:
            status = "LATE"
            hard.append((w["at_ms"], f"decode check PASS for the {label} not within 24 h of the earliest write (halt at that write)"))
        elif now_ms >= deadline:
            status = "MISSING"
            hard.append((w["at_ms"], f"no decode-check result for the {label} within 24 h of the earliest write (a missing result is no PASS; halt at that write)"))
        else:
            status = "PENDING"
            pending.append(dict(e, deadline_ms=deadline))
        per_write.append(dict(e, status=status, mode=(ok[0].get("mode") if ok else None), slot_range=(ok[0].get("slot_range") if ok else None)))
    v["per_write"] = per_write
    if hard or v["verdict"] == "HALT":
        at = min([a for a, _ in hard] + ([v["halt_at_ms"]] if v["verdict"] == "HALT" else []))
        msgs = sorted(set((v.get("hard") or []) if v["verdict"] == "HALT" else []) | {m for _, m in hard})
        v.update(verdict="HALT", halt_at_ms=at, hard=msgs, reasons=msgs, needs_check=[], record_only=[])
    elif pending or v["verdict"] == "NEEDS_CHECK":
        v.update(verdict="NEEDS_CHECK", halt_at_ms=None, needs_check=list(v.get("needs_check") or []) + pending, reasons=["program redeploy inside its 24 h check window"])
    elif passed and v["verdict"] == "RECORD_ONLY":
        keep = [r for r in (v.get("record_only") or []) if r.get("name") not in covered]
        v.update(record_only=passed + keep, repin_advice=REPIN, reasons=["program-only redeploys, every write PASSed the r4-b-T decode check within 24 h: disclosed"])
    return v


def main(argv: Sequence[str] | None = None, *, now_ms: int | None = None) -> int:
    ap = argparse.ArgumentParser(description="r4-b-T A3 classifier with per-write decode results.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("classify")
    c.add_argument("--record", required=True)
    c.add_argument("--pins", required=True)
    c.add_argument("--writes", required=True, help="the tools/r4bt_writes.py output")
    c.add_argument("--decode-checks", action="append", default=[], help="r4bt_decode_check_v1 file (repeatable)")
    c.add_argument("--follower-samples", default=None)
    c.add_argument("--decoder-ref", default=None)
    c.add_argument("--applied-log", default=None)
    c.add_argument("--last-clean-run-utc", default=None)
    c.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    try:
        load = lambda p: json.loads(Path(p).read_text())  # noqa: E731
        samples = [json.loads(x) for x in Path(a.follower_samples).read_text().splitlines() if x.strip()] if a.follower_samples else None
        v = classify_all(load(a.record), load(a.pins), now_ms=now, writes=load(a.writes), decode_checks=merge_checks([load(p) for p in a.decode_checks]),
                         last_clean_run_utc=a.last_clean_run_utc, follower_samples=samples, decoder_ref=load(a.decoder_ref) if a.decoder_ref else None,
                         history=A.read_history(a.applied_log))
        text = json.dumps(v, sort_keys=True)
        if a.out:
            if Path(a.out).exists():
                raise G.Refused(f"{a.out} exists")
            Path(a.out).write_text(text + "\n")
        print(text)
        return 11 if v["verdict"] == "ERROR" else 0
    except (G.Refused, ValueError, OSError) as e:
        print(f"r4bt_a3_writes: REFUSED: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
