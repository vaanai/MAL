"""r4bt_c0_a3_carry: writes the A3 CARRY file that the r4-b-T C0 executor (`tools/r4bt_c0_executor.py`, schema `r4bt_a3_carry_v1`) reads. PAPER ONLY. No RPC, no key.

DEC-032 Am.9 section 5 F4: for r4bt-a1 "any A3 halt flag" means the DEC-031 Am.27 item 12 hard halts only (account pins or fees-program deploy, follower decoder-pin failure,
dead-man). The five boost / synthetic flags are skipped and counted. This tool never judges: it combines its inputs, and ANY failure writes a LATCHING carry.

Inputs (all required):
  --record   the SNAPSHOT of the newest daily structure-monitor line (one JSON object) that the classifier was run on. The carry's record_sha256 is the sha256 of exactly those bytes.
  --daily    the daily file (JSONL): only to record its sha256 and to check that --record is still its last non-empty line. If a newer line has appeared the tool exits 3 and writes
             NOTHING (the wrapper re-snapshots and re-classifies once); with --final it is a refusal like any other (a latching carry).
  --verdict  the `tools.r4bt_a3` / `tools.r4bt_a3_writes` classify output (schema r4bt_a3_v1) FOR THAT RECORD (same run_utc). NONE | RECORD_ONLY clear; HALT | NEEDS_CHECK | ERROR latch.
  --follower-samples, --decoder-ref   the files given to the classifier. Both are required (the decoder-pin halt of item 12 can never pass unevaluated). The samples must hold at least
             one sample with an integer ts_ms, and the newest must be at most 36 h older than the record's run_utc.
  --classify-rc   the classifier's exit code (default 0); nonzero latches.

Failure rule (Am.3 item 1.1 raw-record checks, QP and reviewer on #1249): a classifier failure, an unparseable / flag-less / malformed newest record, a verdict that is missing, of another
schema or run_utc, a clear verdict beside a set or unevaluated hard flag, a stale or missing follower sample, a stale / future record, or a duplicate run_utc, writes a carry with verdict
"ERROR", hard_halt true and hard_halt_reasons "refused:<reason>" (same schema, bot_id, change_id), so the executor latches (`a3_carry:ERROR`) at once; the old carry is never left in place.
Its run_utc is the newest record's when that parses and is within the executor's bounds, else the run instant. Exit 2 (a person must look). The one exception: a change_id the executor does
not register has no identity to write; exit 2, nothing written.

Written (atomic: tmp file in the same dir, fsync, rename; mode 0644): the executor's keys {schema, bot_id, change_id, run_utc, verdict, halt_at_ms, halt.flags, warn.flags.program_changed}
plus {written_utc, hard_halt, hard_halt_reasons, flags_evaluated, skipped_flags, skipped_counts, source {record_sha256, daily_sha256, verdict_sha256, follower_samples_sha256,
newest_sample_ts_ms}}. The file is written by an ordinary user on research-0; copying it to the root-owned `a3_carry_path` on mal-fast-0 is a Helm install step (see the PR body).

    python -m tools.r4bt_c0_a3_carry --record R --daily D --verdict V --follower-samples S --decoder-ref F --change-id ID --out CARRY [--classify-rc N] [--final] [--now-ms N]
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
SAMPLE_MAX_AGE_MS = E.A3_MAX_AGE_MS


class Refused(Exception):
    pass


class NoIdentity(Refused):
    """Nothing can be written (unregistered change_id)."""


class NewerLine(Exception):
    """The daily file has a newer line than the snapshot."""


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


def check_snapshot(daily: bytes, record: bytes) -> None:
    """The snapshot must be the daily file's last non-empty line (else NewerLine); two lines with the latest run_utc and different bytes refuse."""
    lines = [ln.strip() for ln in daily.split(b"\n") if ln.strip()]
    snap = record.strip()
    if not snap or not lines:
        raise Refused("empty record snapshot or daily file")
    if lines[-1] != snap:
        raise NewerLine()
    try:
        rec = json.loads(snap)
    except ValueError:
        return
    for ln in lines[:-1]:
        try:
            o = json.loads(ln)
        except ValueError:
            continue
        if isinstance(o, dict) and isinstance(rec, dict) and o.get("run_utc") == rec.get("run_utc") and ln != snap:
            raise Refused("two daily lines share the latest run_utc with different bytes")


