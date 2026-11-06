"""r4bt_c0_a3_carry: writes the A3 CARRY file that the r4-b-T C0 executor (`tools/r4bt_c0_executor.py`, schema `r4bt_a3_carry_v1`) reads. PAPER ONLY. No RPC, no key.

DEC-032 Am.9 section 5 F4: for r4bt-a1 "any A3 halt flag" means the DEC-031 Am.27 item 12 hard halts only (account pins or fees-program deploy, follower decoder-pin failure,
dead-man). The five boost / synthetic flags are skipped and counted. This tool never judges: it combines two inputs and refuses on any ambiguity.

  --daily   the daily structure monitor file (JSONL, `tools/pump_structure_monitor.py`); the LAST non-empty line is the latest record. A last line that does not parse, or
            two lines with the same run_utc and different bytes, is a refusal (never an earlier record).
  --verdict the `tools.r4bt_a3 classify` / `tools.r4bt_a3_writes classify` output (schema `r4bt_a3_v1`) FOR THAT SAME RECORD (its run_utc must equal the record's). It holds the
            item 12 verdict (NONE | RECORD_ONLY clear; HALT | NEEDS_CHECK | ERROR latch in the executor).

Refusals (exit 2, nothing written, the previous carry is left in place and goes stale at 36 h, which the executor latches): record unparseable / no run_utc / no halt flags / a flag
whose halt or evaluated is not a bool; record older than 36 h or more than 300 s in the future (the executor's own bounds); verdict of another schema, another run_utc, an unknown
verdict, or a CLEAR verdict that lists hard halts or sits next to a hard flag that is set or unevaluated (the two inputs disagree).

Written (atomic: tmp file in the same dir, fsync, rename; mode 0644): the executor's keys {schema, bot_id, change_id, run_utc, verdict, halt_at_ms, halt.flags, warn.flags.program_changed}
plus {written_utc, hard_halt, hard_halt_reasons, flags_evaluated, skipped_flags (the boost / synthetic flags that are set), skipped_counts, source {record_sha256 (the line),
daily_sha256 (the file as read), verdict_sha256}}. hard_halt is True when the executor would latch (a non-clear verdict, a hard flag set / unevaluated, or program_changed warn).
The file is written by an ordinary user on research-0. Copying it to the root-owned `a3_carry_path` on mal-fast-0 is a Helm install step (the executor requires a root-owned file).

    python -m tools.r4bt_c0_a3_carry --daily D --verdict V --change-id ID --out CARRY [--now-ms N]
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from tools import r4bt_c0_executor as E

SCHEMA = E.CARRY_SCHEMA
VERDICT_SCHEMA = "r4bt_a3_v1"
VERDICTS = ("NONE", "NEEDS_CHECK", "RECORD_ONLY", "HALT", "ERROR")
MAX_BYTES = 8_000_000


class Refused(Exception):
    pass


def _ms(v: Any, what: str) -> int:
    try:
        return int(datetime.strptime(v, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() * 1000)
    except (ValueError, TypeError):
        raise Refused(f"{what} run_utc is not YYYY-MM-DDTHH:MM:SSZ")


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _read(path: str) -> bytes:
    try:
        p = Path(path)
        if p.is_symlink() or not p.is_file() or p.stat().st_size > MAX_BYTES:
            raise Refused(f"{path} is not a regular file under {MAX_BYTES} bytes")
        return p.read_bytes()
    except OSError as e:
        raise Refused(f"{path} unreadable: {e.__class__.__name__}")


def latest_record(daily: bytes) -> tuple[dict, bytes]:
    """(record, its line bytes): the last non-empty line. Refuses a last line that is not one JSON object, or a duplicate run_utc with other bytes."""
    lines = [ln for ln in daily.split(b"\n") if ln.strip()]
    if not lines:
        raise Refused("daily file has no record")
    try:
        rec = json.loads(lines[-1])
    except ValueError:
        raise Refused("the last line of the daily file is not JSON (an earlier record is never used)")
    if not isinstance(rec, dict):
        raise Refused("the last daily line is not an object")
    for ln in lines[:-1]:
        try:
            o = json.loads(ln)
        except ValueError:
            continue
        if isinstance(o, dict) and o.get("run_utc") == rec.get("run_utc") and ln.strip() != lines[-1].strip():
            raise Refused("two daily lines share the latest run_utc with different bytes")
    return rec, lines[-1].strip()


def build_carry(rec: Mapping[str, Any], verdict: Mapping[str, Any], *, change_id: str, now_ms: int, record_sha256: str, daily_sha256: str, verdict_sha256: str) -> dict:
    if change_id not in E.REGISTERED_CHANGE_IDS:
        raise Refused(f"change_id {change_id!r} is not registered in the executor")
    rec_ms = _ms(rec.get("run_utc"), "record")
    if rec_ms - now_ms > E.A3_FUTURE_SKEW_MS:
        raise Refused("record run_utc is in the future")
    if now_ms - rec_ms > E.A3_MAX_AGE_MS:
        raise Refused("record is older than 36 h")
    halt = rec.get("halt")
    flags = halt.get("flags") if isinstance(halt, dict) else None
    if not isinstance(flags, dict) or not flags:
        raise Refused("record has no halt.flags")
    out_flags = {}
    for k, f in sorted(flags.items()):
        if not (isinstance(f, dict) and isinstance(f.get("halt"), bool) and isinstance(f.get("evaluated"), bool)):
            raise Refused(f"halt flag {k} has a non-bool halt or evaluated")
        out_flags[k] = {"halt": f["halt"], "evaluated": f["evaluated"]}
    warn = rec.get("warn")
    pc = ((warn.get("flags") or {}).get("program_changed")) if isinstance(warn, dict) and isinstance(warn.get("flags"), dict) else None
    if not (isinstance(pc, dict) and isinstance(pc.get("warn"), bool)):
        raise Refused("record has no boolean warn.flags.program_changed.warn")
    if verdict.get("schema") != VERDICT_SCHEMA:
        raise Refused("verdict is not schema r4bt_a3_v1")
    if verdict.get("run_utc") != rec.get("run_utc"):
        raise Refused("verdict run_utc differs from the latest daily record's")
    v = verdict.get("verdict")
    if v not in VERDICTS:
        raise Refused("unknown verdict")
    hard_flags = sorted(k for k, f in out_flags.items() if k not in E.NOT_HALTING_FLAGS and (f["halt"] or not f["evaluated"]))
    reasons = [f"verdict:{v}"] if v not in E.CARRY_CLEAR else []
    reasons += [f"flag:{k}" for k in hard_flags]
    if pc["warn"]:
        reasons.append("warn:program_changed")
    if v in E.CARRY_CLEAR and (hard_flags or verdict.get("hard")):
        raise Refused("verdict is clear but a hard flag is set / unevaluated or the verdict lists hard halts: the inputs disagree")
    skipped = sorted(k for k, f in out_flags.items() if k in E.NOT_HALTING_FLAGS and f["halt"])
    halt_at = verdict.get("halt_at_ms")
    return {"schema": SCHEMA, "bot_id": E.BOT_ID, "change_id": change_id, "run_utc": rec["run_utc"], "written_utc": _iso(now_ms), "verdict": v,
            "halt_at_ms": halt_at if isinstance(halt_at, int) and not isinstance(halt_at, bool) else None,
            "hard_halt": bool(reasons), "hard_halt_reasons": reasons,
            "halt": {"flags": out_flags}, "warn": {"flags": {"program_changed": {"warn": pc["warn"]}}},
            "flags_evaluated": sorted(k for k, f in out_flags.items() if f["evaluated"]),
            "skipped_flags": skipped, "skipped_counts": {k: 1 for k in skipped},
            "source": {"record_sha256": record_sha256, "daily_sha256": daily_sha256, "verdict_sha256": verdict_sha256}}


def atomic_write(path: str, obj: Mapping[str, Any]) -> None:
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".carry-", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(obj, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def run(daily: str, verdict_path: str, change_id: str, out: str, now_ms: int) -> dict:
    dbytes = _read(daily)
    vbytes = _read(verdict_path)
    rec, line = latest_record(dbytes)
    try:
        verdict = json.loads(vbytes)
    except ValueError:
        raise Refused("verdict file is not JSON")
    if not isinstance(verdict, dict):
        raise Refused("verdict file is not an object")
    carry = build_carry(rec, verdict, change_id=change_id, now_ms=now_ms, record_sha256=_sha(line), daily_sha256=_sha(dbytes), verdict_sha256=_sha(vbytes))
    atomic_write(out, carry)
    return carry


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Write the r4bt-a1 C0 executor's A3 carry file.")
    ap.add_argument("--daily", required=True)
    ap.add_argument("--verdict", required=True)
    ap.add_argument("--change-id", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--now-ms", type=int, default=None)
    a = ap.parse_args(argv)
    now = int(time.time() * 1000) if a.now_ms is None else a.now_ms
    try:
        c = run(a.daily, a.verdict, a.change_id, a.out, now)
    except Refused as e:
        print(f"r4bt_c0_a3_carry: REFUSED: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"r4bt_c0_a3_carry: write failed: {e.__class__.__name__}", file=sys.stderr)
        return 12
    print(json.dumps({k: c[k] for k in ("run_utc", "verdict", "hard_halt", "hard_halt_reasons", "skipped_flags")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
