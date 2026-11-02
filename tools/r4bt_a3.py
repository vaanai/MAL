"""r4bt_a3: the A3 ENTRY of the arena book r4-b-T (`r4bt-a1`; QP-RULING-1102-r4bt Q2, the r4-b-T DEC-031 amendment #1187 item 12, number filled at merge). PAPER ONLY.
Registry write only (one stop line per registry). No key, no RPC.

r4-b-T's A3 regime is its OWN. It does not touch EXP-027's A3 rule or the H5 books' (`tools/a3_halt.py`, `tools/a3_redeploy_check.py`, whose verdicts treat the four BOOST
flags and `synthetic_share_high` as always-hard). This tool reads the SAME daily record (`tools/pump_structure_monitor.py`: `run_utc`, `accounts`, `programs`,
`halt.flags`) and pins file, through `a3_redeploy_check.pins_diff` and `_norm_flags` (imported, not copied), and sorts the record into:

  HALT         the window ends at the HALT INSTANT (Q2 item 2, earliest possible, no judgement), for any of:
                 - an account pin change (bonding_fee_config, pumpswap_fee_config, pumpswap_global_config): the last clean run's `run_utc` (`--last-clean-run-utc`), else the
                   pins' `pinned_utc` (the monitor records no account write instant);
                 - the fees program's deploy slot: the EARLIEST write of the fees program since the last clean run (`--writes`), else the last clean run (else `pinned_utc`);
                   NEVER the record's `deploy_utc`, which dates only the newest write (QP on #1196 C2: on 10-19 the first write was 15:46Z and deploy_utc said 17:47Z);
                 - a follower decoder-pin failure: the FIRST failing sample (`--follower-samples`, JSONL with ts_ms, decoder_blobs_pinned, trade_event_v and optionally
                   decoder_blobs against `--decoder-ref`);
                 - the DEAD-MAN: `pins_changed` unevaluated on two consecutive UTC dates (a date with no run counts as unevaluated; one unevaluated run alone does not halt):
                   the first unevaluated run's `run_utc`, or 00:00Z of the first date with no run;
                 - a program-only pump / PumpSwap redeploy WITHOUT a PASS of the r4-b-T decode check within 24 h of its EARLIEST write since the last clean run (FAIL, ERROR,
                   a late PASS, or no result once 24 h have passed): the halt is at that earliest write's instant (the enumerated writes of `--writes`, else the last clean
                   run, else `pinned_utc`), and the 24 h deadline runs from the same instant; never from `deploy_utc`;
                 - a record it cannot classify (pins_changed set with no typed difference, a difference with no flag, an unknown halt flag): the last clean run, else run_utc.
               A halt ends the window: attempts at or after it never count; if the boundary has not come before it the read is NOT_DECIDABLE (the pricer and the read refuse a
               registry stopped before the boundary); a counted trade whose span contains it is an `a3_halt` exclusion (the pricer).
  RECORD_ONLY  a program-only redeploy whose decode check PASSed within 24 h of the write: disclosed; the window continues.
  NEEDS_CHECK  a program-only redeploy still inside its 24 h with no result: the window continues and the verdict carries `deadline_ms`; it is never open-ended.
  NONE         nothing. ERROR: a broken record, nothing applied.
The four BOOST flags and `synthetic_share_high` never halt r4-b-T; they are listed under `not_halting` (counted as monitor runs, disclosed). The decode-check input must carry
`"profile": "r4bt_decode_check_v1"` (tools/r4bt_decode_check.py); `a3_redeploy_check`'s own H5 verdict is never an input.

    python -m tools.r4bt_a3 classify --record REC --pins PINS [--writes W.json] [--follower-samples S] [--decoder-ref R] [--decode-checks D] [--applied-log L] [--last-clean-run-utc U] [--out V]
    python -m tools.r4bt_a3 apply --verdict V --registry OWN_REGISTRY [--arena-registry /data/mal/arena/bots.json] --applied-log L

`apply` appends ONE stop line to r4-b-T's own registry and, when given, to the shared arena registry (Q2 item 7; `stop_bot` already refuses a second stop, an earlier stop
is never moved), and one line to the applied log for every verdict (it is the dead-man's history).
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools import r4bt_gate as G
from tools.a3_redeploy_check import ALL_HALT_FLAGS, BOOST_FLAGS, CheckError, _norm_flags, pins_diff
from tools.arena_paper import ArenaRefused, load_registry, stop_bot

SCHEMA = "r4bt_a3_v1"
PROFILE = "r4bt_decode_check_v1"
HARD_ACCOUNTS = ("bonding_fee_config", "pumpswap_fee_config", "pumpswap_global_config")
HARD_PROGRAMS = ("fees",)
RECORD_PROGRAMS = ("pump", "pumpswap")
NOT_HALTING = BOOST_FLAGS + ("synthetic_share_high",)
VERDICTS = ("NONE", "NEEDS_CHECK", "RECORD_ONLY", "HALT", "ERROR")
CHECK_WINDOW_MS = 24 * 3_600_000
DAY_MS = 86_400_000
MIN_MS = 1_600_000_000_000


def _utc(x: Any) -> int | None:
    try:
        return G.utc_ms(x)
    except G.Refused:
        return None


def deadman(entries: Sequence[Mapping[str, Any]], now_ms: int) -> int | None:
    """Pure. entries: [{"run_utc", "evaluated"}] of the daily runs. Returns the dead-man's halt instant (ms) or None. A UTC date is unevaluated when it has no run, or only
    runs that did not evaluate `pins_changed`. The span runs from the first run's date to the latest run's date, or to the last CLOSED date when later (a date still open at
    now_ms without a run is not counted yet). Two consecutive unevaluated dates halt at the first unevaluated run's run_utc, or at 00:00Z of that date when it has no run."""
    runs: dict[int, list[tuple[int, bool]]] = {}
    for e in entries:
        ms = _utc(e.get("run_utc"))
        if ms is not None:
            runs.setdefault(ms // DAY_MS, []).append((ms, bool(e.get("evaluated"))))
    if not runs:
        return None
    last = max(max(runs), now_ms // DAY_MS - 1)
    streak_start = None
    streak = 0
    for d in range(min(runs), last + 1):
        ok = any(ev for _ms, ev in runs.get(d, []))
        if ok:
            streak, streak_start = 0, None
            continue
        if streak == 0:
            streak_start = min((ms for ms, _ in runs.get(d, [])), default=d * DAY_MS)
        streak += 1
        if streak >= 2:
            return int(streak_start)
    return None


def _verdict(rec: Mapping, verdict: str, halt_at_ms: int | None, **kw: Any) -> dict:
    base = {"hard": [], "record_only": [], "needs_check": [], "not_halting": [], "reasons": []}
    return {"schema": SCHEMA, "run_utc": rec.get("run_utc"), "verdict": verdict, "halt_at_ms": halt_at_ms, "pins_changed_evaluated": kw.pop("evaluated", None), **base, **kw}


def decoder_failure_ms(samples: Sequence[Mapping[str, Any]], decoder_ref: Sequence[str] | None = None) -> int | None:
    """The FIRST failing follower sample's ts_ms: decoder_blobs_pinned or trade_event_v not true, or decoder_blobs not equal to the reference (QP Q2 item 1)."""
    bad = []
    for s in samples:
        ts = s.get("ts_ms")
        ok = s.get("decoder_blobs_pinned") is True and s.get("trade_event_v") is True
        if decoder_ref is not None:
            ok = ok and sorted(s.get("decoder_blobs") or []) == sorted(decoder_ref)
        if not ok and isinstance(ts, int) and not isinstance(ts, bool):
            bad.append(ts)
        elif not ok:
            bad.append(0)  # a failing sample with no usable time: the earliest instant (fail closed)
    return min(bad) if bad else None


def earliest_writes(writes: Sequence[Mapping[str, Any]] | None) -> dict[str, int]:
    """{program name: the EARLIEST write instant (ms)} from the enumerated writes ([{"name", "slot", "at_ms"}], e.g. a3_redeploy_check's expand_writes); bad entries are ignored."""
    out: dict[str, int] = {}
    for w in (writes or []):
        at = w.get("at_ms")
        if isinstance(at, int) and not isinstance(at, bool) and at >= MIN_MS and w.get("name"):
            out[w["name"]] = min(at, out.get(w["name"], at))
    return out


def classify(rec: Mapping[str, Any], pins: Mapping[str, Any], *, now_ms: int, last_clean_run_utc: str | None = None, follower_samples: Sequence[Mapping] | None = None,
             decoder_ref: Sequence[str] | None = None, decode_checks: Mapping[str, Any] | None = None, history: Sequence[Mapping[str, Any]] = (),
             writes: Sequence[Mapping[str, Any]] | None = None) -> dict:
    """Pure (now_ms is the job's clock)."""
    try:
        run_ms = G.utc_ms(rec.get("run_utc"))
        flags = _norm_flags(rec)
    except (G.Refused, CheckError) as exc:
        return _verdict(rec, "ERROR", None, reasons=[f"broken record: {exc}"])
    clean_ms = _utc(last_clean_run_utc) if last_clean_run_utc else None
    floor_ms = clean_ms if clean_ms is not None else (_utc(pins.get("pinned_utc")) or run_ms)  # the earliest instant the change can be dated to
    fallback_ms = clean_ms if clean_ms is not None else run_ms
    first_write = earliest_writes(writes)  # the earliest write per program since the last clean run; deploy_utc is never used (it dates the newest write)
    hard: list[tuple[int, str]] = []
    writes: list[dict] = []
    diffs = pins_diff(rec, pins)
    for d in diffs:
        if d["kind"] == "account" and d["name"] in HARD_ACCOUNTS:
            hard.append((floor_ms, f"account pin {d['name']} changed"))
        elif d["kind"] == "program" and d["name"] in HARD_PROGRAMS:
            hard.append((first_write.get(d["name"], floor_ms), f"fees program deploy slot {d['old']} -> {d['new']}"))
        elif d["kind"] == "program" and d["name"] in RECORD_PROGRAMS:
            writes.append({"name": d["name"], "slot": d["new"], "old_slot": d["old"], "deploy_utc": d.get("deploy_utc"), "write_ms": first_write.get(d["name"], floor_ms)})
        else:
            hard.append((fallback_ms, f"unknown pins difference {d['kind']} {d['name']}"))
    pc = flags["pins_changed"]
    if pc["halt"] and not diffs:
        hard.append((fallback_ms, "pins_changed set but no typed difference found"))
    if diffs and not pc["halt"]:
        hard.append((fallback_ms, "pins differ from the record but the pins_changed flag is not set (degraded record)"))
    for k, v in flags.items():
        if k not in ALL_HALT_FLAGS and v["halt"]:
            hard.append((fallback_ms, f"unknown halt flag {k}"))
    fd = decoder_failure_ms(follower_samples or [], decoder_ref)
    if fd is not None:
        hard.append((fd, "follower decoder pin failing (first failing sample)"))
    dm = deadman(list(history) + [{"run_utc": rec["run_utc"], "evaluated": pc["evaluated"]}], now_ms)
    if dm is not None:
        hard.append((dm, "dead-man: pins_changed unevaluated on two consecutive dates"))
    # program-only redeploys: the r4-b-T decode check within 24 h of the earliest write, else a hard halt AT THE WRITE
    if decode_checks is not None and decode_checks.get("profile") != PROFILE:
        return _verdict(rec, "ERROR", None, reasons=[f"decode-check input is not the {PROFILE} profile (the H5 verdict of a3_redeploy_check is never an input)"])
    results: dict[tuple, list[Mapping]] = {}
    for r in ((decode_checks or {}).get("redeploys") or []):
        results.setdefault((r.get("name"), r.get("slot")), []).append(r)
    record_only, needs = [], []
    for w in writes:
        rs = results.get((w["name"], w["slot"]), [])
        deadline = w["write_ms"] + CHECK_WINDOW_MS
        bad = [r for r in rs if r.get("verdict") != "PASS"]
        late = [r for r in rs if r.get("verdict") == "PASS" and not (isinstance(r.get("at_ms"), int) and not isinstance(r.get("at_ms"), bool) and r["at_ms"] <= deadline)]
        if bad:
            hard.append((w["write_ms"], f"decode check {bad[0].get('verdict')} for {w['name']} slot {w['slot']} (halt at the write)"))
        elif late:
            hard.append((w["write_ms"], f"decode check PASS for {w['name']} slot {w['slot']} not within 24 h of the write (halt at the write)"))
        elif rs:
            record_only.append(w)
        elif now_ms >= deadline:
            hard.append((w["write_ms"], f"no decode-check PASS within 24 h of the {w['name']} write at slot {w['slot']} (halt at the write)"))
        else:
            needs.append(dict(w, deadline_ms=deadline))
    not_halting = sorted(k for k in NOT_HALTING if flags.get(k, {}).get("halt"))
    common = dict(record_only=record_only, needs_check=needs, not_halting=not_halting, evaluated=pc["evaluated"])
    if hard:
        at = min(a for a, _ in hard)
        return _verdict(rec, "HALT", at, hard=[m for _, m in sorted(hard)], reasons=[m for _, m in sorted(hard)], **common)
    if needs:
        return _verdict(rec, "NEEDS_CHECK", None, reasons=["program redeploy inside its 24 h check window"], **common)
    if record_only:
        return _verdict(rec, "RECORD_ONLY", None, reasons=["program-only redeploy, r4-b-T decode check PASS within 24 h: disclosed"], **common)
    return _verdict(rec, "NONE", None, **common)


def append_log(path: str | Path, entry: Mapping[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def read_history(path: str | Path | None) -> list[dict]:
    if not path or not Path(path).exists():
        return []
    return [{"run_utc": e.get("run_utc"), "evaluated": bool(e.get("pins_changed_evaluated"))} for e in map(json.loads, Path(path).read_text().splitlines()) if e.get("run_utc")]


def validate(v: Mapping[str, Any], now_ms: int) -> None:
    if v.get("schema") != SCHEMA or v.get("verdict") not in VERDICTS:
        raise G.Refused("not an r4bt_a3 verdict")
    h = v.get("halt_at_ms")
    if v["verdict"] == "HALT":
        if not isinstance(h, int) or isinstance(h, bool) or not (MIN_MS <= h <= now_ms + 300_000 or h == 0):
            raise G.Refused("HALT needs an epoch-ms halt_at_ms in range")
    elif h is not None:
        raise G.Refused(f"{v['verdict']} carries a halt_at_ms")


def apply(v: Mapping[str, Any], registry: str | Path, applied_log: str | Path, *, arena_registry: str | Path | None = None, now_ms: int | None = None,
          bot: str = G.BOT_ID) -> dict:
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    regs = [G.check_registry(registry, "r4-b-T's own registry")]
    if arena_registry:
        regs.append(G.check_registry(arena_registry, "the arena registry", allow_arena_bots=True))
    validate(v, now)
    if v["verdict"] == "ERROR":
        raise G.Refused("an ERROR verdict applies nothing and is not logged as applied (the job is loud; no dead-man reset)")
    for r in regs:
        if load_registry(r).get(bot) is None:
            raise G.Refused(f"{bot} is not registered in {r}")
    actions = {}
    if v["verdict"] == "HALT":
        for r in regs:
            if load_registry(r)[bot]["stopped_at_ms"] is not None:
                actions[str(r)] = "already_stopped"  # an earlier stop is never moved
                continue
            try:
                stop_bot(bot, int(v["halt_at_ms"]), r)
                actions[str(r)] = "stopped"
            except ArenaRefused as exc:
                raise G.Refused(str(exc)) from None
    out = {"applied_ms": now, "bot_id": bot, "run_utc": v.get("run_utc"), "verdict": v["verdict"], "halt_at_ms": v.get("halt_at_ms"), "actions": actions,
           "pins_changed_evaluated": v.get("pins_changed_evaluated"), "record_only": v.get("record_only") or [], "not_halting": v.get("not_halting") or [],
           "needs_check": v.get("needs_check") or []}
    append_log(applied_log, out)
    return out


def main(argv: Sequence[str] | None = None, *, now_ms: int | None = None) -> int:
    ap = argparse.ArgumentParser(description="r4-b-T A3 entry.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("classify")
    c.add_argument("--record", required=True)
    c.add_argument("--pins", required=True)
    c.add_argument("--follower-samples", default=None)
    c.add_argument("--decoder-ref", default=None, help="JSON list of the three EXP027_DECODER_REF blobs")
    c.add_argument("--decode-checks", default=None)
    c.add_argument("--writes", default=None, help="JSON list of the enumerated upgrade writes since the last clean run: [{name, slot, at_ms}]")
    c.add_argument("--applied-log", default=None)
    c.add_argument("--last-clean-run-utc", default=None)
    c.add_argument("--out", default=None)
    a_ = sub.add_parser("apply")
    a_.add_argument("--verdict", required=True)
    a_.add_argument("--registry", required=True)
    a_.add_argument("--arena-registry", default=None)
    a_.add_argument("--applied-log", required=True)
    a = ap.parse_args(argv)
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    try:
        if a.cmd == "classify":
            rec = json.loads(Path(a.record).read_text())
            samples = [json.loads(x) for x in Path(a.follower_samples).read_text().splitlines() if x.strip()] if a.follower_samples else None
            ref = json.loads(Path(a.decoder_ref).read_text()) if a.decoder_ref else None
            checks = json.loads(Path(a.decode_checks).read_text()) if a.decode_checks else None
            v = classify(rec, json.loads(Path(a.pins).read_text()), now_ms=now, last_clean_run_utc=a.last_clean_run_utc, follower_samples=samples, decoder_ref=ref,
                         decode_checks=checks, history=read_history(a.applied_log),
                         writes=json.loads(Path(a.writes).read_text()) if a.writes else None)
            text = json.dumps(v, sort_keys=True)
            if a.out:
                if Path(a.out).exists():
                    raise G.Refused(f"{a.out} exists")
                Path(a.out).write_text(text + "\n")
            print(text)
            return 11 if v["verdict"] == "ERROR" else 0
        res = apply(json.loads(Path(a.verdict).read_text()), a.registry, a.applied_log, arena_registry=a.arena_registry, now_ms=now)
        print(json.dumps(res, sort_keys=True))
        return 0
    except (G.Refused, ValueError, OSError) as e:
        print(f"r4bt_a3: REFUSED: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