def newest_sample_ts(samples: bytes) -> int:
    ts = []
    for ln in samples.decode("utf-8", "replace").splitlines():
        if not ln.strip():
            continue
        try:
            s = json.loads(ln)
        except ValueError:
            raise Refused("a follower sample line is not JSON")
        if not isinstance(s, dict):
            raise Refused("a follower sample is not an object")
        t = s.get("ts_ms")
        if isinstance(t, int) and not isinstance(t, bool):
            ts.append(t)
    if not ts:
        raise Refused("no follower sample with an integer ts_ms (the decoder-pin halt would be unevaluated)")
    return max(ts)


def build_carry(rec: Mapping[str, Any], verdict: Mapping[str, Any], *, change_id: str, now_ms: int, source: Mapping[str, Any]) -> dict:
    if change_id not in E.REGISTERED_CHANGE_IDS:
        raise NoIdentity(f"change_id {change_id!r} is not registered in the executor")
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
            "skipped_flags": skipped, "skipped_counts": {k: 1 for k in skipped}, "source": dict(source)}


def latch_carry(*, change_id: str, run_utc: str, now_ms: int, reason: str, source: Mapping[str, Any]) -> dict:
    return {"schema": SCHEMA, "bot_id": E.BOT_ID, "change_id": change_id, "run_utc": run_utc, "written_utc": _iso(now_ms), "verdict": "ERROR", "halt_at_ms": None,
            "hard_halt": True, "hard_halt_reasons": ["refused:" + reason], "halt": {"flags": {}}, "warn": {"flags": {"program_changed": {"warn": True}}},
            "flags_evaluated": [], "skipped_flags": [], "skipped_counts": {}, "source": dict(source)}


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


def _safe_run_utc(record: bytes | None, now_ms: int) -> str:
    """The newest record's run_utc when it parses and is inside the executor's bounds (a stale or future one is latched by the executor anyway), else the run instant."""
    try:
        r = json.loads(record or b"")
        ms = _ms(r.get("run_utc"), "record")
        if -E.A3_FUTURE_SKEW_MS <= now_ms - ms <= E.A3_MAX_AGE_MS:
            return r["run_utc"]
    except (ValueError, AttributeError, TypeError, Refused):
        pass
    return _iso(now_ms)


def run(*, daily: str, record: str, verdict: str, samples: str, decoder_ref: str, change_id: str, out: str, now_ms: int, classify_rc: int = 0, final: bool = False) -> tuple[dict, str | None]:
    """(carry, refusal). refusal None = a normal carry was written. A refusal writes the latching carry. Raises NewerLine (nothing written) when not `final`, NoIdentity (nothing written)."""
    if change_id not in E.REGISTERED_CHANGE_IDS:
        raise NoIdentity(f"change_id {change_id!r} is not registered in the executor")
    source: dict[str, Any] = {}
    rbytes = None
    try:
        rbytes = _read(record)
        dbytes = _read(daily)
        source.update(record_sha256=_sha(rbytes.strip()), daily_sha256=_sha(dbytes))
        check_snapshot(dbytes, rbytes)
        if classify_rc != 0:
            raise Refused(f"classifier exit {classify_rc}")
        vbytes = _read(verdict)
        source["verdict_sha256"] = _sha(vbytes)
        _read(decoder_ref)
        sbytes = _read(samples)
        source["follower_samples_sha256"] = _sha(sbytes)
        newest = newest_sample_ts(sbytes)
        source["newest_sample_ts_ms"] = newest
        try:
            rec = json.loads(rbytes)
            vd = json.loads(vbytes)
        except ValueError:
            raise Refused("record or verdict is not JSON")
        if not isinstance(rec, dict) or not isinstance(vd, dict):
            raise Refused("record or verdict is not an object")
        if newest < _ms(rec.get("run_utc"), "record") - SAMPLE_MAX_AGE_MS:
            raise Refused("newest follower sample is more than 36 h older than the record")
        carry = build_carry(rec, vd, change_id=change_id, now_ms=now_ms, source=source)
        reason = None
    except NewerLine:
        if not final:
            raise
        reason = "a newer daily line appeared after the snapshot"
    except NoIdentity:
        raise
    except Refused as e:
        reason = str(e)
    except Exception as e:  # any unexpected crash inside the checks is a refusal too: a latching carry, never the old clean one
        reason = "unexpected " + e.__class__.__name__
    if reason is not None:
        carry = latch_carry(change_id=change_id, run_utc=_safe_run_utc(rbytes, now_ms), now_ms=now_ms, reason=reason, source=source)
    atomic_write(out, carry)
    return carry, reason


def crash_latch(out: str, change_id: str, now_ms: int, exc: BaseException) -> int:
    """Last resort after an unexpected crash outside run()'s checks: write a latching carry (class name only, no message text); if even that fails, remove the old carry so the
    executor latches on a missing file. Returns the exit code (2 written, 12 neither)."""
    reason = "crash " + exc.__class__.__name__
    try:
        atomic_write(out, latch_carry(change_id=change_id, run_utc=_iso(now_ms), now_ms=now_ms, reason=reason, source={}))
        print(f"r4bt_c0_a3_carry: LATCHING carry written after a crash: {reason}", file=sys.stderr)
        return 2
    except Exception:
        try:
            os.unlink(out)
        except OSError:
            pass
        print(f"r4bt_c0_a3_carry: crash ({reason}); latching carry could not be written; old carry removed", file=sys.stderr)
        return 12


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Write the r4bt-a1 C0 executor's A3 carry file.")
    for k in ("record", "daily", "verdict", "follower-samples", "decoder-ref", "change-id", "out"):
        ap.add_argument("--" + k, required=True)
    ap.add_argument("--classify-rc", type=int, default=0)
    ap.add_argument("--final", action="store_true")
    ap.add_argument("--now-ms", type=int, default=None)
    a = ap.parse_args(argv)
    now = int(time.time() * 1000) if a.now_ms is None else a.now_ms
    try:
        c, reason = run(daily=a.daily, record=a.record, verdict=a.verdict, samples=a.follower_samples, decoder_ref=a.decoder_ref, change_id=a.change_id, out=a.out, now_ms=now,
                        classify_rc=a.classify_rc, final=a.final)
    except NewerLine:
        print("r4bt_c0_a3_carry: a newer daily line appeared; nothing written (re-snapshot)", file=sys.stderr)
        return 3
    except Refused as e:
        print(f"r4bt_c0_a3_carry: REFUSED, nothing written: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"r4bt_c0_a3_carry: write failed: {e.__class__.__name__}", file=sys.stderr)
        try:
            os.unlink(a.out)  # a failed write must not leave the old clean carry
        except OSError:
            pass
        return 12
    except Exception as e:  # unexpected crash anywhere: still a latching carry (an unregistered change_id has no identity: exit 2, nothing written)
        if a.change_id not in E.REGISTERED_CHANGE_IDS:
            print("r4bt_c0_a3_carry: crash with an unregistered change_id; nothing written", file=sys.stderr)
            return 2
        return crash_latch(a.out, a.change_id, now, e)
    print(json.dumps({k: c[k] for k in ("run_utc", "verdict", "hard_halt", "hard_halt_reasons", "skipped_flags")}, sort_keys=True))
    if reason is not None:
        print(f"r4bt_c0_a3_carry: LATCHING carry written: {reason}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
